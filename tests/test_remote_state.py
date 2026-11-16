"""Runtime RX routing, event delivery, echo filtering and lifecycle."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.const import (
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPENING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
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
async def test_channel_event_assumed_position(
    hass, transmitter, config_entry, receiver, command, name
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    assert (
        len(hass.data[DATA_DISPATCHER][SIGNAL_RX_FRAME.format(receiver.entry_id)]) == 1
    )
    await emit(hass, receiver, remote_frame(channel=2, command=command) * 3)
    state = hass.states.get(event)
    assert state.attributes["command"] == name
    assert state.attributes["event_type"] == f"blind_2_{name}"
    assert state.attributes["channel"] == 2
    assert state.attributes["remote_id"] == 0x123C140
    assert state.attributes["broadcast"] is False
    assert hass.states.get(covers[1]).attributes["last_command"] == name
    assert hass.states.get(covers[1]).attributes["last_command_origin"] == "remote"
    assert "last_command" not in hass.states.get(covers[0]).attributes
    for index, cover in enumerate(covers):
        expected = {1: STATE_OPENING, 3: STATE_CLOSING}.get(command, STATE_UNKNOWN)
        cover_state = hass.states.get(cover)
        assert "current_position" not in cover_state.attributes
        assert cover_state.state == (expected if index == 1 else STATE_UNKNOWN)
    await emit(hass, receiver, remote_frame(channel=2, command=command) * 2)
    assert hass.states.get(event).state == state.state
    assert not transmitter.commands


async def test_broadcast_filters_unknown_and_second_press(
    hass, transmitter, config_entry, receiver
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    all_blinds = er.async_get(hass).async_get_entity_id(
        "cover", "harf_dooya", "123c14_0"
    )
    covers.append(all_blinds)
    for timings in [remote_frame(remote_id=123) * 3, remote_frame(channel=4) * 3]:
        await emit(hass, receiver, timings)
    assert hass.states.get(event).state == STATE_UNKNOWN
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await emit(hass, receiver, remote_frame(channel=0) * 3)
    first = hass.states.get(event).state
    assert hass.states.get(event).attributes["broadcast"] is True
    assert hass.states.get(event).attributes["event_type"] == "all_blinds_stop"
    assert all(hass.states.get(c).attributes["last_command"] == "stop" for c in covers)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10.31):
        await emit(hass, receiver, remote_frame(channel=0) * 2)
    assert hass.states.get(event).state != first
    with patch("custom_components.harf_dooya.device.monotonic", return_value=11):
        await emit(hass, receiver, remote_frame(channel=1, command=7) * 2)
    assert hass.states.get(covers[0]).attributes["last_command"] == "unknown_0x7"
    assert hass.states.get(all_blinds).attributes["last_command"] == "stop"
    assert hass.states.get(event).attributes["command"] == "stop"
    assert not transmitter.commands


async def test_broadcast_transmission_and_echo(hass, transmitter, config_entry, receiver):
    covers, event = await setup_remote(hass, config_entry, receiver)
    all_blinds = er.async_get(hass).async_get_entity_id(
        "cover", "harf_dooya", "123c14_0"
    )
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await hass.services.async_call(
            "cover", "stop_cover", {"entity_id": all_blinds}, blocking=True
        )
        assert len(transmitter.commands) == 1
        for entity_id in [all_blinds, *covers]:
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNKNOWN
            assert state.attributes["last_command"] == "stop"
            assert state.attributes["last_command_origin"] == "home_assistant"
        await emit(hass, receiver, remote_frame(channel=0) * 3)
        assert hass.states.get(event).state == STATE_UNKNOWN
        await emit(hass, receiver, remote_frame(channel=2, command=1) * 3)
    assert hass.states.get(event).attributes["command"] == "open"
    assert hass.states.get(covers[1]).attributes["last_command"] == "open"
    assert hass.states.get(all_blinds).attributes["last_command"] == "stop"
    assert len(transmitter.commands) == 1


async def test_echo_and_different_command(hass, transmitter, config_entry, receiver):
    covers, event = await setup_remote(hass, config_entry, receiver)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": covers[0]}, blocking=True
        )
        await emit(hass, receiver, remote_frame(command=1) * 3)
        assert hass.states.get(event).state == STATE_UNKNOWN
        await emit(hass, receiver, remote_frame(command=5) * 3)
    assert hass.states.get(event).attributes["command"] == "stop"
    assert len(transmitter.commands) == 1
    # Matching physical presses outside the echo window can be observed again.
    with patch("custom_components.harf_dooya.device.monotonic", return_value=11):
        await emit(hass, receiver, remote_frame(command=1) * 3)
    assert hass.states.get(event).attributes["command"] == "open"


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
    assert hass.states.get(event).attributes["command"] == "stop"
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
        assert config_entry.unique_id == "123c140"
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
        data={**{k: v for k, v in config_entry.data.items() if k != "entity_key"}, "remote_id": 123},
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


async def test_nonmovement_and_foreign_frames_preserve_positions(
    hass, transmitter, config_entry, receiver
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    await emit(hass, receiver, remote_frame(command=1) * 3)
    expected = dict(config_entry.runtime_data.assumed_positions)
    previous_event = hass.states.get(event).state
    for frame in (
        remote_frame(channel=0, command=7),
        remote_frame(remote_id=123, command=3),
        remote_frame(channel=4, command=3),
    ):
        await emit(hass, receiver, frame * 3)
        assert config_entry.runtime_data.assumed_positions == expected
        assert hass.states.get(covers[0]).state == STATE_OPENING
        assert hass.states.get(event).state == previous_event
    await emit(hass, receiver, remote_frame(channel=0, command=12) * 3)
    assert config_entry.runtime_data.assumed_positions == expected
    assert hass.states.get(covers[0]).state == STATE_OPENING
    assert hass.states.get(event).attributes["command"] == "p2"
    assert not transmitter.commands


async def test_position_survives_radio_loss_and_resets_on_reload(
    hass, transmitter, config_entry, receiver
):
    covers, event = await setup_remote(hass, config_entry, receiver)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=10):
        await emit(hass, receiver, remote_frame(command=3) * 3)
    with patch("custom_components.harf_dooya.device.monotonic", return_value=46):
        config_entry.runtime_data._update_positions()
    assert hass.states.get(covers[0]).attributes["current_position"] == 0
    remote = config_entry.runtime_data
    receiver.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert hass.states.get(event).state == STATE_UNAVAILABLE
    assert hass.states.get(covers[0]).state == STATE_CLOSED
    assert hass.states.get(covers[0]).attributes["current_position"] == 0
    transmitter._attr_available = False
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(covers[0]).state == STATE_UNAVAILABLE
    assert remote.assumed_positions[1] == 0
    transmitter._attr_available = True
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(covers[0]).state == STATE_CLOSED
    assert hass.states.get(covers[0]).attributes["current_position"] == 0
    data, options = dict(config_entry.data), dict(config_entry.options)
    registry = er.async_get(hass)
    identities = {
        e.entity_id: (e.unique_id, e.device_id)
        for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data is not remote
    assert config_entry.data == data and config_entry.options == options
    assert identities == {
        e.entity_id: (e.unique_id, e.device_id)
        for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    for entity_id in hass.states.async_entity_ids("cover"):
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNKNOWN
        assert "current_position" not in state.attributes
