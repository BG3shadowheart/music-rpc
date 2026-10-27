"""freedesktop desktop entries: menu item and login autostart."""
from __future__ import annotations

import logging
import shlex
from pathlib import Path

from . import paths
from .constants import APP_NAME

log = logging.getLogger(__name__)

ICON_NAME = "music-rpc"

_DESKTOP = """[Desktop Entry]
Type=Application
Version=1.0
Name={name}
GenericName=Music Rich Presence
Comment={comment}
Exec={exec_line}
Icon={icon}
Terminal=false
Categories=Audio;Music;Utility;
Keywords=discord;presence;music;fooyin;youtube;
StartupNotify=false
X-GNOME-UsesNotifications=true
"""

_AUTOSTART = """[Desktop Entry]
Type=Application
Version=1.0
Name={name}
Comment={comment}
Exec={exec_line}
Icon={icon}
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
X-KDE-StartupNotify=false
NoDisplay=false
Hidden=false
"""


def _exec_line(exec_line) -> str:
    if isinstance(exec_line, (list, tuple)):
        return " ".join(shlex.quote(str(part)) for part in exec_line)
    return str(exec_line)


def desktop_entry(exec_line, *, icon: str = ICON_NAME, extra: str = "") -> str:
    body = _DESKTOP.format(name=APP_NAME, comment=paths.app_summary(),
                           exec_line=_exec_line(exec_line), icon=icon)
    return body + extra


def autostart_entry(exec_line, *, icon: str = ICON_NAME) -> str:
    return _AUTOSTART.format(name=APP_NAME, comment=paths.app_summary(),
                             exec_line=_exec_line(exec_line), icon=icon)


def applications_dir() -> Path:
    from .paths import _xdg  # local import keeps the XDG helper private
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / "applications"


def menu_entry_path() -> Path:
    return applications_dir() / f"{ICON_NAME}.desktop"


def install_menu_entry(exec_line, *, icon: str = ICON_NAME) -> Path:
    path = menu_entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(exec_line, icon=icon), encoding="utf-8")
    path.chmod(0o755)
    log.info("Menu entry installed at %s", path)
    return path


def remove_menu_entry() -> bool:
    path = menu_entry_path()
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)
    return False


def install_autostart(exec_line, *, icon: str = ICON_NAME) -> Path:
    path = paths.autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(autostart_entry(exec_line, icon=icon), encoding="utf-8")
    path.chmod(0o644)
    log.info("Autostart entry installed at %s", path)
    return path


def remove_autostart() -> bool:
    path = paths.autostart_file()
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)
    return False


def autostart_enabled() -> bool:
    return paths.autostart_file().exists()


def disable_autostart() -> bool:
    """Keep the file but mark it hidden, so re-enabling is a one-liner."""
    path = paths.autostart_file()
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        if "Hidden=true" in text:
            return True
        text = text.replace("Hidden=false", "Hidden=true")
        if "Hidden=true" not in text:
            text = text.rstrip("\n") + "\nHidden=true\n"
        path.write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("Could not disable autostart: %s", exc)
        return False


def enable_autostart() -> bool:
    path = paths.autostart_file()
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8").replace("Hidden=true", "Hidden=false")
        path.write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("Could not enable autostart: %s", exc)
        return False
