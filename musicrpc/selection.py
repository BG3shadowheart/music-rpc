"""Player prioritisation.

Kept pure and platform independent so both the MPRIS and the Windows backend
share exactly the same rules, and so the rules can be tested without a desktop
session.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from .models import Track
from .textutil import slug


def player_names(track: Track) -> tuple[str, str, str]:
    return (track.player or "", track.source or "", track.track_id or "")


def rank_of(track: Track, priority: Sequence[str]) -> int:
    """Lower is better. Unknown players rank after every configured one."""
    haystack = " ".join(slug(name) for name in player_names(track) if name)
    for index, entry in enumerate(priority):
        needle = slug(entry)
        if needle and needle in haystack:
            return index
    return len(priority) + 1


def is_ignored(track: Track, ignored: Sequence[str]) -> bool:
    haystack = " ".join(slug(name) for name in player_names(track) if name)
    return any(slug(entry) and slug(entry) in haystack for entry in ignored)


def rank_tracks(tracks: Iterable[Track], priority: Sequence[str],
                ignored: Sequence[str] = ()) -> list[Track]:
    """Return usable tracks ordered best-first.

    Playing players always beat paused ones; inside each group the configured
    priority decides, then the player name for stable output.
    """
    usable = [track for track in tracks
              if (track.title or "").strip() and not is_ignored(track, ignored)]
    return sorted(
        usable,
        key=lambda track: (
            0 if track.playing else 1,
            rank_of(track, priority),
            (track.player or track.source or "").casefold(),
        ),
    )


def choose_track(tracks: Iterable[Track], priority: Sequence[str],
                 ignored: Sequence[str] = ()) -> Track | None:
    ranked = rank_tracks(tracks, priority, ignored)
    return ranked[0] if ranked else None
