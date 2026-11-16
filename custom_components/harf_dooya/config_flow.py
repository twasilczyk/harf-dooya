"""Select radios, learn or enter a Dooya ID, then choose its channel count."""

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.radio_frequency import (
    ModulationType,
    async_get_transmitters,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_KEY,
    CONF_LEARN,
    CONF_RECEIVER,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
    DOMAIN,
    FREQUENCY,
    LEARN_TIMEOUT,
    MAX_CHANNELS,
    MAX_REMOTE_ID,
)
from .protocol import FrameStream
from .receiver import FrameSource, async_get_source, async_list_sources

NO_RECEIVER = "none"
_LOGGER = logging.getLogger(__name__)


class DooyaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Learn an address without changing existing entry or entity identities."""

    VERSION = 2

    def __init__(self) -> None:
        self._transmitter = ""
        self._receiver: str | None = None
        self._remote_id: int | None = None
        self._learn_task: asyncio.Task[int | None] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            transmitters = async_get_transmitters(
                self.hass, FREQUENCY, ModulationType.OOK
            )
        except HomeAssistantError:
            return self.async_abort(reason="no_transmitters")
        if not transmitters:
            return self.async_abort(reason="no_compatible_transmitters")
        errors = {}
        if user_input is not None:
            transmitter = er.async_get(self.hass).async_get(
                user_input[CONF_TRANSMITTER]
            )
            if transmitter is None or transmitter.entity_id not in transmitters:
                errors[CONF_TRANSMITTER] = "invalid_transmitter"
            else:
                self._transmitter = transmitter.id
                return await self.async_step_receiver()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRANSMITTER): selector.EntitySelector(
                        selector.EntitySelectorConfig(include_entities=transmitters)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_receiver(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            selected = user_input[CONF_RECEIVER]
            self._receiver = None if selected == NO_RECEIVER else selected
            if self._receiver is None:
                return await self.async_step_manual()
            if async_get_source(self.hass, self._receiver) is None:
                errors[CONF_RECEIVER] = "receiver_unusable"
            else:
                return await self.async_step_learn()
        sources = await async_list_sources(self.hass)
        transmitter = er.async_get(self.hass).async_get(self._transmitter)
        default = next(
            (
                source.id
                for source in sources
                if transmitter and source.entry.entry_id == transmitter.config_entry_id
            ),
            sources[0].id if len(sources) == 1 else NO_RECEIVER,
        )
        return self.async_show_form(
            step_id="receiver",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RECEIVER, default=default
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=NO_RECEIVER,
                                    label="No receiver (enter remote ID manually)",
                                ),
                                *(
                                    selector.SelectOptionDict(value=s.id, label=s.name)
                                    for s in sources
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_learn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        starting = self._learn_task is None
        if starting:
            source = async_get_source(self.hass, self._receiver or "")
            if source is None:
                return await self.async_step_retry()
            self._learn_task = self.hass.async_create_background_task(
                self._async_learn(source), "Dooya RF learning"
            )
        # Always enter the progress step, even if eager task execution failed
        # immediately. Otherwise HA can pass the receiver form to the retry step.
        if starting or not self._learn_task.done():
            return self.async_show_progress(
                step_id="learn",
                progress_action="listening",
                progress_task=self._learn_task,
            )
        self._remote_id = self._learn_task.result()
        self._learn_task = None
        return self.async_show_progress_done(
            next_step_id="retry" if self._remote_id is None else "finish"
        )

    async def _async_learn(self, source: FrameSource) -> int | None:
        found: asyncio.Future[int | None] = self.hass.loop.create_future()
        decoder = FrameStream()

        @callback
        def receive(timings: list[int]) -> None:
            for frame in decoder.feed(timings):
                if not found.done():
                    found.set_result(frame.remote_id)

        @callback
        def availability(available: bool) -> None:
            if not available and not found.done():
                found.set_result(None)

        try:
            unsubscribe = source.async_subscribe(receive, availability)
        except Exception:
            _LOGGER.debug(
                "Could not subscribe to receiver %s", source.id, exc_info=True
            )
            return None
        try:
            async with asyncio.timeout(LEARN_TIMEOUT):
                return await found
        except TimeoutError:
            return None
        finally:
            unsubscribe()

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_LEARN]:
                return await self.async_step_receiver()
            self._receiver = None
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="retry",
            data_schema=vol.Schema(
                {vol.Required(CONF_LEARN, default=True): selector.BooleanSelector()}
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            text = str(user_input[CONF_REMOTE_ID]).strip()
            try:
                code = int(text, 16 if text.lower().startswith("0x") else 10)
                if not 0 <= code <= MAX_REMOTE_ID:
                    raise ValueError
            except ValueError:
                errors[CONF_REMOTE_ID] = "invalid_remote_id"
            else:
                self._remote_id = code
                return await self.async_step_finish()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_REMOTE_ID): selector.TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._remote_id is not None
        # Detect duplicates even when a legacy v1 entry has not loaded yet.
        if any(
            other.version == 1
            and other.data[CONF_REMOTE_ID] << 4 == self._remote_id
            for other in self.hass.config_entries.async_entries(DOMAIN)
        ):
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(f"{self._remote_id:07x}")
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is None:
            return self._channel_form(errors)
        channels = user_input[CONF_CHANNELS]
        if (
            isinstance(channels, bool)
            or not isinstance(channels, (int, float))
            or not 1 <= channels <= MAX_CHANNELS
            or int(channels) != channels
        ):
            return self._channel_form({CONF_CHANNELS: "invalid_channels"})
        data = {
            CONF_REMOTE_ID: self._remote_id,
            CONF_ENTITY_KEY: f"{self._remote_id:07x}",
            CONF_TRANSMITTER: self._transmitter,
            CONF_CHANNELS: int(channels),
        }
        if self._receiver is not None:
            data[CONF_RECEIVER] = self._receiver
        return self.async_create_entry(
            title=f"Dooya RF {self._remote_id:07X}",
            data=data,
        )

    def _channel_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="finish",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHANNELS, default=1): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=MAX_CHANNELS,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update remote settings while preserving retained entity identities."""
        entry = self._get_reconfigure_entry()
        try:
            transmitters = async_get_transmitters(
                self.hass,
                FREQUENCY,
                ModulationType.OOK,
            )
        except HomeAssistantError:
            return self.async_abort(reason="no_transmitters")
        if not transmitters:
            return self.async_abort(reason="no_compatible_transmitters")
        registry = er.async_get(self.hass)
        configured_transmitter = registry.async_get(entry.data[CONF_TRANSMITTER])
        errors = {}
        if user_input is not None:
            transmitter = registry.async_get(user_input[CONF_TRANSMITTER])
            if transmitter is None or transmitter.entity_id not in transmitters:
                errors[CONF_TRANSMITTER] = "invalid_transmitter"
            remote_text = str(
                user_input.get(CONF_REMOTE_ID, f"0x{entry.data[CONF_REMOTE_ID]:07X}")
            ).strip()
            try:
                remote_id = int(
                    remote_text, 16 if remote_text.lower().startswith("0x") else 10
                )
                if not 0 <= remote_id <= MAX_REMOTE_ID:
                    raise ValueError
            except ValueError:
                errors[CONF_REMOTE_ID] = "invalid_remote_id"
            else:
                new_unique_id = f"{remote_id:07x}"
                if any(
                    other.entry_id != entry.entry_id
                    and (
                        other.unique_id == new_unique_id
                        or (
                            other.version == 1
                            and other.data[CONF_REMOTE_ID] << 4 == remote_id
                        )
                    )
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    errors[CONF_REMOTE_ID] = "already_configured"
            channels = user_input.get(CONF_CHANNELS, entry.data[CONF_CHANNELS])
            if (
                isinstance(channels, bool)
                or not isinstance(channels, (int, float))
                or not 1 <= channels <= MAX_CHANNELS
                or int(channels) != channels
            ):
                errors[CONF_CHANNELS] = "invalid_channels"
            selected = user_input[CONF_RECEIVER]
            if (
                selected != NO_RECEIVER
                and selected != entry.data.get(CONF_RECEIVER)
                and async_get_source(self.hass, selected) is None
            ):
                errors[CONF_RECEIVER] = "receiver_unusable"
            if not errors:
                data = dict(entry.data)
                data[CONF_TRANSMITTER] = transmitter.id
                data[CONF_REMOTE_ID] = remote_id
                data[CONF_CHANNELS] = int(channels)
                data.pop(CONF_RECEIVER, None)
                if selected != NO_RECEIVER:
                    data[CONF_RECEIVER] = selected
                return self.async_update_reload_and_abort(
                    entry, data=data, unique_id=f"{remote_id:07x}"
                )
        sources = await async_list_sources(self.hass)
        configured = entry.data.get(CONF_RECEIVER, NO_RECEIVER)
        options = [selector.SelectOptionDict(value=NO_RECEIVER, label="No receiver")]
        options.extend(
            selector.SelectOptionDict(value=s.id, label=s.name) for s in sources
        )
        if configured != NO_RECEIVER and configured not in {s.id for s in sources}:
            options.append(
                selector.SelectOptionDict(
                    value=configured, label="Configured receiver (offline or missing)"
                )
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TRANSMITTER,
                        default=(
                            configured_transmitter.entity_id
                            if configured_transmitter
                            and configured_transmitter.entity_id in transmitters
                            else vol.UNDEFINED
                        ),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(include_entities=transmitters)
                    ),
                    vol.Optional(
                        CONF_REMOTE_ID,
                        description={
                            "suggested_value": f"0x{entry.data[CONF_REMOTE_ID]:07X}"
                        },
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_CHANNELS, default=entry.data[CONF_CHANNELS]
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=MAX_CHANNELS,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_RECEIVER, default=configured
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
            errors=errors,
        )
