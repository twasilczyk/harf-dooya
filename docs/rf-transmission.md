# Dooya RF transmission

The shared Python encoder in `custom_components/harf_dooya/protocol.py` generates
all commands. The payload remains a 28-bit remote ID, 4-bit channel, and two
identical command nibbles. Open = 1, Close = 3, Stop = 5. For remote 0x123C140,
channel 1, Stop is `12 3C 14 01 55`.

The upper nibble of the former 8-bit channel byte is part of the 28-bit remote ID.
For example, playroom remote `0x4231157`, channel 4, Close gives
`42 31 15 74 33`, and channel 0 (All) gives `42 31 15 70 33`.
Carrier and pulse timings are unchanged.

P2 programming uses nibble `0xC` twice, giving `12 3C 14 01 CC` for the same
remote and channel. The remote has one configuration button on the existing
device, using channel 1 and sharing the covers' transmitter and fixed frequency. Both physical
P2 keys map to this single command.

`make_programming_command` uses the same sync and bit timings but requests four
frames (`repeat_count=3` additional repeats). It replaces the last LOW with
8,828 µs, producing a 57,300 µs frame start interval. Open, Close and Stop retain
their existing three-frame sequence and 56,968 µs interval.

## Requested waveform

| Constant | Microseconds |
| --- | ---: |
| SYNC_HIGH_US | 4672 |
| SYNC_LOW_US | 1508 |
| BIT_ZERO_HIGH_US | 328 |
| BIT_ZERO_LOW_US | 748 |
| BIT_ONE_HIGH_US | 680 |
| BIT_ONE_LOW_US | 396 |
| INTER_FRAME_GAP_US | 8144 |

Each bit is 1076 µs. `FRAME_COUNT` is 3. The final bit's LOW is replaced by
8144 µs, rather than extended by it. All three supported commands end in a one,
so their sync-to-sync interval is `4672 + 1508 + 40*1076 - 396 + 8144 = 56968 µs`.
The trailing silence after the third frame is harmless and produces no fourth frame.
For an even command nibble, the encoder uses `INTER_FRAME_GAP_ZERO_US = 8146`
to preserve its previous 56618 µs frame interval as well. The cover commands
(Open, Close, Stop) are all odd.

## Transmission path inspected

1. `DooyaCover` calls `make_command`, which uses 433890000 Hz and returns
   one 82-duration raw OOK frame and `repeat_count=2` (additional repeats).
2. HA's RF consumer calls `async_send_command`. The ESPHome RF entity forwards
   the raw durations and frequency through `radio_frequency_transmit_raw_timings`,
   converting additional repeats to **three total** transmissions.
3. ESPHome's native API creates a radio-frequency call. OpenHARF's
   `firmware/components/ir_rf_proxy/ir_rf_proxy.cpp` forwards it to
   `IrRfEntity::transmit_raw_` with zero envelope carrier modulation.
4. OpenHARF's `firmware/rf-module.yaml` `on_control` waits for any previous
   transmission, then calls `cc1101.set_frequency` with the requested Hz.
   `on_transmit` switches the CC1101 to TX.
5. ESPHome's `ir_rf_base.cpp` copies the raw frame and sets `send_times=3`.
   `RemoteTransmitterBase::TransmitCall` defaults `send_wait` to **zero**; the
   raw path never changes it. The YAML action's historical 25 ms repeat default
   is not used by this path.
6. The ESP32 RMT encoder in `remote_transmitter_rmt.cpp` streams the three
   repetitions in one RMT transmission. Its extra repeat delay is `send_wait=0`.
   The 8144 µs trailing space is therefore the whole programmed inter-frame gap.
7. After completion, OpenHARF restores its normal radio frequency and RX mode.
   Retuning and completion callbacks occur outside the three-frame sequence.

The firmware's C++ `remote_base/dooya_protocol.cpp` is used by explicit ESPHome
Dooya actions/decoding, not by this raw HA path. It is not changed by this fix.
Neither global transmitter defaults nor other protocol encoders are changed.

## Falling-edge investigation (2026-11-15)

Compared `oem.sigmf-data` with
`gqrx_20261115_152926_433923000_500000_fc.sigmf-data` in
`openharf-extra/dooya`. Both are little-endian complex float32 at 500 kS/s
(2 µs per sample). All three frames in each decode to `0x123C140155`.

For a consistent comparison, smooth the IQ magnitude with a three-sample boxcar;
use the median magnitude as the noise floor and the 99th percentile as the
ON reference, separately for each capture (their amplitudes differ substantially).
Measure threshold crossings halfway between those levels. The resulting widths
are **threshold-dependent RF envelope widths**, not GPIO measurements:

| HIGH | Previous encoded | Latest HA, 50% threshold | OEM, 50% threshold | New encoded |
| --- | ---: | ---: | ---: | ---: |
| Sync | 4700 | 4700–4702 | 4672–4676 | 4672 |
| Zero (per-frame median) | 354 | 354 | 326–328 | 328 |
| One (per-frame median) | 704 | 704 | 680 | 680 |

At 20% and 80% thresholds, the zero/one HA–OEM differences remain approximately
26–34 / 24–34 µs. Absolute widths differ from measurements made near the noise
floor; this explains why low-threshold RF measurements can exceed encoded widths.
The latest HA's half-amplitude bit widths already match its programmed values.
There is no evidence here of an extra 24 µs being added by RMT.

Source inspection agrees: the configured/default RMT clock is 1 MHz, giving an
exact integer tick per microsecond. `from_microseconds_` converts both HIGH and
LOW identically; the RMT encoder copies these durations without a per-edge delay.
The RF proxy disables envelope carrier modulation. CC1101 is in asynchronous
OOK mode, with the existing 50000 symbol-rate setting; no radio settings change.
These captures alone cannot separate analog envelope response from receiver
filtering and threshold effects, nor prove the exact GPIO-to-RF edge latency.
A simultaneous GPIO/RF capture would be needed for that attribution.

The supported conclusion is that the previous **Dooya waveform constants** placed
falling edges too late relative to the OEM waveform. Correct those constants at
the encoder, not the shared RMT/CC1101 layer. Sync, zero, and one are shortened
by **28, 26, and 24 µs**, respectively, based on their separate measurements.
Their following LOWs increase by the same amounts. The final gap increases by
24 µs for the supported commands, so **all rising edges and frame starts remain
unchanged**, including the existing 56.968 ms frame interval. This intentionally
does not chase the OEM's slightly longer measured 57.02–57.03 ms interval.

The new values are capture-informed programmed targets. Verify them with a fresh
RF capture after deployment; tests cannot establish the resulting analog widths.

## Frequency and calibration

The requested frequency is fixed at **433890000 Hz (433.890 MHz)** for covers and
P2 buttons. Saved frequency overrides are ignored. Setup and reconfiguration
check the transmitter's supported range and OOK support.

The inspected OpenHARF `rf.yaml` configures the 433 MHz module with
`xtal_correction_ppm: -20.38`. The CC1101 driver calculates:

```
f_xtal = 26_000_000 * (1 + correction_ppm / 1_000_000)
FREQ = trunc(requested_hz * 65536 / f_xtal)
```

Different hardware calibration values produce different register words for the
same requested RF frequency. A fresh RF capture is required to confirm the actual
carrier and edge timings; software tests verify requested durations and frequency,
not CC1101 analog edge delays or the USRP frequency reference.

## Reception and press confirmation

Reception has its own tolerant windows and does not modify any TX constants:

| RX field | Allowed µs |
| --- | --- |
| Sync HIGH / LOW | 4000–5400 / 1100–1900 |
| Zero HIGH / LOW | 250–460 / 600–850 |
| One HIGH / LOW | 560–820 / 250–510 |
| Normal bit period | 950–1220 |

Normal bit HIGH/LOW windows do not overlap. Every non-final bit must satisfy
both its symbol windows and overall period. The two final command nibbles must
match. Unknown commands are preserved as typed frames. The final space may be
absent (81 durations), truncated, a normal bit space, or extended idle up to
20 ms. Extended idle in the middle of a frame is rejected. A stream with no final
space waits for the next sync to delimit the frame; a final space may arrive in
a later callback. Invalid types (including booleans/floats), zero durations,
malformed symbols and bad checks break consecutive-frame confirmation.

`FrameStream` stores at most 82 durations. It resynchronizes at sync marks,
handles concatenated/split frames, and resets partial data on disconnect or
300 ms without input. `CONFIRM_FRAMES=2` requires the same remote ID, channel and
command in two consecutive frames. Repeated confirmed frames are deduplicated
within a press; after `PRESS_TIMEOUT=0.3` a new identical press can be reported.
The recent-press set is bounded as well.

`receiver.py` owns transport adapters; `device.py` owns one subscription and
stream per remote plus routing, diagnostics and TX echo protection. Receiver
availability updates affect only the event entity. Listener/discovery cleanup
uses unsubscribe callbacks, task cancellation and connection generation checks
so stale callbacks cannot revive unloaded entities. The transmitter's native
RF consumer still handles availability and HA context propagation.

The transmit coordinator reserves the actual waveform duration times the total
frame count, with a 350 ms echo tail before/after enqueueing. The shared radio
lock is retained for queued airtime to prevent cover/P2 overlap. No receive,
learning, reconnect or restore operation enters the transmit path.
