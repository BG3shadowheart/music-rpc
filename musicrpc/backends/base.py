"""Backend contract."""
from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from ..models import Snapshot


class MediaBackendError(RuntimeError):
    """The backend could not read media information (bus down, API missing...)."""


@runtime_checkable
class MediaBackend(Protocol):
    name: str

    #: Set by the backend when something changed, so the engine can wake up
    #: immediately instead of waiting for the next poll.
    wake: threading.Event

    def poll(self) -> Snapshot:
        """Return the players that currently expose usable metadata."""

    def close(self) -> None:
        """Release whatever the backend holds."""


class StubBackend:
    """Test helper: returns a fixed snapshot, records polls."""

    name = "stub"

    def __init__(self, *snapshots):
        self.snapshots = list(snapshots) or [Snapshot()]
        self.wake = threading.Event()
        self.polls = 0
        self.closed = False

    def poll(self) -> Snapshot:
        index = min(self.polls, len(self.snapshots) - 1)
        self.polls += 1
        return self.snapshots[index]

    def close(self):
        self.closed = True
