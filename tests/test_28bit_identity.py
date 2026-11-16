"""Regression tests for 28-bit remote identities, four-bit channels, and migration."""

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.harf_dooya.protocol import decode_frame, encode_timings, make_command
from .test_decoder import remote_frame


@pytest.mark.parametrize(
    ("remote_id", "channel", "payload"),
    [
        (0x123C140, 2, "12 3C 14 02 33"),
        (0x7B31154, 1, "7B 31 15 41 33"),
        (0x4231157, 0, "42 31 15 70 33"),
        (0x4231157, 4, "42 31 15 74 33"),
        (0x4231157, 15, "42 31 15 7F 33"),
    ],
)
def test_wire_layout(remote_id, channel, payload):
    command = make_command(remote_id, channel, 3)
    bits = "".join("1" if high == 680 else "0" for high in command.get_raw_timings()[2::2])
    assert int(bits, 2).to_bytes(5, "big") == bytes.fromhex(payload)
    assert decode_frame(remote_frame(remote_id, channel, 3)).remote_id == remote_id


@pytest.mark.parametrize("remote_id,channel", [(0x10000000, 1), (-1, 1), (0x123C140, 16), (0x123C140, -1)])
def test_reject_invalid_field_sizes(remote_id, channel):
    with pytest.raises(ValueError):
        encode_timings(remote_id, channel, 5)


@pytest.mark.asyncio
async def test_migration_and_reconfigure_preserve_entity_ids(hass: HomeAssistant, transmitter, config_entry):
    assert config_entry.version == 1
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.version == 2
    assert config_entry.data["remote_id"] == 0x123C140
    assert config_entry.data["entity_key"] == "123c14"
    assert config_entry.unique_id == "123c140"
    registry = er.async_get(hass)
    before = {e.entity_id for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)}
    assert registry.async_get_entity_id("cover", "harf_dooya", "123c14_2") in before

    flow = await hass.config_entries.flow.async_init(
        "harf_dooya", context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"transmitter": transmitter.entity_id, "receiver": "none", "remote_id": "0x123C147"},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert config_entry.data["remote_id"] == 0x123C147
    assert config_entry.data["entity_key"] == "123c14"
    assert config_entry.unique_id == "123c147"
    assert {e.entity_id for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)} == before

    channel_two = registry.async_get_entity_id("cover", "harf_dooya", "123c14_2")
    await hass.services.async_call("cover", "close_cover", {"entity_id": channel_two}, blocking=True)
    bits = "".join("1" if high == 680 else "0" for high in transmitter.commands[-1].get_raw_timings()[2::2])
    assert int(bits, 2).to_bytes(5, "big") == bytes.fromhex("12 3C 14 72 33")
