"""Extension bridge: packet parsing and the Unix-socket transport."""
from __future__ import annotations

import json
import socket

import pytest

from musicrpc.bridge import (
    ERROR_INVALID,
    ERROR_NO_TITLE,
    SUCCESS_CLEARED,
    SUCCESS_IGNORED,
    SUCCESS_RECEIVED,
    SUCCESS_UPDATED,
    ExtensionBridge,
    parse_extension_packet,
)


def packet(**overrides):
    base = {
        "jsApplicationType": "youtubeMusic",
        "jsTitle": "Song Title",
        "jsAuthor": "Artist Name",
        "jsAlbum": "Album Name",
        "jsVideoUrl": "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "presenceData": {
            "type": 2,
            "details": "Song Title",
            "state": "by Artist Name",
            "assets": {"large_image": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
                       "large_text": "Album Name"},
            "timestamps": {"start": 1_700_000_000_000, "end": 1_700_000_200_000},
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_valid_packet_becomes_a_youtube_track():
    update, response = parse_extension_packet(packet(), now=1.0, wall=1_700_000_100.0)
    assert response["data"] == SUCCESS_UPDATED
    assert update.kind == "update"
    track = update.track
    assert track.title == "Song Title"
    assert track.artist == "Artist Name"
    assert track.album == "Album Name"
    assert track.video_id == "dQw4w9WgXcQ"
    assert track.kind == "youtube"
    assert track.playing is True
    assert track.duration_ms == 200_000
    assert track.position_ms == 100_000
    assert track.image_url.endswith("maxresdefault.jpg")


def test_idle_sentinel_clears_the_track():
    update, response = parse_extension_packet({"presenceData": "#*IDLE*#"}, now=2.0)
    assert update.kind == "idle"
    assert update.track is None
    assert response["data"] == SUCCESS_CLEARED


def test_regular_youtube_playback_is_ignored():
    update, response = parse_extension_packet(
        packet(jsApplicationType="youtube"), now=3.0)
    assert update.kind == "ignored"
    assert response["data"] == SUCCESS_IGNORED

    update, response = parse_extension_packet(
        packet(presenceData={"type": 3, "details": "x"}), now=3.0)
    assert update.kind == "ignored"


def test_ignoring_can_be_disabled():
    update, _response = parse_extension_packet(
        packet(jsApplicationType="youtube"), now=4.0, ignore_regular_youtube=False)
    assert update.kind == "update"


def test_missing_title_is_an_error():
    payload = packet()
    payload["jsTitle"] = ""
    payload["presenceData"]["details"] = ""
    update, response = parse_extension_packet(payload, now=5.0)
    assert update.kind == "error"
    assert response["data"] == ERROR_NO_TITLE


def test_non_presence_message_is_acknowledged_without_state_change():
    update, response = parse_extension_packet({"presenceData": "something else"}, now=6.0)
    assert update.kind == "noop"
    assert response["data"] == SUCCESS_RECEIVED


@pytest.mark.parametrize("bad", [None, [], "text", 42])
def test_garbage_input_is_rejected(bad):
    update, response = parse_extension_packet(bad, now=7.0)
    assert update.kind == "error"
    assert response["data"] == ERROR_INVALID


def test_paused_state_is_understood():
    update, _response = parse_extension_packet(packet(jsPlaybackState="paused"), now=8.0)
    assert update.track.playing is False


def test_position_and_duration_fields_override_timestamps():
    payload = packet(jsPositionSec=42.5, jsDurationSec=180)
    update, _response = parse_extension_packet(payload, now=9.0)
    assert update.track.position_ms == 42_500
    assert update.track.duration_ms == 180_000


def test_video_id_is_taken_from_a_thumbnail_url_when_missing():
    payload = packet(jsVideoUrl="")
    payload["presenceData"]["assets"]["large_image"] = \
        "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"
    update, _response = parse_extension_packet(payload, now=10.0)
    assert update.track.video_id == "abcdefghijk"


def test_artist_prefix_is_removed():
    payload = packet(jsAuthor="by Someone")
    update, _response = parse_extension_packet(payload, now=11.0)
    assert update.track.artist == "Someone"


def test_album_falls_back_to_the_asset_text():
    payload = packet(jsAlbum="")
    payload["presenceData"]["assets"]["large_text"] = "Asset Album"
    update, _response = parse_extension_packet(payload, now=12.0)
    assert update.track.album == "Asset Album"


# ---------------------------------------------------------------------------
# Bridge object
# ---------------------------------------------------------------------------

def test_bridge_stores_and_expires_tracks():
    clock = [1000.0]
    bridge = ExtensionBridge(socket_path=None, clock=lambda: clock[0])
    bridge.handle(packet())
    track, age = bridge.current_track()
    assert track.title == "Song Title"
    assert age == 0.0

    clock[0] += 10
    track, age = bridge.current_track(max_age=5)
    assert track is None and age == 10

    bridge.handle({"presenceData": "#*IDLE*#"})
    assert bridge.current_track()[0] is None


def test_bridge_ignores_duplicate_packets():
    bridge = ExtensionBridge(socket_path=None)
    for _ in range(3):
        bridge.handle(packet())
    track, _age = bridge.current_track()
    assert track.title == "Song Title"
    assert bridge.last_update.kind == "update"


def test_ignored_packets_clear_previous_state():
    bridge = ExtensionBridge(socket_path=None)
    bridge.handle(packet())
    bridge.handle(packet(jsApplicationType="youtube"))
    assert bridge.current_track()[0] is None


def test_unknown_packets_do_not_clear_state():
    bridge = ExtensionBridge(socket_path=None)
    bridge.handle(packet())
    bridge.handle({"presenceData": "unknown"})
    assert bridge.current_track()[0].title == "Song Title"


# ---------------------------------------------------------------------------
# Real socket round trip (no browser involved)
# ---------------------------------------------------------------------------

def test_socket_transport_round_trip(tmp_path):
    bridge = ExtensionBridge(socket_path=tmp_path / "bridge.sock")
    assert bridge.start() is True
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(bridge.socket_path))
            client.sendall(json.dumps(packet()).encode("utf-8") + b"\n")
            reply = json.loads(client.makefile("rb").readline().decode("utf-8"))
        assert reply["data"] == SUCCESS_UPDATED
        track, _age = bridge.current_track()
        assert track.title == "Song Title"
    finally:
        bridge.stop()
    assert not bridge.socket_path.exists()


def test_socket_transport_rejects_invalid_json(tmp_path):
    bridge = ExtensionBridge(socket_path=tmp_path / "bridge.sock")
    assert bridge.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(bridge.socket_path))
            client.sendall(b"this is not json\n")
            reply = json.loads(client.makefile("rb").readline().decode("utf-8"))
        assert reply["data"] == ERROR_INVALID
    finally:
        bridge.stop()


def test_socket_permissions_are_private(tmp_path):
    bridge = ExtensionBridge(socket_path=tmp_path / "bridge.sock")
    assert bridge.start()
    try:
        mode = bridge.socket_path.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        bridge.stop()


def test_server_survives_a_silent_client(tmp_path):
    bridge = ExtensionBridge(socket_path=tmp_path / "bridge.sock")
    assert bridge.start()
    try:
        quiet = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        quiet.connect(str(bridge.socket_path))
        quiet.close()
        # A second, well-behaved client must still be served.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(bridge.socket_path))
            client.sendall(json.dumps(packet()).encode("utf-8") + b"\n")
            reply = json.loads(client.makefile("rb").readline().decode("utf-8"))
        assert reply["data"] == SUCCESS_UPDATED
    finally:
        bridge.stop()
