"""Browser-extension bridge.

The browser cannot talk to the daemon directly: it starts the native messaging
host, which speaks Chrome's length-prefixed stdio protocol. On the Windows build
that host connected to a loopback TCP port. The Linux daemon listens on a
Unix socket in ``$XDG_RUNTIME_DIR`` instead, so nothing is exposed to the
network at all.

``parse_extension_packet`` below is a port of the Windows ``accept()`` method and
keeps the exact same response strings, so the existing extension keeps working
unchanged. Two optional fields were *added* (``jsPlaybackState`` and
``jsPositionSec`` / ``jsDurationSec``) because MPRIS is not always available for
every browser build; the Windows build simply ignores them.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

from . import paths
from .constants import BRIDGE_MAX_FRAME
from .models import ExtensionUpdate, Track
from .textutil import (
    clean,
    normalize_artist,
    normalize_title,
    to_epoch_seconds,
    video_id_from_url,
    yt_thumb_urls,
)

log = logging.getLogger(__name__)

IDLE_SENTINEL = "#*IDLE*#"
SUCCESS_UPDATED = "0_SUCCESS: PRESENCE_UPDATED"
SUCCESS_CLEARED = "0_SUCCESS: PRESENCE_CLEARED"
SUCCESS_IGNORED = "0_SUCCESS: REGULAR_YOUTUBE_IGNORED"
SUCCESS_RECEIVED = "0_SUCCESS: MESSAGE_RECEIVED"
ERROR_INVALID = "1_CLIENT_ERROR: Invalid native message"
ERROR_NO_TITLE = "1_CLIENT_ERROR: Missing track title"

_PLAYBACK_STATES = {
    "playing": True,
    "paused": False,
    "pause": False,
    "stopped": False,
    "none": False,
    "buffering": True,
}


def _response(message: str) -> dict:
    return {"data": message}


def parse_extension_packet(packet, *, now: float = 0.0, wall: float | None = None,
                           ignore_regular_youtube: bool = True) -> tuple[ExtensionUpdate, dict]:
    """Validate one native-messaging packet.

    Returns ``(update, response)`` where ``response`` is what the native host
    sends back to the extension verbatim.
    """
    wall = time.time() if wall is None else wall
    try:
        if not isinstance(packet, dict):
            return ExtensionUpdate("error", None, ERROR_INVALID, now), _response(ERROR_INVALID)

        presence = packet.get("presenceData")
        if presence == IDLE_SENTINEL:
            return ExtensionUpdate("idle", None, SUCCESS_CLEARED, now), _response(SUCCESS_CLEARED)

        if not isinstance(presence, dict):
            return (ExtensionUpdate("noop", None, SUCCESS_RECEIVED, now),
                    _response(SUCCESS_RECEIVED))

        application_type = packet.get("jsApplicationType")
        if ignore_regular_youtube and (application_type != "youtubeMusic"
                                      or presence.get("type") != 2):
            log.debug("Extension packet ignored (application=%s type=%s)",
                      application_type, presence.get("type"))
            return ExtensionUpdate("ignored", None, SUCCESS_IGNORED, now), _response(SUCCESS_IGNORED)

        title = clean(presence.get("details") or packet.get("jsTitle"))
        artist = clean(packet.get("jsAuthor") or presence.get("state"))
        if artist.casefold().startswith("by "):
            artist = artist[3:].strip()
        title = normalize_title(title, artist)
        artist = normalize_artist(artist, title)
        album = clean(packet.get("jsAlbum"))
        if not title:
            return ExtensionUpdate("error", None, ERROR_NO_TITLE, now), _response(ERROR_NO_TITLE)

        assets = presence.get("assets") if isinstance(presence.get("assets"), dict) else {}
        if not album:
            candidate_album = clean(assets.get("large_text"))
            if candidate_album.casefold() not in {title.casefold(), artist.casefold()}:
                album = candidate_album

        image_url = assets.get("large_image")
        video_id = (video_id_from_url(packet.get("jsVideoUrl"))
                    or video_id_from_url(image_url)
                    or (clean(packet.get("jsVideoId")) or None))
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
            position = max(0, int((wall - start_epoch) * 1000))
            if duration:
                position = min(position, duration)

        # Optional fields added for Linux: the extension knows the real state
        # even when the browser exposes no MPRIS player at all.
        playing = True
        state = clean(packet.get("jsPlaybackState")).casefold()
        if state in _PLAYBACK_STATES:
            playing = _PLAYBACK_STATES[state]
        try:
            if packet.get("jsDurationSec") is not None:
                duration = max(0, int(float(packet["jsDurationSec"]) * 1000)) or duration
            if packet.get("jsPositionSec") is not None:
                position = max(0, int(float(packet["jsPositionSec"]) * 1000))
        except (TypeError, ValueError):
            pass

        track = Track(
            title=title,
            artist=artist,
            album=album,
            playing=playing,
            source="YouTube Music",
            duration_ms=duration,
            position_ms=position,
            image_url=image_url,
            video_id=video_id,
            start_epoch=start_epoch,
            kind="youtube",
            player="YouTube Music",
            track_id=f"ytm:{video_id}" if video_id else None,
        )
        return ExtensionUpdate("update", track, SUCCESS_UPDATED, now), _response(SUCCESS_UPDATED)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Native message handling failed: %s", exc)
        return ExtensionUpdate("error", None, ERROR_INVALID, now), _response(ERROR_INVALID)


class ExtensionBridge:
    """Unix-socket server for the native host process."""

    def __init__(self, socket_path: Path | None = None, *, clock=time.monotonic,
                 ignore_regular_youtube: bool = True):
        self.socket_path = Path(socket_path) if socket_path else paths.bridge_socket_path()
        self.clock = clock
        self.ignore_regular_youtube = ignore_regular_youtube
        self._lock = threading.Lock()
        self._track: Track | None = None
        self._received_at = float("-inf")
        self._last = ExtensionUpdate("idle", None, "", float("-inf"))
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # -- packet handling (also used directly by tests) ----------------------
    def handle(self, packet) -> dict:
        update, response = parse_extension_packet(
            packet, now=self.clock(), ignore_regular_youtube=self.ignore_regular_youtube)
        with self._lock:
            self._last = update
            if update.is_track:
                self._track = update.track
                self._received_at = update.received_at
            elif update.kind in ("idle", "ignored", "error"):
                self._track = None
                self._received_at = update.received_at
        if update.kind == "update":
            log.info("Extension sent: %r by %r (video=%s)", update.track.title,
                     update.track.artist, update.track.video_id)
        return response

    def current_track(self, max_age: float | None = None):
        with self._lock:
            if self._track is None:
                return None, float("inf")
            age = self.clock() - self._received_at
            if max_age is not None and age > max_age:
                return None, age
            return self._track, age

    @property
    def last_update(self) -> ExtensionUpdate:
        return self._last

    def clear(self):
        with self._lock:
            self._track = None
            self._received_at = float("-inf")

    # -- socket server -----------------------------------------------------
    def start(self) -> bool:
        paths.ensure_dir(self.socket_path.parent)
        try:
            if self.socket_path.exists() or self.socket_path.is_socket():
                # Stale socket from a crashed instance: remove it, but only if
                # nothing is listening.
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.5)
                    probe.connect(str(self.socket_path))
                    probe.close()
                    log.warning("Extension bridge socket already in use: %s", self.socket_path)
                    return False
                except OSError:
                    probe.close()
                    self.socket_path.unlink(missing_ok=True)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            server.listen(8)
            server.settimeout(1.0)
            self._server = server
        except OSError as exc:
            log.warning("Extension bridge could not start: %s", exc)
            return False
        self._thread = threading.Thread(target=self._serve, daemon=True, name="ExtensionBridge")
        self._thread.start()
        log.info("Extension bridge listening on %s", self.socket_path)
        return True

    def _serve(self):
        while not self._stopping.is_set():
            try:
                client, _address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client, args=(client,), daemon=True,
                             name="ExtensionClient").start()

    def _client(self, client: socket.socket):
        try:
            client.settimeout(5.0)
            stream = client.makefile("rb")
            raw = stream.readline(BRIDGE_MAX_FRAME + 1)
            if not raw or len(raw) > BRIDGE_MAX_FRAME:
                return
            try:
                packet = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = _response(ERROR_INVALID)
            else:
                response = self.handle(packet)
            client.sendall(json.dumps(response).encode("utf-8") + b"\n")
        except Exception as exc:
            log.debug("Extension bridge client error: %s", exc)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self):
        self._stopping.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    def close(self):
        self.stop()
