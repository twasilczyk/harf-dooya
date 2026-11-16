"""Control Dooya blinds through a Home Assistant RF transmitter."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_KEY,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
    MAX_CHANNELS,
)
from .device import DooyaRemote

PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.EVENT, Platform.NUMBER]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Upgrade old 24-bit IDs without changing existing HA entity/device IDs.

    The missing high nibble of the remote ID cannot be inferred from a v1
    entry. Default to zero, preserving its previous channel 0–15 waveform;
    the Reconfigure form lets the user correct this prefix afterward.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        old_id = entry.data[CONF_REMOTE_ID]
        new_id = old_id << 4
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_REMOTE_ID: new_id,
                CONF_ENTITY_KEY: f"{old_id:06x}",
                CONF_CHANNELS: min(entry.data[CONF_CHANNELS], MAX_CHANNELS),
            },
            unique_id=f"{new_id:07x}",
            version=2,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the remote's covers and programming buttons."""
    transmitter_entity_id = er.async_validate_entity_id(
        er.async_get(hass), entry.data[CONF_TRANSMITTER]
    )

    async def async_transmitter_updated(
        event: Event[er.EventEntityRegistryUpdatedData],
    ) -> None:
        """Resolve the transmitter again when its entity ID changes."""
        if event.data["action"] == "update" and "entity_id" in event.data["changes"]:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(
        async_track_entity_registry_updated_event(
            hass, transmitter_entity_id, async_transmitter_updated
        )
    )
    entry.runtime_data = DooyaRemote(hass, entry)
    entry.async_on_unload(entry.runtime_data.async_stop)
    entry.runtime_data.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the remote's entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
