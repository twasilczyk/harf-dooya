"""Per-blind full travel time, stored independently of transient positions."""

import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_KEY,
    CONF_REMOTE_ID,
    CONF_TRAVEL_TIMES,
    DOMAIN,
)
from .entity import DooyaDeviceEntity
from .motion import MAX_TRAVEL_TIME, MIN_TRAVEL_TIME


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    registry = er.async_get(hass)
    identity = entry.data.get(CONF_ENTITY_KEY, f"{entry.data[CONF_REMOTE_ID]:07x}")
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "number" or entity.platform != DOMAIN:
            continue
        prefix, separator, channel = entity.unique_id.rpartition("_travel_time_")
        if separator and prefix == identity and channel.isdecimal():
            if int(channel) > entry.data[CONF_CHANNELS]:
                registry.async_remove(entity.entity_id)
    async_add_entities(
        DooyaTravelTime(entry, channel)
        for channel in range(1, entry.data[CONF_CHANNELS] + 1)
    )


class DooyaTravelTime(DooyaDeviceEntity, NumberEntity):
    """A configuration value; changing it never moves the blind or reloads it."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_TRAVEL_TIME
    _attr_native_max_value = MAX_TRAVEL_TIME
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry: ConfigEntry, channel: int) -> None:
        super().__init__(entry)
        self._entry = entry
        self._channel = channel
        self._attr_unique_id = f"{self._identity}_travel_time_{channel}"
        self._attr_name = f"Blind {channel} travel time"

    @property
    def native_value(self) -> float:
        return self._remote.travel_time(self._channel)

    async def async_set_native_value(self, value: float) -> None:
        if not math.isfinite(value) or not MIN_TRAVEL_TIME <= value <= MAX_TRAVEL_TIME:
            raise HomeAssistantError("Travel time must be between 1 and 3600 seconds")
        times = dict(self._entry.options.get(CONF_TRAVEL_TIMES, {}))
        times[str(self._channel)] = value
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_TRAVEL_TIMES: times}
        )
        self.async_write_ha_state()
