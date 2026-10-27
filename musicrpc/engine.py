"""The presence state machine.

Deliberately free of platform and Discord details so it can be driven by a fake
backend, a fake Discord client and a fake clock in tests:

    backend.poll()  -> Snapshot(players=(Track, ...))
    bridge.current_track() -> (Track | None, age_seconds)
    resolver.resolve(track) -> (image_url, album, tier)
    rpc.update(Activity) / rpc.clear()

Rules (identical to the Windows build):

* presence is cleared on pause and nine seconds after a player disappears;
* a track change is published once, duplicates never cause activity churn;
* the progress bar is refreshed every ``refresh_seconds`` and after a seek;
* a failed Discord update drops the connection and retries with backoff;
* artwork failures are not fatal.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .config import Config
from .discord import Activity, PyPresenceClient
from .merge import merge_tracks
from .models import Snapshot, Track
from .selection import choose_track

log = logging.getLogger(__name__)


@dataclass
class EngineResult:
    """What one tick did - used by the CLI, the tray and the tests."""

    track: Track | None = None
    activity: Activity | None = None
    sent: bool = False
    cleared: bool = False
    discord_state: str = "disconnected"
    artwork_tier: str = "none"
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def build_activity(track: Track, album: str, image_url: str | None,
                   *, start: int | None = None, end: int | None = None) -> Activity:
    """Preserve the original Windows formatting of the Discord card."""
    album_text = album or track.album
    if album_text and album_text.casefold() in {track.title.casefold(), track.artist.casefold()}:
        album_text = ""
    return Activity(
        details=(track.title or "Unknown")[:128],
        state=(track.artist or "Music")[:128],
        large_image=(image_url or None),
        large_text=(album_text[:128] if album_text else None),
        start=start,
        end=end,
        activity_type="listening",
        status_display_type="details",
    )


class PresenceEngine:
    def __init__(self, *, backend, config: Config, resolver,
                 rpc_factory=None, bridge=None, clock=time.monotonic,
                 wall=time.time, on_event=None, relay_enabled=False):
        self.backend = backend
        self.config = config
        self.resolver = resolver
        self.bridge = bridge
        self.clock = clock
        self.wall = wall
        self.on_event = on_event
        self.relay_enabled = relay_enabled
        self._rpc_factory = rpc_factory or (lambda: PyPresenceClient(config.discord_app_id))

        self._rpc = None
        self._stop = threading.Event()
        self._last_sent_key = None
        self._last_update_at = float("-inf")
        self._missing_since = None
        self._next_rpc_attempt = 0.0
        self._rpc_backoff = config.discord_retry_seconds
        self._resolved_key = None
        self._resolved: tuple = (None, "", "none")
        self._last_art_retry = float("-inf")
        self._position_state = None
        self._last_track: Track | None = None
        self._backend_error: str | None = None
        self._last_error_logged = 0.0
        self.last_result = EngineResult()

    # -- helpers -----------------------------------------------------------
    def _emit(self, kind, value):
        if self.on_event:
            try:
                self.on_event(kind, value)
            except Exception:  # pragma: no cover - UI callbacks must not break us
                pass

    def _log_error(self, message: str):
        now = self.clock()
        if now - self._last_error_logged > 30:
            self._last_error_logged = now
            log.debug("engine: %s", message)

    def _extension_track(self):
        if self.bridge is None:
            return None, float("inf")
        try:
            track, age = self.bridge.current_track(
                max_age=self.config.extension_sticky_seconds)
        except Exception as exc:
            self._log_error(f"extension bridge: {exc}")
            return None, float("inf")
        if track is None:
            return None, age
        if not track.playing and self.config.clear_on_pause:
            return track, age
        return track, age

    def _select(self, snapshot: Snapshot) -> Track | None:
        """Pick the platform track, then overlay extension data."""
        base = choose_track(snapshot.players, self.config.player_priority,
                            self.config.ignored_players)
        extension, age = self._extension_track()
        if base is None and extension is not None and age > self.config.extension_fresh_seconds:
            # The browser was closed without sending its idle message.
            extension = None
        if extension is not None:
            self.resolver.remember_player_track(extension)
        return merge_tracks(base, extension)

    def _position(self, track: Track, now: float):
        """Estimate the playback position; returns (position_ms, seek_detected)."""
        seek = False
        if track.start_epoch:
            position = max(0.0, (self.wall() - track.start_epoch) * 1000.0)
            self._position_state = None
        elif track.position_ms is None:
            position = None
            self._position_state = None
        else:
            reported = max(0.0, float(track.position_ms))
            previous = self._position_state
            if previous is None or previous["identity"] != track.identity:
                position = reported
            elif not track.playing:
                position = reported
            elif abs(reported - previous["reported"]) > 1500:
                # A jump in the player's reported position: the user seeked.
                position = reported
                seek = True
            else:
                position = previous["position"] + (now - previous["at"]) * 1000.0
            if position is not None and track.duration_ms:
                position = min(position, float(track.duration_ms))
            self._position_state = {
                "identity": track.identity,
                "position": position if position is not None else reported,
                "reported": reported,
                "at": now,
            }
        if position is not None and track.duration_ms:
            position = min(position, float(track.duration_ms))
        return position, seek

    def _local_cover(self, track: Track):
        if not self.relay_enabled or not track.local_art:
            return None
        from .artwork import fetch_art_bytes
        return fetch_art_bytes(track.local_art)

    # -- discord -----------------------------------------------------------
    def _connect(self, now: float, result: EngineResult):
        if not self.config.discord_enabled:
            result.discord_state = "disabled"
            return None
        if self._rpc is not None:
            return self._rpc
        if now < self._next_rpc_attempt:
            result.discord_state = "waiting"
            return None
        client = None
        try:
            client = self._rpc_factory()
            client.connect()
        except Exception as exc:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            self._rpc = None
            wait = self._rpc_backoff
            self._next_rpc_attempt = now + wait
            self._rpc_backoff = min(wait * 2, self.config.discord_max_backoff_seconds)
            result.discord_state = "disconnected"
            result.error = str(exc)
            self._emit("discord", "Open the Discord desktop app to connect")
            self._emit("error", str(exc))
            log.warning("Discord RPC connection failed (retrying in %.0fs): %s", wait, exc)
            return None
        self._rpc = client
        self._next_rpc_attempt = 0.0
        self._rpc_backoff = self.config.discord_retry_seconds
        self._last_sent_key = None          # force a fresh activity after reconnect
        result.discord_state = "connected"
        self._emit("discord", "Connected")
        log.info("Connected to the local Discord client with application id %s",
                 self.config.discord_app_id)
        return client

    def _drop_rpc(self, exc, now: float, result: EngineResult):
        client, self._rpc = self._rpc, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self._last_sent_key = None
        self._next_rpc_attempt = now + self._rpc_backoff
        self._rpc_backoff = min(self._rpc_backoff * 2, self.config.discord_max_backoff_seconds)
        result.discord_state = "reconnecting"
        result.error = str(exc)
        self._emit("discord", "Reconnecting to Discord…")
        self._emit("error", str(exc))
        log.warning("Discord RPC update failed: %s", exc)

    def _clear(self, client, now: float, result: EngineResult, reason: str):
        if client is not None and self._last_sent_key is not None:
            try:
                client.clear()
                log.info("Discord activity cleared because %s", reason)
            except Exception as exc:
                self._drop_rpc(exc, now, result)
        self._last_sent_key = None
        self._resolved_key = None
        self._position_state = None
        result.cleared = True
        self._emit("presence", "cleared")

    # -- one iteration -----------------------------------------------------
    def tick(self) -> EngineResult:
        result = self._tick()
        self.last_result = result
        return result

    def _tick(self) -> EngineResult:
        now = self.clock()
        result = EngineResult()

        try:
            snapshot = self.backend.poll()
        except Exception as exc:                      # backend contract violation
            snapshot = Snapshot(players=(), taken_at=now, error=str(exc))
        if snapshot.error:
            if snapshot.error != self._backend_error:
                self._backend_error = snapshot.error
                log.warning("Media backend problem: %s", snapshot.error)
                self._emit("error", snapshot.error)
        elif self._backend_error:
            log.info("Media backend recovered")
            self._backend_error = None

        track = self._select(snapshot)
        client = self._rpc
        if client is None:
            client = self._connect(now, result)
        result.discord_state = result.discord_state if result.discord_state != "disconnected" \
            else ("connected" if self._rpc else "disconnected")

        # ---- nothing playing ------------------------------------------------
        if track is None:
            if self._missing_since is None:
                self._missing_since = now
                if self._last_sent_key is not None:
                    log.info("No supported player is active; clearing Discord activity "
                             "in %.0f seconds unless another track starts",
                             self.config.no_track_grace_seconds)
            self._last_track = None
            self._emit("track", None)
            if self._missing_since is not None and \
                    now - self._missing_since >= self.config.no_track_grace_seconds:
                self._clear(client, now, result, "no supported player is active")
            return result

        self._missing_since = None

        # ---- paused ---------------------------------------------------------
        if not track.playing and self.config.clear_on_pause:
            self._last_track = track
            self._emit("track", (track, None))
            self._clear(client, now, result, "media is paused")
            return result

        if self._last_track is None or track.key != self._last_track.key:
            log.info("Playing: title=%r artist=%r album=%r player=%r kind=%s video=%s",
                     track.title, track.artist, track.album, track.player, track.kind,
                     bool(track.video_id))
            self._position_state = None
        self._last_track = track

        # ---- artwork --------------------------------------------------------
        if self.config.artwork_enabled:
            if self._resolved_key != track.key:
                self._resolved_key = track.key
                self._resolved = self._resolve(track)
                self._last_art_retry = now
            elif (self._resolved[2] in ("relay", "none", "cache-unverified", "disabled")
                  and now - self._last_art_retry > self.config.art_retry_seconds):
                self._last_art_retry = now
                snapshot_result = self._resolved
                retried = self._resolve(track, prefer_permanent=True, bypass_memo=True)
                if retried[0] and retried[2] not in ("none", "relay", "disabled"):
                    self._resolved = retried
                    log.info("Upgraded artwork to a permanent source: tier=%s", retried[2])
                else:
                    self._resolved = snapshot_result
        else:
            self._resolved = (None, track.album, "disabled")
            self._resolved_key = track.key
        image_url, album, tier = self._resolved
        result.artwork_tier = tier

        # ---- activity -------------------------------------------------------
        position, seek = self._position(track, now)
        start = end = None
        if position is not None and track.duration_ms and track.playing:
            start = int(self.wall() - position / 1000.0)
            end = start + max(0, int(track.duration_ms // 1000))
        activity = build_activity(track, album, image_url, start=start, end=end)
        result.track = track
        result.activity = activity

        send_key = (track.title, track.artist, album, image_url, track.playing)
        due = (send_key != self._last_sent_key
               or now - self._last_update_at > self.config.refresh_seconds
               or seek)
        if client is not None and due:
            try:
                client.update(activity)
                self._last_sent_key = send_key
                self._last_update_at = now
                result.sent = True
                result.discord_state = "connected"
                self._emit("discord", "Connected • activity updated")
                log.debug("Activity sent: title=%r image=%s tier=%s", track.title,
                          bool(image_url), tier)
            except Exception as exc:
                self._drop_rpc(exc, now, result)
        elif client is None:
            result.notes.append("discord-unavailable")

        self._emit("track", (track, self._resolved[0], album, tier))
        return result

    def _resolve(self, track: Track, prefer_permanent=False, bypass_memo=False):
        try:
            return self.resolver.resolve(track, self._local_cover(track),
                                        prefer_permanent=prefer_permanent,
                                        bypass_memo=bypass_memo)
        except Exception as exc:
            log.warning("Artwork resolution failed (continuing without artwork): %s", exc)
            return None, track.album, "none"

    # -- loop --------------------------------------------------------------
    def run(self, stop_event: threading.Event | None = None):
        stop = stop_event or self._stop
        log.info("%s is watching for media playback", "Music RPC")
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Unexpected error in the presence loop; continuing")
            self._wait(stop)
        self.shutdown()

    def _wait(self, stop: threading.Event):
        timeout = max(0.2, float(self.config.poll_seconds))
        wake = getattr(self.backend, "wake", None)
        deadline = self.clock() + timeout
        while not stop.is_set():
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            if isinstance(wake, threading.Event):
                if wake.wait(min(remaining, 0.5)):
                    wake.clear()
                    return
            elif stop.wait(min(remaining, 0.5)):
                return

    def shutdown(self):
        client, self._rpc = self._rpc, None
        if client is not None:
            try:
                client.clear()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
        try:
            self.resolver.store.save()
        except Exception:
            pass
        try:
            self.backend.close()
        except Exception:
            pass

    def stop(self):
        self._stop.set()
