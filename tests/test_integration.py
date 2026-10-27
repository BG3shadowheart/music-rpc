"""End-to-end wiring: launcher script, native host subprocess, running daemon.

These tests start real processes but need no display, D-Bus session bus, Discord
client or browser.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import time
from pathlib import Path

import pytest

from musicrpc import control, installer, paths
from musicrpc.bridge import SUCCESS_UPDATED, ExtensionBridge
from musicrpc.config import Config
from musicrpc.constants import NATIVE_VERSION


def frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def read_frames(data: bytes) -> list[dict]:
    messages, offset = [], 0
    while offset + 4 <= len(data):
        size = struct.unpack("<I", data[offset:offset + 4])[0]
        offset += 4
        messages.append(json.loads(data[offset:offset + size].decode("utf-8")))
        offset += size
    return messages


PRESENCE_PACKET = {
    "jsApplicationType": "youtubeMusic",
    "jsTitle": "Integration Song",
    "jsAuthor": "Integration Artist",
    "jsAlbum": "Integration Album",
    "jsVideoUrl": "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "presenceData": {"type": 2, "details": "Integration Song",
                     "state": "by Integration Artist",
                     "assets": {"large_image":
                                "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"}},
}


@pytest.fixture
def installed():
    """Run the real installer into the isolated HOME of this test."""
    result = installer.install(Config(), force_browsers=True, with_autostart=True)
    return result


def test_generated_launchers_are_executable_shell_scripts(installed):
    launcher = Path(installed["launcher"])
    native = Path(installed["native_launcher"])
    for script in (launcher, native):
        assert script.is_file()
        assert os.access(script, os.X_OK)
        text = script.read_text()
        assert text.startswith("#!/bin/sh")
        assert "musicrpc" in text


def test_installed_native_host_answers_the_version_handshake(installed):
    native = Path(installed["native_launcher"])
    completed = subprocess.run(
        [str(native)], input=frame({"getNativeVersion": True}),
        capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode()
    assert read_frames(completed.stdout)[0]["nativeVersion"] == NATIVE_VERSION


def test_installed_native_host_forwards_to_a_live_bridge(installed):
    bridge = ExtensionBridge(socket_path=paths.bridge_socket_path())
    assert bridge.start()
    try:
        native = Path(installed["native_launcher"])
        completed = subprocess.run(
            [str(native), "chrome-extension://bdgahehmdkmmmfbjefaoongpfkkpfnpg/"],
            input=frame(PRESENCE_PACKET), capture_output=True, timeout=60)
        assert completed.returncode == 0, completed.stderr.decode()
        assert read_frames(completed.stdout)[0]["data"] == SUCCESS_UPDATED
        assert bridge.current_track()[0].title == "Integration Song"
    finally:
        bridge.stop()


def test_native_host_rejects_an_unknown_origin(installed):
    native = Path(installed["native_launcher"])
    completed = subprocess.run(
        [str(native), "chrome-extension://" + "z" * 32 + "/"],
        input=frame({"getNativeVersion": True}), capture_output=True, timeout=60)
    assert completed.returncode == 2


def _wait_for(predicate, timeout=25.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_daemon_runs_headless_and_reports_status(installed):
    """Start the daemon exactly like the autostart entry, talk to it, stop it."""
    launcher = Path(installed["launcher"])
    env = dict(os.environ)
    env["MUSIC_RPC_NO_AUTOSTART"] = "1"
    process = subprocess.Popen(
        [str(launcher), "run", "--no-tray"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        start_new_session=True)
    try:
        assert _wait_for(lambda: paths.bridge_socket_path().exists()), \
            "the extension bridge socket never appeared"
        assert _wait_for(lambda: control.send_command("status") is not None), \
            "the control socket never answered"

        # The native host (as used by the browser) forwards into the live daemon.
        native = Path(installed["native_launcher"])
        completed = subprocess.run(
            [str(native)], input=frame(PRESENCE_PACKET), capture_output=True, timeout=60)
        assert read_frames(completed.stdout)[0]["data"] == SUCCESS_UPDATED

        assert _wait_for(
            lambda: (control.send_command("status") or {}).get("result", {})
            .get("track") is not None, timeout=20), "the daemon never saw the track"
        status = control.send_command("status")["result"]
        assert status["running"] is True
        assert status["backend"] == "mpris"
        assert status["track"]["title"] == "Integration Song"
        assert status["track"]["kind"] == "youtube"

        assert control.send_command("quit")["ok"] is True
        assert _wait_for(lambda: process.poll() is not None, timeout=20), \
            "the daemon did not exit on request"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
    output = process.stdout.read() if process.stdout else ""
    assert "Traceback" not in output, output


def test_second_launch_exits_and_leaves_the_first_running(installed):
    launcher = Path(installed["launcher"])
    first = subprocess.Popen([str(launcher), "run", "--no-tray"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        assert _wait_for(lambda: control.send_command("status") is not None)
        second = subprocess.run([str(launcher), "run", "--no-tray"],
                                capture_output=True, text=True, timeout=60)
        assert second.returncode == 0
        assert "already running" in (second.stdout + second.stderr).lower()
        assert first.poll() is None
    finally:
        control.send_command("quit")
        if first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=15)
            except subprocess.TimeoutExpired:
                first.kill()
