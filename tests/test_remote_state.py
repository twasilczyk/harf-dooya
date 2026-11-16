"""Runtime RX routing, event delivery, echo filtering and lifecycle."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import DATA_DISPATCHER
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harf_dooya.receiver import SIGNAL_RX_FRAME

from .test_decoder import remote_frame
from .test_learning import emit


async def setup_remote(hass, config_entry, receiver):
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, "receiver": f"hackrf_proxy:{receiver.entry_id}"},
        unique_id="123c14",
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    covers = [
        registry.async_get_entity_id("cover", "harf_dooya", f"123c14_{i}")
        for i in range(1, 4)
    ]
    event = registry.async_get_entity_id("event", "harf_dooya", "123c14_remote_action")
    return covers, event


@pytest.mark.parametrize(
    "command,name", [(1, "open"), (3, "close"), (5, "stop"), (12, "p2")]
)
async def test_channel_event_unknown_position(
    hass, transmitter, config_entry, receiver, command, name
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    assert (
        len(hass.data[DATA_DISPATCHER][SIGNAL_RX_FRAME.format(receiver.entry_id)]) == 1
    )
    await emit(hass, receiver, remote_frame(channel=2, command=command) * 3)
    state = hass.states.get(event)
    assert state.attributes["event_type"] == name
    assert state.attributes["channel"] == 2
    assert state.attributes["remote_id"] == 0x123C14
    assert state.attributes["broadcast"] is False
    assert hass.states.get(covers[1]).attributes["last_command"] == name
    assert hass.states.get(covers[1]).attributes["last_command_origin"] == "remote"
    assert "last_command" not in hass.states.get(covers[0]).attributes
    assert all(hass.states.get(c).state == STATE_UNKNOWN for c in covers)
    assert all("current_position" not in hass.states.get(c).attributes for c in covers)
    await emit(hass, receiver, remote_frame(channel=2, command=command) * 2)
    assert hass.states.get(event).state == state.state
    assert not transmitter.commands


async def test_broadcast_filters_unknown_and_second_press(
    hass, transmitter, config_entry, receiver
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    for timings in [remote_frame(remote_id=123) * 3, remote_frame(channel=4) * 3]:
        await emit(hass, receiver, timings)
    assert hass.states.get(event).state == STATE_UNKNOWN
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await emit(hass, receiver, remote_frame(channel=0) * 3)
    first = hass.states.get(event).state
    assert hass.states.get(event).attributes["broadcast"] is True
    assert all(hass.states.get(c).attributes["last_command"] == "stop" for c in covers)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10.31):
        await emit(hass, receiver, remote_frame(channel=0) * 2)
    assert hass.states.get(event).state != first
    with patch("custom_components.harf_dooya.device.monotonic", return_value=11):
        await emit(hass, receiver, remote_frame(channel=1, command=7) * 2)
    assert hass.states.get(covers[0]).attributes["last_command"] == "unknown_0x7"
    assert hass.states.get(event).attributes["event_type"] == "stop"
    assert not transmitter.commands


async def test_echo_and_different_command(hass, transmitter, config_entry, receiver):
    covers, event = await setup_remote(hass, config_entry, receiver)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": covers[0]}, blocking=True
        )
        await emit(hass, receiver, remote_frame(command=1) * 3)
        assert hass.states.get(event).state == STATE_UNKNOWN
        await emit(hass, receiver, remote_frame(command=5) * 3)
    assert hass.states.get(event).attributes["event_type"] == "stop"
    assert len(transmitter.commands) == 1
    # Matching physical presses outside the echo window can be observed again.
    with patch("custom_components.harf_dooya.device.monotonic", return_value=11):
        await emit(hass, receiver, remote_frame(command=1) * 3)
    assert hass.states.get(event).attributes["event_type"] == "open"


async def test_disconnect_and_unload(hass, transmitter, config_entry, receiver):
    covers, event = await setup_remote(hass, config_entry, receiver)
    await emit(hass, receiver, remote_frame())
    receiver.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert hass.states.get(event).state == STATE_UNAVAILABLE
    assert hass.states.get(covers[0]).state == STATE_UNKNOWN
    receiver.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()
    await emit(hass, receiver, remote_frame())
    assert hass.states.get(event).state == STATE_UNKNOWN
    await emit(hass, receiver, remote_frame())
    assert hass.states.get(event).attributes["event_type"] == "stop"
    transmitter._attr_available = False
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(covers[0]).state == STATE_UNAVAILABLE
    assert hass.states.get(event).state != STATE_UNAVAILABLE
    remote = config_entry.runtime_data
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert not hass.data[DATA_DISPATCHER].get(SIGNAL_RX_FRAME.format(receiver.entry_id))
    remote.async_receive(remote_frame(command=1) * 3)
    assert not transmitter.commands
    assert not remote._listeners and not remote._event_listeners


async def test_missing_receiver_and_reconfigure_preserve_ids(
    hass, transmitter, config_entry, receiver
):
    hass.config_entries.async_update_entry(
        config_entry, options={"frequency": 433_921_000}, unique_id="123c14"
    )
    covers, event = await setup_remote(hass, config_entry, receiver)
    registry = er.async_get(hass)
    ids = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    for source in ["hackrf_proxy:missing", f"hackrf_proxy:{receiver.entry_id}", "none"]:
        if source.endswith(":missing"):
            hass.config_entries.async_update_entry(
                config_entry, data={**config_entry.data, "receiver": source}
            )
            assert await hass.config_entries.async_reload(config_entry.entry_id)
        result = await hass.config_entries.flow.async_init(
            "harf_dooya",
            context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"transmitter": transmitter.entity_id, "receiver": source},
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert ids == {
            e.entity_id
            for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
        }
        assert config_entry.unique_id == "123c14"
        assert config_entry.options == {"frequency": 433_921_000}
    assert hass.states.get(covers[0]).state == STATE_UNKNOWN
    assert hass.states.get(event).state == STATE_UNAVAILABLE
    assert not transmitter.commands


async def test_platforms_and_remotes_share_transmitter_lock(
    hass, transmitter, config_entry, receiver
):
    covers, _ = await setup_remote(hass, config_entry, receiver)
    other = MockConfigEntry(
        domain="harf_dooya",
        title="Other remote",
        data={**config_entry.data, "remote_id": 123},
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert other.runtime_data._tx_lock is config_entry.runtime_data._tx_lock
    registry = er.async_get(hass)
    button = registry.async_get_entity_id("button", "harf_dooya", "00007b_p2")
    await asyncio.gather(
        hass.services.async_call(
            "cover", "open_cover", {"entity_id": covers[0]}, blocking=True
        ),
        hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        ),
    )
    assert [c.repeat_count for c in transmitter.commands] == [2, 3]
    assert not config_entry.runtime_data._tx_tasks
    assert not other.runtime_data._tx_tasks
