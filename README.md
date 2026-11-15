# Dooya RF

> [!WARNING]
> This project is mostly AI generated and not well reviewed.
>
> USE AT YOUR OWN RISK

A small Home Assistant custom integration for Dooya blinds, using any compatible
`radio_frequency` transmitter. Creates one device per remote and one cover per
numbered channel, with **open**, **close**, and **stop**.

Project: `harf-dooya`. Home Assistant domain: `harf_dooya`.

## Requirements

- Home Assistant **2026.9.2 or newer**. This is the minimum tested version.
- An RF transmitter already added to Home Assistant, exposing a
  `radio_frequency` entity supporting **433.934 MHz OOK**. A `remote` entity alone
  does not provide this API.
- The ID and channel count of a remote already paired with your blinds.

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
2. Select the transmitter, enter your paired remote's ID (decimal or `0x`-prefixed
   hexadecimal, for example `0x00123C14`), and enter its number of channels.

For three channels, setup creates **Blind 1**, **Blind 2**, and **Blind 3** under
the remote's device. Channels run from 1 to the configured count (maximum 255).
Channel 0, the remote's broadcast channel, is not included in that count. You can
use a Home Assistant cover group to control several of these covers together.

The device's **Configuration** section also provides one **P2 (Programming)**
button for the whole remote, using channel 1. Both physical P2 keys use the same
command, so only one P2 entity is exposed. It uses that remote's configured RF frequency
and sends `0xCC` four times, with 57.3 ms between frame starts.

The integration does not receive signals or learn remote IDs yet. The remote
must already be paired with the blinds. There is no position feedback or travel
timer: state stays unknown and both direction controls remain available, as with
the existing ESPHome template covers. Availability follows the transmitter.

If you installed the earlier development version under the `dooya` domain,
remove that integration entry and its old `custom_components/dooya` directory,
then install this version, restart Home Assistant, and add **Dooya RF**.
Home Assistant treats `harf_dooya` as a separate integration.

## Protocol

The encoder follows OpenHarf's
[`dooya_protocol.cpp`](https://github.com/twasilczyk/openharf/blob/main/firmware/components/remote_base/dooya_protocol.cpp):
24-bit remote ID, 8-bit channel, and two identical 4-bit button/check values.
Open uses `1`, close `3`, and stop `5`. Each command is sent three times, with
an 8,144 µs silent interval measured from the final RF falling edge to the next
sync rising edge. Programmed timings fitted to the OEM envelope are sync 4,672/1,508 µs,
zero 328/748 µs, and one 680/396 µs (HIGH/LOW). The bit period is 1,076 µs.
Open, Close, and Stop have a 56.968 ms frame start interval.

The carrier defaults to **433.934 MHz**, including for existing entries. Use the
integration’s **Configure** action to set a different carrier frequency in Hz for
each remote. The selected transmitter must support that frequency. Saving reloads
the covers; it does not alter the radio’s crystal calibration or other protocols.

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
cover commands, protocol timings, availability, renames, and unloading.

GitHub Actions runs these tests, Hassfest, and HACS validation. See
[RELEASING.md](RELEASING.md) for publishing a version and adding the repository
to the HACS catalog.
