"""One receiver, decoder and command coordinator per configured Dooya remote.

Receiver and echo lifecycle follows harf-insoma and harf-rhine (LICENSE.rhine).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from weakref import WeakValueDictionary

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from rf_protocols import RadioFrequencyCommand

from .const import (
    BUTTON_CLOSE,
    BUTTON_OPEN,
    BUTTON_PROGRAM,
    BUTTON_STOP,
    CONF_CHANNELS,
    CONF_RECEIVER,
    CONF_REMOTE_ID,
    CONF_TRANSMITTER,
)
from .protocol import Frame, FrameStream
from .receiver import async_get_source

_LOGGER = logging.getLogger(__name__)
ECHO_TAIL = 0.35
DEFAULT_ASSUMED_POSITION = 50
COMMAND_POSITIONS = {
    BUTTON_OPEN: 100,
    BUTTON_CLOSE: 0,
    BUTTON_STOP: DEFAULT_ASSUMED_POSITION,
}
COMMAND_NAMES = {
    BUTTON_OPEN: "open",
    BUTTON_CLOSE: "close",
    BUTTON_STOP: "stop",
    BUTTON_PROGRAM: "p2",
}
_LOCKS_KEY = "harf_dooya_transmitter_locks"


class DooyaRemote:
    """Track button presses and optimistic positions without retransmitting RX."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.remote_id = entry.data[CONF_REMOTE_ID]
        self.channels = entry.data[CONF_CHANNELS]
        self.receiver_available = False
        self.observations: dict[int, dict] = {}
        self.assumed_positions: dict[int, int] = {
            channel: DEFAULT_ASSUMED_POSITION for channel in range(self.channels + 1)
        }
        self._decoder = FrameStream()
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[Frame], None]] = []
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._closed = False
        self._echo_until: dict[tuple[int, int], float] = {}
        self._sending: tuple[int, int] | None = None
        locks = hass.data.setdefault(_LOCKS_KEY, WeakValueDictionary())
        transmitter = entry.data[CONF_TRANSMITTER]
        if (lock := locks.get(transmitter)) is None:
            lock = locks[transmitter] = asyncio.Lock()
        self._tx_lock = lock
        self._tx_tasks: set[asyncio.Task] = set()

    @callback
    def async_start(self) -> None:
        if not (source_id := self.entry.data.get(CONF_RECEIVER)):
            return
        if (source := async_get_source(self.hass, source_id)) is None:
            _LOGGER.warning(
                "Dooya receiver %s is missing; transmission remains available",
                source_id,
            )
            return
        try:
            self._unsubscribe = source.async_subscribe(
                self.async_receive, self._availability
            )
        except Exception:
            _LOGGER.warning(
                "Could not subscribe to Dooya receiver %s", source_id, exc_info=True
            )

    @callback
    def _availability(self, available: bool) -> None:
        if self._closed:
            return
        self.receiver_available = available
        if not available:
            self._decoder.reset()
        self._notify()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: (
            self._listeners.remove(listener) if listener in self._listeners else None
        )

    @callback
    def async_add_event_listener(
        self, listener: Callable[[Frame], None]
    ) -> CALLBACK_TYPE:
        self._event_listeners.append(listener)
        return lambda: (
            self._event_listeners.remove(listener)
            if listener in self._event_listeners
            else None
        )

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _observe(self, channel: int, command: int, origin: str) -> None:
        observation = {
            "last_command": COMMAND_NAMES.get(command, f"unknown_0x{command:X}"),
            "last_command_origin": origin,
            "last_command_timestamp": dt_util.utcnow().isoformat(),
        }
        channels = range(self.channels + 1) if channel == 0 else (channel,)
        position = COMMAND_POSITIONS.get(command)
        for target in channels:
            self.observations[target] = observation
            if position is not None:
                self.assumed_positions[target] = position
        self._notify()

    @callback
    def async_receive(self, timings: list[int]) -> None:
        if self._closed:
            return
        now = monotonic()
        for frame in self._decoder.feed(timings, now=now):
            if frame.remote_id != self.remote_id or frame.channel > self.channels:
                continue
            key = (frame.channel, frame.command)
            if key == self._sending or now <= self._echo_until.get(key, 0):
                continue
            self._observe(frame.channel, frame.command, "remote")
            if frame.command in COMMAND_NAMES:
                for listener in tuple(self._event_listeners):
                    listener(frame)

    async def async_transmit(
        self,
        channel: int,
        command: int,
        rf_command: RadioFrequencyCommand,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        """Serialize covers/P2 across entries sharing a radio, including queued airtime."""
        task = asyncio.current_task()
        self._tx_tasks.add(task)
        try:
            async with self._tx_lock:
                if self._closed:
                    raise HomeAssistantError("The Dooya remote has been unloaded")
                airtime = (
                    sum(map(abs, rf_command.get_raw_timings()))
                    * (rf_command.repeat_count + 1)
                    / 1_000_000
                )
                key = (channel, command)
                now = monotonic()
                self._echo_until = {
                    k: end for k, end in self._echo_until.items() if end > now
                }
                self._sending = key
                self._echo_until[key] = now + airtime + ECHO_TAIL
                try:
                    await send(rf_command)
                    if not self._closed:
                        self._observe(channel, command, "home_assistant")
                finally:
                    self._sending = None
                    self._echo_until[key] = monotonic() + airtime + ECHO_TAIL
                    # Native API calls can return before the radio has sent the burst.
                    if not self._closed:
                        await asyncio.sleep(airtime)
        finally:
            self._tx_tasks.discard(task)

    @callback
    def async_stop(self) -> None:
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for task in tuple(self._tx_tasks):
            task.cancel()
        self._decoder.reset()
        self._echo_until.clear()
        self._listeners.clear()
        self._event_listeners.clear()
        self.receiver_available = False
