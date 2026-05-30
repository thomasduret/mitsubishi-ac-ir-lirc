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

## How the frame layout was found

The layout was derived empirically from captures of the original remote:

1. Capture raw IR timings with an Arduino IR receiver while changing only one thing
   on the remote at a time, such as temperature, fan speed, mode, or airflow.
2. Convert the timings into bits. The captures have one repeated mark length and two
   clear space lengths, so short spaces become `0` and long spaces become `1`.
3. Put captures side by side and compare only the bits that changed. A temperature
   sweep is especially useful because all non-temperature settings are held constant.
4. Notice the protection pattern: bits 9-16 are the logical NOT of bits 1-8, and bits
   25-32 protect the mode/temp area in the same way.
5. Identify constants: the first three bits are always `111`, and the last 16 bits are
   always `0101010010101011` in the captured states.
6. Label fields by controlled changes:
   - changing fan speed only moves bits 6-7;
   - changing mode only moves bits 17-19;
   - changing temperature only moves bits 21-24 and their inverse bits 29-32;
   - changing airflow up/down moves bit 4 and its inverse bit 12.
7. Derive the temperature formula by reading bits 21-24 as LSB-first integers. The
   pattern matches `temp_code = 32 - temp_deg_c`.

This is why the script includes both a generator and a decoder: new captures can be
decoded back into named fields and checked against the inferred layout.

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

## What goes in `path/to/capture.txt`

`path/to/capture.txt` is just a text file containing the serial output from an IR
receiver capture. You make it by uploading the Arduino sketch in
`arduino/irrecvdump/irrecvdump.ino`, opening the Arduino Serial Monitor at `9600`
baud, pointing the AC remote at the IR receiver, and pressing one or more buttons.

The decoder looks for Arduino `Raw (98): ...` lines like this:

```text
FFFFFFFF
FFFFFFFF (0 bits)
Raw (98): -7400 550 -3400 500 -3400 550 -3400 500 -1450 ...
```

Save that output exactly as text, then decode it:

```sh
python3 mitsubishi_ac_ir.py decode captures/min-temp.txt
```

The file may contain several captures one after another. The decoder will print one
decoded state per `Raw (98)` line, for example:

```text
raw1: temp=21 mode=AC fan=Low air_ud=ON air_lr=ON feature=Normal ...
raw2: temp=20 mode=AC fan=Low air_ud=ON air_lr=ON feature=Normal ...
```

The decoder can also read older CSV/timing rows where each row starts with a label
followed by raw timing numbers, but the Arduino `Raw (98)` text is the simplest
format to reproduce.

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
