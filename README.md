# Mitsubishi AC IR tools for LIRC

Small reverse-engineered toolkit for generating and decoding Mitsubishi AC infrared
commands for use with LIRC.

Many AC remotes do not send one command per button. Instead, each IR transmission
contains the complete state of the AC: mode, temperature, fan, airflow, and feature
flags. This repository documents one such 48-bit Mitsubishi AC frame and provides a
Python generator for LIRC `raw_codes`.

## Contents

- `mitsubishi_ac_ir.py` builds and decodes Mitsubishi AC IR frames.
- `mitsubishi_ac.lircd.conf` is a ready-to-use generated LIRC config.
- `arduino/irrecvdump/irrecvdump.ino` is a simple Arduino IR receiver sketch for
  capturing raw timings with the older `IRremote` library API.

## Known supported state model

- Temperatures: 17-30 deg C.
- Modes: AC/cool, fan, dry/drop, heat, auto/triangle.
- Fan: low, medium, high/JSP, auto.
- Airflow up/down: on/off.
- Airflow left/right: supported in the bit model, but less thoroughly captured.
- Features: normal, high power, eco.

The 17 deg C state may appear as a blank/minimum temperature on some physical remote
screens. It is still encoded as a valid IR frame.

## Timing model

The generated LIRC raw command uses normalized microsecond timings:

- Header: `5700 7200`
- Bit `0`: `600 1350`
- Bit `1`: `600 3300`
- Trailer: `600 7300 600`

Each command has 48 data bits:

`header + 48 * (mark, space) + trailer`

## Frame layout

Bit positions are 1-indexed.

| Bits | Meaning |
| --- | --- |
| 1-3 | Constant `111` |
| 4 | Airflow up/down, `0` = ON, `1` = OFF |
| 5 | Airflow left/right, `0` = ON, `1` = OFF |
| 6-7 | Fan speed: Low `00`, Medium `10`, High/JSP `01`, Auto `11` |
| 8 | Normal/special bit. Normal frames use `1`; high-power/eco use `0` with fan bits overloaded |
| 9-16 | Logical NOT of bits 1-8 |
| 17-19 | Mode |
| 20 | Unknown/legacy flag. Current generator uses `0` |
| 21-24 | Temperature code, LSB first |
| 25-27 | Logical NOT of bits 17-19 |
| 28 | Logical NOT of bit 20 |
| 29-32 | Logical NOT of bits 21-24 |
| 33-48 | Constant tail `0101010010101011` |

Mode bits:

| Mode/icon | Bits 17-19 |
| --- | --- |
| Drop / dry | `101` |
| Fan | `001` |
| Sun / heat | `110` |
| Triangle / auto | `111` |
| AC / cool | `011` |

Temperature encoding:

`temp_code = 32 - temp_deg_c`, stored LSB first in bits 21-24.

## Usage

Run the self-test:

```sh
python3 mitsubishi_ac_ir.py self-test
```

Generate one command:

```sh
python3 mitsubishi_ac_ir.py generate --temp 17 --mode AC --fan Auto --air-ud ON --air-lr ON
```

Regenerate the included LIRC config:

```sh
python3 mitsubishi_ac_ir.py generate-config --output mitsubishi_ac.lircd.conf
```

Decode a saved Arduino dump or CSV timing file:

```sh
python3 mitsubishi_ac_ir.py decode path/to/capture.txt
```

## LIRC example

Install or copy `mitsubishi_ac.lircd.conf` into your LIRC configuration, then send a
command like:

```sh
irsend SEND_ONCE MITSUBISHI_AC KEY_AC_COOL_AUTO_UD_ON_LR_ON_17
```

If you merge commands into an existing LIRC remote, keep the command names consistent
with your existing config.

## Arduino capture sketch

The Arduino sketch uses the older `IRremote` API:

```sh
arduino-cli lib install 'IRremote@2.2.3'
arduino-cli compile --fqbn arduino:avr:uno arduino/irrecvdump
```

An IR receiver/demodulator should be connected to the sketch's `RECV_PIN`.
