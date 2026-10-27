"""Artwork resolution and caching.

This is a port of the artwork pipeline from the Windows ``app.py``. The tiers,
their order, the verification rules and the on-disk cache format are unchanged:

    1. youtube ......... https://i.ytimg.com/vi/<id>/maxresdefault.jpg
    2. cache ........... a cover URL already proven for this track
    3. player .......... the URL the player/extension reported
    4. album-cache ..... a cover URL already proven for this album
    5. catalog ......... Apple Music / Deezer album art on their CDNs
    6. relay ........... session-only URL for embedded art (opt-in on Linux)
    7. search .......... video id derived from title + artist

Every candidate is verified with a real HTTP fetch before it is handed to
Discord, because Discord caches failed image fetches. Artwork failures are
never fatal: ``resolve()`` returns ``(None, album, "none")`` instead of raising.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .constants import HTTP_TIMEOUT, PROBE_TTL_SECONDS, USER_AGENT
from .models import Track
from .textutil import (
    album_cache_key,
    clean,
    slug,
    strip_decorations,
    track_cache_key,
    video_id_from_url,
    yt_thumb_urls,
)

log = logging.getLogger(__name__)

try:  # Pillow is optional: without it only magic-byte checks are possible.
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - exercised only on minimal systems
    Image = None
    ImageOps = None


# ---------------------------------------------------------------------------
# HTTP + image probing
# ---------------------------------------------------------------------------

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


def looks_like_image(data, content_type=None) -> bool:
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


@dataclass
class ArtworkSettings:
    enabled: bool = True
    catalog_lookup: bool = True
    youtube_search: bool = True


class Fetcher:
    """Default network fetcher with a short-lived probe cache."""

    def __init__(self, timeout=HTTP_TIMEOUT):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._probe_cache: dict[str, tuple[bool, float]] = {}

    def get(self, url, *, timeout=None, max_bytes=4_000_000, headers=None):
        return http_get(url, timeout=timeout or self.timeout,
                        max_bytes=max_bytes, headers=headers)

    def probe(self, url, *, attempts=1, min_side=120, max_bytes=1_500_000) -> bool:
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return False
        now = time.monotonic()
        with self._lock:
            cached = self._probe_cache.get(url)
            if cached and now - cached[1] < PROBE_TTL_SECONDS:
                return cached[0]
        ok = False
        for attempt in range(max(1, attempts)):
            try:
                data, content_type = self.get(url, max_bytes=max_bytes)
                if looks_like_image(data, content_type):
                    size = image_size(data)
                    if size is None:
                        ok = True
                    else:
                        ok = size[0] >= min_side and size[1] >= min_side
            except Exception:
                ok = False
            if ok:
                break
            if attempt + 1 < max(1, attempts):
                time.sleep(0.5)
        with self._lock:
            self._probe_cache[url] = (ok, now)
            if len(self._probe_cache) > 400:
                self._probe_cache.pop(next(iter(self._probe_cache)))
        return ok


class NullFetcher:
    """Fetcher used by ``--dump`` and tests: never touches the network."""

    def get(self, url, **_kwargs):
        return None, ""

    def probe(self, url, **_kwargs):
        return False


def image_size(data):
    if Image is None or not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return None


def fetch_art_bytes(url_or_path) -> bytes | None:
    """Read local or remote artwork bytes without ever uploading anything."""
    if not url_or_path:
        return None
    target = str(url_or_path)
    try:
        if target.startswith("file://"):
            path = Path(urllib.parse.unquote(urllib.parse.urlparse(target).path))
            if path.is_file() and path.stat().st_size < 8_000_000:
                return path.read_bytes()
            return None
        if target.startswith(("http://", "https://")):
            data, _ctype = http_get(target, max_bytes=4_000_000)
            return data
        path = Path(os.path.expanduser(target))
        if path.is_file() and path.stat().st_size < 8_000_000:
            return path.read_bytes()
    except Exception as exc:
        log.debug("Could not read artwork %s: %s", target, exc)
    return None


# ---------------------------------------------------------------------------
# Permanent artwork cache (same schema as the Windows build)
# ---------------------------------------------------------------------------

class ArtworkStore:
    VERSION = 2
    LIMIT = 4000

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.artwork_cache_file()
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
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("Artwork cache at %s is unreadable (%s); starting fresh",
                        self.path, exc)

    def save(self):
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            payload = json.dumps(self._data, ensure_ascii=False)
        try:
            paths.ensure_dir(self.path.parent)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        except Exception as exc:
            log.warning("Could not persist artwork cache: %s", exc)

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

    def __len__(self):
        return len(self._data["tracks"]) + len(self._data["albums"])


# ---------------------------------------------------------------------------
# Catalog lookups (permanent CDN URLs)
# ---------------------------------------------------------------------------

def _itunes_search(fetcher, params):
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
    data, _content_type = fetcher.get(url, timeout=7, max_bytes=1_500_000)
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


def itunes_album_art(fetcher, artist, album):
    if not album:
        return None, None
    term = " ".join(part for part in (artist, album) if part)
    try:
        results = _itunes_search(fetcher, {"term": term, "entity": "album", "limit": 6})
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
        if target_artist and target_artist not in slug(result.get("artistName")) and not exact_album:
            continue
        artwork = _artwork_600(result)
        if artwork:
            return artwork, clean(result.get("collectionName"))
    return None, None


def itunes_song_art(fetcher, title, artist, album=""):
    if not title:
        return None, None
    term = " ".join(part for part in (artist, title, album) if part)
    try:
        results = _itunes_search(fetcher, {"term": term, "entity": "song", "limit": 8})
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


def deezer_song_art(fetcher, title, artist):
    if not title:
        return None, None
    query = f"{artist} {title}".strip()
    try:
        data, _content_type = fetcher.get(
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


def youtube_search_video_id(fetcher, title, artist):
    """Best-effort video id lookup used only when no player id exists."""
    query = " ".join(part for part in (artist, strip_decorations(title)) if part).strip()
    if not query:
        return None
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": query, "hl": "en", "gl": "US"})
    try:
        data, _content_type = fetcher.get(url, timeout=8, max_bytes=4_000_000,
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
# Relay interface (see relay.py for the opt-in Linux implementation)
# ---------------------------------------------------------------------------

class NullRelay:
    public_url = None

    def set_image(self, data, cache_key=None):
        return None

    def clear(self):
        pass


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class ArtworkResolver:
    def __init__(self, *, fetcher=None, store=None, relay=None,
                 settings: ArtworkSettings | None = None):
        # ``or`` would be wrong here: ArtworkStore defines __len__, so an empty
        # cache is falsy and would be silently replaced by a new store.
        self.fetcher = fetcher if fetcher is not None else Fetcher()
        self.store = store if store is not None else ArtworkStore()
        self.relay = relay if relay is not None else NullRelay()
        self.settings = settings if settings is not None else ArtworkSettings()
        self._memo: dict = {}
        self._memo_lock = threading.Lock()

    # -- persistence ---------------------------------------------------------
    def remember_player_track(self, track: Track) -> None:
        """Persist the extension's permanent mapping (title/artist -> video id)."""
        if track.kind != "youtube":
            return
        key = track_cache_key(track.title, track.artist)
        if not key.strip("|"):
            return
        video_id = track.video_id or video_id_from_url(track.image_url)
        if video_id:
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

    # -- candidate generation ------------------------------------------------
    def _candidates(self, track: Track, local_cover, prefer_permanent=False):
        key = track_cache_key(track.title, track.artist)
        cached = self.store.get_track(key) if key.strip("|") else None

        if track.kind == "youtube":
            video_id = (track.video_id or (cached or {}).get("video_id")
                        or video_id_from_url(track.image_url))
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
            if self.settings.catalog_lookup:
                url, album = self._catalog_for(track)
                if url:
                    yield "catalog", url, album or track.album
            if not prefer_permanent:
                url = self.relay.set_image(local_cover, key)
                if url:
                    yield "relay", url, track.album
            if not video_id and self.settings.youtube_search:
                found = youtube_search_video_id(self.fetcher, track.title, track.artist)
                for url in yt_thumb_urls(found):
                    yield "search", url, track.album
            return

        # Local files: a confident catalog match is both accurate and
        # permanently cached by Discord, so it is tried before the relay.
        if cached and cached.get("url"):
            yield "cache", cached["url"], cached.get("album") or track.album
        # MPRIS players often publish a permanent CDN artwork URL themselves
        # (Spotify, Deezer, ...). It is a real candidate, not a last resort.
        if (track.image_url or "").startswith("https://"):
            yield "player", track.image_url, track.album
        if track.album and track.artist:
            album_entry = self.store.get_album(album_cache_key(track.artist, track.album))
            if album_entry and album_entry.get("url"):
                yield "album-cache", album_entry["url"], track.album
        if self.settings.catalog_lookup:
            url, album = self._catalog_for(track)
            if url:
                yield "catalog", url, album or track.album
        if not prefer_permanent:
            url = self.relay.set_image(local_cover, track_cache_key(track.title, track.artist))
            if url:
                yield "relay", url, track.album
        if self.settings.youtube_search:
            found = youtube_search_video_id(self.fetcher, track.title, track.artist)
            if found:
                for url in yt_thumb_urls(found):
                    yield "search", url, track.album

    def _catalog_for(self, track: Track):
        title = strip_decorations(track.title)
        if track.kind != "youtube" and track.album:
            url, album = itunes_album_art(self.fetcher, track.artist, track.album)
            if url:
                self.store.put_album(album_cache_key(track.artist, track.album), url)
                return url, album
        url, album = itunes_song_art(self.fetcher, title, track.artist, track.album)
        if url:
            return url, album
        url, album = deezer_song_art(self.fetcher, title, track.artist)
        if url:
            return url, album
        return None, None

    # -- public API ----------------------------------------------------------
    def resolve(self, track: Track, local_cover=None, prefer_permanent=False,
                bypass_memo=False):
        """Return ``(image_url, album, tier)``. Never returns an unverified URL."""
        if not self.settings.enabled:
            return None, track.album, "disabled"

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
        try:
            candidates = list(self._candidates(track, local_cover, prefer_permanent))
        except Exception as exc:
            log.warning("Artwork candidate generation failed: %s", exc)
            candidates = []

        for tier, url, candidate_album in candidates:
            if not url:
                continue
            if tier == "cache":
                cached_candidate = (url, candidate_album or album)
            # A cached permanent URL gets one extra probe attempt: a transient
            # network blip must not make a known-good cover look dead.
            if self.fetcher.probe(url, attempts=2 if tier == "cache" else 1):
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
            log.info("Artwork resolved: tier=%s url=%s%s", chosen[2], chosen[0],
                     (" rejected=" + ",".join(tried)) if tried else "")
        else:
            log.warning("No usable artwork found for %r by %r%s", track.title, track.artist,
                        (" (tried " + ", ".join(tried) + ")") if tried else "")

        with self._memo_lock:
            self._memo[memo_key] = chosen
            if len(self._memo) > 500:
                self._memo.pop(next(iter(self._memo)))
        return chosen

    def close(self):
        try:
            self.store.save()
        except Exception:
            pass


def thumbnail_data_url(data: bytes) -> str | None:  # pragma: no cover - helper
    if not data:
        return None
    digest = hashlib.sha256(data).hexdigest()[:8]
    return f"sha256:{digest}"
