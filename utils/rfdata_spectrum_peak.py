import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from rfdata_analyzer import (
    DEFAULT_DATA_DIR,
    DEFAULT_FS,
    NUM_CHANNELS,
    NUM_LINES,
    compute_spectrum,
    find_peak,
    load_rfdata,
)


DEFAULT_OUT_DIR = Path(r"D:\MyProjects\py_prj\ultrasound_dev_kit\log\rfdata_spectrum_peak")


def compute_peak_matrices(rf_data, fs=DEFAULT_FS, use_log=False, min_freq_mhz=0.1):
    """Compute spectrum peak frequency and magnitude for every line/channel."""
    if rf_data.shape[0] != NUM_LINES or rf_data.shape[2] != NUM_CHANNELS:
        raise ValueError(
            f"rf_data must have shape ({NUM_LINES}, samples, {NUM_CHANNELS}), "
            f"got {rf_data.shape}"
        )

    peak_freq_mhz = np.zeros((NUM_LINES, NUM_CHANNELS), dtype=np.float64)
    peak_mag_db = np.zeros((NUM_LINES, NUM_CHANNELS), dtype=np.float64)

    for line_idx in range(NUM_LINES):
        for ch_idx in range(NUM_CHANNELS):
            signal = rf_data[line_idx, :, ch_idx]
            freq_mhz, mag_db = compute_spectrum(signal, fs=fs, use_log=use_log)
            peak_freq, peak_mag = find_peak(freq_mhz, mag_db, min_freq_mhz=min_freq_mhz)
            peak_freq_mhz[line_idx, ch_idx] = peak_freq
            peak_mag_db[line_idx, ch_idx] = peak_mag

    return peak_freq_mhz, peak_mag_db


def save_peak_matrices(out_dir, peak_freq_mhz, peak_mag_db):
    """Save 64x64 peak matrices with line/ch labels."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    line_index = [f"line{i}" for i in range(NUM_LINES)]
    channel_columns = [f"ch{i}" for i in range(NUM_CHANNELS)]

    freq_df = pd.DataFrame(peak_freq_mhz, index=line_index, columns=channel_columns)
    mag_df = pd.DataFrame(peak_mag_db, index=line_index, columns=channel_columns)

    freq_path = out_dir / "spectrum_peak_freq_mhz.csv"
    mag_path = out_dir / "spectrum_peak_mag_db.csv"
    freq_df.to_csv(freq_path, index_label="line")
    mag_df.to_csv(mag_path, index_label="line")
    return freq_path, mag_path


def plot_peak_overview(peak_freq_mhz, peak_mag_db, save_fig=None, no_show=False):
    """Visualize 64x64 spectrum peak matrices as heatmaps and summary curves."""
    peak_freq_mhz = np.asarray(peak_freq_mhz, dtype=np.float64)
    peak_mag_db = np.asarray(peak_mag_db, dtype=np.float64)
    if peak_freq_mhz.shape != (NUM_LINES, NUM_CHANNELS):
        raise ValueError(f"peak_freq_mhz shape must be {(NUM_LINES, NUM_CHANNELS)}, got {peak_freq_mhz.shape}")
    if peak_mag_db.shape != (NUM_LINES, NUM_CHANNELS):
        raise ValueError(f"peak_mag_db shape must be {(NUM_LINES, NUM_CHANNELS)}, got {peak_mag_db.shape}")

    channel_idx = np.arange(NUM_CHANNELS)
    line_idx = np.arange(NUM_LINES)
    channel_mean_freq = np.mean(peak_freq_mhz, axis=0)
    line_mean_freq = np.mean(peak_freq_mhz, axis=1)

    fig, axes = plt.subplots(2, 2, figsize=(13, 10), constrained_layout=True)

    freq_img = axes[0, 0].imshow(peak_freq_mhz, aspect="auto", origin="lower", cmap="viridis")
    axes[0, 0].set_title("Spectrum peak frequency (MHz)")
    axes[0, 0].set_xlabel("Channel")
    axes[0, 0].set_ylabel("Focus line")
    fig.colorbar(freq_img, ax=axes[0, 0], label="MHz")

    mag_img = axes[0, 1].imshow(peak_mag_db, aspect="auto", origin="lower", cmap="magma")
    axes[0, 1].set_title("Spectrum peak magnitude (dB)")
    axes[0, 1].set_xlabel("Channel")
    axes[0, 1].set_ylabel("Focus line")
    fig.colorbar(mag_img, ax=axes[0, 1], label="dB")

    axes[1, 0].plot(channel_idx, channel_mean_freq, marker=".", linewidth=1.0)
    axes[1, 0].set_title("Mean peak frequency by channel")
    axes[1, 0].set_xlabel("Channel")
    axes[1, 0].set_ylabel("MHz")
    axes[1, 0].set_xlim(0, NUM_CHANNELS - 1)
    axes[1, 0].grid(True, alpha=0.3)

    axes[1, 1].plot(line_idx, line_mean_freq, marker=".", linewidth=1.0)
    axes[1, 1].set_title("Mean peak frequency by focus line")
    axes[1, 1].set_xlabel("Focus line")
    axes[1, 1].set_ylabel("MHz")
    axes[1, 1].set_xlim(0, NUM_LINES - 1)
    axes[1, 1].grid(True, alpha=0.3)

    if save_fig:
        save_path = Path(save_fig)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=160)
        print(f"Saved overview figure: {save_path}")

    if not no_show:
        plt.show()
    else:
        plt.close(fig)


def parse_args():
    parser = argparse.ArgumentParser(description="Compute 64x64 RF spectrum peak matrices.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="directory containing rfdata_1.csv..rfdata_64.csv")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory for spectrum peak CSV files")
    parser.add_argument("--fs", type=float, default=DEFAULT_FS, help="sampling rate in Hz, default 25e6")
    parser.add_argument("--log", action="store_true", help="apply log10(abs(x) + 1e-10) before FFT")
    parser.add_argument("--min-freq-mhz", type=float, default=0.1, help="minimum frequency considered for peak search")
    parser.add_argument("--save-fig", default=None, help="optional overview figure path; defaults to <out-dir>/spectrum_peak_overview.png")
    parser.add_argument("--no-show", action="store_true", help="do not show plot window")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.fs <= 0:
        raise ValueError(f"--fs must be positive, got {args.fs}")
    if args.min_freq_mhz < 0:
        raise ValueError(f"--min-freq-mhz must be non-negative, got {args.min_freq_mhz}")

    rf_data = load_rfdata(args.data_dir)
    print(f"Loaded RF cube: {rf_data.shape} from {Path(args.data_dir)}")
    print(f"Sampling rate : {args.fs / 1e6:.3f} MHz")
    print(f"FFT input     : {'log RF' if args.log else 'raw RF'}")
    print(f"Peak min freq : {args.min_freq_mhz:.6f} MHz")

    peak_freq_mhz, peak_mag_db = compute_peak_matrices(
        rf_data=rf_data,
        fs=args.fs,
        use_log=args.log,
        min_freq_mhz=args.min_freq_mhz,
    )
    freq_path, mag_path = save_peak_matrices(args.out_dir, peak_freq_mhz, peak_mag_db)

    print(f"Saved peak frequency CSV: {freq_path}")
    print(f"Saved peak magnitude CSV: {mag_path}")
    print(
        "Peak frequency range: "
        f"{np.min(peak_freq_mhz):.6f}..{np.max(peak_freq_mhz):.6f} MHz"
    )
    print(
        "Peak magnitude range: "
        f"{np.min(peak_mag_db):.2f}..{np.max(peak_mag_db):.2f} dB"
    )

    save_fig = Path(args.save_fig) if args.save_fig else Path(args.out_dir) / "spectrum_peak_overview.png"
    plot_peak_overview(peak_freq_mhz, peak_mag_db, save_fig=save_fig, no_show=args.no_show)


if __name__ == "__main__":
    main()