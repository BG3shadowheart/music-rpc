"""Windows System Media Transport Controls backend (WinRT).

This is a port of ``get_track()`` from the original ``app.py`` into the new
backend interface. It is *not* used on Linux and imports WinRT lazily, so the
module is safe to import anywhere.

The shipped Windows application is still ``app.py`` - this backend exists so the
shared engine, configuration, artwork cache and Discord layer can run on Windows
too without duplicating the logic a third time.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from ..models import Snapshot, Track
from ..textutil import clean, normalize_artist, normalize_title

log = logging.getLogger(__name__)

YOUTUBE_MUSIC_AUMID_SUFFIX = "_crx_cinhimbnkkghhklpknlkffjgod"
YOUTUBE_MUSIC_AUMID_MARK = "cinhimbnkkghhklpknlkffjgod"


def is_youtube_music_source(source: str) -> bool:
    lowered = (source or "").casefold()
    return lowered.endswith(YOUTUBE_MUSIC_AUMID_SUFFIX) or YOUTUBE_MUSIC_AUMID_MARK in lowered


class WindowsSmtcBackend:
    name = "windows"

    def __init__(self, config=None):
        self.config = config
        self.wake = threading.Event()
        self._loop = None

    def _run(self, coroutine):
        try:
            return asyncio.run(coroutine)
        except RuntimeError:
            # Called from a thread that already has a loop.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coroutine)
            finally:
                loop.close()

    def poll(self) -> Snapshot:
        taken = time.monotonic()
        try:
            players = self._run(self._poll_async())
        except Exception as exc:
            return Snapshot(players=(), taken_at=taken, error=str(exc))
        return Snapshot(players=tuple(players), taken_at=taken)

    async def _poll_async(self):  # pragma: no cover - Windows only
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        manager = await MediaManager.request_async()
        players: list[Track] = []
        for session in manager.get_sessions():
            try:
                source = clean(session.source_app_user_model_id)
                source_lower = source.casefold()
                is_youtube_music = is_youtube_music_source(source)
                is_foobar2000 = source_lower.startswith("foobar2000")
                if not (is_youtube_music or is_foobar2000):
                    continue
                info = session.get_playback_info()
                props = await session.try_get_media_properties_async()
                artist = clean(props.artist or props.album_artist)
                title = normalize_title(props.title, artist)
                artist = normalize_artist(artist, title)
                if not title or title.casefold() == "unknown":
                    continue
                timeline = session.get_timeline_properties()
                duration = None
                position = None
                if timeline is not None:
                    if timeline.end_time is not None:
                        duration = int(timeline.end_time.total_seconds() * 1000)
                    if timeline.position is not None:
                        position = int(timeline.position.total_seconds() * 1000)
                local_art = None
                # Embedded artwork is only exposed as a WinRT stream; the shared
                # engine asks for a path, so the thumbnail is written to a
                # temporary file when it is needed for the optional relay.
                try:
                    if props.thumbnail is not None and self._relay_enabled():
                        local_art = await self._write_thumbnail(props.thumbnail)
                except Exception:
                    local_art = None
                players.append(Track(
                    title=title,
                    artist=artist,
                    album=clean(props.album_title),
                    playing=info.playback_status == 4,
                    source="YouTube Music" if is_youtube_music else source,
                    duration_ms=duration,
                    position_ms=position,
                    kind="youtube" if is_youtube_music else "local",
                    player="YouTube Music" if is_youtube_music else source,
                    local_art=local_art,
                ))
            except Exception:
                continue
        return players

    def _relay_enabled(self) -> bool:
        return bool(getattr(self.config, "artwork_relay_enabled", False))

    async def _write_thumbnail(self, thumbnail):  # pragma: no cover - Windows only
        import tempfile
        from pathlib import Path

        from winrt.windows.storage.streams import DataReader

        stream = await thumbnail.open_read_async()
        reader = DataReader(stream.get_input_stream_at(0))
        size = await reader.load_async(stream.size)
        buffer = bytearray(size)
        reader.read_bytes(buffer)
        path = Path(tempfile.gettempdir()) / "music-rpc-cover.jpg"
        path.write_bytes(bytes(buffer))
        return str(path)

    def close(self):
        pass
