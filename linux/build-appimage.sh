#!/bin/sh
# Build an AppImage from the standalone bundle.
#
#   ./linux/build-appimage.sh
#
# Requirements (all on the *build* machine, none of them end up in the AppImage):
#   * the PyInstaller standalone bundle (built automatically by this script)
#   * appimagetool - either on PATH, in $APPIMAGETOOL, or downloadable from
#     https://github.com/AppImage/appimagetool/releases (needs network access to
#     GitHub release assets, which some sandboxes block)
#   * FUSE is *not* needed for building, only the AppImage runtime is embedded.
#
# Result: dist/MusicRPC-<version>-x86_64.AppImage
#
# Why the AppImage is not just "the Python script renamed": the AppImage
# contains the PyInstaller bundle (its own CPython interpreter plus PyQt6 and
# every dependency) inside a squashfs image with the AppImage type-2 runtime in
# front. Nothing is installed on the target system.
set -eu

VERSION="${VERSION:-3.0.0}"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD="$SRC_DIR/build/appimage"
APPDIR="$BUILD/MusicRPC.AppDir"
OUT="$SRC_DIR/dist"
BUNDLE="$SRC_DIR/build/standalone/dist/music-rpc"

echo "==> 1/4 Building the standalone bundle"
"$SRC_DIR/linux/build-standalone.sh"

[ -x "$BUNDLE/music-rpc" ] || {
    echo "The standalone bundle is missing: $BUNDLE/music-rpc" >&2
    exit 1
}

echo "==> 2/4 Assembling the AppDir"
rm -rf "$BUILD"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps" "$OUT"
cp -R "$BUNDLE"/. "$APPDIR/usr/bin/"
cp "$SRC_DIR/packaging/music-rpc.desktop" "$APPDIR/music-rpc.desktop"
cp "$SRC_DIR/linux/icons/hicolor/512x512/apps/music-rpc.png" "$APPDIR/music-rpc.png"
cp "$APPDIR/music-rpc.desktop" "$APPDIR/usr/share/applications/"
cp "$APPDIR/music-rpc.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# AppImage entry point. $APPDIR is the mounted read-only image.
HERE="${APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}"
# The native messaging host must be reachable by the browser with a stable
# path, so the AppImage knows its own filename through $APPIMAGE.
export MUSIC_RPC_EXECUTABLE="${APPIMAGE:-$HERE/usr/bin/music-rpc}"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/music-rpc" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/usr/bin/music-rpc-native-host" <<'HOST'
#!/bin/sh
# Thin wrapper used as the browser native messaging host path.
exec "${MUSIC_RPC_EXECUTABLE:?}" native-host "$@"
HOST
chmod +x "$APPDIR/usr/bin/music-rpc-native-host"

echo "==> 3/4 Locating appimagetool"
TOOL="${APPIMAGETOOL:-}"
if [ -z "$TOOL" ]; then
    TOOL=$(command -v appimagetool 2>/dev/null || true)
fi
if [ -z "$TOOL" ]; then
    CACHE="$SRC_DIR/build/tools"
    mkdir -p "$CACHE"
    TOOL="$CACHE/appimagetool-x86_64.AppImage"
    if [ ! -x "$TOOL" ]; then
        echo "Downloading appimagetool from GitHub"
        curl -fL -o "$TOOL" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
            || { echo "Could not download appimagetool. Set APPIMAGETOOL=/path/to/appimagetool and retry." >&2; exit 1; }
        chmod +x "$TOOL"
    fi
fi

echo "==> 4/4 Building the AppImage"
export ARCH=x86_64
# appimagetool is itself an AppImage; extract it first so FUSE is not required.
if ! "$TOOL" --version >/dev/null 2>&1; then
    ( cd "$BUILD" && "$TOOL" --appimage-extract >/dev/null 2>&1 ) || true
    [ -x "$BUILD/squashfs-root/AppRun" ] && TOOL="$BUILD/squashfs-root/AppRun"
fi

"$TOOL" --no-appstream --comp zstd \
    "$APPDIR" "$OUT/MusicRPC-${VERSION}-x86_64.AppImage"

chmod +x "$OUT/MusicRPC-${VERSION}-x86_64.AppImage"
echo
echo "Built $OUT/MusicRPC-${VERSION}-x86_64.AppImage"
echo "Install it with:  ./MusicRPC-${VERSION}-x86_64.AppImage install"
