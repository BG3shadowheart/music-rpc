"""Tiny control socket used for "Open"/"Settings" from a second launch.

A Unix socket in the runtime directory, mode 0600, JSON lines. Commands:
``status``, ``settings``, ``restart``, ``quit``, ``reload``.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

CONTROL_SOCKET_NAME = "control.sock"


def socket_path() -> Path:
    return paths.runtime_dir() / CONTROL_SOCKET_NAME


def send_command(command: str, *, path: Path | None = None, timeout: float = 2.0) -> dict | None:
    target = Path(path) if path else socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(target))
            client.sendall(json.dumps({"cmd": command}).encode("utf-8") + b"\n")
            raw = client.makefile("rb").readline(1_000_000)
        return json.loads(raw.decode("utf-8")) if raw else None
    except (OSError, ValueError):
        return None


class ControlServer(threading.Thread):
    def __init__(self, handlers: dict, *, path: Path | None = None):
        super().__init__(daemon=True, name="ControlServer")
        self.handlers = handlers
        self.path = Path(path) if path else socket_path()
        self._server: socket.socket | None = None
        self._stop = threading.Event()

    def start(self):
        paths.ensure_dir(self.path.parent)
        try:
            self.path.unlink(missing_ok=True)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.path))
            os.chmod(self.path, 0o600)
            server.listen(4)
            server.settimeout(1.0)
            self._server = server
        except OSError as exc:
            log.warning("Control socket unavailable: %s", exc)
            return False
        super().start()
        return True

    def run(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                client.settimeout(3.0)
                raw = client.makefile("rb").readline(65_536)
                command = ""
                if raw:
                    try:
                        command = str(json.loads(raw.decode("utf-8")).get("cmd") or "")
                    except (ValueError, UnicodeDecodeError):
                        command = ""
                handler = self.handlers.get(command)
                if handler is None:
                    response = {"ok": False, "error": f"unknown command {command!r}"}
                else:
                    try:
                        response = {"ok": True, "result": handler()}
                    except Exception as exc:
                        response = {"ok": False, "error": str(exc)}
                client.sendall(json.dumps(response).encode("utf-8") + b"\n")
            except Exception as exc:
                log.debug("Control client error: %s", exc)
            finally:
                try:
                    client.close()
                except Exception:
                    pass

    def stop(self):
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
