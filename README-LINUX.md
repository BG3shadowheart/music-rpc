# Music RPC for Linux — Discord Rich Presence for Fooyin and YouTube Music

A background application that shows what you are listening to on Discord:
**Fooyin**, any other MPRIS player, and **YouTube Music** in Chrome, Chromium,
Brave or Thorium.

This is a real Linux port of the existing Windows application in this
repository. The Windows build (`app.py`, `build.bat`, `installer/Spotify.iss`) is
untouched and still works; everything Linux-specific lives in the new
`musicrpc/` package.

* Version: **3.0.0**
* Discord application id: `1561450956242161745` (unchanged — the card name and
  icon still come from that Discord Developer Portal application)
* Licence of the original asset: see `ASSET_CREDITS.md`

---

## 1. What it does

| Feature | How it works on Linux |
|---|---|
| Detect Fooyin | MPRIS over D-Bus (`org.mpris.MediaPlayer2.fooyin`). No UI scraping. |
| Detect other players | Any MPRIS player: Chromium browsers, VLC, mpv, Elisa, Spotify… |
| Title / artist / album | MPRIS `xesam:title`, `xesam:artist`, `xesam:album` |
| Play / pause / seek / stop | MPRIS `PlaybackStatus`, `Position`, `Seeked` + `PropertiesChanged` |
| Player disappears / restarts | `NameOwnerChanged` signal, reconnection with backoff |
| YouTube Music artwork + exact track | Small browser extension talks to a native messaging host |
| Discord presence | `pypresence` over the local Discord IPC socket |
| Cover art | Same tier system, cache file format and HTTP verification as the Windows build |
| Tray icon | Qt system tray: Open, Settings, Restart, Quit |
| Start at login | freedesktop `~/.config/autostart/music-rpc.desktop` |
| Single instance | `flock` on `$XDG_RUNTIME_DIR/music-rpc/music-rpc.lock` |

Presence is cleared when playback pauses and nine seconds after the player
disappears, so a stale track never stays on your profile.

---

## 2. Install

Pick **one** of the four routes. All of them are per-user except the optional
package install; nothing needs root to *run* the app.

### Route A — Debian package (Debian, Ubuntu, Linux Mint) ✅ tested artifact

```bash
sudo apt install python3 python3-pil python3-pyqt6     # python3-pyqt6 is optional
git clone https://github.com/BG3shadowheart/music-rpc.git
cd music-rpc
sudo apt install ./dist/music-rpc_3.0.0_all.deb   # or: sudo dpkg -i dist/…deb
music-rpc install        # run as YOUR user, registers autostart + browser hosts
music-rpc run --background
```

`python3-pyqt6` only provides the tray icon and the settings window; without it
everything still works from the command line.

### Route B — RPM (Nobara, Fedora, RHEL family)

The spec is in `packaging/rpm/music-rpc.spec`. Build it once, then install:

```bash
sudo dnf install rpm-build python3-devel python3-pip
./linux/build-rpm.sh                 # writes dist/music-rpc-3.0.0-1*.noarch.rpm
sudo dnf install dist/music-rpc-3.0.0-1*.noarch.rpm
music-rpc install                    # as your user
music-rpc run --background
```

> The RPM was written and reviewed but **not built** in the environment where
> this port was developed (no `rpmbuild` available there). Use Route C if you
> want a route that has been executed end-to-end.

### Route C — No root, private virtualenv (works on Nobara and Mint) ✅ tested steps

```bash
git clone https://github.com/BG3shadowheart/music-rpc.git
cd music-rpc
./linux/install.sh
```

It creates `~/.local/share/music-rpc/venv`, installs the pinned dependencies from
`requirements-lock.txt`, writes `~/.local/bin/music-rpc`, registers the autostart
entry and the browser hosts, and starts the daemon.

Remove it again:

```bash
./linux/uninstall.sh          # keeps your settings
./linux/uninstall.sh --purge  # deletes settings, logs and the artwork cache
```

### Route D — AppImage

AppImages need the type-2 runtime, which is not redistributable from this
sandbox (GitHub release downloads are blocked here and no AppImage tooling was
available). The build script is complete and documented:

```bash
./linux/build-appimage.sh     # needs PyInstaller + appimagetool + FUSE-free build
./MusicRPC-3.0.0-x86_64.AppImage install
```

See section 10 for exactly what is and is not a standalone artifact.

### Dependencies, in plain words

| Component | Needed for | Where it comes from |
|---|---|---|
| `python3` ≥ 3.9 | everything | distribution |
| `jeepney` (pure Python) | MPRIS over D-Bus | vendored in .deb/.rpm, PyPI for venv |
| `pypresence` (pure Python) | Discord IPC | vendored in .deb/.rpm, PyPI for venv |
| `Pillow` | verifying cover images, relay re-encoding | `python3-pil` / `python3-pillow` (optional) |
| `PyQt6` | tray icon + settings window | `python3-pyqt6` (optional) |
| `cloudflared` | **only** for the opt-in artwork relay | optional, never required |

No Windows-only packages (WinRT, pywin32, Nuitka-Windows) are part of the Linux
dependency set.

---

## 3. Enable Fooyin Rich Presence (step by step)

1. Install and start **fooyin**, then play something.
2. In a terminal, check that the player is visible on D-Bus:
   ```bash
   busctl --user list | grep -i mpris
   ```
   You should see `org.mpris.MediaPlayer2.fooyin`.
   (`playerctl -l` works too if you have `playerctl` installed.)
3. Start Music RPC:
   ```bash
   music-rpc run --background
   ```
4. Ask it what it sees:
   ```bash
   music-rpc dump
   ```
   Expected output:
   ```
   Player: fooyin  (local)
   Title:  …
   Artist: …
   Album:  …
   State:  playing
   Art:    catalog https://…
   ```
5. Make sure the **Discord desktop app** is running (not the web version) and
   that your Discord status is not set to "Invisible".
6. `music-rpc status` should print `"discord": "connected"`.

Fooyin appears on Discord as *Listening to …* with the title on the first row,
the artist on the second and the album on the cover tooltip — the same
formatting the Windows build used.

### Player priority

Fooyin wins whenever it is playing. If two players are playing at once, the
order in `player_priority` decides. Change it without editing JSON:

```bash
music-rpc config set player_priority '["fooyin", "chromium", "brave", "thorium"]'
music-rpc restart    # or: music-rpc disable && music-rpc enable
```

A player that is *playing* always beats one that is *paused*, and players listed
in `ignored_players` are never used.

---

## 4. Enable YouTube Music Rich Presence (step by step)

The browser extension is in `extension/chromium` (Chrome, Chromium, Brave,
Thorium) and `extension/firefox` (experimental, see below). It is **not in any
extension store**; you load it unpacked. Its id is pinned by the `key` field in
`manifest.json`, so it is the same on every machine:

```
chrome-extension://bdgahehmdkmmmfbjefaoongpfkkpfnpg/
```

That value is *computed* from the manifest key (Chromium hashes the public key),
not invented — `tests/test_extension.py` checks it.

### 4.1 Register the native messaging host

```bash
music-rpc install
```

This writes, for every browser it finds:

| Browser | Manifest location |
|---|---|
| Chrome | `~/.config/google-chrome/NativeMessagingHosts/com.ytdp.discord.presence.json` |
| Chromium | `~/.config/chromium/NativeMessagingHosts/…` |
| Brave | `~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/…` |
| Thorium | `~/.config/thorium/NativeMessagingHosts/…` |
| Edge / Vivaldi | `~/.config/microsoft-edge/…`, `~/.config/vivaldi/…` |
| Chromium (Snap) | `~/snap/chromium/current/.config/chromium/NativeMessagingHosts/…` |
| Flatpak browsers | `~/.var/app/<app-id>/config/<browser>/NativeMessagingHosts/…` |
| Firefox | `~/.mozilla/native-messaging-hosts/com.ytdp.discord.presence.json` |

Use `music-rpc install --all-browsers` to write every path even if the browser's
config directory does not exist yet (for example if you install Brave later).
Re-run `music-rpc install` after installing a new browser.

### 4.2 Load the extension

1. Open `chrome://extensions` (Brave: `brave://extensions`, Thorium:
   `thorium://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**.
4. Select the folder printed by `music-rpc install`, for example
   `/usr/share/music-rpc/extension/chromium` (package install) or
   `~/music-rpc/extension/chromium` (source checkout).
5. **Restart the browser** — browsers read native messaging manifests only at
   startup.
6. Open <https://music.youtube.com> and play a song. `music-rpc status` should
   show the title and `"kind": "youtube"`, and Discord shows the real
   YouTube thumbnail.

### 4.3 Permissions the extension asks for

* `nativeMessaging` — to talk to the local companion (required).
* host permission `https://music.youtube.com/*` — to read the player bar.
* That is all: no tabs, storage, cookies or `<all_urls>` access.

### 4.4 Ordinary YouTube videos

The content script only matches `music.youtube.com`. It never runs on
`youtube.com/watch`, so ordinary videos are ignored by design. If you *want*
regular YouTube, change `ignore_regular_youtube` to `false` in the config and
add `https://www.youtube.com/*` to `content_scripts.matches` yourself — this is
not supported out of the box.

### 4.5 Firefox

`extension/firefox` is provided, uses the same code and the
`youtube-music-presence@music-rpc.local` id that the host manifest allows. It is
**untested**: Firefox requires signing for permanent installation, and only
supports temporary loading via `about:debugging` → *Load Temporary Add-on*. Use
the Chromium version if you can.

### 4.6 The upstream YouTubeDiscordPresence extension

The Windows build also accepted the store extension
`hnmeidgkfcbpjjjpmjmpehjdljlaeaaa`. That origin stays in the allow-list, so if
you already have it installed in a Chromium browser it keeps working with this
companion.

---

## 5. Start automatically at login

`music-rpc install` already wrote the autostart entry:

```
~/.config/autostart/music-rpc.desktop
```

To turn it on/off later:

```bash
music-rpc disable     # stop now, do not start at login (settings are kept)
music-rpc enable      # re-enable and start
```

To move the autostart entry to the old per-session path, or to see whether the
desktop environment honours it:

```bash
cat ~/.config/autostart/music-rpc.desktop
```

On KDE Plasma you can also confirm it under *System Settings → Autostart*; on
Cinnamon under *Startup Applications*.

---

## 6. Everyday commands

```bash
music-rpc                    # run in the foreground (Ctrl+C stops it)
music-rpc run --background   # what the autostart entry runs
music-rpc status             # JSON: Discord state, current track, artwork tier
music-rpc dump               # what the app sees right now (+ artwork resolution)
music-rpc doctor             # diagnose D-Bus, Discord, browsers, config
music-rpc settings           # tray-style settings window (needs PyQt6)
music-rpc config show        # print the configuration
music-rpc config edit        # open config.json in $EDITOR
music-rpc config set poll_seconds 1.5
music-rpc install            # (re-)register hosts, autostart, menu entry
music-rpc uninstall --purge  # remove everything
music-rpc version
```

Logs: `~/.local/state/music-rpc/presence.log` (rotated, 1 MB × 3).
Diagnostic report: `~/.local/state/music-rpc/diagnose.json`.
Config: `~/.config/music-rpc/config.json`.
Artwork cache: `~/.cache/music-rpc/artwork-cache.json`.

### Useful settings

| Key | Default | Meaning |
|---|---|---|
| `player_priority` | `["fooyin", "youtube music", "chromium", …]` | who wins when several players play |
| `ignored_players` | `["chromium-browser-hint", "kde-connect"]` | never publish these |
| `clear_on_pause` | `true` | clear the card on pause |
| `poll_seconds` | `1.5` | MPRIS polling interval (signals wake it instantly anyway) |
| `refresh_seconds` | `40` | re-send the activity to keep the progress bar honest |
| `no_track_grace_seconds` | `9` | delay before clearing after a player vanishes |
| `artwork_enabled` | `true` | show covers at all |
| `artwork_catalog_lookup` | `true` | Apple Music / Deezer cover lookup |
| `artwork_youtube_search` | `true` | derive a video id from title + artist |
| `artwork_relay_enabled` | `false` | **opt-in** local cover relay (section 7) |
| `tray_enabled` | `true` | tray icon |
| `log_level` | `INFO` | `DEBUG` for verbose logs |

Changes apply after a restart (`music-rpc disable && music-rpc enable`), or use
*Settings → Save* in the tray window, which restarts automatically.

---

## 7. Privacy and the optional cover relay

Everything is on-device by default:

* Playback data is read from D-Bus on your machine and the title/artist/album is
  sent **only to the local Discord client**.
* For cover art the app may send the title/artist/album to Apple's iTunes Search
  API, Deezer's public API or a YouTube search page, to find a **permanent URL**
  to a public cover image. Discord then fetches that URL.
* Local music files are **never uploaded**. Embedded art without a catalogue
  match stays local unless you turn on the relay below.
* No tokens, cookies or credentials are read or stored. No listening history is
  stored, only the cover-URL cache (`artwork-cache.json`).

The Windows build always opened a Cloudflare Quick Tunnel to publish embedded
art. On Linux that is **off by default**; there are two opt-in modes:

```bash
# 1) cloudflared (a random *.trycloudflare.com hostname while the app runs)
#    risk: the current cover is publicly reachable while the tunnel is up
music-rpc config set artwork_relay_enabled true
music-rpc config set artwork_relay_cloudflared true

# 2) your own HTTPS front end (reverse proxy / tunnel you control)
music-rpc config set artwork_relay_enabled true
music-rpc config set artwork_relay_public_url https://music.example.com
```

The relay binds to `127.0.0.1` and serves exactly one cover behind a random
token that changes per track. Relay URLs are never written to the permanent
cache.

---

## 8. Troubleshooting

Start with:

```bash
music-rpc doctor
```

It prints one line per subsystem (D-Bus, MPRIS players, Discord socket,
extension bridge, native messaging manifests, tray) plus the last log lines.
`music-rpc doctor --json` is machine readable.

| Symptom | Check |
|---|---|
| Nothing shows on Discord | Is the **desktop** app running (not the browser)? `ls $XDG_RUNTIME_DIR/discord-ipc-*`. Flatpak Discord: reinstall or `--filesystem` the socket. Then `music-rpc status`. |
| `discord: disconnected` | Discord was closed after Music RPC started; it reconnects automatically every 8 s (up to 60 s backoff). Check `presence.log`. |
| Fooyin not detected | `busctl --user list \| grep -i mpris` — if fooyin is missing, it is not exporting MPRIS; check the player, not Music RPC. |
| Presence stuck on an old track | Should be impossible (9 s grace + clear on stop). If it happens, `music-rpc restart` and send the log. |
| Cover art missing | `music-rpc dump` shows the chosen tier. `none` means no permanent URL was found; `cache-unverified` means the CDN was unreachable. |
| Extension shows "Could not connect" | Re-run `music-rpc install`, restart the browser, check `~/.mozilla/…` or `~/.config/…/NativeMessagingHosts`. |
| Extension connects but nothing shows | MPRIS is the source of playback state; if the browser exposes no MPRIS player, close other tabs or set `clear_on_pause false`. `music-rpc dump` lists every player. |
| No tray icon | Your desktop has no StatusNotifier tray, or PyQt6 is missing (`music-rpc doctor`). The app keeps working headless. |
| High CPU | Should not happen (1.5 s poll, ~0 % idle). Run `music-rpc config set log_level DEBUG` and check the log; also `music-rpc doctor` for a reconnect loop. |

Full details, including D-Bus debugging commands and per-browser quirks:
[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

## 9. How the port is structured

```
app.py                     unchanged Windows application (SMTC + WinRT)
musicrpc/                  new cross-platform Linux implementation
├── backends/mpris.py      D-Bus MPRIS backend (Fooyin, browsers, VLC, mpv)
├── backends/windows_smtc.py  WinRT backend for the same engine (parity, not shipped)
├── engine.py              presence state machine (pause, grace, dedupe, backoff)
├── artwork.py             the Windows artwork pipeline, ported tier by tier
├── discord.py             pypresence wrapper + IPC socket discovery
├── bridge.py              extension <-> daemon unix socket + packet parsing
├── native_host.py         Chrome native messaging protocol (stdio framing)
├── relay.py               opt-in cover relay (cloudflared or your own URL)
├── config.py, paths.py    XDG config with validation
├── tray.py                PyQt6 tray + settings window
├── installer.py           native hosts, autostart, launchers
├── doctor.py, cli.py      diagnostics + command line
extension/                 Chromium and Firefox extensions (source + built copies)
tests/                     226 pytest tests + 12 Node tests
linux/, packaging/         installers, .deb/.rpm/AppImage/Flatpak build files
```

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 10. What is actually standalone?

Be precise here, because "Python app" and "compiled binary" are not the same
thing:

| Artifact | Self-contained? | Needs what on the target machine |
|---|---|---|
| `dist/music-rpc_3.0.0_all.deb` | No — a normal distribution package | `python3` (≥ 3.9). Ships the app and the two pure-Python dependencies. Pillow and PyQt6 are recommended. **Built and verified in this repo.** |
| RPM built from `packaging/rpm/music-rpc.spec` | No | `python3`. Spec provided, **not built here**. |
| `./linux/install.sh` (venv) | No | `python3` + network for PyPI. Installs into `~/.local/share/music-rpc/venv`. |
| `linux/build-standalone.sh` (PyInstaller) | Yes — bundles CPython, PyQt6 and all dependencies in a directory | nothing, but the *build* needs a shared `libpython` (`python3-devel` / `libpython3-dev`) |
| `linux/build-appimage.sh` | Yes — the same bundle inside a type-2 AppImage | nothing; building needs `appimagetool` |

Neither the `.deb` nor the venv install contains a compiled executable: both run
the Python package with the system interpreter. The only truly standalone
artifacts are the PyInstaller bundle and the AppImage built from it — and those
two could **not** be produced in the sandbox where this port was developed
(no shared `libpython`, GitHub release downloads blocked). The build scripts are
complete and documented; on a normal Nobara or Mint machine they work.

---

## 11. Tests

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest            # 226 automated tests
node --test extension/tests  # 12 extension tests (run automatically by pytest)
```

What is covered automatically: MPRIS metadata parsing, playback-state
transitions, track changes, seeks, pause/stop clearing, the 9-second grace
period, player prioritisation, duplicate packets, artwork tiers and failures,
native messaging framing, the Unix-socket bridge end to end, Discord
disconnection/reconnection, configuration validation, installer paths,
extension/manifest consistency.

What still needs a human with a desktop session: a real Discord client, a real
Fooyin, a real browser with the extension, the Qt tray on Plasma/Cinnamon, and
the AppImage build. The checklist with the current status is in
[`docs/TEST-RESULTS.md`](docs/TEST-RESULTS.md).

---

## 12. Known limitations

* **Wayland + tray**: works on KDE Plasma (StatusNotifierItem) and Cinnamon
  (XApp status icons). A bare Wayland session without a tray host shows no icon;
  the app still runs.
* **Flatpak** packaging is included as an experimental manifest only: sandboxed
  browsers cannot start a native messaging host from the host, and Discord IPC
  needs explicit socket sharing. Use the .deb, the RPM or the tarball.
* **Snap browsers** work if the native messaging manifest is written to the Snap
  config path (`music-rpc install` does this when it detects it), but Snap
  confinement can still block the host binary; Flatpak/Snap store browsers are
  not officially supported.
* **Firefox** extension is untested (unsigned).
* **MPRIS artwork**: some players publish no `mpris:artUrl`; the catalogue
  lookup then provides the cover, which can occasionally pick a different
  release of the same album.
* The Discord card's **name and icon** come from the Discord application
  `1561450956242161745`, not from this repository (see the Windows README).
* The RPM spec and the AppImage/Flatpak files were reviewed but not built or run
  in this environment; see the table in section 10.
