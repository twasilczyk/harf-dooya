"""Independent OEM-like frames; never use the transmit encoder as RX fixtures."""

import pytest

from custom_components.harf_dooya.protocol import (
    MAX_BUFFERED_TIMINGS,
    Frame,
    FrameStream,
    decode_frame,
)


def remote_frame(remote_id=0x123C14, channel=1, command=5, check=None):
    word = (
        (remote_id << 16)
        | (channel << 8)
        | (command << 4)
        | (command if check is None else check)
    )
    timings = [4682, -1502]
    for bit in f"{word:040b}":
        timings.extend((690, -386) if bit == "1" else (338, -738))
    timings[-1] = -8143
    return timings


@pytest.mark.parametrize("command", [1, 3, 5, 12, 7])
@pytest.mark.parametrize("channel", [0, 1, 3, 255])
def test_decode(command, channel):
    assert decode_frame(remote_frame(channel=channel, command=command)) == Frame(
        0x123C14, channel, command
    )


@pytest.mark.parametrize(
    "index,value",
    [
        (0, 3000),
        (1, -800),
        (2, 510),
        (3, -400),
        (3, -9000),
        (80, 100),
        (81, -100000),
        (81, 100),
    ],
)
def test_invalid_timing(index, value):
    timings = remote_frame()
    timings[index] = value
    assert decode_frame(timings) is None


@pytest.mark.parametrize("bad", [True, False, 0, 338.0, None, "338"])
def test_invalid_types(bad):
    timings = remote_frame()
    timings[2] = bad
    assert decode_frame(timings) is None
    assert FrameStream().feed(timings * 3) == []


def test_mismatch_and_unrelated():
    assert decode_frame(remote_frame(command=5, check=3)) is None
    assert FrameStream().feed([320, -592] * 1000) == []


@pytest.mark.parametrize("split", range(1, 164))
def test_split_and_concat(split):
    data = remote_frame() * 3
    stream = FrameStream()
    result = stream.feed([120, -90] + data[:split], now=0)
    result += stream.feed(data[split:], now=0.05)
    assert result == [Frame(0x123C14, 1, 5)]
    assert len(stream._timings) <= MAX_BUFFERED_TIMINGS


def test_confirmation_dedup_and_new_press():
    s = FrameStream()
    assert not s.feed(remote_frame(), now=0)
    assert not s.feed(remote_frame(command=1), now=0.06)
    assert s.feed(remote_frame(command=1), now=0.12) == [Frame(0x123C14, 1, 1)]
    assert not s.feed(remote_frame(command=1) * 4, now=0.18)
    assert s.feed(remote_frame(command=1) * 2, now=0.49) == [Frame(0x123C14, 1, 1)]


def test_malformed_breaks_confirmation_and_resync():
    s = FrameStream()
    assert not s.feed(remote_frame(), now=0)
    bad = remote_frame()
    bad[9] = -9000
    assert not s.feed(bad + remote_frame(), now=0.05)
    assert s.feed(remote_frame(), now=0.1) == [Frame(0x123C14, 1, 5)]


def test_disconnect_stale_and_bounded():
    s = FrameStream()
    assert not s.feed(remote_frame()[:70], now=0)
    assert not s.feed(remote_frame()[70:], now=0.31)
    assert not s.feed(remote_frame(), now=0.32)
    assert not s.feed([], now=0.33)
    assert not s.feed(remote_frame(), now=0.34)
    assert s.feed(remote_frame(), now=0.4)
    assert not s.feed([4682] + [-100] * 10000, now=0.41)
    assert len(s._timings) <= MAX_BUFFERED_TIMINGS


@pytest.mark.parametrize("tail", [20, 400, 1000, 1800, 8143, 8828])
def test_absent_and_truncated_tail(tail):
    frame = remote_frame()
    assert decode_frame(frame[:-1]) == Frame(0x123C14, 1, 5)
    assert FrameStream().feed(frame[:-1] * 3) == [Frame(0x123C14, 1, 5)]
    frame[-1] = -tail
    assert FrameStream().feed(frame * 2) == [Frame(0x123C14, 1, 5)]
