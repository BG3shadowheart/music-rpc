#!/bin/sh
# Remove the browser native-messaging host manifests for the current user.
# Settings, the daemon and the autostart entry are left untouched.
set -eu

MUSIC_RPC=""
if command -v music-rpc >/dev/null 2>&1; then
    MUSIC_RPC=$(command -v music-rpc)
elif [ -x "$HOME/.local/share/music-rpc/venv/bin/music-rpc" ]; then
    MUSIC_RPC="$HOME/.local/share/music-rpc/venv/bin/music-rpc"
elif [ -x "$HOME/.local/bin/music-rpc" ]; then
    MUSIC_RPC="$HOME/.local/bin/music-rpc"
else
    echo "music-rpc was not found; nothing to uninstall." >&2
    exit 0
fi

exec "$MUSIC_RPC" uninstall
