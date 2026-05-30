#!/usr/bin/env python3
"""Build and decode Mitsubishi AC LIRC raw IR commands.

This script is intentionally small and boring:

- build a 48-bit Mitsubishi AC frame from named fields;
- validate the inverse/check fields;
- turn the frame into normalized LIRC raw timings;
- decode Arduino `Raw (98)` dumps or CSV timing rows back into fields.

The default generated state matches the latest low-temperature capture:
AC/cool mode, low fan, airflow up/down ON, airflow left/right ON.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


HEADER = (5700, 7200)
BIT_MARK = 600
ZERO_SPACE = 1350
ONE_SPACE = 3300
TRAILER = (600, 7300, 600)
TAIL_BITS = (0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 1)
MIN_TEMP = 17
MAX_TEMP = 30

AIRFLOW_BITS = {
    "ON": 0,
    "OFF": 1,
}

FAN_BITS = {
    "Low": (0, 0),
    "Medium": (1, 0),
    "High": (0, 1),
    "JSP": (0, 1),
    "Auto": (1, 1),
}

MODE_BITS = {
    "Drop": (1, 0, 1),
    "Dry": (1, 0, 1),
    "Fan": (0, 0, 1),
    "Sun": (1, 1, 0),
    "Heat": (1, 1, 0),
    "Triangle": (1, 1, 1),
    "Auto": (1, 1, 1),
    "AC": (0, 1, 1),
    "Cool": (0, 1, 1),
}

MODE_NAMES = {
    (1, 0, 1): "Drop",
    (0, 0, 1): "Fan",
    (1, 1, 0): "Heat",
    (1, 1, 1): "Triangle",
    (0, 1, 1): "AC",
}

MODE_COMMAND_NAMES = {
    "AC": "COOL",
    "Cool": "COOL",
    "Drop": "DRY",
    "Dry": "DRY",
    "Fan": "FAN",
    "Sun": "HEAT",
    "Heat": "HEAT",
    "Triangle": "AUTO",
    "Auto": "AUTO",
}

FAN_NAMES = {
    (0, 0): "Low",
    (1, 0): "Medium",
    (0, 1): "High",
    (1, 1): "Auto",
}

# Captures show high power and eco as a special encoding in bits 6-8.
FEATURE_BITS = {
    "Normal": (None, 1),
    "HighPower": ((1, 1), 0),
    "Eco": ((0, 1), 0),
}

FEATURE_COMMAND_NAMES = {
    "Normal": "",
    "HighPower": "HIGH_POWER",
    "Eco": "ECO",
}


def canonical(value: str, choices: Iterable[str]) -> str:
    """Return a canonical choice name, ignoring case, spaces, hyphens, underscores."""
    cleaned = re.sub(r"[\s_-]+", "", value).lower()
    for choice in choices:
        if re.sub(r"[\s_-]+", "", choice).lower() == cleaned:
            return choice
    raise ValueError(f"unknown value {value!r}; expected one of {', '.join(choices)}")


def invert(bits: Sequence[int]) -> list[int]:
    return [0 if bit else 1 for bit in bits]


def temp_bits(temp: int) -> list[int]:
    if not MIN_TEMP <= temp <= MAX_TEMP:
        raise ValueError(f"temperature must be between {MIN_TEMP} and {MAX_TEMP} deg C")
    code = 32 - temp
    return [(code >> index) & 1 for index in range(4)]


def bits_to_int_lsb(bits: Sequence[int]) -> int:
    return sum(bit << index for index, bit in enumerate(bits))


@dataclass(frozen=True)
class ACState:
    temp: int = 23
    mode: str = "AC"
    fan: str = "Low"
    air_ud: str = "ON"
    air_lr: str = "ON"
    feature: str = "Normal"

    def normalized(self) -> "ACState":
        return ACState(
            temp=int(self.temp),
            mode=canonical(self.mode, MODE_BITS),
            fan=canonical(self.fan, FAN_BITS),
            air_ud=canonical(self.air_ud, AIRFLOW_BITS),
            air_lr=canonical(self.air_lr, AIRFLOW_BITS),
            feature=canonical(self.feature, FEATURE_BITS),
        )

    def command_name(self) -> str:
        state = self.normalized()
        mode = MODE_COMMAND_NAMES[state.mode]
        feature = "" if state.feature == "Normal" else f"_{FEATURE_COMMAND_NAMES[state.feature]}"
        return f"KEY_AC_{mode}_{state.fan.upper()}_UD_{state.air_ud}_LR_{state.air_lr}{feature}_{state.temp}"


def build_bits(state: ACState) -> list[int]:
    state = state.normalized()
    air_ud = AIRFLOW_BITS[state.air_ud]
    air_lr = AIRFLOW_BITS[state.air_lr]
    fan = list(FAN_BITS[state.fan])
    feature_fan, special_bit = FEATURE_BITS[state.feature]
    if feature_fan is not None:
        fan = list(feature_fan)

    first_byte = [1, 1, 1, air_ud, air_lr, *fan, special_bit]
    mode = list(MODE_BITS[state.mode])
    temp = temp_bits(state.temp)
    bits = (
        first_byte
        + invert(first_byte)
        + mode
        + [0]
        + temp
        + invert(mode)
        + [1]
        + invert(temp)
        + list(TAIL_BITS)
    )
    validate_bits(bits)
    return bits


def validate_bits(bits: Sequence[int]) -> None:
    if len(bits) != 48:
        raise ValueError(f"expected 48 bits, got {len(bits)}")
    if list(bits[0:3]) != [1, 1, 1]:
        raise ValueError("bits 1-3 are not the expected 111 prefix")
    if list(bits[8:16]) != invert(bits[0:8]):
        raise ValueError("bits 9-16 are not the inverse of bits 1-8")
    if bits[27] != invert([bits[19]])[0]:
        raise ValueError("bit 28 is not the inverse of bit 20")
    if list(bits[24:27]) != invert(bits[16:19]):
        raise ValueError("bits 25-27 are not the inverse of bits 17-19")
    if list(bits[28:32]) != invert(bits[20:24]):
        raise ValueError("bits 29-32 are not the inverse of bits 21-24")
    if tuple(bits[32:48]) != TAIL_BITS:
        raise ValueError("bits 33-48 do not match the constant tail")


def state_from_bits(bits: Sequence[int]) -> dict[str, object]:
    validate_bits(bits)
    air_ud = "ON" if bits[3] == 0 else "OFF"
    air_lr = "ON" if bits[4] == 0 else "OFF"
    fan_tuple = tuple(bits[5:7])
    special_bit = bits[7]

    feature = "Normal"
    if special_bit == 0 and fan_tuple == (1, 1):
        feature = "HighPower"
    elif special_bit == 0 and fan_tuple == (0, 1):
        feature = "Eco"
    elif special_bit == 0:
        feature = f"UnknownSpecial{fan_tuple}"

    fan = FAN_NAMES.get(fan_tuple, f"Unknown{fan_tuple}")
    if feature != "Normal":
        fan = "Overridden"
    mode = MODE_NAMES.get(tuple(bits[16:19]), f"Unknown{tuple(bits[16:19])}")
    temp = 32 - bits_to_int_lsb(bits[20:24])
    return {
        "temp": temp,
        "mode": mode,
        "fan": fan,
        "air_ud": air_ud,
        "air_lr": air_lr,
        "feature": feature,
        "bit20": bits[19],
        "bits": "".join(str(bit) for bit in bits),
    }


def timings_from_bits(bits: Sequence[int]) -> list[int]:
    validate_bits(bits)
    timings = list(HEADER)
    for bit in bits:
        timings.append(BIT_MARK)
        timings.append(ONE_SPACE if bit else ZERO_SPACE)
    timings.extend(TRAILER)
    return timings


def bits_from_timings(numbers: Sequence[int]) -> list[int]:
    """Decode full LIRC/CSV timings or Arduino Raw (98) timings into 48 bits."""
    if len(numbers) >= 101:
        # Full timing row: header + 48 mark/space pairs + trailer.
        data = list(numbers[2:98])
    elif len(numbers) == 98:
        # Arduino Raw (98): leading gap, then 48 mark/space pairs, then final mark.
        data = list(numbers[1:97])
    else:
        raise ValueError(f"need a full timing row or Raw (98), got {len(numbers)} numbers")

    if len(data) != 96:
        raise ValueError(f"expected 96 mark/space data timings, got {len(data)}")

    spaces = [abs(data[index + 1]) for index in range(0, 96, 2)]
    threshold = 2300 if max(spaces) > 1000 else 250
    bits = [1 if space > threshold else 0 for space in spaces]
    validate_bits(bits)
    return bits


def parse_numbers(text: str) -> list[int]:
    return [int(match) for match in re.findall(r"-?\d+", text)]


def load_text(path: Path) -> str:
    if path.suffix.lower() == ".rtf":
        try:
            return subprocess.check_output(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    return path.read_text(errors="replace")


def decode_lines(path: Path) -> list[tuple[str, dict[str, object]]]:
    text = load_text(path)
    decoded: list[tuple[str, dict[str, object]]] = []

    for index, raw in enumerate(re.findall(r"Raw \(98\):([^\n]+)", text), start=1):
        bits = bits_from_timings(parse_numbers(raw))
        decoded.append((f"raw{index}", state_from_bits(bits)))

    if decoded:
        return decoded

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        label = re.split(r"[;,]", stripped, maxsplit=1)[0].strip() or f"line{line_number}"
        if ":" in stripped and len(parse_numbers(stripped)) < 90:
            continue
        parts = re.split(r"[;,]", stripped, maxsplit=1)
        number_text = parts[1] if len(parts) == 2 else stripped
        numbers = parse_numbers(number_text)
        if len(numbers) < 90:
            continue
        try:
            bits = bits_from_timings(numbers)
        except ValueError as exc:
            decoded.append((label, {"error": str(exc)}))
            continue
        decoded.append((label, state_from_bits(bits)))
    return decoded


def chunked(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def format_timing_block(timings: Sequence[int], indent: str = "    ") -> str:
    return "\n".join(
        indent + " ".join(f"{value:4d}" for value in chunk)
        for chunk in chunked(timings, 6)
    )


def lirc_raw_code(name: str, timings: Sequence[int]) -> str:
    return f"      name {name}\n{format_timing_block(timings, indent='        ')}"


def default_config_states() -> list[ACState]:
    states: list[ACState] = []

    # Temperature sweep matching the newest capture conditions.
    for temp in range(MIN_TEMP, MAX_TEMP + 1):
        states.append(ACState(temp=temp, mode="AC", fan="Low", air_ud="ON", air_lr="ON"))
        states.append(ACState(temp=temp, mode="AC", fan="Auto", air_ud="ON", air_lr="ON"))

    # Common one-field changes at a safe reference temperature.
    for fan in ("Low", "Medium", "High", "Auto"):
        states.append(ACState(temp=23, mode="AC", fan=fan, air_ud="ON", air_lr="ON"))
    for mode in ("AC", "Fan", "Drop", "Heat", "Triangle"):
        states.append(ACState(temp=23, mode=mode, fan="Low", air_ud="ON", air_lr="ON"))
    for air_ud in ("ON", "OFF"):
        states.append(ACState(temp=23, mode="AC", fan="Low", air_ud=air_ud, air_lr="ON"))
    for feature in ("Normal", "HighPower", "Eco"):
        states.append(ACState(temp=23, mode="AC", fan="Low", air_ud="ON", air_lr="ON", feature=feature))

    unique: dict[str, ACState] = {}
    for state in states:
        unique[state.command_name()] = state
    return list(unique.values())


def generate_lirc_config(states: Sequence[ACState], remote_name: str) -> str:
    blocks = []
    for state in states:
        bits = build_bits(state)
        blocks.append(lirc_raw_code(state.command_name(), timings_from_bits(bits)))

    body = "\n\n".join(blocks)
    return f"""begin remote

  name  {remote_name}
  flags RAW_CODES
  eps            30
  aeps          100
  gap        100000

  begin raw_codes

{body}

  end raw_codes

end remote
"""


def print_decoded(path: Path) -> None:
    rows = decode_lines(path)
    if not rows:
        raise SystemExit(f"no decodable timing rows found in {path}")

    for label, state in rows:
        if "error" in state:
            print(f"{label}: ERROR {state['error']}")
            continue
        print(
            "{label}: temp={temp} mode={mode} fan={fan} "
            "air_ud={air_ud} air_lr={air_lr} feature={feature} bit20={bit20} bits={bits}".format(
                label=label,
                **state,
            )
        )


def command_generate(args: argparse.Namespace) -> None:
    state = ACState(
        temp=args.temp,
        mode=args.mode,
        fan=args.fan,
        air_ud=args.air_ud,
        air_lr=args.air_lr,
        feature=args.feature,
    )
    bits = build_bits(state)
    timings = timings_from_bits(bits)
    print(f"name {state.command_name()}")
    print(f"bits {''.join(str(bit) for bit in bits)}")
    print(format_timing_block(timings))


def command_generate_config(args: argparse.Namespace) -> None:
    config = generate_lirc_config(default_config_states(), args.remote_name)
    output = Path(args.output)
    output.write_text(config)
    print(f"wrote {output} with {len(default_config_states())} raw commands")


def command_self_test() -> None:
    expected = {
        21: "111000010001111001101101100100100101010010101011",
        20: "111000010001111001100011100111000101010010101011",
        19: "111000010001111001101011100101000101010010101011",
        18: "111000010001111001100111100110000101010010101011",
        17: "111000010001111001101111100100000101010010101011",
    }
    for temp, bits_text in expected.items():
        state = ACState(temp=temp, mode="AC", fan="Low", air_ud="ON", air_lr="ON")
        actual = "".join(str(bit) for bit in build_bits(state))
        if actual != bits_text:
            raise SystemExit(f"self-test failed for {temp}: {actual} != {bits_text}")
    print("self-test ok")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="print one raw LIRC command")
    generate.add_argument("--temp", type=int, default=23)
    generate.add_argument("--mode", default="AC", choices=sorted(MODE_BITS))
    generate.add_argument("--fan", default="Low", choices=sorted(FAN_BITS))
    generate.add_argument("--air-ud", default="ON", choices=sorted(AIRFLOW_BITS))
    generate.add_argument("--air-lr", default="ON", choices=sorted(AIRFLOW_BITS))
    generate.add_argument("--feature", default="Normal", choices=sorted(FEATURE_BITS))
    generate.set_defaults(func=command_generate)

    decode = subparsers.add_parser("decode", help="decode an Arduino dump or CSV timing file")
    decode.add_argument("path", type=Path)
    decode.set_defaults(func=lambda args: print_decoded(args.path))

    config = subparsers.add_parser("generate-config", help="write a practical LIRC config")
    config.add_argument("--output", default="mitsubishi_ac.lircd.conf")
    config.add_argument("--remote-name", default="MITSUBISHI_AC")
    config.set_defaults(func=command_generate_config)

    self_test = subparsers.add_parser("self-test", help="verify generator against known captures")
    self_test.set_defaults(func=lambda _args: command_self_test())

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
