#!/bin/sh
# Build a Debian package (.deb) for Debian / Ubuntu / Linux Mint.
#
#   ./linux/build-deb.sh
#
# The package is architecture-independent ("all"): it ships the Python sources
# plus the two pure-Python dependencies, and uses the distribution's Python 3.
# Pillow and PyQt6 are recommended, not required, because the app degrades
# gracefully without them.
set -eu

VERSION="${VERSION:-3.0.0}"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD="$SRC_DIR/build/deb"
ROOT="$BUILD/music-rpc_${VERSION}_all"
OUT="$SRC_DIR/dist"

command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required" >&2; exit 1; }

rm -rf "$BUILD"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" \
         "$ROOT/usr/lib/music-rpc" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/doc/music-rpc" \
         "$ROOT/usr/share/music-rpc" \
         "$OUT"

echo "Copying application sources"
cp -R "$SRC_DIR/musicrpc" "$ROOT/usr/lib/music-rpc/"
find "$ROOT/usr/lib/music-rpc" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "Vendoring pure-Python dependencies"
PYTHON="${PYTHON:-python3}"
WHEELS="$BUILD/wheels"
mkdir -p "$WHEELS"
"$PYTHON" -m pip download --quiet --no-deps --only-binary :all: \
    --dest "$WHEELS" "jeepney==0.9.0" "pypresence==4.6.2"
"$PYTHON" - "$WHEELS" "$ROOT/usr/lib/music-rpc/vendor" <<'PY'
import sys, zipfile, pathlib
wheels, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)
for wheel in sorted(wheels.glob("*.whl")):
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
print("vendored:", ", ".join(sorted(p.name for p in target.iterdir())))
PY
# Drop the wheel metadata to keep the package small but keep the licences.
find "$ROOT/usr/lib/music-rpc/vendor" -maxdepth 1 -name '*.dist-info' -type d | while read -r info; do
    mkdir -p "$ROOT/usr/share/doc/music-rpc/vendor-licenses"
    cp "$info"/LICENSE* "$ROOT/usr/share/doc/music-rpc/vendor-licenses/" 2>/dev/null || true
    cp "$info"/METADATA "$ROOT/usr/share/doc/music-rpc/vendor-licenses/$(basename "$info").METADATA" 2>/dev/null || true
done
find "$ROOT/usr/lib/music-rpc/vendor" -maxdepth 1 -name '*.dist-info' -type d -exec rm -rf {} +

echo "Installing launcher, desktop entry and icons"
cat > "$ROOT/usr/bin/music-rpc" <<'LAUNCHER'
#!/bin/sh
# Music RPC launcher (installed by the .deb/.rpm package).
PYTHONPATH="/usr/lib/music-rpc:/usr/lib/music-rpc/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
exec /usr/bin/python3 -m musicrpc "$@"
LAUNCHER
chmod 755 "$ROOT/usr/bin/music-rpc"

cat > "$ROOT/usr/share/applications/music-rpc.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=Music RPC
GenericName=Music Rich Presence
Comment=Discord Rich Presence for Fooyin and YouTube Music
Exec=music-rpc run
Icon=music-rpc
Terminal=false
Categories=Audio;Music;Utility;
Keywords=discord;presence;music;fooyin;youtube;
StartupNotify=false
DESKTOP
chmod 644 "$ROOT/usr/share/applications/music-rpc.desktop"

for size in 16 24 32 48 64 128 256 512; do
    install -Dm644 "$SRC_DIR/linux/icons/hicolor/${size}x${size}/apps/music-rpc.png" \
        "$ROOT/usr/share/icons/hicolor/${size}x${size}/apps/music-rpc.png"
done

cp -R "$SRC_DIR/extension" "$ROOT/usr/share/music-rpc/extension"
rm -rf "$ROOT/usr/share/music-rpc/extension/src" \
       "$ROOT/usr/share/music-rpc/extension/tests" \
       "$ROOT/usr/share/music-rpc/extension/build.sh"

cp "$SRC_DIR/README-LINUX.md" "$ROOT/usr/share/doc/music-rpc/README-LINUX.md"
cp "$SRC_DIR/README-LINUX.md" "$ROOT/usr/share/doc/music-rpc/README.LINUX.md" 2>/dev/null || true
[ -f "$SRC_DIR/docs/TROUBLESHOOTING.md" ] && \
    cp "$SRC_DIR/docs/TROUBLESHOOTING.md" "$ROOT/usr/share/doc/music-rpc/TROUBLESHOOTING.md"

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: music-rpc
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: all
Maintainer: BG3shadowheart <noreply@github.com>
Installed-Size: $(du -sk "$ROOT" | cut -f1)
Depends: python3 (>= 3.9)
Recommends: python3-pil, python3-pyqt6
Suggests: cloudflared
Homepage: https://github.com/BG3shadowheart/music-rpc
Description: Discord Rich Presence for Fooyin and YouTube Music
 Music RPC reads the currently playing track from any MPRIS-capable player
 (Fooyin, Chromium browsers, VLC, mpv, ...) and publishes it to the Discord
 desktop client with title, artist, album and cover art. A bundled browser
 extension adds exact YouTube Music artwork.
 .
 The application itself is a Python package driven by the system python3; it is
 not a compiled native binary. Run "music-rpc install" once per user to register
 the autostart entry and the browser native-messaging manifests.
CONTROL

cat > "$ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    echo "Music RPC installed."
    echo "Run as your normal user (not root):  music-rpc install"
    echo "Then start it with:                  music-rpc run --background"
fi
exit 0
POSTINST
chmod 755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
# Stop a running per-user daemon if this runs as that user; never fail removal.
if [ "$1" = "remove" ] && [ "$(id -u)" != "0" ]; then
    command -v music-rpc >/dev/null 2>&1 && music-rpc uninstall >/dev/null 2>&1 || true
fi
exit 0
PRERM
chmod 755 "$ROOT/DEBIAN/prerm"

echo "Generating md5sums"
( cd "$ROOT" && find usr -type f -exec md5sum {} + > DEBIAN/md5sums )

dpkg-deb --build --root-owner-group "$ROOT" "$OUT/music-rpc_${VERSION}_all.deb" >/dev/null
echo "Built $OUT/music-rpc_${VERSION}_all.deb"
dpkg-deb --info "$OUT/music-rpc_${VERSION}_all.deb" | sed -n '1,12p'
