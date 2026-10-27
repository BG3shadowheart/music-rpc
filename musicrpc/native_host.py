"""Chrome/Chromium native messaging host.

Protocol (unchanged from the Windows build): every message is a 4-byte
little-endian length followed by UTF-8 JSON. The host forwards the packet to the
running daemon over the Unix socket and returns the daemon's answer.

This module is intentionally free of Windows-only tricks: the Windows build had
to recover console handles with ``msvcrt`` because its GUI subsystem build had no
Python streams. On Linux stdin/stdout are always valid pipes.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

from . import paths
from .constants import BRIDGE_MAX_FRAME, NATIVE_VERSION

log = logging.getLogger(__name__)

ERROR_START = "1_CLIENT_ERROR: Start Music RPC to connect"
ERROR_INVALID = "1_CLIENT_ERROR: Invalid native message"


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def read_frame(stream):
    """Read one length-prefixed JSON message; ``None`` on EOF."""
    header = _read_exact(stream, 4)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if size > BRIDGE_MAX_FRAME:
        raise ValueError(f"native message too large: {size} bytes")
    payload = _read_exact(stream, size)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


def write_frame(stream, value):
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(payload)) + payload)
    stream.flush()


def _read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


# ---------------------------------------------------------------------------
# Daemon plumbing
# ---------------------------------------------------------------------------

def forward(packet, socket_path, timeout=2.0):
    """Send one packet to the daemon and return its parsed answer."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n")
        stream = client.makefile("rb")
        raw = stream.readline(BRIDGE_MAX_FRAME + 1)
    if not raw:
        return {"data": ERROR_START}
    return json.loads(raw.decode("utf-8"))


def daemon_command() -> list[str]:
    """Command that starts a detached daemon for this installation."""
    if getattr(sys, "frozen", False):  # PyInstaller/AppImage build
        return [sys.executable, "run", "--background"]
    executable = shutil.which("music-rpc")
    if executable:
        return [executable, "run", "--background"]
    return [sys.executable, "-m", "musicrpc", "run", "--background"]


def daemon_env() -> dict:
    """Environment for the spawned daemon (keeps source checkouts importable)."""
    env = dict(os.environ)
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "site-packages" not in package_root and "dist-packages" not in package_root:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = package_root + (os.pathsep + existing if existing else "")
    return env


def spawn_daemon(popen=None, wait_seconds: float = 6.0,
                 socket_path=None) -> bool:
    """Start the daemon in the background and wait for its socket."""
    if os.environ.get("MUSIC_RPC_NO_AUTOSTART") not in (None, "", "0"):
        return False
    socket_path = socket_path or paths.bridge_socket_path()
    launcher = popen or subprocess.Popen
    try:
        launcher(daemon_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                 env=daemon_env())
    except Exception as exc:
        log.warning("Could not start Music RPC automatically: %s", exc)
        return False
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if os.path.exists(str(socket_path)):
            return True
        time.sleep(0.2)
    return False


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    if not origin:
        return True  # launched by hand during diagnostics
    normalised = origin.rstrip("/")
    if not allowed:
        return True  # the manifest is the authority when no allow-list is set
    return any(normalised == item.rstrip("/") for item in allowed)


def run_native_host(argv: list[str] | None = None, *, allowed_origins=None,
                    socket_path=None, version: str = NATIVE_VERSION,
                    stdin=None, stdout=None, autostart: bool = True) -> int:
    """Entry point used by the native messaging manifest."""
    argv = list(sys.argv[1:] if argv is None else argv)
    origin = next((arg for arg in argv if arg.startswith(("chrome-extension://",
                                                          "moz-extension://"))), None)
    if allowed_origins is None:
        try:
            from .config import Config
            allowed_origins = Config.load().extension_origins
        except Exception:  # pragma: no cover - broken config file
            from .config import DEFAULT_EXTENSION_ORIGINS
            allowed_origins = list(DEFAULT_EXTENSION_ORIGINS)
        if not allowed_origins:
            from .config import DEFAULT_EXTENSION_ORIGINS
            allowed_origins = list(DEFAULT_EXTENSION_ORIGINS)
    if not origin_allowed(origin, list(allowed_origins)):
        log.error("Refusing native message from unrecognised origin %s", origin)
        return 2

    stream_in = stdin if stdin is not None else sys.stdin.buffer
    stream_out = stdout if stdout is not None else sys.stdout.buffer
    socket_path = socket_path or paths.bridge_socket_path()

    while True:
        try:
            packet = read_frame(stream_in)
        except ValueError as exc:
            log.warning("Bad native message: %s", exc)
            return 0
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Native message was not JSON: %s", exc)
            return 0
        except OSError:
            return 0
        if packet is None:
            return 0

        try:
            if isinstance(packet, dict) and packet.get("getNativeVersion"):
                write_frame(stream_out, {"nativeVersion": version})
                continue
            try:
                response = forward(packet, socket_path)
            except (OSError, ValueError) as exc:
                log.info("Daemon not reachable (%s); trying to start it", exc)
                if autostart and spawn_daemon(socket_path=socket_path):
                    response = forward(packet, socket_path)
                else:
                    response = {"data": ERROR_START}
            write_frame(stream_out, response)
        except (BrokenPipeError, OSError):
            return 0
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Native host failure: %s", exc)
            try:
                write_frame(stream_out, {"data": ERROR_INVALID})
            except Exception:
                return 0
