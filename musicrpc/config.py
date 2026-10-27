"""Configuration: JSON file with defaults, validation and atomic saving."""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .extension_id import chrome_origin
from .constants import (
    APP_ID,
    DEV_EXTENSION_ID,
    ART_RETRY_SECONDS,
    DEFAULT_IGNORED_PLAYERS,
    DEFAULT_PLAYER_PRIORITY,
    DISCORD_MAX_BACKOFF_SECONDS,
    DISCORD_RETRY_SECONDS,
    FRESH_EXTENSION_SECONDS,
    LEGACY_CHROME_EXTENSION_ORIGIN,
    NATIVE_STICKY_SECONDS,
    NO_TRACK_GRACE_SECONDS,
    POLL_SECONDS,
    REFRESH_SECONDS,
)

log = logging.getLogger(__name__)

# Origins allowed to talk to the native messaging host: the upstream store
# extension quoted in the Windows README, plus the extension shipped in this
# repository (its id is derived from the pinned manifest key).
DEFAULT_EXTENSION_ORIGINS = [
    LEGACY_CHROME_EXTENSION_ORIGIN,
    chrome_origin(DEV_EXTENSION_ID),
]


class ConfigError(ValueError):
    """Raised when a configuration value cannot be interpreted at all."""


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled", ""}:
            return False
    return default


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(minimum, min(maximum, number))


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        parts = [item.strip() for item in value.replace(";", ",").split(",")]
        return [item for item in parts if item] or list(default)
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items
    return list(default)


@dataclass
class Config:
    """Everything the user can tune. Serialised to ``config.json``."""

    # Player selection ------------------------------------------------------
    player_priority: list[str] = field(default_factory=lambda: list(DEFAULT_PLAYER_PRIORITY))
    ignored_players: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORED_PLAYERS))
    clear_on_pause: bool = True
    ignore_regular_youtube: bool = True
    backend: str = "auto"                     # auto | mpris | windows | none

    # Timing ---------------------------------------------------------------
    poll_seconds: float = POLL_SECONDS
    refresh_seconds: float = REFRESH_SECONDS
    no_track_grace_seconds: float = NO_TRACK_GRACE_SECONDS
    art_retry_seconds: float = ART_RETRY_SECONDS
    extension_sticky_seconds: int = NATIVE_STICKY_SECONDS
    extension_fresh_seconds: float = FRESH_EXTENSION_SECONDS

    # Discord --------------------------------------------------------------
    discord_enabled: bool = True
    discord_app_id: str = APP_ID
    discord_retry_seconds: float = DISCORD_RETRY_SECONDS
    discord_max_backoff_seconds: float = DISCORD_MAX_BACKOFF_SECONDS

    # Artwork --------------------------------------------------------------
    artwork_enabled: bool = True
    artwork_catalog_lookup: bool = True       # Apple Music / Deezer lookups
    artwork_youtube_search: bool = True       # derive a video id from title
    artwork_relay_enabled: bool = False       # opt-in local cover relay
    artwork_relay_cloudflared: bool = False   # opt-in public tunnel
    artwork_relay_public_url: str = ""        # opt-in self-managed public base URL

    # Browser extension ----------------------------------------------------
    extension_bridge_enabled: bool = True
    extension_origins: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXTENSION_ORIGINS)
    )

    # Desktop integration --------------------------------------------------
    tray_enabled: bool = True
    log_level: str = "INFO"
    diagnostics_port: int = 0                 # 0 = disabled

    # Bookkeeping ----------------------------------------------------------
    source_path: str | None = field(default=None, repr=False, compare=False)

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data.pop("source_path", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: str | None = None) -> "Config":
        if not isinstance(data, dict):
            raise ConfigError("configuration root must be a JSON object")
        defaults = cls()
        cfg = cls(source_path=source_path)
        errors: list[str] = []

        def take(name, converter, *args):
            if name not in data:
                return
            try:
                setattr(cfg, name, converter(data[name], *args))
            except Exception as exc:  # pragma: no cover - converters are total
                errors.append(f"{name}: {exc}")

        take("player_priority", _as_str_list, list(DEFAULT_PLAYER_PRIORITY))
        take("ignored_players", _as_str_list, list(DEFAULT_IGNORED_PLAYERS))
        take("extension_origins", _as_str_list, list(DEFAULT_EXTENSION_ORIGINS))
        take("clear_on_pause", _as_bool, defaults.clear_on_pause)
        take("ignore_regular_youtube", _as_bool, defaults.ignore_regular_youtube)
        take("discord_enabled", _as_bool, defaults.discord_enabled)
        take("artwork_enabled", _as_bool, defaults.artwork_enabled)
        take("artwork_catalog_lookup", _as_bool, defaults.artwork_catalog_lookup)
        take("artwork_youtube_search", _as_bool, defaults.artwork_youtube_search)
        take("artwork_relay_enabled", _as_bool, defaults.artwork_relay_enabled)
        take("artwork_relay_cloudflared", _as_bool, defaults.artwork_relay_cloudflared)
        take("extension_bridge_enabled", _as_bool, defaults.extension_bridge_enabled)
        take("tray_enabled", _as_bool, defaults.tray_enabled)

        take("poll_seconds", _as_float, defaults.poll_seconds, 0.5, 30.0)
        take("refresh_seconds", _as_float, defaults.refresh_seconds, 10.0, 3600.0)
        take("no_track_grace_seconds", _as_float, defaults.no_track_grace_seconds, 0.0, 120.0)
        take("art_retry_seconds", _as_float, defaults.art_retry_seconds, 10.0, 3600.0)
        take("extension_fresh_seconds", _as_float, defaults.extension_fresh_seconds, 5.0, 600.0)
        take("discord_retry_seconds", _as_float, defaults.discord_retry_seconds, 1.0, 600.0)
        take("discord_max_backoff_seconds", _as_float,
             defaults.discord_max_backoff_seconds, 2.0, 3600.0)
        take("extension_sticky_seconds", lambda v, d: int(_as_float(v, d, 60, 86400)),
             defaults.extension_sticky_seconds)

        if "discord_app_id" in data:
            value = str(data["discord_app_id"] or "").strip()
            if value.isdigit():
                cfg.discord_app_id = value
            else:
                errors.append("discord_app_id must be numeric")
        if "artwork_relay_public_url" in data:
            value = str(data["artwork_relay_public_url"] or "").strip()
            if not value or value.startswith("https://"):
                cfg.artwork_relay_public_url = value.rstrip("/")
            else:
                errors.append("artwork_relay_public_url must be https or empty")
        if "backend" in data:
            value = str(data["backend"] or "auto").strip().casefold()
            if value in {"auto", "mpris", "windows", "none"}:
                cfg.backend = value
            else:
                errors.append(f"backend: unknown value {value!r}")
        if "log_level" in data:
            value = str(data["log_level"] or "").strip().upper()
            if value in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                cfg.log_level = value
            else:
                errors.append(f"log_level: unknown value {value!r}")
        if "diagnostics_port" in data:
            try:
                cfg.diagnostics_port = max(0, min(65535, int(data["diagnostics_port"])))
            except (TypeError, ValueError):
                errors.append("diagnostics_port must be an integer")

        cfg._load_errors = errors  # type: ignore[attr-defined]
        if errors:
            for item in errors:
                log.warning("Configuration problem: %s", item)
        return cfg

    # -- disk -----------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = Path(path) if path else paths.config_file()
        if not target.is_file():
            return cls(source_path=str(target))
        try:
            raw = target.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Could not read %s (%s); using defaults", target, exc)
            cfg = cls(source_path=str(target))
            cfg._load_errors = [f"unreadable config: {exc}"]  # type: ignore[attr-defined]
            return cfg
        if not isinstance(data, dict):
            cfg = cls(source_path=str(target))
            cfg._load_errors = ["configuration root is not an object"]  # type: ignore[attr-defined]
            return cfg
        return cls.from_dict(data, source_path=str(target))

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path else Path(self.source_path or paths.config_file())
        paths.ensure_dir(target.parent)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self.source_path = str(target)
        return target

    # -- helpers --------------------------------------------------------------
    @property
    def load_errors(self) -> list[str]:
        return list(getattr(self, "_load_errors", []))

    def priority_index(self, name: str) -> int:
        """Return the configured rank for a player, or a large number."""
        from .textutil import slug

        wanted = [slug(item) for item in self.player_priority if slug(item)]
        candidate = slug(name)
        for index, entry in enumerate(wanted):
            if entry and entry in candidate:
                return index
        return len(wanted) + 1

    def is_ignored(self, name: str) -> bool:
        from .textutil import slug

        candidate = slug(name)
        return any(slug(item) and slug(item) in candidate for item in self.ignored_players)
