#!/bin/sh
# Music RPC - per-user source/venv installation for Linux.
#
#   ./linux/install.sh              install into ~/.local
#   ./linux/install.sh --no-deps    reuse an existing virtual environment
#   PREFIX=/opt/music ./linux/install.sh
#
# Everything is per-user: no root, no system Python changes.
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$HOME/.local/share/music-rpc"
VENV="$APP_DIR/venv"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
NO_DEPS=0

for arg in "$@"; do
    case "$arg" in
        --no-deps) NO_DEPS=1 ;;
        --help|-h)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

command -v python3 >/dev/null 2>&1 || {
    echo "python3 is required. On Nobara/Fedora: sudo dnf install python3" >&2
    echo "On Debian/Ubuntu/Mint:       sudo apt install python3 python3-venv" >&2
    exit 1
}

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')
[ "$PY_OK" = "1" ] || { echo "Python 3.9 or newer is required." >&2; exit 1; }

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating a private Python environment in $VENV"
    python3 -m venv "$VENV" || {
        echo "Could not create a virtual environment. Install python3-venv:" >&2
        echo "  Fedora/Nobara: sudo dnf install python3-virtualenv python3-pip" >&2
        echo "  Debian/Mint:   sudo apt install python3-venv python3-pip" >&2
        exit 1
    }
fi

if [ "$NO_DEPS" = "0" ]; then
    echo "Installing dependencies (this downloads from PyPI)"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r "$SRC_DIR/requirements-lock.txt"
fi

echo "Installing Music RPC itself"
"$VENV/bin/python" -m pip install --quiet --no-deps --force-reinstall "$SRC_DIR"

mkdir -p "$PREFIX/bin"
"$VENV/bin/music-rpc" install

echo
echo "Music RPC is installed."
echo "  Start it:            music-rpc run --background   (or log out and back in)"
echo "  Status:              music-rpc status"
echo "  Diagnostics:         music-rpc doctor"
echo "  Settings window:     music-rpc settings"
echo "  Remove everything:   $SRC_DIR/linux/uninstall.sh"
echo
echo "Next: install the browser extension from"
echo "  $SRC_DIR/extension/chromium"
echo "See README-LINUX.md for the click-by-click steps."
