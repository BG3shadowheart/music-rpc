# What was wrong, and what changed

## Version 2.0 — new name, new icon, real installer

- **Icon**: the supplied artwork is now the EXE icon (`assets/spotify.ico`,
  generated from `assets/spotify-logo.png` at 16–256 px) and the installer icon.
- **Name**: the product is called **Spotify**. The executable is `Spotify.exe`,
  installed to `%LOCALAPPDATA%\Programs\Spotify`, with a `Spotify` Start Menu
  entry, Run key and Startup shortcut.
  The Discord-visible name/icon still comes from the Developer Portal for app ID
  `1561450956242161745`; see `README.md`.
- **Installer**: `Spotify-Setup.exe` (Inno Setup, per-user, no UAC) creates the
  uninstaller entry in Settings → Apps, places the files, then calls
  `Spotify.exe --install` to register sign-in startup, the Startup shortcut and
  the browser native-messaging host, and to start the monitor.
- **Safe process control**: nothing ever runs `taskkill /IM Spotify.exe` — that
  would kill the real Spotify client. Processes are matched by executable path
  and the current process is excluded.
- **Migration**: installing adopts the artwork cache from a previous `YumeTune`
  install, removes its folder, Run key, Startup shortcut and stale browser
  registrations, so only one copy remains.

## Version 1.9 — the artwork bug

## The symptom

The Discord thumbnail appeared sometimes and was missing other times — visible
yesterday, gone today, back again later — while the song title and artist were
always correct.

## The root causes

1. **The Chrome extension was never connected.** No Chrome Native Messaging host
   was registered on this PC (`com.ytdp.discord.presence` was absent from
   `HKCU\Software\Google\Chrome\NativeMessagingHosts`), so YumeTune never
   received the extension's data — even though the extension was installed in
   Thorium. The `YumeTune.exe.lnk` startup shortcut pointed at the old copy in
   `C:\Program Files (x86)\Qobuz`, and that build never registered itself.

2. **Every thumbnail depended on an ephemeral Cloudflare Quick Tunnel.** With no
   extension data, the only artwork source was the cover embedded in the Windows
   SMTC session. That cover was resized and served through
   `https://<random>.trycloudflare.com`. A Quick Tunnel hostname is created fresh
   on every launch and its first request pays a cold-start penalty. Discord's
   image crawler fetches each URL once and **caches failures**, so when the first
   fetch lost that race the activity kept a blank image until Discord retried
   later — which is exactly why the picture "came back" on its own.

3. **Even with the extension working, the app threw its permanent URL away.**
   The extension supplies `https://i.ytimg.com/vi/<videoId>/...`, but the bridge
   only trusted that data for **8 seconds**. After that it fell back to the SMTC
   session, which has no video id, so the app overwrote the good `i.ytimg.com`
   URL with the flaky tunnel URL a few seconds into every song.

4. **Extension timestamps were read as seconds.** The extension sends
   `Date.now()` milliseconds; the old code treated them as seconds, which would
   have produced a nonsense progress bar the moment the extension did connect.

## The fix

* **Native messaging is now registered on every launch** — for Chrome, Chromium,
  Thorium, Brave, Edge and Vivaldi — so the extension's exact video id reaches
  the app. A one-time `install.bat` also sets it up explicitly.

* **The extension's data is sticky, not 8-second.** It is kept for the session
  and merged with Windows' live playback position, so artwork survives the whole
  song instead of being replaced after 8 seconds.

* **Artwork is resolved permanent-URL first and every candidate is verified**
  with a real HTTP GET (image magic bytes, content type, and at least 120×120)
  before it is sent to Discord:
  `sticky cache` → `i.ytimg.com/vi/<id>/maxresdefault.jpg` → Apple/Deezer CDN →
  embedded art via relay → YouTube search.

* **Permanent URLs are cached on disk** (`artwork-cache.json`), keyed by
  artist/title and artist/album, so the second play of a song is instant and
  never re-depends on the network.

* **The relay is demoted to last resort**, its Cloudflare edge connection is
  warmed as soon as it comes up, and the image is warmed again before the URL is
  handed to Discord.

* **A 45-second safety retry** replaces a session-only relay URL with a permanent
  CDN URL if one can be found, so a song cannot stay cover-less.

* Album names that used to be dropped are now filled in from the catalog and show
  on the cover tooltip.

## Files

- `app.py` — rewritten artwork pipeline (single file, no new dependencies).
- `YumeTune.exe` — rebuilt standalone binary.
- `README.md` — documents the artwork tiers.
- `uninstall.bat` — now also removes every browser's host registration and the
  artwork cache.
