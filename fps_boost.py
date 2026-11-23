"""Background FPS-label randomizer for MSI App Player and BlueStacks 5."""
from __future__ import annotations

import random
import threading
import time

import psutil
from pymem import Pymem

PROCESS_NAME = "hd-player.exe"
RENDERER_MODULES = {"libopenglrender.dll", "hd-opengl-native.dll"}
FPS_FORMAT = b"FPS %3d"
MIN_DISPLAY_FPS = 190
MAX_DISPLAY_FPS = 242


class FpsDisplayMonitor:
    """Wait for HD-Player and update its renderer's displayed FPS each second."""

    def __init__(self, logger):
        self._log = logger
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="FpsDisplayMonitor", daemon=True)
        self._sessions = {}
        self._last_open_error = {}

    def start(self):
        self._thread.start()
        self._log.info("FPS display monitor started; waiting for MSI Player or BlueStacks")

    def stop(self, timeout=3):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _open(self, pid):
        process = Pymem(pid)
        return {"process": process, "patches": {}, "scanned": set(), "last_value": None}

    def _scan(self, session):
        process = session["process"]
        for module in process.list_modules():
            name = module.name.lower()
            if name not in RENDERER_MODULES:
                continue
            module_key = (name, int(module.lpBaseOfDll))
            if module_key in session["scanned"]:
                continue
            session["scanned"].add(module_key)
            addresses = process.pattern_scan_module(
                FPS_FORMAT, module, return_multiple=True
            ) or []
            found = []
            for address in addresses:
                try:
                    original = process.read_bytes(address, len(FPS_FORMAT))
                except Exception:
                    continue
                if original == FPS_FORMAT:
                    found.append((address, original))
            if found:
                session["patches"][module_key] = found
                self._log.info("Found %d FPS display string(s) in %s (pid %s)",
                               len(found), module.name, process.process_id)

    @staticmethod
    def _write(process, address, data):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.VirtualProtectEx.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.VirtualProtectEx.restype = wintypes.BOOL
        kernel32.WriteProcessMemory.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )
        kernel32.WriteProcessMemory.restype = wintypes.BOOL

        handle = process.process_handle
        address_ptr = ctypes.c_void_p(address)
        old_protect = wintypes.DWORD()
        if not kernel32.VirtualProtectEx(handle, address_ptr, len(data), 0x04,
                                         ctypes.byref(old_protect)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buffer = ctypes.create_string_buffer(data)
            written = ctypes.c_size_t()
            if not kernel32.WriteProcessMemory(handle, address_ptr, buffer, len(data),
                                               ctypes.byref(written)):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value != len(data):
                raise RuntimeError("Incomplete FPS display write")
        finally:
            ignored = wintypes.DWORD()
            kernel32.VirtualProtectEx(handle, address_ptr, len(data), old_protect.value,
                                      ctypes.byref(ignored))

    @staticmethod
    def _new_value(previous):
        value = random.randint(MIN_DISPLAY_FPS, MAX_DISPLAY_FPS)
        while value == previous:
            value = random.randint(MIN_DISPLAY_FPS, MAX_DISPLAY_FPS)
        return value

    def _attach_and_update(self, pid, session):
        self._scan(session)
        if not session["patches"]:
            return False
        value = self._new_value(session["last_value"])
        replacement = f"FPS {value:03d}".encode("ascii")
        process = session["process"]
        changed = 0
        for patches in session["patches"].values():
            for address, _original in patches:
                self._write(process, address, replacement)
                changed += 1
        if changed:
            session["last_value"] = value
            return True
        return False

    def _close_session(self, session):
        # The emulator drops its process memory when it closes; keep the last
        # displayed value in that process and do not rewrite any DLL on disk.
        try:
            session["process"].close_process()
        except Exception:
            pass

    def _run(self):
        while not self._stop.is_set():
            live = set()
            try:
                for proc in psutil.process_iter(["name"]):
                    try:
                        if (proc.info.get("name") or "").lower() != PROCESS_NAME:
                            continue
                        pid = proc.pid
                        live.add(pid)
                        session = self._sessions.get(pid)
                        if session is None:
                            try:
                                session = self._open(pid)
                                self._sessions[pid] = session
                                self._log.info("Attached FPS display monitor to HD-Player pid %s", pid)
                            except Exception as exc:
                                now = time.monotonic()
                                if now - self._last_open_error.get(pid, 0) >= 15:
                                    self._log.warning("Could not access HD-Player pid %s: %s", pid, exc)
                                    self._last_open_error[pid] = now
                                continue
                        try:
                            if self._attach_and_update(pid, session):
                                self._log.debug("Randomized HD-Player FPS display for pid %s", pid)
                        except Exception as exc:
                            self._log.warning("FPS display update failed for pid %s: %s", pid, exc)
                            self._close_session(session)
                            self._sessions.pop(pid, None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception as exc:
                self._log.warning("FPS display process scan failed: %s", exc)

            for pid in list(self._sessions):
                if pid not in live:
                    self._log.info("HD-Player pid %s closed; waiting for another emulator launch", pid)
                    self._close_session(self._sessions.pop(pid))
                    self._last_open_error.pop(pid, None)
            self._stop.wait(1.0)

        for session in self._sessions.values():
            self._close_session(session)
        self._sessions.clear()
