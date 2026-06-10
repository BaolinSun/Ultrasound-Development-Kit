#!/usr/bin/env python3
"""Generate tools/configs/bram_default.json from the Vitis BRAM C arrays."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAM_SRC = ROOT / "vitis" / "B_mode_focus_imaging" / "src" / "bram"
OUT_PATH = ROOT / "tools" / "configs" / "bram_default.json"

ARRAY_SOURCES = {
    "fdemod_sin": (BRAM_SRC / "demod_coeffs.c", "fdemod_sin_array", 8192),
    "fdemod_cos": (BRAM_SRC / "demod_coeffs.c", "fdemod_cos_array", 8192),
    "log_table": (BRAM_SRC / "log_table.c", "log_table_hex", 1024),
    "x_element": (BRAM_SRC / "bram_config.c", "x_element_hex", 64),
    "hamming": (BRAM_SRC / "bram_config.c", "hamming_hex", 64),
    "dfilter": (BRAM_SRC / "bram_config.c", "dfilter_hex", 64),
    "t_timing": (BRAM_SRC / "bram_config.c", "T_timing_hex", 10),
    "sin_beta": (BRAM_SRC / "bram_config.c", "sin_beta_hex", 64),
    "delay_profile": (BRAM_SRC / "delay_profile.c", "delay_profile", 16384),
    "tx7332_config_data": (BRAM_SRC / "bram_config.c", "tx7332_config_data", 128),
}


def strip_line_comments(text: str) -> str:
    return re.sub(r"//.*", "", text)


def parse_array(path: Path, array_name: str) -> list[str]:
    text = strip_line_comments(path.read_text(encoding="utf-8", errors="ignore"))
    pattern = rf"u32\s+{re.escape(array_name)}\s*(?:\[[^\]]*\])?\s*=\s*\{{(?P<body>.*?)\}};"
    match = re.search(pattern, text, flags=re.S)
    if match is None:
        raise ValueError(f"array {array_name} not found in {path}")
    values = []
    for token in re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", match.group("body")):
        values.append(f"0x{int(token, 0) & 0xFFFFFFFF:08X}")
    return values


def main() -> int:
    bram: dict[str, list[str]] = {}
    for section, (path, array_name, expected_len) in ARRAY_SOURCES.items():
        values = parse_array(path, array_name)
        if len(values) != expected_len:
            raise ValueError(f"{section} expected {expected_len} words, got {len(values)}")
        bram[section] = values

    payload = {
        "description": (
            "Default BRAM imaging parameters generated from Vitis "
            "bram_config.c, delay_profile.c, demod_coeffs.c, and log_table.c."
        ),
        "bram": bram,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for section, values in bram.items():
        print(f"{section}: {len(values)} words")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
