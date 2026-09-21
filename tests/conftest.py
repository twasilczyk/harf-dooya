"""Home Assistant fixtures for the Dooya integration."""

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from homeassistant.components.radio_frequency import (
    DATA_COMPONENT,
    RadioFrequencyCommand,
    RadioFrequencyTransmitterEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RecordingTransmitter(RadioFrequencyTransmitterEntity):
    """Use HA's RF platform while recording commands at the radio boundary."""

    _attr_unique_id = "dooya_test_radio"
    _attr_name = "Dooya test radio"

    def __init__(self) -> None:
        self.commands: list[RadioFrequencyCommand] = []
        self.frequency_ranges = [(433_000_000, 434_000_000)]

    @property
    def supported_frequency_ranges(self) -> list[tuple[int, int]]:
        return self.frequency_ranges

    async def async_send_command(self, command: RadioFrequencyCommand) -> None:
        self.commands.append(command)


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load this integration from the source tree."""


@pytest_asyncio.fixture
async def transmitter(hass: HomeAssistant) -> RecordingTransmitter:
    """Register a usable RF transmitter with a real entity registry entry."""
    assert await async_setup_component(hass, "radio_frequency", {})
    radio = RecordingTransmitter()
    await hass.data[DATA_COMPONENT].async_add_entities([radio])
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(radio.entity_id) is not None
    return radio


@pytest.fixture
def config_entry(
    hass: HomeAssistant, transmitter: RecordingTransmitter
) -> MockConfigEntry:
    """A three-channel remote linked by the transmitter's registry UUID."""
    radio_entry = er.async_get(hass).async_get(transmitter.entity_id)
    assert radio_entry is not None
    entry = MockConfigEntry(
        domain="harf_dooya",
        title="Dooya RF 123C14",
        data={"remote_id": 0x123C14, "channels": 3, "transmitter": radio_entry.id},
    )
    entry.add_to_hass(hass)
    return entry
