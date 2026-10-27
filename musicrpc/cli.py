"""Command line entry point.

``music-rpc`` with no arguments runs the daemon. The Windows-style switches that
the original ``app.py`` accepted (``--native-host``, ``--dump``, ``--install``,
``--uninstall``) keep working so browser manifests and shortcuts written for the
old build remain valid.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__, autostart, constants, control, installer, paths
from .config import Config, ConfigError
from .logging_setup import setup_logging

log = logging.getLogger("musicrpc.cli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(args) -> Config:
    path = getattr(args, "config", None)
    return Config.load(Path(path) if path else None)


def _print(data):
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    config = _load_config(args)
    log_file = setup_logging(config.log_level, console=(True if args.verbose else None))
    if log_file:
        log.debug("Logging to %s", log_file)
    for problem in config.load_errors:
        log.warning("%s", problem)

    from .single_instance import SingleInstance
    guard = SingleInstance()
    if not guard.acquire():
        response = control.send_command("settings")
        if response and response.get("ok"):
            print("Music RPC is already running - its settings were opened.")
        else:
            print("Music RPC is already running.")
        return 0
    try:
        from .service import run_daemon
        return run_daemon(config, use_tray=not args.no_tray)
    except Exception:
        log.exception("Music RPC stopped because of an unexpected error")
        return 1
    finally:
        guard.release()


def cmd_native_host(args) -> int:
    # stdout is the native messaging channel: never log there.
    setup_logging("INFO", console=False)
    from .native_host import run_native_host
    return run_native_host(args.argv)


def cmd_dump(args) -> int:
    setup_logging("INFO", console=False)
    config = _load_config(args)
    from .artwork import ArtworkResolver, ArtworkSettings, ArtworkStore, Fetcher, NullRelay
    from .backends import create_backend
    from .selection import choose_track

    report: dict = {"version": __version__, "players": [], "selected": None,
                    "artwork": None, "errors": config.load_errors}
    backend = create_backend(config)
    try:
        snapshot = backend.poll()
        if snapshot.error:
            report["backend_error"] = snapshot.error
        for track in snapshot.players:
            report["players"].append({
                "player": track.player,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "playing": track.playing,
                "kind": track.kind,
                "duration_ms": track.duration_ms,
                "position_ms": track.position_ms,
                "art": "remote" if track.image_url else ("local" if track.local_art else "none"),
            })
        selected = choose_track(snapshot.players, config.player_priority,
                                config.ignored_players)
        if selected is not None:
            report["selected"] = {
                "player": selected.player, "title": selected.title,
                "artist": selected.artist, "album": selected.album,
                "playing": selected.playing, "kind": selected.kind,
            }
            resolver = ArtworkResolver(
                fetcher=Fetcher(), store=ArtworkStore(),
                relay=NullRelay(),
                settings=ArtworkSettings(
                    enabled=config.artwork_enabled,
                    catalog_lookup=config.artwork_catalog_lookup,
                    youtube_search=config.artwork_youtube_search))
            image, album, tier = resolver.resolve(selected)
            report["artwork"] = {"image": image, "album": album, "tier": tier}
            resolver.close()
    finally:
        try:
            backend.close()
        except Exception:
            pass

    target = paths.state_dir() / "diagnose.json"
    try:
        paths.ensure_dir(target.parent)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write %s: %s", target, exc)
    if args.json:
        _print(report)
    else:
        selected = report["selected"]
        if selected:
            print(f"Player: {selected['player']}  ({selected['kind']})")
            print(f"Title:  {selected['title']}")
            print(f"Artist: {selected['artist']}")
            print(f"Album:  {selected['album']}")
            print(f"State:  {'playing' if selected['playing'] else 'paused'}")
            art = report["artwork"] or {}
            print(f"Art:    {art.get('tier')} {art.get('image')}")
        else:
            print("No supported player is reporting metadata.")
        print(f"Report written to {target}")
    return 0


def cmd_doctor(args) -> int:
    setup_logging("INFO", console=False)
    config = _load_config(args)
    from .doctor import collect, render, to_json
    report = collect(config)
    if args.json:
        _print(to_json(report))
    else:
        print(render(report), end="")
    return 0


def cmd_status(args) -> int:
    response = control.send_command("status")
    if not response or not response.get("ok"):
        _print({"running": False, "detail": "Music RPC is not running"})
        return 1
    _print(response["result"])
    return 0


def cmd_install(args) -> int:
    setup_logging("INFO", console=True)
    config = _load_config(args)
    result = installer.install(
        config,
        extension_ids=args.extension_id or [],
        force_browsers=args.all_browsers,
        with_autostart=not args.no_autostart,
        bin_dir=Path(args.bin_dir).expanduser() if args.bin_dir else None,
        host_path=Path(args.host_path).expanduser() if args.host_path else None,
        write_launchers=not args.no_launchers,
        write_menu_entry=not args.no_menu,
    )
    _print({
        "native_host": result["host"],
        "launcher": result["launcher"],
        "allowed_origins": result["origins"],
        "manifests_written": [f"{label}: {path}" for label, path in result["manifests_installed"]],
        "browsers_skipped": result["manifests_skipped"],
        "autostart": result["autostart"],
        "menu_entry": result["menu_entry"],
        "discord_socket": result["discord_sockets"] or "none found yet",
    })
    from .resources import extension_dir
    print("\nLoad the extension from %s in your browser, then restart the browser."
          % (extension_dir() / "chromium"))
    return 0


def cmd_uninstall(args) -> int:
    setup_logging("INFO", console=True)
    result = installer.uninstall(purge=args.purge,
                                 remove_dir=Path(args.bin_dir).expanduser() if args.bin_dir else None)
    _print(result)
    return 0


def cmd_enable(args) -> int:
    setup_logging("INFO", console=True)
    if autostart.autostart_enabled():
        autostart.enable_autostart()
    else:
        config = _load_config(args)
        installer.install(config, with_autostart=True)
    if control.send_command("status") is None:
        installer.spawn_self("run", "--background")
        print("Music RPC enabled and started.")
    else:
        print("Music RPC is enabled and already running.")
    return 0


def cmd_disable(args) -> int:
    setup_logging("INFO", console=True)
    autostart.disable_autostart()
    control.send_command("quit")
    print("Music RPC disabled: it will not start at login and the daemon was stopped.\n"
          f"Your settings are kept in {paths.config_dir()}.")
    return 0


def cmd_settings(args) -> int:
    from .tray import show_settings_dialog, gui_available
    if not gui_available():
        print(f"No graphical settings window is available.\nEdit {paths.config_file()} "
              "or use: music-rpc config set <key> <value>", file=sys.stderr)
        return 1
    changed = show_settings_dialog(_load_config(args))
    if changed:
        running = control.send_command("status") is not None
        if running:
            control.send_command("restart")
            print("Settings saved; Music RPC is restarting.")
        else:
            print("Settings saved.")
    return 0


def cmd_config(args) -> int:
    config = Config.load(Path(args.config) if args.config else None)
    action = args.action
    if action == "path":
        print(config.source_path or paths.config_file())
        return 0
    if action == "show":
        _print(config.to_dict())
        return 0
    if action == "reset":
        Config().save(Path(config.source_path or paths.config_file()))
        print("Configuration reset to defaults.")
        return 0
    if action == "edit":
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        target = Path(config.source_path or paths.config_file())
        if not target.exists():
            config.save(target)
        os.execvp(editor, [editor, str(target)])
        return 0
    if action == "get":
        if not args.key:
            print("usage: music-rpc config get <key>", file=sys.stderr)
            return 2
        _print(config.to_dict().get(args.key))
        return 0
    if action == "set":
        if not args.key or args.value is None:
            print("usage: music-rpc config set <key> <value>", file=sys.stderr)
            return 2
        data = config.to_dict()
        if args.key not in data:
            print(f"Unknown key {args.key!r}. Known keys:\n  "
                  + "\n  ".join(sorted(data)), file=sys.stderr)
            return 2
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        data[args.key] = value
        try:
            updated = Config.from_dict(data, source_path=config.source_path)
        except ConfigError as exc:
            print(f"Invalid value: {exc}", file=sys.stderr)
            return 2
        problems = [problem for problem in updated.load_errors
                    if args.key in problem]
        if problems:
            for problem in problems:
                print(f"Invalid value: {problem}", file=sys.stderr)
            return 2
        path = updated.save()
        print(f"Saved {args.key} = {json.dumps(updated.to_dict()[args.key])} in {path}")
        for problem in updated.load_errors:
            print(f"  warning: {problem}", file=sys.stderr)
        return 0
    print("usage: music-rpc config {show|path|get|set|reset|edit}", file=sys.stderr)
    return 2


def cmd_version(_args) -> int:
    print(f"{constants.APP_NAME} {__version__}")
    print(f"Discord application id: {constants.APP_ID}")
    print(f"Native messaging host: {constants.NATIVE_HOST_NAME} v{constants.NATIVE_VERSION}")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-rpc",
        description="Discord Rich Presence for Fooyin and YouTube Music (Linux).")
    parser.add_argument("--version", "-V", action="store_true", help="show the version")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the background daemon (default)")
    run.add_argument("--background", "-b", action="store_true",
                     help="started at login; keep running quietly")
    run.add_argument("--no-tray", action="store_true", help="do not show a tray icon")
    run.add_argument("--verbose", "-v", action="store_true", help="log to the terminal")
    run.add_argument("--config", help="path to config.json")
    run.set_defaults(func=cmd_run)

    native = sub.add_parser("native-host", help="run the browser native messaging host")
    native.add_argument("argv", nargs=argparse.REMAINDER,
                        help="arguments passed by the browser (origin)")
    native.set_defaults(func=cmd_native_host)

    version = sub.add_parser("version", help="show version information")
    version.set_defaults(func=cmd_version)

    dump = sub.add_parser("dump", help="print the current track and chosen artwork")
    dump.add_argument("--json", action="store_true")
    dump.add_argument("--config")
    dump.set_defaults(func=cmd_dump)

    doctor = sub.add_parser("doctor", help="diagnose D-Bus, Discord, browser and config problems")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--config")
    doctor.set_defaults(func=cmd_doctor)

    status = sub.add_parser("status", help="show the running daemon's status")
    status.set_defaults(func=cmd_status)

    install = sub.add_parser("install",
                             help="install autostart, menu entry and browser native hosts")
    install.add_argument("--extension-id", action="append",
                         help="extra Chromium extension id to allow (repeatable)")
    install.add_argument("--all-browsers", action="store_true",
                         help="write manifests even for browsers not detected")
    install.add_argument("--no-autostart", action="store_true")
    install.add_argument("--bin-dir", help="where to write the launcher scripts")
    install.add_argument("--host-path", help="override the native host executable")
    install.add_argument("--no-launchers", action="store_true",
                         help="do not write ~/.local/bin wrapper scripts")
    install.add_argument("--no-menu", action="store_true",
                         help="do not install the application menu entry")
    install.add_argument("--config")
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall", help="remove hosts, autostart and launchers")
    uninstall.add_argument("--purge", action="store_true",
                           help="also delete configuration, logs and the artwork cache")
    uninstall.add_argument("--bin-dir")
    uninstall.set_defaults(func=cmd_uninstall)

    enable = sub.add_parser("enable", help="re-enable start at login and start the daemon")
    enable.add_argument("--config")
    enable.set_defaults(func=cmd_enable)

    disable = sub.add_parser("disable", help="stop the daemon and disable start at login")
    disable.set_defaults(func=cmd_disable)

    settings = sub.add_parser("settings", help="open the settings window")
    settings.add_argument("--config")
    settings.set_defaults(func=cmd_settings)

    config = sub.add_parser("config", help="inspect or change the configuration")
    config.add_argument("action", nargs="?", default="show",
                        choices=["show", "path", "get", "set", "reset", "edit"])
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.add_argument("--config")
    config.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Windows-compatible switches, handled before argparse so that an origin
    # argument (chrome-extension://...) is never mistaken for a subcommand.
    origin = next((item for item in argv
                   if item.startswith(("chrome-extension://", "moz-extension://"))), None)
    if "--native-host" in argv or origin is not None:
        extra = [origin] if origin else []
        from .native_host import run_native_host
        setup_logging("INFO", console=False)
        return run_native_host(extra)
    legacy = {"--dump", "--install", "--uninstall"}
    for flag, command in (("--dump", "dump"), ("--install", "install"),
                          ("--uninstall", "uninstall")):
        if flag in argv:
            argv = [command] + [item for item in argv
                                if item.startswith("--") and item not in legacy]
            break

    parser = build_parser()
    if not argv:
        argv = ["run"]
    args = parser.parse_args(argv)
    if args.version:
        return cmd_version(args)
    if not getattr(args, "command", None):
        argv = ["run"]
        args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
