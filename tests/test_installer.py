"""Per-user installation: launchers, browser manifests, autostart, uninstall."""
from __future__ import annotations

import json
import os
from pathlib import Path

from musicrpc import autostart, installer, native_install
from musicrpc.config import Config
from musicrpc.constants import DEV_EXTENSION_ID, LEGACY_CHROME_EXTENSION_ORIGIN, NATIVE_HOST_NAME


def make_browsers(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    config = tmp_path / "config"
    paths = {
        "chrome": config / "google-chrome",
        "chromium": config / "chromium",
        "brave": config / "BraveSoftware" / "Brave-Browser",
        "thorium": config / "thorium",
        "firefox": home / ".mozilla",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def test_install_writes_manifests_launchers_and_autostart(tmp_path):
    paths = make_browsers(tmp_path)
    result = installer.install(Config(), with_autostart=True)

    launcher = Path(result["launcher"])
    native_launcher = Path(result["native_launcher"])
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    assert "musicrpc" in launcher.read_text()
    assert "native-host" in native_launcher.read_text()
    assert Path(result["host"]) == native_launcher

    chrome_manifest = paths["chrome"] / "NativeMessagingHosts" / f"{NATIVE_HOST_NAME}.json"
    data = json.loads(chrome_manifest.read_text())
    assert data["name"] == NATIVE_HOST_NAME
    assert data["type"] == "stdio"
    assert data["path"] == str(native_launcher)
    assert LEGACY_CHROME_EXTENSION_ORIGIN in data["allowed_origins"]
    assert f"chrome-extension://{DEV_EXTENSION_ID}/" in data["allowed_origins"]

    for key in ("chromium", "brave", "thorium"):
        assert (paths[key] / "NativeMessagingHosts" / f"{NATIVE_HOST_NAME}.json").is_file()

    firefox_manifest = paths["firefox"] / "native-messaging-hosts" / f"{NATIVE_HOST_NAME}.json"
    firefox = json.loads(firefox_manifest.read_text())
    assert firefox["allowed_extensions"] == [native_install.FIREFOX_EXTENSION_ID]
    assert "allowed_origins" not in firefox

    autostart_file = Path(result["autostart"])
    text = autostart_file.read_text()
    assert "[Desktop Entry]" in text
    assert "run" in text and "--background" in text
    assert Path(result["menu_entry"]).is_file()


def test_browsers_that_are_not_installed_are_skipped(tmp_path):
    result = installer.install(Config(), with_autostart=False)
    assert result["manifests_installed"] == []
    assert result["manifests_skipped"]
    assert result["autostart"] is None


def test_force_writes_every_manifest(tmp_path):
    result = installer.install(Config(), force_browsers=True, with_autostart=False)
    assert len(result["manifests_installed"]) == len(native_install.all_targets())


def test_extra_extension_ids_are_added(tmp_path):
    make_browsers(tmp_path)
    result = installer.install(Config(), extension_ids=["a" * 32], with_autostart=False)
    assert f"chrome-extension://{'a' * 32}/" in result["origins"]


def test_uninstall_removes_everything(tmp_path):
    make_browsers(tmp_path)
    Config().save()                       # something for --purge to remove
    result = installer.install(Config(), with_autostart=True)
    manifest = Path(result["manifests_installed"][0][1])
    assert manifest.is_file()

    removed = installer.uninstall(purge=True)
    assert removed["manifests_removed"]
    assert removed["autostart_removed"] is True
    assert removed["menu_entry_removed"] is True
    assert not manifest.exists()
    assert not autostart.autostart_enabled()
    assert removed["binaries_removed"]
    assert removed["purged"]


def test_existing_manifests_report_broken_hosts(tmp_path):
    make_browsers(tmp_path)
    installer.install(Config(), with_autostart=False)
    entries = native_install.existing_manifests()
    installed = [item for item in entries if item.get("exists")]
    assert installed
    assert all(item["host_executable"] for item in installed)


# ---------------------------------------------------------------------------
# Autostart helpers
# ---------------------------------------------------------------------------

def test_autostart_disable_and_enable_keep_the_file():
    path = autostart.install_autostart("/usr/bin/music-rpc run --background")
    assert path.is_file()
    assert "Hidden=true" not in path.read_text()
    assert autostart.disable_autostart() is True
    assert "Hidden=true" in path.read_text()
    assert autostart.enable_autostart() is True
    assert "Hidden=true" not in path.read_text()
    assert autostart.remove_autostart() is True
    assert not path.exists()


def test_autostart_entry_quotes_executable_paths():
    entry = autostart.autostart_entry(["/opt/my apps/music-rpc", "run", "--background"])
    assert "'/opt/my apps/music-rpc'" in entry


def test_desktop_entry_contains_required_keys():
    entry = autostart.desktop_entry("music-rpc run")
    for key in ("Type=Application", "Name=Music RPC", "Exec=music-rpc run",
                "Icon=music-rpc", "Categories="):
        assert key in entry


def test_launcher_pythonpath_includes_vendored_dependencies(tmp_path, monkeypatch):
    """Packages ship pure-Python deps in <root>/vendor, so it must be on the path."""
    root = tmp_path / "music-rpc"
    (root / "musicrpc").mkdir(parents=True)
    monkeypatch.setattr(installer, "__file__", str(root / "musicrpc" / "installer.py"))

    assert installer._installed_package_dir() == str(root)

    (root / "vendor" / "jeepney").mkdir(parents=True)
    value = installer._installed_package_dir()
    assert value.split(os.pathsep) == [str(root), str(root / "vendor")]


def test_restart_uses_appimage_path_when_frozen(monkeypatch):
    from musicrpc import service

    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(service.sys, "executable", "/tmp/.mount_x/usr/bin/music-rpc", raising=False)
    monkeypatch.setattr(service.sys, "argv", ["music-rpc", "run"], raising=False)
    monkeypatch.setenv("MUSIC_RPC_EXECUTABLE", "/home/u/Apps/Music_RPC.AppImage")
    assert service.restart_argv() == ["/home/u/Apps/Music_RPC.AppImage", "run"]
