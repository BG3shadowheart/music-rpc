#!/bin/sh
# Build the self-contained Linux bundle with PyInstaller.
#
#   ./linux/build-standalone.sh
#
# Result: dist/music-rpc-<version>-x86_64.tar.gz
#
# The bundle contains its own CPython interpreter, PyQt6 and every Python
# dependency, so the target machine needs no Python at all. It is a directory of
# native and Python artefacts produced by PyInstaller - not a single compiled
# binary and not an AppImage.
set -eu

VERSION="${VERSION:-3.0.0}"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON="${PYTHON:-$SRC_DIR/.venv/bin/python}"
BUILD="$SRC_DIR/build/standalone"
OUT="$SRC_DIR/dist"
NAME="music-rpc"

[ -x "$PYTHON" ] || { echo "Python interpreter not found: $PYTHON" >&2; exit 1; }
"$PYTHON" -c 'import PyInstaller' 2>/dev/null || {
    echo "PyInstaller is missing. Run: $PYTHON -m pip install -r requirements-dev.txt" >&2
    exit 1
}

# PyInstaller needs a *shared* libpython. Debian ships a statically linked
# interpreter, so check first and explain the requirement instead of failing deep
# inside PyInstaller.
"$PYTHON" - "$@" <<'PYCHECK' || exit 1
import sys, sysconfig, glob, os
if not sysconfig.get_config_var("Py_ENABLE_SHARED"):
    sys.exit(0)
libdir = sysconfig.get_config_var("LIBDIR") or ""
library = sysconfig.get_config_var("LDLIBRARY") or ""
candidates = [os.path.join(libdir, library)] if libdir and library else []
candidates += glob.glob("/usr/lib*/**/libpython*.so*", recursive=True)
if not any(os.path.exists(item) for item in candidates if "libpython" in item):
    print("This Python has no shared libpython, so PyInstaller cannot bundle it.")
    print("Install the development package first, e.g.:")
    print("  Fedora/Nobara: sudo dnf install python3-devel")
    print("  Debian/Ubuntu/Mint: sudo apt install libpython3-dev python3-dev")
    print("Then re-run this script.")
    sys.exit(1)
PYCHECK

rm -rf "$BUILD"
mkdir -p "$BUILD" "$OUT"

echo "Running PyInstaller"
cd "$SRC_DIR"
"$PYTHON" -m PyInstaller \
    --noconfirm --clean --log-level WARN \
    --name "$NAME" \
    --distpath "$BUILD/dist" \
    --workpath "$BUILD/work" \
    --specpath "$BUILD" \
    --onedir \
    --console \
    --add-data "$SRC_DIR/musicrpc/data/music-rpc.png:musicrpc/data" \
    --add-data "$SRC_DIR/extension:extension" \
    --hidden-import jeepney.io.blocking \
    --hidden-import pypresence \
    --hidden-import PyQt6.QtWidgets \
    --hidden-import PyQt6.QtGui \
    --hidden-import PyQt6.QtCore \
    --exclude-module tkinter \
    --exclude-module PyQt6.QtQml \
    --exclude-module PyQt6.QtQuick \
    --exclude-module PyQt6.QtWebEngineCore \
    --exclude-module PyQt6.Qt3DCore \
    scripts/frozen_entry.py

BUNDLE="$BUILD/dist/$NAME"
[ -x "$BUNDLE/$NAME" ] || { echo "PyInstaller did not produce $BUNDLE/$NAME" >&2; exit 1; }

echo "Verifying the bundle"
"$BUNDLE/$NAME" version
DISPLAY= "$BUNDLE/$NAME" doctor --json > "$BUILD/doctor.json"
python3 - "$BUILD/doctor.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
print("  bundle doctor: %d checks, %d players, version %s"
      % (len(report["checks"]), len(report["players"]), report["version"]))
PY

cat > "$BUNDLE/README-BUNDLE.txt" <<'EOF'
Music RPC - self-contained Linux bundle
======================================

This directory is the whole application: a bundled CPython interpreter,
PyQt6 and every Python dependency. No system Python is required.

Install for the current user:

    ./install.sh

It copies the bundle to ~/.local/share/music-rpc/bundle, writes the
music-rpc launcher into ~/.local/bin, registers the browser native-messaging
host manifests, the autostart entry and the menu entry, then starts the daemon.

    music-rpc status        # is it running, what is playing
    music-rpc doctor        # diagnose D-Bus / Discord / browser problems
    music-rpc settings      # tray settings window
    ./uninstall.sh          # remove it again

The binary is not a single compiled file: it is a PyInstaller onedir bundle
(music-rpc plus _internal/). Keep the directory intact.
EOF

cp "$SRC_DIR/linux/bundle-install.sh" "$BUNDLE/install.sh"
cp "$SRC_DIR/linux/bundle-uninstall.sh" "$BUNDLE/uninstall.sh"
chmod +x "$BUNDLE/install.sh" "$BUNDLE/uninstall.sh"

echo "Packing $OUT/${NAME}-${VERSION}-x86_64.tar.gz"
tar -C "$BUILD/dist" -czf "$OUT/${NAME}-${VERSION}-x86_64.tar.gz" "$NAME"
ls -lh "$OUT/${NAME}-${VERSION}-x86_64.tar.gz"
