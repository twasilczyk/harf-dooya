# Dooya RF

> [!WARNING]
> This project is mostly AI generated and not well reviewed.
>
> USE AT YOUR OWN RISK

A small Home Assistant custom integration for Dooya blinds, using any compatible
`radio_frequency` transmitter. Creates one device per remote, one cover per
numbered channel, and an **All blinds** broadcast cover, with **open**, **close**,
and **stop**.

Project: `harf-dooya`. Home Assistant domain: `harf_dooya`.

## Requirements

- Home Assistant **2026.9.2 or newer**. This is the minimum tested version.
- An RF transmitter already added to Home Assistant, exposing a
  `radio_frequency` entity supporting **433.890 MHz OOK**. A `remote` entity alone
  does not provide this API.
- A remote already paired with your blinds, and its channel count.
- Optionally, an ESPHome RF receiver or a `hackrf_proxy` integration to learn its
  28-bit ID and observe physical presses. Without a receiver, enter the ID manually.

## Install with HACS

With [HACS](https://www.hacs.xyz/docs/use/) installed, open this repository in HACS:

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=twasilczyk&repository=harf-dooya&category=integration)

You can also add it manually:

1. Open **HACS → ⋮ → Custom repositories**.
2. Enter `https://github.com/twasilczyk/harf-dooya` and select type **Integration**.
3. Add the repository, open **Dooya RF**, and download it.
4. Restart Home Assistant, then follow the setup instructions below.

Future releases appear as updates in HACS. Restart Home Assistant after updating.

## Manual installation

Download the source archive from a
[release](https://github.com/twasilczyk/harf-dooya/releases), then copy its
`custom_components/harf_dooya` directory into your Home Assistant configuration's
`custom_components` directory. The resulting path should be
`<config>/custom_components/harf_dooya/manifest.json`. Restart Home Assistant.

## Set up

1. Go to **Settings → Devices & services → Add integration → Dooya RF**.
2. Select the RF transmitter.
3. Select an optional ESPHome or HackRF receiver. The receiver belonging to the
   selected transmitter is suggested when available; otherwise a sole receiver
   is suggested. Select **No receiver** for manual setup.
4. With a receiver, press **Stop** on the physical remote during the 30-second
   learning dialog. Any consistently repeated valid Dooya command can learn its
   ID. On timeout or receiver failure, retry or choose manual entry. Manual IDs
   accept decimal or `0x`-prefixed hexadecimal, for example `0x4231157`.
5. Enter the number of numbered channels (1–15). Learning a channel does not
   determine the channel count. No RF command is sent during setup or learning.

For three channels, setup creates **All blinds**, **Blind 1**, **Blind 2**, and **Blind 3** under
the remote's device. Channels run from 1 to the configured count (maximum 15).
**All blinds** uses channel 0, the remote's broadcast channel, which is not included
in that count. Its controls send one broadcast command to all blinds paired with
the remote, using the same frequency, timings, and three-frame sequence.

The device's **Configuration** section also provides one **P2 (Programming)**
button for the whole remote, using channel 1. Both physical P2 keys use the same
command, so only one P2 entity is exposed. It uses the fixed 433.890 MHz RF frequency
and sends `0xCC` four times, with 57.3 ms between frame starts.

Learning identifies an already paired remote; it does not pair the blinds.
**P2 (Programming)** remains a separate, manual operation.

## Physical remote reception

Each remote has one **Remote action** event entity with `open`, `close`, `stop`,
and `p2` event types. Its attributes include the remote ID, channel, and whether
the press was broadcast. Two consecutive identical complete frames confirm a
press; repeats are deduplicated until 300 ms of silence. Unknown valid command
nibbles remain available as cover diagnostics, but do not generate known events.

A numbered press updates only that channel's `last_command`,
`last_command_origin`, and `last_command_timestamp`. Channel 0 updates these
attributes on **All blinds** and every numbered cover and generates one broadcast
event. Numbered-channel presses leave the broadcast cover's diagnostics unchanged. These
observations are **button presses, not verified motor state**. Receiving a command
never transmits another.

Covers estimate position from elapsed travel time; there is no physical position
feedback. Each numbered blind has a **Blind N travel time** setting in the device's
**Configuration** section. Enter its full end-to-end travel time in seconds, shared
by opening and closing (default 30 seconds; range 1–3600, in 0.1-second increments).
Settings are saved across restarts. Changing a time does not move the blind or reset
its position; an ongoing movement keeps the time it started with.

At startup, restart, or integration reload, position and open/closed state are
**unknown**. Open and Close immediately report `opening` or `closing` after a
successful HA transmission, or when a physical remote command is received. From a
known position, the percentage advances linearly with elapsed time. From an unknown
position, the percentage stays unknown until a full uninterrupted travel time has
elapsed in one direction. Stopping earlier keeps it unknown; reversing starts a
new full travel time. Repeating the same direction while moving does not restart
the timer.

At the estimated end of travel, the displayed position stays at 99% when opening
or 1% when closing for an extra **20% of the configured full travel time**. This
margin is a constant, including for partial journeys. Only afterward does the cover
report 100% (`open`) or 0% (`closed`). For example, a blind with a 30-second travel
time starting from unknown shows 99% after 30 seconds of opening and 100% after
36 seconds. Ordinary Open/Close timers only update the UI; the motor stops at its
own end limit.

Stop sends the existing Stop command and freezes the current estimated percentage,
including 99%/1% during the extra-time period. P2 and unknown commands do not start
or stop a movement estimate. Missed remote presses can leave estimates stale.
Radio disconnection does not reset the model or stop an already-running timer; cover availability still follows the
transmitter, independently of the receiver.

Channel 0 (All blinds) starts or stops every blind's estimate with one broadcast
RF command. Each numbered blind uses its own configured time. All blinds has a
separate estimate of the last broadcast action, using the longest configured
travel time; it is not an aggregate measurement or confirmation that every blind
has stopped. Subsequent numbered-channel commands update only that channel.

Numbered covers also support HA's position slider and `cover.set_cover_position`.
When position is unknown, a target below 50% closes fully; a target of 50% or above
opens fully. This establishes an endpoint using the normal full travel time and
20% margin, without a timed Stop or a subsequent move to the original target.
Once position is known, HA sends Open or Close, waits for the calculated travel
time, then sends Stop for intermediate targets. For example, moving from 25% to 75% with
a 20-second travel time takes approximately 10 seconds. Intermediate targets do
not include the 20% endpoint margin. Targets of 0% and 100% use ordinary full
Close/Open behavior, including the margin, and also work from unknown.

A new target replaces the previous one. Manual HA Open/Close/Stop requests and
received physical movement commands cancel the pending timed Stop for affected
channels; broadcast commands cancel all of them. P2, unknown commands, and echoes
do not cancel targets. Reloading or restarting cancels pending targets and resets
position to unknown; it does not send a physical Stop. All blinds has no percentage
slider because its channels can have different starting positions and travel times.

Position control remains approximate: RF latency, transmitter queueing, and missed
commands can cause overshoot. The final estimate reflects elapsed time when Stop
successfully sends, rather than forcing the display to the requested target. If
sending Stop fails, HA logs the error and continues estimating movement toward
the endpoint; it does not claim the blind stopped at the target.

The receiver is independent of the transmitter. A missing/offline receiver makes
Remote action unavailable while covers and P2 remain usable with their transmitter.
ESPHome receivers must already be configured for the relevant 433 MHz OOK signal;
selecting a source in HA does not retune the receiver. The backend discovers native
RF receive channels and subscribes directly to the ESPHome API, including when HA
only exposes transmitters. It ignores IR/unknown/transmit-only channels and
rediscovers RF channels on reconnect. HackRF uses its RX dispatcher contract.

Use the integration entry's **Reconfigure** action to change the transmitter, optional
receiver, or number of channels (1–15). Increasing the count adds covers; decreasing
it removes covers above the new count. Retained channels keep their entity IDs,
and All blinds remains available. You can also
correct the complete 28-bit remote ID (including the upper
nibble of the former channel byte) without recreating the existing entities. An offline/missing configured receiver can be retained or replaced.
Existing entries without a receiver remain transmit-only. v1 entries automatically
migrate from a 24-bit ID to a 28-bit ID by shifting left four bits. Their
original HA entity/device identities are preserved. For nonzero prefixes use
**Reconfigure** to set the full ID: master `0x123C140`, kids `0x7B31154`,
playroom `0x4231157`. Any old channel count above 15 is capped to 15.
The HA minimum remains 2026.9.2; no native HA RF receiver API is required.

Local TX echoes matching the channel and command are suppressed during sending
and for the calculated burst airtime plus 350 ms after the send returns. An
identical physical press inside that window cannot reliably be distinguished
from an echo; different commands are still processed. Cover and P2 operations,
including different remotes on the same transmitter, share a transmission lock.

Receiver lifecycle patterns are adapted from `harf-insoma` and `harf-rhine`;
the MIT attribution is included in `custom_components/harf_dooya/LICENSE.rhine`.

If you installed the earlier development version under the `dooya` domain,
remove that integration entry and its old `custom_components/dooya` directory,
then install this version, restart Home Assistant, and add **Dooya RF**.
Home Assistant treats `harf_dooya` as a separate integration.

## Protocol

The encoder follows OpenHarf's
[`dooya_protocol.cpp`](https://github.com/twasilczyk/openharf/blob/main/firmware/components/remote_base/dooya_protocol.cpp):
28-bit remote ID, 4-bit channel (0–15), and two identical 4-bit button/check values.
Open uses `1`, close `3`, and stop `5`. Each command is sent three times, with
an 8,144 µs silent interval measured from the final RF falling edge to the next
sync rising edge. Programmed timings fitted to the OEM envelope are sync 4,672/1,508 µs,
zero 328/748 µs, and one 680/396 µs (HIGH/LOW). The bit period is 1,076 µs.
Open, Close, and Stop have a 56.968 ms frame start interval.

The carrier is fixed at **433890000 Hz (433.890 MHz)** for all remotes, including
existing entries with saved frequency overrides. The selected transmitter must
support this frequency. The integration does not alter the radio’s crystal
calibration or other protocols.

See [RF transmission details](docs/rf-transmission.md) for the transmission path,
CC1101 register calculation, and repeat timing.

The transmitter selection follows
[`Aetf/hass-proflame`](https://github.com/Aetf/hass-proflame).
Home Assistant's
[`radio_frequency` consumer helper](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/radio_frequency/helpers.py)
handles transmission and availability. The integration resolves the transmitter's
registry UUID and reloads its covers when the transmitter is renamed. No additional
protocol package or ESPHome cover configuration is required.

## Development

Using Python 3.14.2 or newer, run from this directory:

```sh
python -m pip install -r requirements-test.txt
python -m pytest
```

Tests use Home Assistant 2026.9.2 and a simulated RF transmitter to check setup,
cover/P2 commands, protocol timings, independent OEM-like decoding fixtures,
receiver discovery, learning, remote events, echo suppression, availability,
renames, and unloading.

GitHub Actions runs these tests, Hassfest, and HACS validation. See
[RELEASING.md](RELEASING.md) for publishing a version and adding the repository
to the HACS catalog.
