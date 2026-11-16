"""Encode Dooya transmissions and decode received 40-bit remote frames."""

from dataclasses import dataclass
from time import monotonic

from rf_protocols.commands.ook import OOKCommand

from .const import BUTTON_PROGRAM, FREQUENCY, MAX_CHANNELS, MAX_REMOTE_ID

# Capture-derived envelope widths, not a global radio/RMT delay correction.
SYNC_PERIOD_US = 6180
SYNC_HIGH_US = 4672
SYNC_LOW_US = SYNC_PERIOD_US - SYNC_HIGH_US
BIT_PERIOD_US = 1076
BIT_ZERO_HIGH_US = 328
BIT_ZERO_LOW_US = BIT_PERIOD_US - BIT_ZERO_HIGH_US
BIT_ONE_HIGH_US = 680
BIT_ONE_LOW_US = BIT_PERIOD_US - BIT_ONE_HIGH_US
# Preserve frame starts when advancing the last falling edge: 24 us for one,
# 26 us for zero. Open, Close and Stop all end in one.
INTER_FRAME_GAP_US = 8144
INTER_FRAME_GAP_ZERO_US = 8146
FRAME_COUNT = 3
PROGRAM_FRAME_COUNT = 4
PROGRAM_FRAME_INTERVAL_US = 57_300


def encode_timings(remote_id: int, channel: int, button: int) -> list[int]:
    """Encode a 28-bit ID, 4-bit channel, button and matching check nibble."""
    if not 0 <= remote_id <= MAX_REMOTE_ID:
        raise ValueError("Remote ID must fit in 28 bits")
    if not 0 <= channel <= MAX_CHANNELS:
        raise ValueError("Channel must fit in 4 bits")
    if not 0 <= button <= 0xF:
        raise ValueError("Button must fit in 4 bits")

    frame = (remote_id << 12) | (channel << 8) | (button << 4) | button
    timings = [SYNC_HIGH_US, -SYNC_LOW_US]
    for bit in range(39, -1, -1):
        timings.extend(
            (BIT_ONE_HIGH_US, -BIT_ONE_LOW_US)
            if frame & (1 << bit)
            else (BIT_ZERO_HIGH_US, -BIT_ZERO_LOW_US)
        )
    return timings


def make_command(remote_id: int, channel: int, button: int) -> OOKCommand:
    """Send three OEM frames with the gap measured from the final falling edge."""
    timings = encode_timings(remote_id, channel, button)
    # The final bit's space is part of the gap, not an additional delay.
    timings[-1] = -(INTER_FRAME_GAP_US if button & 1 else INTER_FRAME_GAP_ZERO_US)
    return OOKCommand(
        frequency=FREQUENCY, timings=timings, repeat_count=FRAME_COUNT - 1
    )


def make_programming_command(remote_id: int, channel: int) -> OOKCommand:
    """Send P2 (0xCC) four times, with 57.3 ms between frame starts."""
    timings = encode_timings(remote_id, channel, BUTTON_PROGRAM)
    # Replace the final LOW so it completes the requested start-to-start period.
    timings[-1] = -(PROGRAM_FRAME_INTERVAL_US - sum(abs(t) for t in timings[:-1]))
    return OOKCommand(
        frequency=FREQUENCY, timings=timings, repeat_count=PROGRAM_FRAME_COUNT - 1
    )


# Receive windows deliberately differ from TX constants: RF envelope thresholds
# and OEM clocks vary. Zero/one HIGH and LOW windows do not overlap.
RX_SYNC_HIGH = (4000, 5400)
RX_SYNC_LOW = (1100, 1900)
RX_ZERO_HIGH = (250, 460)
RX_ZERO_LOW = (600, 850)
RX_ONE_HIGH = (560, 820)
RX_ONE_LOW = (250, 510)
RX_BIT_PERIOD = (950, 1220)
RX_MAX_TRAILING_LOW = 20_000
CONFIRM_FRAMES = 2
PRESS_TIMEOUT = 0.3
MAX_BUFFERED_TIMINGS = 82


@dataclass(frozen=True, slots=True)
class Frame:
    """One remote press frame, not motor feedback."""

    remote_id: int
    channel: int
    command: int


def _within(value: int, window: tuple[int, int]) -> bool:
    return window[0] <= value <= window[1]


def decode_frame(timings: list[int]) -> Frame | None:
    """Decode 40 bits; the final space may be absent, truncated or extended."""
    if len(timings) not in (81, 82) or any(
        type(t) is not int or t == 0 for t in timings
    ):
        return None
    if not _within(timings[0], RX_SYNC_HIGH) or not _within(-timings[1], RX_SYNC_LOW):
        return None
    word = 0
    for index in range(2, 81, 2):
        high = timings[index]
        if _within(high, RX_ZERO_HIGH):
            bit, low_window = 0, RX_ZERO_LOW
        elif _within(high, RX_ONE_HIGH):
            bit, low_window = 1, RX_ONE_LOW
        else:
            return None
        if index < 80:
            low = -timings[index + 1]
            if not _within(low, low_window) or not _within(high + low, RX_BIT_PERIOD):
                return None
        elif len(timings) == 82:
            low = -timings[-1]
            # End-of-frame idle is not a data symbol. Short idle can be a
            # receiver's truncated tail; intermediate invalid bit widths cannot.
            if not 1 <= low <= RX_MAX_TRAILING_LOW:
                return None
        word = (word << 1) | bit
    command = word & 0xF
    if (word >> 4) & 0xF != command:
        return None
    return Frame(word >> 12, (word >> 8) & 0xF, command)


class FrameStream:
    """Bounded parser, two-frame confirmation and one event per physical press.

    Partial frames expire after 300 ms without input. Empty input resets the
    connection. Missing final spaces require the next sync to delimit the frame.
    Malformed input breaks consecutive confirmation, but not press deduplication.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._timings: list[int] = []
        self._candidate: Frame | None = None
        self._count = 0
        self._last_rx: float | None = None
        self._emitted: set[Frame] = set()

    def _confirm(self, frame: Frame | None, result: list[Frame]) -> None:
        if frame is None or frame != self._candidate:
            self._candidate, self._count = frame, 0
        if frame is not None:
            self._count = min(CONFIRM_FRAMES, self._count + 1)
            if self._count == CONFIRM_FRAMES and frame not in self._emitted:
                # Bounded even when a continuous noisy stream changes addresses.
                if len(self._emitted) >= 256:
                    self._emitted.clear()
                self._emitted.add(frame)
                result.append(frame)

    def feed(self, timings: list[int], *, now: float | None = None) -> list[Frame]:
        now = monotonic() if now is None else now
        if not timings or (
            self._last_rx is not None and now - self._last_rx >= PRESS_TIMEOUT
        ):
            self.reset()
        self._last_rx = now
        result: list[Frame] = []
        for timing in timings:
            if type(timing) is not int or timing == 0:
                self._timings.clear()
                self._confirm(None, result)
                continue
            if _within(timing, RX_SYNC_HIGH):
                if len(self._timings) == 81:
                    self._confirm(decode_frame(self._timings), result)
                elif self._timings:
                    self._confirm(None, result)
                self._timings = [timing]
                continue
            if not self._timings:
                # Ignore idle between frames; other noise breaks confirmation.
                if timing > 0:
                    self._confirm(None, result)
                continue
            self._timings.append(timing)
            if len(self._timings) == MAX_BUFFERED_TIMINGS:
                self._confirm(decode_frame(self._timings), result)
                self._timings.clear()
        return result
