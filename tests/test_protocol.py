"""Check the OEM payload and waveform for every supported command."""

import pytest

from custom_components.harf_dooya.protocol import encode_timings, make_command


@pytest.mark.parametrize("button", [1, 3, 5])
def test_oem_frame_and_repeats(button: int) -> None:
    """All commands use the measured symbols and exactly three identical frames."""
    payload = bytes((0x12, 0x3C, 0x14, 1, button * 0x11))
    bits = f"{int.from_bytes(payload, 'big'):040b}"
    expected = [4672, -1508]
    for bit in bits:
        expected.extend((680, -396) if bit == "1" else (328, -748))
    assert encode_timings(0x123C14, 1, button) == expected
    assert all(sum(map(abs, expected[i : i + 2])) == 1076 for i in range(2, 82, 2))

    command = make_command(0x123C14, 1, button)
    assert command.frequency == 433_934_000
    assert command.modulation == "OOK"
    assert command.repeat_count == 2
    expected[-1] = -8144
    assert command.get_raw_timings() == expected

    # HA's ESPHome adapter adds one to repeat_count; the raw path adds no delay.
    sequence = command.get_raw_timings() * (command.repeat_count + 1)
    starts = []
    falling_edges = []
    elapsed = 0
    for i, duration in enumerate(sequence):
        if i % 82 == 0:
            starts.append(elapsed)
        elapsed += abs(duration)
        if i % 82 == 80:
            falling_edges.append(elapsed)
    assert starts == [0, 56_968, 113_936]
    assert [starts[i + 1] - falling_edges[i] for i in range(2)] == [8144, 8144]
    assert len(sequence) == 3 * 82
    assert sequence[:82] == sequence[82:164] == sequence[164:]


def test_frequency_override_and_final_zero() -> None:
    """Frequency overrides preserve data; a final zero preserves its frame interval."""
    command = make_command(0xABCDEF, 255, 2, 433_921_000)
    assert command.frequency == 433_921_000
    timings = command.get_raw_timings()
    assert timings[-2:] == [328, -8146]
    bits = "".join("1" if mark == 680 else "0" for mark in timings[2::2])
    assert int(bits, 2).to_bytes(5, "big") == bytes.fromhex("AB CD EF FF 22")
    assert sum(map(abs, timings)) == 56_618


@pytest.mark.parametrize("button", range(16))
def test_falling_edge_changes_preserve_all_rising_edges(button: int) -> None:
    """Keep every rising edge, including repeat starts, at its previous offset."""
    payload = (0x123C14 << 16) | (1 << 8) | (button << 4) | button
    previous = [4700, -1480]
    for bit in f"{payload:040b}":
        previous.extend((704, -372) if bit == "1" else (354, -722))
    previous[-1] = -8120
    current = make_command(0x123C14, 1, button).get_raw_timings()

    old_elapsed = new_elapsed = 0
    for index, (old, new) in enumerate(zip(previous * 3, current * 3, strict=True)):
        if index % 2 == 0:
            assert new_elapsed == old_elapsed
            advance = 28 if index % 82 == 0 else (24 if old == 704 else 26)
            assert new == old - advance
        old_elapsed += abs(old)
        new_elapsed += abs(new)
    assert new_elapsed == old_elapsed
