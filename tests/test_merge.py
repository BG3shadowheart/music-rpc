"""Merging MPRIS state with browser-extension data."""
from __future__ import annotations

from fakes import track
from musicrpc.merge import merge_tracks


def test_without_extension_data_the_platform_track_is_used():
    platform = track(title="Song", artist="Artist")
    assert merge_tracks(platform, None) is platform


def test_without_platform_data_the_extension_track_is_used():
    extension = track(title="Browser Song", kind="youtube", player="YouTube Music")
    assert merge_tracks(None, extension) is extension


def test_matching_tracks_are_combined():
    platform = track(title="Song", artist="Artist", album="", position_ms=5000,
                     duration_ms=200_000, player="chromium", kind="local")
    extension = track(title="Song", artist="Artist", album="YT Album", kind="youtube",
                      player="YouTube Music", video_id="abcdefghijk",
                      image_url="https://i.ytimg.com/vi/abcdefghijk/maxresdefault.jpg")
    merged = merge_tracks(platform, extension)
    assert merged.kind == "youtube"
    assert merged.album == "YT Album"
    assert merged.video_id == "abcdefghijk"
    assert merged.position_ms == 5000          # MPRIS owns the position
    assert merged.duration_ms == 200_000
    assert merged.playing is True
    assert merged.image_url.endswith("maxresdefault.jpg")


def test_platform_artist_wins_when_both_are_present():
    platform = track(title="Song", artist="Real Artist", player="chromium")
    extension = track(title="Song", artist="Channel Name", kind="youtube",
                      player="YouTube Music")
    merged = merge_tracks(platform, extension)
    assert merged.artist == "Real Artist"


def test_mismatched_titles_keep_the_playing_platform_track():
    platform = track(title="Song A", artist="Artist", playing=True, player="chromium")
    extension = track(title="Song B", artist="Artist", kind="youtube",
                      player="YouTube Music")
    merged = merge_tracks(platform, extension)
    assert merged.title == "Song A"


def test_mismatched_titles_prefer_the_extension_when_the_player_is_paused():
    platform = track(title="Paused Song", artist="Artist", playing=False)
    extension = track(title="Playing Song", artist="Artist", kind="youtube",
                      player="YouTube Music")
    merged = merge_tracks(platform, extension)
    assert merged.title == "Playing Song"


def test_extension_start_epoch_is_used_when_mpris_has_no_position():
    platform = track(position_ms=None, duration_ms=None)
    extension = track(start_epoch=1_700_000_000.0, kind="youtube")
    merged = merge_tracks(platform, extension)
    assert merged.start_epoch == 1_700_000_000.0
