"""Control Dooya blinds through a Home Assistant RF transmitter."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import CONF_TRANSMITTER
from .device import DooyaRemote

PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.EVENT]


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
