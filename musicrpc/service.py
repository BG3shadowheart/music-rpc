"""Wiring: config -> backend + bridge + resolver + engine."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field

from . import paths
from .artwork import ArtworkResolver, ArtworkSettings, ArtworkStore, Fetcher
from .backends import create_backend
from .bridge import ExtensionBridge
from .config import Config
from .control import ControlServer
from .engine import PresenceEngine
from .relay import CoverRelay

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    config: Config
    backend: object
    engine: PresenceEngine
    bridge: ExtensionBridge | None = None
    relay: CoverRelay | None = None
    resolver: ArtworkResolver | None = None
    control: ControlServer | None = None
    _engine_thread: threading.Thread | None = None
    quit_event: threading.Event = field(default_factory=threading.Event)
    restart_requested: bool = False

    # -- lifetime ----------------------------------------------------------
    def start_engine(self) -> threading.Thread:
        if self._engine_thread is not None:
            return self._engine_thread
        thread = threading.Thread(target=self.engine.run, daemon=True, name="PresenceEngine")
        thread.start()
        self._engine_thread = thread
        return thread

    def stop(self, *, clear: bool = True):
        try:
            self.engine.stop()
        except Exception:
            pass
        if self.control is not None:
            self.control.stop()
        if self.bridge is not None:
            self.bridge.stop()
        if self.relay is not None:
            self.relay.close()
        if clear:
            try:
                self.engine.shutdown()
            except Exception:
                pass

    def status(self) -> dict:
        result = self.engine.last_result
        track = result.track
        return {
            "running": not self.quit_event.is_set(),
            "discord": result.discord_state,
            "artwork_tier": result.artwork_tier,
            "track": None if track is None else {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "player": track.player,
                "playing": track.playing,
                "kind": track.kind,
            },
            "bridge": (None if self.bridge is None
                       else str(self.bridge.socket_path)),
            "backend": getattr(self.backend, "name", "unknown"),
        }


def build_runtime(config: Config, *, start_bridge: bool = True,
                  enable_tray: bool = True) -> Runtime:
    cloudflared = None
    if config.artwork_relay_cloudflared:
        import shutil
        cloudflared = shutil.which("cloudflared")
    relay = CoverRelay(
        enabled=config.artwork_relay_enabled,
        use_cloudflared=config.artwork_relay_cloudflared,
        public_url=config.artwork_relay_public_url,
        cloudflared_path=cloudflared,
    )
    resolver = ArtworkResolver(
        fetcher=Fetcher(),
        store=ArtworkStore(paths.artwork_cache_file()),
        relay=relay,
        settings=ArtworkSettings(
            enabled=config.artwork_enabled,
            catalog_lookup=config.artwork_catalog_lookup,
            youtube_search=config.artwork_youtube_search,
        ),
    )
    backend = create_backend(config)
    bridge = None
    if config.extension_bridge_enabled:
        bridge = ExtensionBridge(ignore_regular_youtube=config.ignore_regular_youtube)
        if start_bridge and not bridge.start():
            log.warning("Browser extension bridge is not available; "
                        "YouTube Music artwork will come from MPRIS only")
    engine = PresenceEngine(
        backend=backend,
        config=config,
        resolver=resolver,
        bridge=bridge,
        relay_enabled=bool(config.artwork_relay_enabled),
    )
    return Runtime(config=config, backend=backend, engine=engine, bridge=bridge,
                   relay=relay, resolver=resolver)


def _install_signal_handlers(runtime: Runtime):
    def handler(signum, _frame):
        log.info("Received signal %s; shutting down", signum)
        runtime.quit_event.set()
        runtime.engine.stop()

    for signal_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        number = getattr(signal, signal_name, None)
        if number is None:
            continue
        try:
            signal.signal(number, handler)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass


def run_daemon(config: Config, *, use_tray: bool = True) -> int:
    """Run until quit. Returns a process exit code."""
    runtime = build_runtime(config)

    def request_quit():
        runtime.quit_event.set()
        runtime.engine.stop()
        return "stopping"

    def request_restart():
        runtime.restart_requested = True
        request_quit()
        return "restarting"

    def open_settings():
        from .installer import spawn_self
        return "opened" if spawn_self("settings") is not None else "unavailable"

    runtime.control = ControlServer({
        "status": runtime.status,
        "quit": request_quit,
        "restart": request_restart,
        "reload": lambda: "reload-requested",
        "settings": open_settings,
    })
    if not runtime.control.start():
        log.info("Continuing without the control socket")

    _install_signal_handlers(runtime)
    runtime.start_engine()
    log.info("%s %s started (backend=%s, tray=%s)", "Music RPC",
             __import__("musicrpc").__version__, getattr(runtime.backend, "name", "?"),
             use_tray)

    tray = None
    if use_tray and config.tray_enabled:
        from .tray import create_tray
        tray = create_tray(runtime)
        if tray is None:
            log.info("No system tray available; running without a tray icon")

    if tray is not None:
        try:
            tray.run(on_quit=request_quit, on_restart=request_restart,
                     quit_event=runtime.quit_event)
        except Exception as exc:  # pragma: no cover - GUI problems
            log.warning("Tray failed (%s); continuing headless", exc)
            tray = None

    if tray is None:
        while not runtime.quit_event.is_set():
            time.sleep(0.25)

    runtime.stop(clear=True)
    if runtime.restart_requested:
        log.info("Restarting Music RPC")
        return restart_process()
    return 0


def restart_argv() -> list[str]:
    """Command line that starts a fresh copy of this program."""
    executable = os.environ.get("MUSIC_RPC_EXECUTABLE") or sys.executable
    if getattr(sys, "frozen", False):
        # Inside an AppImage sys.executable points into the mount point, whose
        # path changes on every launch; AppRun exports the stable .AppImage.
        return [executable] + sys.argv[1:]
    argv = getattr(sys, "orig_argv", None) or [sys.executable] + sys.argv
    if argv and not argv[0].startswith(("/", "./")) and executable:
        # orig_argv[0] may be a bare module name when started as "python -m".
        argv = [executable] + argv[1:]
    return argv


def restart_process() -> int:
    """Replace this process with a fresh copy of itself."""
    argv = restart_argv()
    try:
        os.execv(argv[0], argv)
    except Exception as exc:  # pragma: no cover - exec failure
        log.error("Could not restart: %s", exc)
        return 1
    return 0  # pragma: no cover
