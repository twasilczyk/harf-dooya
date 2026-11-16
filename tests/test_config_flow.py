"""Exercise setup through Home Assistant's config flow manager."""

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import RecordingTransmitter

pytestmark = pytest.mark.asyncio


async def manual_flow(hass, transmitter, remote_id="0x123C140", channels=3):
    result = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"transmitter": transmitter.entity_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": "none"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"remote_id": remote_id}
    )
    if result["type"] == FlowResultType.FORM and result["step_id"] == "finish":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"channels": channels}
        )
    return result


async def test_setup_and_duplicate_remote(hass, transmitter):
    result = await manual_flow(hass, transmitter)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "remote_id": 0x123C140,
        "entity_key": "123c140",
        "channels": 3,
        "transmitter": er.async_get(hass).async_get(transmitter.entity_id).id,
    }
    await hass.async_block_till_done()
    assert len(hass.states.async_all("cover")) == 4
    assert len(hass.states.async_all("button")) == 1
    assert transmitter.commands == []
    duplicate = await manual_flow(hass, transmitter, str(0x123C140))
    assert duplicate["reason"] == "already_configured"


@pytest.mark.parametrize("remote_id", ["not-a-remote", "0x10000000", "-1"])
async def test_invalid_remote_id(hass, transmitter, remote_id):
    result = await manual_flow(hass, transmitter, remote_id)
    assert result["errors"] == {"remote_id": "invalid_remote_id"}
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_invalid_fractional_channels(hass, transmitter):
    result = await manual_flow(hass, transmitter, channels=1.5)
    assert result["errors"] == {"channels": "invalid_channels"}
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_channels_above_max(hass, transmitter):
    with pytest.raises(InvalidData):
        await manual_flow(hass, transmitter, channels=16)


async def test_invalid_transmitter(hass, transmitter):
    result = await hass.config_entries.flow.async_init(
        "harf_dooya",
        context={"source": SOURCE_USER},
        data={"transmitter": "radio_frequency.missing"},
    )
    assert result["errors"] == {"transmitter": "invalid_transmitter"}
    hass.config_entries.flow.async_abort(result["flow_id"])


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


@pytest.mark.parametrize("storage", ["data", "options"])
async def test_saved_frequency_ignored(
    hass: HomeAssistant,
    transmitter: RecordingTransmitter,
    config_entry: MockConfigEntry,
    storage: str,
) -> None:
    """Saved frequency overrides cannot change the fixed carrier."""
    hass.config_entries.async_update_entry(
        config_entry,
        **{storage: {**getattr(config_entry, storage), "frequency": 433_921_000}},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for service in ("open_cover", "close_cover", "stop_cover"):
        await hass.services.async_call(
            "cover",
            service,
            {"entity_id": hass.states.async_entity_ids("cover")[0]},
            blocking=True,
        )
    assert [command.frequency for command in transmitter.commands] == [433_890_000] * 3
