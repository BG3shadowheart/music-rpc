"""Command line behaviour (no desktop session required)."""
from __future__ import annotations

import json


import musicrpc.native_host as native_host
from musicrpc import paths
from musicrpc.cli import main
from musicrpc.config import Config


def test_version(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "Music RPC" in out and "1561450956242161745" in out


def test_config_path_and_show(capsys):
    assert main(["config", "path"]) == 0
    assert str(paths.config_file()) in capsys.readouterr().out
    assert main(["config", "show"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["discord_app_id"] == "1561450956242161745"


def test_config_set_and_get(capsys):
    assert main(["config", "set", "poll_seconds", "2.5"]) == 0
    capsys.readouterr()
    assert Config.load().poll_seconds == 2.5
    assert main(["config", "get", "poll_seconds"]) == 0
    assert capsys.readouterr().out.strip() == "2.5"


def test_config_set_rejects_unknown_keys(capsys):
    assert main(["config", "set", "nonsense", "1"]) == 2
    assert "Unknown key" in capsys.readouterr().err


def test_config_set_rejects_invalid_values(capsys):
    assert main(["config", "set", "backend", "telepathy"]) == 2
    assert "Invalid value" in capsys.readouterr().err
    assert Config.load().backend == "auto"


def test_config_reset(capsys):
    main(["config", "set", "poll_seconds", "9"])
    capsys.readouterr()
    assert main(["config", "reset"]) == 0
    assert Config.load().poll_seconds == 1.5


def test_status_when_no_daemon_is_running(capsys):
    assert main(["status"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False


def test_doctor_json(capsys):
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["version"]
    assert any(check["name"] == "Discord IPC socket" for check in report["checks"])
    assert "paths" in report


def test_doctor_human_output(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "diagnostics" in out and "Checks:" in out


def test_dump_json_without_players(capsys):
    assert main(["dump", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["players"] == []
    assert report["selected"] is None


def test_dump_writes_a_report(capsys):
    assert main(["dump"]) == 0
    assert (paths.state_dir() / "diagnose.json").is_file()


def test_disable_does_not_crash(capsys):
    assert main(["disable"]) == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_install_and_uninstall(capsys):
    assert main(["install", "--all-browsers", "--no-autostart"]) == 0
    out = capsys.readouterr().out
    assert "native_host" in out
    assert main(["uninstall"]) == 0
    assert json.loads(capsys.readouterr().out)["manifests_removed"]


def test_native_host_switch_is_forwarded(monkeypatch, capsys):
    called = {}

    def fake_run(argv, **kwargs):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(native_host, "run_native_host", fake_run)
    assert main(["--native-host", "chrome-extension://" + "a" * 32 + "/"]) == 0
    assert called["argv"] == ["chrome-extension://" + "a" * 32 + "/"]


def test_dump_switch_is_forwarded(capsys):
    assert main(["--dump", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["players"] == []


def test_settings_without_a_display_explains_the_alternative(capsys, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert main(["settings"]) == 1
    assert "config" in capsys.readouterr().err


def test_no_arguments_runs_the_daemon(monkeypatch, capsys):
    """`music-rpc` alone must start the daemon, not print an error."""
    started = {}

    def fake_run_daemon(config, use_tray=True):
        started["config"] = config
        started["tray"] = use_tray
        return 0

    import musicrpc.service as service
    monkeypatch.setattr(service, "run_daemon", fake_run_daemon)
    assert main([]) == 0
    assert started["tray"] is True


def test_second_instance_reports_already_running(monkeypatch, capsys):
    from musicrpc.single_instance import SingleInstance

    guard = SingleInstance()
    assert guard.acquire()
    try:
        assert main(["run", "--no-tray"]) == 0
        assert "already running" in capsys.readouterr().out
    finally:
        guard.release()
