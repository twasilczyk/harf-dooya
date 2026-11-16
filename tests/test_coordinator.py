"""Coordinator checks independent of HA's threaded startup fixtures."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.harf_dooya.device import DooyaRemote
from custom_components.harf_dooya.protocol import make_command, make_programming_command

from .test_decoder import remote_frame


def make_remote(hass=None, remote_id=0x123C14):
    hass = hass or SimpleNamespace(data={})
    entry = SimpleNamespace(
        data={"remote_id": remote_id, "channels": 3, "transmitter": "radio-uuid"}
    )
    return DooyaRemote(hass, entry)


def test_routing_and_echo_without_motor_state():
    remote = make_remote()
    events = []
    remote.async_add_event_listener(events.append)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        remote.async_receive(remote_frame(channel=0) * 3)
    assert len(events) == 1 and events[0].channel == 0
    assert set(remote.observations) == {1, 2, 3}
    assert all(o["last_command"] == "stop" for o in remote.observations.values())
    with patch("custom_components.harf_dooya.device.monotonic", return_value=11):
        remote._echo_until[(1, 1)] = 12
        remote.async_receive(remote_frame(command=1) * 3)
        remote.async_receive(remote_frame(command=12) * 4)
        remote.async_receive(remote_frame(remote_id=1) * 3)
        remote.async_receive(remote_frame(channel=255) * 3)
    assert [f.command for f in events] == [5, 12]
    remote.async_stop()
    remote.async_receive(remote_frame() * 3)
    assert len(events) == 2


def test_confirmation_disconnect_and_unknown():
    remote = make_remote()
    events = []
    remote.async_add_event_listener(events.append)
    remote.async_receive(remote_frame())
    remote._availability(False)
    remote.async_receive(remote_frame())
    assert not events
    remote.async_receive(remote_frame())
    assert len(events) == 1
    remote.async_receive(remote_frame(command=7) * 2)
    assert len(events) == 1
    assert remote.observations[1]["last_command"] == "unknown_0x7"


def test_serialization_echo_during_send_and_context_callback():
    async def run():
        hass = SimpleNamespace(data={})
        remote = make_remote(hass)
        other = make_remote(hass, 123)
        assert remote._tx_lock is other._tx_lock
        seen = []
        remote.async_add_event_listener(seen.append)
        sent = []

        async def send(command):
            sent.append(command)
            remote.async_receive(remote_frame(command=1) * 3)
            remote.async_receive(remote_frame(command=5) * 3)

        command = make_command(0x123C14, 1, 1)
        programming = make_programming_command(123, 1)
        first = asyncio.create_task(remote.async_transmit(1, 1, command, send))
        await asyncio.sleep(0)
        second = asyncio.create_task(other.async_transmit(1, 12, programming, send))
        await asyncio.sleep(0)
        assert sent == [command]  # Includes queued airtime, not just the send call.
        await asyncio.gather(first, second)
        assert sent == [command, programming]
        assert [f.command for f in seen] == [5]
        assert remote.observations[1]["last_command_origin"] == "home_assistant"
        assert not remote._tx_tasks and not other._tx_tasks

    asyncio.run(run())


def test_unload_cancels_pending_senders():
    async def run():
        remote = make_remote()
        calls = []

        async def send(command):
            calls.append(command)

        await remote._tx_lock.acquire()
        task = asyncio.create_task(
            remote.async_transmit(1, 5, make_command(1, 1, 5), send)
        )
        await asyncio.sleep(0)
        remote.async_stop()
        remote._tx_lock.release()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not calls
        assert not remote._tx_tasks

    asyncio.run(run())
