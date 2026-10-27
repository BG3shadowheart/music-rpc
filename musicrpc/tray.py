"""System tray icon and settings window (PyQt6).

PyQt6 is optional: without it, or without a graphical session, the daemon runs
headless and everything can still be configured with ``music-rpc config`` or by
editing ``config.json``.
"""
from __future__ import annotations

import copy
import logging
import os
import sys
from . import paths
from .constants import APP_NAME, __version__
from .resources import icon_path
from .textutil import clean

log = logging.getLogger(__name__)


def gui_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except Exception:
        return False
    return True


def _load_icon():
    from PyQt6.QtGui import QIcon
    icon_file = icon_path()
    if icon_file.is_file():
        return QIcon(str(icon_file))
    return QIcon.fromTheme("music-rpc", QIcon.fromTheme("audio-x-generic"))


def _app():
    from PyQt6.QtWidgets import QApplication
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication([APP_NAME])


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

def show_settings_dialog(config=None) -> bool:
    """Show the settings window. Returns True when the user saved changes."""
    if not gui_available():
        raise RuntimeError("No graphical session or PyQt6 is not installed")
    from PyQt6.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
        QFormLayout, QGroupBox, QLabel, QLineEdit, QVBoxLayout,
    )

    from .config import Config

    config = copy.deepcopy(config or Config.load())
    _app()   # ensure a QApplication exists
    dialog = QDialog()
    dialog.setWindowTitle(f"{APP_NAME} settings")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)

    players = QGroupBox("Players")
    players_form = QFormLayout(players)
    priority = QLineEdit(", ".join(config.player_priority))
    priority.setToolTip("First match wins. fooyin first means Fooyin beats a "
                       "playing browser tab.")
    ignored = QLineEdit(", ".join(config.ignored_players))
    clear_on_pause = QCheckBox("Clear the Discord activity when playback pauses")
    clear_on_pause.setChecked(config.clear_on_pause)
    players_form.addRow("Priority order:", priority)
    players_form.addRow("Ignore players:", ignored)
    players_form.addRow(clear_on_pause)
    layout.addWidget(players)

    artwork = QGroupBox("Artwork")
    artwork_form = QFormLayout(artwork)
    artwork_enabled = QCheckBox("Show cover art")
    artwork_enabled.setChecked(config.artwork_enabled)
    catalog = QCheckBox("Look up covers on Apple Music / Deezer")
    catalog.setChecked(config.artwork_catalog_lookup)
    youtube = QCheckBox("Fall back to a YouTube thumbnail search")
    youtube.setChecked(config.artwork_youtube_search)
    relay = QCheckBox("Enable the local artwork relay (publishes the current cover)")
    relay.setChecked(config.artwork_relay_enabled)
    cloudflared = QCheckBox("Use a temporary cloudflared tunnel for the relay")
    cloudflared.setChecked(config.artwork_relay_cloudflared)
    public_url = QLineEdit(config.artwork_relay_public_url)
    public_url.setPlaceholderText("https://your-own-host.example (optional)")
    artwork_form.addRow(artwork_enabled)
    artwork_form.addRow(catalog)
    artwork_form.addRow(youtube)
    artwork_form.addRow(relay)
    artwork_form.addRow(cloudflared)
    artwork_form.addRow("Public relay URL:", public_url)
    layout.addWidget(artwork)

    behaviour = QGroupBox("Behaviour")
    behaviour_form = QFormLayout(behaviour)
    discord_enabled = QCheckBox("Publish to Discord")
    discord_enabled.setChecked(config.discord_enabled)
    tray_enabled = QCheckBox("Show the tray icon")
    tray_enabled.setChecked(config.tray_enabled)
    poll = QDoubleSpinBox()
    poll.setRange(0.5, 10.0)
    poll.setSingleStep(0.5)
    poll.setValue(float(config.poll_seconds))
    poll.setSuffix(" s")
    log_level = QComboBox()
    log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
    log_level.setCurrentText(config.log_level)
    behaviour_form.addRow(discord_enabled)
    behaviour_form.addRow(tray_enabled)
    behaviour_form.addRow("Poll interval:", poll)
    behaviour_form.addRow("Log level:", log_level)
    layout.addWidget(behaviour)

    hint = QLabel(f"Configuration file: {config.source_path or paths.config_file()}")
    hint.setWordWrap(True)
    hint.setStyleSheet("color: palette(mid);")
    layout.addWidget(hint)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                               | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False

    config.player_priority = [part.strip() for part in priority.text().split(",") if part.strip()]
    config.ignored_players = [part.strip() for part in ignored.text().split(",") if part.strip()]
    config.clear_on_pause = clear_on_pause.isChecked()
    config.artwork_enabled = artwork_enabled.isChecked()
    config.artwork_catalog_lookup = catalog.isChecked()
    config.artwork_youtube_search = youtube.isChecked()
    config.artwork_relay_enabled = relay.isChecked()
    config.artwork_relay_cloudflared = cloudflared.isChecked()
    config.artwork_relay_public_url = clean(public_url.text())
    config.discord_enabled = discord_enabled.isChecked()
    config.tray_enabled = tray_enabled.isChecked()
    config.poll_seconds = float(poll.value())
    config.log_level = log_level.currentText()
    path = config.save()
    log.info("Settings saved to %s", path)
    return True


# ---------------------------------------------------------------------------
# Status window
# ---------------------------------------------------------------------------

def show_status_dialog(runtime) -> None:
    if not gui_available():
        raise RuntimeError("No graphical session or PyQt6 is not installed")
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

    _app()   # ensure a QApplication exists
    status = runtime.status()
    track = status.get("track") or {}
    lines = [
        f"<b>{APP_NAME} {__version__}</b>",
        "",
        f"Discord: {status.get('discord', 'unknown')}",
        f"Backend: {status.get('backend', 'unknown')}",
        f"Artwork tier: {status.get('artwork_tier', 'none')}",
    ]
    if track:
        lines.extend([
            "",
            f"<b>{track.get('title', '')}</b>",
            f"{track.get('artist', '')}"
            + (f" — {track.get('album')}" if track.get("album") else ""),
            f"Player: {track.get('player', '')}",
        ])
    else:
        lines.extend(["", "Nothing is playing right now."])

    dialog = QDialog()
    dialog.setWindowTitle(APP_NAME)
    dialog.setMinimumWidth(420)
    layout = QVBoxLayout(dialog)
    label = QLabel("<br>".join(lines))
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

class TrayIcon:
    def __init__(self, runtime):
        self.runtime = runtime
        self._app = None
        self._tray = None
        self._quit_requested = False

    def run(self, *, on_quit, on_restart, quit_event):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QAction
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

        self._app = _app()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("The desktop environment has no system tray")
            return
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationName(APP_NAME)
        self._app.setDesktopFileName("music-rpc")

        tray = QSystemTrayIcon(_load_icon())
        tray.setToolTip(f"{APP_NAME} {__version__}")
        menu = QMenu()

        open_action = QAction("Open", menu)
        open_action.triggered.connect(lambda: self._safe(show_status_dialog, self.runtime))
        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(lambda: self._safe(self._settings, on_restart))
        restart_action = QAction("Restart", menu)
        restart_action.triggered.connect(lambda: self._restart(on_restart))
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: self._quit(on_quit))
        for action in (open_action, settings_action, None, restart_action, quit_action):
            menu.addSeparator() if action is None else menu.addAction(action)

        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self._safe(show_status_dialog, self.runtime)
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        self._tray = tray

        def refresh():
            try:
                status = self.runtime.status()
            except Exception:
                return
            track = status.get("track") or {}
            if track:
                tooltip = f"{track.get('title', '')}\n{track.get('artist', '')}"
            else:
                tooltip = "Nothing playing"
            tray.setToolTip(f"{APP_NAME} — {tooltip}\nDiscord: {status.get('discord')}")
            if quit_event.is_set() and not self._quit_requested:
                self._quit_requested = True
                self._app.quit()

        timer = QTimer()
        timer.timeout.connect(refresh)
        timer.start(2000)
        refresh()
        self._app.exec()

    # -- actions -----------------------------------------------------------
    def _safe(self, function, *args):
        try:
            function(*args)
        except Exception as exc:  # pragma: no cover - GUI errors
            log.warning("Dialog failed: %s", exc)

    def _settings(self, on_restart):
        if show_settings_dialog(self.runtime.config):
            if self._tray is not None:
                self._tray.setToolTip("Saving…")
            on_restart()

    def _restart(self, on_restart):
        on_restart()
        if self._app is not None:
            self._app.quit()

    def _quit(self, on_quit):
        on_quit()
        if self._app is not None:
            self._app.quit()


def create_tray(runtime):
    if not gui_available():
        return None
    return TrayIcon(runtime)


def main_settings() -> int:  # pragma: no cover - interactive
    try:
        changed = show_settings_dialog()
    except RuntimeError as exc:
        print(f"{exc}. Edit {paths.config_file()} instead.", file=sys.stderr)
        return 1
    print("Settings saved. Restart Music RPC to apply them." if changed else "No changes.")
    return 0
