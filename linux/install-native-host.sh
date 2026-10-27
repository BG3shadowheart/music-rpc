#!/bin/sh
# Install ONLY the browser native-messaging host manifests for the current user.
# Use this if Music RPC is already installed (deb/rpm/AppImage) but you want to
# (re-)register the extension host, e.g. after installing Brave.
#
#   ./linux/install-native-host.sh                       # detected browsers only
#   ./linux/install-native-host.sh --all-browsers        # write every known path
#   ./linux/install-native-host.sh --extension-id <id>   # allow another extension
set -eu

MUSIC_RPC=""
if command -v music-rpc >/dev/null 2>&1; then
    MUSIC_RPC=$(command -v music-rpc)
elif [ -x "$HOME/.local/share/music-rpc/venv/bin/music-rpc" ]; then
    MUSIC_RPC="$HOME/.local/share/music-rpc/venv/bin/music-rpc"
elif [ -x "$HOME/.local/bin/music-rpc" ]; then
    MUSIC_RPC="$HOME/.local/bin/music-rpc"
else
    echo "music-rpc was not found. Install Music RPC first (README-LINUX.md)." >&2
    exit 1
fi

exec "$MUSIC_RPC" install --no-autostart --no-menu "$@"
