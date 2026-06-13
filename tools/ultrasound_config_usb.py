#!/usr/bin/env python3
"""Send ultrasound runtime configuration to Zynq PS over USB bulk endpoints.

This tool intentionally reuses the command builders from
ultrasound_config_uart.py, but sends each ASCII command in a small framed USB
packet:

    PC -> PS: 0xEF 0xC0 LEN_L LEN_H ASCII_COMMAND
    PS -> PC: 0xEF 0xC1 LEN_L LEN_H ASCII_RESPONSE

The PS firmware still executes the same CFG/AFE/BRAM/TX7332/BEAM command set.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import ultrasound_config_uart as uart_cfg

try:
    import usb.core
    import usb.util
except ImportError:  # pragma: no cover - depends on PC environment
    usb = None


USB_FRAME_HEADER = 0xEF
USB_CFG_REQ_TYPE = 0xC0
USB_CFG_RESP_TYPE = 0xC1
DEFAULT_USB_VID = 0x0424
DEFAULT_USB_PID = 0x4940
DEFAULT_USB_EP_OUT = 0x01
DEFAULT_USB_EP_IN = 0x81
DEFAULT_MAX_RESPONSE_BYTES = 512


def parse_int_auto(text: str) -> int:
    return int(text, 0)


class UsbConfigTransport:
    def __init__(
        self,
        *,
        vid: int,
        pid: int,
        ep_out: int,
        ep_in: int,
        timeout_s: float,
        max_response_bytes: int,
    ) -> None:
        if usb is None:
            raise SystemExit("pyusb is required: pip install pyusb")
        self.timeout_ms = max(1, int(timeout_s * 1000))
        self.ep_out = ep_out
        self.ep_in = ep_in
        self.max_response_bytes = max_response_bytes
        self.dev = usb.core.find(idVendor=vid, idProduct=pid)
        if self.dev is None:
            raise RuntimeError(f"USB device not found: vid=0x{vid:04X}, pid=0x{pid:04X}")
        self.dev.set_configuration()

    def close(self) -> None:
        if usb is not None and self.dev is not None:
            usb.util.dispose_resources(self.dev)

    def write_command(self, line: str) -> None:
        payload = line.encode("ascii")
        if len(payload) == 0 or len(payload) > 0xFFFF:
            raise ValueError("USB command payload length is invalid")
        frame = bytes(
            (
                USB_FRAME_HEADER,
                USB_CFG_REQ_TYPE,
                len(payload) & 0xFF,
                (len(payload) >> 8) & 0xFF,
            )
        ) + payload
        self.dev.write(self.ep_out, frame, timeout=self.timeout_ms)

    def read_response(self, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        last_bad_frame = ""
        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                raw = bytes(
                    self.dev.read(
                        self.ep_in,
                        self.max_response_bytes,
                        timeout=min(self.timeout_ms, remaining_ms),
                    )
                )
            except usb.core.USBTimeoutError:
                continue
            if len(raw) < 4:
                last_bad_frame = f"short USB response ({len(raw)} bytes)"
                continue
            if raw[0] != USB_FRAME_HEADER or raw[1] != USB_CFG_RESP_TYPE:
                last_bad_frame = f"non-config USB response: {raw[:8].hex(' ')}"
                continue
            payload_len = raw[2] | (raw[3] << 8)
            if payload_len > len(raw) - 4:
                last_bad_frame = (
                    f"truncated USB response: need {payload_len}, got {len(raw) - 4}"
                )
                continue
            return raw[4 : 4 + payload_len].decode("ascii", errors="replace").strip()
        if last_bad_frame:
            raise TimeoutError(f"timed out waiting for PS USB acknowledgement ({last_bad_frame})")
        raise TimeoutError("timed out waiting for PS USB acknowledgement")


def append_bram_config_commands(commands: list[str], config_path: Path, chunk_words: int) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    commands.extend(uart_cfg.build_bram_commands(config, chunk_words))


def build_config_commands(args: argparse.Namespace) -> tuple[int, list[str]]:
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

    if args.target in uart_cfg.BRAM_TARGET_ORDER:
        append_bram_config_commands(commands, bram_config_paths[args.target], args.chunk_words)
    if args.target == "all":
        for target in uart_cfg.BRAM_TARGET_ORDER:
            append_bram_config_commands(commands, bram_config_paths[target], args.chunk_words)
    if args.target in ("tx7332", "all"):
        tx7332_config = json.loads(args.tx7332_config.read_text(encoding="utf-8"))
        commands.extend(uart_cfg.build_tx7332_commands(tx7332_config, args.chunk_words))
    if args.target in ("afe5832", "all"):
        afe_config = json.loads(afe_config_path.read_text(encoding="utf-8"))
        commands.extend(uart_cfg.build_afe_commands(afe_config))
    commands.append("CFG END")
    return seq, commands


def read_response(transport: UsbConfigTransport, seq: int, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = transport.read_response(deadline - time.monotonic())
        if line:
            print(f"< {line}")
        if line.startswith(f"@ACK {seq} ") or line.startswith(f"@RD {seq} "):
            return line
        if line.startswith(f"@NACK {seq} ") or line.startswith("@NACK 0 "):
            raise RuntimeError(line)
    raise TimeoutError("timed out waiting for PS acknowledgement")


def send_line(transport: UsbConfigTransport, line: str, seq: int, timeout_s: float) -> None:
    print(f"> {line}")
    transport.write_command(line)
    read_response(transport, seq, timeout_s)


def read_beam_ack(transport: UsbConfigTransport, expected: str, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = transport.read_response(deadline - time.monotonic())
        if line:
            print(f"< {line}")

        nack = uart_cfg.NACK_RE.match(line)
        if nack:
            raise RuntimeError(line)

        ack = uart_cfg.ACK_RE.match(line)
        if ack and ack.group(2) == expected:
            return int(ack.group(1))

    raise TimeoutError(f"timed out waiting for @ACK <seq> {expected}")


def send_beam_command(transport: UsbConfigTransport, operation: str, timeout_s: float) -> int:
    operation = operation.upper()
    expected = f"BEAM_{operation}"
    line = f"BEAM {operation}"
    print(f"> {line}")
    transport.write_command(line)
    return read_beam_ack(transport, expected, timeout_s)


def run_beam_mode(transport: UsbConfigTransport, args: argparse.Namespace) -> None:
    if args.beam == "start":
        send_beam_command(transport, "START", args.timeout)
        return
    if args.beam == "stop":
        send_beam_command(transport, "STOP", args.timeout)
        return
    if args.beam != "cycle":
        return

    for cycle in range(1, args.cycles + 1):
        print(f"# cycle {cycle}/{args.cycles}: restart")
        send_beam_command(transport, "START", args.timeout)
        uart_cfg.wait_for_beam_interval(args.run_seconds, "beamforming enabled")

        print(f"# cycle {cycle}/{args.cycles}: freeze")
        send_beam_command(transport, "STOP", args.timeout)
        uart_cfg.wait_for_beam_interval(args.freeze_seconds, "frame-boundary freeze settling")

    if args.final_state == "running":
        print("# requesting final running state")
        send_beam_command(transport, "START", args.timeout)


def add_common_config_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--afe-config", type=Path, default=uart_cfg.DEFAULT_AFE_CONFIG)
    parser.add_argument("--tx7332-config", type=Path, default=uart_cfg.DEFAULT_TX7332_CONFIG)
    parser.add_argument("--delay-profile-config", type=Path, default=uart_cfg.DEFAULT_DELAY_PROFILE_CONFIG)
    parser.add_argument("--demod-coeffs-config", type=Path, default=uart_cfg.DEFAULT_DEMOD_COEFFS_CONFIG)
    parser.add_argument("--log-table-config", type=Path, default=uart_cfg.DEFAULT_LOG_TABLE_CONFIG)
    parser.add_argument("--x-element-config", type=Path, default=uart_cfg.DEFAULT_X_ELEMENT_CONFIG)
    parser.add_argument("--hamming-config", type=Path, default=uart_cfg.DEFAULT_HAMMING_CONFIG)
    parser.add_argument("--dfilter-config", type=Path, default=uart_cfg.DEFAULT_DFILTER_CONFIG)
    parser.add_argument("--t-timing-config", type=Path, default=uart_cfg.DEFAULT_T_TIMING_CONFIG)
    parser.add_argument("--sin-beta-config", type=Path, default=uart_cfg.DEFAULT_SIN_BETA_CONFIG)
    parser.add_argument("--chunk-words", type=int, default=8, help="Words per BRAM/TX7332 WR command, max 8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vid", type=parse_int_auto, default=DEFAULT_USB_VID, help="USB VID, default 0x0424")
    parser.add_argument("--pid", type=parse_int_auto, default=DEFAULT_USB_PID, help="USB PID, default 0x4940")
    parser.add_argument("--ep-out", type=parse_int_auto, default=DEFAULT_USB_EP_OUT, help="Bulk OUT endpoint")
    parser.add_argument("--ep-in", type=parse_int_auto, default=DEFAULT_USB_EP_IN, help="Bulk IN endpoint")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES)
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
    parser.add_argument("--dry-run", action="store_true", help="Print commands without opening the USB device")
    add_common_config_args(parser)
    args = parser.parse_args()

    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    if args.max_response_bytes < 8:
        parser.error("--max-response-bytes must be at least 8")
    if args.run_seconds < 0 or args.freeze_seconds < 0:
        parser.error("--run-seconds and --freeze-seconds must not be negative")

    if args.beam != "none":
        if args.dry_run:
            for line in uart_cfg.iter_beam_dry_run_lines(args):
                print(line)
            return 0
        transport = UsbConfigTransport(
            vid=args.vid,
            pid=args.pid,
            ep_out=args.ep_out,
            ep_in=args.ep_in,
            timeout_s=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
        try:
            run_beam_mode(transport, args)
        finally:
            transport.close()
        return 0

    seq, commands = build_config_commands(args)
    if args.dry_run:
        for line in commands:
            print(line)
        return 0

    transport = UsbConfigTransport(
        vid=args.vid,
        pid=args.pid,
        ep_out=args.ep_out,
        ep_in=args.ep_in,
        timeout_s=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    cfg_started = False
    try:
        for line in commands:
            send_line(transport, line, seq, args.timeout)
            if line.startswith("CFG BEGIN "):
                cfg_started = True
            elif line == "CFG END":
                cfg_started = False
    except Exception:
        if cfg_started:
            try:
                send_line(transport, "CFG ABORT", seq, args.timeout)
            except Exception as abort_error:
                print(f"warning: CFG ABORT failed: {abort_error}", file=sys.stderr)
        raise
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
