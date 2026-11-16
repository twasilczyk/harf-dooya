"""Shared identity and RF binding for Dooya entities."""

from homeassistant.components.radio_frequency import (
    RadioFrequencyTransmitterConsumerEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from rf_protocols.commands.ook import OOKCommand

from .const import CONF_FREQUENCY, CONF_REMOTE_ID, CONF_TRANSMITTER, DOMAIN, FREQUENCY


class DooyaDeviceEntity(Entity):
    """Device identity shared by transmit and receive entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._remote_id = entry.data[CONF_REMOTE_ID]
        self._remote = entry.runtime_data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._remote_id:06x}")},
            manufacturer="Dooya",
            model="RF remote",
            name=entry.title,
        )


class DooyaEntity(RadioFrequencyTransmitterConsumerEntity, DooyaDeviceEntity):
    """A transmit entity bound to a remote channel and RF transmitter."""

    def __init__(
        self, entry: ConfigEntry, channel: int, transmitter_entity_id: str
    ) -> None:
        DooyaDeviceEntity.__init__(self, entry)
        self._channel = channel
        self._frequency = entry.options.get(
            CONF_FREQUENCY, entry.data.get(CONF_FREQUENCY, FREQUENCY)
        )
        self._rf_transmitter_entity_id = transmitter_entity_id
        # Home Assistant 2026.9 uses the UUID-based consumer helper.
        self._rf_transmitter_entity_id_or_uuid = entry.data[CONF_TRANSMITTER]

    async def _async_transmit(self, command: int, rf_command: OOKCommand) -> None:
        await self._remote.async_transmit(
            self._channel, command, rf_command, self._send_command
        )
