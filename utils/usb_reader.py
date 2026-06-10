import usb.core
import usb.util
import numpy as np
import matplotlib.pyplot as plt
from scipy.io import savemat


LINE_NUM = 64
RF_DEPTH = 6144
LINE_HEADER_WORDS = 8
WORDS_PER_LINE = LINE_HEADER_WORDS + RF_DEPTH
BYTES_PER_WORD = 2

BF_FRAME_WORDS = LINE_NUM * WORDS_PER_LINE
BF_FRAME_BYTES = BF_FRAME_WORDS * BYTES_PER_WORD

DDR_RING_BYTES = 1024 * 1000
DDR_RING_WORDS = DDR_RING_BYTES // BYTES_PER_WORD
USB_CHUNK_BYTES = 1024

LINE_HEADER1 = 0x1C00
LINE_HEADER2 = 0x1800

USB_VENDOR_ID = 0x0424
USB_PRODUCT_ID = 0x4940
USB_EP_OUT = 0x01
USB_EP_IN = 0x81

READY_DATA = [0xEF, 0x01, 0x10, 0x00]


def byte_to_u16(data: bytes) -> np.ndarray:
    if len(data) % BYTES_PER_WORD != 0:
        raise ValueError(f"Data length must be even, got {len(data)} bytes")
    return np.frombuffer(data, dtype="<u2")


def read_exact_window(dev, endpoint=USB_EP_IN, window_bytes=DDR_RING_BYTES, timeout=5000) -> bytes:
    data = bytes(dev.read(endpoint, window_bytes, timeout=timeout))
    if len(data) != window_bytes:
        raise RuntimeError(f"USB short read: expected {window_bytes} bytes, got {len(data)} bytes")
    return data


def parse_beamformed_window(window_bytes: bytes) -> tuple[np.ndarray, set[int], dict]:
    recv_data = byte_to_u16(window_bytes)

    rfdata = np.zeros((LINE_NUM, RF_DEPTH), dtype=np.int32)
    line_set = set()

    headers = np.where(
        (recv_data[:-1] == LINE_HEADER1) &
        (recv_data[1:] == LINE_HEADER2)
    )[0]

    if len(headers) < 2:
        raise ValueError(f"Too few line headers found: {len(headers)}")

    line_gap = np.diff(headers)
    valid_header_pos = headers[:-1][line_gap == WORDS_PER_LINE]

    for base in valid_header_pos:
        data_start = base + LINE_HEADER_WORDS
        data_end = data_start + RF_DEPTH
        if data_end > len(recv_data):
            continue

        line_num = int(recv_data[base + 2])
        if not (0 <= line_num < LINE_NUM):
            continue

        # If the DDR window contains repeated line numbers, keep the later one in the window.
        rfdata[line_num, :] = recv_data[data_start:data_end].astype(np.int32)
        line_set.add(line_num)

    stats = {
        "window_words": int(len(recv_data)),
        "headers": int(len(headers)),
        "valid_lines": int(len(valid_header_pos)),
        "line_set": sorted(int(x) for x in line_set),
    }

    return rfdata, line_set, stats


def usb_reader(data_queue=None, stop_flag=None, save_mat=True, debug_plot=False):
    """
    USB data acquisition for the PL DDR ring window.

    Current DDR/USB format:
        DDR window = 1024 * 1000 bytes = 0xFA000
        line       = 8 header words + 6144 data words
        word       = uint16 little-endian
        parser     = search line header and require adjacent header diff == 6152 words
    """
    dev = usb.core.find(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID)
    if dev is None:
        raise RuntimeError("USB device not found")

    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    ep = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_OUT,
    )
    print(f"[USB] OUT endpoint descriptor: {ep}")
    print(
        f"[USB] read window: {DDR_RING_BYTES} bytes = 0x{DDR_RING_BYTES:X}, "
        f"words={DDR_RING_WORDS}"
    )
    print(
        f"[USB] line format: header={LINE_HEADER_WORDS} words, "
        f"depth={RF_DEPTH}, words_per_line={WORDS_PER_LINE}"
    )

    try:
        while True:
            if stop_flag is not None and stop_flag.value:
                break

            dev.write(USB_EP_OUT, READY_DATA, timeout=1000)

            window_raw = read_exact_window(dev)
            rfdata, line_set, stats = parse_beamformed_window(window_raw)

            print(
                f"[USB] headers={stats['headers']}, valid_lines={stats['valid_lines']}, "
                f"received_lines={len(line_set)} / {LINE_NUM}"
            )
            print(f"[USB] line_set={stats['line_set']}")

            if len(line_set) == LINE_NUM:
                if debug_plot:
                    plt.figure()
                    plt.plot(rfdata[25, :])
                    plt.title("Focused line 25")
                    plt.grid(True)
                    plt.show()

                if save_mat:
                    savemat("rfdata.mat", {"rfdata": rfdata})
                    print("[USB] saved rfdata.mat")

                if data_queue is not None:
                    data_queue.put(rfdata)

                break

    finally:
        usb.util.dispose_resources(dev)
        print("[USB] Stop.")


if __name__ == "__main__":
    usb_reader(None, None, save_mat=True, debug_plot=True)
