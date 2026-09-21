"""Encode the Dooya protocol used by ESPHome's remote_transmitter."""

from rf_protocols.commands.ook import OOKCommand

from .const import FREQUENCY, MAX_CHANNELS, MAX_REMOTE_ID


def encode_timings(remote_id: int, channel: int, button: int) -> list[int]:
    """Encode a 24-bit ID, 8-bit channel, button and matching check nibble."""
    if not 0 <= remote_id <= MAX_REMOTE_ID:
        raise ValueError("Remote ID must fit in 24 bits")
    if not 0 <= channel <= MAX_CHANNELS:
        raise ValueError("Channel must fit in 8 bits")
    if not 0 <= button <= 0xF:
        raise ValueError("Button must fit in 4 bits")

    frame = (remote_id << 16) | (channel << 8) | (button << 4) | button
    timings = [5000, -1500]
    for bit in range(39, -1, -1):
        timings.extend((750, -350) if frame & (1 << bit) else (350, -750))
    return timings


def make_command(remote_id: int, channel: int, button: int) -> OOKCommand:
    """Match ESPHome's repeat: 3, including its default 25 ms repeat delay."""
    timings = encode_timings(remote_id, channel, button)
    # RF commands have no separate repeat delay. Merge it into the last space
    # to preserve alternating marks/spaces when the transmitter repeats them.
    timings[-1] -= 25_000
    return OOKCommand(frequency=FREQUENCY, timings=timings, repeat_count=2)
