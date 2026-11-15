"""Verify cover controls and the RF consumer lifecycle in Home Assistant."""

import pytest
from homeassistant.components.cover import DATA_COMPONENT, CoverEntityFeature
from homeassistant.components.radio_frequency.helpers import async_send_command
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from rf_protocols.commands.ook import OOKCommand

from .conftest import RecordingTransmitter

pytestmark = pytest.mark.asyncio


async def test_other_ook_transmissions_unchanged(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    config_entry: MockConfigEntry,
) -> None:
    """Sending Dooya on a shared radio does not adjust another raw protocol."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    other = OOKCommand(
        frequency=433_920_000, timings=[1000, -500, 250, -10_000], repeat_count=4
    )
    await async_send_command(hass, transmitter.entity_id, other)
    await hass.services.async_call(
        "cover",
        "stop_cover",
        {"entity_id": hass.states.async_entity_ids("cover")[0]},
        blocking=True,
    )
    await async_send_command(hass, transmitter.entity_id, other)
    assert transmitter.commands[0] is other
    assert transmitter.commands[2] is other
    assert other.get_raw_timings() == [1000, -500, 250, -10_000]
    assert other.frequency == 433_920_000
    assert other.repeat_count == 4
    assert transmitter.commands[1].get_raw_timings()[:2] == [4672, -1508]


async def test_channels_and_commands(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    config_entry: MockConfigEntry,
) -> None:
    """Each numbered cover sends the same commands as the ESPHome covers."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    covers = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len(covers) == 3
    assert len({cover.device_id for cover in covers}) == 1
    assert {cover.unique_id for cover in covers} == {"123c14_1", "123c14_2", "123c14_3"}

    for channel in range(1, 4):
        entity_id = registry.async_get_entity_id(
            "cover", "harf_dooya", f"123c14_{channel}"
        )
        assert entity_id is not None
        cover = hass.data[DATA_COMPONENT].get_entity(entity_id)
        # Newer RF helpers require a resolved entity ID for availability tracking.
        assert cover._rf_transmitter_entity_id == transmitter.entity_id
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN
        assert state.attributes["assumed_state"] is True
        assert state.attributes["device_class"] == "blind"
        assert "current_position" not in state.attributes
        assert state.attributes["supported_features"] == (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )

        for service, button in (
            ("open_cover", 1),
            ("close_cover", 3),
            ("stop_cover", 5),
        ):
            await hass.services.async_call(
                "cover", service, {"entity_id": entity_id}, blocking=True
            )
            command = transmitter.commands[-1]
            timings = command.get_raw_timings()
            assert len(timings) == 82
            assert timings[:2] == [4672, -1508]
            bits = "".join("1" if mark == 680 else "0" for mark in timings[2::2])
            payload = int(bits, 2).to_bytes(5, "big")
            assert payload == bytes((0x12, 0x3C, 0x14, channel, button * 0x11))
            assert command.frequency == 433_934_000
            assert command.repeat_count == 2
            assert timings[-1] == -8144

        # Sending a command cannot tell us the blind's actual position.
        assert hass.states.get(entity_id).state == STATE_UNKNOWN

    assert len(transmitter.commands) == 9


async def test_transmitter_availability_rename_and_unload(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    config_entry: MockConfigEntry,
) -> None:
    """Covers follow the radio and keep working when its entity ID changes."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    covers = hass.states.async_entity_ids("cover")
    assert len(covers) == 3
    assert hass.states.get(transmitter.entity_id).state == STATE_UNKNOWN
    assert all(
        hass.states.get(entity_id).state == STATE_UNKNOWN for entity_id in covers
    )

    transmitter._attr_available = False
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == STATE_UNAVAILABLE for entity_id in covers
    )

    new_radio_id = "radio_frequency.renamed_radio"
    er.async_get(hass).async_update_entity(
        transmitter.entity_id, new_entity_id=new_radio_id
    )
    await hass.async_block_till_done()
    assert transmitter.entity_id == new_radio_id
    transmitter._attr_available = True
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == STATE_UNKNOWN for entity_id in covers
    )

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": covers[0]}, blocking=True
    )
    assert len(transmitter.commands) == 1

    # A second rename checks that reloading also renews the registry listener.
    er.async_get(hass).async_update_entity(
        transmitter.entity_id, new_entity_id="radio_frequency.renamed_again"
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": covers[0]}, blocking=True
    )
    assert len(transmitter.commands) == 2
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert all(
        hass.data[DATA_COMPONENT].get_entity(entity_id) is None for entity_id in covers
    )
    assert all(
        hass.states.get(entity_id).state == STATE_UNAVAILABLE for entity_id in covers
    )

    er.async_get(hass).async_update_entity(
        transmitter.entity_id, new_entity_id="radio_frequency.renamed_after_unload"
    )
    await hass.async_block_till_done()
    assert all(
        hass.data[DATA_COMPONENT].get_entity(entity_id) is None for entity_id in covers
    )

    # State changes after unloading must not recreate covers or retain listeners.
    transmitter._attr_available = False
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    transmitter._attr_available = True
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == STATE_UNAVAILABLE for entity_id in covers
    )
