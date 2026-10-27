"""Merging platform metadata with the browser extension's data.

Mirrors ``merge_tracks()`` from the Windows build: MPRIS owns playback state and
position, the extension contributes the exact YouTube video id, artwork and
album, which MPRIS cannot provide.
"""
from __future__ import annotations

from .models import Track
from .textutil import artists_match, titles_match


def merge_tracks(platform_track: Track | None, extension_track: Track | None) -> Track | None:
    """Combine live player state with the extension's artwork data."""
    if extension_track is None:
        return platform_track
    if platform_track is None:
        return extension_track
    if not (titles_match(extension_track.title, platform_track.title)
            and artists_match(extension_track.artist, platform_track.artist)):
        if platform_track.playing or not extension_track.playing:
            return platform_track
        return extension_track

    kind = "youtube" if "youtube" in (platform_track.kind, extension_track.kind) else "local"
    duration = platform_track.duration_ms or extension_track.duration_ms
    position = platform_track.position_ms
    start_epoch = None
    if position is None or not duration:
        start_epoch = extension_track.start_epoch

    return Track(
        title=extension_track.title or platform_track.title,
        artist=platform_track.artist or extension_track.artist,
        album=extension_track.album or platform_track.album,
        playing=platform_track.playing,
        source=platform_track.source or extension_track.source,
        duration_ms=duration,
        position_ms=position,
        image_url=extension_track.image_url or platform_track.image_url,
        video_id=extension_track.video_id or platform_track.video_id,
        start_epoch=start_epoch,
        kind=kind,
        player=platform_track.player or extension_track.player,
        track_id=platform_track.track_id or extension_track.track_id,
        local_art=platform_track.local_art or extension_track.local_art,
    )
