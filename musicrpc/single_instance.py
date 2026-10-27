"""Single-instance guard based on an advisory ``flock``."""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)


class SingleInstance:
    """Hold an exclusive lock for the lifetime of the process.

    ``flock`` is released automatically when the file descriptor is closed or the
    process dies (including SIGKILL), so a crash never leaves a stale lock.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.lock_file()
        self._fd: int | None = None

    def acquire(self) -> bool:
        paths.ensure_dir(self.path.parent)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            return False
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        self._fd = fd
        return True

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()
        return False

    @staticmethod
    def is_locked(path: Path | None = None) -> bool:
        target = Path(path) if path else paths.lock_file()
        if not target.exists():
            return False
        try:
            fd = os.open(str(target), os.O_RDWR)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
