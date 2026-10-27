#!/bin/sh
# Install the self-contained Music RPC bundle for the current user.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST="$HOME/.local/share/music-rpc/bundle"
BIN_DIR="$HOME/.local/bin"

[ -x "$HERE/music-rpc" ] || { echo "Run this from inside the extracted bundle." >&2; exit 1; }

echo "Copying the bundle to $DEST"
mkdir -p "$DEST" "$BIN_DIR"
cp -R "$HERE"/. "$DEST"/

cat > "$BIN_DIR/music-rpc" <<WRAPPER
#!/bin/sh
exec "$DEST/music-rpc" "\$@"
WRAPPER
chmod 755 "$BIN_DIR/music-rpc"

cat > "$BIN_DIR/music-rpc-native-host" <<WRAPPER
#!/bin/sh
MUSIC_RPC_EXECUTABLE="$DEST/music-rpc" exec "$DEST/music-rpc" native-host "\$@"
WRAPPER
chmod 755 "$BIN_DIR/music-rpc-native-host"

"$BIN_DIR/music-rpc" install \
    --host-path "$BIN_DIR/music-rpc-native-host" \
    --no-launchers

echo "Starting Music RPC"
("$BIN_DIR/music-rpc" run --background >/dev/null 2>&1 &)

echo
echo "Music RPC is installed."
echo "  music-rpc status | doctor | settings | disable | enable"
echo "Remove it with: $DEST/uninstall.sh"
