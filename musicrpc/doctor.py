"""Diagnostics: one command that explains why presence is not working."""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
from pathlib import Path

from . import __version__, autostart, native_install, paths
from .config import Config
from .discord import find_ipc_sockets


def _check(name: str, ok: bool | None, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def collect(config: Config | None = None) -> dict:
    config = config or Config.load()
    report: dict = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "config": {
            "path": str(config.source_path or paths.config_file()),
            "exists": Path(config.source_path or paths.config_file()).exists(),
            "errors": config.load_errors,
            "player_priority": config.player_priority,
            "backend": config.backend,
        },
        "checks": [],
        "players": [],
        "manifests": [],
        "autostart": {},
    }

    # Session bus ----------------------------------------------------------
    bus_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    report["checks"].append(_check(
        "D-Bus session bus",
        bool(bus_address) or Path(f"/run/user/{os.getuid()}/bus").exists(),
        bus_address or f"/run/user/{os.getuid()}/bus"))

    # MPRIS ----------------------------------------------------------------
    try:
        from .backends.mpris import list_mpris_players
        players = list_mpris_players()
        report["players"] = players
        fooyin = any("fooyin" in str(item).casefold() for item in players)
        report["checks"].append(_check(
            "MPRIS players", bool(players),
            f"{len(players)} player(s)" + (" - fooyin detected" if fooyin else "")))
    except Exception as exc:
        report["checks"].append(_check("MPRIS players", False, str(exc)))

    # Discord --------------------------------------------------------------
    sockets = find_ipc_sockets()
    report["discord"] = {"sockets": sockets}
    report["checks"].append(_check(
        "Discord IPC socket", bool(sockets),
        sockets[0] if sockets else
        "not found - start the Discord desktop app (Flatpak/Snap paths are checked too)"))

    # Extension bridge -----------------------------------------------------
    bridge_path = paths.bridge_socket_path()
    listening = False
    if bridge_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1.0)
                probe.connect(str(bridge_path))
            listening = True
        except OSError:
            listening = False
    report["bridge"] = {"socket": str(bridge_path), "listening": listening,
                        "enabled": config.extension_bridge_enabled}
    report["checks"].append(_check(
        "Extension bridge", listening or not config.extension_bridge_enabled,
        str(bridge_path) if listening else "not running (start Music RPC)"))

    # Native messaging manifests ------------------------------------------
    manifests = native_install.existing_manifests()
    report["manifests"] = manifests
    installed = [item for item in manifests if item.get("exists")]
    broken = [item for item in installed if not item.get("host_executable", True)]
    report["checks"].append(_check(
        "Native messaging manifests", bool(installed) and not broken,
        f"{len(installed)} installed" + (f", {len(broken)} broken" if broken else "")
        if installed else "none - run: music-rpc install"))

    # Autostart ------------------------------------------------------------
    report["autostart"] = {
        "path": str(paths.autostart_file()),
        "enabled": autostart.autostart_enabled(),
        "menu_entry": str(autostart.menu_entry_path()),
        "menu_entry_exists": autostart.menu_entry_path().exists(),
    }

    # Artwork / tray -------------------------------------------------------
    cache = paths.artwork_cache_file()
    report["artwork"] = {
        "cache": str(cache),
        "cache_exists": cache.exists(),
        "cache_bytes": cache.stat().st_size if cache.exists() else 0,
        "enabled": config.artwork_enabled,
        "catalog_lookup": config.artwork_catalog_lookup,
        "relay_enabled": config.artwork_relay_enabled,
        "relay_cloudflared": config.artwork_relay_cloudflared,
        "cloudflared_installed": bool(shutil.which("cloudflared")),
    }
    try:
        import PyQt6  # noqa: F401  (import test only)
        tray = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        report["checks"].append(_check("System tray", tray,
                                       "Qt available" if tray else "no graphical session"))
    except Exception:
        report["checks"].append(_check("System tray", False,
                                       "PyQt6 not installed (optional: tray and settings window)"))

    report["checks"].append(_check(
        "Single instance lock", True,
        str(paths.lock_file()) + (" (held)" if Path(str(paths.lock_file())).exists() else "")))
    report["paths"] = {
        "config": str(paths.config_dir()),
        "state": str(paths.state_dir()),
        "cache": str(paths.cache_dir()),
        "runtime": str(paths.runtime_dir()),
        "log": str(paths.log_file()),
    }
    report["log_tail"] = _tail(paths.log_file(), 25)
    return report


def _tail(path: Path, lines: int) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def render(report: dict) -> str:
    lines = [
        f"Music RPC {report['version']} diagnostics",
        f"  python {report['python']} on {report['platform']}",
        "",
        "Checks:",
    ]
    for check in report["checks"]:
        ok = check["ok"]
        mark = "ok  " if ok else ("WARN" if ok is False else "?   ")
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    if report["players"]:
        lines.append("")
        lines.append("MPRIS players:")
        for player in report["players"]:
            if "error" in player:
                lines.append(f"  - {player['bus_name']}: error: {player['error']}")
            else:
                lines.append(
                    f"  - {player['bus_name']} ({player['identity']}) "
                    f"status={player['status'] or '?'} "
                    f"title={player['title']!r} artist={player['artist']!r} "
                    f"art={player['art_url_kind']}")
    if report["manifests"]:
        lines.append("")
        lines.append("Native messaging manifests:")
        for item in report["manifests"]:
            if item.get("exists"):
                state = "ok" if item.get("host_executable") else "host missing"
                lines.append(f"  - {item['browser']}: {item['path']} ({state})")
    lines.append("")
    lines.append("Paths:")
    for key, value in report["paths"].items():
        lines.append(f"  {key}: {value}")
    if report["config"]["errors"]:
        lines.append("")
        lines.append("Configuration problems:")
        for problem in report["config"]["errors"]:
            lines.append(f"  - {problem}")
    if report["log_tail"]:
        lines.append("")
        lines.append("Last log lines:")
        for line in report["log_tail"]:
            lines.append(f"  {line}")
    return "\n".join(lines) + "\n"


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
