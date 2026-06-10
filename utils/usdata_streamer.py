import usb.core
import usb.util
import multiprocessing as mp
import numpy as np
import cv2
import os
import sys
import time
from datetime import datetime
from fractions import Fraction

try:
    import av as pyav
except ImportError:
    pyav = None  # type: ignore[misc, assignment]

# 与 demo_udp_feed.py / stream_relay ingress 一致：MPEG-TS + H.264 over UDP（无 ffmpeg 子进程；依赖 PyAV / libav 编码）
DEFAULT_UDP_MPEGTS_URL = "udp://127.0.0.1:10000?pkt_size=1316"


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

def usb_reader(data_queue, stop_flag):
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

    rfdepth = 4096
    read_length = 1024 * 1000
    line_header1 = 0x1400
    line_header2 = 0x1000
    # line_header1 = 0x2400
    # line_header2 = 0x2000

    ready_data = [0xef,0x01,0x10,0x00]

    rfdata = np.zeros((64, rfdepth), dtype=np.int32)

    cnt = 0

    while not stop_flag.value:
        recv_data = []

        ready_data[1] = 0x01

        cnt += 1
        dev.write(0x1, ready_data, timeout=1000)

        data = dev.read(0x81, read_length, timeout=5000)

        recv_data = byte_to_double(data)

        indices = np.where((recv_data[:-1] == line_header1) & (recv_data[1:] == line_header2))[0]
        
        line_depth = np.diff(indices)
        indices = indices[:-1]
        # indices = indices[(line_depth==4104) | (line_depth==4105)]
        indices = indices[line_depth==(rfdepth+8)]

        line_num = recv_data[indices+2]
        
        for i in range(len(indices)):
            rfdata[line_num[i], :] = recv_data[indices[i]+8:indices[i]+rfdepth+8]


        data_queue.put(rfdata)


    usb.util.dispose_resources(dev)
    print("[USB] Stop.")


def data_worker(data_queue, img_queue, stop_flag, DR_value):

    # 基本参数
    c = 1540.0       # 声速 m/s
    f0 = 2.5e6       # 中心频率
    fs = 25e6        # 采样频率
    depth = 4096

    wvln = c / f0

    angles = np.linspace(-45, 45, 64)
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
        start_time = time.time()
        rfdata = data_queue.get(timeout=1)
        
        bimg = reconstruct_frame(rfdata, r_index, theta_index, valid, (img_grid.shape[0], img_grid.shape[1]))

        bimg_norm  = bimg / np.max(bimg)

        DR = DR_value.value
        min_val = 10 ** (-DR / 60.0)
        data_dr = np.clip((bimg_norm - min_val) / (1 - min_val), 0, 1)

        data_dr = (data_dr * 255).astype(np.uint8)



        # bimg = (bimg - np.nanmin(bimg)) / (np.nanmax(bimg) - np.nanmin(bimg)) * drange

        # bimg_norm = bimg - np.nanmin(bimg)
        # bimg_norm = bimg_norm / np.nanmax(bimg_norm)   # 归一化到 [0,1]
        # bimg_uint8 = (bimg_norm * 255).astype(np.uint8)

        img_queue.put(data_dr)



    print("[GPU Worker] Stop.")



def _open_mpegts_udp_writer(udp_url: str, width: int, height: int, fps: int):
    """PyAV：H.264 封装为 MPEG-TS 经 UDP 发送（对接 stream_relay / 与 demo_udp_feed 目标格式一致）。"""
    if pyav is None:
        raise RuntimeError(
            "UDP 推流需要 PyAV（内含 libav 编码，无需系统 ffmpeg 可执行文件）。请安装: pip install av"
        )

    container = pyav.open(udp_url, mode="w", format="mpegts")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {
        "preset": "ultrafast",
        "tune": "zerolatency",
    }
    ctx = stream.codec_context
    ctx.bit_rate = 2_000_000
    ctx.max_b_frames = 0
    ctx.gop_size = max(fps, 10)
    return container, stream


def display_worker(img_queue, stop_flag, DR_value):
    """
    显示与保存进程；可选经 UDP 输出 MPEG-TS（H.264），供 stream_relay 接收。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = os.path.join("usimage", timestamp)
    os.makedirs(save_dir)

    width = 256
    height = 256
    fps = 10
    udp_url = os.environ.get("PLANE_WAVE_UDP_URL", DEFAULT_UDP_MPEGTS_URL).strip()
    udp_enabled = os.environ.get("PLANE_WAVE_UDP_ENABLE", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "",
    )
    udp_container = None
    udp_stream = None
    frame_idx = 0
    if udp_enabled and udp_url:
        try:
            udp_container, udp_stream = _open_mpegts_udp_writer(udp_url, width, height, fps)
            print(f"[Display] MPEG-TS UDP -> {udp_url} ({width}x{height} @ {fps}fps)")
        except Exception as e:
            print(f"[Display] UDP 推流未启动: {e}", file=sys.stderr)
            udp_container = None
            udp_stream = None

    cv2.namedWindow('Ultrasound', 0)
    cv2.createTrackbar('DR', 'Ultrasound', 30, 80, nothing)
    
    img_idx = 0
    while not stop_flag.value:
        try:
            img = img_queue.get(timeout=1)
        except:
            continue
        
        img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        # img_rgb = cv2.applyColorMap(img_rgb, cv2.COLORMAP_JET)

    
        cv2.imshow("Ultrasound", img_rgb)
        DR_value.value = cv2.getTrackbarPos('DR', 'Ultrasound')

        proc_img = cv2.resize(img_rgb, (256, 256), interpolation=cv2.INTER_LINEAR)
        if udp_container is not None and udp_stream is not None:
            vframe = pyav.VideoFrame.from_ndarray(proc_img, format="bgr24")
            vframe.pts = frame_idx
            vframe.time_base = Fraction(1, fps)
            frame_idx += 1
            for packet in udp_stream.encode(vframe):
                udp_container.mux(packet)

        # timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # with h5py.File(os.path.join(save_dir,f"usimage_{img_idx}.h5"), 'w') as f:
        #     # 图像数据
        #     f.create_dataset('image', data=img)

        #     # 元信息
        #     f.attrs["ID"] = 1
        #     f.attrs['DR'] = DR_value.value
        #     f.attrs['timestamp'] = timestamp
        # img_idx += 1


        key = cv2.waitKey(1) & 0xFF
        if key==27:
            stop_flag.value = True
            break

    if udp_container is not None and udp_stream is not None:
        for packet in udp_stream.encode(None):
            udp_container.mux(packet)
        udp_container.close()
        print("[Display] UDP writer closed.")

    cv2.destroyAllWindows()
    print("[Display] Stop.")


if __name__ == "__main__":
    mp.set_start_method("spawn")

    # 进程间队列
    data_queue = mp.Queue(maxsize=3)
    img_queue = mp.Queue(maxsize=3)
    stop_flag = mp.Value('b', False)
    DR_value = mp.Value('i', 30)

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
