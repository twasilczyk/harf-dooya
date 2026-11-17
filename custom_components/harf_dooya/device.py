"""One receiver, decoder and command coordinator per configured Dooya remote.

Receiver and echo lifecycle follows harf-insoma and harf-rhine (LICENSE.rhine).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from weakref import WeakValueDictionary

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    CONF_TRAVEL_TIMES,
)
from .motion import DEFAULT_TRAVEL_TIME, POSITION_UPDATE_INTERVAL, Motion
from .protocol import Frame, FrameStream, make_command
from .receiver import async_get_source

_LOGGER = logging.getLogger(__name__)
ECHO_TAIL = 0.35
POST_BURST_IDLE = 0.2
COMMAND_NAMES = {
    BUTTON_OPEN: "open",
    BUTTON_CLOSE: "close",
    BUTTON_STOP: "stop",
    BUTTON_PROGRAM: "p2",
}
_LOCKS_KEY = "harf_dooya_transmitter_locks"


@dataclass
class PositionTarget:
    """Identity token for one request, including its cancellable Stop timer."""

    position: int
    timer: asyncio.TimerHandle | None = None


class DooyaRemote:
    """Track button presses and optimistic positions without retransmitting RX."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.remote_id = entry.data[CONF_REMOTE_ID]
        self.channels = entry.data[CONF_CHANNELS]
        self.receiver_available = False
        self.observations: dict[int, dict] = {}
        self.motions = {channel: Motion() for channel in range(1, self.channels + 1)}
        self._position_timer: asyncio.TimerHandle | None = None
        self._position_targets: dict[int, PositionTarget] = {}
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
        if origin == "remote" and command in (BUTTON_OPEN, BUTTON_CLOSE, BUTTON_STOP):
            self._cancel_position_targets(channel)
        observation = {
            "last_command": COMMAND_NAMES.get(command, f"unknown_0x{command:X}"),
            "last_command_origin": origin,
            "last_command_timestamp": dt_util.utcnow().isoformat(),
        }
        channels = range(self.channels + 1) if channel == 0 else (channel,)
        now = monotonic()
        # A command can arrive before an overdue timer callback. Advance every
        # channel before replacing that timer so no completed movement is stranded.
        for motion in self.motions.values():
            motion.update(now)
        for target in channels:
            self.observations[target] = observation
            if target != 0:
                self.motions[target].observe(command, now, self.travel_time(target))
        self._schedule_position_update(now)
        self._notify()

    @property
    def assumed_positions(self) -> dict[int, int | None]:
        """Read the last published estimates without advancing time in getters."""
        return {channel: self.position(channel) for channel in range(self.channels + 1)}

    def position(self, channel: int) -> int | None:
        """Average every numbered blind for the All blinds cover."""
        if channel != 0:
            return self.motions[channel].percentage
        positions = [motion.percentage for motion in self.motions.values()]
        if any(position is None for position in positions):
            return None
        return round(sum(positions) / len(positions))

    def is_moving(self, channel: int, direction: int) -> bool:
        """A group is moving while any of its blinds moves in that direction."""
        motions = self.motions.values() if channel == 0 else (self.motions[channel],)
        return any(motion.direction == direction for motion in motions)

    def travel_time(self, channel: int) -> float:
        """Return a numbered blind's configured full travel time."""
        times = self.entry.options.get(CONF_TRAVEL_TIMES, {})
        return times.get(str(channel), DEFAULT_TRAVEL_TIME)

    @callback
    def _schedule_position_update(self, now: float) -> None:
        if self._position_timer is not None:
            self._position_timer.cancel()
            self._position_timer = None
        deadlines = [
            motion.finish
            for motion in self.motions.values()
            if motion.direction is not None and motion.finish > now
        ]
        if deadlines and not self._closed:
            delay = min(POSITION_UPDATE_INTERVAL, min(deadlines) - now)
            self._position_timer = self.hass.loop.call_later(delay, self._update_positions)

    @callback
    def _update_positions(self) -> None:
        if self._closed:
            return
        now = monotonic()
        for motion in self.motions.values():
            motion.update(now)
        self._schedule_position_update(now)
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
        if command in (BUTTON_OPEN, BUTTON_CLOSE, BUTTON_STOP):
            self._cancel_position_targets(channel)
        task = asyncio.current_task()
        self._tx_tasks.add(task)
        try:
            async with self._tx_lock:
                await self._async_send_locked(channel, command, rf_command, send)
        finally:
            self._tx_tasks.discard(task)

    async def _async_send_locked(
        self,
        channel: int,
        command: int,
        rf_command: RadioFrequencyCommand,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
        on_sent: Callable[[], None] | None = None,
    ) -> None:
        """Send with the transmitter lock held, preserving echo and airtime rules."""
        if self._closed:
            raise HomeAssistantError("The Dooya remote has been unloaded")
        airtime = (
            sum(map(abs, rf_command.get_raw_timings()))
            * (rf_command.repeat_count + 1)
            / 1_000_000
        )
        key = (channel, command)
        now = monotonic()
        self._echo_until = {k: end for k, end in self._echo_until.items() if end > now}
        self._sending = key
        self._echo_until[key] = now + airtime + ECHO_TAIL
        try:
            await send(rf_command)
            if not self._closed:
                self._observe(channel, command, "home_assistant")
                if on_sent is not None:
                    on_sent()
        finally:
            self._sending = None
            self._echo_until[key] = monotonic() + airtime + ECHO_TAIL
            # Native API calls can return before the radio has sent the burst.
            if not self._closed:
                await asyncio.sleep(airtime + POST_BURST_IDLE)

    @callback
    def _cancel_position_targets(self, channel: int) -> None:
        channels = range(self.channels + 1) if channel == 0 else (channel,)
        for target_channel in channels:
            target = self._position_targets.pop(target_channel, None)
            if target is not None and target.timer is not None:
                target.timer.cancel()

    async def async_set_position(
        self,
        channel: int,
        position: int,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        """Start each affected blind sequentially; travel runs in the background."""
        if not 0 <= channel <= self.channels or not 0 <= position <= 100:
            raise ServiceValidationError("Set position requires a valid blind and 0–100%")
        self._cancel_position_targets(channel)
        channels = range(1, self.channels + 1) if channel == 0 else (channel,)
        # Reserve every request before the first await. A later command (including
        # physical RX) can then cancel even a group member still waiting to start.
        targets = {i: PositionTarget(position) for i in channels}
        self._position_targets.update(targets)
        try:
            for target_channel in channels:
                target = targets.pop(target_channel)
                await self._async_set_position(target_channel, target, send)
        finally:
            # On failure/cancellation, discard unstarted requests but preserve Stop
            # timers for blinds that have already begun moving.
            for target_channel, target in targets.items():
                if self._position_targets.get(target_channel) is target:
                    self._cancel_position_targets(target_channel)

    async def _async_set_position(
        self,
        channel: int,
        target: PositionTarget,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        position = target.position
        task = asyncio.current_task()
        self._tx_tasks.add(task)
        try:
            async with self._tx_lock:
                if self._closed or self._position_targets.get(channel) is not target:
                    return
                # Recalculate after waiting for the radio, not when the request arrived.
                self._update_positions()
                motion = self.motions[channel]
                if position in (0, 100) or motion.position is None:
                    # Without a starting position, establish the nearest endpoint
                    # to the requested target instead of timing an intermediate Stop.
                    self._cancel_position_targets(channel)
                    command = BUTTON_OPEN if position >= 50 else BUTTON_CLOSE
                    if motion.direction != command:
                        await self._async_send_locked(
                            channel, command,
                            make_command(self.remote_id, channel, command), send,
                        )
                    return
                if motion.percentage == position:
                    self._cancel_position_targets(channel)
                    if motion.direction is not None:
                        await self._async_send_locked(
                            channel, BUTTON_STOP,
                            make_command(self.remote_id, channel, BUTTON_STOP),
                            send,
                        )
                    return
                command = BUTTON_OPEN if position > motion.position else BUTTON_CLOSE
                if motion.direction == command:
                    self._schedule_target_stop(channel, target, send)
                    return
                await self._async_send_locked(
                    channel, command, make_command(self.remote_id, channel, command),
                    send,
                    lambda: self._schedule_target_stop(channel, target, send),
                )
        except BaseException:
            if self._position_targets.get(channel) is target:
                self._cancel_position_targets(channel)
            raise
        finally:
            self._tx_tasks.discard(task)

    @callback
    def _schedule_target_stop(
        self,
        channel: int,
        target: PositionTarget,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        if self._closed or self._position_targets.get(channel) is not target:
            return
        motion = self.motions[channel]
        assert motion.position is not None
        remaining = (
            target.position - motion.position
            if motion.direction == BUTTON_OPEN
            else motion.position - target.position
        )
        delay = max(0.0, remaining) / 100 * motion.travel_time
        target.timer = self.hass.loop.call_later(
            delay, self._start_target_stop, channel, target, send
        )

    @callback
    def _start_target_stop(
        self,
        channel: int,
        target: PositionTarget,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        target.timer = None
        if self._closed or self._position_targets.get(channel) is not target:
            return
        self.hass.async_create_background_task(
            self._async_target_stop(channel, target, send), "Dooya timed Stop"
        )

    async def _async_target_stop(
        self,
        channel: int,
        target: PositionTarget,
        send: Callable[[RadioFrequencyCommand], Awaitable[None]],
    ) -> None:
        task = asyncio.current_task()
        self._tx_tasks.add(task)
        try:
            async with self._tx_lock:
                # A manual command or newer target may have superseded this queued Stop.
                if self._closed or self._position_targets.get(channel) is not target:
                    return
                self._position_targets.pop(channel)
                await self._async_send_locked(
                    channel, BUTTON_STOP,
                    make_command(self.remote_id, channel, BUTTON_STOP), send,
                )
        except Exception:
            _LOGGER.exception("Could not send timed Stop for Dooya channel %s", channel)
        finally:
            self._tx_tasks.discard(task)

    @callback
    def async_stop(self) -> None:
        self._closed = True
        self._cancel_position_targets(0)
        if self._position_timer is not None:
            self._position_timer.cancel()
            self._position_timer = None
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
