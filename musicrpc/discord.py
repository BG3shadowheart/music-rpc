"""Discord Rich Presence client.

The Windows build uses ``pypresence``, which is already cross-platform: on Linux
it speaks the Discord IPC socket in ``$XDG_RUNTIME_DIR/discord-ipc-*`` (including
the Snap and Flatpak layouts). We keep it as the transport and wrap it so that:

* the engine never sees a pypresence exception type,
* reconnection is a small state machine in the engine (testable with a fake),
* the presence payload keeps the original Windows wording/formatting.
"""
from __future__ import annotations

import glob
import logging
import os
import socket
import tempfile
from dataclasses import dataclass

log = logging.getLogger(__name__)


class DiscordError(RuntimeError):
    """Any failure while talking to the local Discord client."""


@dataclass(frozen=True)
class Activity:
    """A Discord activity, in the same shape the Windows build sent."""

    details: str
    state: str
    large_image: str | None = None
    large_text: str | None = None
    start: int | None = None
    end: int | None = None
    activity_type: str = "listening"
    status_display_type: str = "details"


class PyPresenceClient:
    """Thin wrapper around ``pypresence.Presence``."""

    def __init__(self, app_id: str, *, presence_factory=None):
        self.app_id = str(app_id)
        self._presence = None
        self._factory = presence_factory

    # -- lifecycle ---------------------------------------------------------
    def connect(self):
        try:
            if self._factory is not None:
                presence = self._factory(self.app_id)
            else:
                from pypresence import Presence  # imported lazily: optional at test time
                presence = Presence(self.app_id)
            presence.connect()
            self._presence = presence
        except Exception as exc:
            self._presence = None
            raise DiscordError(f"Could not connect to Discord: {exc}") from exc

    @property
    def connected(self) -> bool:
        return self._presence is not None

    def close(self):
        presence, self._presence = self._presence, None
        if presence is None:
            return
        try:
            presence.close()
        except Exception:
            pass

    # -- payload -----------------------------------------------------------
    def update(self, activity: Activity):
        if self._presence is None:
            raise DiscordError("Discord is not connected")
        try:
            from pypresence import ActivityType, StatusDisplayType
            types = {"playing": ActivityType.PLAYING, "listening": ActivityType.LISTENING,
                     "watching": ActivityType.WATCHING, "competing": ActivityType.COMPETING}
            statuses = {"name": StatusDisplayType.NAME, "state": StatusDisplayType.STATE,
                        "details": StatusDisplayType.DETAILS}
            kwargs = {
                "details": activity.details[:128],
                "state": activity.state[:128],
                "activity_type": types.get(activity.activity_type, ActivityType.LISTENING),
                "status_display_type": statuses.get(activity.status_display_type,
                                                    StatusDisplayType.DETAILS),
            }
            if activity.large_text:
                kwargs["large_text"] = activity.large_text[:128]
            if activity.large_image:
                kwargs["large_image"] = activity.large_image[:256]
            if activity.start is not None and activity.end is not None:
                kwargs["start"] = int(activity.start)
                kwargs["end"] = int(activity.end)
            return self._presence.update(**kwargs)
        except DiscordError:
            raise
        except Exception as exc:
            raise DiscordError(f"Discord update failed: {exc}") from exc

    def clear(self):
        if self._presence is None:
            return None
        try:
            return self._presence.clear()
        except Exception as exc:
            raise DiscordError(f"Discord clear failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Diagnostics helpers
# ---------------------------------------------------------------------------

def ipc_directories():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    dirs = []
    if runtime:
        dirs.extend([
            runtime,
            os.path.join(runtime, "snap.discord"),
            os.path.join(runtime, "app/com.discordapp.Discord"),
            os.path.join(runtime, "app/com.discordapp.DiscordCanary"),
        ])
    else:
        dirs.append(tempfile.gettempdir())
    dirs.append("/tmp")
    return dirs


def find_ipc_sockets() -> list[str]:
    """Return every usable ``discord-ipc-*`` socket (used by ``doctor``)."""
    found: list[str] = []
    for directory in ipc_directories():
        try:
            for path in sorted(glob.glob(os.path.join(directory, "discord-ipc-*"))):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(1.0)
                        client.connect(path)
                    found.append(path)
                except OSError:
                    continue
        except OSError:
            continue
    return found
