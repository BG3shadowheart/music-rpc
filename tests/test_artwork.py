"""Artwork tiers, verification, caching and failure behaviour."""
from __future__ import annotations

import json

from fakes import track
from musicrpc.artwork import (
    ArtworkResolver,
    ArtworkSettings,
    ArtworkStore,
    NullFetcher,
    fetch_art_bytes,
    looks_like_image,
)
from musicrpc.textutil import album_cache_key, track_cache_key


class FakeFetcher:
    """Serves canned JSON for catalog endpoints and images for known URLs."""

    def __init__(self, images=(), json_routes=None):
        self.images = set(images)
        self.json_routes = json_routes or {}
        self.calls = []

    def get(self, url, **_kwargs):
        self.calls.append(("get", url))
        for needle, payload in self.json_routes.items():
            if needle in url:
                return json.dumps(payload).encode("utf-8"), "application/json"
        return None, ""

    def probe(self, url, *, attempts=1, **_kwargs):
        self.calls.append(("probe", url))
        return url in self.images


class FakeRelay:
    public_url = "https://relay.example"

    def __init__(self, url="https://relay.example/cover/token.jpg"):
        self.url = url
        self.calls = 0

    def set_image(self, data, cache_key=None):
        if not data:
            return None
        self.calls += 1
        return self.url

    def clear(self):
        pass


def resolver(fetcher=None, relay=None, store=None, **settings):
    options = dict(enabled=True, catalog_lookup=True, youtube_search=True)
    options.update(settings)
    return ArtworkResolver(fetcher=fetcher or FakeFetcher(),
                           store=store if store is not None else ArtworkStore(),
                           relay=relay,
                           settings=ArtworkSettings(**options))


def test_youtube_tier_wins_for_video_ids():
    maxres = "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
    art = resolver(FakeFetcher(images={maxres}))
    image, album, tier = art.resolve(track(kind="youtube", video_id="dQw4w9WgXcQ",
                                           album="Album"))
    assert (image, tier) == (maxres, "youtube")
    assert art.store.get_track(track_cache_key("Song", "Artist"))["url"] == maxres


def test_falls_back_to_a_smaller_thumbnail_when_maxres_is_missing():
    mq = "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
    art = resolver(FakeFetcher(images={mq}))
    image, _album, tier = art.resolve(track(kind="youtube", video_id="dQw4w9WgXcQ"))
    assert tier == "youtube"
    assert image == mq


def test_cached_url_is_reused_before_network_lookups():
    store = ArtworkStore()
    store.put_track(track_cache_key("Song", "Artist"), "https://cdn.example/cover.jpg")
    fetcher = FakeFetcher(images={"https://cdn.example/cover.jpg"})
    art = resolver(fetcher, store=store)
    image, _album, tier = art.resolve(track())
    assert (image, tier) == ("https://cdn.example/cover.jpg", "cache")


def test_dead_cache_is_still_sent_as_unverified_fallback():
    store = ArtworkStore()
    store.put_track(track_cache_key("Song", "Artist"), "https://cdn.example/dead.jpg")
    art = resolver(FakeFetcher(), store=store)
    image, _album, tier = art.resolve(track())
    assert image == "https://cdn.example/dead.jpg"
    assert tier == "cache-unverified"


def test_catalog_lookup_is_used_when_nothing_else_exists():
    payload = {"results": [{"trackName": "Song", "artistName": "Artist",
                            "collectionName": "Album",
                            "artworkUrl100": "https://cdn.example/100x100bb.jpg"}]}
    fetcher = FakeFetcher(images={"https://cdn.example/600x600bb.jpg"},
                          json_routes={"itunes.apple.com/search": payload})
    art = resolver(fetcher)
    image, album, tier = art.resolve(track())
    assert tier == "catalog"
    assert image == "https://cdn.example/600x600bb.jpg"
    assert album == "Album"


def test_album_cache_is_used_for_local_tracks():
    store = ArtworkStore()
    store.put_album(album_cache_key("Artist", "Album"), "https://cdn.example/album.jpg")
    art = resolver(FakeFetcher(images={"https://cdn.example/album.jpg"}), store=store)
    image, _album, tier = art.resolve(track())
    assert (image, tier) == ("https://cdn.example/album.jpg", "album-cache")


def test_player_url_is_considered_for_local_players():
    url = "https://cdn.example/from-player.jpg"
    art = resolver(FakeFetcher(images={url}))
    image, _album, tier = art.resolve(track(image_url=url))
    assert (image, tier) == (url, "player")


def test_missing_artwork_returns_none_instead_of_raising():
    art = resolver(FakeFetcher())
    image, album, tier = art.resolve(track())
    assert image is None
    assert tier == "none"
    assert album == "Album"


def test_relay_is_used_and_never_cached():
    relay = FakeRelay()
    art = resolver(FakeFetcher(images={relay.url}), relay=relay)
    image, _album, tier = art.resolve(track(local_art="/tmp/cover.jpg"), b"/data")
    assert tier == "relay"
    assert image == relay.url
    assert art.store.get_track(track_cache_key("Song", "Artist")) is None


def test_relay_is_skipped_when_permanent_artwork_is_preferred():
    relay = FakeRelay()
    art = resolver(FakeFetcher(), relay=relay, catalog_lookup=False)
    image, _album, tier = art.resolve(track(local_art="/tmp/c.jpg"), b"/data",
                                      prefer_permanent=True)
    assert relay.calls == 0
    assert tier == "none"


def test_youtube_search_tier_derives_a_video_id():
    search_html = b'junk "videoRenderer":{"videoId":"ZZZZZZZZZZZ"'
    id_url = "https://i.ytimg.com/vi/ZZZZZZZZZZZ/maxresdefault.jpg"

    class SearchFetcher(FakeFetcher):
        def get(self, url, **kwargs):
            if "youtube.com/results" in url:
                return search_html, "text/html"
            return super().get(url, **kwargs)

    art = resolver(SearchFetcher(images={id_url}), catalog_lookup=False)
    image, _album, tier = art.resolve(track())
    assert tier == "search"
    assert image == id_url


def test_disabled_artwork_makes_no_requests():
    fetcher = FakeFetcher()
    art = resolver(fetcher, enabled=False)
    image, _album, tier = art.resolve(track())
    assert image is None and tier == "disabled"
    assert fetcher.calls == []


def test_results_are_memoised():
    fetcher = FakeFetcher(images={"https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"})
    art = resolver(fetcher)
    item = track(kind="youtube", video_id="dQw4w9WgXcQ")
    art.resolve(item)
    probes = len(fetcher.calls)
    art.resolve(item)
    assert len(fetcher.calls) == probes


def test_remember_player_track_persists_the_video_mapping():
    store = ArtworkStore()
    art = resolver(FakeFetcher(), store=store)
    art.remember_player_track(track(kind="youtube", video_id="dQw4w9WgXcQ"))
    entry = store.get_track(track_cache_key("Song", "Artist"))
    assert entry["video_id"] == "dQw4w9WgXcQ"
    assert entry["url"].startswith("https://i.ytimg.com/vi/dQw4w9WgXcQ/")


def test_remember_player_track_ignores_local_players():
    store = ArtworkStore()
    art = resolver(FakeFetcher(), store=store)
    art.remember_player_track(track(kind="local"))
    assert len(store) == 0


def test_store_persists_to_disk(tmp_path):
    path = tmp_path / "cache.json"
    store = ArtworkStore(path)
    store.put_track("a|b", "https://cdn.example/x.jpg", album="Album", video_id="v")
    store.save()
    reloaded = ArtworkStore(path)
    assert reloaded.get_track("a|b")["url"] == "https://cdn.example/x.jpg"


def test_store_prunes_to_the_limit():
    store = ArtworkStore()
    store.LIMIT = 3
    for index in range(6):
        store.put_track(f"k{index}", f"https://x/{index}.jpg")
    assert len(store._data["tracks"]) <= 6      # prune runs after the limit is exceeded
    store.put_track("last", "https://x/last.jpg")
    assert len(store._data["tracks"]) <= 4


def test_store_tolerates_a_corrupt_cache_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{{{", encoding="utf-8")
    store = ArtworkStore(path)
    assert len(store) == 0


def test_http_helpers_are_offline_safe():
    assert NullFetcher().probe("https://example.com/x.jpg") is False
    assert NullFetcher().get("https://example.com") == (None, "")


def test_fetch_art_bytes_reads_local_files(tmp_path):
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 2000)
    assert fetch_art_bytes(str(path)).startswith(b"\xff\xd8\xff")
    assert fetch_art_bytes(f"file://{path}") is not None
    assert fetch_art_bytes(str(tmp_path / "missing.jpg")) is None
    assert fetch_art_bytes(None) is None


def test_looks_like_image_checks_magic_bytes():
    assert looks_like_image(b"\xff\xd8\xff" + b"0" * 2000) is True
    assert looks_like_image(b"\x89PNG\r\n\x1a\n" + b"0" * 2000) is True
    assert looks_like_image(b"<html>" + b"0" * 2000) is False
    assert looks_like_image(b"\xff\xd8\xff") is False          # too small
    assert looks_like_image(b"nope", "image/jpeg") is False
