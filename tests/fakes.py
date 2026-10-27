"""Test doubles for the engine, Discord client and backend."""
from __future__ import annotations

from dataclasses import replace

from musicrpc.models import Snapshot, Track


def track(**kwargs) -> Track:
    defaults = dict(
        title="Song",
        artist="Artist",
        album="Album",
        playing=True,
        source="Fooyin",
        duration_ms=200_000,
        position_ms=10_000,
        image_url=None,
        kind="local",
        player="fooyin",
        track_id="/org/mpris/MediaPlayer2/TrackList/1",
    )
    defaults.update(kwargs)
    return Track(**defaults)


class FakeClock:
    def __init__(self, start: float = 1000.0, wall: float = 1_700_000_000.0):
        self.now = start
        self.wall_now = wall

    def __call__(self) -> float:
        return self.now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, seconds: float):
        self.now += seconds
        self.wall_now += seconds


class FakeRPC:
    def __init__(self, *, connect_failures: int = 0, update_failures: int = 0,
                 clear_failures: int = 0):
        self.connect_failures = connect_failures
        self.update_failures = update_failures
        self.clear_failures = clear_failures
        self.updates = []
        self.clears = 0
        self.connected = False
        self.closed = False
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise RuntimeError("Discord is not running")
        self.connected = True

    def update(self, activity):
        if self.update_failures > 0:
            self.update_failures -= 1
            raise RuntimeError("Discord IPC broke")
        if not self.connected:
            raise RuntimeError("not connected")
        self.updates.append(activity)
        return {"cmd": "SET_ACTIVITY", "data": {"application_id": "1", "type": 2}}

    def clear(self):
        self.clears += 1
        if self.clear_failures > 0:
            self.clear_failures -= 1
            raise RuntimeError("clear failed")
        return {}

    def close(self):
        self.closed = True
        self.connected = False


class SequenceFactory:
    """Returns each client in turn; the last one is reused."""

    def __init__(self, *clients):
        self.clients = list(clients)
        self.created = 0

    def __call__(self):
        index = min(self.created, len(self.clients) - 1)
        self.created += 1
        return self.clients[index]


class FakeResolver:
    def __init__(self, *results, raises: Exception | None = None):
        self.results = list(results) or [(None, "", "none")]
        self.raises = raises
        self.calls = []
        self.remembered = []

    def resolve(self, track, local_cover=None, prefer_permanent=False, bypass_memo=False):
        self.calls.append({"track": track, "prefer_permanent": prefer_permanent,
                           "bypass_memo": bypass_memo})
        if self.raises is not None:
            raise self.raises
        index = min(len(self.calls) - 1, len(self.results) - 1)
        return self.results[index]

    def remember_player_track(self, track):
        self.remembered.append(track)

    class _Store:
        def save(self):
            pass

    store = _Store()


class StubBackend:
    name = "stub"

    def __init__(self, *snapshots, wake=None):
        import threading
        self.snapshots = list(snapshots) or [Snapshot(players=())]
        self.polls = 0
        self.closed = False
        self.wake = wake or threading.Event()

    def poll(self) -> Snapshot:
        index = min(self.polls, len(self.snapshots) - 1)
        self.polls += 1
        return self.snapshots[index]

    def close(self):
        self.closed = True


class RecordingBackend(StubBackend):
    """Records every snapshot returned, so tests can assert poll counts."""


def snapshots(*players) -> list[Snapshot]:
    """Build one snapshot per argument (a tuple of tracks, or a single track)."""
    result = []
    for item in players:
        if item is None:
            result.append(Snapshot(players=()))
        elif isinstance(item, Track):
            result.append(Snapshot(players=(item,)))
        else:
            result.append(Snapshot(players=tuple(item)))
    return result


class FakeBridge:
    def __init__(self, track_value=None, age=0.0):
        self.track = track_value
        self.age = age
        self.cleared = 0

    def current_track(self, max_age=None):
        if self.track is None:
            return None, float("inf")
        return self.track, self.age

    def remember(self, value):
        pass

    def clear(self):
        self.cleared += 1


def with_position(value: Track, position_ms: int) -> Track:
    return replace(value, position_ms=position_ms)
