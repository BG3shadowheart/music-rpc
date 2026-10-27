"""Cross-platform data models."""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Track:
    """A single playable item, independent of the platform that reported it."""

    title: str
    artist: str = ""
    album: str = ""
    playing: bool = True
    source: str = ""
    duration_ms: int | None = None
    position_ms: int | None = None
    image_url: str | None = None
    video_id: str | None = None
    start_epoch: float | None = None
    kind: str = "local"            # "local" | "youtube"
    player: str = ""               # human readable player name
    track_id: str | None = None    # MPRIS mpris:trackid, used for dedupe
    local_art: str | None = None   # file:// path or on-disk cover

    @property
    def key(self):
        # The cover URL is deliberately not part of the key: artwork for one
        # song is resolved once and must not cause activity churn.
        return (self.title, self.artist, self.album, self.playing, self.kind)

    @property
    def identity(self):
        """A dedupe key that also separates players."""
        return (self.player, self.title, self.artist, self.album, self.track_id)

    def with_(self, **changes) -> "Track":
        return replace(self, **changes)


@dataclass(frozen=True)
class Snapshot:
    """Result of one backend poll.

    ``players`` contains one entry per player that reported usable metadata,
    in the order the backend discovered them. ``error`` is set when the backend
    could not talk to the platform at all (for example no D-Bus session).
    """

    players: tuple[Track, ...] = ()
    taken_at: float = 0.0
    error: str | None = None

    def __bool__(self):  # pragma: no cover - convenience only
        return bool(self.players)


@dataclass(frozen=True)
class ExtensionUpdate:
    """A packet received from the browser extension."""

    kind: str                      # "update" | "idle" | "ignored" | "error"
    track: Track | None = None
    message: str = ""
    received_at: float = 0.0

    @property
    def is_track(self):
        return self.kind == "update" and self.track is not None
