#!/bin/sh
# Remove the self-contained Music RPC bundle and its per-user integration.
set -eu

DEST="$HOME/.local/share/music-rpc"
BIN_DIR="$HOME/.local/bin"

if [ -x "$DEST/bundle/music-rpc" ]; then
    "$DEST/bundle/music-rpc" uninstall --purge || true
elif [ -x "$BIN_DIR/music-rpc" ]; then
    "$BIN_DIR/music-rpc" uninstall --purge || true
fi

rm -f "$BIN_DIR/music-rpc" "$BIN_DIR/music-rpc-native-host"
rm -rf "$DEST"
echo "Music RPC removed."
