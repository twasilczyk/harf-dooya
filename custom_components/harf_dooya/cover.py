"""Dooya covers with open, close and stop controls."""

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.radio_frequency import (
    RadioFrequencyTransmitterConsumerEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import (
    BUTTON_CLOSE,
    BUTTON_OPEN,
    BUTTON_STOP,
    CONF_CHANNELS,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
    DOMAIN,
)
from .protocol import make_command

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one cover for each numbered remote channel."""
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
    async_add_entities(
        DooyaCover(entry, channel, transmitter_entity_id)
        for channel in range(1, entry.data[CONF_CHANNELS] + 1)
    )


class DooyaCover(RadioFrequencyTransmitterConsumerEntity, CoverEntity):
    """A blind controlled by one channel of a Dooya remote."""

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_is_closed = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self, entry: ConfigEntry, channel: int, transmitter_entity_id: str
    ) -> None:
        """Bind the cover to its remote, channel and RF transmitter."""
        self._remote_id = entry.data[CONF_REMOTE_ID]
        self._channel = channel
        self._rf_transmitter_entity_id = transmitter_entity_id
        # Home Assistant 2026.9 uses the UUID-based consumer helper.
        self._rf_transmitter_entity_id_or_uuid = entry.data[CONF_TRANSMITTER]
        self._attr_unique_id = f"{self._remote_id:06x}_{channel}"
        self._attr_name = f"Blind {channel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._remote_id:06x}")},
            manufacturer="Dooya",
            model="RF remote",
            name=entry.title,
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_OPEN)
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_CLOSE)
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_STOP)
        )
