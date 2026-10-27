# Architecture: how the Windows app became a Linux app

The original application is a single 1785-line file (`app.py`) that talks to
WinRT System Media Transport Controls and Discord. Rather than porting it line by
line behind `if os.name == "nt"`, the media *source* was isolated behind a
backend interface, and everything that made the presence look right (artwork
tiers, cache format, text normalisation, payload formatting, presence rules) was
moved into platform-neutral modules.

```
                         ┌──────────────────────────┐
   Fooyin / Chromium ───▶│ backends/mpris.py        │──┐
   (D-Bus MPRIS)         │  parse_metadata()        │  │
                         └──────────────────────────┘  │
                         ┌──────────────────────────┐  │   ┌────────────────┐
   Windows SMTC ────────▶│ backends/windows_smtc.py │──┼──▶│  engine.py     │
   (WinRT, unchanged     │  get_track() port        │  │   │  tick()        │
    logic from app.py)   └──────────────────────────┘  │   └───────┬────────┘
                         ┌──────────────────────────┐  │           │
   Browser extension ──▶ │ bridge.py + native_host  │──┘           │
   (native messaging)    │  parse_extension_packet  │              │
                         └──────────────────────────┘              │
                                                                   ▼
   ┌────────────────────┐   ┌───────────────┐   ┌──────────────┐  ┌──────────────┐
   │ artwork.py         │◀──│ engine asks   │──▶│ discord.py   │  │ config.py    │
   │ tiers + cache      │   │ for a cover   │   │ pypresence   │  │ XDG + JSON   │
   └────────────────────┘   └───────────────┘   └──────────────┘  └──────────────┘
```

## Why a backend interface

`app.py` mixed three responsibilities: reading Windows media sessions, resolving
artwork, and speaking to Discord. On Linux only the first one has to change.
`MediaBackend` is a two-method contract:

```python
class MediaBackend(Protocol):
    name: str
    wake: threading.Event          # set by the backend when something changed
    def poll(self) -> Snapshot: ...  # tuple[Track, ...] + optional error
    def close(self) -> None: ...
```

`Snapshot` carries one `Track` per player *plus* an optional `error`, so a broken
D-Bus session is reported without looking like "nothing is playing".

## The MPRIS backend

Two threads, deliberately:

* the engine thread calls `poll()`, which performs a blocking `GetAll` on
  `org.freedesktop.DBus.Properties` for every `org.mpris.MediaPlayer2.*` name;
* a watcher thread holds its own D-Bus connection, subscribes to
  `PropertiesChanged` (on `/org/mpris/MediaPlayer2`), `Seeked` and
  `NameOwnerChanged`, and sets `backend.wake` so the engine can react instantly
  instead of waiting up to `poll_seconds`.

Consequences that matter in practice:

* play/pause/seek/track change are reflected immediately (signal) *and* at worst
  one poll later;
* a player restart or a D-Bus daemon restart shows up as `NameOwnerChanged`; the
  poll then reopens the connection lazily and the engine's grace period prevents
  a flicker;
* a single broken player only produces a per-poll warning — the other players
  keep working.

Field mapping:

| MPRIS | Track field |
|---|---|
| `xesam:title` | `title` (normalised, duplicated artist stripped) |
| `xesam:artist` / `xesam:albumArtist` | `artist` (list joined with `, `) |
| `xesam:album` | `album` |
| `mpris:length` (µs) | `duration_ms` |
| `Position` (µs) | `position_ms` |
| `PlaybackStatus` | `playing` |
| `mpris:artUrl` | `image_url` (http/https) or `local_art` (file://) |
| `mpris:trackid` | `track_id` (dedupe; `…/NoTrack` is dropped) |
| `Identity` / `DesktopEntry` | `player` (used for prioritisation) |

`:1.42`-style unique names and `%20` escapes are handled; variants arrive from
`jeepney` as `(signature, value)` tuples and are flattened by
`unwrap_variant()`.

## Player prioritisation

`selection.rank_tracks()` sorts by:

1. playing before paused,
2. `config.player_priority` index (substring match on identity/desktop-entry/bus
   name, punctuation-insensitive),
3. player name, for a stable order.

`config.ignored_players` removes players entirely. The rules are pure functions
and are unit tested with fooyin/chromium/mpv/VLC combinations.

## The presence state machine

`engine.tick()` is the whole app in one function; it never sleeps, which is what
makes it testable with a fake clock:

| Situation | Action |
|---|---|
| new track | resolve artwork, `SET_ACTIVITY` |
| same track, 40 s later | re-send (keeps the progress bar from drifting) |
| reported position jumped > 1.5 s | re-send (seek) |
| duplicate polls | nothing (no activity churn) |
| paused | `clear` (configurable) |
| no player | wait `no_track_grace_seconds`, then `clear` once |
| Discord update/connect fails | drop the client, exponential backoff 8 → 60 s, re-send after reconnect |
| artwork `none`/`relay`/`cache-unverified` | retry once per `art_retry_seconds`, prefer a permanent URL |
| resolver raises | log, continue without artwork |

## Artwork

`artwork.py` is the Windows pipeline ported verbatim in behaviour, with the HTTP
layer injected so tests never touch the network:

1. `youtube` – `i.ytimg.com/vi/<id>/maxresdefault.jpg` (then mq/hq),
2. `cache` – a previously proven URL for this track,
3. `player` – the URL the player/extension supplied,
4. `album-cache` – a proven URL for this album,
5. `catalog` – iTunes Search → Deezer,
6. `relay` – session-only URL for embedded art (opt-in),
7. `search` – a video id derived from title + artist.

Every candidate is verified with a real HTTP GET (magic bytes, content type,
≥ 120 × 120) because Discord caches failed image fetches. The on-disk cache
(`~/.cache/music-rpc/artwork-cache.json`) keeps the Windows file name and JSON
schema (`version: 2`, `tracks`, `albums`), so a cache can be copied between
machines.

## Extension protocol

Unchanged from Windows:

```
browser → native host (stdio, 4-byte LE length + UTF-8 JSON)
native host → daemon (unix socket, one JSON line each way)
```

The daemon's answer is returned to the browser with the same strings the Windows
build used (`0_SUCCESS: PRESENCE_UPDATED`, `1_CLIENT_ERROR: …`). Two optional
fields were *added* for Linux (`jsPlaybackState`, `jsPositionSec`/
`jsDurationSec`); Windows ignores them, and Linux uses them when the browser
exposes no MPRIS player at all.

Differences from Windows, on purpose:

* the socket is a Unix socket in `$XDG_RUNTIME_DIR/music-rpc/bridge.sock`
  (mode 0600) instead of a loopback TCP port — nothing listens on the network;
* the native host is a per-user wrapper script registered through JSON manifests
  in the documented browser directories, not registry keys;
* the host validates the calling origin against `extension_origins` before
  forwarding, and starts the daemon on demand if it is not running.

## Configuration and lifecycle

* `config.py` validates every value (clamps timings, rejects non-numeric Discord
  ids, refuses non-HTTPS relay URLs) and never fails to start: a corrupt file
  falls back to defaults and is reported by `doctor`.
* `single_instance.py` uses `flock`, which the kernel releases on crash.
* `control.py` is a second Unix socket with `status`, `settings`, `restart`,
  `quit`, used by a second launch and by the tray.
* `tray.py` runs Qt on the main thread and only *reads* the engine's state, so a
  Qt crash cannot stop the presence loop.
* `installer.py` writes launchers, native messaging manifests, the menu entry and
  the freedesktop autostart entry — all per user, no root.
