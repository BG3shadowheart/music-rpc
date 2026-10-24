# Spotify — music presence for Discord

A lightweight Windows background app that publishes YouTube Music and foobar2000
to Discord Rich Presence. No window, no tray icon, no console.

Installed name: **Spotify**. Install location:
`%LOCALAPPDATA%\Programs\Spotify`. Runs as you (no administrator rights).

> **About the name and icon in Discord.** The application name and icon shown on
> the Discord activity card are **not** stored in this program — Discord reads
> them from the application registered for ID `1561450956242161745` in the
> Discord Developer Portal. Renaming the EXE changes only your own files,
> shortcuts and Windows entries. To make the card itself read "Spotify" with your
> avatar, open <https://discord.com/developers/applications> → your application →
> **General Information** → change **Name** and **App Icon**, then restart
> Discord. (Only the app owner can do this, and Discord's naming/branding rules
> apply to that change.)

## Install

1. Run **`Spotify-Setup.exe`**.
2. It installs to `%LOCALAPPDATA%\Programs\Spotify`, creates the Start Menu
   shortcut, the sign-in **startup shortcut**, registers the browser
   native-messaging host, and starts the monitor.

The installer does not need administrator rights, and the install folder is a
durable per-user location (the same place Discord and VS Code use), so it is not
touched by temporary-folder cleanup.

Prefer the portable route? Run `install.bat` next to `Spotify.exe`; it performs
the same registration and copies itself to the install folder.

Remove it through **Settings → Apps → Spotify**, or run `uninstall.bat`, which
also deletes startup entries, browser host registrations and the artwork cache.

## What it supports

- **YouTube Music** through Windows System Media Transport Controls (Chrome,
  Thorium, other Chromium browsers) *and* through the YouTubeDiscordPresence
  extension (store ID `hnmeidgkfcbpjjjpmjmpehjdljlaeaaa`) via Chrome Native
  Messaging. The extension supplies the exact video id, which yields the
  highest-resolution permanent cover.
- **foobar2000** through System Media Transport Controls (install the x64
  `foo_mediacontrol` component).
- Title and artist on separate rows; album name on the cover tooltip.
- Presence clears on pause, or after a nine-second gap at the end of a track.
  Regular YouTube video playback is ignored.
- Automatic start after Windows sign-in, with a single-instance guard.

## How cover art stays visible

Discord does not accept image data — it accepts a **URL**, fetches it once, and
caches the result including failures. An artwork source that only exists for one
session, or that is slow on its first byte, therefore shows up only sometimes.

Artwork is resolved **permanent-URL first**, and every candidate is verified with
a real HTTP request before it is sent:

| # | Tier | Source | Survives restart |
|---|------|--------|------------------|
| 1 | `youtube` | `https://i.ytimg.com/vi/<id>/maxresdefault.jpg` | yes |
| 2 | `cache` | a cover URL already proven for this track/album | yes |
| 3 | `catalog` | Apple Music / Deezer album art on their CDNs | yes |
| 4 | `relay` | embedded art via a temporary Cloudflare Quick Tunnel | no |
| 5 | `search` | video id derived from title + artist | yes |

Permanent URLs are stored in `%LOCALAPPDATA%\SpotifyPresence\artwork-cache.json`,
so a repeat play needs no network lookup. Dead candidates are skipped, and if the
only cover came from the session-only relay, the app retries once per minute for
a permanent CDN equivalent.

## Build from source

Run `build.bat` on Windows. It creates a project-local virtual environment,
installs the pinned dependencies, downloads the Cloudflare Tunnel helper, and
compiles `Spotify.exe` with Nuitka (icon and version info included).

To rebuild the installer you also need Inno Setup 6; then:

```
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\Spotify.iss
```

which produces `dist-installer\Spotify-Setup.exe`.

## Diagnostics

`Spotify.exe --dump` writes the current track, the chosen artwork tier and the
resulting URL to `%LOCALAPPDATA%\SpotifyPresence\diagnose.json`. The running log
is `%LOCALAPPDATA%\SpotifyPresence\presence.log`.

## Privacy

Media detection stays on-device. When a song starts, its title, artist and album
may be sent to Apple's iTunes Search API, Deezer's public API, or a YouTube
search page purely to find a cover URL. Embedded art from local files is never
uploaded; it is served briefly from memory over a tunnel that closes with the
app. No credentials are read and no listening history is stored beyond the local
cover-URL cache.

## Requirements

- Windows 10/11 x64 and the Discord desktop app.
- The YouTubeDiscordPresence extension for exact YouTube Music artwork.
- The `foo_mediacontrol` x64 component for foobar2000.
