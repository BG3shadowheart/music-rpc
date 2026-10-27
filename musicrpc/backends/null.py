"""A backend that never reports anything (``backend = "none"``)."""
from __future__ import annotations

import threading

from ..models import Snapshot


class NullBackend:
    name = "null"

    def __init__(self, config=None):
        self.wake = threading.Event()

    def poll(self) -> Snapshot:
        return Snapshot(players=(), taken_at=0.0)

    def close(self):
        pass
