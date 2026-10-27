"""Control socket and single-instance guard."""
from __future__ import annotations

import os
import subprocess
import sys


from musicrpc.control import ControlServer, send_command
from musicrpc.single_instance import SingleInstance


def test_control_server_dispatches_commands(tmp_path):
    server = ControlServer({
        "status": lambda: {"running": True},
        "quit": lambda: "stopping",
        "explode": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    }, path=tmp_path / "control.sock")
    assert server.start() is True
    try:
        assert send_command("status", path=server.path) == {"ok": True,
                                                           "result": {"running": True}}
        assert send_command("quit", path=server.path)["result"] == "stopping"
        failed = send_command("explode", path=server.path)
        assert failed["ok"] is False and "boom" in failed["error"]
        unknown = send_command("nonsense", path=server.path)
        assert unknown["ok"] is False
    finally:
        server.stop()


def test_send_command_without_a_server_returns_none(tmp_path):
    assert send_command("status", path=tmp_path / "missing.sock") is None


def test_control_socket_is_private(tmp_path):
    server = ControlServer({"status": lambda: None}, path=tmp_path / "control.sock")
    assert server.start()
    try:
        assert server.path.stat().st_mode & 0o777 == 0o600
    finally:
        server.stop()


def test_single_instance_blocks_a_second_process(tmp_path):
    lock = tmp_path / "test.lock"
    guard = SingleInstance(lock)
    assert guard.acquire() is True
    assert SingleInstance.is_locked(lock) is True

    script = (
        "import sys; sys.path.insert(0, %r);"
        "from musicrpc.single_instance import SingleInstance;"
        "raise SystemExit(0 if SingleInstance(%r).acquire() else 3)"
        % (str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), str(lock))
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 3, result.stderr

    guard.release()
    assert SingleInstance.is_locked(lock) is False


def test_lock_file_records_the_pid(tmp_path):
    lock = tmp_path / "test.lock"
    with SingleInstance(lock):
        assert lock.read_text().strip() == str(os.getpid())


def test_second_acquire_in_the_same_process_is_allowed_after_release(tmp_path):
    lock = tmp_path / "test.lock"
    guard = SingleInstance(lock)
    assert guard.acquire()
    guard.release()
    assert guard.acquire()
    guard.release()
