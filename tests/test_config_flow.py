"""Exercise setup through Home Assistant's config flow manager."""

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from .conftest import RecordingTransmitter

pytestmark = pytest.mark.asyncio


async def test_setup_and_duplicate_remote(
    hass: HomeAssistant, transmitter: RecordingTransmitter
) -> None:
    """Setup normalizes the remote ID and rejects its decimal equivalent."""
    result = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"remote_id": "0x123C14", "channels": 3, "transmitter": transmitter.entity_id},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    radio_entry = er.async_get(hass).async_get(transmitter.entity_id)
    assert radio_entry is not None
    assert result["data"] == {
        "remote_id": 0x123C14,
        "channels": 3,
        "transmitter": radio_entry.id,
    }
    await hass.async_block_till_done()
    assert len(hass.states.async_all("cover")) == 3

    duplicate = await hass.config_entries.flow.async_init(
        "harf_dooya",
        context={"source": SOURCE_USER},
        data={
            "remote_id": str(0x123C14),
            "channels": 3,
            "transmitter": transmitter.entity_id,
        },
    )
    assert duplicate["type"] == FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"remote_id": "not-a-remote"}, "invalid_remote_id"),
        ({"remote_id": "0x1000000"}, "invalid_remote_id"),
        ({"channels": 0}, "invalid_channels"),
        ({"channels": 256}, "invalid_channels"),
        ({"channels": 1.5}, "invalid_channels"),
        ({"transmitter": "radio_frequency.missing"}, "invalid_transmitter"),
    ],
)
async def test_invalid_setup(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    overrides: dict[str, str | float],
    error: str,
) -> None:
    """Invalid input remains in the form instead of producing broken entities."""
    result = await hass.config_entries.flow.async_init(
        "harf_dooya",
        context={"source": SOURCE_USER},
        data={
            "remote_id": "0x123C14",
            "channels": 3,
            "transmitter": transmitter.entity_id,
            **overrides,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert error in result["errors"].values()


async def test_no_transmitters(hass: HomeAssistant) -> None:
    """Setup explains when no radio has been added."""
    result = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_transmitters"


async def test_no_compatible_transmitters(
    hass: HomeAssistant, transmitter: RecordingTransmitter
) -> None:
    """A radio on another band is not offered for Dooya commands."""
    transmitter.frequency_ranges = [(300_000_000, 348_000_000)]
    result = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_compatible_transmitters"
