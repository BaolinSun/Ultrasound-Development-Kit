import os
import cv2
import sys
import time
import h5py
import usb.util
import usb.core
import numpy as np
import subprocess as sp
import multiprocessing as mp
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path


DEFAULT_SOUND_SPEED_M_S = 1540.0
DEFAULT_PROBE_FREQUENCY_HZ = 4.0e6
DEFAULT_SAMPLING_RATE_HZ = 25e6
DEFAULT_RF_DEPTH = 6144
DEFAULT_CHANNEL_COUNT = 64
DEFAULT_ELEMENT_COUNT = 64
DEFAULT_DYNAMIC_RANGE_DB = 45
DEFAULT_BRIGHTNESS_DB = 3.0
DEFAULT_CONTRAST_GAIN = 1.0
DEFAULT_NOISE_FLOOR_DB = -45.0
DEFAULT_LOG10_FRAC_BITS = 14
DEFAULT_TGC_MIN_DB = -24.0
DEFAULT_TGC_MAX_DB = 24.0
DEFAULT_TGC_MIN_GAIN = 10 ** (DEFAULT_TGC_MIN_DB / 20.0)
DEFAULT_TGC_MAX_GAIN = 10 ** (DEFAULT_TGC_MAX_DB / 20.0)
DDR_RING_BYTES = 1024 * 1000
LINE_HEADER_WORDS = 8
WORDS_PER_LINE = DEFAULT_RF_DEPTH + LINE_HEADER_WORDS
USB_EP_OUT = 0x01
USB_EP_IN = 0x81
LINE_HEADER1 = 0x1C00
LINE_HEADER2 = 0x1800

def get_default_imaging_params():
    """Return imaging metadata used by the PySide6 GUI display panel."""
    imaging_depth_m = 0.5 * DEFAULT_SOUND_SPEED_M_S / DEFAULT_SAMPLING_RATE_HZ * (
        DEFAULT_RF_DEPTH - 1
    )
    return {
        "imaging_depth_mm": imaging_depth_m * 1000.0,
        "probe_frequency_mhz": DEFAULT_PROBE_FREQUENCY_HZ / 1e6,
        "element_count": DEFAULT_ELEMENT_COUNT,
        "channel_count": DEFAULT_CHANNEL_COUNT,
        "sampling_rate_mhz": DEFAULT_SAMPLING_RATE_HZ / 1e6,
        "mode": "B-mode",
        "dynamic_range_db": DEFAULT_DYNAMIC_RANGE_DB,
        "brightness_db": DEFAULT_BRIGHTNESS_DB,
        "contrast_gain": DEFAULT_CONTRAST_GAIN,
        "noise_floor_db": DEFAULT_NOISE_FLOOR_DB,
        "gain_db": 0,
        "tgc": "Flat",
    }


def get_default_bmode_params():
    """Return MATLAB graymap GUI compatible B-mode display parameters."""
    return {
        "dynamic_range_db": DEFAULT_DYNAMIC_RANGE_DB,
        "brightness_db": DEFAULT_BRIGHTNESS_DB,
        "contrast_gain": DEFAULT_CONTRAST_GAIN,
        "noise_floor_db": DEFAULT_NOISE_FLOOR_DB,
    }


def default_tgc_control_points():
    """Default TGC dB control points over the 6144-sample depth axis."""
    return np.array(
        [
            [0, 0.0],
            [512, 0.0],
            [1024, 0.0],
            [2048, 0.0],
            [3072, 0.0],
            [4096, 0.0],
            [4608, 0.0],
            [5120, 0.0],
            [5632, 0.0],
            [DEFAULT_RF_DEPTH - 1, 0.0],
        ],
        dtype=np.float32,
    )


def tgc_db_to_gain(tgc_db):
    """Convert dB TGC values to linear gain."""
    return np.power(10.0, np.asarray(tgc_db, dtype=np.float32) / 20.0).astype(np.float32)


def tgc_gain_to_db(tgc_gain):
    """Convert linear TGC gain values to dB."""
    safe_gain = np.clip(np.asarray(tgc_gain, dtype=np.float32), 1e-6, None)
    return (20.0 * np.log10(safe_gain)).astype(np.float32)


def make_tgc_db_curve(ctrl_points=None, num_samples=DEFAULT_RF_DEPTH, min_db=DEFAULT_TGC_MIN_DB, max_db=DEFAULT_TGC_MAX_DB):
    """Interpolate dB control points into a full-depth TGC dB curve."""
    points = default_tgc_control_points() if ctrl_points is None else np.asarray(ctrl_points, dtype=np.float32)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("TGC control points must be an Nx2 array")
    xs = np.clip(points[:, 0], 0, num_samples - 1)
    ys = np.clip(points[:, 1], float(min_db), float(max_db))
    order = np.argsort(xs)
    xs = xs[order]
    ys = ys[order]
    xs[0] = 0.0
    xs[-1] = float(num_samples - 1)
    xq = np.arange(num_samples, dtype=np.float32)
    return np.interp(xq, xs, ys).astype(np.float32)


def make_tgc_gain(ctrl_points=None, num_samples=DEFAULT_RF_DEPTH, min_db=DEFAULT_TGC_MIN_DB, max_db=DEFAULT_TGC_MAX_DB):
    """Interpolate dB control points and convert them into linear TGC gain."""
    return tgc_db_to_gain(make_tgc_db_curve(ctrl_points, num_samples, min_db, max_db))


def q214_to_log10_float(rfdata_q214):
    """Convert FPGA Q2.14 log10 samples into normal float32 log10 values."""
    return rfdata_q214.astype(np.float32, copy=False) / float(2 ** DEFAULT_LOG10_FRAC_BITS)


def apply_tgc_gain_log10_q214(rfdata_q214, tgc_gain, enable_tgc=True):
    """Convert Q2.14 log10 data to float log10 and apply depth-wise TGC.

    The requested linear-domain operation is:
        log10((10 ** log10_data) * tgc_gain)
    which is equivalent to:
        log10_data + log10(tgc_gain)
    This keeps the per-frame work vectorized and avoids a costly exp/log round trip.
    """
    log10_data = q214_to_log10_float(rfdata_q214)
    if not enable_tgc:
        return log10_data

    gain = np.asarray(tgc_gain, dtype=np.float32).reshape(-1)
    if gain.size != log10_data.shape[1]:
        raise ValueError(f"TGC gain length must be {log10_data.shape[1]}, got {gain.size}")
    safe_gain = np.clip(gain, DEFAULT_TGC_MIN_GAIN, DEFAULT_TGC_MAX_GAIN)
    return log10_data + np.log10(safe_gain)[np.newaxis, :]


def load_graymap_lut(path=None):
    """Load a 256-entry graymap LUT from MATLAB text format, or return linear."""
    if path is None:
        path = Path(__file__).resolve().parent / "DR" / "graymap_lut.txt"
    path = Path(path)
    if not path.exists():
        return np.arange(256, dtype=np.uint8)

    text = path.read_text(encoding="utf-8", errors="ignore")
    for old, new in (("[", " "), ("]", " "), (",", " "), (";", " "), ("=", " ")):
        text = text.replace(old, new)
    values = []
    for token in text.split():
        if token.startswith("#") or token.lower() == "lut":
            continue
        try:
            values.append(int(float(token)))
        except ValueError:
            continue
    if len(values) != 256:
        return np.arange(256, dtype=np.uint8)
    return np.clip(values, 0, 255).astype(np.uint8)

def display_log_bmode_auto(bimg, low_percent=1, high_percent=99.5):
    bimg = bimg.astype(np.float32)

    lower = np.percentile(bimg, low_percent)
    upper = np.percentile(bimg, high_percent)

    img = (bimg - lower) / (upper - lower + 1e-6)
    img = np.clip(img, 0, 1)

    img8 = (img * 255).astype(np.uint8)
    return img8

import numpy as np
import cv2



def byte_to_double(data):
    if len(data)%2 !=0:
        raise ValueError("Data length must be even")
    return np.frombuffer(data, dtype='<u2')


def make_pixel_grid(xlims, zlims, dx, dz):
    x = np.arange(xlims[0], xlims[1] + dx, dx)
    z = np.arange(zlims[0], zlims[1] + dz, dz)
    xx, zz = np.meshgrid(x, z)
    yy = np.zeros_like(xx)
    grid = np.stack((xx, yy, zz), axis=2)
    return grid


def reconstruct_frame(rfdata, r_index, theta_index, valid, img_shape):
    bimgsc = np.zeros_like(r_index, dtype=float)
    bimgsc[valid] = rfdata[theta_index[valid], r_index[valid]]
    return bimgsc.reshape(img_shape)

def nothing(x):
    pass


def full_imaging_depth_mm():
    """Return the full physical receive depth represented by the current RF frame."""
    range_step_m = DEFAULT_SOUND_SPEED_M_S / (2.0 * DEFAULT_SAMPLING_RATE_HZ)
    return range_step_m * (DEFAULT_RF_DEPTH - 1) * 1000.0


def clamp_display_depth_mm(display_depth_mm):
    """Clamp GUI display depth without changing acquisition frame length."""
    full_depth = full_imaging_depth_mm()
    if display_depth_mm is None:
        return full_depth
    return float(np.clip(float(display_depth_mm), 20.0, full_depth))


def precompute_scan_converter(num_lines, num_range, display_depth_mm=None):
    """Precompute MATLAB-style polar-to-Cartesian bilinear indices and weights.

    display_depth_mm changes only the Cartesian field of view. The input polar
    frame still keeps the full num_range samples, so no hardware or acquisition
    depth changes are required.
    """
    c = DEFAULT_SOUND_SPEED_M_S
    fc = DEFAULT_PROBE_FREQUENCY_HZ
    fs = DEFAULT_SAMPLING_RATE_HZ
    cart_step_m = (c / fc) / 2.0
    range_step_m = c / (2.0 * fs)

    angles_rad = np.deg2rad(np.linspace(-45.0, 45.0, num_lines)).astype(np.float32)
    full_r_max = float((num_range - 1) * range_step_m)
    requested_depth_m = clamp_display_depth_mm(display_depth_mm) / 1000.0
    r_max = min(full_r_max, requested_depth_m)
    x_max = r_max * np.sin(np.deg2rad(45.0))
    x_axis = np.arange(-x_max, x_max + cart_step_m * 0.5, cart_step_m, dtype=np.float32)
    z_axis = np.arange(0.0, r_max + cart_step_m * 0.5, cart_step_m, dtype=np.float32)
    xx, zz = np.meshgrid(x_axis, z_axis)

    theta_q = np.arctan2(xx, zz)
    r_q = np.hypot(xx, zz)
    theta_pos = (theta_q - angles_rad[0]) / (angles_rad[1] - angles_rad[0])
    range_pos = r_q / range_step_m

    theta0 = np.floor(theta_pos).astype(np.int32)
    range0 = np.floor(range_pos).astype(np.int32)
    theta1 = theta0 + 1
    range1 = range0 + 1

    valid = (
        (theta0 >= 0)
        & (theta1 < num_lines)
        & (range0 >= 0)
        & (range1 < num_range)
        & (theta_q >= angles_rad[0])
        & (theta_q <= angles_rad[-1])
        & (r_q <= r_max)
    )

    wt = (theta_pos - theta0).astype(np.float32)
    wr = (range_pos - range0).astype(np.float32)
    wt = np.clip(wt, 0.0, 1.0)
    wr = np.clip(wr, 0.0, 1.0)

    return {
        "shape": xx.shape,
        "valid_flat": valid.ravel(),
        "theta0": theta0.ravel(),
        "theta1": theta1.ravel(),
        "range0": range0.ravel(),
        "range1": range1.ravel(),
        "w00": ((1.0 - wt) * (1.0 - wr)).ravel(),
        "w01": ((1.0 - wt) * wr).ravel(),
        "w10": (wt * (1.0 - wr)).ravel(),
        "w11": (wt * wr).ravel(),
    }


def scan_convert_bilinear(polar_data, scan):
    """Apply precomputed bilinear scan conversion to one polar frame."""
    out = np.full(scan["valid_flat"].shape, np.nan, dtype=np.float32)
    valid = scan["valid_flat"]
    t0 = scan["theta0"][valid]
    t1 = scan["theta1"][valid]
    r0 = scan["range0"][valid]
    r1 = scan["range1"][valid]

    out[valid] = (
        polar_data[t0, r0] * scan["w00"][valid]
        + polar_data[t0, r1] * scan["w01"][valid]
        + polar_data[t1, r0] * scan["w10"][valid]
        + polar_data[t1, r1] * scan["w11"][valid]
    )
    return out.reshape(scan["shape"])


def apply_bmode_mapping(cart_log10_data, params, graymap_lut):
    """MATLAB ultrasound_bmode_gui_graymap.m display mapping for float log10 data."""
    dynamic_range_db, brightness_db, contrast_gain, noise_floor_db = params
    dynamic_range_db = max(1.0, float(dynamic_range_db))
    brightness_db = float(brightness_db)
    contrast_gain = float(contrast_gain)
    noise_floor_db = float(noise_floor_db)

    cart_log10 = cart_log10_data.astype(np.float32, copy=False)
    finite = np.isfinite(cart_log10)
    if np.any(finite):
        cart_log10_max = np.max(cart_log10[finite])
        # cart_log10_max = np.percentile(cart_log10[finite], 99.9)
    else:
        cart_log10_max = 0.0


    db = 20.0 * (cart_log10 - cart_log10_max)
    db = (db + brightness_db) * contrast_gain
    db = np.where(db < noise_floor_db, noise_floor_db, db)
    db = np.clip(db, -dynamic_range_db, 0.0)
    db[~finite] = -dynamic_range_db

    img = np.round((db + dynamic_range_db) / dynamic_range_db * 255.0)
    img = np.clip(img, 0, 255).astype(np.uint8)
    return graymap_lut[img]


def read_shared_bmode_params(bmode_params):
    if hasattr(bmode_params, "get_lock"):
        with bmode_params.get_lock():
            return np.array(bmode_params[:], dtype=np.float32)
    return np.array(bmode_params[:], dtype=np.float32)


def read_shared_lut(graymap_lut):
    if hasattr(graymap_lut, "get_obj"):
        with graymap_lut.get_lock():
            return np.frombuffer(graymap_lut.get_obj(), dtype=np.uint8).copy()
    return np.asarray(graymap_lut, dtype=np.uint8).copy()


def read_shared_tgc_gain(tgc_gain):
    if tgc_gain is None:
        return make_tgc_gain()
    if hasattr(tgc_gain, "get_obj"):
        with tgc_gain.get_lock():
            return np.frombuffer(tgc_gain.get_obj(), dtype=np.float32).copy()
    return np.asarray(tgc_gain, dtype=np.float32).copy()


def read_shared_tgc_enabled(enable_tgc):
    if enable_tgc is None:
        return False
    if hasattr(enable_tgc, "value"):
        return bool(enable_tgc.value)
    return bool(enable_tgc)


def read_shared_display_depth_mm(display_depth_mm):
    if display_depth_mm is None:
        return full_imaging_depth_mm()
    if hasattr(display_depth_mm, "value"):
        return clamp_display_depth_mm(display_depth_mm.value)
    return clamp_display_depth_mm(display_depth_mm)


def usb_reader(data_queue, stop_flag, pause_flag=None):
    """
    USB数据采集进程
    """
    dev = usb.core.find(idVendor=0x0424, idProduct=0x4940)
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0,0)]
    ep = usb.util.find_descriptor(
        intf, 
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    print(ep)

    ready_data = [0xef, 0x01, 0x10, 0x00]
    rfdata = np.zeros((DEFAULT_CHANNEL_COUNT, DEFAULT_RF_DEPTH), dtype=np.int32)

    cnt = 0

    while not stop_flag.value:
        if pause_flag is not None and pause_flag.value:
            time.sleep(0.03)
            continue

        ready_data[1] = 0x01

        cnt += 1
        dev.write(USB_EP_OUT, ready_data, timeout=1000)

        data = bytes(dev.read(USB_EP_IN, DDR_RING_BYTES, timeout=5000))
        if len(data) != DDR_RING_BYTES:
            raise RuntimeError(f"USB short read: expected {DDR_RING_BYTES} bytes, got {len(data)} bytes")

        recv_data = byte_to_double(data)
        line_set = set()

        indices = np.where(
            (recv_data[:-1] == LINE_HEADER1) &
            (recv_data[1:] == LINE_HEADER2)
        )[0]

        if len(indices) >= 2:
            line_gap = np.diff(indices)
            valid_indices = indices[:-1][line_gap == WORDS_PER_LINE]
        else:
            valid_indices = np.array([], dtype=np.int64)

        for base in valid_indices:
            base = int(base)
            data_start = base + LINE_HEADER_WORDS
            data_end = data_start + DEFAULT_RF_DEPTH
            if data_end > len(recv_data):
                continue

            line_num = int(recv_data[base + 2])
            if not (0 <= line_num < DEFAULT_CHANNEL_COUNT):
                continue

            rfdata[line_num, :] = recv_data[data_start:data_end].astype(np.int32)
            line_set.add(line_num)

        if line_set:
            data_queue.put(rfdata)

    usb.util.dispose_resources(dev)
    print("[USB] Stop.")


def data_worker(
    data_queue,
    img_queue,
    stop_flag,
    bmode_params,
    graymap_lut,
    pause_flag=None,
    tgc_gain=None,
    enable_tgc=None,
    display_depth_mm=None,
):
    active_display_depth_mm = read_shared_display_depth_mm(display_depth_mm)
    scan = precompute_scan_converter(DEFAULT_CHANNEL_COUNT, DEFAULT_RF_DEPTH, active_display_depth_mm)

    # Basic imaging parameters used by both the legacy display and the GUI.
    c = DEFAULT_SOUND_SPEED_M_S
    f0 = DEFAULT_PROBE_FREQUENCY_HZ
    fs = DEFAULT_SAMPLING_RATE_HZ
    depth = DEFAULT_RF_DEPTH

    log10_frac_bits = 14

    wvln = c / f0

    angles = np.linspace(-45, 45, DEFAULT_CHANNEL_COUNT)
    theta = np.deg2rad(angles)

    dr = 0.5 * c / fs
    rmax = dr * (depth - 1)
    rlims = [0, rmax]

    # 生成像素网格
    xlims = rlims[1] * np.array([-0.7, 0.7])
    zlims = rlims[1] * np.array([0, 1])
    img_grid = make_pixel_grid(xlims, zlims, wvln, wvln)

    img_grid_x = img_grid[:, :, 0].ravel()
    img_grid_z = img_grid[:, :, 2].ravel()


    # 构建 LUT 映射关系 (一次性预计算)
    img_r = np.sqrt(img_grid_x**2 + img_grid_z**2)
    img_theta = np.arctan2(img_grid_x, img_grid_z)

    # 半径对应索引
    r_index = np.round((img_r - rlims[0]) / dr).astype(int)
    # 角度对应索引
    theta_index = np.round((img_theta - theta[0]) / (theta[1] - theta[0])).astype(int)

    # 限制范围
    valid = (r_index >= 0) & (r_index < depth) & \
            (theta_index >= 0) & (theta_index < len(theta))


    while not stop_flag.value:
        if pause_flag is not None and pause_flag.value:
            time.sleep(0.03)
            continue

        try:
            rfdata = data_queue.get(timeout=1)
        except Exception:
            continue
        
        requested_display_depth_mm = read_shared_display_depth_mm(display_depth_mm)
        if abs(requested_display_depth_mm - active_display_depth_mm) > 0.5:
            active_display_depth_mm = requested_display_depth_mm
            scan = precompute_scan_converter(
                DEFAULT_CHANNEL_COUNT,
                DEFAULT_RF_DEPTH,
                active_display_depth_mm,
            )

        polar_log10 = apply_tgc_gain_log10_q214(
            rfdata,
            read_shared_tgc_gain(tgc_gain),
            read_shared_tgc_enabled(enable_tgc),
        )
        cart_log10 = scan_convert_bilinear(polar_log10, scan)

        # max_signal = np.max(bimg)
        # bimg_norm = bimg / max_signal

        # drange = max(0, int(DR_value.value))
        # min_val = 10 ** (-drange / 20.0)
        # denom = max(1e-6, 1 - min_val)
        # data_dr = np.clip((bimg_norm - min_val) / denom, 0, 1)

        # data_dr = (data_dr * 255).astype(np.uint8)

        # data_dr = display_log_bmode_auto(bimg, low_percent=10, high_percent=99.5)
        # data_dr = display_log_bmode(bimg, drange_db=60, fpga_log_range_db=60)

        params = read_shared_bmode_params(bmode_params)
        lut = read_shared_lut(graymap_lut)
        img_queue.put(apply_bmode_mapping(cart_log10, params, lut))

    print("[GPU Worker] Stop.")



def display_worker(img_queue, stop_flag, DR_value):
    """
    显示与保存进程
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = os.path.join("usimage", timestamp)
    # os.makedirs(save_dir)

    window_name = "Ultrasound"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 800, 800)

    img_idx = 0
    while not stop_flag.value:
        try:
            img = img_queue.get(timeout=1)
        except:
            continue
        
        img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        # img_rgb = cv2.applyColorMap(img_rgb, cv2.COLORMAP_JET)

    
        cv2.imshow(window_name, img_rgb)


        key = cv2.waitKey(1) & 0xFF
        if key==27:
            stop_flag.value = True
            break

    cv2.destroyAllWindows()
    print("[Display] Stop.")


if __name__ == "__main__":
    mp.set_start_method("spawn")

    # 进程间队列
    data_queue = mp.Queue(maxsize=3)
    img_queue = mp.Queue(maxsize=3)
    stop_flag = mp.Value('b', False)
    defaults = get_default_bmode_params()
    bmode_params = mp.Array(
        'd',
        [
            defaults["dynamic_range_db"],
            defaults["brightness_db"],
            defaults["contrast_gain"],
            defaults["noise_floor_db"],
        ],
    )
    graymap_lut = mp.Array('B', load_graymap_lut().tolist())

    # 启动进程
    usb_proc = mp.Process(target=usb_reader, args=(data_queue, stop_flag))
    data_proc = mp.Process(target=data_worker, args=(data_queue, img_queue, stop_flag, bmode_params, graymap_lut))
    disp_proc = mp.Process(target=display_worker, args=(img_queue, stop_flag, bmode_params))

    usb_proc.start()
    data_proc.start()
    disp_proc.start()

    # 等待退出
    disp_proc.join()  # 等 ESC 退出
    stop_flag.value = True

    print("All processes stopped.")

    usb_proc.terminate()
    data_proc.terminate()
    disp_proc.terminate()

    sys.exit(0)
