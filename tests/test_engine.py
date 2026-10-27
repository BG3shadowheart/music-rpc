"""Presence state machine: transitions, dedupe, artwork retry, Discord recovery."""
from __future__ import annotations

import pytest

from fakes import (
    FakeBridge,
    FakeClock,
    FakeRPC,
    FakeResolver,
    SequenceFactory,
    StubBackend,
    snapshots,
    track,
)
from musicrpc.config import Config
from musicrpc.engine import PresenceEngine, build_activity


def make_engine(player_snapshots, *, resolver=None, rpc=None, config=None,
                bridge=None, factory=None):
    clock = FakeClock()
    backend = StubBackend(*player_snapshots)
    resolver = resolver or FakeResolver()
    rpc = rpc or FakeRPC()
    factory = factory or SequenceFactory(rpc)
    engine = PresenceEngine(backend=backend, config=config or Config(),
                            resolver=resolver, rpc_factory=factory,
                            bridge=bridge, clock=clock, wall=clock.wall)
    return engine, clock, backend, resolver, rpc


def test_presence_is_sent_once_per_track():
    engine, clock, _backend, _resolver, rpc = make_engine(snapshots(track()))
    first = engine.tick()
    assert first.sent is True
    assert first.discord_state == "connected"
    assert rpc.updates[0].details == "Song"
    assert rpc.updates[0].state == "Artist"
    assert rpc.updates[0].activity_type == "listening"
    assert rpc.updates[0].status_display_type == "details"

    # Repeated polls with identical metadata must not churn the activity.
    for _ in range(5):
        assert engine.tick().sent is False
    assert len(rpc.updates) == 1


def test_progress_bar_uses_position_and_duration():
    engine, _clock, _backend, _resolver, rpc = make_engine(snapshots(track()))
    engine.tick()
    activity = rpc.updates[0]
    assert activity.start is not None and activity.end is not None
    assert activity.end - activity.start == 200


def test_track_change_is_published_again():
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), track(title="Second Song", track_id="/track/2")))
    engine.tick()
    clock.advance(2)
    assert engine.tick().sent is True
    assert [activity.details for activity in rpc.updates] == ["Song", "Second Song"]


def test_activity_refreshes_after_refresh_interval():
    config = Config()
    engine, clock, _backend, _resolver, rpc = make_engine(snapshots(track()), config=config)
    engine.tick()
    clock.advance(config.refresh_seconds - 1)
    assert engine.tick().sent is False
    clock.advance(2)
    assert engine.tick().sent is True
    assert len(rpc.updates) == 2


def test_seek_triggers_an_update():
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(position_ms=10_000), track(position_ms=90_000)))
    engine.tick()
    clock.advance(1)
    result = engine.tick()
    assert result.sent is True
    # Seeking forward moves the computed start (wall clock - position)
    # backwards by the seek distance.
    assert rpc.updates[0].start - rpc.updates[-1].start >= 79


def test_pause_clears_the_activity():
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), track(playing=False)))
    engine.tick()
    clock.advance(2)
    result = engine.tick()
    assert result.cleared is True
    assert rpc.clears == 1
    assert engine._last_sent_key is None


def test_pause_without_clear_on_pause_keeps_presence_without_timer():
    config = Config()
    config.clear_on_pause = False
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), track(playing=False)), config=config)
    engine.tick()
    clock.advance(2)
    result = engine.tick()
    assert result.sent is True
    assert result.activity.start is None
    assert result.activity.end is None
    assert rpc.clears == 0


def test_missing_player_clears_only_after_grace_period():
    config = Config()
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), None), config=config)
    engine.tick()
    clock.advance(1)
    assert engine.tick().cleared is False
    clock.advance(config.no_track_grace_seconds)
    result = engine.tick()
    assert result.cleared is True
    assert rpc.clears == 1


def test_player_closing_after_grace_clears_once():
    config = Config()
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), None), config=config)
    engine.tick()
    clock.advance(1)
    engine.tick()                      # the player disappears here
    clock.advance(config.no_track_grace_seconds + 1)
    engine.tick()                      # grace expired -> clear
    engine.tick()                      # and never again
    assert rpc.clears == 1


def test_backend_error_is_reported_but_does_not_crash():
    from musicrpc.models import Snapshot

    engine, clock, _backend, _resolver, rpc = make_engine(
        [Snapshot(players=(), error="cannot connect to the D-Bus session bus")])
    result = engine.tick()
    assert result.error is None  # backend problems are not Discord problems
    assert result.cleared is False
    assert rpc.updates == []


def test_extension_data_overlays_artwork_and_video_id():
    extension = track(title="Song", artist="Artist", album="YT Album", kind="youtube",
                      player="YouTube Music", video_id="dQw4w9WgXcQ",
                      image_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg")
    bridge = FakeBridge(extension, age=1.0)
    resolver = FakeResolver(("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
                              "YT Album", "youtube"))
    engine, _clock, _backend, resolver, rpc = make_engine(
        snapshots(track()), resolver=resolver, bridge=bridge)
    result = engine.tick()
    assert result.track.kind == "youtube"
    assert result.track.album == "YT Album"
    assert resolver.remembered and resolver.remembered[0].video_id == "dQw4w9WgXcQ"
    assert rpc.updates[0].large_image.endswith("maxresdefault.jpg")
    assert rpc.updates[0].large_text == "YT Album"


def test_extension_only_mode_when_browser_exposes_no_mpris_player():
    extension = track(title="Browser Song", artist="Browser Artist", kind="youtube",
                      player="YouTube Music", album="", video_id="abcdefghijk")
    bridge = FakeBridge(extension, age=2.0)
    engine, _clock, _backend, _resolver, rpc = make_engine(
        snapshots(None), bridge=bridge)
    result = engine.tick()
    assert result.sent is True
    assert rpc.updates[0].details == "Browser Song"


def test_stale_extension_data_is_ignored():
    config = Config()
    extension = track(title="Ghost Song", artist="Ghost", kind="youtube",
                      player="YouTube Music")
    bridge = FakeBridge(extension, age=config.extension_fresh_seconds + 5)
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(None), config=config, bridge=bridge)
    result = engine.tick()
    assert result.sent is False
    clock.advance(config.no_track_grace_seconds + 1)
    engine.tick()
    assert rpc.updates == []


def test_artwork_failure_does_not_block_presence():
    resolver = FakeResolver(raises=RuntimeError("catalogue exploded"))
    engine, _clock, _backend, _resolver, rpc = make_engine(
        snapshots(track()), resolver=resolver)
    result = engine.tick()
    assert result.sent is True
    assert result.artwork_tier == "none"
    assert rpc.updates[0].large_image is None


def test_artwork_is_upgraded_after_retry_interval():
    config = Config()
    resolver = FakeResolver((None, "", "none"),
                            ("https://cdn.example/cover.jpg", "Album", "catalog"))
    engine, clock, _backend, resolver, rpc = make_engine(
        snapshots(track()), resolver=resolver, config=config)
    engine.tick()
    assert rpc.updates[0].large_image is None
    clock.advance(config.art_retry_seconds + 1)
    result = engine.tick()
    assert result.artwork_tier == "catalog"
    assert rpc.updates[-1].large_image == "https://cdn.example/cover.jpg"
    assert resolver.calls[-1]["prefer_permanent"] is True


def test_discord_connection_is_retried_with_backoff():
    first = FakeRPC(connect_failures=1)
    second = FakeRPC()
    factory = SequenceFactory(first, second)
    engine, clock, _backend, _resolver, _rpc = make_engine(
        snapshots(track()), factory=factory)
    result = engine.tick()
    assert result.discord_state == "disconnected"
    assert result.error
    assert factory.created == 1

    # Too early: still backing off.
    clock.advance(1)
    engine.tick()
    assert factory.created == 1

    clock.advance(Config().discord_retry_seconds)
    result = engine.tick()
    assert result.discord_state == "connected"
    assert result.sent is True
    assert factory.created == 2


def test_discord_update_failure_reconnects_and_resends():
    rpc = FakeRPC(update_failures=1)
    engine, clock, _backend, _resolver, _rpc = make_engine(
        snapshots(track()), rpc=rpc)
    result = engine.tick()
    assert result.sent is False
    assert result.discord_state == "reconnecting"
    assert rpc.closed is True

    clock.advance(Config().discord_retry_seconds)
    result = engine.tick()
    assert result.discord_state == "connected"
    assert result.sent is True
    assert len(rpc.updates) == 1


def test_discord_disabled_never_connects():
    config = Config()
    config.discord_enabled = False
    engine, _clock, _backend, _resolver, rpc = make_engine(
        snapshots(track()), config=config)
    result = engine.tick()
    assert result.discord_state == "disabled"
    assert rpc.connect_calls == 0
    assert rpc.updates == []


def test_clear_failure_drops_the_connection():
    rpc = FakeRPC(clear_failures=1)
    engine, clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(), track(playing=False)), rpc=rpc)
    engine.tick()
    clock.advance(2)
    result = engine.tick()
    assert result.discord_state == "reconnecting"
    assert rpc.closed is True


def test_shutdown_clears_and_closes():
    engine, _clock, backend, resolver, rpc = make_engine(snapshots(track()))
    engine.tick()
    engine.shutdown()
    assert rpc.clears == 1
    assert rpc.closed is True
    assert backend.closed is True


def test_build_activity_truncates_and_hides_duplicate_album():
    long_title = "x" * 300
    activity = build_activity(track(title=long_title, artist="", album=long_title), "", None)
    assert len(activity.details) == 128
    assert activity.state == "Music"
    assert activity.large_text is None


def test_build_activity_keeps_album_as_tooltip():
    activity = build_activity(track(album="Great Album"), "", "https://art")
    assert activity.large_text == "Great Album"
    assert activity.large_image == "https://art"


def test_android_position_is_clamped_to_duration():
    engine, _clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(position_ms=500_000, duration_ms=200_000)))
    engine.tick()
    activity = rpc.updates[0]
    assert activity.end - activity.start == 200


@pytest.mark.parametrize("position,duration", [(None, None), (0, 0), (None, 180_000)])
def test_missing_position_or_duration_omits_timestamps(position, duration):
    engine, _clock, _backend, _resolver, rpc = make_engine(
        snapshots(track(position_ms=position, duration_ms=duration)))
    engine.tick()
    activity = rpc.updates[0]
    if not position or not duration:
        assert activity.start is None and activity.end is None
