"""Set up a Dooya remote and its channels."""

from typing import Any

import voluptuous as vol
from homeassistant.components.radio_frequency import (
    ModulationType,
    async_get_transmitters,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CHANNELS,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
    DOMAIN,
    FREQUENCY,
    MAX_CHANNELS,
    MAX_REMOTE_ID,
)


class DooyaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure the transmitter and an existing remote ID."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the RF transmitter, remote ID and number of channels."""
        try:
            transmitters = async_get_transmitters(
                self.hass, FREQUENCY, ModulationType.OOK
            )
        except HomeAssistantError:
            return self.async_abort(reason="no_transmitters")
        if not transmitters:
            return self.async_abort(reason="no_compatible_transmitters")

        errors: dict[str, str] = {}
        if user_input is not None:
            remote_text = str(user_input[CONF_REMOTE_ID]).strip()
            try:
                remote_id = int(
                    remote_text, 16 if remote_text.lower().startswith("0x") else 10
                )
                if not 0 <= remote_id <= MAX_REMOTE_ID:
                    raise ValueError
            except ValueError:
                errors[CONF_REMOTE_ID] = "invalid_remote_id"

            channels = user_input[CONF_CHANNELS]
            if (
                isinstance(channels, bool)
                or not isinstance(channels, (int, float))
                or not 1 <= channels <= MAX_CHANNELS
                or int(channels) != channels
            ):
                errors[CONF_CHANNELS] = "invalid_channels"

            transmitter = er.async_get(self.hass).async_get(
                user_input[CONF_TRANSMITTER]
            )
            if transmitter is None or transmitter.entity_id not in transmitters:
                errors[CONF_TRANSMITTER] = "invalid_transmitter"

            if not errors:
                await self.async_set_unique_id(f"{remote_id:06x}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Dooya RF {remote_id:06X}",
                    data={
                        CONF_TRANSMITTER: transmitter.id,
                        CONF_REMOTE_ID: remote_id,
                        CONF_CHANNELS: int(channels),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_TRANSMITTER): selector.EntitySelector(
                    selector.EntitySelectorConfig(include_entities=transmitters)
                ),
                vol.Required(CONF_REMOTE_ID): selector.TextSelector(),
                vol.Required(CONF_CHANNELS, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=MAX_CHANNELS,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
