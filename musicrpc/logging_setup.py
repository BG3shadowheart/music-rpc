"""Logging setup with a rotating log file and optional console output."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import paths

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured = False


def setup_logging(level: str = "INFO", *, console: bool | None = None,
                  log_to_file: bool = True) -> Path | None:
    """Configure the root logger. Safe to call more than once."""
    global _configured
    root = logging.getLogger()
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(numeric)

    if not _configured:
        formatter = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        file_path = None
        if log_to_file:
            try:
                target = paths.log_file()
                paths.ensure_dir(target.parent)
                handler = logging.handlers.RotatingFileHandler(
                    target, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(formatter)
                root.addHandler(handler)
                file_path = target
            except OSError as exc:  # pragma: no cover - disk problems only
                print(f"Could not open log file: {exc}", file=sys.stderr)
        show_console = console if console is not None else sys.stderr.isatty()
        if show_console:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            root.addHandler(stream)
        _configured = True
        return file_path

    return paths.log_file() if log_to_file else None


def reset_logging() -> None:
    """Remove handlers (used by tests)."""
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _configured = False
