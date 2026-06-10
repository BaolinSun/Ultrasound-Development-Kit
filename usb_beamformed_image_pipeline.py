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


DEFAULT_SOUND_SPEED_M_S = 1540.0
DEFAULT_PROBE_FREQUENCY_HZ = 4.0e6
DEFAULT_SAMPLING_RATE_HZ = 25e6
DEFAULT_RF_DEPTH = 4096
DEFAULT_CHANNEL_COUNT = 64
DEFAULT_ELEMENT_COUNT = 64
DEFAULT_DYNAMIC_RANGE_DB = 60


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
        "gain_db": 0,
        "tgc": "Flat",
    }

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

    rfdepth = DEFAULT_RF_DEPTH
    read_length = 1024 * 1000
    line_header1 = 0x1400
    line_header2 = 0x1000

    ready_data = [0xef,0x01,0x10,0x00]

    rfdata = np.zeros((DEFAULT_CHANNEL_COUNT, rfdepth), dtype=np.int32)

    cnt = 0

    while not stop_flag.value:
        if pause_flag is not None and pause_flag.value:
            time.sleep(0.03)
            continue

        recv_data = []

        ready_data[1] = 0x01

        cnt += 1
        dev.write(0x1, ready_data, timeout=1000)

        data = dev.read(0x81, read_length, timeout=5000)

        recv_data = byte_to_double(data)

        indices = np.where((recv_data[:-1] == line_header1) & (recv_data[1:] == line_header2))[0]
        
        line_depth = np.diff(indices)
        indices = indices[:-1]
        indices = indices[line_depth==(rfdepth+8)]

        line_num = recv_data[indices+2]
        
        for i in range(len(indices)):
            rfdata[line_num[i], :] = recv_data[indices[i]+8:indices[i]+rfdepth+8]


        data_queue.put(rfdata)


    usb.util.dispose_resources(dev)
    print("[USB] Stop.")


def data_worker(data_queue, img_queue, stop_flag, DR_value, pause_flag=None):

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
        
        bimg = reconstruct_frame(rfdata, r_index, theta_index, valid, (img_grid.shape[0], img_grid.shape[1]))

        # max_signal = np.max(bimg)
        # bimg_norm = bimg / max_signal

        # drange = max(0, int(DR_value.value))
        # min_val = 10 ** (-drange / 20.0)
        # denom = max(1e-6, 1 - min_val)
        # data_dr = np.clip((bimg_norm - min_val) / denom, 0, 1)

        # data_dr = (data_dr * 255).astype(np.uint8)

        # data_dr = display_log_bmode_auto(bimg, low_percent=10, high_percent=99.5)
        # data_dr = display_log_bmode(bimg, drange_db=60, fpga_log_range_db=60)

        dynamic_range_db = int(DR_value.value)
        cart_data = bimg
        cart_log10 = cart_data / (2 ** log10_frac_bits)
        cart_log10_max = np.max(cart_log10[np.isfinite(cart_log10)])
        cart_db = 20 * (cart_log10 - cart_log10_max)
        cart_db = np.clip(cart_db, -dynamic_range_db, 0)

        cart_uint8 = np.round((cart_db + dynamic_range_db) / dynamic_range_db * 255).astype(np.uint8)




        img_queue.put(cart_uint8)

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
    DR_value = mp.Value('i', DEFAULT_DYNAMIC_RANGE_DB)

    # 启动进程
    usb_proc = mp.Process(target=usb_reader, args=(data_queue, stop_flag))
    data_proc = mp.Process(target=data_worker, args=(data_queue, img_queue, stop_flag, DR_value))
    disp_proc = mp.Process(target=display_worker, args=(img_queue, stop_flag, DR_value))

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
