# RPM spec for Music RPC - Nobara / Fedora / RHEL-family.
#
# Build (as a normal user, no root needed for the build itself):
#     rpmbuild -bb packaging/rpm/music-rpc.spec
# or with mock:
#     mock -r fedora-40-x86_64 --buildsrpm --spec packaging/rpm/music-rpc.spec \
#          --sources packaging/rpm/
#
# NOTE: this spec was written and reviewed, but it could not be executed in the
# environment where the Linux port was developed (no rpmbuild available there).
# Treat the .deb and the self-contained tarball as the tested artifacts.
#
# The package uses the distribution Python 3 and vendors the two pure-Python
# dependencies (jeepney, pypresence); Pillow and PyQt6 are recommended, not
# required, because the application degrades gracefully without them.

%global debug_package %{nil}
%global _build_id_links none

Name:           music-rpc
Version:        3.0.0
Release:        1%{?dist}
Summary:        Discord Rich Presence for Fooyin and YouTube Music
URL:            https://github.com/BG3shadowheart/music-rpc
License:        LicenseRef-Fedora-Public-Domain
BuildArch:      noarch

Source0:        %{name}-%{version}.tar.gz

Requires:       python3 >= 3.9
Recommends:     python3-pillow
Recommends:     python3-pyqt6
Suggests:       cloudflared

%description
Music RPC reads the currently playing track from any MPRIS-capable player
(Fooyin, Chromium-based browsers, VLC, mpv, ...) through D-Bus and publishes it
to the Discord desktop client: title, artist, album and cover art. A bundled
browser extension adds exact YouTube Music artwork and video ids.

The application is a Python package driven by the system python3; it is not a
compiled native binary. Run "music-rpc install" once per user to register the
autostart entry and the browser native-messaging manifests.

%prep
%setup -q

%build
# Vendor the two pure-Python dependencies so the package needs no extra
# python3dist() packages at runtime. This step needs network access.
%{__python3} -m pip download --quiet --no-deps --only-binary :all: \
    --dest %{_builddir}/wheels "jeepney==0.9.0" "pypresence==4.6.2"

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_prefix}/lib/music-rpc %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications %{buildroot}%{_datadir}/music-rpc
install -d %{buildroot}%{_datadir}/icons/hicolor %{buildroot}%{_docdir}/music-rpc

cp -r musicrpc %{buildroot}%{_prefix}/lib/music-rpc/
find %{buildroot}%{_prefix}/lib/music-rpc -name '__pycache__' -type d -prune -exec rm -rf {} +

# Unpack the pure-Python wheels into the vendor directory.
install -d %{buildroot}%{_prefix}/lib/music-rpc/vendor
for wheel in %{_builddir}/wheels/*.whl ; do
    ( cd %{buildroot}%{_prefix}/lib/music-rpc/vendor && unzip -qo "$wheel" ) || \
        %{__python3} -m zipfile -e "$wheel" %{buildroot}%{_prefix}/lib/music-rpc/vendor
done
find %{buildroot}%{_prefix}/lib/music-rpc/vendor -maxdepth 1 -name '*.dist-info' -type d \
    -exec cp -r {} %{buildroot}%{_docdir}/music-rpc/ \; \
    -exec rm -rf {} +

cat > %{buildroot}%{_bindir}/music-rpc <<'LAUNCHER'
#!/bin/sh
PYTHONPATH="/usr/lib/music-rpc:/usr/lib/music-rpc/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
exec /usr/bin/python3 -m musicrpc "$@"
LAUNCHER
chmod 755 %{buildroot}%{_bindir}/music-rpc

install -m644 packaging/music-rpc.desktop %{buildroot}%{_datadir}/applications/music-rpc.desktop
for size in 16 24 32 48 64 128 256 512 ; do
    install -Dm644 linux/icons/hicolor/${size}x${size}/apps/music-rpc.png \
        %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/music-rpc.png
done

cp -r extension %{buildroot}%{_datadir}/music-rpc/extension
rm -rf %{buildroot}%{_datadir}/music-rpc/extension/src \
       %{buildroot}%{_datadir}/music-rpc/extension/tests \
       %{buildroot}%{_datadir}/music-rpc/extension/build.sh

install -m644 README-LINUX.md %{buildroot}%{_docdir}/music-rpc/README-LINUX.md
[ -f docs/TROUBLESHOOTING.md ] && install -m644 docs/TROUBLESHOOTING.md %{buildroot}%{_docdir}/music-rpc/

%files
%{_bindir}/music-rpc
%{_prefix}/lib/music-rpc
%{_datadir}/applications/music-rpc.desktop
%{_datadir}/icons/hicolor/*/apps/music-rpc.png
%{_datadir}/music-rpc/extension
%doc %{_docdir}/music-rpc/*

%post
echo "Music RPC installed."
echo "Run as your normal user (not root):  music-rpc install"
echo "Then start it with:                  music-rpc run --background"

%changelog
* Mon Oct 27 2025 BG3shadowheart <noreply@github.com> - 3.0.0-1
- First Linux release: MPRIS backend (Fooyin + browsers), native messaging host,
  Chromium/Firefox extension, system tray, autostart, artwork reuse from 2.0.
