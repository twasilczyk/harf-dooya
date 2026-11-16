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
    CONF_ENTITY_KEY,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
    DOMAIN,
)
from .entity import DooyaEntity
from .protocol import make_command

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the broadcast cover and one cover for each numbered remote channel."""
    registry = er.async_get(hass)
    identity = entry.data.get(CONF_ENTITY_KEY, f"{entry.data[CONF_REMOTE_ID]:07x}")
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "cover" or entity.platform != DOMAIN:
            continue
        prefix, separator, channel = entity.unique_id.rpartition("_")
        if (
            separator
            and prefix == identity
            and channel.isdecimal()
            and int(channel) > entry.data[CONF_CHANNELS]
        ):
            registry.async_remove(entity.entity_id)
    transmitter_entity_id = er.async_validate_entity_id(
        registry, entry.data[CONF_TRANSMITTER]
    )

    async_add_entities(
        DooyaCover(entry, channel, transmitter_entity_id)
        for channel in range(entry.data[CONF_CHANNELS] + 1)
    )


class DooyaCover(DooyaEntity, CoverEntity):
    """A blind controlled by one channel of a Dooya remote."""

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_assumed_state = True
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self, entry: ConfigEntry, channel: int, transmitter_entity_id: str
    ) -> None:
        """Name the cover for its remote channel."""
        super().__init__(entry, channel, transmitter_entity_id)
        self._attr_unique_id = f"{self._identity}_{channel}"
        self._attr_name = "All blinds" if channel == 0 else f"Blind {channel}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._remote.async_add_listener(self.async_write_ha_state))

    @property
    def current_cover_position(self) -> int:
        """Return the assumed position, not measured motor feedback."""
        return self._remote.assumed_positions[self._channel]

    @property
    def is_closed(self) -> bool:
        """Only the assumed fully closed position is closed."""
        return self.current_cover_position == 0

    @property
    def extra_state_attributes(self) -> dict:
        """Last observed/requested action; never verified motor position."""
        return self._remote.observations.get(self._channel, {})

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the blind."""
        await self._async_transmit(
            BUTTON_OPEN,
            make_command(self._remote_id, self._channel, BUTTON_OPEN),
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the blind."""
        await self._async_transmit(
            BUTTON_CLOSE,
            make_command(self._remote_id, self._channel, BUTTON_CLOSE),
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the blind."""
        await self._async_transmit(
            BUTTON_STOP,
            make_command(self._remote_id, self._channel, BUTTON_STOP),
        )
