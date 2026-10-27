"""MPRIS metadata parsing, player enumeration and error handling."""
from __future__ import annotations

import pytest

from musicrpc.backends.mpris import (
    MPRIS_PATH,
    PLAYER_INTERFACE,
    ROOT_INTERFACE,
    MprisBackend,
    MprisBus,
    MprisUnavailable,
    parse_metadata,
    parse_player_properties,
    unwrap_variant,
)
from musicrpc.config import Config
from musicrpc.models import Snapshot


# ---------------------------------------------------------------------------
# Variant / metadata parsing
# ---------------------------------------------------------------------------

def test_unwrap_variant_flattens_jeepney_tuples():
    assert unwrap_variant(("s", "hello")) == "hello"
    assert unwrap_variant({"a": ("s", "x"), "b": ("as", ["y", "z"])}) == {
        "a": "x", "b": ["y", "z"]}
    assert unwrap_variant(("a{sv}", {"k": ("s", "v")})) == {"k": "v"}


def test_parse_metadata_accepts_jeepney_variants():
    metadata = {
        "mpris:trackid": ("o", "/org/mpris/MediaPlayer2/TrackList/3"),
        "mpris:length": ("x", 215_000_000),
        "mpris:artUrl": ("s", "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"),
        "xesam:title": ("s", "Song Title"),
        "xesam:artist": ("as", ["First Artist", "Second Artist"]),
        "xesam:album": ("s", "Great Album"),
    }
    track = parse_metadata(metadata, status="Playing", position_us=30_000_000,
                           player="fooyin")
    assert track is not None
    assert track.title == "Song Title"
    assert track.artist == "First Artist, Second Artist"
    assert track.album == "Great Album"
    assert track.playing is True
    assert track.duration_ms == 215_000
    assert track.position_ms == 30_000
    assert track.player == "fooyin"
    assert track.kind == "youtube"          # i.ytimg.com URL yields a video id
    assert track.video_id == "dQw4w9WgXcQ"
    assert track.track_id == "/org/mpris/MediaPlayer2/TrackList/3"


def test_parse_metadata_file_artwork_becomes_local_path():
    metadata = {"xesam:title": "Song", "xesam:artist": ["Artist"],
                "mpris:artUrl": "file:///tmp/cover%20art.jpg"}
    track = parse_metadata(metadata, status="Playing")
    assert track.image_url is None
    assert track.local_art == "/tmp/cover art.jpg"


def test_parse_metadata_missing_length_and_position():
    track = parse_metadata({"xesam:title": "Song"}, status="Playing",
                           position_us=5_000_000)
    assert track.duration_ms is None
    assert track.position_ms is None       # a position without a duration is useless


def test_parse_metadata_paused_and_stopped():
    assert parse_metadata({"xesam:title": "Song"}, status="Paused").playing is False
    assert parse_metadata({"xesam:title": "Song"}, status="Stopped").playing is False
    assert parse_metadata({"xesam:title": "Song"}, status="Whatever").playing is False


@pytest.mark.parametrize("metadata", [
    {},
    {"xesam:title": ""},
    {"xesam:title": "Unknown"},
    {"xesam:artist": ["Artist"]},
    None,
    "not-a-dict",
])
def test_parse_metadata_rejects_unusable_metadata(metadata):
    assert parse_metadata(metadata) is None


def test_parse_metadata_strips_duplicated_artist_from_title():
    track = parse_metadata({"xesam:title": "Artist - Song", "xesam:artist": ["Artist"]})
    assert track.title == "Song"


def test_parse_metadata_uses_album_artist_as_fallback():
    track = parse_metadata({"xesam:title": "Song", "xesam:albumArtist": ["Various"]})
    assert track.artist == "Various"


def test_no_track_sentinel_is_dropped():
    track = parse_metadata({"xesam:title": "Song",
                            "mpris:trackid": "/org/mpris/MediaPlayer2/TrackList/NoTrack"})
    assert track.track_id is None


def test_parse_player_properties_reads_status_and_position():
    properties = {
        "PlaybackStatus": "Playing",
        "Position": 42_000_000,
        "Metadata": {"xesam:title": "Song", "xesam:artist": ["Artist"],
                     "mpris:length": 180_000_000},
    }
    track = parse_player_properties(properties, identity="fooyin",
                                    bus_name="org.mpris.MediaPlayer2.fooyin")
    assert track.playing is True
    assert track.position_ms == 42_000
    assert track.player == "fooyin"


def test_parse_player_properties_requires_a_status():
    track = parse_player_properties({"Metadata": {"xesam:title": "Song"}})
    assert track.playing is False


# ---------------------------------------------------------------------------
# Bus plumbing (fake jeepney connection)
# ---------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, member, body):
        from jeepney import HeaderFields
        self.header = type("Header", (), {"fields": {HeaderFields.member: member}})
        self.body = body


class FakeConnection:
    """Answers ListNames / GetAll like a session bus would."""

    def __init__(self, players):
        self.players = players          # bus_name -> {"root": {...}, "player": {...}}
        self.calls = []
        self.closed = False

    def send_and_get_reply(self, message, timeout=None):
        from jeepney import HeaderFields
        member = message.header.fields[HeaderFields.member]
        bus_name = message.header.fields.get(HeaderFields.destination)
        self.calls.append((member, bus_name, message.body))
        if member == "ListNames":
            return FakeMessage(member, (["org.freedesktop.DBus",
                                         *self.players.keys()],))
        if member == "GetAll":
            interface = message.body[0]
            data = self.players[bus_name]
            if interface == ROOT_INTERFACE:
                return FakeMessage(member, (data["root"],))
            return FakeMessage(member, (data["player"],))
        raise AssertionError(f"unexpected call {member}")

    def close(self):
        self.closed = True


def _player(title="Song", status="Playing", identity="fooyin"):
    return {
        "root": {"Identity": identity, "DesktopEntry": identity},
        "player": {
            "PlaybackStatus": status,
            "Position": 1_000_000,
            "Metadata": {"xesam:title": title, "xesam:artist": ["Artist"],
                         "mpris:length": 100_000_000},
        },
    }


def test_bus_lists_only_mpris_players():
    connection = FakeConnection({
        "org.mpris.MediaPlayer2.fooyin": _player(),
        "com.example.Other": _player(),
    })
    bus = MprisBus(connect=lambda: connection)
    assert bus.list_players() == ["org.mpris.MediaPlayer2.fooyin"]


def test_bus_get_all_targets_the_mpris_object_path():
    connection = FakeConnection({"org.mpris.MediaPlayer2.fooyin": _player()})
    bus = MprisBus(connect=lambda: connection)
    bus.get_all("org.mpris.MediaPlayer2.fooyin", PLAYER_INTERFACE)
    member, destination, body = connection.calls[-1]
    assert member == "GetAll"
    assert destination == "org.mpris.MediaPlayer2.fooyin"
    assert body == (PLAYER_INTERFACE,)
    assert MPRIS_PATH == "/org/mpris/MediaPlayer2"


def test_backend_polls_all_players_and_keeps_playing_ones():
    connection = FakeConnection({
        "org.mpris.MediaPlayer2.fooyin": _player("Foo Song"),
        "org.mpris.MediaPlayer2.chromium": _player("Browser Song", status="Playing",
                                                   identity="chromium"),
    })
    backend = MprisBackend(config=Config(), bus=MprisBus(connect=lambda: connection),
                           watch=False)
    snapshot = backend.poll()
    assert isinstance(snapshot, Snapshot)
    assert snapshot.error is None
    assert {item.title for item in snapshot.players} == {"Foo Song", "Browser Song"}
    backend.close()
    assert connection.closed is True


def test_backend_drops_paused_players_when_clearing_on_pause():
    connection = FakeConnection({
        "org.mpris.MediaPlayer2.fooyin": _player("Foo Song", status="Paused"),
    })
    backend = MprisBackend(config=Config(), bus=MprisBus(connect=lambda: connection),
                           watch=False)
    assert backend.poll().players == ()


def test_backend_keeps_paused_players_when_configured():
    config = Config()
    config.clear_on_pause = False
    connection = FakeConnection({
        "org.mpris.MediaPlayer2.fooyin": _player("Foo Song", status="Paused"),
    })
    backend = MprisBackend(config=config, bus=MprisBus(connect=lambda: connection),
                           watch=False)
    players = backend.poll().players
    assert len(players) == 1 and players[0].playing is False


def test_backend_deduplicates_the_same_track_from_two_players():
    connection = FakeConnection({
        "org.mpris.MediaPlayer2.fooyin": _player("Same Song"),
        "org.mpris.MediaPlayer2.chromium": _player("Same Song", identity="chromium"),
    })
    backend = MprisBackend(config=Config(), bus=MprisBus(connect=lambda: connection),
                           watch=False)
    assert len(backend.poll().players) == 1


def test_backend_reports_bus_failure_without_raising():
    def broken():
        raise MprisUnavailable("no session bus")

    backend = MprisBackend(config=Config(), bus=MprisBus(connect=broken), watch=False)
    snapshot = backend.poll()
    assert snapshot.players == ()
    assert "no session bus" in snapshot.error


def test_backend_survives_one_broken_player():
    class HalfBroken(FakeConnection):
        def send_and_get_reply(self, message, timeout=None):
            from jeepney import HeaderFields
            member = message.header.fields[HeaderFields.member]
            destination = message.header.fields.get(HeaderFields.destination)
            if member == "GetAll" and destination == "org.mpris.MediaPlayer2.broken":
                raise MprisUnavailable("player went away")
            return super().send_and_get_reply(message, timeout)

    connection = HalfBroken({
        "org.mpris.MediaPlayer2.fooyin": _player("Foo Song"),
        "org.mpris.MediaPlayer2.broken": _player("Gone"),
    })
    backend = MprisBackend(config=Config(), bus=MprisBus(connect=lambda: connection),
                           watch=False)
    snapshot = backend.poll()
    assert [item.title for item in snapshot.players] == ["Foo Song"]
    assert "player went away" in snapshot.error
