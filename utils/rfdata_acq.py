import argparse
import os

import usb.core
import usb.util
import numpy as np
import matplotlib.pyplot as plt


MAGIC = 0xA55A
WORDS_PER_CHANNEL = 4096
HEADER_WORDS = 3
RF_SAMPLES = WORDS_PER_CHANNEL - HEADER_WORDS
RTC_LINES = 128
CHANNELS_PER_RTC_LINE = 32
LOGICAL_LINES = 64
TOTAL_CHANNELS = 64
FRAME_WORDS = RTC_LINES * CHANNELS_PER_RTC_LINE * WORDS_PER_CHANNEL
FRAME_BYTES = FRAME_WORDS * 2
DEFAULT_DUMP_PATH = None


def byte_to_words(data):
    """Convert little-endian USB bytes to uint16 words."""
    if len(data) % 2 != 0:
        raise ValueError("Data length must be even")
    return np.frombuffer(data, dtype="<u2").copy()


def words_to_signed16(words):
    """Convert uint16 two's-complement words to signed int32 samples."""
    return np.asarray(words, dtype=np.uint16).view(np.int16).astype(np.int32)


def ensure_dir_for_file(file_path):
    dir_name = os.path.dirname(os.path.abspath(file_path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)


def save_hex_dump(file_path, words):
    ensure_dir_for_file(file_path)
    np.savetxt(file_path, np.asarray(words, dtype=np.uint16), fmt="%04X")


def find_circular_frame_start(words):
    """
    Find the real RF frame start in a circular DDR dump.

    Frame start is the first channel header:
        word0 = A55A
        word1 = rtc_line 0
        word2 = channel 0
    """
    words = np.asarray(words, dtype=np.uint16)
    n_words = len(words)

    candidates = np.flatnonzero(words == MAGIC)
    for idx in candidates:
        rtc_line = int(words[(idx + 1) % n_words])
        channel_id = int(words[(idx + 2) % n_words])
        if rtc_line == 0 and channel_id == 0:
            return int(idx)

    raise RuntimeError("Cannot find circular frame header A55A, 0000, 0000")


def read_circular_block(words, start, length):
    """Read length words from a circular word buffer."""
    indices = np.arange(start, start + length, dtype=np.int64)
    return np.take(words, indices, mode="wrap")


def parse_channel_block(words, frame_start, rtc_line, local_channel):
    """
    Parse one 4096-word channel block from the circular RF frame.

    Even rtc_line stores global channels 0..31.
    Odd rtc_line stores global channels 32..63.
    """
    block_index = rtc_line * CHANNELS_PER_RTC_LINE + local_channel
    block_start = frame_start + block_index * WORDS_PER_CHANNEL
    block = read_circular_block(words, block_start, WORDS_PER_CHANNEL)

    magic = int(block[0])
    got_rtc_line = int(block[1])
    got_channel = int(block[2])
    expected_channel = local_channel if (rtc_line % 2 == 0) else local_channel + 32

    if magic != MAGIC:
        raise RuntimeError(
            f"Bad magic at rtc_line={rtc_line}, local_ch={local_channel}: "
            f"got 0x{magic:04X}"
        )

    if got_rtc_line != rtc_line or got_channel != expected_channel:
        raise RuntimeError(
            f"Bad header at rtc_line={rtc_line}, local_ch={local_channel}: "
            f"got rtc_line={got_rtc_line}, channel={got_channel}, "
            f"expected rtc_line={rtc_line}, channel={expected_channel}"
        )

    samples = words_to_signed16(block[HEADER_WORDS:])
    return expected_channel, samples


def extract_focus_line_rf(words, focus_line):
    """
    Extract one logical focus line as a 64 x 4093 signed RF matrix.

    focus_line is 0..63.
    focus_line n maps to rtc_line 2*n and 2*n+1.
    The output row index is global channel id 0..63.
    """
    if not 0 <= focus_line < LOGICAL_LINES:
        raise ValueError(f"focus_line must be 0..63, got {focus_line}")

    words = np.asarray(words, dtype=np.uint16)
    if len(words) < FRAME_WORDS:
        raise ValueError(f"DDR dump is too short: {len(words)} words, expected {FRAME_WORDS}")

    words = words[:FRAME_WORDS]
    frame_start = find_circular_frame_start(words)

    rtc_lines = (focus_line * 2, focus_line * 2 + 1)
    rf_data = np.zeros((TOTAL_CHANNELS, RF_SAMPLES), dtype=np.int32)

    for rtc_line in rtc_lines:
        for local_channel in range(CHANNELS_PER_RTC_LINE):
            channel_id, samples = parse_channel_block(words, frame_start, rtc_line, local_channel)
            rf_data[channel_id, :] = samples

    meta = {
        "frame_start_word": frame_start,
        "frame_start_byte": frame_start * 2,
        "focus_line": focus_line,
        "rtc_lines": rtc_lines,
    }
    return rf_data, meta


def extend_rf_data_to_4096(rf_data):
    """Extend 64 x 4093 RF samples to 64 x 4096 by repeating each channel's last sample."""
    rf_data = np.asarray(rf_data)
    if rf_data.shape != (TOTAL_CHANNELS, RF_SAMPLES):
        raise ValueError(f"Expected rf_data shape {(TOTAL_CHANNELS, RF_SAMPLES)}, got {rf_data.shape}")

    pad_count = WORDS_PER_CHANNEL - RF_SAMPLES
    last_samples = rf_data[:, -1:]
    pad_samples = np.repeat(last_samples, pad_count, axis=1)
    return np.concatenate([rf_data, pad_samples], axis=1)


def save_all_focus_lines_merged_csvs(root_dir, words):
    """Save each logical focus line as one 4096 x 64 headerless CSV file."""
    out_root = os.path.abspath(root_dir)
    os.makedirs(out_root, exist_ok=True)

    saved_files = []
    for line_idx in range(LOGICAL_LINES):
        rf_data, _ = extract_focus_line_rf(words, line_idx)
        rf_data_4096 = extend_rf_data_to_4096(rf_data)
        file_path = os.path.join(out_root, f"rfdata_{line_idx + 1}.csv")
        np.savetxt(file_path, rf_data_4096.T, delimiter=",", fmt="%d")
        saved_files.append(file_path)

    return saved_files


def save_focus_line_csv(csv_path, rf_data):
    """
    Save RF data as one merged CSV.

    CSV layout:
        rows    = time samples 0..4092
        columns = ch0..ch63
    """
    ensure_dir_for_file(csv_path)
    header = ",".join(f"ch{ch}" for ch in range(TOTAL_CHANNELS))
    np.savetxt(csv_path, rf_data.T, delimiter=",", fmt="%d", header=header, comments="")


def acquire_ddr_words():
    dev = usb.core.find(idVendor=0x0424, idProduct=0x4940)
    if dev is None:
        raise RuntimeError("USB device 0424:4940 not found")

    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
    )
    print(ep)

    ready_data = [0xEF, 0x01, 0x10, 0x00]
    dev.write(0x1, ready_data, timeout=1000)

    data = dev.read(0x81, FRAME_BYTES, timeout=5000)
    words = byte_to_words(data)

    usb.util.dispose_resources(dev)
    return words


def usb_reader(data_queue=None, stop_flag=None, focus_line=0, csv_path=None, dump_path=None, all_lines_dir=None, plot=False):
    words = acquire_ddr_words()
    print(f"Received data length: {len(words)} words")

    if dump_path:
        save_hex_dump(dump_path, words)
        print(f"Saved DDR hex dump: {dump_path}")
    else:
        print("DDR hex dump path not specified; skip saving dump file")

    if all_lines_dir:
        saved_files = save_all_focus_lines_merged_csvs(all_lines_dir, words)
        print(
            f"Saved all logical focus lines to: {os.path.abspath(all_lines_dir)}\n"
            f"  files          = rfdata_1.csv..rfdata_64.csv\n"
            f"  csv shape      = {WORDS_PER_CHANNEL} samples x {TOTAL_CHANNELS} channels\n"
            f"  file count     = {len(saved_files)}"
        )
    else:
        print("All-lines output directory not specified; skip saving all focus lines")

    rf_data, meta = extract_focus_line_rf(words, focus_line)

    if csv_path:
        save_focus_line_csv(csv_path, rf_data)
        print(
            f"Saved focus line {focus_line} RF CSV: {csv_path}\n"
            f"  rtc_lines      = {meta['rtc_lines']}\n"
            f"  frame_start    = word {meta['frame_start_word']} "
            f"(byte 0x{meta['frame_start_byte']:08X})\n"
            f"  csv shape      = {rf_data.shape[1]} samples x {rf_data.shape[0]} channels"
        )
    else:
        print(
            f"Focus line {focus_line} RF extracted; merged CSV path not specified, skip saving merged CSV\n"
            f"  rtc_lines      = {meta['rtc_lines']}\n"
            f"  frame_start    = word {meta['frame_start_word']} "
            f"(byte 0x{meta['frame_start_byte']:08X})\n"
            f"  rf shape       = {rf_data.shape[0]} channels x {rf_data.shape[1]} samples"
        )

    if plot:
        for ch in range(TOTAL_CHANNELS):
            plt.plot(rf_data[ch])
        plt.ylim(-600, 600)
        plt.title(f"RF focus line {focus_line}")
        plt.xlabel("sample")
        plt.ylabel("ADC code")
        plt.show()

    return rf_data


def parse_args():
    parser = argparse.ArgumentParser(description="Acquire raw RF data from circular DDR dump.")
    parser.add_argument("--line", type=int, default=0, help="logical focus line index, 0..63")
    parser.add_argument("--csv", default=None, help="output merged CSV path; not saved if omitted")
    parser.add_argument("--dump", default=None, help="output raw DDR hex dump path; not saved if omitted")
    parser.add_argument("--all-lines", default=None, metavar="ROOT_DIR", help="output directory for all 64 logical focus-line CSV files; not saved if omitted")
    parser.add_argument("--plot", action="store_true", help="plot the extracted 64-channel RF data")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    usb_reader(
        focus_line=args.line,
        csv_path=args.csv,
        dump_path=args.dump,
        all_lines_dir=args.all_lines,
        plot=args.plot,
    )