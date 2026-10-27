"""Shared constants.

Values that must stay identical to the Windows build (Discord application id,
native messaging host name, extension protocol version) are marked as such.
"""
from __future__ import annotations

# Product identity -----------------------------------------------------------
APP_NAME = "Music RPC"
APP_ID = "1561450956242161745"  # unchanged: the Discord card uses this app id
__version__ = "3.0.0"
VERSION = __version__

# Native messaging ----------------------------------------------------------
# The host name is part of the contract with the browser extension and with the
# existing Windows build, so it must not change.
NATIVE_HOST_NAME = "com.ytdp.discord.presence"
NATIVE_VERSION = "2.0.0"

# The upstream YouTubeDiscordPresence extension id, quoted in the original
# README. It is *read from the project history*, not invented.
LEGACY_CHROME_EXTENSION_ORIGIN = "chrome-extension://hnmeidgkfcbpjjjpmjmpehjdljlaeaaa/"

# Id of the extension shipped in this repository (extension/chromium). It is
# derived from the pinned "key" in its manifest.json, so the value is computed,
# not guessed. tests/test_extension.py asserts the two stay in sync.
DEV_EXTENSION_ID = "bdgahehmdkmmmfbjefaoongpfkkpfnpg"

# Loopback bridge used by the Windows build (kept for protocol compatibility).
NATIVE_TCP_PORT = 48261

# Unix socket used on Linux between the native host process and the daemon.
BRIDGE_SOCKET_NAME = "bridge.sock"
BRIDGE_MAX_FRAME = 1_000_000

# Artwork ------------------------------------------------------------------
USER_AGENT = f"MusicRPC/{__version__} (Linux; album artwork lookup)"
HTTP_TIMEOUT = 6
PROBE_TTL_SECONDS = 900
RELAY_IMAGE_SIZE = 320

# Behaviour ----------------------------------------------------------------
POLL_SECONDS = 1.5
NO_TRACK_GRACE_SECONDS = 9
REFRESH_SECONDS = 40
ART_RETRY_SECONDS = 60
NATIVE_STICKY_SECONDS = 1800
FRESH_EXTENSION_SECONDS = 20
DISCORD_RETRY_SECONDS = 8
DISCORD_MAX_BACKOFF_SECONDS = 60

# Default player priority: fooyin first, then browsers, then anything else.
DEFAULT_PLAYER_PRIORITY = [
    "fooyin",
    "youtube music",
    "chromium",
    "google-chrome",
    "chrome",
    "brave",
    "thorium",
    "vivaldi",
    "firefox",
    "mpv",
    "vlc",
]

# Players that are never used, even when they are the only ones playing.
DEFAULT_IGNORED_PLAYERS = ["chromium-browser-hint", "kde-connect"]
