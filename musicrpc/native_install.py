"""Install/remove browser native-messaging manifests on Linux.

Only real, documented locations are used:

Chromium family (Chrome, Chromium, Brave, Thorium, Edge, Vivaldi):
    ~/.config/<vendor-dir>/NativeMessagingHosts/<name>.json
    Snap Chromium:   ~/snap/chromium/current/.config/chromium/NativeMessagingHosts/
    Flatpak browsers: ~/.var/app/<app-id>/config/<vendor-dir>/NativeMessagingHosts/

Mozilla Firefox:
    ~/.mozilla/native-messaging-hosts/<name>.json
    Snap:    ~/snap/firefox/common/.mozilla/native-messaging-hosts/
    Flatpak: ~/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts/

Every directory in the list is only written when its browser appears to be
installed (its config directory exists) or when ``force`` is used.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .constants import APP_NAME, NATIVE_HOST_NAME

log = logging.getLogger(__name__)

FIREFOX_EXTENSION_ID = "youtube-music-presence@music-rpc.local"


@dataclass(frozen=True)
class BrowserTarget:
    key: str
    label: str
    family: str            # "chromium" | "firefox"
    config_root: Path      # everything up to (not including) NativeMessagingHosts


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _xdg_config() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(value) if value and os.path.isabs(value) else _home() / ".config"


def chromium_targets() -> list[BrowserTarget]:
    config = _xdg_config()
    home = _home()
    candidates = [
        ("chrome", "Google Chrome", config / "google-chrome"),
        ("chromium", "Chromium", config / "chromium"),
        ("chromium-browser", "Chromium (chromium-browser)", config / "chromium-browser"),
        ("brave", "Brave", config / "BraveSoftware" / "Brave-Browser"),
        ("thorium", "Thorium", config / "thorium"),
        ("edge", "Microsoft Edge", config / "microsoft-edge"),
        ("vivaldi", "Vivaldi", config / "vivaldi"),
        ("chromium-snap", "Chromium (Snap)",
         home / "snap" / "chromium" / "current" / ".config" / "chromium"),
        ("chrome-flatpak", "Google Chrome (Flatpak)",
         home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome"),
        ("chromium-flatpak", "Chromium (Flatpak)",
         home / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium"),
        ("brave-flatpak", "Brave (Flatpak)",
         home / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser"),
        ("thorium-flatpak", "Thorium (Flatpak)",
         home / ".var" / "app" / "info.rehansattar.thorium" / "config" / "thorium"),
    ]
    return [BrowserTarget(key=key, label=label, family="chromium", config_root=root)
            for key, label, root in candidates]


def firefox_targets() -> list[BrowserTarget]:
    home = _home()
    candidates = [
        ("firefox", "Firefox", home / ".mozilla"),
        ("firefox-snap", "Firefox (Snap)",
         home / "snap" / "firefox" / "common" / ".mozilla"),
        ("firefox-flatpak", "Firefox (Flatpak)",
         home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla"),
    ]
    return [BrowserTarget(key=key, label=label, family="firefox", config_root=root)
            for key, label, root in candidates]


def all_targets() -> list[BrowserTarget]:
    return chromium_targets() + firefox_targets()


def _manifest_dir(target: BrowserTarget) -> Path:
    if target.family == "chromium":
        return target.config_root / "NativeMessagingHosts"
    return target.config_root / "native-messaging-hosts"


def _installed(target: BrowserTarget) -> bool:
    """A browser is considered present when its config root exists."""
    return target.config_root.exists()


def host_manifest(target: BrowserTarget, host_path: Path, chrome_origins: list[str],
                  firefox_extension_id: str = FIREFOX_EXTENSION_ID) -> dict:
    common = {
        "name": NATIVE_HOST_NAME,
        "description": f"{APP_NAME} YouTube Music native messaging host",
        "path": str(host_path),
        "type": "stdio",
    }
    if target.family == "chromium":
        common["allowed_origins"] = sorted(set(
            origin.rstrip("/") + "/" for origin in chrome_origins if origin))
    else:
        common["allowed_extensions"] = [firefox_extension_id]
    return common


def install(host_path: Path, *, chrome_origins: list[str], firefox_extension_id: str = FIREFOX_EXTENSION_ID,
            force: bool = False, targets: list[BrowserTarget] | None = None) -> dict:
    """Write the manifests. Returns ``{"installed": [...], "skipped": [...]}``."""
    host_path = Path(host_path).expanduser().resolve()
    installed, skipped = [], []
    for target in (targets or all_targets()):
        if not force and not _installed(target):
            skipped.append(target.label)
            continue
        directory = _manifest_dir(target)
        manifest = host_manifest(target, host_path, chrome_origins, firefox_extension_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{NATIVE_HOST_NAME}.json"
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            if target.family == "chromium":
                path.chmod(0o644)
            installed.append((target.label, str(path)))
            log.info("Native messaging host written for %s", target.label)
        except OSError as exc:
            skipped.append(f"{target.label} ({exc})")
            log.warning("Could not write the host manifest for %s: %s", target.label, exc)
    return {"installed": installed, "skipped": skipped, "host": str(host_path)}


def uninstall(targets: list[BrowserTarget] | None = None) -> list[str]:
    removed = []
    for target in (targets or all_targets()):
        path = _manifest_dir(target) / f"{NATIVE_HOST_NAME}.json"
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            log.warning("Could not remove %s: %s", path, exc)
    return removed


def existing_manifests() -> list[dict]:
    """Diagnostic helper: every manifest found on this machine."""
    found = []
    for target in all_targets():
        path = _manifest_dir(target) / f"{NATIVE_HOST_NAME}.json"
        entry = {"browser": target.label, "path": str(path), "exists": path.exists()}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entry["host"] = data.get("path")
                entry["origins"] = data.get("allowed_origins") or data.get("allowed_extensions") or []
                entry["host_exists"] = Path(str(data.get("path", ""))).exists()
                entry["host_executable"] = bool(
                    entry["host_exists"] and os.access(str(data.get("path")), os.X_OK))
            except (OSError, ValueError) as exc:
                entry["error"] = str(exc)
        found.append(entry)
    return found


def install_launcher(source: Path, destination: Path) -> Path:
    """Copy the native host launcher and make it executable."""
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    mode = destination.stat().st_mode
    destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination
