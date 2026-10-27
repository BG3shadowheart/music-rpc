"""Locate bundled data files in source checkouts and in frozen builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def base_dir() -> Path:
    """Directory that holds the package data (PyInstaller aware)."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent


def data_file(name: str) -> Path:
    return base_dir() / "data" / name


def icon_path() -> Path:
    exe_icon = data_file("music-rpc.png")
    if exe_icon.is_file():
        return exe_icon
    return Path("/usr/share/icons/hicolor/256x256/apps/music-rpc.png")


def extension_dir() -> Path:
    """Where the shipped browser extension lives."""
    override = os.environ.get("MUSIC_RPC_EXTENSION_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    candidates = [
        base_dir() / "extension",
        Path(__file__).resolve().parent.parent / "extension",
        Path("/usr/share/music-rpc/extension"),
    ]
    for candidate in candidates:
        if (candidate / "chromium" / "manifest.json").is_file():
            return candidate
    return candidates[-1]
