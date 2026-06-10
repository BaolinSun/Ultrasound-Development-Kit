#!/usr/bin/env python3
"""Send ultrasound startup configuration to Zynq PS over UART.

The PS firmware accepts short ASCII commands. This tool keeps JSON parsing and
register packing on the PC side so the bare-metal firmware can stay small.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    import serial
except ImportError:  # pragma: no cover - depends on PC environment
    serial = None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AFE_CONFIG = SCRIPT_DIR / "configs" / "afe5832_default.json"
DEFAULT_BRAM_CONFIG = SCRIPT_DIR / "configs" / "bram_default.json"

LNA_GAIN = {18: 0b00, 21: 0b01, 15: 0b11}
PGA_GAIN = {24: 0b00, 27: 0b01, 21: 0b10}
LPF_PROG = {20: 0b00, 25: 0b01, 10: 0b10, 15: 0b11}
HPF_KHZ = {
    100: 0b00000,
    110: 0b00001,
    120: 0b00010,
    130: 0b00011,
    140: 0b00100,
    150: 0b00101,
    160: 0b00110,
    170: 0b00111,
    20: 0b01000,
    30: 0b01001,
    40: 0b01010,
    50: 0b01011,
    60: 0b01100,
    70: 0b01101,
    80: 0b01110,
    90: 0b01111,
    260: 0b10000,
    270: 0b10001,
    290: 0b10010,
    300: 0b10011,
    310: 0b10100,
    180: 0b11000,
    190: 0b11001,
    200: 0b11010,
    210: 0b11011,
    220: 0b11100,
    230: 0b11101,
    240: 0b11110,
    250: 0b11111,
}
DHPF_REGS = {0: 0x15, 1: 0x21, 2: 0x2D, 3: 0x39}
DTGC_PROFILE_BASE = {0: 0xA1, 1: 0xA6, 2: 0xAB, 3: 0xB0}
VALID_MAPS = {"GLOBAL", "ADC", "VCA", "DTGC"}
BRAM_SECTIONS = {
    "fdemod_sin": 8192,
    "fdemod_cos": 8192,
    "log_table": 1024,
    "x_element": 64,
    "hamming": 64,
    "dfilter": 64,
    "t_timing": 10,
    "sin_beta": 64,
    "delay_profile": 16384,
    "tx7332_config_data": 128,
}


def parse_int(value: Any, name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{name} must be an integer or integer string")


def require_range(value: int, bits: int, name: str) -> int:
    limit = (1 << bits) - 1
    if not 0 <= value <= limit:
        raise ValueError(f"{name} must fit in {bits} bits, got {value}")
    return value


def add_reg(commands: list[str], reg_map: str, addr: int, data: int) -> None:
    reg_map = reg_map.upper()
    if reg_map not in VALID_MAPS:
        raise ValueError(f"invalid AFE register map: {reg_map}")
    require_range(addr, 8, "AFE address")
    require_range(data, 16, "AFE data")
    commands.append(f"AFE WR {reg_map} 0x{addr:02X} 0x{data:04X}")


def build_vca_commands(afe: dict[str, Any], commands: list[str]) -> None:
    vca = afe.get("vca")
    if not vca:
        return

    lna_db = parse_int(vca.get("lna_gain_db", 18), "lna_gain_db")
    pga_db = parse_int(vca.get("pga_gain_db", 24), "pga_gain_db")
    lpf_mhz = parse_int(vca.get("lpf_mhz", 20), "lpf_mhz")
    hpf_khz = parse_int(vca.get("lna_hpf_khz", 100), "lna_hpf_khz")
    power_mode = str(vca.get("power_mode", "low_noise")).lower()

    if lna_db not in LNA_GAIN:
        raise ValueError("lna_gain_db must be one of 15, 18, 21")
    if pga_db not in PGA_GAIN:
        raise ValueError("pga_gain_db must be one of 21, 24, 27")
    if lpf_mhz not in LPF_PROG:
        raise ValueError("lpf_mhz must be one of 10, 15, 20, 25")
    if hpf_khz not in HPF_KHZ:
        raise ValueError("lna_hpf_khz is not supported by AFE5832 register C7")
    if power_mode not in {"low_noise", "low_power"}:
        raise ValueError("power_mode must be low_noise or low_power")
    if power_mode == "low_power" and lpf_mhz == 25:
        raise ValueError("25 MHz LPF is not supported in low-power mode")

    lpf1 = LPF_PROG[lpf_mhz]
    lpf2 = 0 if power_mode == "low_power" else lpf1
    c7 = (
        (lpf2 << 14)
        | (HPF_KHZ[hpf_khz] << 9)
        | (lpf1 << 5)
        | (PGA_GAIN[pga_db] << 2)
        | LNA_GAIN[lna_db]
    )
    add_reg(commands, "VCA", 0xC7, c7)
    add_reg(commands, "VCA", 0xC8, 1 if power_mode == "low_power" else 0)


def build_dhpf_commands(afe: dict[str, Any], commands: list[str]) -> None:
    dhpf = afe.get("dhpf")
    if not dhpf:
        return
    for item in dhpf.get("groups", []):
        group = parse_int(item.get("group"), "dhpf.group")
        if group not in DHPF_REGS:
            raise ValueError("dhpf group must be 0, 1, 2, or 3")
        enable = 1 if item.get("enable", True) else 0
        corner = require_range(parse_int(item.get("corner_code", 3), "corner_code"), 4, "corner_code")
        round_enable = 1 if item.get("round_enable", False) else 0
        data = (round_enable << 5) | (corner << 1) | enable
        add_reg(commands, "ADC", DHPF_REGS[group], data)


def build_dtgc_commands(afe: dict[str, Any], commands: list[str]) -> None:
    dtgc = afe.get("dtgc")
    if not dtgc:
        return
    for profile in dtgc.get("profiles", []):
        idx = parse_int(profile.get("profile"), "dtgc.profile")
        if idx not in DTGC_PROFILE_BASE:
            raise ValueError("dtgc profile must be 0, 1, 2, or 3")
        base = DTGC_PROFILE_BASE[idx]
        start_gain = require_range(parse_int(profile.get("start_gain", 0), "start_gain"), 8, "start_gain")
        stop_gain = require_range(parse_int(profile.get("stop_gain", 0x90), "stop_gain"), 8, "stop_gain")
        pos_step = require_range(parse_int(profile.get("pos_step", 0), "pos_step"), 5, "pos_step")
        pos_step_feq = require_range(parse_int(profile.get("pos_step_feq", 0), "pos_step_feq"), 3, "pos_step_feq")
        neg_step = require_range(parse_int(profile.get("neg_step", 0x1F), "neg_step"), 5, "neg_step")
        neg_step_feq = require_range(parse_int(profile.get("neg_step_feq", 0x07), "neg_step_feq"), 3, "neg_step_feq")
        add_reg(commands, "DTGC", base + 0, (start_gain << 8) | stop_gain)
        add_reg(commands, "DTGC", base + 1, (pos_step << 11) | (pos_step_feq << 8) | (neg_step << 3) | neg_step_feq)

        # Optional extended profile fields write the following DTGC registers.
        if any(key in profile for key in ("start_index", "stop_index", "start_gain_time", "hold_gain_time")):
            start_index = require_range(parse_int(profile.get("start_index", 0), "start_index"), 8, "start_index")
            stop_index = require_range(parse_int(profile.get("stop_index", 0x90), "stop_index"), 8, "stop_index")
            start_gain_time = require_range(
                parse_int(profile.get("start_gain_time", 0), "start_gain_time"), 16, "start_gain_time"
            )
            hold_gain_time = require_range(
                parse_int(profile.get("hold_gain_time", 0), "hold_gain_time"), 16, "hold_gain_time"
            )
            add_reg(commands, "DTGC", base + 2, (start_index << 8) | stop_index)
            add_reg(commands, "DTGC", base + 3, start_gain_time)
            add_reg(commands, "DTGC", base + 4, hold_gain_time)

    for reg in dtgc.get("raw_registers", []):
        add_reg(
            commands,
            "DTGC",
            parse_int(reg.get("addr"), "dtgc.raw_registers.addr"),
            parse_int(reg.get("data"), "dtgc.raw_registers.data"),
        )


def build_raw_commands(afe: dict[str, Any], commands: list[str]) -> None:
    for reg in afe.get("raw_registers", []):
        add_reg(
            commands,
            str(reg.get("map", "")).upper(),
            parse_int(reg.get("addr"), "raw_registers.addr"),
            parse_int(reg.get("data"), "raw_registers.data"),
        )


def build_afe_commands(config: dict[str, Any]) -> list[str]:
    afe = config.get("afe5832", {})
    commands: list[str] = []
    if "input_impedance_ohm" in afe:
        print(
            "note: afe5832.input_impedance_ohm is recorded only; "
            "AFE5832LP has no direct input-impedance register in this interface.",
            file=sys.stderr,
        )
    build_vca_commands(afe, commands)
    build_dhpf_commands(afe, commands)
    build_dtgc_commands(afe, commands)
    build_raw_commands(afe, commands)
    return commands


def build_commands(config: dict[str, Any]) -> list[str]:
    return build_afe_commands(config) + ["CFG END"]


def parse_bram_word(value: Any, name: str) -> int:
    word = parse_int(value, name)
    require_range(word, 32, name)
    return word


def build_bram_commands(config: dict[str, Any], chunk_words: int) -> list[str]:
    if not 1 <= chunk_words <= 8:
        raise ValueError("--chunk-words must be in the range 1..8")

    bram = config.get("bram", {})
    commands: list[str] = []
    for section, expected_len in BRAM_SECTIONS.items():
        if section not in bram:
            continue
        values = bram[section]
        if not isinstance(values, list):
            raise ValueError(f"bram.{section} must be a list")
        if len(values) != expected_len:
            raise ValueError(f"bram.{section} must contain {expected_len} words, got {len(values)}")
        words = [parse_bram_word(value, f"bram.{section}[{idx}]") for idx, value in enumerate(values)]
        for offset in range(0, len(words), chunk_words):
            chunk = words[offset : offset + chunk_words]
            payload = " ".join(f"0x{word:08X}" for word in chunk)
            commands.append(f"BRAM WR {section} {offset} {len(chunk)} {payload}")

    unknown = sorted(set(bram) - set(BRAM_SECTIONS))
    if unknown:
        raise ValueError(f"unknown BRAM section(s): {', '.join(unknown)}")
    return commands


def read_response(port: Any, seq: int, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = port.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace").strip()
        if line:
            print(f"< {line}")
        if line.startswith(f"@ACK {seq} ") or line.startswith(f"@RD {seq} "):
            return line
        if line.startswith(f"@NACK {seq} ") or line.startswith("@NACK 0 "):
            raise RuntimeError(line)
    raise TimeoutError("timed out waiting for PS acknowledgement")


def send_line(port: Any, line: str, seq: int, timeout_s: float) -> None:
    print(f"> {line}")
    port.write((line + "\n").encode("ascii"))
    port.flush()
    read_response(port, seq, timeout_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3", help="Serial port, default COM3")
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument(
        "--target",
        choices=("afe", "bram", "both"),
        default="afe",
        help="Configuration target, default afe",
    )
    parser.add_argument("--config", type=Path, help="Legacy AFE5832 JSON config path")
    parser.add_argument("--afe-config", type=Path, default=DEFAULT_AFE_CONFIG, help="AFE5832 JSON config path")
    parser.add_argument("--bram-config", type=Path, default=DEFAULT_BRAM_CONFIG, help="BRAM imaging-parameter JSON config path")
    parser.add_argument("--chunk-words", type=int, default=8, help="Words per BRAM WR command, max 8")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true", help="Print commands without opening the serial port")
    args = parser.parse_args()

    seq = int(time.time() * 1000) & 0xFFFFFFFF
    afe_config_path = args.config or args.afe_config
    commands = [f"CFG BEGIN {seq}"]

    if args.target in ("bram", "both") and args.bram_config is not None:
        bram_config = json.loads(args.bram_config.read_text(encoding="utf-8"))
        commands += build_bram_commands(bram_config, args.chunk_words)
    if args.target in ("afe", "both") and afe_config_path is not None:
        afe_config = json.loads(afe_config_path.read_text(encoding="utf-8"))
        commands += build_afe_commands(afe_config)
    if args.target in ("afe", "both") and afe_config_path is None:
        raise SystemExit("provide --afe-config or --config for AFE configuration")
    if args.target in ("bram", "both") and args.bram_config is None:
        raise SystemExit("provide --bram-config for BRAM configuration")
    commands.append("CFG END")

    if args.dry_run:
        for line in commands:
            print(line)
        return 0

    if serial is None:
        raise SystemExit("pyserial is required: pip install pyserial")

    with serial.Serial(args.port, args.baud, timeout=0.1) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        for line in commands:
            send_line(port, line, seq, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
