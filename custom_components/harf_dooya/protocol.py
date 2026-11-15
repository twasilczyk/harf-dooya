"""Encode the Dooya protocol used by ESPHome's remote_transmitter."""

from rf_protocols.commands.ook import OOKCommand

from .const import FREQUENCY, MAX_CHANNELS, MAX_REMOTE_ID

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


def encode_timings(remote_id: int, channel: int, button: int) -> list[int]:
    """Encode a 24-bit ID, 8-bit channel, button and matching check nibble."""
    if not 0 <= remote_id <= MAX_REMOTE_ID:
        raise ValueError("Remote ID must fit in 24 bits")
    if not 0 <= channel <= MAX_CHANNELS:
        raise ValueError("Channel must fit in 8 bits")
    if not 0 <= button <= 0xF:
        raise ValueError("Button must fit in 4 bits")

    frame = (remote_id << 16) | (channel << 8) | (button << 4) | button
    timings = [SYNC_HIGH_US, -SYNC_LOW_US]
    for bit in range(39, -1, -1):
        timings.extend(
            (BIT_ONE_HIGH_US, -BIT_ONE_LOW_US)
            if frame & (1 << bit)
            else (BIT_ZERO_HIGH_US, -BIT_ZERO_LOW_US)
        )
    return timings


def make_command(
    remote_id: int, channel: int, button: int, frequency: int = FREQUENCY
) -> OOKCommand:
    """Send three OEM frames with the gap measured from the final falling edge."""
    timings = encode_timings(remote_id, channel, button)
    # The final bit's space is part of the gap, not an additional delay.
    timings[-1] = -(INTER_FRAME_GAP_US if button & 1 else INTER_FRAME_GAP_ZERO_US)
    return OOKCommand(
        frequency=frequency, timings=timings, repeat_count=FRAME_COUNT - 1
    )
