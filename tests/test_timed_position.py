"""Timed estimates and configuration through Home Assistant's state registry."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.harf_dooya import device

from .test_decoder import remote_frame
from .test_learning import emit
from .test_remote_state import setup_remote


@pytest.fixture
def motion_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(device, "monotonic", lambda: clock.now)
    return clock


def cover_ids(hass):
    registry = er.async_get(hass)
    return [
        registry.async_get_entity_id("cover", "harf_dooya", f"123c14_{i}")
        for i in range(4)
    ]


def assert_cover(hass, entity_id, position, state):
    actual = hass.states.get(entity_id)
    assert actual.state == state
    assert actual.attributes.get("current_position") == position
    assert actual.attributes["is_closed"] is (
        None if position is None else position == 0
    )
    assert actual.attributes["assumed_state"] is True
    assert actual.attributes["supported_features"] == (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )


def advance(entry, clock, seconds):
    clock.now += seconds
    entry.runtime_data._update_positions()


@pytest.mark.parametrize("origin", ["ha", "remote"])
async def test_movement_stop_and_unknown_recovery(
    hass, transmitter, config_entry, receiver, motion_clock, origin
):
    await setup_remote(hass, config_entry, receiver)
    entities = cover_ids(hass)
    for entity_id in entities:
        assert_cover(hass, entity_id, None, "unknown")

    async def command(button):
        if origin == "ha":
            service = {1: "open_cover", 3: "close_cover", 5: "stop_cover"}[button]
            await hass.services.async_call(
                "cover", service, {"entity_id": entities[1]}, blocking=True
            )
        else:
            await emit(hass, receiver, remote_frame(command=button) * 3)

    await command(1)
    assert_cover(hass, entities[1], None, "opening")
    advance(config_entry, motion_clock, 10)
    await command(5)
    assert_cover(hass, entities[1], None, "unknown")
    advance(config_entry, motion_clock, 1)
    await command(3)
    assert_cover(hass, entities[1], None, "closing")
    advance(config_entry, motion_clock, 30)
    assert_cover(hass, entities[1], 0, "closed")
    await command(1)
    advance(config_entry, motion_clock, 12)
    assert_cover(hass, entities[1], 40, "opening")
    # Stop advances the estimate even between the periodic ticks.
    motion_clock.now += 3
    await command(5)
    assert_cover(hass, entities[1], 50, "open")
    advance(config_entry, motion_clock, 60)
    assert_cover(hass, entities[1], 50, "open")
    for channel in (0, 2, 3):
        assert_cover(hass, entities[channel], None, "unknown")
    assert len(transmitter.commands) == (5 if origin == "ha" else 0)
    assert config_entry.runtime_data._position_timer is None


@pytest.mark.parametrize("origin", ["ha", "remote"])
async def test_broadcast_uses_individual_times(
    hass, transmitter, config_entry, receiver, motion_clock, origin
):
    hass.config_entries.async_update_entry(
        config_entry, options={"travel_times": {"1": 10, "2": 20, "3": 30}}
    )
    await setup_remote(hass, config_entry, receiver)
    entities = cover_ids(hass)
    if origin == "ha":
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": entities[0]}, blocking=True
        )
    else:
        await emit(hass, receiver, remote_frame(channel=0, command=1) * 3)
    advance(config_entry, motion_clock, 10)
    assert_cover(hass, entities[1], 100, "open")
    for channel in (0, 2, 3):
        assert_cover(hass, entities[channel], None, "opening")
    advance(config_entry, motion_clock, 2)
    assert_cover(hass, entities[1], 100, "open")
    advance(config_entry, motion_clock, 12)
    assert_cover(hass, entities[2], 100, "open")
    advance(config_entry, motion_clock, 12)
    for entity_id in entities:
        assert_cover(hass, entity_id, 100, "open")
    await emit(hass, receiver, remote_frame(channel=2, command=3) * 3)
    advance(config_entry, motion_clock, 10)
    assert_cover(hass, entities[2], 50, "closing")
    assert_cover(hass, entities[0], 83, "closing")
    for channel in (1, 3):
        assert_cover(hass, entities[channel], 100, "open")
    await emit(hass, receiver, remote_frame(channel=0, command=5) * 3)
    assert_cover(hass, entities[2], 50, "open")
    assert len(transmitter.commands) == (1 if origin == "ha" else 0)


async def test_timer_publishes_completion_and_unload_cancels(
    hass, transmitter, config_entry, receiver, motion_clock, monkeypatch
):
    monkeypatch.setattr(device, "POSITION_UPDATE_INTERVAL", 0.01)
    await setup_remote(hass, config_entry, receiver)
    remote = config_entry.runtime_data
    entity_id = cover_ids(hass)[1]
    await emit(hass, receiver, remote_frame(command=1) * 3)
    updated = asyncio.Event()
    unsubscribe = remote.async_add_listener(updated.set)
    motion_clock.now += 36
    await asyncio.wait_for(updated.wait(), timeout=2)
    assert_cover(hass, entity_id, 100, "open")
    assert remote._position_timer is None
    unsubscribe()
    await emit(hass, receiver, remote_frame(command=3) * 3)
    handle = remote._position_timer
    assert handle is not None
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert handle.cancelled()
    assert remote._position_timer is None
    motion_clock.now += 100
    remote._update_positions()
    assert hass.states.get(entity_id).state == "unavailable"


async def test_travel_time_configuration_persists_without_reset(
    hass, transmitter, config_entry, receiver, motion_clock
):
    await setup_remote(hass, config_entry, receiver)
    registry = er.async_get(hass)
    number = registry.async_get_entity_id(
        "number", "harf_dooya", "123c14_travel_time_1"
    )
    assert len(hass.states.async_entity_ids("number")) == 3
    assert float(hass.states.get(number).state) == 30
    assert hass.states.get(number).attributes["unit_of_measurement"] == "s"
    entity_id = cover_ids(hass)[1]
    assert registry.async_get(number).device_id == registry.async_get(entity_id).device_id
    await emit(hass, receiver, remote_frame(command=1) * 3)
    remote = config_entry.runtime_data
    for value in (20, 12.5):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": value}, blocking=True
        )
        assert config_entry.runtime_data is remote
        assert float(hass.states.get(number).state) == value
        assert remote.motions[1].travel_time == 30  # Current movement keeps its timing.
        assert config_entry.options["travel_times"]["1"] == value
    for value in (0, 3601, float("nan"), float("inf")):
        with pytest.raises((HomeAssistantError, ValueError)):
            await hass.services.async_call(
                "number", "set_value", {"entity_id": number, "value": value}, blocking=True
            )
    advance(config_entry, motion_clock, 36)
    assert_cover(hass, entity_id, 100, "open")
    await emit(hass, receiver, remote_frame(command=3) * 3)
    assert remote.motions[1].travel_time == 12.5
    advance(config_entry, motion_clock, 6.25)
    assert_cover(hass, entity_id, 50, "closing")
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(number).state) == 12.5
    assert_cover(hass, entity_id, None, "unknown")
    assert not transmitter.commands


async def test_channel_reconfigure_removes_travel_time_entities(
    hass, transmitter, config_entry, motion_clock
):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    retained = registry.async_get_entity_id(
        "number", "harf_dooya", "123c14_travel_time_1"
    )
    for count in (1, 4):
        result = await hass.config_entries.flow.async_init(
            "harf_dooya",
            context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"transmitter": transmitter.entity_id, "receiver": "none", "channels": count},
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert len(hass.states.async_entity_ids("number")) == count
        assert registry.async_get(retained) is not None
        numbers = [
            e for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
            if e.domain == "number"
        ]
        assert len(numbers) == count


async def test_unrelated_command_advances_overdue_movement(
    hass, transmitter, config_entry, receiver, motion_clock
):
    """Rescheduling on another channel cannot strand an overdue completion."""
    await setup_remote(hass, config_entry, receiver)
    await emit(hass, receiver, remote_frame(command=1) * 3)
    motion_clock.now += 40  # Command arrives before the delayed timer callback.
    await emit(hass, receiver, remote_frame(channel=2, command=12) * 3)
    entities = cover_ids(hass)
    assert_cover(hass, entities[1], 100, "open")
    assert_cover(hass, entities[2], None, "unknown")
    assert config_entry.runtime_data._position_timer is None
    assert not transmitter.commands
