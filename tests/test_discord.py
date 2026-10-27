"""Discord client wrapper, payload mapping and IPC socket discovery."""
from __future__ import annotations

import socket

import pytest

from musicrpc.discord import (
    Activity,
    DiscordError,
    PyPresenceClient,
    find_ipc_sockets,
)


class FakePresence:
    def __init__(self, app_id, *, fail_update=False, fail_connect=False):
        self.app_id = app_id
        self.fail_update = fail_update
        self.fail_connect = fail_connect
        self.updates = []
        self.clears = 0
        self.connected = False
        self.closed = False

    def connect(self):
        if self.fail_connect:
            raise ConnectionError("Discord is not running")
        self.connected = True

    def update(self, **kwargs):
        if self.fail_update:
            raise ConnectionError("pipe closed")
        self.updates.append(kwargs)
        return {"cmd": "SET_ACTIVITY", "data": {"application_id": self.app_id, "type": 2}}

    def clear(self):
        self.clears += 1
        return {"cmd": "SET_ACTIVITY"}

    def close(self):
        self.closed = True


def make_client(presence):
    return PyPresenceClient("1561450956242161745", presence_factory=lambda _app_id: presence)


def test_update_maps_the_activity_to_pypresence_kwargs():
    from pypresence import ActivityType, StatusDisplayType

    presence = FakePresence("1")
    client = make_client(presence)
    client.connect()
    activity = Activity(details="Song", state="Artist", large_image="https://x/y.jpg",
                        large_text="Album", start=100, end=300)
    reply = client.update(activity)
    assert reply["cmd"] == "SET_ACTIVITY"
    kwargs = presence.updates[0]
    assert kwargs["details"] == "Song"
    assert kwargs["state"] == "Artist"
    assert kwargs["large_image"] == "https://x/y.jpg"
    assert kwargs["large_text"] == "Album"
    assert kwargs["start"] == 100 and kwargs["end"] == 300
    assert kwargs["activity_type"] == ActivityType.LISTENING
    assert kwargs["status_display_type"] == StatusDisplayType.DETAILS


def test_update_omits_optional_fields():
    presence = FakePresence("1")
    client = make_client(presence)
    client.connect()
    client.update(Activity(details="Song", state="Music"))
    kwargs = presence.updates[0]
    assert "large_image" not in kwargs
    assert "large_text" not in kwargs
    assert "start" not in kwargs


def test_long_strings_are_truncated():
    presence = FakePresence("1")
    client = make_client(presence)
    client.connect()
    client.update(Activity(details="t" * 300, state="s" * 300,
                           large_image="https://x/" + "y" * 400,
                           large_text="a" * 300))
    kwargs = presence.updates[0]
    assert len(kwargs["details"]) == 128
    assert len(kwargs["state"]) == 128
    assert len(kwargs["large_image"]) == 256
    assert len(kwargs["large_text"]) == 128


def test_connect_failure_is_wrapped():
    client = make_client(FakePresence("1", fail_connect=True))
    with pytest.raises(DiscordError):
        client.connect()
    assert client.connected is False


def test_update_failure_is_wrapped():
    client = make_client(FakePresence("1", fail_update=True))
    client.connect()
    with pytest.raises(DiscordError):
        client.update(Activity(details="x", state="y"))


def test_update_without_connection_is_an_error():
    client = PyPresenceClient("1", presence_factory=lambda _id: FakePresence("1"))
    with pytest.raises(DiscordError):
        client.update(Activity(details="x", state="y"))


def test_close_is_idempotent():
    presence = FakePresence("1")
    client = make_client(presence)
    client.connect()
    client.close()
    client.close()
    assert presence.closed is True


def test_clear_without_connection_is_a_noop():
    client = PyPresenceClient("1", presence_factory=lambda _id: FakePresence("1"))
    assert client.clear() is None


def test_ipc_sockets_are_discovered_in_the_runtime_directory(monkeypatch, tmp_path):
    runtime = tmp_path / "run"      # created by the conftest XDG fixture
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = runtime / "discord-ipc-0"
    server.bind(str(path))
    server.listen(1)
    try:
        assert len(find_ipc_sockets()) >= 1
        assert str(path) in find_ipc_sockets()
    finally:
        server.close()


def test_stale_ipc_socket_files_are_ignored(monkeypatch, tmp_path):
    runtime = tmp_path / "run"      # created by the conftest XDG fixture
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    (runtime / "discord-ipc-9").write_text("")   # dead file, nothing listening
    assert all("discord-ipc-9" not in item for item in find_ipc_sockets())
