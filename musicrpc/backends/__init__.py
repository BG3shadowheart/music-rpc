"""Platform media backends.

The Linux backend reads MPRIS over D-Bus. The Windows backend keeps using WinRT
System Media Transport Controls (the API the original ``app.py`` used) so the
same engine can run there as well. Nothing Windows-specific is imported on
Linux and vice versa.
"""
from __future__ import annotations

import sys

from .base import MediaBackend, MediaBackendError
from .null import NullBackend

__all__ = ["MediaBackend", "MediaBackendError", "NullBackend", "create_backend"]


def default_backend_name() -> str:
    if sys.platform.startswith("linux"):
        return "mpris"
    if sys.platform == "win32":
        return "windows"
    return "none"


def create_backend(config=None, name: str | None = None):
    """Instantiate the configured backend. Never raises for missing platforms."""
    choice = (name or (getattr(config, "backend", "auto") if config else "auto") or "auto")
    choice = choice.casefold()
    if choice == "auto":
        choice = default_backend_name()

    if choice == "mpris":
        from .mpris import MprisBackend
        return MprisBackend(config=config)
    if choice == "windows":
        from .windows_smtc import WindowsSmtcBackend
        return WindowsSmtcBackend(config=config)
    return NullBackend()
