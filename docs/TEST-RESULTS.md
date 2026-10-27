# Test results

Environment where this port was developed and verified:

```
Debian GNU/Linux 12 (bookworm), x86_64, kernel 6.1
Python 3.11.2, Node 22.22.3
No graphical session, no D-Bus session bus, no Discord client, no browser,
no Fooyin. No root access.
```

Because there is no desktop session in that environment, the automated suite
uses fakes and real sockets rather than a live desktop. Section 3 lists exactly
what still needs a human.

## 1. Automated tests — all passing

```bash
python3 -m pytest            # 226 passed
node --test extension/tests/content.test.mjs   # 12 passed (also run inside pytest)
```

| Test file | Tests | Covers |
|---|---:|---|
| `tests/test_mpris.py` | 24 | MPRIS metadata parsing (jeepney variants, artist lists, µs→ms, `file://` art, `NoTrack`), player enumeration over a fake D-Bus connection, paused/dedup handling, per-player failures, bus failure |
| `tests/test_engine.py` | 26 | first publish, duplicate suppression, track change, refresh, seek, pause clear, `clear_on_pause=false`, 9-second grace, player close, backend errors, extension overlay, extension-only mode, stale extension, artwork retry/upgrade, Discord backoff, reconnect, clear failure, shutdown |
| `tests/test_bridge.py` | 23 | extension packet validation (13 cases), idle/ignored/noop/error responses, duplicate and unknown packets, real Unix-socket round trip, 0600 permissions, silent client |
| `tests/test_native_host.py` | 12 | 4-byte length framing (incl. 1-byte reads, unicode, oversized), version handshake, forwarding, daemon auto-start, origin allow/deny, EOF |
| `tests/test_artwork.py` | 21 | every artwork tier, dead candidates, `cache-unverified` fallback, relay never cached, memoisation, video-id persistence, store persistence/pruning/corruption, local file reading, magic-byte checks |
| `tests/test_installer.py` | 11 | launchers (incl. `PYTHONPATH` for vendored deps of a package install), manifests per browser (Chromium + Firefox formats), skip-if-absent, `--all-browsers`, extra extension ids, uninstall + purge, autostart enable/disable, AppImage-safe restart command |
| `tests/test_extension.py` | 8 | extension id derived from the manifest key equals `DEV_EXTENSION_ID`, MV3 fields, content-script scope, host name in `background.js`, source/build copies identical, icons, Node tests |
| `tests/test_config.py` | 20 | defaults, corrupt JSON, clamping, string booleans, list parsing, invalid app id/backend/log level, HTTPS-only relay URL, atomic save, priority helpers |
| `tests/test_cli.py` | 18 | version, config path/show/get/set/reset, invalid key/value, status, doctor (human + JSON), dump, install/uninstall, legacy `--dump`/`--native-host`, settings without display, second instance |
| `tests/test_discord.py` | 10 | activity → pypresence kwargs, truncation, connect/update error wrapping, IPC socket discovery, stale socket rejection |
| `tests/test_selection.py` | 10 | fooyin vs browser, playing beats paused, configurable order, ignored players, unknown players, stable ordering |
| `tests/test_merge.py` | 7 | MPRIS + extension merging rules, mismatched tracks, start-epoch fallback |
| `tests/test_textutil.py` | 25 | title/artist normalisation, decorations, video ids, timestamps (shared with Windows) |
| `tests/test_control_and_lock.py` | 6 | control socket dispatch/errors/permissions, `flock` single instance across processes |
| `tests/test_integration.py` | 6 | real processes: generated launcher scripts, installed native host answering the version handshake, native host → bridge forwarding, origin rejection, **daemon started like the autostart entry** answering `status` with the forwarded track, second launch exiting while the first keeps running |

Also verified while building:

* `music-rpc doctor` runs without a session bus and reports each subsystem
  instead of crashing.
* `music-rpc dump --json` writes `~/.local/state/music-rpc/diagnose.json`.
* The packaged `.deb` tree is importable and executable:
  `PYTHONPATH=…/usr/lib/music-rpc:…/vendor python3 -m musicrpc version|doctor`
  (checked by extracting the `.deb` with `dpkg-deb -x`).

## 2. Artifacts built in this environment

| Artifact | Built | Verified |
|---|---|---|
| `dist/music-rpc_3.0.0_all.deb` | ✅ `./linux/build-deb.sh` | ✅ `dpkg-deb --info/-c`, extracted tree runs `version`, `doctor`, `dump` |
| `dist/music-rpc-3.0.0-source.tar.gz` | ✅ | ✅ used for `rpmbuild -tb`/review |
| `dist/MusicRPC-3.0.0-x86_64.AppImage` | ❌ | Blocked: the AppImage type-2 runtime and `appimagetool` are only distributed as GitHub *release assets*, which this sandbox cannot download (`release-assets.githubusercontent.com` is unreachable). `linux/build-appimage.sh` is complete and takes over on a normal machine. |
| RPM | ❌ | Blocked: `rpmbuild` is not installed and cannot be installed without root. Spec reviewed; `linux/build-rpm.sh` builds it where `rpm-build` exists. |

## 3. Not verified here — needs a real desktop

These were **not** executed in the development environment. Please treat them as
"works as designed, unverified by automation":

| # | Check | Why not automated |
|---|---|---|
| 1 | Discord desktop shows the activity | no Discord client, no IPC socket |
| 2 | Fooyin appears and updates on track change | no Fooyin, no D-Bus session bus |
| 3 | Pause / resume / seek / stop from a real player | same |
| 4 | Chromium/Brave/Thorium loads the extension and connects | no browser |
| 5 | Native messaging host really launched by a browser | browser-only behaviour |
| 6 | Qt tray icon on KDE Plasma (Wayland + X11) | no display; `libGL.so.1` is also absent in the sandbox, so `PyQt6` cannot even be imported here |
| 7 | Qt tray icon on Cinnamon | same |
| 8 | Settings window save → automatic restart | needs Qt + display |
| 9 | MPRIS artwork from a real player's `mpris:artUrl` | needs a player |
| 10 | Autostart entry honoured by the desktop session | needs a login session |
| 11 | AppImage build and run | see section 2 |
| 12 | RPM install on Nobara | see section 2 |
| 13 | Firefox extension | unsigned, temporary load only |

### Manual checklist for your machine

```bash
music-rpc doctor                     # every line should be "ok" or a known WARN
music-rpc dump                       # while fooyin plays -> title/artist/tier
music-rpc status                     # while YouTube Music plays -> kind youtube
```

1. Play a song in fooyin → Discord shows it within ~2 s.
2. Pause → the card disappears. Resume → it comes back.
3. Seek → the progress bar jumps once and stays correct.
4. Close fooyin → the card disappears within ~9 s.
5. Play YouTube Music in Chromium with the extension → `music-rpc status`
   shows `"kind": "youtube"` and Discord shows the YouTube thumbnail.
6. Load a regular YouTube video → no presence change.
7. Install a track with no catalogue match → presence still appears (without a
   cover), and the log shows `tier=none`.
8. Right-click the tray icon → Open / Settings / Restart / Quit all work.
9. Log out and back in → the app starts by itself (check `music-rpc status`).
10. `music-rpc disable` → stops and does not come back after login.
