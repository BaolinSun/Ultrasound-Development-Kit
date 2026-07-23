import argparse
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
import usb.core
import usb.util
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from rfdata_acq import (
    MAGIC,
    WORDS_PER_CHANNEL,
    HEADER_WORDS,
    RF_SAMPLES,
    RTC_LINES,
    CHANNELS_PER_RTC_LINE,
    LOGICAL_LINES,
    TOTAL_CHANNELS,
    FRAME_BYTES,
    USB_EP_OUT,
    USB_EP_IN,
    USB_TIMEOUT_MS,
    byte_to_words,
    read_circular_block,
    send_hv_command,
    words_to_signed16,
)

USB_VENDOR_ID = 0x0424
USB_PRODUCT_ID = 0x4940
READY_DATA = [0xEF, 0x01, 0x10, 0x00]
DEFAULT_HV_DELAY_S = 3.0
FRAME_QUEUE_DEPTH = 2


def find_circular_frame_start_fast(words):
    """Find A55A, 0000, 0000 in a circular uint16 frame buffer."""
    words = np.asarray(words, dtype=np.uint16)
    n_words = len(words)
    if n_words < WORDS_PER_CHANNEL:
        raise RuntimeError(f"RF frame too short: {n_words} words")

    candidates = np.flatnonzero(words == MAGIC)
    for idx in candidates:
        if int(words[(idx + 1) % n_words]) == 0 and int(words[(idx + 2) % n_words]) == 0:
            return int(idx)

    raise RuntimeError("Cannot find circular frame header A55A, 0000, 0000")


def parse_selected_channel(words, focus_line, channel):
    """Extract one signed RF waveform for logical line 0..63 and channel 0..63."""
    if not 0 <= focus_line < LOGICAL_LINES:
        raise ValueError(f"focus_line must be 0..{LOGICAL_LINES - 1}, got {focus_line}")
    if not 0 <= channel < TOTAL_CHANNELS:
        raise ValueError(f"channel must be 0..{TOTAL_CHANNELS - 1}, got {channel}")

    words = np.asarray(words, dtype=np.uint16)
    expected_words = RTC_LINES * CHANNELS_PER_RTC_LINE * WORDS_PER_CHANNEL
    if len(words) < expected_words:
        raise ValueError(f"RF frame is too short: {len(words)} words, expected {expected_words}")
    words = words[:expected_words]

    frame_start = find_circular_frame_start_fast(words)
    rtc_line = focus_line * 2 + (1 if channel >= CHANNELS_PER_RTC_LINE else 0)
    local_channel = channel % CHANNELS_PER_RTC_LINE
    block_index = rtc_line * CHANNELS_PER_RTC_LINE + local_channel
    block_start = frame_start + block_index * WORDS_PER_CHANNEL
    block = read_circular_block(words, block_start, WORDS_PER_CHANNEL)

    magic = int(block[0])
    got_rtc_line = int(block[1])
    got_channel = int(block[2])
    if magic != MAGIC:
        raise RuntimeError(
            f"Bad magic for line={focus_line}, channel={channel}: got 0x{magic:04X}"
        )
    if got_rtc_line != rtc_line or got_channel != channel:
        raise RuntimeError(
            f"Bad header for line={focus_line}, channel={channel}: "
            f"got rtc_line={got_rtc_line}, channel={got_channel}, "
            f"expected rtc_line={rtc_line}, channel={channel}"
        )

    samples = words_to_signed16(block[HEADER_WORDS:])
    meta = {
        "frame_start_word": frame_start,
        "frame_start_byte": frame_start * 2,
        "focus_line": focus_line,
        "rtc_line": rtc_line,
        "channel": channel,
        "local_channel": local_channel,
    }
    return samples, meta


def acquire_frame_words(dev, timeout_ms=USB_TIMEOUT_MS):
    dev.write(USB_EP_OUT, READY_DATA, timeout=1000)
    raw = bytes(dev.read(USB_EP_IN, FRAME_BYTES, timeout=timeout_ms))
    if len(raw) != FRAME_BYTES:
        raise RuntimeError(f"USB short read: expected {FRAME_BYTES} bytes, got {len(raw)} bytes")
    return byte_to_words(raw)


class UsbFrameReader(threading.Thread):
    def __init__(self, frame_queue, status_queue, stop_event, args, single_shot=False):
        super().__init__(daemon=True)
        self.frame_queue = frame_queue
        self.status_queue = status_queue
        self.stop_event = stop_event
        self.args = args
        self.single_shot = single_shot
        self.dev = None
        self.hv_enabled = False

    def _status(self, kind, message, **payload):
        self.status_queue.put({"kind": kind, "message": message, **payload})

    def _connect(self):
        self.dev = usb.core.find(idVendor=self.args.vid, idProduct=self.args.pid)
        if self.dev is None:
            raise RuntimeError(f"USB device {self.args.vid:04X}:{self.args.pid:04X} not found")
        self.dev.set_configuration()
        self._status("status", f"Connected USB {self.args.vid:04X}:{self.args.pid:04X}")

    def _enable_hv(self):
        if self.args.no_hv:
            return
        send_hv_command(self.dev, True)
        self.hv_enabled = True
        if self.args.hv_delay > 0:
            self._status("status", f"HV enabled; waiting {self.args.hv_delay:.2f} s")
            deadline = time.monotonic() + self.args.hv_delay
            while time.monotonic() < deadline and not self.stop_event.is_set():
                time.sleep(0.05)

    def _disable_hv(self):
        if self.dev is None or self.args.no_hv or not self.hv_enabled:
            return
        try:
            send_hv_command(self.dev, False)
            self._status("status", "HV disabled")
        except Exception as error:
            self._status("error", f"Failed to disable HV: {error}")
        finally:
            self.hv_enabled = False

    def run(self):
        frame_count = 0
        try:
            self._connect()
            self._enable_hv()
            while not self.stop_event.is_set():
                started = time.monotonic()
                words = acquire_frame_words(self.dev, self.args.timeout_ms)
                frame_count += 1

                while True:
                    try:
                        self.frame_queue.put_nowait((frame_count, started, words))
                        break
                    except queue.Full:
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            pass

                self._status("frame", f"Frame {frame_count} received", frame_count=frame_count)
                if self.single_shot:
                    break

        except Exception as error:
            self._status("error", str(error))
        finally:
            self._disable_hv()
            if self.dev is not None:
                usb.util.dispose_resources(self.dev)
                self.dev = None
            self._status("stopped", f"Stopped after {frame_count} frame(s)", frame_count=frame_count)


class RfLiveViewer(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.title("Raw RF Live Viewer")
        self.geometry("1100x760")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.frame_queue = queue.Queue(maxsize=FRAME_QUEUE_DEPTH)
        self.status_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.reader = None
        self.latest_words = None
        self.latest_frame_count = 0
        self.last_meta = None

        self.line_var = tk.IntVar(value=args.line)
        self.channel_var = tk.IntVar(value=args.channel)
        self.status_var = tk.StringVar(value="Idle")
        self.detail_var = tk.StringVar(value="line 0, channel 0")

        self._build_controls()
        self._build_plot()
        self.after(100, self._poll_queues)

    def _build_controls(self):
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="Line").pack(side=tk.LEFT)
        ttk.Spinbox(bar, from_=0, to=LOGICAL_LINES - 1, textvariable=self.line_var, width=5, command=self._refresh_from_latest).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(bar, text="Channel").pack(side=tk.LEFT)
        ttk.Spinbox(bar, from_=0, to=TOTAL_CHANNELS - 1, textvariable=self.channel_var, width=5, command=self._refresh_from_latest).pack(side=tk.LEFT, padx=(4, 12))



        self.start_button = ttk.Button(bar, text="Start", command=self.start_reader)
        self.start_button.pack(side=tk.LEFT, padx=3)
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop_reader, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Single Shot", command=self.single_shot).pack(side=tk.LEFT, padx=3)

        status = ttk.Frame(self, padding=(8, 2, 8, 6))
        status.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.detail_var).pack(side=tk.RIGHT)

    def _build_plot(self):
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Raw RF waveform")
        self.ax.set_xlabel("Sample")
        self.ax.set_ylabel("ADC code")
        self.ax.set_ylim(-512, 512)
        self.ax.grid(True, alpha=0.3)
        self.line_plot, = self.ax.plot(np.arange(RF_SAMPLES), np.zeros(RF_SAMPLES), linewidth=0.9)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.draw()
        toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _validated_selection(self):
        line = int(self.line_var.get())
        channel = int(self.channel_var.get())
        if not 0 <= line < LOGICAL_LINES:
            raise ValueError(f"Line must be 0..{LOGICAL_LINES - 1}")
        if not 0 <= channel < TOTAL_CHANNELS:
            raise ValueError(f"Channel must be 0..{TOTAL_CHANNELS - 1}")
        return line, channel


    def start_reader(self):
        if self.reader is not None and self.reader.is_alive():
            return
        try:
            self._validated_selection()
        except Exception as error:
            self.status_var.set(str(error))
            return

        self.stop_event.clear()
        self.reader = UsbFrameReader(self.frame_queue, self.status_queue, self.stop_event, self.args, single_shot=False)
        self.reader.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("Starting acquisition")

    def single_shot(self):
        if self.reader is not None and self.reader.is_alive():
            return
        try:
            self._validated_selection()
        except Exception as error:
            self.status_var.set(str(error))
            return

        self.stop_event.clear()
        self.reader = UsbFrameReader(self.frame_queue, self.status_queue, self.stop_event, self.args, single_shot=True)
        self.reader.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("Acquiring one frame")

    def stop_reader(self):
        self.stop_event.set()
        self.status_var.set("Stopping")

    def _poll_queues(self):
        try:
            while True:
                event = self.status_queue.get_nowait()
                kind = event.get("kind")
                self.status_var.set(event.get("message", ""))
                if kind == "stopped":
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
        except queue.Empty:
            pass

        newest = None
        try:
            while True:
                newest = self.frame_queue.get_nowait()
        except queue.Empty:
            pass

        if newest is not None:
            frame_count, _, words = newest
            self.latest_frame_count = frame_count
            self.latest_words = words
            self._refresh_from_latest()

        self.after(100, self._poll_queues)

    def _refresh_from_latest(self):
        if self.latest_words is None:
            return
        try:
            focus_line, channel = self._validated_selection()
            samples, meta = parse_selected_channel(self.latest_words, focus_line, channel)
            self.last_meta = meta
            x = np.arange(samples.size)
            self.line_plot.set_data(x, samples)
            self.ax.set_xlim(0, max(1, samples.size - 1))
            self.ax.set_ylim(-512, 512)
            self.ax.set_title(f"Raw RF waveform: line {focus_line}, channel {channel}")
            self.detail_var.set(
                f"frame {self.latest_frame_count}, rtc_line {meta['rtc_line']}, "
                f"frame_start word {meta['frame_start_word']}"
            )
            self.canvas.draw_idle()
        except Exception as error:
            self.status_var.set(f"Parse error: {error}")

    def on_close(self):
        self.stop_reader()
        self.after(300, self.destroy)


def parse_args():
    parser = argparse.ArgumentParser(description="Live raw RF waveform viewer over USB.")
    parser.add_argument("--line", type=int, default=0, help="initial logical focus line, 0..63")
    parser.add_argument("--channel", type=int, default=0, help="initial channel, 0..63")
    parser.add_argument("--hv-delay", type=float, default=DEFAULT_HV_DELAY_S, help="seconds to wait after HV enable")
    parser.add_argument("--timeout-ms", type=int, default=USB_TIMEOUT_MS, help="USB read timeout in milliseconds")
    parser.add_argument("--vid", type=lambda x: int(x, 0), default=USB_VENDOR_ID, help="USB vendor ID")
    parser.add_argument("--pid", type=lambda x: int(x, 0), default=USB_PRODUCT_ID, help="USB product ID")
    parser.add_argument("--no-hv", action="store_true", help="do not send HV SET commands")
    args = parser.parse_args()

    if not 0 <= args.line < LOGICAL_LINES:
        parser.error(f"--line must be 0..{LOGICAL_LINES - 1}")
    if not 0 <= args.channel < TOTAL_CHANNELS:
        parser.error(f"--channel must be 0..{TOTAL_CHANNELS - 1}")
    if args.hv_delay < 0:
        parser.error("--hv-delay must be non-negative")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    return args


def main():
    app = RfLiveViewer(parse_args())
    app.mainloop()


if __name__ == "__main__":
    main()



