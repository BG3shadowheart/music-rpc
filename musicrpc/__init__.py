"""Music RPC - cross-platform music presence for Discord.

The Linux port lives in this package. The original single-file Windows
application (``app.py``) is kept untouched so the existing Windows build keeps
working; this package reuses the same artwork, formatting, caching and
track-state algorithms, but obtains media information from a platform backend
(MPRIS on Linux, WinRT SMTC on Windows).
"""

from .constants import APP_NAME, APP_ID, __version__

__all__ = ["APP_NAME", "APP_ID", "__version__"]
