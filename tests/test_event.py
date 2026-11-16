"""Received action labels identify numbered blinds and the broadcast channel."""

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from .test_decoder import remote_frame
from .test_learning import emit
from .test_remote_state import setup_remote


@pytest.mark.parametrize("channel", [0, 1, 3, 15])
@pytest.mark.parametrize("command,name,label", [
    (1, "open", "Open"), (3, "close", "Close"),
    (5, "stop", "Stop"), (12, "p2", "P2"),
])
async def test_event_target_and_translated_label(
    hass, transmitter, config_entry, receiver, channel, command, name, label
):
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, "channels": 15}
    )
    _, event = await setup_remote(hass, config_entry, receiver)
    await emit(hass, receiver, remote_frame(channel=channel, command=command) * 3)
    state = hass.states.get(event)
    target = "all_blinds" if channel == 0 else f"blind_{channel}"
    event_type = f"{target}_{name}"
    assert state.attributes["event_type"] == event_type
    assert event_type in state.attributes["event_types"]
    assert state.attributes["command"] == name
    assert state.attributes["channel"] == channel
    assert state.attributes["broadcast"] is (channel == 0)
    registry_entry = er.async_get(hass).async_get(event)
    assert registry_entry.translation_key == "remote_action"
    assert registry_entry.unique_id == "123c14_remote_action"
    translations = await async_get_translations(hass, "en", "entity", {"harf_dooya"})
    key = f"component.harf_dooya.entity.event.remote_action.state_attributes.event_type.state.{event_type}"
    display_target = "All blinds" if channel == 0 else f"Blind {channel}"
    assert translations[key] == f"{display_target}: {label}"
    # Repeated frames remain one event, and received commands never retransmit.
    await emit(hass, receiver, remote_frame(channel=channel, command=command) * 2)
    assert hass.states.get(event).state == state.state
    assert not transmitter.commands
