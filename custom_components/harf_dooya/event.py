"""One physical remote press event entity, independent of TX availability."""

from typing import ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .device import COMMAND_NAMES
from .entity import DooyaDeviceEntity
from .protocol import Frame


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DooyaRemoteEvent(entry)])


class DooyaRemoteEvent(DooyaDeviceEntity, EventEntity):
    _attr_name = "Remote action"
    _attr_event_types: ClassVar[list[str]] = list(COMMAND_NAMES.values())

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{self._remote_id:06x}_remote_action"

    @property
    def available(self) -> bool:
        return self._remote.receiver_available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._remote.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self._remote.async_add_event_listener(self._receive))

    @callback
    def _receive(self, frame: Frame) -> None:
        self._trigger_event(
            COMMAND_NAMES[frame.command],
            {
                "remote_id": frame.remote_id,
                "channel": frame.channel,
                "broadcast": frame.channel == 0,
            },
        )
        self.async_write_ha_state()
