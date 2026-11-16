"""One P2 programming button for each Dooya remote."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BUTTON_PROGRAM, CONF_REMOTE_ID, CONF_TRANSMITTER
from .entity import DooyaEntity
from .protocol import make_programming_command

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the remote's single P2 button and retire the old per-channel entities."""
    registry = er.async_get(hass)
    remote_id = f"{entry.data[CONF_REMOTE_ID]:06x}"
    unique_id = f"{remote_id}_p2"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "button" or entity.platform != "harf_dooya":
            continue
        prefix, separator, suffix = entity.unique_id.rpartition("_")
        if not separator or suffix != "p2" or not prefix.startswith(f"{remote_id}_"):
            continue
        channel = prefix[len(remote_id) + 1 :]
        if not channel.isdecimal():
            continue
        if (
            channel == "1"
            and registry.async_get_entity_id("button", "harf_dooya", unique_id) is None
        ):
            registry.async_update_entity(entity.entity_id, new_unique_id=unique_id)
        else:
            registry.async_remove(entity.entity_id)
    transmitter_entity_id = er.async_validate_entity_id(
        er.async_get(hass), entry.data[CONF_TRANSMITTER]
    )
    async_add_entities([DooyaProgrammingButton(entry, transmitter_entity_id)])


class DooyaProgrammingButton(DooyaEntity, ButtonEntity):
    """Send the P2 command using the remote's existing RF binding."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, transmitter_entity_id: str) -> None:
        """Identify the remote's programming button on the existing device."""
        super().__init__(entry, 1, transmitter_entity_id)
        self._attr_unique_id = f"{self._remote_id:06x}_p2"
        self._attr_name = "P2 (Programming)"

    async def async_press(self) -> None:
        """Transmit four P2 frames."""
        await self._async_transmit(
            BUTTON_PROGRAM,
            make_programming_command(self._remote_id, self._channel, self._frequency),
        )
