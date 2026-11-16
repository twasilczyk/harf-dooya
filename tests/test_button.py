"""Exercise P2 through Home Assistant's button platform."""

import pytest
from homeassistant.components.button import DATA_COMPONENT
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import RecordingTransmitter


@pytest.mark.parametrize("frequency", [433_934_000, 433_921_000])
async def test_programming_button(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    config_entry: MockConfigEntry,
    frequency: int,
) -> None:
    """One configuration button per remote shares the covers' device and radio."""
    if frequency != 433_934_000:
        hass.config_entries.async_update_entry(
            config_entry, options={"frequency": frequency}
        )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    buttons = hass.states.async_entity_ids("button")
    assert len(buttons) == 1
    entity_id = registry.async_get_entity_id("button", "harf_dooya", "123c14_p2")
    assert entity_id in buttons
    entry = registry.async_get(entity_id)
    cover_id = registry.async_get_entity_id("cover", "harf_dooya", "123c14_1")
    assert entry.device_id == registry.async_get(cover_id).device_id
    assert entry.entity_category == EntityCategory.CONFIG
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    command = transmitter.commands[-1]
    assert command.frequency == frequency
    assert command.repeat_count == 3
    timings = command.get_raw_timings()
    bits = "".join("1" if mark == 680 else "0" for mark in timings[2::2])
    assert int(bits, 2).to_bytes(5, "big") == bytes((0x12, 0x3C, 0x14, 1, 0xCC))
    assert sum(map(abs, timings)) == 57_300

    transmitter._attr_available = False
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == STATE_UNAVAILABLE for entity_id in buttons
    )
    registry.async_update_entity(
        transmitter.entity_id, new_entity_id="radio_frequency.renamed_p2_radio"
    )
    await hass.async_block_till_done()
    transmitter._attr_available = True
    transmitter.async_write_ha_state()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": buttons[0]}, blocking=True
    )
    assert len(transmitter.commands) == 2
    assert transmitter.commands[-1].frequency == frequency
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert all(
        hass.data[DATA_COMPONENT].get_entity(entity_id) is None for entity_id in buttons
    )


async def test_previous_per_channel_buttons_consolidated(
    hass, transmitter, config_entry
):
    """Retain channel one's registry entity and retire duplicate programming keys."""
    registry = er.async_get(hass)
    old = [
        registry.async_get_or_create(
            "button", "harf_dooya", f"123c14_{i}_p2", config_entry=config_entry
        )
        for i in range(1, 4)
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        registry.async_get_entity_id("button", "harf_dooya", "123c14_p2")
        == old[0].entity_id
    )
    assert registry.async_get(old[1].entity_id) is None
    assert registry.async_get(old[2].entity_id) is None
    assert len(hass.states.async_entity_ids("button")) == 1
    assert not transmitter.commands
