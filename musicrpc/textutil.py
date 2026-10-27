"""Text, title and URL helpers.

These functions are a direct port of the helpers in the Windows ``app.py`` so
that titles, artists and artwork cache keys behave identically on both
platforms. They are pure functions with no platform dependencies.
"""
from __future__ import annotations

import re
import unicodedata


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u200b", "")).strip()


def slug(value) -> str:
    text = unicodedata.normalize("NFKD", clean(value))
    text = text.encode("ascii", "ignore").decode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def track_cache_key(title, artist) -> str:
    return f"{slug(artist)}|{slug(title)}"


def album_cache_key(artist, album) -> str:
    return f"{slug(artist)}|{slug(album)}"


_DECORATION = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DECORATION_WORDS = (
    "official", "video", "audio", "lyric", "lyrics", "visualizer", "remaster",
    "mv", "m/v", "4k", "8k", "hd", "hq", "explicit", "clean", "performance",
    "version", "edit", "mix", "audio only", "with lyrics", "color coded",
)


def strip_decorations(title) -> str:
    """Turn 'Song (Official Video) [4K]' into 'Song' for search/catalog use."""
    def drop(match):
        inner = match.group(1).casefold()
        return " " if any(word in inner for word in _DECORATION_WORDS) else match.group(0)
    text = _DECORATION.sub(drop, clean(title))
    text = re.sub(r"\s*[-–—]\s*topic\s*$", "", text, flags=re.I)
    text = re.sub(r"\((?:feat|ft)\.?[^)]*\)", "", text, flags=re.I)
    return clean(text)


def normalize_title(title, artist) -> str:
    """Remove a duplicated leading artist from players that include it in the title."""
    title, artist = clean(title), clean(artist)
    if not title or not artist:
        return title
    separators = (" • ", " · ", " – ", " — ", " - ", ": ")
    if title.casefold() == artist.casefold():
        for separator in separators:
            if separator in title:
                _prefix, _, remainder = title.partition(separator)
                if remainder.strip():
                    return remainder.strip()
    for separator in separators:
        prefix, found, remainder = title.partition(separator)
        if found and prefix.casefold() == artist.casefold() and remainder.strip():
            return remainder.strip()
    return title


def normalize_artist(artist, title) -> str:
    """Strip a duplicate title when a player combines artist and track in both fields."""
    artist, title = clean(artist), clean(title)
    if not artist or not title:
        return artist
    for separator in (" • ", " · ", " – ", " — ", " - ", ": "):
        if separator in artist:
            first, _, remainder = artist.partition(separator)
            if remainder.strip().casefold() == title.casefold():
                return first.strip()
    if artist.casefold() == title.casefold():
        return ""
    return artist


def video_id_from_url(url):
    if not isinstance(url, str):
        return None
    match = re.search(r"(?:[?&]v=|/vi/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


def yt_thumb_urls(video_id):
    if not video_id:
        return []
    return [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]


def to_epoch_seconds(value):
    """Extension timestamps are milliseconds (Date.now()); MPRIS ones are not."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e11:          # milliseconds since epoch
        number /= 1000.0
    return number


def titles_match(left, right) -> bool:
    a, b = slug(strip_decorations(left)), slug(strip_decorations(right))
    if not a or not b:
        return False
    return a == b or a in b or b in a


def artists_match(left, right) -> bool:
    a, b = slug(left), slug(right)
    if not a or not b:
        return True
    return a == b or a in b or b in a
