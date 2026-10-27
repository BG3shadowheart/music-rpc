"""Native messaging protocol: framing, version handshake, forwarding, origins."""
from __future__ import annotations

import io
import json
import struct

import pytest

import musicrpc.native_host as native_host
from musicrpc.bridge import SUCCESS_UPDATED, ExtensionBridge
from musicrpc.constants import NATIVE_VERSION


def frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def unframe(data: bytes) -> list[dict]:
    messages, offset = [], 0
    while offset < len(data):
        size = struct.unpack("<I", data[offset:offset + 4])[0]
        offset += 4
        messages.append(json.loads(data[offset:offset + size].decode("utf-8")))
        offset += size
    return messages


class OneByteStream:
    """Simulates a pipe that delivers a single byte per read()."""

    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def read(self, size=-1):
        if self._offset >= len(self._data):
            return b""
        chunk = self._data[self._offset:self._offset + 1]
        self._offset += 1
        return chunk


def test_frame_round_trip_including_unicode():
    stream = io.BytesIO()
    native_host.write_frame(stream, {"jsTitle": "Björk – Jóga 🎵"})
    stream.seek(0)
    assert native_host.read_frame(stream) == {"jsTitle": "Björk – Jóga 🎵"}
    assert native_host.read_frame(stream) is None


def test_frame_reader_handles_partial_reads():
    stream = OneByteStream(frame({"hello": "world"}))
    assert native_host.read_frame(stream) == {"hello": "world"}


def test_oversized_frame_is_rejected():
    header = struct.pack("<I", 5_000_000)
    with pytest.raises(ValueError):
        native_host.read_frame(io.BytesIO(header))


def test_host_answers_the_version_handshake_without_a_daemon():
    output = io.BytesIO()
    code = native_host.run_native_host(
        [], stdin=io.BytesIO(frame({"getNativeVersion": True})), stdout=output,
        socket_path="/nonexistent/bridge.sock", autostart=False)
    assert code == 0
    assert unframe(output.getvalue()) == [{"nativeVersion": NATIVE_VERSION}]


def test_packets_are_forwarded_to_the_daemon(tmp_path):
    bridge = ExtensionBridge(socket_path=tmp_path / "bridge.sock")
    assert bridge.start()
    try:
        packet = {
            "jsApplicationType": "youtubeMusic",
            "jsTitle": "Song",
            "jsAuthor": "Artist",
            "presenceData": {"type": 2, "details": "Song", "state": "by Artist"},
        }
        output = io.BytesIO()
        code = native_host.run_native_host(
            [], stdin=io.BytesIO(frame(packet)), stdout=output,
            socket_path=str(bridge.socket_path), autostart=False)
        assert code == 0
        assert unframe(output.getvalue())[0]["data"] == SUCCESS_UPDATED
        assert bridge.current_track()[0].title == "Song"
    finally:
        bridge.stop()


def test_missing_daemon_produces_a_clear_error(tmp_path):
    output = io.BytesIO()
    code = native_host.run_native_host(
        [], stdin=io.BytesIO(frame({"presenceData": "#*IDLE*#"})), stdout=output,
        socket_path=str(tmp_path / "absent.sock"), autostart=False)
    assert code == 0
    assert unframe(output.getvalue())[0]["data"] == native_host.ERROR_START


def test_daemon_is_started_on_demand(tmp_path, monkeypatch):
    socket_path = tmp_path / "bridge.sock"
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        socket_path.write_text("")          # pretend the daemon came up

        class Process:
            pass

        return Process()

    assert native_host.spawn_daemon(popen=fake_popen, socket_path=socket_path,
                                   wait_seconds=1.0) is True
    assert "run" in started["command"]
    assert "--background" in started["command"]


def test_autostart_can_be_disabled_by_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_RPC_NO_AUTOSTART", "1")
    assert native_host.spawn_daemon(socket_path=tmp_path / "x.sock") is False


def test_unknown_origin_is_refused():
    code = native_host.run_native_host(
        ["chrome-extension://" + "a" * 32 + "/"],
        allowed_origins=["chrome-extension://" + "b" * 32 + "/"],
        stdin=io.BytesIO(b""), stdout=io.BytesIO(), autostart=False)
    assert code == 2


def test_origin_is_accepted_when_listed():
    origin = "chrome-extension://" + "a" * 32 + "/"
    output = io.BytesIO()
    code = native_host.run_native_host(
        [origin], allowed_origins=[origin],
        stdin=io.BytesIO(frame({"getNativeVersion": True})), stdout=output,
        autostart=False)
    assert code == 0
    assert unframe(output.getvalue())[0]["nativeVersion"] == NATIVE_VERSION


def test_origin_check_accepts_trailing_slash_differences():
    assert native_host.origin_allowed("chrome-extension://abc", ["chrome-extension://abc/"])
    assert native_host.origin_allowed(None, []) is True
    assert native_host.origin_allowed("moz-extension://x", []) is True


def test_host_stops_on_eof():
    assert native_host.run_native_host(
        [], stdin=io.BytesIO(b""), stdout=io.BytesIO(), autostart=False) == 0
