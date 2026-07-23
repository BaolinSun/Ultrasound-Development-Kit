import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


DEFAULT_DATA_DIR = Path(r"D:\MyProjects\py_prj\ultrasound_dev_kit\log\rfdata")
DEFAULT_FS = 25e6
NUM_LINES = 64
NUM_CHANNELS = 64
NUM_SAMPLES = 4096


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
        if data.shape != (NUM_SAMPLES, NUM_CHANNELS):
            raise ValueError(
                f"Bad shape for {csv_path}: got {data.shape}, "
                f"expected {(NUM_SAMPLES, NUM_CHANNELS)}"
            )
        lines.append(data)

    return np.stack(lines, axis=0)


def main():
    rf_data = load_rfdata(DEFAULT_DATA_DIR)

    fig, axs = plt.subplots(3, 1, figsize=(10, 8))
    axs[0].plot(rf_data[0, :, 0])
    axs[1].plot(rf_data[32, :, 0])
    axs[2].plot(rf_data[63, :, 0])

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()