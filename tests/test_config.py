"""Configuration loading, validation and saving."""
from __future__ import annotations

import json
from pathlib import Path

from musicrpc import paths
from musicrpc.config import Config, ConfigError
from musicrpc.constants import APP_ID, DEV_EXTENSION_ID, LEGACY_CHROME_EXTENSION_ORIGIN


def test_defaults_are_sane():
    config = Config()
    assert config.discord_app_id == APP_ID
    assert config.player_priority[0] == "fooyin"
    assert config.poll_seconds >= 0.5
    assert config.artwork_relay_enabled is False      # network exposure is opt-in
    assert LEGACY_CHROME_EXTENSION_ORIGIN in config.extension_origins


def test_missing_file_yields_defaults(tmp_path):
    config = Config.load(tmp_path / "nope.json")
    assert config.discord_enabled is True
    assert config.load_errors == []


def test_save_and_reload_round_trip():
    config = Config()
    config.player_priority = ["fooyin", "chrome"]
    config.poll_seconds = 2.0
    path = config.save()
    assert Path(path).is_file()
    reloaded = Config.load(Path(path))
    assert reloaded.player_priority == ["fooyin", "chrome"]
    assert reloaded.poll_seconds == 2.0


def test_default_config_path_uses_xdg():
    assert paths.config_file().name == "config.json"
    assert str(paths.config_file()).startswith(str(paths.config_dir()))


def test_broken_json_falls_back_to_defaults(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{not json", encoding="utf-8")
    config = Config.load(target)
    assert config.discord_enabled is True
    assert config.load_errors and "unreadable" in config.load_errors[0]


def test_non_object_root_is_rejected(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    config = Config.load(target)
    assert config.load_errors


def test_unknown_keys_are_ignored():
    config = Config.from_dict({"poll_seconds": 3, "not_a_real_key": True})
    assert config.poll_seconds == 3
    assert not hasattr(config, "not_a_real_key")


def test_out_of_range_values_are_clamped():
    config = Config.from_dict({"poll_seconds": 999, "refresh_seconds": -5})
    assert config.poll_seconds <= 30
    assert config.refresh_seconds >= 10


def test_string_booleans_are_understood():
    config = Config.from_dict({"clear_on_pause": "no", "discord_enabled": "yes"})
    assert config.clear_on_pause is False
    assert config.discord_enabled is True


def test_comma_separated_lists_are_accepted():
    config = Config.from_dict({"player_priority": "fooyin, chromium ; mpv"})
    assert config.player_priority == ["fooyin", "chromium", "mpv"]


def test_invalid_discord_app_id_is_reported():
    config = Config.from_dict({"discord_app_id": "not-a-number"})
    assert config.discord_app_id == APP_ID
    assert any("discord_app_id" in problem for problem in config.load_errors)


def test_unknown_backend_and_log_level_are_reported():
    config = Config.from_dict({"backend": "telepathy", "log_level": "LOUD"})
    assert config.backend == "auto"
    assert config.log_level == "INFO"
    assert len(config.load_errors) == 2


def test_relay_url_must_be_https():
    config = Config.from_dict({"artwork_relay_public_url": "http://insecure.example"})
    assert config.artwork_relay_public_url == ""
    assert any("https" in problem for problem in config.load_errors)
    good = Config.from_dict({"artwork_relay_public_url": "https://ok.example/"})
    assert good.artwork_relay_public_url == "https://ok.example"


def test_from_dict_rejects_non_objects():
    try:
        Config.from_dict(None)  # type: ignore[arg-type]
    except ConfigError:
        pass
    else:  # pragma: no cover
        raise AssertionError("ConfigError expected")


def test_priority_helpers():
    config = Config()
    assert config.priority_index("fooyin") == 0
    assert config.priority_index("chromium") < config.priority_index("unknown-player")
    assert config.is_ignored("kde-connect") is True
    assert config.is_ignored("fooyin") is False


def test_save_is_atomic_and_creates_parent_directory(tmp_path):
    target = tmp_path / "deep" / "nested" / "config.json"
    path = Config().save(target)
    assert Path(path).is_file()
    assert json.loads(Path(path).read_text(encoding="utf-8"))["poll_seconds"]
    leftovers = [item.name for item in Path(path).parent.iterdir() if item.name.endswith(".tmp")]
    assert leftovers == []


def test_extension_origins_include_the_shipped_extension():
    config = Config.from_dict({"extension_origins": ["chrome-extension://abc/"]})
    assert config.extension_origins == ["chrome-extension://abc/"]
    assert DEV_EXTENSION_ID  # the derived id is always available for the installer


def test_env_override_for_config_path(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    target.write_text(json.dumps({"poll_seconds": 4}), encoding="utf-8")
    monkeypatch.setenv("MUSIC_RPC_CONFIG", str(target))
    assert Config.load().poll_seconds == 4


def test_default_origins_include_the_bundled_extension():
    config = Config()
    origins = config.extension_origins
    assert LEGACY_CHROME_EXTENSION_ORIGIN in origins
    assert f"chrome-extension://{DEV_EXTENSION_ID}/" in origins
