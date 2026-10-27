"""Player prioritisation rules."""
from __future__ import annotations

from fakes import track
from musicrpc.config import Config
from musicrpc.selection import choose_track, rank_of, rank_tracks


def test_fooyin_wins_over_a_playing_browser():
    tracks = [track(player="chromium", source="Chromium", title="Browser"),
              track(player="fooyin", source="Fooyin", title="Local")]
    chosen = choose_track(tracks, Config().player_priority)
    assert chosen.title == "Local"


def test_a_playing_browser_beats_a_paused_fooyin():
    tracks = [track(player="fooyin", playing=False, title="Local"),
              track(player="chromium", playing=True, title="Browser")]
    chosen = choose_track(tracks, Config().player_priority)
    assert chosen.title == "Browser"


def test_priority_order_is_configurable():
    tracks = [track(player="fooyin", title="Local"),
              track(player="chromium", title="Browser")]
    chosen = choose_track(tracks, ["chromium", "fooyin"])
    assert chosen.title == "Browser"


def test_ignored_players_are_skipped():
    tracks = [track(player="kde-connect", title="Phone"), track(player="fooyin", title="Local")]
    chosen = choose_track(tracks, Config().player_priority, ["kde-connect"])
    assert chosen.title == "Local"


def test_unknown_players_rank_after_configured_ones():
    tracks = [track(player="some-new-player", source="New Player", title="Unknown"),
              track(player="fooyin", source="Fooyin", title="Local")]
    ranked = rank_tracks(tracks, ["fooyin"])
    assert [item.title for item in ranked] == ["Local", "Unknown"]
    assert rank_of(tracks[0], ["fooyin"]) > rank_of(tracks[1], ["fooyin"])


def test_priority_matching_is_case_and_punctuation_insensitive():
    assert rank_of(track(player="Google-Chrome"), ["googlechrome"]) == 0


def test_tracks_without_a_title_are_dropped():
    tracks = [track(title="   "), track(title="Real", player="fooyin")]
    assert choose_track(tracks, ["fooyin"]).title == "Real"


def test_no_players_returns_none():
    assert choose_track([], ["fooyin"]) is None


def test_ranking_is_stable_for_equal_players():
    tracks = [track(player="b", title="B"), track(player="a", title="A")]
    assert [item.player for item in rank_tracks(tracks, [])] == ["a", "b"]


def test_ignored_matching_uses_bus_name_and_source():
    tracks = [track(player="", source="org.mpris.MediaPlayer2.vlc", title="VLC")]
    assert choose_track(tracks, [], ["vlc"]) is None
