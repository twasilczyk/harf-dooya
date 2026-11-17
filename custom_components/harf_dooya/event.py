"""One physical remote press event entity, independent of TX availability."""

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_CHANNELS
from .device import COMMAND_NAMES
from .entity import DooyaDeviceEntity
from .protocol import Frame


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DooyaRemoteEvent(entry)])


def _event_type(channel: int, command: str) -> str:
    """Include the received target in HA's displayed event type."""
    target = "all_blinds" if channel == 0 else f"blind_{channel}"
    return f"{target}_{command}"


class DooyaRemoteEvent(DooyaDeviceEntity, EventEntity):
    _attr_name = "Remote action"
    _attr_icon = "mdi:remote"
    _attr_translation_key = "remote_action"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry)
        self._attr_event_types = [
            _event_type(channel, command)
            for channel in range(entry.data[CONF_CHANNELS] + 1)
            for command in COMMAND_NAMES.values()
        ]
        self._attr_unique_id = f"{self._identity}_remote_action"

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
            _event_type(frame.channel, COMMAND_NAMES[frame.command]),
            {
                "command": COMMAND_NAMES[frame.command],
                "remote_id": frame.remote_id,
                "channel": frame.channel,
                "broadcast": frame.channel == 0,
            },
        )
        self.async_write_ha_state()
