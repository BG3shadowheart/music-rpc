#!/bin/sh
# Build the RPM (Nobara / Fedora / RHEL family).
#
#   ./linux/build-rpm.sh
#
# Requires rpmbuild (dnf install rpm-build) and network access during %build to
# fetch the two vendored pure-Python dependencies.
set -eu

VERSION="${VERSION:-3.0.0}"
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TOP="$SRC_DIR/build/rpm"
NAME="music-rpc"

command -v rpmbuild >/dev/null 2>&1 || {
    echo "rpmbuild is required: sudo dnf install rpm-build" >&2
    exit 1
}

rm -rf "$TOP"
mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
tar -C "$SRC_DIR/.." -czf "$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" \
    --transform "s,^$(basename "$SRC_DIR"),${NAME}-${VERSION}," "$(basename "$SRC_DIR")"
cp "$SRC_DIR/packaging/rpm/${NAME}.spec" "$TOP/SPECS/"

rpmbuild --define "_topdir $TOP" -bb "$TOP/SPECS/${NAME}.spec"
find "$TOP/RPMS" -name '*.rpm' -exec sh -c 'mkdir -p '"$SRC_DIR"'/dist && cp "$1" '"$SRC_DIR"'/dist/' _ {} \;
ls -l "$SRC_DIR/dist"/*.rpm 2>/dev/null || true
