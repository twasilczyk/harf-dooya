"""Dooya covers with open, close and stop controls."""

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    BUTTON_CLOSE,
    BUTTON_OPEN,
    BUTTON_STOP,
    CONF_CHANNELS,
    CONF_TRANSMITTER,
)
from .entity import DooyaEntity
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

    async_add_entities(
        DooyaCover(entry, channel, transmitter_entity_id)
        for channel in range(1, entry.data[CONF_CHANNELS] + 1)
    )


class DooyaCover(DooyaEntity, CoverEntity):
    """A blind controlled by one channel of a Dooya remote."""

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_assumed_state = True
    _attr_is_closed = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self, entry: ConfigEntry, channel: int, transmitter_entity_id: str
    ) -> None:
        """Name the cover for its remote channel."""
        super().__init__(entry, channel, transmitter_entity_id)
        self._attr_unique_id = f"{self._remote_id:06x}_{channel}"
        self._attr_name = f"Blind {channel}"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_OPEN, self._frequency)
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_CLOSE, self._frequency)
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the blind."""
        await self._send_command(
            make_command(self._remote_id, self._channel, BUTTON_STOP, self._frequency)
        )
