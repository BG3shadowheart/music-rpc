#!/bin/sh
# Music RPC - remove a per-user installation.
#
#   ./linux/uninstall.sh           remove the app, keep settings
#   ./linux/uninstall.sh --purge   also delete configuration, logs and the cache
set -eu

APP_DIR="$HOME/.local/share/music-rpc"
VENV="$APP_DIR/venv"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PURGE=""
[ "${1:-}" = "--purge" ] && PURGE="--purge"

if [ -x "$VENV/bin/music-rpc" ]; then
    "$VENV/bin/music-rpc" uninstall ${PURGE:+$PURGE} || true
elif command -v music-rpc >/dev/null 2>&1; then
    music-rpc uninstall ${PURGE:+$PURGE} || true
else
    echo "No Music RPC installation found; removing leftover files only."
fi

rm -rf "$APP_DIR"
echo "Removed $APP_DIR"

if [ -n "$PURGE" ]; then
    rm -rf "$HOME/.config/music-rpc" "$HOME/.local/state/music-rpc" "$HOME/.cache/music-rpc"
    echo "Removed configuration, logs and the artwork cache."
else
    echo "Your settings are kept in ~/.config/music-rpc (use --purge to delete them)."
fi

echo "Done. Browser native-messaging manifests and the autostart entry are gone."
