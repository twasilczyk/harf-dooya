"""RF receive channels, isolated from the button protocol.

Adapted from twasilczyk/harf-insoma and twasilczyk/harf-rhine; see LICENSE.rhine.

Source IDs name radio config entries, independently of the transmitter.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

if TYPE_CHECKING:
    from homeassistant.components.esphome.entry_data import RuntimeEntryData

_LOGGER = logging.getLogger(__name__)
SIGNAL_RX_FRAME = "hackrf_proxy_rx_frame_{}"
type FrameHandler = Callable[[list[int]], None]
type AvailabilityHandler = Callable[[bool], None]


class FrameSource(ABC):
    """A receiver owned and managed by another integration."""

    domain: ClassVar[str]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    @property
    def id(self) -> str:
        return f"{self.domain}:{self.entry.entry_id}"

    @property
    def name(self) -> str:
        return self.entry.title

    @abstractmethod
    async def async_available(self) -> bool:
        """Whether the source can be selected now."""

    @abstractmethod
    @callback
    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler | None = None
    ) -> CALLBACK_TYPE:
        """Subscribe until the returned callback is called."""


class HackrfProxySource(FrameSource):
    """Dispatcher contract exported by hass-hackrf-proxy."""

    domain = "hackrf_proxy"

    async def async_available(self) -> bool:
        return self.entry.state is ConfigEntryState.LOADED

    @callback
    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler | None = None
    ) -> CALLBACK_TYPE:
        active = True

        @callback
        def on_frame(frame: dict[str, Any]) -> None:
            if not active:
                return
            if self.entry.state is not ConfigEntryState.LOADED:
                handler([])
                return
            timings = frame.get("timings")
            if isinstance(timings, (list, tuple)):
                handler(list(timings))

        unsubscribe_frame = async_dispatcher_connect(
            self.hass, SIGNAL_RX_FRAME.format(self.entry.entry_id), on_frame
        )

        @callback
        def state_changed() -> None:
            if not active:
                return
            handler([])  # Discard partial frames across disconnects/reloads.
            if availability:
                availability(self.entry.state is ConfigEntryState.LOADED)

        unsubscribe_state = self.entry.async_on_state_change(state_changed)
        if availability:
            availability(self.entry.state is ConfigEntryState.LOADED)

        @callback
        def unsubscribe() -> None:
            nonlocal active
            active = False
            unsubscribe_frame()
            unsubscribe_state()

        return unsubscribe


class EsphomeSource(FrameSource):
    """Native API events with reload/reconnect-aware subscriptions."""

    domain = "esphome"

    async def async_available(self) -> bool:
        if self.entry.state is not ConfigEntryState.LOADED:
            return False
        data: RuntimeEntryData = self.entry.runtime_data
        if not data.available:
            return False
        return bool(await self._async_receiver_keys(data))

    async def _async_receiver_keys(
        self, data: "RuntimeEntryData"
    ) -> set[tuple[int, int]]:
        """Read unfiltered API metadata; HA may cache only RF transmitters."""
        from aioesphomeapi import RadioFrequencyInfo

        async with asyncio.timeout(5):
            infos, _ = await data.client.list_entities_services()
        rf_infos = [info for info in infos if isinstance(info, RadioFrequencyInfo)]
        for info in rf_infos:
            _LOGGER.debug(
                "ESPHome RF channel %s device_id=%s key=%s: capabilities=%s, "
                "frequency_min=%s Hz, frequency_max=%s Hz, supported_modulations=%s",
                self.id,
                info.device_id,
                info.key,
                info.capabilities,
                info.frequency_min,
                info.frequency_max,
                info.supported_modulations,
            )
        return {
            (info.device_id, info.key)
            for info in rf_infos
            if _compatible_receiver(info)
        }

    @callback
    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler | None = None
    ) -> CALLBACK_TYPE:
        from aioesphomeapi import InfraredRFReceiveEvent

        unsub_update: CALLBACK_TYPE | None = None
        unsub_receive: CALLBACK_TYPE | None = None
        refresh_task: asyncio.Task[None] | None = None
        active = True
        generation = 0

        @callback
        def status(value: bool) -> None:
            if active and availability:
                availability(value)

        async def refresh(data: "RuntimeEntryData", token: int) -> None:
            nonlocal unsub_receive
            try:
                keys = await self._async_receiver_keys(data)
                if not active or token != generation or not data.available:
                    return
                if not keys:
                    status(False)
                    return
                last_key: tuple[int, int] | None = None

                @callback
                def on_event(event: InfraredRFReceiveEvent) -> None:
                    nonlocal last_key
                    key = (event.device_id, event.key)
                    if (
                        not active
                        or token != generation
                        or not data.available
                        or key not in keys
                    ):
                        return
                    # Never stitch partial frames from different RF input channels.
                    if last_key is not None and key != last_key:
                        handler([])
                    last_key = key
                    handler(list(event.timings))

                unsub_receive = data.client.subscribe_infrared_rf_receive(on_event)
                status(True)
            except Exception:
                status(False)
                _LOGGER.debug(
                    "Could not subscribe to receiver %s", self.id, exc_info=True
                )

        @callback
        def cancel_receive() -> None:
            nonlocal refresh_task, unsub_receive, generation
            generation += 1
            if refresh_task is not None:
                refresh_task.cancel()
                refresh_task = None
            if unsub_receive is not None:
                unsub_receive()
                unsub_receive = None

        @callback
        def on_device_update() -> None:
            nonlocal refresh_task
            if not active:
                return
            data: RuntimeEntryData = self.entry.runtime_data
            if not data.available:
                cancel_receive()
                handler([])
                status(False)
            elif unsub_receive is None and (
                refresh_task is None or refresh_task.done()
            ):
                refresh_task = self.hass.async_create_task(
                    refresh(data, generation), "Dooya receiver channel discovery"
                )

        @callback
        def detach() -> None:
            nonlocal unsub_update
            cancel_receive()
            if unsub_update is not None:
                unsub_update()
                unsub_update = None

        @callback
        def on_state_change() -> None:
            nonlocal unsub_update
            if not active:
                return
            if self.entry.state is not ConfigEntryState.LOADED:
                detach()
                handler([])
                status(False)
            elif unsub_update is None:
                data: RuntimeEntryData = self.entry.runtime_data
                unsub_update = data.async_subscribe_device_updated(on_device_update)
                on_device_update()

        unsub_state = self.entry.async_on_state_change(on_state_change)
        try:
            on_state_change()
        except Exception:
            unsub_state()
            detach()
            raise

        @callback
        def unsubscribe() -> None:
            nonlocal active
            active = False
            unsub_state()
            detach()

        return unsubscribe


_IMPLEMENTATIONS: dict[str, type[FrameSource]] = {
    "hackrf_proxy": HackrfProxySource,
    "esphome": EsphomeSource,
}


def _compatible_receiver(info: Any) -> bool:
    """Recognize RF receive capability; TX frequency metadata is not an RX filter."""
    from aioesphomeapi import RadioFrequencyCapability

    return bool(info.capabilities & RadioFrequencyCapability.RECEIVER)


async def async_list_sources(hass: HomeAssistant) -> list[FrameSource]:
    """List live sources; an offline radio must not prevent transmit-only setup."""
    sources = []
    for entry in hass.config_entries.async_entries():
        if (implementation := _IMPLEMENTATIONS.get(entry.domain)) is None:
            continue
        source = implementation(hass, entry)
        try:
            if await source.async_available():
                sources.append(source)
        except Exception:
            # Receiver discovery is best effort, including optional API errors.
            _LOGGER.debug("Could not query receiver %s", source.id, exc_info=True)
    return sources


@callback
def async_get_source(hass: HomeAssistant, source_id: str) -> FrameSource | None:
    """Resolve a persisted source, even before its radio has loaded."""
    domain, _, entry_id = source_id.partition(":")
    implementation = _IMPLEMENTATIONS.get(domain)
    entry = hass.config_entries.async_get_entry(entry_id)
    if implementation is None or entry is None or entry.domain != domain:
        return None
    return implementation(hass, entry)
