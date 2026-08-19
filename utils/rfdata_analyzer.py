import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


DEFAULT_DATA_DIR = Path(r"D:\MyProjects\py_prj\ultrasound_dev_kit\log\rfdata")
DEFAULT_FS = 25e6
NUM_LINES = 64
NUM_CHANNELS = 64
NUM_SAMPLES = 2048


def load_rfdata(data_dir):
    """Load rfdata_1.csv..rfdata_64.csv as a (line, sample, channel) cube."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        raise FileNotFoundError(f"RF data directory not found: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"RF data path is not a directory: {data_dir}")

    lines = []
    for line_idx in range(NUM_LINES):
        csv_path = data_dir / f"rfdata_{line_idx + 1}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Missing RF CSV for line {line_idx}: {csv_path}")

        data = pd.read_csv(csv_path, header=None).to_numpy(dtype=np.int32)
        # data[1::2, :] *= -1
        if data.shape != (NUM_SAMPLES, NUM_CHANNELS):
            raise ValueError(
                f"Bad shape for {csv_path}: got {data.shape}, "
                f"expected {(NUM_SAMPLES, NUM_CHANNELS)}"
            )
        lines.append(data)

    return np.stack(lines, axis=0)


def log_rf_data(data):
    """Convert signed RF data to log-amplitude domain before spectrum analysis."""
    return np.log10(np.abs(np.asarray(data, dtype=np.float64)) + 1e-10)


def compute_spectrum(signal, fs=DEFAULT_FS, use_log=False):
    """Compute a raw unwindowed relative spectrum, optionally after log10(abs(x) + 1e-10)."""
    signal = np.asarray(signal, dtype=np.float64).reshape(-1)
    if signal.size == 0:
        raise ValueError("signal must not be empty")

    spectrum_input = log_rf_data(signal) if use_log else signal
    spectrum = np.fft.rfft(spectrum_input)
    mag = np.abs(spectrum)

    eps = np.finfo(np.float64).eps
    ref = max(float(np.max(mag)), eps)
    mag_db = 20.0 * np.log10(np.maximum(mag, eps) / ref)
    freq_mhz = np.fft.rfftfreq(signal.size, d=1.0 / fs) / 1e6
    return freq_mhz, mag_db


def compute_average_spectrum(rf_data, line_idx, fs=DEFAULT_FS, use_log=False):
    """Average magnitude spectra over all 64 channels of one focus line."""
    rf_data = np.asarray(rf_data)
    if rf_data.shape != (NUM_LINES, NUM_SAMPLES, NUM_CHANNELS):
        raise ValueError(
            f"rf_data shape must be {(NUM_LINES, NUM_SAMPLES, NUM_CHANNELS)}, "
            f"got {rf_data.shape}"
        )
    if not 0 <= line_idx < NUM_LINES:
        raise ValueError(f"line_idx must be 0..{NUM_LINES - 1}, got {line_idx}")

    line_data = rf_data[line_idx]
    data = log_rf_data(line_data) if use_log else line_data.astype(np.float64)
    mag = np.abs(np.fft.rfft(data, axis=0))
    avg_mag = np.mean(mag, axis=1)

    eps = np.finfo(np.float64).eps
    ref = max(float(np.max(avg_mag)), eps)
    avg_mag_db = 20.0 * np.log10(np.maximum(avg_mag, eps) / ref)
    freq_mhz = np.fft.rfftfreq(NUM_SAMPLES, d=1.0 / fs) / 1e6
    return freq_mhz, avg_mag_db


def find_peak(freq_mhz, mag_db, min_freq_mhz=0.1):
    """Find the peak frequency, ignoring near-DC bins by default."""
    freq_mhz = np.asarray(freq_mhz)
    mag_db = np.asarray(mag_db)
    valid = freq_mhz >= min_freq_mhz
    if not np.any(valid):
        idx = int(np.argmax(mag_db))
    else:
        local_idx = int(np.argmax(mag_db[valid]))
        idx = int(np.flatnonzero(valid)[local_idx])
    return float(freq_mhz[idx]), float(mag_db[idx])


def plot_rf_analysis(rf_data, line_idx, channel_idx, fs, use_log=False, save_fig=None, no_show=False):
    """Plot selected RF waveform, selected spectrum, and selected-line average spectrum."""
    signal = rf_data[line_idx, :, channel_idx]
    time_us = np.arange(NUM_SAMPLES) / fs * 1e6

    freq_mhz, mag_db = compute_spectrum(signal, fs, use_log=use_log)
    avg_freq_mhz, avg_mag_db = compute_average_spectrum(rf_data, line_idx, fs, use_log=use_log)

    peak_freq, peak_mag = find_peak(freq_mhz, mag_db)
    avg_peak_freq, avg_peak_mag = find_peak(avg_freq_mhz, avg_mag_db)

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), constrained_layout=True)

    axes[0].plot(time_us, signal, linewidth=0.9)
    axes[0].set_title(f"RF waveform: line {line_idx}, channel {channel_idx}")
    axes[0].set_xlabel("Time (us)")
    axes[0].set_ylabel("ADC code")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(freq_mhz, mag_db, linewidth=0.9)
    spectrum_mode = "log RF" if use_log else "raw RF"
    axes[1].set_title(f"Spectrum ({spectrum_mode}): peak {peak_freq:.3f} MHz ({peak_mag:.1f} dB)")
    axes[1].set_xlabel("Frequency (MHz)")
    axes[1].set_ylabel("Magnitude (dB, relative)")
    axes[1].set_xlim(0.0, fs / 2.0 / 1e6)
    axes[1].set_ylim(-100.0, 5.0)
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(avg_freq_mhz, avg_mag_db, linewidth=0.9)
    axes[2].set_title(f"Line {line_idx} channel-average spectrum ({spectrum_mode}): peak {avg_peak_freq:.3f} MHz ({avg_peak_mag:.1f} dB)")
    axes[2].set_xlabel("Frequency (MHz)")
    axes[2].set_ylabel("Magnitude (dB, relative)")
    axes[2].set_xlim(0.0, fs / 2.0 / 1e6)
    axes[2].set_ylim(-100.0, 5.0)
    axes[2].grid(True, alpha=0.3)

    if save_fig:
        save_path = Path(save_fig)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=160)
        print(f"Saved figure: {save_path}")

    print(f"Spectrum input        : {spectrum_mode}")
    print(f"Selected spectrum peak: {peak_freq:.6f} MHz, {peak_mag:.2f} dB")
    print(f"Line average spectrum : {avg_peak_freq:.6f} MHz, {avg_peak_mag:.2f} dB")

    if not no_show:
        plt.show()
    else:
        plt.close(fig)


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze raw RF CSV data spectrum.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="directory containing rfdata_1.csv..rfdata_64.csv")
    parser.add_argument("--line", type=int, default=0, help="logical focus line index, 0..63")
    parser.add_argument("--channel", type=int, default=0, help="channel index, 0..63")
    parser.add_argument("--fs", type=float, default=DEFAULT_FS, help="sampling rate in Hz, default 25e6")
    parser.add_argument("--save-fig", default=None, help="optional output figure path")
    parser.add_argument("--log", action="store_true", help="apply log10(abs(x) + 1e-10) before FFT")
    parser.add_argument("--no-show", action="store_true", help="do not show plot window")
    return parser.parse_args()


def main():
    args = parse_args()

    if not 0 <= args.line < NUM_LINES:
        raise ValueError(f"--line must be 0..{NUM_LINES - 1}, got {args.line}")
    if not 0 <= args.channel < NUM_CHANNELS:
        raise ValueError(f"--channel must be 0..{NUM_CHANNELS - 1}, got {args.channel}")
    if args.fs <= 0:
        raise ValueError(f"--fs must be positive, got {args.fs}")

    rf_data = load_rfdata(args.data_dir)
    print(f"Loaded RF cube: {rf_data.shape} from {Path(args.data_dir)}")
    print(f"Sampling rate : {args.fs / 1e6:.3f} MHz")
    print(f"FFT input     : {'log RF' if args.log else 'raw RF'}")

    plot_rf_analysis(
        rf_data=rf_data,
        line_idx=args.line,
        channel_idx=args.channel,
        fs=args.fs,
        use_log=args.log,
        save_fig=args.save_fig,
        no_show=args.no_show,
    )


if __name__ == "__main__":
    main()