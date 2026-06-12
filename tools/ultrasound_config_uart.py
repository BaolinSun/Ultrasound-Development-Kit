#!/usr/bin/env python3
"""Send ultrasound runtime configuration to Zynq PS over UART.

The PS firmware accepts short ASCII commands. This tool keeps JSON parsing and
register packing on the PC side so the bare-metal firmware can stay small.
The firmware freezes imaging and disables HV during each configuration
transaction, then restarts imaging after a successful CFG END.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from calc_dtgc_registers import calc_dtgc_registers

try:
    import serial
except ImportError:  # pragma: no cover - depends on PC environment
    serial = None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AFE_CONFIG = SCRIPT_DIR / "configs" / "afe5832_default.json"
DEFAULT_TX7332_CONFIG = SCRIPT_DIR / "configs" / "tx7332_default.json"
DEFAULT_DELAY_PROFILE_CONFIG = SCRIPT_DIR / "configs" / "delay_profile_default.json"
DEFAULT_DEMOD_COEFFS_CONFIG = SCRIPT_DIR / "configs" / "demod_coeffs_default.json"
DEFAULT_LOG_TABLE_CONFIG = SCRIPT_DIR / "configs" / "log_table_default.json"
DEFAULT_X_ELEMENT_CONFIG = SCRIPT_DIR / "configs" / "x_element_default.json"
DEFAULT_HAMMING_CONFIG = SCRIPT_DIR / "configs" / "hamming_default.json"
DEFAULT_DFILTER_CONFIG = SCRIPT_DIR / "configs" / "dfilter_default.json"
DEFAULT_T_TIMING_CONFIG = SCRIPT_DIR / "configs" / "t_timing_default.json"
DEFAULT_SIN_BETA_CONFIG = SCRIPT_DIR / "configs" / "sin_beta_default.json"

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
DTGC_MEMORY_WORDS = 160
DTGC_CALC_FLOAT_KEYS = (
    "adc_clk_hz",
    "sound_speed",
    "dtgc_start_depth_cm",
    "max_gain_depth_cm",
    "ramp_down_depth_cm",
)
DTGC_CALC_INT_KEYS = (
    "lna_gain_db",
    "pga_gain_db",
    "start_gain_code",
    "stop_gain_code",
    "pos_step_code",
    "neg_step_code",
    "slope_fac",
    "mem_bank",
)
DTGC_CALC_KEYS = DTGC_CALC_FLOAT_KEYS + DTGC_CALC_INT_KEYS
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
}
TX7332_SECTIONS = {"tx7332_config_data": 128}
BRAM_TARGET_ORDER = (
    "delay_profile",
    "demod_coeffs",
    "log_table",
    "x_element",
    "hamming",
    "dfilter",
    "t_timing",
    "sin_beta",
)
ACK_RE = re.compile(r"^@ACK\s+(\d+)\s+(\S+)$")
NACK_RE = re.compile(r"^@NACK\s+(\d+)\s+(.+)$")


def parse_int(value: Any, name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{name} must be an integer or integer string")


def parse_float(value: Any, name: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise ValueError(f"{name} must be a number or numeric string")


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
    if has_dtgc_calc_params(dtgc):
        build_dtgc_internal_nonuniform_commands(dtgc, commands)
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


def has_dtgc_calc_params(dtgc: Any) -> bool:
    return isinstance(dtgc, dict) and all(key in dtgc for key in DTGC_CALC_KEYS)


def build_dtgc_internal_nonuniform_commands(dtgc: dict[str, Any], commands: list[str]) -> None:
    kwargs: dict[str, Any] = {}
    for key in DTGC_CALC_FLOAT_KEYS:
        kwargs[key] = parse_float(dtgc[key], f"dtgc.{key}")
    for key in DTGC_CALC_INT_KEYS:
        kwargs[key] = parse_int(dtgc[key], f"dtgc.{key}")

    cfg = calc_dtgc_registers(**kwargs)
    registers = cfg["registers"]
    memory_words = cfg["memory_words"]
    if len(memory_words) > DTGC_MEMORY_WORDS:
        raise ValueError(f"dtgc.memory_words must contain at most {DTGC_MEMORY_WORDS} words")

    # Internal Non-Uniform Mode: program memory bank first, then profile timing.
    add_reg(commands, "DTGC", 0xB5, parse_int(registers["B5"], "dtgc.registers.B5"))
    for idx in range(DTGC_MEMORY_WORDS):
        word = memory_words[idx] if idx < len(memory_words) else 0
        add_reg(commands, "DTGC", 0x01 + idx, parse_int(word, f"dtgc.memory_words[{idx}]"))
    for reg_name in ("A1", "A2", "A3", "A4", "A5", "B6", "B7"):
        add_reg(commands, "DTGC", int(reg_name, 16), parse_int(registers[reg_name], f"dtgc.registers.{reg_name}"))


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


def build_tx7332_commands(config: dict[str, Any], chunk_words: int) -> list[str]:
    if not 1 <= chunk_words <= 8:
        raise ValueError("--chunk-words must be in the range 1..8")

    tx7332 = config.get("tx7332", {})
    commands: list[str] = []
    for section, expected_len in TX7332_SECTIONS.items():
        if section not in tx7332:
            continue
        values = tx7332[section]
        if not isinstance(values, list):
            raise ValueError(f"tx7332.{section} must be a list")
        if len(values) != expected_len:
            raise ValueError(f"tx7332.{section} must contain {expected_len} words, got {len(values)}")
        words = [parse_bram_word(value, f"tx7332.{section}[{idx}]") for idx, value in enumerate(values)]
        for offset in range(0, len(words), chunk_words):
            chunk = words[offset : offset + chunk_words]
            payload = " ".join(f"0x{word:08X}" for word in chunk)
            commands.append(f"TX7332 WR {section} {offset} {len(chunk)} {payload}")

    unknown = sorted(set(tx7332) - set(TX7332_SECTIONS))
    if unknown:
        raise ValueError(f"unknown TX7332 section(s): {', '.join(unknown)}")
    return commands


def append_bram_config_commands(commands: list[str], config_path: Path, chunk_words: int) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    commands.extend(build_bram_commands(config, chunk_words))


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


def read_beam_ack(port: Any, expected: str, timeout_s: float) -> int:
    """Wait for a BEAM ACK.

    BEAM commands do not carry a PC-generated sequence number. The firmware
    replies with its current uart_cfg_seq, so only the ACK payload is checked.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace").strip()
        if line:
            print(f"< {line}")

        nack = NACK_RE.match(line)
        if nack:
            raise RuntimeError(line)

        ack = ACK_RE.match(line)
        if ack and ack.group(2) == expected:
            return int(ack.group(1))

    raise TimeoutError(f"timed out waiting for @ACK <seq> {expected}")


def send_beam_command(port: Any, operation: str, timeout_s: float) -> int:
    operation = operation.upper()
    expected = f"BEAM_{operation}"
    line = f"BEAM {operation}"
    print(f"> {line}")
    port.write((line + "\n").encode("ascii"))
    port.flush()
    return read_beam_ack(port, expected, timeout_s)


def wait_for_beam_interval(seconds: float, message: str) -> None:
    if seconds <= 0:
        return
    print(f"# {message}: waiting {seconds:.3f} s")
    time.sleep(seconds)


def iter_beam_dry_run_lines(args: argparse.Namespace) -> list[str]:
    if args.beam == "start":
        return ["BEAM START"]
    if args.beam == "stop":
        return ["BEAM STOP"]
    if args.beam != "cycle":
        return []

    lines: list[str] = []
    for cycle in range(1, args.cycles + 1):
        lines.append(f"# cycle {cycle}/{args.cycles}")
        lines.append("BEAM START")
        lines.append(f"# wait run-seconds {args.run_seconds:.3f}")
        lines.append("BEAM STOP")
        lines.append(f"# wait freeze-seconds {args.freeze_seconds:.3f}")
    if args.final_state == "running":
        lines.append("# final-state running")
        lines.append("BEAM START")
    return lines


def run_beam_mode(port: Any, args: argparse.Namespace) -> None:
    if args.beam == "start":
        send_beam_command(port, "START", args.timeout)
        return
    if args.beam == "stop":
        send_beam_command(port, "STOP", args.timeout)
        return
    if args.beam != "cycle":
        return

    for cycle in range(1, args.cycles + 1):
        print(f"# cycle {cycle}/{args.cycles}: restart")
        send_beam_command(port, "START", args.timeout)
        wait_for_beam_interval(args.run_seconds, "beamforming enabled")

        print(f"# cycle {cycle}/{args.cycles}: freeze")
        send_beam_command(port, "STOP", args.timeout)
        wait_for_beam_interval(args.freeze_seconds, "frame-boundary freeze settling")

    if args.final_state == "running":
        print("# requesting final running state")
        send_beam_command(port, "START", args.timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM6", help="Serial port, default COM6")
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument(
        "--target",
        choices=(
            "afe5832",
            "tx7332",
            "delay_profile",
            "demod_coeffs",
            "log_table",
            "x_element",
            "hamming",
            "dfilter",
            "t_timing",
            "sin_beta",
            "all",
        ),
        default="afe5832",
        help="Configuration target, default afe5832",
    )
    parser.add_argument("--config", type=Path, help="Legacy AFE5832 JSON config path")
    parser.add_argument("--afe-config", type=Path, default=DEFAULT_AFE_CONFIG, help="AFE5832 JSON config path")
    parser.add_argument("--tx7332-config", type=Path, default=DEFAULT_TX7332_CONFIG, help="TX7332 JSON config path")
    parser.add_argument("--delay-profile-config", type=Path, default=DEFAULT_DELAY_PROFILE_CONFIG)
    parser.add_argument("--demod-coeffs-config", type=Path, default=DEFAULT_DEMOD_COEFFS_CONFIG)
    parser.add_argument("--log-table-config", type=Path, default=DEFAULT_LOG_TABLE_CONFIG)
    parser.add_argument("--x-element-config", type=Path, default=DEFAULT_X_ELEMENT_CONFIG)
    parser.add_argument("--hamming-config", type=Path, default=DEFAULT_HAMMING_CONFIG)
    parser.add_argument("--dfilter-config", type=Path, default=DEFAULT_DFILTER_CONFIG)
    parser.add_argument("--t-timing-config", type=Path, default=DEFAULT_T_TIMING_CONFIG)
    parser.add_argument("--sin-beta-config", type=Path, default=DEFAULT_SIN_BETA_CONFIG)
    parser.add_argument("--chunk-words", type=int, default=8, help="Words per BRAM WR command, max 8")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--beam",
        choices=("none", "start", "stop", "cycle"),
        default="none",
        help="Beamforming control mode. Non-none modes send BEAM commands only and skip JSON configuration.",
    )
    parser.add_argument("--cycles", type=int, default=1, help="BEAM cycle count, default 1")
    parser.add_argument("--run-seconds", type=float, default=2.0, help="Delay after BEAM START in cycle mode")
    parser.add_argument("--freeze-seconds", type=float, default=2.0, help="Delay after BEAM STOP in cycle mode")
    parser.add_argument(
        "--final-state",
        choices=("stopped", "running"),
        default="stopped",
        help="Requested final state after --beam cycle, default stopped",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without opening the serial port")
    args = parser.parse_args()

    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    if args.run_seconds < 0 or args.freeze_seconds < 0:
        parser.error("--run-seconds and --freeze-seconds must not be negative")

    if args.beam != "none":
        if args.dry_run:
            for line in iter_beam_dry_run_lines(args):
                print(line)
            return 0
        if serial is None:
            raise SystemExit("pyserial is required: pip install pyserial")
        with serial.Serial(args.port, args.baud, timeout=0.1) as port:
            time.sleep(0.2)
            port.reset_input_buffer()
            run_beam_mode(port, args)
        return 0

    seq = int(time.time() * 1000) & 0xFFFFFFFF
    afe_config_path = args.config or args.afe_config
    commands = [f"CFG BEGIN {seq}"]
    bram_config_paths = {
        "delay_profile": args.delay_profile_config,
        "demod_coeffs": args.demod_coeffs_config,
        "log_table": args.log_table_config,
        "x_element": args.x_element_config,
        "hamming": args.hamming_config,
        "dfilter": args.dfilter_config,
        "t_timing": args.t_timing_config,
        "sin_beta": args.sin_beta_config,
    }

    if args.target in BRAM_TARGET_ORDER:
        append_bram_config_commands(commands, bram_config_paths[args.target], args.chunk_words)
    if args.target == "all":
        for target in BRAM_TARGET_ORDER:
            append_bram_config_commands(commands, bram_config_paths[target], args.chunk_words)
    if args.target in ("tx7332", "all") and args.tx7332_config is not None:
        tx7332_config = json.loads(args.tx7332_config.read_text(encoding="utf-8"))
        commands += build_tx7332_commands(tx7332_config, args.chunk_words)
    if args.target in ("afe5832", "all") and afe_config_path is not None:
        afe_config = json.loads(afe_config_path.read_text(encoding="utf-8"))
        commands += build_afe_commands(afe_config)
    if args.target in ("afe5832", "all") and afe_config_path is None:
        raise SystemExit("provide --afe-config or --config for AFE configuration")
    if args.target in ("tx7332", "all") and args.tx7332_config is None:
        raise SystemExit("provide --tx7332-config for TX7332 configuration")
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
        cfg_started = False
        try:
            for line in commands:
                send_line(port, line, seq, args.timeout)
                if line.startswith("CFG BEGIN "):
                    cfg_started = True
                elif line == "CFG END":
                    cfg_started = False
        except Exception:
            if cfg_started:
                try:
                    send_line(port, "CFG ABORT", seq, args.timeout)
                except Exception as abort_error:
                    print(f"warning: CFG ABORT failed: {abort_error}", file=sys.stderr)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
