"""XDG base-directory handling (freedesktop.org specification).

All paths are resolved at call time so tests can point ``XDG_*`` variables at a
temporary directory without reloading modules.
"""
from __future__ import annotations

import os
from pathlib import Path

from .constants import APP_NAME, BRIDGE_SOCKET_NAME


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _xdg(var: str, fallback: Path) -> Path:
    value = os.environ.get(var, "").strip()
    if value and os.path.isabs(value):
        return Path(value)
    return fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", _home() / ".config") / "music-rpc"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", _home() / ".local" / "state") / "music-rpc"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", _home() / ".cache") / "music-rpc"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", _home() / ".local" / "share") / "music-rpc"


def log_file() -> Path:
    return state_dir() / "presence.log"


def artwork_cache_file() -> Path:
    # Same file name and JSON schema as the Windows build, so an existing cache
    # can be copied across if somebody dual-boots.
    return cache_dir() / "artwork-cache.json"


def config_file() -> Path:
    override = os.environ.get("MUSIC_RPC_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.json"


def runtime_dir() -> Path:
    """Per-session runtime directory (sockets). Falls back if unset."""
    base = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if base and os.path.isabs(base):
        return Path(base) / "music-rpc"
    # No login session (cron, ssh, containers): use the state dir.
    return state_dir() / "run"


def bridge_socket_path() -> Path:
    return runtime_dir() / BRIDGE_SOCKET_NAME


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def lock_file() -> Path:
    return runtime_dir() / "music-rpc.lock"


def autostart_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", _home() / ".config") / "autostart" / "music-rpc.desktop"


def app_summary() -> str:
    return f"{APP_NAME} - Discord Rich Presence for your music players"
