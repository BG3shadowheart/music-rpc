# Music RPC – YouTube Music extension

Sends the currently playing YouTube Music track (title, artist, album, exact
video id, artwork, position, duration and play state) to the local Music RPC
companion, which then publishes it to Discord.

This extension is **not published in any store**. You load it unpacked; it has no
store id, and none is invented anywhere in this repository.

## Load it in Chrome / Chromium / Brave / Thorium

1. Install the companion and register the native messaging host first:

   ```bash
   music-rpc install
   ```

   That command prints the folder to use in step 4 and writes
   `com.ytdp.discord.presence.json` into every browser's native messaging
   directory.

2. Open `chrome://extensions` (`brave://extensions`, `thorium://extensions`).
3. Enable **Developer mode**.
4. **Load unpacked** → select the `chromium` folder
   (for example `/usr/share/music-rpc/extension/chromium`).
5. Restart the browser (native messaging manifests are read at startup).
6. Open <https://music.youtube.com> and play a song.

## Permissions

| Permission | Why |
|---|---|
| `nativeMessaging` | Talk to the local companion. Nothing is sent anywhere else. |
| `https://music.youtube.com/*` | Read the player bar, `<video>` element and Media Session metadata. |

No `tabs`, no `storage`, no `<all_urls>`, no analytics, no remote code. The only
network request the extension makes is the browser's own image loading.

## Ordinary YouTube videos

The content script only matches `music.youtube.com`, so `youtube.com/watch`
never reports anything. This is deliberate: the Windows app also ignored regular
YouTube playback.

## Firefox

`firefox/` contains the same code with a Manifest V3 Firefox manifest
(`browser_specific_settings.gecko.id = youtube-music-presence@music-rpc.local`,
which is the id allowed by the Firefox native messaging manifest).

**Untested.** Load it via `about:debugging` → *This Firefox* → *Load Temporary
Add-on* → pick `firefox/manifest.json`. It disappears when Firefox restarts
because it is unsigned. Use the Chromium build where possible.

## Files

```
src/content.js          shared source: player reading + packet building
src/background.js       shared source: native messaging port + reconnect
chromium/               ready-to-load Chromium build (manifest + copies + icons)
firefox/                ready-to-load Firefox build
tests/content.test.mjs  Node unit tests for the pure helpers
build.sh                copies src/ into both builds (run after editing src/)
```

`tests/test_extension.py` in the repository root fails if the copies drift away
from `src/`, so run `./build.sh` after editing.

## How the data flows

```
music.youtube.com  ──chrome.runtime.sendMessage──▶  background.js
                                                     │
                              chrome.runtime.connectNative("com.ytdp.discord.presence")
                                                     │
                                   4-byte length + JSON frames on stdio
                                                     ▼
                            music-rpc-native-host  ──▶  Unix socket  ──▶  daemon
```

If the daemon is not running, the native host starts it. If the host is missing,
the background script retries with backoff (1 s → 30 s) and keeps the last packet
so a reconnect does not lose the current track.
