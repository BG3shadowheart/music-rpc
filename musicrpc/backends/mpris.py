"""MPRIS (D-Bus) media backend for Linux.

Everything is read through the standard ``org.mpris.MediaPlayer2`` interfaces:
fooyin, Chromium browsers, VLC, mpv, Spotify and every other compliant player
publish the same properties, so nothing scrapes a player's UI.

Two threads are involved:

* the engine thread calls :meth:`MprisBackend.poll`, which performs the actual
  property reads over a dedicated blocking connection;
* a watcher thread listens for ``PropertiesChanged`` and ``NameOwnerChanged``
  signals on its own connection and sets ``backend.wake`` so the engine can
  react immediately to play/pause/seek/player-restart events.

If the session bus disappears (session restart, D-Bus daemon restart) the
connection is dropped and reopened on the next poll; errors are reported as
``Snapshot.error`` and never crash the daemon.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse

from ..models import Snapshot, Track
from ..textutil import clean, normalize_artist, normalize_title, video_id_from_url

log = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

PLAYING = "Playing"
PAUSED = "Paused"
STOPPED = "Stopped"

_SIGNATURE = re.compile(r"^[ybnqiuxtdsogavh{}()]+$")


class MprisUnavailable(RuntimeError):
    """D-Bus session bus or MPRIS is not reachable."""


# ---------------------------------------------------------------------------
# Variant handling
# ---------------------------------------------------------------------------

def unwrap_variant(value, depth: int = 0):
    """Flatten jeepney's ``(signature, value)`` variant tuples.

    jeepney keeps D-Bus variants as two-tuples. Tests also feed plain Python
    values, so this works for both shapes.
    """
    if depth > 8:
        return value
    if (isinstance(value, tuple) and len(value) == 2
            and isinstance(value[0], str)
            and 0 < len(value[0]) <= 8
            and _SIGNATURE.match(value[0] or "")):
        return unwrap_variant(value[1], depth + 1)
    if isinstance(value, dict):
        return {key: unwrap_variant(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap_variant(item, depth + 1) for item in value]
    return value


def _artist_text(raw) -> str:
    raw = unwrap_variant(raw)
    if isinstance(raw, (list, tuple)):
        return clean(", ".join(str(item) for item in raw if str(item).strip()))
    return clean(raw)


def _int_or_none(value):
    value = unwrap_variant(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _status_playing(status: str) -> bool:
    return clean(status).casefold() == PLAYING.casefold()


def parse_metadata(metadata, *, status: str = STOPPED, position_us=None,
                   player: str = "", bus_name: str = "") -> Track | None:
    """Build a :class:`Track` from an MPRIS ``Metadata`` dictionary.

    Pure function: accepts plain values or jeepney variants, so it is unit
    tested without a bus.
    """
    data = unwrap_variant(metadata) if isinstance(metadata, dict) else {}
    if not isinstance(data, dict):
        return None

    title = clean(data.get("xesam:title"))
    artist = _artist_text(data.get("xesam:artist"))
    if not artist:
        artist = _artist_text(data.get("xesam:albumArtist"))
    album = clean(data.get("xesam:album"))
    title = normalize_title(title, artist)
    artist = normalize_artist(artist, title)
    if not title or title.casefold() in {"unknown", "unknown title"}:
        return None

    length_us = _int_or_none(data.get("mpris:length"))
    duration_ms = int(length_us / 1000) if length_us else None
    position_ms = None
    raw_position = _int_or_none(position_us)
    if raw_position is not None:
        position_ms = int(raw_position / 1000)
    if duration_ms is None and position_ms is not None:
        position_ms = None

    art = data.get("mpris:artUrl")
    art_url = clean(art) if isinstance(art, str) else ""
    image_url = None
    local_art = None
    if art_url.startswith(("http://", "https://")):
        image_url = art_url
    elif art_url.startswith("file://"):
        local_art = urllib.parse.unquote(urllib.parse.urlparse(art_url).path)
    elif art_url.startswith("/"):
        local_art = art_url

    track_id = clean(data.get("mpris:trackid")) or None
    if track_id == NO_TRACK:
        track_id = None

    video_id = video_id_from_url(image_url or "")
    kind = "youtube" if video_id else "local"

    return Track(
        title=title,
        artist=artist,
        album=album,
        playing=_status_playing(status),
        source=player or bus_name or "MPRIS",
        duration_ms=duration_ms,
        position_ms=position_ms,
        image_url=image_url,
        video_id=video_id,
        start_epoch=None,
        kind=kind,
        player=player or bus_name,
        track_id=track_id,
        local_art=local_art,
    )


def parse_player_properties(properties, *, identity: str = "", bus_name: str = "",
                            desktop_entry: str = "") -> Track | None:
    """Parse a Player ``GetAll`` result (``PlaybackStatus``, ``Metadata``...)."""
    data = unwrap_variant(properties) if isinstance(properties, dict) else {}
    if not isinstance(data, dict):
        return None
    status = clean(data.get("PlaybackStatus")) or STOPPED
    metadata = data.get("Metadata")
    player = clean(identity) or clean(desktop_entry) or clean(bus_name)
    return parse_metadata(metadata, status=status, position_us=data.get("Position"),
                          player=player, bus_name=bus_name)


# ---------------------------------------------------------------------------
# D-Bus plumbing
# ---------------------------------------------------------------------------

def _jeepney_connect():
    """Open a blocking session-bus connection (imported lazily)."""
    try:
        from jeepney.io.blocking import open_dbus_connection
    except Exception as exc:  # pragma: no cover - dependency missing
        raise MprisUnavailable(f"jeepney is not installed: {exc}") from exc
    try:
        return open_dbus_connection(bus="SESSION")
    except Exception as exc:
        raise MprisUnavailable(f"cannot connect to the D-Bus session bus: {exc}") from exc


class MprisBus:
    """Small wrapper around a jeepney blocking connection."""

    def __init__(self, connect=None, *, timeout: float = 3.0):
        self._connect = connect or _jeepney_connect
        self._connection = None
        self._timeout = timeout
        self._identity_cache: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def connection(self):
        with self._lock:
            if self._connection is None:
                self._connection = self._connect()
            return self._connection

    def close(self):
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def invalidate(self):
        self.close()

    # -- queries -----------------------------------------------------------
    def list_names(self) -> list[str]:
        from jeepney.bus_messages import DBus
        try:
            reply = self.connection().send_and_get_reply(
                DBus().ListNames(), timeout=self._timeout)
        except Exception as exc:
            self.invalidate()
            raise MprisUnavailable(f"ListNames failed: {exc}") from exc
        body = getattr(reply, "body", None) or ()
        names = body[0] if body else []
        return [str(name) for name in names if isinstance(name, str)]

    def list_players(self) -> list[str]:
        return sorted({name for name in self.list_names() if name.startswith(MPRIS_PREFIX)})

    def get_all(self, bus_name: str, interface: str) -> dict:
        from jeepney import DBusAddress, new_method_call
        address = DBusAddress(MPRIS_PATH, bus_name=bus_name,
                              interface=PROPERTIES_INTERFACE)
        message = new_method_call(address, "GetAll", "s", (interface,))
        try:
            reply = self.connection().send_and_get_reply(message, timeout=self._timeout)
        except Exception as exc:
            raise MprisUnavailable(f"GetAll({interface}) on {bus_name} failed: {exc}") from exc
        body = getattr(reply, "body", None) or ()
        result = body[0] if body else {}
        return unwrap_variant(result) if isinstance(result, dict) else {}

    def identity(self, bus_name: str) -> tuple[str, str]:
        with self._lock:
            cached = self._identity_cache.get(bus_name)
        if cached:
            return cached
        try:
            root = self.get_all(bus_name, ROOT_INTERFACE)
        except MprisUnavailable:
            return (bus_name, "")
        value = (clean(root.get("Identity")) or bus_name, clean(root.get("DesktopEntry")))
        with self._lock:
            self._identity_cache[bus_name] = value
        return value

    def forget(self, bus_name: str):
        with self._lock:
            self._identity_cache.pop(bus_name, None)


# ---------------------------------------------------------------------------
# Signal watcher
# ---------------------------------------------------------------------------

class MprisWatcher(threading.Thread):
    """Wakes the engine on MPRIS property changes and player (dis)appearance."""

    def __init__(self, wake: threading.Event, connect=None, *, stop_event=None):
        super().__init__(daemon=True, name="MprisWatcher")
        self.wake = wake
        self._connect = connect or _jeepney_connect
        self._stop = stop_event or threading.Event()
        self._connection = None

    def run(self):  # pragma: no cover - needs a live bus
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._listen()
                backoff = 1.0
            except Exception as exc:
                log.debug("MPRIS watcher disconnected: %s", exc)
            finally:
                self._close()
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 30.0)

    def _listen(self):
        from jeepney.bus_messages import DBus, MatchRule
        connection = self._connect()
        self._connection = connection
        rules = [
            MatchRule(type="signal", interface=PROPERTIES_INTERFACE,
                      member="PropertiesChanged", path=MPRIS_PATH),
            MatchRule(type="signal", interface="org.freedesktop.DBus",
                      member="NameOwnerChanged"),
            MatchRule(type="signal", interface=PLAYER_INTERFACE, member="Seeked"),
        ]
        bus = DBus()
        for rule in rules:
            connection.send_and_get_reply(bus.AddMatch(rule), timeout=5)
        log.debug("MPRIS watcher subscribed")
        while not self._stop.is_set():
            try:
                message = connection.receive(timeout=1.0)
            except TimeoutError:
                continue
            if message is not None:
                self.wake.set()

    def _close(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        self._close()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class MprisBackend:
    name = "mpris"

    def __init__(self, config=None, *, bus=None, connect=None, watch: bool = True):
        self.config = config
        self.wake = threading.Event()
        self.bus = bus or MprisBus(connect=connect)
        self._watch = watch
        self._watcher: MprisWatcher | None = None
        self._started = False
        self._last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._started or not self._watch:
            return
        self._started = True
        try:
            self._watcher = MprisWatcher(self.wake)
            self._watcher.start()
        except Exception as exc:  # pragma: no cover - thread start only
            log.debug("MPRIS watcher not started: %s", exc)
            self._watcher = None

    def close(self):
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
        self.bus.close()

    # -- polling -----------------------------------------------------------
    def poll(self) -> Snapshot:
        self.start()
        taken = time.monotonic()
        try:
            names = self.bus.list_players()
        except MprisUnavailable as exc:
            self._last_error = str(exc)
            return Snapshot(players=(), taken_at=taken, error=str(exc))

        players: list[Track] = []
        seen: set[tuple[str, str]] = set()
        problems: list[str] = []
        for bus_name in names:
            try:
                identity, desktop_entry = self.bus.identity(bus_name)
                properties = self.bus.get_all(bus_name, PLAYER_INTERFACE)
            except MprisUnavailable as exc:
                problems.append(str(exc))
                self.bus.forget(bus_name)
                continue
            track = parse_player_properties(properties, identity=identity,
                                            bus_name=bus_name,
                                            desktop_entry=desktop_entry)
            if track is None or not track.playing and not self._keep_paused():
                continue
            marker = (track.title.casefold(), (track.artist or "").casefold())
            if marker in seen:
                continue
            seen.add(marker)
            players.append(track)

        if self._last_error and not problems:
            log.info("MPRIS is reachable again")
            self._last_error = None
        error = "; ".join(problems) if problems else None
        if error and error != self._last_error:
            log.debug("MPRIS player read problem: %s", error)
            self._last_error = error
        return Snapshot(players=tuple(players), taken_at=taken, error=error)

    def _keep_paused(self) -> bool:
        config = self.config
        if config is None:
            return True
        return not bool(getattr(config, "clear_on_pause", True))


def list_mpris_players(connect=None) -> list[dict]:
    """Diagnostic helper: describe every MPRIS player on the bus."""
    bus = MprisBus(connect=connect)
    result = []
    try:
        for name in bus.list_players():
            try:
                identity, desktop_entry = bus.identity(name)
                properties = bus.get_all(name, PLAYER_INTERFACE)
                track = parse_player_properties(properties, identity=identity,
                                                bus_name=name, desktop_entry=desktop_entry)
                result.append({
                    "bus_name": name,
                    "identity": identity,
                    "desktop_entry": desktop_entry,
                    "status": clean(properties.get("PlaybackStatus")),
                    "title": track.title if track else "",
                    "artist": track.artist if track else "",
                    "album": track.album if track else "",
                    "art_url_kind": ("remote" if track and track.image_url
                                     else "local" if track and track.local_art
                                     else "none"),
                })
            except Exception as exc:
                result.append({"bus_name": name, "error": str(exc)})
    finally:
        bus.close()
    return result
