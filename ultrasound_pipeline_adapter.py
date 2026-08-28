import math
import multiprocessing as mp
import time
from queue import Empty

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

import usb_graymap_bmode_pipeline


class FrameReceiverThread(QThread):
    """Receive processed ultrasound frames without blocking the Qt GUI thread."""

    frame_ready = Signal(object)
    fps_updated = Signal(float)
    error_occurred = Signal(str)

    def __init__(
        self,
        img_queue,
        stop_flag,
        pause_flag,
        bmode_params,
        graymap_lut,
        tgc_gain=None,
        enable_tgc=None,
        display_depth_mm=None,
        demo_mode=False,
        parent=None,
    ):
        super().__init__(parent)
        self.img_queue = img_queue
        self.stop_flag = stop_flag
        self.pause_flag = pause_flag
        self.bmode_params = bmode_params
        self.graymap_lut = graymap_lut
        self.tgc_gain = tgc_gain
        self.enable_tgc = enable_tgc
        self.display_depth_mm = display_depth_mm
        self.full_depth_mm = float(usb_graymap_bmode_pipeline.full_imaging_depth_mm())
        self.demo_mode = demo_mode
        self._running = True
        self._last_fps_time = time.perf_counter()
        self._frame_count = 0
        self._demo_phase = 0.0
        self._rng = np.random.default_rng()

    def stop(self):
        self._running = False

    def run(self):
        if self.demo_mode:
            self._run_demo()
            return

        while self._running and not self.stop_flag.value:
            if self.pause_flag.value:
                self.msleep(30)
                continue

            try:
                # usb_graymap_bmode_pipeline.data_worker already produces uint8 B-mode frames.
                frame = self.img_queue.get(timeout=0.2)
            except Empty:
                continue
            except Exception as exc:
                self.error_occurred.emit(f"Frame receive error: {exc}")
                self.msleep(100)
                continue

            self.frame_ready.emit(frame)
            self._update_fps()

    def _run_demo(self):
        while self._running and not self.stop_flag.value:
            if self.pause_flag.value:
                self.msleep(30)
                continue

            frame = self._make_demo_frame()
            self.frame_ready.emit(frame)
            self._update_fps()
            self.msleep(33)

    def _update_fps(self):
        self._frame_count += 1
        now = time.perf_counter()
        elapsed = now - self._last_fps_time
        if elapsed >= 0.5:
            self.fps_updated.emit(self._frame_count / elapsed)
            self._frame_count = 0
            self._last_fps_time = now

    def _make_demo_frame(self):
        """Create a synthetic sector-shaped B-mode image for GUI testing."""
        height, width = 512, 512
        y, x = np.mgrid[0:height, 0:width]
        cx = width / 2.0
        z = y + 18.0
        lateral = x - cx
        angle = np.arctan2(lateral, z)
        radius = np.sqrt(lateral * lateral + z * z)
        sector = (np.abs(angle) <= math.radians(42)) & (radius < 540)

        self._demo_phase += 0.08
        speckle = self._rng.rayleigh(scale=0.22, size=(height, width))
        attenuation = np.exp(-y / 360.0)
        scan_lines = 0.08 * np.sin(0.12 * x + self._demo_phase)

        lesion_1 = np.exp(-(((x - 212) / 42) ** 2 + ((y - 238) / 30) ** 2))
        lesion_2 = np.exp(-(((x - 315) / 28) ** 2 + ((y - 335) / 42) ** 2))
        reflector = np.exp(-((y - (148 + 9 * np.sin(self._demo_phase))) / 4.0) ** 2)

        image = (0.55 * speckle + scan_lines + 0.34 * reflector + 0.32 * lesion_2) * attenuation
        image -= 0.28 * lesion_1
        image = np.clip(image, 0, None)
        image *= sector

        max_signal = float(np.max(image))
        if max_signal > 0:
            image = image / max_signal

        params = self._read_bmode_params()
        dynamic_range_db, brightness_db, contrast_gain, noise_floor_db = params
        dynamic_range_db = max(1.0, float(dynamic_range_db))
        db = 20.0 * np.log10(np.clip(image, 1e-6, 1.0))
        db = (db + float(brightness_db)) * float(contrast_gain)
        db = np.where(db < float(noise_floor_db), float(noise_floor_db), db)
        db = np.clip(db, -dynamic_range_db, 0.0)
        gray = np.clip(np.round((db + dynamic_range_db) / dynamic_range_db * 255.0), 0, 255).astype(np.uint8)
        gray = self._apply_demo_display_depth(gray)
        gray = self._apply_demo_tgc(gray)
        return self._read_graymap_lut()[gray]

    def _read_bmode_params(self):
        with self.bmode_params.get_lock():
            return np.array(self.bmode_params[:], dtype=np.float32)

    def _read_graymap_lut(self):
        with self.graymap_lut.get_lock():
            return np.frombuffer(self.graymap_lut.get_obj(), dtype=np.uint8).copy()

    def _read_tgc_enabled(self):
        if self.enable_tgc is None:
            return False
        return bool(self.enable_tgc.value)

    def _read_display_depth_mm(self):
        if self.display_depth_mm is None:
            return self.full_depth_mm
        return float(np.clip(self.display_depth_mm.value, 20.0, self.full_depth_mm))

    def _apply_demo_display_depth(self, gray):
        display_depth = self._read_display_depth_mm()
        ratio = min(1.0, max(0.05, display_depth / max(1.0, self.full_depth_mm)))
        source_height = max(1, min(gray.shape[0], int(round(gray.shape[0] * ratio))))
        if source_height == gray.shape[0]:
            return gray
        row_idx = np.linspace(0, source_height - 1, gray.shape[0]).astype(np.int32)
        return gray[:source_height, :][row_idx, :]

    def _read_tgc_gain(self):
        if self.tgc_gain is None:
            return None
        with self.tgc_gain.get_lock():
            return np.frombuffer(self.tgc_gain.get_obj(), dtype=np.float32).copy()

    def _apply_demo_tgc(self, gray):
        if not self._read_tgc_enabled():
            return gray
        gain = self._read_tgc_gain()
        if gain is None or gain.size == 0:
            return gray
        depth_gain = np.interp(
            np.linspace(0, gain.size - 1, gray.shape[0], dtype=np.float32),
            np.arange(gain.size, dtype=np.float32),
            gain,
        ).astype(np.float32)
        normalized_gain = depth_gain / max(1.0, float(np.max(depth_gain)))
        compensated = gray.astype(np.float32) * normalized_gain[:, np.newaxis]
        return np.clip(compensated, 0, 255).astype(np.uint8)


class UltrasoundPipeline(QObject):
    """Bridge the existing multiprocessing imaging pipeline into Qt signals."""

    frame_ready = Signal(object)
    fps_updated = Signal(float)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, demo_mode=False, parent=None):
        super().__init__(parent)
        self.demo_mode = demo_mode
        self.params = usb_graymap_bmode_pipeline.get_default_imaging_params()
        defaults = usb_graymap_bmode_pipeline.get_default_bmode_params()
        self.current_bmode_values = [
            defaults["dynamic_range_db"],
            defaults["brightness_db"],
            defaults["contrast_gain"],
            defaults["noise_floor_db"],
        ]
        self.current_graymap_lut = usb_graymap_bmode_pipeline.load_graymap_lut()
        self.current_tgc_gain = usb_graymap_bmode_pipeline.make_tgc_gain()
        self.current_tgc_enabled = False
        self.full_depth_mm = float(self.params["imaging_depth_mm"])
        self.current_display_depth_mm = self.full_depth_mm
        self.ctx = mp.get_context("spawn")
        self.data_queue = None
        self.img_queue = None
        self.stop_flag = None
        self.pause_flag = None
        self.bmode_params = None
        self.graymap_lut = None
        self.tgc_gain = None
        self.enable_tgc = None
        self.display_depth_mm = None
        self.usb_proc = None
        self.data_proc = None
        self.receiver = None
        self.running = False

    def start(self, bmode_values=None):
        if self.running:
            self.pause(False)
            return

        self.data_queue = self.ctx.Queue(maxsize=3)
        self.img_queue = self.ctx.Queue(maxsize=3)
        self.stop_flag = self.ctx.Value("b", False)
        self.pause_flag = self.ctx.Value("b", False)
        values = list(self.current_bmode_values)
        if isinstance(bmode_values, (int, float)):
            values[0] = float(bmode_values)
        elif bmode_values is not None:
            values = [float(v) for v in bmode_values]
        self.current_bmode_values = list(values)
        self.bmode_params = self.ctx.Array("d", values)
        self.graymap_lut = self.ctx.Array("B", self.current_graymap_lut.astype(np.uint8).tolist())
        self.tgc_gain = self.ctx.Array("f", self.current_tgc_gain.astype(np.float32).tolist())
        self.enable_tgc = self.ctx.Value("b", bool(self.current_tgc_enabled))
        self.display_depth_mm = self.ctx.Value("d", float(self.current_display_depth_mm))

        if not self.demo_mode:
            self.usb_proc = self.ctx.Process(
                target=usb_graymap_bmode_pipeline.usb_reader,
                args=(self.data_queue, self.stop_flag, self.pause_flag),
                name="ultrasound-usb-reader",
            )
            self.data_proc = self.ctx.Process(
                target=usb_graymap_bmode_pipeline.data_worker,
                args=(
                    self.data_queue,
                    self.img_queue,
                    self.stop_flag,
                    self.bmode_params,
                    self.graymap_lut,
                    self.pause_flag,
                    self.tgc_gain,
                    self.enable_tgc,
                    self.display_depth_mm,
                ),
                name="ultrasound-data-worker",
            )
            self.usb_proc.start()
            self.data_proc.start()

        self.receiver = FrameReceiverThread(
            self.img_queue,
            self.stop_flag,
            self.pause_flag,
            self.bmode_params,
            self.graymap_lut,
            self.tgc_gain,
            self.enable_tgc,
            self.display_depth_mm,
            demo_mode=self.demo_mode,
        )
        self.receiver.frame_ready.connect(self.frame_ready.emit)
        self.receiver.fps_updated.connect(self.fps_updated.emit)
        self.receiver.error_occurred.connect(self.error_occurred.emit)
        self.receiver.start()

        self.running = True
        self.status_changed.emit("Running")

    def pause(self, paused=True):
        if self.pause_flag is not None:
            self.pause_flag.value = bool(paused)
        self.status_changed.emit("Paused" if paused else "Running")

    def stop(self):
        if (
            not self.running
            and self.receiver is None
            and self.usb_proc is None
            and self.data_proc is None
            and self.data_queue is None
            and self.img_queue is None
        ):
            self.status_changed.emit("Stopped")
            return

        if self.stop_flag is not None:
            self.stop_flag.value = True

        if self.receiver is not None:
            self.receiver.stop()
            if not self.receiver.wait(1500):
                self.receiver.terminate()
                self.receiver.wait(1000)
            self.receiver = None

        for proc in (self.usb_proc, self.data_proc):
            if proc is None:
                continue
            proc.join(timeout=1.0)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
            if proc.is_alive() and hasattr(proc, "kill"):
                proc.kill()
                proc.join(timeout=1.0)

        self.usb_proc = None
        self.data_proc = None
        self._close_queues()
        self.running = False
        self.status_changed.emit("Stopped")

    def set_dynamic_range(self, value):
        if self.bmode_params is not None:
            with self.bmode_params.get_lock():
                self.bmode_params[0] = float(value)
        self.params["dynamic_range_db"] = int(value)
        self.current_bmode_values[0] = float(value)

    def set_bmode_params(self, dynamic_db, brightness_db, contrast_gain, noise_floor_db):
        if self.bmode_params is not None:
            with self.bmode_params.get_lock():
                self.bmode_params[0] = float(dynamic_db)
                self.bmode_params[1] = float(brightness_db)
                self.bmode_params[2] = float(contrast_gain)
                self.bmode_params[3] = float(noise_floor_db)
        self.params["dynamic_range_db"] = float(dynamic_db)
        self.params["brightness_db"] = float(brightness_db)
        self.params["contrast_gain"] = float(contrast_gain)
        self.params["noise_floor_db"] = float(noise_floor_db)
        self.current_bmode_values = [
            float(dynamic_db),
            float(brightness_db),
            float(contrast_gain),
            float(noise_floor_db),
        ]

    def set_graymap_lut(self, lut_uint8_256):
        lut = np.asarray(lut_uint8_256, dtype=np.uint8).reshape(-1)
        if lut.size != 256:
            raise ValueError("Graymap LUT must have 256 entries")
        self.current_graymap_lut = lut.copy()
        if self.graymap_lut is not None:
            with self.graymap_lut.get_lock():
                np.frombuffer(self.graymap_lut.get_obj(), dtype=np.uint8)[:] = lut

    def set_tgc_gain(self, gain_6144):
        gain = np.asarray(gain_6144, dtype=np.float32).reshape(-1)
        expected = usb_graymap_bmode_pipeline.DEFAULT_RF_DEPTH
        if gain.size != expected:
            raise ValueError(f"TGC gain must have {expected} entries")
        self.current_tgc_gain = gain.copy()
        if self.tgc_gain is not None:
            with self.tgc_gain.get_lock():
                np.frombuffer(self.tgc_gain.get_obj(), dtype=np.float32)[:] = gain

    def set_tgc_enabled(self, enabled):
        self.current_tgc_enabled = bool(enabled)
        if self.enable_tgc is not None:
            self.enable_tgc.value = bool(enabled)

    def set_display_depth_mm(self, depth_mm):
        depth = float(np.clip(float(depth_mm), 20.0, self.full_depth_mm))
        self.current_display_depth_mm = depth
        self.params["display_depth_mm"] = depth
        if self.display_depth_mm is not None:
            self.display_depth_mm.value = depth

    def _close_queues(self):
        for queue_obj in (self.data_queue, self.img_queue):
            if queue_obj is None:
                continue
            try:
                queue_obj.close()
                queue_obj.join_thread()
            except Exception:
                pass
        self.data_queue = None
        self.img_queue = None
        self.stop_flag = None
        self.pause_flag = None
        self.bmode_params = None
        self.graymap_lut = None
        self.tgc_gain = None
        self.enable_tgc = None
