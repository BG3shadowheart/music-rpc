"""Shared fixtures: isolated XDG directories for every test."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from musicrpc.logging_setup import reset_logging  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path, monkeypatch):
    """Point every XDG directory at a temporary tree."""
    home = tmp_path / "home"
    home.mkdir()
    for variable, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "run"),
        ("XDG_DATA_HOME", "data"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(variable, str(directory))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MUSIC_RPC_CONFIG", raising=False)
    monkeypatch.delenv("MUSIC_RPC_NATIVE_HOST", raising=False)
    monkeypatch.delenv("MUSIC_RPC_BIN_DIR", raising=False)
    monkeypatch.delenv("MUSIC_RPC_NO_AUTOSTART", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(ROOT) + (os.pathsep + os.environ.get("PYTHONPATH", "")
                                                  if os.environ.get("PYTHONPATH") else ""))
    reset_logging()
    yield tmp_path
    reset_logging()
