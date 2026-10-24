"""Spotify: Windows media sessions to Discord Rich Presence.

Artwork policy (why this file looks the way it does)
----------------------------------------------------
Discord shows an activity image by fetching it from a URL. That fetch is done
by Discord's own crawler, once per distinct URL, and a failed fetch is cached
as "no image" for a while. A URL that only exists for one app session, or that
is slow on the first byte, therefore produces exactly the symptom this app used
to have: the thumbnail appears sometimes and is missing other times.

So artwork is resolved in *permanent first* order, and every candidate URL is
verified with a real HTTP fetch before it is handed to Discord:

    1. Sticky cache ......... a cover URL already proven for this track/album
    2. Player URL ........... the Chrome extension's i.ytimg.com URL
    3. YouTube thumbnail .... https://i.ytimg.com/vi/<id>/maxresdefault.jpg
    4. Catalog .............. Apple Music / Deezer album art (permanent CDN)
    5. Local relay .......... last resort, for embedded art with no match
    6. YouTube search ....... derive a video id from title + artist

Only tiers 1-4 and 6 are stored on disk, because only they stay valid across
restarts. The relay (tier 5) lives for one app session by design.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import io
import json
import logging
import os
import queue
import re
import secrets
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageOps
from pypresence import ActivityType, Presence, StatusDisplayType
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from winrt.windows.storage.streams import DataReader

APP_ID = "1561450956242161745"
APP_NAME = "Spotify"
APP_DIR_NAME = "SpotifyPresence"
LEGACY_APP_NAME = "YumeTune"
LEGACY_APP_DIR_NAME = "YumeTune"
EXE_NAME = "Spotify.exe"
INSTALL_DIR_PARTS = ("Programs", "Spotify")
MUTEX_NAME = "Local\\SpotifyPresence-SingleInstance"
POLL_SECONDS = 3
NO_TRACK_GRACE_SECONDS = 9
NATIVE_HOST_NAME = "com.ytdp.discord.presence"
NATIVE_PORT = 48261
NATIVE_VERSION = "1.9.0"
YOUTUBE_EXTENSION_ORIGIN = "chrome-extension://hnmeidgkfcbpjjjpmjmpehjdljlaeaaa/"
YOUTUBE_MUSIC_AUMID_SUFFIX = "_crx_cinhimbnkkghhklpknlkffjgod"
YOUTUBE_MUSIC_AUMID_MARK = "cinhimbnkkghhklpknlkffjgod"
USER_AGENT = "Spotify/2.0 (Windows; album artwork lookup)"
HTTP_TIMEOUT = 6
PROBE_TTL_SECONDS = 900
RELAY_IMAGE_SIZE = 320
REFRESH_SECONDS = 40
ART_RETRY_SECONDS = 60
NATIVE_STICKY_SECONDS = 1800
FRESH_EXTENSION_SECONDS = 20

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_DIR_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)
ART_CACHE_FILE = LOG_DIR / "artwork-cache.json"
logging.basicConfig(filename=LOG_DIR / "presence.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")

VENDOR_NATIVE_HOST_KEYS = (
    r"Software\Google\Chrome",
    r"Software\Chromium",
    r"Software\Thorium",
    r"Software\BraveSoftware\Brave-Browser",
    r"Software\Microsoft\Edge",
    r"Software\Vivaldi",
)


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    album: str
    playing: bool
    source: str
    duration_ms: int | None
    position_ms: int | None
    image_url: str | None = None
    video_id: str | None = None
    start_epoch: float | None = None
    kind: str = "local"

    @property
    def key(self):
        # The cover URL is deliberately not part of the key: artwork for one
        # song is resolved once and must not cause activity churn.
        return (self.title, self.artist, self.album, self.playing, self.kind)


# ---------------------------------------------------------------------------
# Small text / URL helpers
# ---------------------------------------------------------------------------

def clean(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u200b", "")).strip()


def slug(value):
    text = unicodedata.normalize("NFKD", clean(value))
    text = text.encode("ascii", "ignore").decode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def track_cache_key(title, artist):
    return f"{slug(artist)}|{slug(title)}"


def album_cache_key(artist, album):
    return f"{slug(artist)}|{slug(album)}"


_DECORATION = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DECORATION_WORDS = (
    "official", "video", "audio", "lyric", "lyrics", "visualizer", "remaster",
    "mv", "m/v", "4k", "8k", "hd", "hq", "explicit", "clean", "performance",
    "version", "edit", "mix", "audio only", "with lyrics", "color coded",
)


def strip_decorations(title):
    """Turn 'Song (Official Video) [4K]' into 'Song' for search/catalog use."""
    def drop(match):
        inner = match.group(1).casefold()
        return " " if any(word in inner for word in _DECORATION_WORDS) else match.group(0)
    text = _DECORATION.sub(drop, clean(title))
    text = re.sub(r"\s*[-–—]\s*topic\s*$", "", text, flags=re.I)
    text = re.sub(r"\((?:feat|ft)\.?[^)]*\)", "", text, flags=re.I)
    return clean(text)


def normalize_title(title, artist):
    """Remove a duplicated leading artist from players that include it in the title."""
    title, artist = clean(title), clean(artist)
    if not title or not artist:
        return title
    separators = (" • ", " · ", " – ", " — ", " - ", ": ")
    if title.casefold() == artist.casefold():
        for separator in separators:
            if separator in title:
                _prefix, _, remainder = title.partition(separator)
                if remainder.strip():
                    return remainder.strip()
    for separator in separators:
        prefix, found, remainder = title.partition(separator)
        if found and prefix.casefold() == artist.casefold() and remainder.strip():
            return remainder.strip()
    return title


def normalize_artist(artist, title):
    """Strip a duplicate title when SMTC combines artist and track in both fields."""
    artist, title = clean(artist), clean(title)
    if not artist or not title:
        return artist
    for separator in (" • ", " · ", " – ", " — ", " - ", ": "):
        if separator in artist:
            first, _, remainder = artist.partition(separator)
            if remainder.strip().casefold() == title.casefold():
                return first.strip()
    if artist.casefold() == title.casefold():
        return ""
    return artist


def video_id_from_url(url):
    if not isinstance(url, str):
        return None
    match = re.search(r"(?:[?&]v=|/vi/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


def yt_thumb_urls(video_id):
    if not video_id:
        return []
    return [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]


def to_epoch_seconds(value):
    """Extension timestamps are milliseconds (Date.now()); SMTC ones are seconds."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e11:          # milliseconds since epoch
        number /= 1000.0
    return number


# ---------------------------------------------------------------------------
# HTTP + image probing
# ---------------------------------------------------------------------------

_probe_lock = threading.Lock()
_probe_cache: dict[str, tuple[bool, float]] = {}


def http_get(url, timeout=HTTP_TIMEOUT, max_bytes=4_000_000, headers=None):
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if len(data) > max_bytes:
        return None, content_type
    return data, content_type


def looks_like_image(data, content_type=None):
    if not data or len(data) < 900:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:4] in (b"RIFF", b"GIF8"):
        return True
    if data[:4] == b"WEBP" or data[8:12] == b"WEBP":
        return True
    return bool(content_type and content_type.startswith("image/"))


def probe_image(url, timeout=HTTP_TIMEOUT, min_side=120, max_bytes=1_500_000, attempts=1):
    """Return True only when the URL really serves a usable image right now."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return False
    now = time.monotonic()
    with _probe_lock:
        cached = _probe_cache.get(url)
        if cached and now - cached[1] < PROBE_TTL_SECONDS:
            return cached[0]
    ok = False
    for attempt in range(max(1, attempts)):
        try:
            data, content_type = http_get(url, timeout=timeout, max_bytes=max_bytes)
            if looks_like_image(data, content_type):
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.size
                ok = width >= min_side and height >= min_side
        except Exception:
            ok = False
        if ok:
            break
        if attempt + 1 < attempts:
            time.sleep(0.5)
    with _probe_lock:
        _probe_cache[url] = (ok, now)
        if len(_probe_cache) > 400:
            _probe_cache.pop(next(iter(_probe_cache)))
    return ok


# ---------------------------------------------------------------------------
# Permanent artwork cache
# ---------------------------------------------------------------------------

class ArtworkStore:
    """Disk cache of cover URLs that survive restarts (never relay URLs)."""

    VERSION = 2
    LIMIT = 4000

    def __init__(self, path=ART_CACHE_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"version": self.VERSION, "tracks": {}, "albums": {}}
        self._dirty = False
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("version") == self.VERSION:
                data.setdefault("tracks", {})
                data.setdefault("albums", {})
                self._data = data
        except Exception:
            pass

    def save(self):
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            payload = json.dumps(self._data, ensure_ascii=False)
        try:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        except Exception as exc:
            logging.warning("Could not persist artwork cache: %s", exc)

    def _prune(self):
        for section in ("tracks", "albums"):
            entries = self._data[section]
            if len(entries) <= self.LIMIT:
                continue
            ordered = sorted(entries.items(), key=lambda item: item[1].get("ts", 0))
            for key, _value in ordered[: len(entries) - self.LIMIT]:
                entries.pop(key, None)

    def get_track(self, key):
        with self._lock:
            entry = self._data["tracks"].get(key)
            return dict(entry) if entry else None

    def put_track(self, key, url, album="", video_id=None):
        if not key or not url:
            return
        with self._lock:
            current = self._data["tracks"].get(key) or {}
            current.update({"url": url, "album": album or current.get("album", ""),
                            "video_id": video_id or current.get("video_id"),
                            "ts": int(time.time())})
            self._data["tracks"][key] = current
            self._prune()
            self._dirty = True

    def get_album(self, key):
        with self._lock:
            entry = self._data["albums"].get(key)
            return dict(entry) if entry else None

    def put_album(self, key, url):
        if not key or not url:
            return
        with self._lock:
            self._data["albums"][key] = {"url": url, "ts": int(time.time())}
            self._prune()
            self._dirty = True


# ---------------------------------------------------------------------------
# Catalog lookups (permanent CDN URLs)
# ---------------------------------------------------------------------------

def _itunes_search(params):
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
    data, _content_type = http_get(url, timeout=7, max_bytes=1_500_000)
    if not data:
        return []
    try:
        payload = json.loads(data)
    except Exception:
        return []
    results = payload.get("results")
    return results if isinstance(results, list) else []


def _artwork_600(result):
    art = result.get("artworkUrl100") or result.get("artworkUrl60")
    if not art:
        return None
    art = str(art)
    for size in ("100x100bb", "60x60bb", "100x100", "60x60"):
        if size in art:
            return art.replace(size, "600x600bb")
    return art


def itunes_album_art(artist, album):
    if not album:
        return None, None
    term = " ".join(part for part in (artist, album) if part)
    try:
        results = _itunes_search({"term": term, "entity": "album", "limit": 6})
    except Exception:
        return None, None
    target_album, target_artist = slug(album), slug(artist)
    for result in results:
        collection = slug(result.get("collectionName"))
        if not collection:
            continue
        exact_album = collection == target_album
        if not (exact_album or collection.startswith(target_album) or target_album in collection):
            continue
        # Soundtrack and compilation albums are often credited to "Various
        # Artists", so a distinctive exact album name is accepted on its own.
        if target_artist and target_artist not in slug(result.get("artistName")) and not exact_album:
            continue
        artwork = _artwork_600(result)
        if artwork:
            return artwork, clean(result.get("collectionName"))
    return None, None


def itunes_song_art(title, artist, album=""):
    if not title:
        return None, None
    term = " ".join(part for part in (artist, title, album) if part)
    try:
        results = _itunes_search({"term": term, "entity": "song", "limit": 8})
    except Exception:
        return None, None
    target_title, target_artist = slug(strip_decorations(title)), slug(artist)

    def score(result):
        value = 0
        name = slug(result.get("trackName"))
        if name and name == target_title:
            value += 5
        elif name and target_title and (target_title in name or name in target_title):
            value += 2
        if target_artist and target_artist in slug(result.get("artistName")):
            value += 2
        return value

    ranked = sorted(results, key=score, reverse=True)
    if not ranked or score(ranked[0]) < 5:
        return None, None
    artwork = _artwork_600(ranked[0])
    if not artwork:
        return None, None
    return artwork, clean(ranked[0].get("collectionName"))


def deezer_song_art(title, artist):
    if not title:
        return None, None
    query = f"{artist} {title}".strip()
    try:
        data, _content_type = http_get(
            "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": query, "limit": 5}),
            timeout=7, max_bytes=1_000_000)
        if not data:
            return None, None
        payload = json.loads(data)
    except Exception:
        return None, None
    target_title = slug(strip_decorations(title))
    for item in payload.get("data", []) or []:
        if slug(item.get("title")) != target_title:
            continue
        album = item.get("album") or {}
        artwork = album.get("cover_xl") or album.get("cover_big")
        if artwork:
            return artwork, clean(album.get("title"))
    return None, None


def youtube_search_video_id(title, artist):
    """Best-effort video id lookup used only when no player URL exists."""
    query = " ".join(part for part in (artist, strip_decorations(title)) if part).strip()
    if not query:
        return None
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": query, "hl": "en", "gl": "US"})
    try:
        data, _content_type = http_get(url, timeout=8, max_bytes=4_000_000,
                                       headers={"Accept-Language": "en-US,en;q=0.9"})
        if not data:
            return None
        text = data.decode("utf-8", "replace")
    except Exception:
        return None
    ordered = []
    for match in re.finditer(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"', text):
        candidate = match.group(1)
        if candidate not in ordered:
            ordered.append(candidate)
        if len(ordered) >= 3:
            break
    if not ordered:
        for candidate in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', text):
            if candidate not in ordered:
                ordered.append(candidate)
            if len(ordered) >= 3:
                break
    return ordered[0] if ordered else None


# ---------------------------------------------------------------------------
# Relay process lifetime
# ---------------------------------------------------------------------------

_JOB_HANDLE = None


def _bind_to_job(process):
    """Tie the relay to this process so it dies with it, even on a hard kill.

    Without this, killing the monitor (uninstall, upgrade, Task Manager) leaves
    an orphaned cloudflared behind. Orphans keep a tunnel open and, because they
    inherit our working directory, they can lock the install folder so that an
    uninstall cannot delete it.
    """
    global _JOB_HANDLE
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _JOB_HANDLE is None:
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(limits),
                                                    ctypes.sizeof(limits)):
                return
            _JOB_HANDLE = handle
        kernel32.AssignProcessToJobObject(_JOB_HANDLE, int(process._handle))
    except Exception as exc:
        logging.info("Could not bind relay to a job object: %s", exc)


def _cleanup_orphan_relays():
    """Kill relay helpers whose parent app is already gone."""
    if os.name != "nt":
        return
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
        "Where-Object { -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Local cover relay (session only)
# ---------------------------------------------------------------------------

class CoverProxy:
    """Temporarily expose only the current cover to Discord through a Quick Tunnel.

    This is the last resort for embedded art that has no catalog match. The
    public hostname only lives as long as this process, so URLs from here are
    never written to the permanent cache.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._image = None
        self._token = None
        self._digest = None
        self.public_url = None
        self._closed = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                token = self.path.removeprefix("/cover/").split("?", 1)[0].removesuffix(".jpg")
                with owner._lock:
                    image = owner._image if token and secrets.compare_digest(token, owner._token or "") else None
                if not image:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(image)))
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(image)

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name="CoverHTTP").start()
        self.process = None
        if os.name == "nt":
            cloudflared = bundled_path("cloudflared.exe")
            if not cloudflared.is_file():
                cloudflared = bundled_path("tools") / "cloudflared.exe"
            if cloudflared.is_file():
                try:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    self.process = subprocess.Popen(
                        [str(cloudflared), "tunnel", "--no-autoupdate", "--url",
                         f"http://127.0.0.1:{self.server.server_port}"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", creationflags=flags,
                        # Never inherit the install folder as the working
                        # directory: a stuck child would block uninstall.
                        cwd=tempfile.gettempdir(),
                    )
                    _bind_to_job(self.process)
                    threading.Thread(target=self._read_tunnel, daemon=True, name="CoverTunnel").start()
                    logging.info("Temporary cover relay starting")
                except Exception as exc:
                    self.process = None
                    logging.warning("Could not start temporary cover relay: %s", exc)
            else:
                logging.warning("Cover relay helper is missing; catalog artwork will be used")

    def _read_tunnel(self):
        try:
            for line in self.process.stdout:
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line, re.I)
                if match:
                    self.public_url = match.group(0)
                    logging.info("Temporary cover relay is ready")
                    # Warm the edge so Discord's first fetch is not a cold
                    # connection through the tunnel (that cold start is what
                    # made artwork appear only sometimes).
                    threading.Thread(target=self._warm, args=(self.public_url,),
                                     daemon=True, name="CoverWarm").start()
                    return
                if self._closed.is_set():
                    return
        except Exception as exc:
            if not self._closed.is_set():
                logging.warning("Temporary cover relay stopped: %s", exc)

    @staticmethod
    def _warm(url, attempts=2):
        for _ in range(attempts):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=8) as response:
                    response.read(1024)
            except Exception:
                pass

    def set_image(self, data):
        if not data:
            return None
        try:
            digest = hashlib.sha256(data).digest()
            with self._lock:
                token = self._token if digest == self._digest else None
            if token is None:
                image = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"),
                                     (RELAY_IMAGE_SIZE, RELAY_IMAGE_SIZE),
                                     method=Image.Resampling.LANCZOS)
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
                payload = out.getvalue()
                with self._lock:
                    self._image = payload
                    self._digest = digest
                    self._token = secrets.token_urlsafe(16)
                    token = self._token
            if not self.public_url or not token:
                return None
            url = f"{self.public_url}/cover/{token}.jpg"
            self._warm(url, attempts=1)
            return url
        except Exception as exc:
            logging.warning("Cover relay encode failed: %s", exc)
            return None

    def clear(self):
        with self._lock:
            self._image = None
            self._token = None
            self._digest = None

    def close(self):
        self._closed.set()
        self.clear()
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        process = getattr(self, "process", None)
        if process and process.poll() is None:
            process.terminate()


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class ArtworkResolver:
    def __init__(self, relay: CoverProxy):
        self.relay = relay
        self.store = ArtworkStore()
        self._memo = {}
        self._memo_lock = threading.Lock()

    def remember_player_track(self, track):
        """Persist the extension's permanent mapping (title/artist -> video id)."""
        if track.kind != "youtube":
            return
        key = track_cache_key(track.title, track.artist)
        if not key.strip("|"):
            return
        video_id = track.video_id or video_id_from_url(track.image_url)
        if video_id:
            # Store the highest-resolution variant; the resolver verifies it and
            # falls back to mqdefault for videos that have no maxres frame.
            url = yt_thumb_urls(video_id)[0]
        elif (track.image_url or "").startswith("https://"):
            url = track.image_url
        else:
            return
        existing = self.store.get_track(key) or {}
        if existing.get("url") == url and existing.get("video_id") == video_id:
            return
        self.store.put_track(key, url, track.album, video_id)
        self.store.save()

    def _candidates(self, track, local_cover, prefer_permanent=False):
        key = track_cache_key(track.title, track.artist)
        cached = self.store.get_track(key) if key.strip("|") else None

        if track.kind == "youtube":
            video_id = track.video_id or (cached or {}).get("video_id") or video_id_from_url(track.image_url)
            # The highest-resolution thumbnail of the exact video being played
            # is the best permanent image there is. It is tried before the
            # stored URL so an older mqdefault entry upgrades to maxresdefault.
            for url in yt_thumb_urls(video_id):
                yield "youtube", url, track.album
            if cached and cached.get("url"):
                yield "cache", cached["url"], cached.get("album") or track.album
            if ((track.image_url or "").startswith("https://")
                    and "i.ytimg.com" not in track.image_url):
                yield "player", track.image_url, track.album
            if track.album and track.artist:
                album_entry = self.store.get_album(album_cache_key(track.artist, track.album))
                if album_entry and album_entry.get("url"):
                    yield "album-cache", album_entry["url"], track.album
            url, album = self._catalog_for(track)
            if url:
                yield "catalog", url, album or track.album
            if not prefer_permanent:
                url = self.relay.set_image(local_cover)
                if url:
                    yield "relay", url, track.album
            if not video_id:
                found = youtube_search_video_id(track.title, track.artist)
                for url in yt_thumb_urls(found):
                    yield "search", url, track.album
            return

        # Local files (foobar2000): a confident catalog match is both accurate
        # and permanently cached by Discord, so it is tried before the relay.
        if cached and cached.get("url"):
            yield "cache", cached["url"], cached.get("album") or track.album
        if track.album and track.artist:
            album_entry = self.store.get_album(album_cache_key(track.artist, track.album))
            if album_entry and album_entry.get("url"):
                yield "album-cache", album_entry["url"], track.album
        url, album = self._catalog_for(track)
        if url:
            yield "catalog", url, album or track.album
        if not prefer_permanent:
            url = self.relay.set_image(local_cover)
            if url:
                yield "relay", url, track.album
        found = youtube_search_video_id(track.title, track.artist)
        if found:
            for url in yt_thumb_urls(found):
                yield "search", url, track.album

    def _catalog_for(self, track):
        title = strip_decorations(track.title)
        if track.kind != "youtube" and track.album:
            url, album = itunes_album_art(track.artist, track.album)
            if url:
                self.store.put_album(album_cache_key(track.artist, track.album), url)
                return url, album
        url, album = itunes_song_art(title, track.artist, track.album)
        if url:
            return url, album
        url, album = deezer_song_art(title, track.artist)
        if url:
            return url, album
        return None, None

    def resolve(self, track, local_cover, prefer_permanent=False, bypass_memo=False):
        """Return (image_url, album, tier). Never returns an unverified URL."""
        memo_key = (track_cache_key(track.title, track.artist), prefer_permanent)
        if not bypass_memo:
            with self._memo_lock:
                hit = self._memo.get(memo_key)
            if hit:
                return hit

        album = track.album
        chosen = None
        cached_candidate = None
        tried = []
        for tier, url, candidate_album in self._candidates(track, local_cover, prefer_permanent):
            if not url:
                continue
            if tier == "cache":
                cached_candidate = (url, candidate_album or album)
            # A cached permanent URL gets one extra probe attempt: a transient
            # network blip must not make a known-good cover look dead.
            if probe_image(url, attempts=2 if tier == "cache" else 1):
                chosen = (url, candidate_album or album, tier)
                break
            tried.append(f"{tier}:dead")
        if chosen is None and cached_candidate:
            # Offline or CDN hiccup: Discord may still have this image cached,
            # so a previously proven URL beats sending nothing.
            chosen = (cached_candidate[0], cached_candidate[1], "cache-unverified")
        if chosen is None:
            chosen = (None, album, "none")

        if chosen[0]:
            album = chosen[1] or album
            if chosen[2] in ("cache", "player", "youtube", "catalog", "search", "album-cache"):
                key = track_cache_key(track.title, track.artist)
                video_id = track.video_id or (self.store.get_track(key) or {}).get("video_id")
                self.store.put_track(key, chosen[0], album, video_id)
                self.store.save()
            logging.info("Artwork resolved: tier=%s url=%s%s", chosen[2], chosen[0],
                         (" rejected=" + ",".join(tried)) if tried else "")
        else:
            logging.warning("No usable artwork found for %r by %r%s", track.title, track.artist,
                            (" (tried " + ", ".join(tried) + ")") if tried else "")

        with self._memo_lock:
            self._memo[memo_key] = chosen
            if len(self._memo) > 500:
                self._memo.pop(next(iter(self._memo)))
        return chosen


# ---------------------------------------------------------------------------
# Chrome native messaging bridge
# ---------------------------------------------------------------------------

class NativeMessagingServer:
    """Receives the YTDP Chrome extension's native-host messages on loopback."""
    def __init__(self):
        self._lock = threading.Lock()
        self._track = None
        self._received_at = 0.0
        self._last_ignored = 0.0
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    raw = self.rfile.readline(1_000_001)
                    if not raw or len(raw) > 1_000_000:
                        return
                    packet = json.loads(raw.decode("utf-8"))
                    response = owner.accept(packet)
                    self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
                    self.wfile.flush()
                except Exception:
                    try:
                        self.wfile.write(b'{"data":"1_CLIENT_ERROR: Invalid native message"}\n')
                        self.wfile.flush()
                    except Exception:
                        pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = None
        try:
            self.server = Server(("127.0.0.1", NATIVE_PORT), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True, name="ChromeNativeBridge").start()
            logging.info("Chrome extension bridge listening on loopback")
        except OSError as exc:
            logging.warning("Chrome extension bridge could not start: %s", exc)

    def accept(self, packet):
        try:
            presence = packet.get("presenceData")
            if presence == "#*IDLE*#":
                with self._lock:
                    self._track = None
                    self._received_at = time.monotonic()
                return {"data": "0_SUCCESS: PRESENCE_CLEARED"}
            if not isinstance(presence, dict):
                return {"data": "0_SUCCESS: MESSAGE_RECEIVED"}
            application_type = packet.get("jsApplicationType")
            if application_type != "youtubeMusic" or presence.get("type") != 2:
                with self._lock:
                    self._track = None
                    self._received_at = time.monotonic()
                now = time.monotonic()
                if now - self._last_ignored > 60:
                    self._last_ignored = now
                    logging.info("Extension packet ignored (application=%s type=%s)",
                                 application_type, presence.get("type"))
                return {"data": "0_SUCCESS: REGULAR_YOUTUBE_IGNORED"}

            title = clean(presence.get("details") or packet.get("jsTitle"))
            artist = clean(packet.get("jsAuthor") or presence.get("state"))
            if artist.casefold().startswith("by "):
                artist = artist[3:].strip()
            title = normalize_title(title, artist)
            artist = normalize_artist(artist, title)
            album = clean(packet.get("jsAlbum"))
            if not title:
                return {"data": "1_CLIENT_ERROR: Missing track title"}
            assets = presence.get("assets") if isinstance(presence.get("assets"), dict) else {}
            if not album:
                candidate_album = clean(assets.get("large_text"))
                if candidate_album.casefold() not in {title.casefold(), artist.casefold()}:
                    album = candidate_album

            image_url = assets.get("large_image")
            video_id = video_id_from_url(packet.get("jsVideoUrl")) or video_id_from_url(image_url)
            if not isinstance(image_url, str) or not image_url.startswith(("https://", "http://")):
                image_url = yt_thumb_urls(video_id)[1] if video_id else None
            if video_id and "i.ytimg.com" not in (image_url or ""):
                image_url = yt_thumb_urls(video_id)[0]

            stamps = presence.get("timestamps") if isinstance(presence.get("timestamps"), dict) else {}
            start_epoch = to_epoch_seconds(stamps.get("start"))
            end_epoch = to_epoch_seconds(stamps.get("end"))
            duration = None
            if start_epoch and end_epoch and end_epoch > start_epoch:
                duration = int((end_epoch - start_epoch) * 1000)
            position = None
            if start_epoch:
                position = max(0, int((time.time() - start_epoch) * 1000))
                if duration:
                    position = min(position, duration)

            track = Track(title, artist, album, True, "YouTube Music", duration, position,
                          image_url, video_id, start_epoch, "youtube")
            with self._lock:
                self._track = track
                self._received_at = time.monotonic()
            return {"data": "0_SUCCESS: PRESENCE_UPDATED"}
        except Exception as exc:
            logging.warning("Native message handling failed: %s", exc)
            return {"data": "1_CLIENT_ERROR: Invalid native message"}

    def current_track(self):
        """Return (track, age_seconds) so callers can tell stale data apart."""
        with self._lock:
            if not self._track:
                return None, float("inf")
            age = time.monotonic() - self._received_at
            if age > NATIVE_STICKY_SECONDS:
                return None, age
            return self._track, age

    def close(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# SMTC
# ---------------------------------------------------------------------------

def is_youtube_music_source(source):
    lowered = source.casefold()
    return lowered.endswith(YOUTUBE_MUSIC_AUMID_SUFFIX) or YOUTUBE_MUSIC_AUMID_MARK in lowered


async def get_track():
    manager = await MediaManager.request_async()
    candidates = []
    for session in manager.get_sessions():
        try:
            source = clean(session.source_app_user_model_id)
            source_lower = source.casefold()
            is_youtube_music = is_youtube_music_source(source)
            is_foobar2000 = source_lower.startswith("foobar2000")
            if not (is_youtube_music or is_foobar2000):
                continue
            info = session.get_playback_info()
            props = await session.try_get_media_properties_async()
            artist = clean(props.artist or props.album_artist)
            title = normalize_title(props.title, artist)
            artist = normalize_artist(artist, title)
            if not title or title.lower() in {"unknown", ""}:
                continue
            timeline = session.get_timeline_properties()
            duration = int(timeline.end_time.total_seconds() * 1000) if timeline and timeline.end_time is not None else None
            position = int(timeline.position.total_seconds() * 1000) if timeline and timeline.position is not None else None
            cover = None
            if props.thumbnail:
                try:
                    stream = await props.thumbnail.open_read_async()
                    reader = DataReader(stream.get_input_stream_at(0))
                    size = await reader.load_async(stream.size)
                    cover_buffer = bytearray(size)
                    reader.read_bytes(cover_buffer)
                    cover = bytes(cover_buffer)
                except Exception:
                    pass
            kind = "youtube" if is_youtube_music else "local"
            track = Track(title, artist, clean(props.album_title), info.playback_status == 4,
                          "YouTube Music" if is_youtube_music else source, duration, position,
                          None, None, None, kind)
            candidates.append((info.playback_status == 4, track, cover))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2]


def _titles_match(left, right):
    a, b = slug(strip_decorations(left)), slug(strip_decorations(right))
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _artists_match(left, right):
    a, b = slug(left), slug(right)
    if not a or not b:
        return True
    return a == b or a in b or b in a


def merge_tracks(smtc_track, local_cover, extension_track):
    """Combine Windows' live playback state with the extension's artwork data."""
    if extension_track is None:
        return smtc_track, local_cover
    if smtc_track is None:
        return extension_track, None
    if not (_titles_match(extension_track.title, smtc_track.title)
            and _artists_match(extension_track.artist, smtc_track.artist)):
        if smtc_track.playing or not extension_track.playing:
            return smtc_track, local_cover
        return extension_track, None

    kind = "youtube" if "youtube" in (smtc_track.kind, extension_track.kind) else "local"
    duration = smtc_track.duration_ms or extension_track.duration_ms
    position = smtc_track.position_ms
    start_epoch = None
    if position is None or not duration:
        start_epoch = extension_track.start_epoch
    merged = Track(
        title=extension_track.title or smtc_track.title,
        artist=smtc_track.artist or extension_track.artist,
        album=extension_track.album or smtc_track.album,
        playing=smtc_track.playing,
        source="YouTube Music" if kind == "youtube" else smtc_track.source,
        duration_ms=duration,
        position_ms=position,
        image_url=extension_track.image_url or smtc_track.image_url,
        video_id=extension_track.video_id or smtc_track.video_id,
        start_epoch=start_epoch,
        kind=kind,
    )
    return merged, local_cover


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class Bridge:
    def __init__(self):
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.native_bridge = NativeMessagingServer()
        self.cover_proxy = CoverProxy()
        self.art = ArtworkResolver(self.cover_proxy)
        self._position_state = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _smooth_position(self, track):
        """Keep the elapsed timer moving if a player freezes its SMTC timeline."""
        if track.start_epoch:
            position = max(0, int((time.time() - track.start_epoch) * 1000))
            if track.duration_ms:
                position = min(position, track.duration_ms)
            return replace(track, position_ms=position)

        now = time.monotonic()
        identity = (track.title, track.artist, track.album, track.source)
        reported = max(0, track.position_ms or 0)
        previous = self._position_state
        if not previous or previous["identity"] != identity:
            position = reported
        else:
            elapsed_ms = max(0, int((now - previous["at"]) * 1000))
            position = previous["position"] + (elapsed_ms if previous["playing"] else 0)
            if track.playing and reported != previous["reported"]:
                position = reported
            elif not track.playing and reported != previous["reported"]:
                position = reported
        if track.duration_ms:
            position = min(position, track.duration_ms)
        self._position_state = {"identity": identity, "position": position, "reported": reported,
                                "at": now, "playing": track.playing}
        return replace(track, position_ms=position)

    def _emit(self, kind, value):
        self.events.put((kind, value))

    def _run(self):
        asyncio.run(self._loop())

    async def _current(self):
        try:
            media = await get_track()
        except Exception as exc:
            self._emit("error", str(exc))
            media = None
        smtc_track, local_cover = media if media else (None, None)
        extension_track, extension_age = self.native_bridge.current_track()
        if extension_track is not None:
            self.art.remember_player_track(extension_track)
            if smtc_track is None and extension_age > FRESH_EXTENSION_SECONDS:
                # The browser was closed without sending its idle message.
                extension_track = None
        track, local_cover = merge_tracks(smtc_track, local_cover, extension_track)
        if track is not None:
            track = self._smooth_position(track)
        return track, local_cover

    async def _loop(self):
        rpc = None
        last_sent_key = None
        last_seen_key = None
        missing_since = None
        last_update_at = 0.0
        last_rpc_try = 0.0
        resolved_key = None
        resolved = (None, "", "none")
        last_art_retry = 0.0

        while not self.stop_event.is_set():
            try:
                track, local_cover = await self._current()
            except Exception as exc:
                track, local_cover = None, None
                self._emit("error", str(exc))

            if track is None:
                now = time.monotonic()
                if missing_since is None:
                    missing_since = now
                    if last_sent_key is not None:
                        logging.info("No supported media session; waiting %.0f seconds before clearing Discord activity",
                                     NO_TRACK_GRACE_SECONDS)
                self._emit("track", None)
                if rpc and last_sent_key is not None and now - missing_since >= NO_TRACK_GRACE_SECONDS:
                    try:
                        await asyncio.to_thread(rpc.clear)
                    except Exception:
                        pass
                    logging.info("No replacement track appeared; Discord activity cleared")
                    last_sent_key = None
                    self.cover_proxy.clear()
                elif last_sent_key is None or rpc is None:
                    self.cover_proxy.clear()
                await asyncio.sleep(POLL_SECONDS)
                continue

            missing_since = None
            if not track.playing:
                if rpc and last_sent_key is not None:
                    try:
                        await asyncio.to_thread(rpc.clear)
                        logging.info("Discord activity cleared because media is paused")
                    except Exception as exc:
                        logging.warning("Discord RPC clear on pause failed: %s", exc)
                    last_sent_key = None
                resolved_key = None
                self.cover_proxy.clear()
                self._emit("track", (track, local_cover))
                await asyncio.sleep(POLL_SECONDS)
                continue

            if track.key != last_seen_key:
                logging.info("Supported media session detected: kind=%s source=%s artist=%s album=%s video=%s",
                             track.kind, track.source, bool(track.artist), bool(track.album), bool(track.video_id))
                last_seen_key = track.key
                self._position_state = None

            if rpc is None and time.monotonic() - last_rpc_try > 8:
                last_rpc_try = time.monotonic()
                try:
                    rpc = Presence(APP_ID)
                    await asyncio.to_thread(rpc.connect)
                    last_sent_key = None
                    self._emit("discord", "Connected")
                    logging.info("Connected to Discord local RPC using application id %s", APP_ID)
                except Exception as exc:
                    rpc = None
                    self._emit("discord", "Open the Discord desktop app to connect")
                    self._emit("error", str(exc))
                    logging.warning("Discord RPC connection failed: %s", exc)

            now = time.monotonic()
            if resolved_key != track.key:
                resolved_key = track.key
                resolved = await asyncio.to_thread(self.art.resolve, track, local_cover)
                last_art_retry = now
            elif (resolved[2] in ("relay", "none", "cache-unverified")
                  and now - last_art_retry > ART_RETRY_SECONDS):
                # A session-only relay URL can still be missed by Discord's
                # crawler. Retry for a permanent CDN URL so a song never stays
                # without a cover, and keep retrying while nothing was found.
                last_art_retry = now
                snapshot = resolved
                retried = await asyncio.to_thread(self.art.resolve, track, local_cover, True, True)
                if retried[0] and retried[2] not in ("none", "relay"):
                    resolved = retried
                    logging.info("Upgraded artwork to a permanent source: tier=%s", retried[2])
                else:
                    resolved = snapshot

            image_url, album, _tier = resolved
            album = album or track.album
            if album and album.casefold() in {track.title.casefold(), track.artist.casefold()}:
                album = ""

            send_key = (track.title, track.artist, album, image_url, track.playing)
            should_update = rpc and (send_key != last_sent_key or now - last_update_at > REFRESH_SECONDS)
            if should_update:
                details = track.title[:128]
                state = (track.artist or "Music")[:128]
                params = {"details": details, "state": state, "activity_type": ActivityType.LISTENING,
                          "status_display_type": StatusDisplayType.DETAILS}
                if album:
                    params["large_text"] = album[:128]
                if image_url:
                    params["large_image"] = image_url[:256]
                if track.duration_ms and track.position_ms is not None:
                    start = int(time.time() - max(0, track.position_ms) / 1000)
                    params["start"] = start
                    params["end"] = start + max(0, track.duration_ms // 1000)
                changed = send_key != last_sent_key
                try:
                    reply = await asyncio.to_thread(rpc.update, **params)
                    last_sent_key = send_key
                    last_update_at = time.monotonic()
                    self._emit("discord", "Connected • activity updated")
                    if changed:
                        data = reply.get("data") if isinstance(reply, dict) else None
                        logging.info("Discord RPC update reply: cmd=%s activity_type=%s application_id=%s",
                                     reply.get("cmd") if isinstance(reply, dict) else "unknown",
                                     data.get("type") if isinstance(data, dict) else "unknown",
                                     data.get("application_id") if isinstance(data, dict) else "unknown")
                        logging.info("Activity assets: image=%s tier=%s album=%s",
                                     bool(image_url), _tier, bool(album))
                except Exception as exc:
                    try:
                        await asyncio.to_thread(rpc.close)
                    except Exception:
                        pass
                    rpc = None
                    last_sent_key = None
                    last_rpc_try = time.monotonic()
                    self._emit("discord", "Reconnecting to Discord…")
                    self._emit("error", str(exc))
                    logging.warning("Discord RPC update failed: %s", exc)

            self._emit("track", (track, local_cover, album))
            await asyncio.sleep(POLL_SECONDS)

        if rpc:
            try:
                await asyncio.to_thread(rpc.clear)
                await asyncio.to_thread(rpc.close)
            except Exception:
                pass
        self.art.store.save()
        self.cover_proxy.close()
        self.native_bridge.close()

    def close(self):
        self.stop_event.set()

    def wait_forever(self):
        logging.info("%s background monitor started", APP_NAME)
        try:
            while not self.stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            self.close()


# ---------------------------------------------------------------------------
# Install / startup registration
# ---------------------------------------------------------------------------

def bundled_path(name):
    return Path(__file__).resolve().parent / name


def app_executable():
    if globals().get("__compiled__") is not None:
        # sys.argv[0] stays the launched one-file EXE; sys.executable can point
        # into Nuitka's temporary extraction folder.
        return Path(sys.argv[0]).resolve()
    return Path(sys.executable).resolve()


def write_native_host_manifest(exe_path):
    manifest_path = LOG_DIR / f"{NATIVE_HOST_NAME}.json"
    manifest = {
        "name": NATIVE_HOST_NAME,
        "description": f"{APP_NAME} YouTube Music native messaging host",
        "path": str(exe_path),
        "type": "stdio",
        "allowed_origins": [YOUTUBE_EXTENSION_ORIGIN],
    }
    try:
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) == manifest:
                return manifest_path
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not write native host manifest: %s", exc)
    return manifest_path


def register_native_host(exe_path):
    """Register the native messaging host for every Chromium-family browser."""
    import winreg
    manifest_path = write_native_host_manifest(exe_path)
    for vendor in VENDOR_NATIVE_HOST_KEYS:
        path = rf"{vendor}\NativeMessagingHosts\{NATIVE_HOST_NAME}"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                    winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                current = None
                try:
                    current, _kind = winreg.QueryValueEx(key, None)
                except FileNotFoundError:
                    pass
                if current != str(manifest_path):
                    winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest_path))
                    logging.info("Registered native messaging host under %s", vendor)
        except Exception as exc:
            logging.warning("Could not register native host under %s: %s", vendor, exc)
    return manifest_path


def local_app_dir():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home())))


def install_directory():
    return local_app_dir().joinpath(*INSTALL_DIR_PARTS)


def startup_folder():
    return (Path(os.environ.get("APPDATA", str(Path.home())))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")


def _stop_instances(paths):
    """Stop our own processes by image path.

    This deliberately never uses taskkill /IM: the installed EXE is called
    Spotify.exe, and killing by image name would also kill the real Spotify
    client. Only processes whose executable path matches one of ours are ended.
    """
    targets = [str(Path(item)) for item in paths if item]
    if not targets:
        return
    skip = {os.getpid()}
    try:
        skip.add(os.getppid())
    except Exception:
        pass
    quoted = ",".join("'" + item.replace("'", "''") + "'" for item in targets)
    skip_list = ",".join(str(pid) for pid in skip)
    script = (
        "$targets=@(" + quoted + ");$skip=@(" + skip_list + ");"
        "Get-Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and ($targets -contains $_.Path) -and ($skip -notcontains $_.Id) } | "
        "Stop-Process -Force -ErrorAction SilentlyContinue"
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass
    time.sleep(0.8)


def remove_startup_entries():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as key:
            for name in (APP_NAME, LEGACY_APP_NAME, "PresenceFusion"):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass
    folder = startup_folder()
    for name in (f"{APP_NAME}.lnk", f"{LEGACY_APP_NAME}.lnk", f"{LEGACY_APP_NAME}.exe.lnk"):
        try:
            link = folder / name
            if link.exists():
                link.unlink()
        except Exception:
            pass


def register_startup(destination, app_dir):
    import winreg
    command = f'"{destination}" --background'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        for legacy in (LEGACY_APP_NAME, "PresenceFusion"):
            try:
                winreg.DeleteValue(key, legacy)
            except FileNotFoundError:
                pass

    folder = startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    startup_link = folder / f"{APP_NAME}.lnk"
    for name in (f"{APP_NAME}.lnk", f"{LEGACY_APP_NAME}.lnk", f"{LEGACY_APP_NAME}.exe.lnk"):
        try:
            link = folder / name
            if link.exists():
                link.unlink()
        except Exception:
            pass
    shortcut_script = (
        "$ErrorActionPreference='Stop'; "
        "$ws=New-Object -ComObject WScript.Shell; "
        f"$shortcut=$ws.CreateShortcut('{str(startup_link).replace(chr(39), chr(39) * 2)}'); "
        f"$shortcut.TargetPath='{str(destination).replace(chr(39), chr(39) * 2)}'; "
        "$shortcut.Arguments='--background'; "
        f"$shortcut.WorkingDirectory='{str(app_dir).replace(chr(39), chr(39) * 2)}'; "
        f"$shortcut.WindowStyle=7; $shortcut.Description='{APP_NAME} music presence'; $shortcut.Save()"
    )
    shortcut = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", shortcut_script],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if shortcut.returncode:
        logging.warning("Could not create Startup folder shortcut: %s", shortcut.stderr.strip())
    else:
        logging.info("Created per-user Startup folder shortcut")
    for task in (APP_NAME, LEGACY_APP_NAME):
        subprocess.run(["schtasks.exe", "/Delete", "/TN", task, "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def migrate_legacy_install(destination):
    """Adopt the artwork cache from, and remove, an older YumeTune install."""
    legacy_dir = local_app_dir() / LEGACY_APP_DIR_NAME
    if destination.parent == legacy_dir or not legacy_dir.is_dir():
        return
    import shutil
    _stop_instances([legacy_dir / f"{LEGACY_APP_NAME}.exe", destination])
    source_cache = legacy_dir / "artwork-cache.json"
    target_cache = LOG_DIR / "artwork-cache.json"
    try:
        if source_cache.is_file() and not target_cache.exists():
            shutil.copy2(source_cache, target_cache)
            logging.info("Migrated the artwork cache from the previous install")
    except Exception as exc:
        logging.warning("Could not migrate the artwork cache: %s", exc)
    for attempt in range(5):
        try:
            shutil.rmtree(legacy_dir)
            logging.info("Removed the previous install at %s", legacy_dir)
            return
        except Exception:
            _stop_instances([legacy_dir / f"{LEGACY_APP_NAME}.exe"])
    logging.warning("Previous install folder could not be removed: %s", legacy_dir)


def install_autostart(exe_path=None):
    source = Path(exe_path).resolve() if exe_path else app_executable()
    app_dir = install_directory()
    app_dir.mkdir(parents=True, exist_ok=True)
    destination = app_dir / EXE_NAME

    if source != destination:
        import shutil
        for attempt in range(40):
            try:
                shutil.copy2(source, destination)
                logging.info("Copied %s to %s", EXE_NAME, destination)
                break
            except OSError:
                _stop_instances([destination])
                time.sleep(0.4)
        else:
            logging.error("Could not replace %s", destination)

    # Point the browser at the new EXE first, so a relaunched native host can
    # never re-lock the folder that is about to be removed.
    register_native_host(destination)
    migrate_legacy_install(destination)
    register_startup(destination, app_dir)
    logging.info("Configured sign-in startup and browser native messaging for %s", APP_NAME)

    _stop_instances([destination])
    subprocess.Popen([str(destination), "--background"], cwd=str(app_dir), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def uninstall():
    destination = install_directory() / EXE_NAME
    legacy = local_app_dir() / LEGACY_APP_DIR_NAME / f"{LEGACY_APP_NAME}.exe"
    _stop_instances([destination, legacy])
    _cleanup_orphan_relays()
    remove_startup_entries()
    import winreg
    failures = []
    for vendor in VENDOR_NATIVE_HOST_KEYS:
        # VENDOR_NATIVE_HOST_KEYS already starts with "Software\"; adding it a
        # second time deleted nothing and the error used to be swallowed.
        path = rf"{vendor}\NativeMessagingHosts\{NATIVE_HOST_NAME}"
        deleted = False
        for attempt in range(5):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
                deleted = True
                break
            except FileNotFoundError:
                deleted = True
                break
            except OSError:
                time.sleep(0.4)
        if not deleted:
            failures.append(vendor)
    if failures:
        logging.warning("Could not remove browser host keys: %s", ", ".join(failures))
    else:
        logging.info("Removed browser native-messaging registrations")


# ---------------------------------------------------------------------------
# Chrome native messaging host process
# ---------------------------------------------------------------------------

def run_native_host():
    """Speak Chrome's length-prefixed native-messaging protocol and forward to the app."""
    # The no-console build has no Python streams, so recover Chrome's
    # redirected native-messaging pipes from the Win32 standard handles.
    if (sys.stdin is None or sys.stdout is None
            or getattr(sys.stdin, "buffer", None) is None
            or getattr(sys.stdout, "buffer", None) is None):
        try:
            import msvcrt
            ctypes = __import__("ctypes")
            kernel32 = ctypes.windll.kernel32
            kernel32.GetStdHandle.restype = ctypes.c_void_p
            input_handle = kernel32.GetStdHandle(-10)
            output_handle = kernel32.GetStdHandle(-11)
            invalid_handle = ctypes.c_void_p(-1).value
            if not input_handle or input_handle == invalid_handle or not output_handle or output_handle == invalid_handle:
                raise OSError("Chrome did not provide native-messaging pipe handles")
            input_fd = msvcrt.open_osfhandle(input_handle, os.O_BINARY)
            output_fd = msvcrt.open_osfhandle(output_handle, os.O_BINARY)
            sys.stdin = os.fdopen(input_fd, "rb", buffering=0)
            sys.stdout = os.fdopen(output_fd, "wb", buffering=0)
        except Exception as exc:
            logging.error("Could not attach Chrome native-messaging pipes: %s", exc)
            return
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = getattr(sys.stdout, "buffer", sys.stdout)

    def read_exact(stream, size):
        data = bytearray()
        while len(data) < size:
            chunk = stream.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def reply(value):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        output_stream.write(struct.pack("<I", len(payload)) + payload)
        output_stream.flush()

    while True:
        header = read_exact(input_stream, 4)
        if header is None:
            return
        size = struct.unpack("<I", header)[0]
        if size > 1_000_000:
            return
        raw = read_exact(input_stream, size)
        if raw is None:
            return
        try:
            packet = json.loads(raw.decode("utf-8"))
            if packet.get("getNativeVersion"):
                reply({"nativeVersion": NATIVE_VERSION})
                continue
            with socket.create_connection(("127.0.0.1", NATIVE_PORT), timeout=2) as client:
                client.sendall(json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n")
                response = client.makefile("rb").readline(1_000_001)
            reply(json.loads(response.decode("utf-8")) if response else {"data": "1_CLIENT_ERROR: App did not respond"})
        except Exception:
            reply({"data": "1_CLIENT_ERROR: Start " + APP_NAME + " to connect"})


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class _NullRelay:
    """Resolver stand-in for --dump, which must not start a tunnel."""
    public_url = None

    def set_image(self, data):
        return None


def dump_state():
    """Resolve the current track and write a report (used for verification)."""
    media = asyncio.run(get_track())
    report = {"track": None, "resolved": None}
    if media:
        track, cover = media
        resolver = ArtworkResolver(_NullRelay())
        image, album, tier = resolver.resolve(track, cover)
        report["track"] = {
            "title": track.title, "artist": track.artist, "album": track.album,
            "source": track.source, "kind": track.kind, "playing": track.playing,
            "duration_ms": track.duration_ms, "position_ms": track.position_ms,
            "cover_bytes": len(cover) if cover else 0,
        }
        report["resolved"] = {"image": image, "album": album, "tier": tier}
    (LOG_DIR / "diagnose.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    logging.info("Diagnostic written: %s", json.dumps(report, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if os.name != "nt":
        raise SystemExit(f"{APP_NAME} requires Windows 10 or later.")
    native_origin = next((arg for arg in sys.argv[1:] if arg.startswith("chrome-extension://")), None)
    if "--native-host" in sys.argv or native_origin:
        if native_origin and native_origin.rstrip("/") != YOUTUBE_EXTENSION_ORIGIN.rstrip("/"):
            raise SystemExit("Unrecognized Chrome extension origin.")
        run_native_host()
        return
    if "--dump" in sys.argv:
        dump_state()
        return
    if "--uninstall" in sys.argv:
        uninstall()
        return
    if "--install" in sys.argv:
        install_autostart()
        return

    # Keep Chrome native messaging wired up on every launch, so a moved or
    # freshly extracted EXE still receives the extension's artwork data. Only a
    # compiled build can act as a native host, so skip this when running the
    # source directly (sys.executable would be python.exe).
    if globals().get("__compiled__") is not None:
        try:
            register_native_host(app_executable())
        except Exception as exc:
            logging.warning("Native host self-registration failed: %s", exc)

    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not mutex:
        logging.error("Could not create single-instance mutex: %s", ctypes.WinError(ctypes.get_last_error()))
        return
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(mutex)
        return
    logging.info("%s launch requested; background-only mode active", APP_NAME)
    _cleanup_orphan_relays()
    Bridge().wait_forever()


if __name__ == "__main__":
    main()
