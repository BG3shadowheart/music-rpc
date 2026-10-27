"""Title/artist normalisation and URL helpers (shared with the Windows build)."""
from __future__ import annotations

import pytest

from musicrpc.textutil import (
    album_cache_key,
    artists_match,
    clean,
    normalize_artist,
    normalize_title,
    slug,
    strip_decorations,
    titles_match,
    to_epoch_seconds,
    track_cache_key,
    video_id_from_url,
    yt_thumb_urls,
)


@pytest.mark.parametrize("value,expected", [
    ("  spaced   out  ", "spaced out"),
    ("zero\u200bwidth", "zerowidth"),
    (None, ""),
    (123, "123"),
])
def test_clean(value, expected):
    assert clean(value) == expected


def test_slug_strips_accents_and_punctuation():
    assert slug("Beyoncé - Halo!") == "beyoncehalo"
    assert track_cache_key("Halo", "Beyoncé") == "beyonce|halo"
    assert album_cache_key("Artist", "Album") == "artist|album"


@pytest.mark.parametrize("title,expected", [
    ("Song (Official Video)", "Song"),
    ("Song [4K]", "Song"),
    ("Song (Live at Home)", "Song (Live at Home)"),
    ("Song - Topic", "Song"),
    ("Song (feat. Someone)", "Song"),
])
def test_strip_decorations(title, expected):
    assert strip_decorations(title) == expected


def test_normalize_title_removes_duplicated_artist():
    assert normalize_title("Artist - Song", "Artist") == "Song"
    assert normalize_title("Song", "Artist") == "Song"
    assert normalize_title("Artist", "Artist") == "Artist"


def test_normalize_artist_removes_duplicated_title():
    assert normalize_artist("Artist - Song", "Song") == "Artist"
    assert normalize_artist("Song", "Song") == ""


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://example.com/cover.jpg", None),
    (None, None),
])
def test_video_id_from_url(url, expected):
    assert video_id_from_url(url) == expected


def test_thumb_urls_prefer_maxres():
    urls = yt_thumb_urls("dQw4w9WgXcQ")
    assert urls[0].endswith("maxresdefault.jpg")
    assert yt_thumb_urls(None) == []


@pytest.mark.parametrize("value,expected", [
    (1_700_000_000_000, 1_700_000_000.0),
    (1_700_000_000, 1_700_000_000.0),
    (0, None),
    (-5, None),
    ("nope", None),
    (None, None),
])
def test_to_epoch_seconds(value, expected):
    assert to_epoch_seconds(value) == expected


def test_title_and_artist_matching():
    assert titles_match("Song (Official Video)", "Song") is True
    assert titles_match("Song", "Other") is False
    assert artists_match("Artist feat. Other", "Artist") is True
    assert artists_match("", "Anything") is True
