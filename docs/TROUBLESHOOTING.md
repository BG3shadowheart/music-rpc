# Troubleshooting

Everything below assumes you run the commands **as your own user** — never with
`sudo`. A native messaging host, the Discord socket and the D-Bus session bus
belong to your login session.

Start here:

```bash
music-rpc doctor
```

and if you want to see the machinery:

```bash
music-rpc config set log_level DEBUG
music-rpc disable && music-rpc enable
tail -f ~/.local/state/music-rpc/presence.log
```

---

## 1. Discord IPC

### Symptoms
* `music-rpc status` says `"discord": "disconnected"` or `"waiting"`.
* The log repeats `Discord RPC connection failed`.

### Checks

```bash
# Is the desktop client running?
pgrep -a -f -i discord

# Does its socket exist?
ls -l "${XDG_RUNTIME_DIR:-/run/user/$UID}"/discord-ipc-*
```

Expected: at least one `discord-ipc-0` socket. Music RPC also looks in
`$XDG_RUNTIME_DIR/snap.discord`, `$XDG_RUNTIME_DIR/app/com.discordapp.Discord`
and `$XDG_RUNTIME_DIR/app/com.discordapp.DiscordCanary`, plus `/tmp`.

### Common causes

| Cause | Fix |
|---|---|
| Discord is only open in the browser | Rich Presence needs the desktop client. |
| Discord status is "Invisible" | Change it — invisible users never show activities. |
| Discord Flatpak | The socket lives inside the sandbox. Either install the official `.deb`/`.rpm`, or grant the flatpak access to `$XDG_RUNTIME_DIR/app/com.discordapp.Discord`. |
| Discord started *after* Music RPC | It reconnects automatically (8 s, backing off to 60 s). Force it: `music-rpc disable && music-rpc enable`. |
| `XDG_RUNTIME_DIR` missing (ssh, tmux) | Start Music RPC from your graphical session (autostart does this for you). |
| Presence shows for a second and disappears | Another application is clearing the same activity, or Discord's "activity privacy" setting is off. |

Music RPC never needs a Discord token: it uses the local RPC socket only, and the
client must be logged in.

---

## 2. D-Bus / MPRIS

### Checks

```bash
echo "$DBUS_SESSION_BUS_ADDRESS"          # usually unix:path=/run/user/1000/bus
busctl --user list | grep -i mpris        # every MPRIS-enabled player
playerctl -l                              # if playerctl is installed
music-rpc dump                            # what Music RPC actually sees
```

### Common causes

| Cause | Fix |
|---|---|
| Fooyin not listed | Fooyin exports MPRIS itself; if it is missing, check the player build/version, and that you started it in the same session. |
| Browser not listed | Chromium browsers only export MPRIS while a tab is actually playing audio. The extension bridge covers browsers that never show up. |
| `cannot connect to the D-Bus session bus` | Music RPC was started outside the session (cron, ssh, a systemd unit without `--user`). Start it from the autostart entry or with `music-rpc run --background` in a terminal of your session. |
| Player shows as paused only | Some players report `Paused` when they are actually stopped. Set `clear_on_pause false` if you want the card to stay. |
| Two players fight each other | Order `player_priority`; a *playing* player always beats a paused one. |

The backend is hardened against trouble: one broken player is skipped, a D-Bus
daemon restart is retried, and a vanished player is cleared after the 9-second
grace period rather than leaving a stale track on your profile.

---

## 3. Browser extension / native messaging

### Symptoms
* The extension's icon is grey, or its popup says it cannot connect.
* Chrome logs `Specified native messaging host not found`.
* `music-rpc status` never shows `"kind": "youtube"` while YouTube Music plays.

### Checks

```bash
music-rpc install --all-browsers     # (re)write every manifest
music-rpc doctor                     # lists manifests and whether the host exists
cat ~/.config/google-chrome/NativeMessagingHosts/com.ytdp.discord.presence.json
```

The manifest must contain:

```json
{
  "name": "com.ytdp.discord.presence",
  "path": "/home/you/.local/bin/music-rpc-native-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://bdgahehmdkmmmfbjefaoongpfkkpfnpg/"]
}
```

* `path` must exist **and be executable** (`ls -l`).
* The extension id must match the one shown on `chrome://extensions`. It is
  derived from the `key` in `extension/chromium/manifest.json`; if you edited or
  removed that key, the id changes — add it with
  `music-rpc install --extension-id <id>`.
* **Restart the browser** after writing a manifest. Chromium reads native
  messaging manifests only at startup.

### Manual test of the host (no browser involved)

```bash
printf '\x0c\x00\x00\x00{"getNativeVersion":true}' | music-rpc-native-host
```

A correct host answers with a length-prefixed JSON frame containing
`{"nativeVersion": "2.0.0"}`.

### Snap / Flatpak browsers

`music-rpc install` writes manifests into the Snap and Flatpak config paths when
it finds them, but confinement can still block the host binary. If the extension
cannot connect:

* use a distribution-packaged browser (`.deb`/`.rpm`), or
* grant the sandbox access to `~/.local/bin` and the socket directory, or
* fall back to MPRIS-only presence (the extension is only needed for exact
  YouTube artwork and video ids).

---

## 4. Missing artwork

`music-rpc dump` prints the tier that was chosen:

| Tier | Meaning |
|---|---|
| `youtube` | Thumbnail of the exact video (best case, permanent) |
| `cache` | A URL that already worked for this track |
| `player` | The URL the player/extension reported |
| `album-cache` | A URL that already worked for this album |
| `catalog` | Apple Music / Deezer lookup |
| `relay` | Session-only local relay (opt-in) |
| `search` | Video id found by searching title + artist |
| `cache-unverified` | The CDN was unreachable; the last known URL was sent anyway |
| `none` | Nothing usable was found |
| `disabled` | Artwork is turned off in the settings |

Notes:

* Discord caches image **failures** for a while; if a cover was broken once it
  can stay blank even after you fix it. Change the track or wait.
* `none` usually means the network was down or the album genuinely is not in any
  catalogue. Try `music-rpc config set artwork_youtube_search true`.
* The relay is off by default. Enable it only if you accept that the current
  cover becomes publicly fetchable (section 7 of the main README).

---

## 5. Tray icon, settings window, autostart

```bash
music-rpc doctor | grep -i tray
python3 -c "import PyQt6" || echo "PyQt6 missing"
```

* **No tray on Wayland**: KDE Plasma and Cinnamon provide a StatusNotifier host;
  minimal compositors (sway, Hyprland without a bar) do not. The app runs fine
  headless — use `music-rpc status` / `settings` from a terminal.
* **No tray on GNOME**: GNOME hides legacy tray icons; install the
  *AppIndicator* extension if you want the icon.
* **Autostart ignored**: check `~/.config/autostart/music-rpc.desktop` exists and
  has `Hidden=false`. KDE: *System Settings → Autostart*. Cinnamon: *Startup
  Applications*. `music-rpc disable` sets `Hidden=true` instead of deleting the
  file, so `music-rpc enable` is instant.

---

## 6. High CPU, reconnects, logs

* Normal usage is close to 0 % CPU: one 1.5 s MPRIS poll plus signals.
* A log full of `Discord RPC connection failed` every 8–60 s means Discord is
  not reachable — not a busy loop.
* A log full of `MPRIS player read problem` means a specific player is broken
  (usually a browser that is shutting down); it is harmless.
* `music-rpc doctor --json` plus `~/.local/state/music-rpc/presence.log` contain
  everything needed for a useful bug report.

## 7. Starting from scratch

```bash
music-rpc disable
rm -rf ~/.cache/music-rpc            # artwork cache (safe)
music-rpc config reset               # default settings
music-rpc install --all-browsers     # rewrite every manifest
music-rpc enable
music-rpc doctor
```

Full removal:

```bash
./linux/uninstall.sh --purge         # source install
sudo apt remove music-rpc            # deb
music-rpc uninstall --purge          # any install
```
