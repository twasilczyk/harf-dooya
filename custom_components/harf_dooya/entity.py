"""Shared identity and RF binding for Dooya entities."""

from homeassistant.components.radio_frequency import (
    RadioFrequencyTransmitterConsumerEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_FREQUENCY, CONF_REMOTE_ID, CONF_TRANSMITTER, DOMAIN, FREQUENCY


class DooyaEntity(RadioFrequencyTransmitterConsumerEntity):
    """An entity bound to a numbered remote channel and its RF transmitter."""

    _attr_has_entity_name = True

    def __init__(
        self, entry: ConfigEntry, channel: int, transmitter_entity_id: str
    ) -> None:
        """Bind the entity to its remote, channel and RF transmitter."""
        self._remote_id = entry.data[CONF_REMOTE_ID]
        self._channel = channel
        self._frequency = entry.options.get(
            CONF_FREQUENCY, entry.data.get(CONF_FREQUENCY, FREQUENCY)
        )
        self._rf_transmitter_entity_id = transmitter_entity_id
        # Home Assistant 2026.9 uses the UUID-based consumer helper.
        self._rf_transmitter_entity_id_or_uuid = entry.data[CONF_TRANSMITTER]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._remote_id:06x}")},
            manufacturer="Dooya",
            model="RF remote",
            name=entry.title,
        )
