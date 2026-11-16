"""Exercise setup through Home Assistant's config flow manager."""

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.harf_dooya.protocol import make_command

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


async def test_reconfigure_channels(hass, transmitter, config_entry):
    """Resize an existing remote while keeping retained entities and the device."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    retained_id = registry.async_get_entity_id("cover", "harf_dooya", "123c14_1")
    registry.async_update_entity(retained_id, new_entity_id="cover.custom_blind")
    retained = registry.async_get("cover.custom_blind")
    original_data = dict(config_entry.data)
    original_unique_id = config_entry.unique_id
    for count in (15, 1, 3):
        previous_count = config_entry.data["channels"]
        result = await hass.config_entries.flow.async_init(
            "harf_dooya",
            context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id},
        )
        assert result["data_schema"]({
            "transmitter": transmitter.entity_id, "receiver": "none"
        })["channels"] == previous_count
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"transmitter": transmitter.entity_id, "receiver": "none", "channels": count},
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert config_entry.data == {**original_data, "channels": count}
        assert config_entry.unique_id == original_unique_id
        covers = [
            e for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
            if e.domain == "cover"
        ]
        assert {e.unique_id for e in covers} == {
            f"123c14_{channel}" for channel in range(count + 1)
        }
        assert set(hass.states.async_entity_ids("cover")) == {e.entity_id for e in covers}
        assert registry.async_get("cover.custom_blind").id == retained.id
        assert all(e.device_id == retained.device_id for e in covers)
        assert len(hass.states.async_entity_ids("button")) == 1
        added_id = registry.async_get_entity_id("cover", "harf_dooya", f"123c14_{count}")
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": added_id}, blocking=True
        )
        assert transmitter.commands[-1].get_raw_timings() == make_command(
            config_entry.data["remote_id"], count, 1
        ).get_raw_timings()


@pytest.mark.parametrize("channels", [0, 16, 1.5])
async def test_reconfigure_invalid_channels(hass, transmitter, config_entry, channels):
    """Reject channel counts outside the supported whole-number range."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    original_data = dict(config_entry.data)
    result = await hass.config_entries.flow.async_init(
        "harf_dooya",
        context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id},
    )
    user_input = {
        "transmitter": transmitter.entity_id, "receiver": "none", "channels": channels
    }
    if channels == 1.5:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
        assert result["errors"] == {"channels": "invalid_channels"}
    else:
        with pytest.raises(InvalidData):
            await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert config_entry.data == original_data
    hass.config_entries.flow.async_abort(result["flow_id"])
