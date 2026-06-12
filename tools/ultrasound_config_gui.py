#!/usr/bin/env python3
"""Dark themed ultrasound configuration GUI.

The GUI reuses ultrasound_config_uart.py for JSON validation, command packing,
and UART transaction semantics. MATLAB remains responsible for generating JSON
configuration files.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

try:
    from PySide6.QtCore import QPoint, QSize, QThread, Qt, Signal
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QAbstractItemView,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - depends on PC environment
    raise SystemExit(
        "PySide6 is required for the GUI. Install with:\n"
        "  pip install -r tools/requirements_gui.txt"
    ) from exc

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - depends on PC environment
    serial = None
    list_ports = None

import ultrasound_config_uart as uart_cfg


TARGETS = (
    "afe5832",
    "tx7332",
    "delay_profile",
    "demod_coeffs",
    "log_table",
    "x_element",
    "hamming",
    "dfilter",
    "t_timing",
    "sin_beta",
    "all",
)

BRAM_TARGETS = tuple(uart_cfg.BRAM_TARGET_ORDER)

DEFAULT_CONFIG_PATHS = {
    "afe5832": uart_cfg.DEFAULT_AFE_CONFIG,
    "tx7332": uart_cfg.DEFAULT_TX7332_CONFIG,
    "delay_profile": uart_cfg.DEFAULT_DELAY_PROFILE_CONFIG,
    "demod_coeffs": uart_cfg.DEFAULT_DEMOD_COEFFS_CONFIG,
    "log_table": uart_cfg.DEFAULT_LOG_TABLE_CONFIG,
    "x_element": uart_cfg.DEFAULT_X_ELEMENT_CONFIG,
    "hamming": uart_cfg.DEFAULT_HAMMING_CONFIG,
    "dfilter": uart_cfg.DEFAULT_DFILTER_CONFIG,
    "t_timing": uart_cfg.DEFAULT_T_TIMING_CONFIG,
    "sin_beta": uart_cfg.DEFAULT_SIN_BETA_CONFIG,
}

TARGET_TITLES = {
    "afe5832": "AFE5832",
    "tx7332": "TX7332",
    "delay_profile": "Delay Profile",
    "demod_coeffs": "Demodulation Coeffs",
    "log_table": "Log Table",
    "x_element": "X Element",
    "hamming": "Hamming",
    "dfilter": "Dynamic Filter",
    "t_timing": "T Timing",
    "sin_beta": "Sin Beta",
    "all": "All Targets",
}
DTGC_FLOAT_FIELDS = set(uart_cfg.DTGC_CALC_FLOAT_KEYS)
DTGC_INT_FIELDS = set(uart_cfg.DTGC_CALC_INT_KEYS)
DTGC_FIELD_ORDER = tuple(uart_cfg.DTGC_CALC_KEYS)
DTGC_VCA_GAIN_FIELDS = ("lna_gain_db", "pga_gain_db")

AFE_INIT_ROWS = [
    ("ADC", "0x00", "0x0001"),
    ("ADC", "0x01", "0x0000"),
    ("ADC", "0x03", "0x0010"),
    ("ADC", "0xD1", "0x0007"),
    ("ADC", "0xD4", "0x0001"),
    ("ADC", "0x41", "0x8000"),
    ("ADC", "0x42", "0x8000"),
    ("ADC", "0x41", "0x0000"),
    ("ADC", "0x42", "0x0000"),
    ("ADC", "0x01", "0x0000"),
    ("ADC", "0x02", "0x0000"),
    ("ADC", "0x03", "0x2010"),
    ("ADC", "0x15", "0x0027"),
    ("ADC", "0x21", "0x0007"),
    ("ADC", "0x2D", "0x0027"),
    ("ADC", "0x39", "0x0007"),
    ("ADC", "0x04", "0x0013"),
    ("DTGC", "0xA1", "0x145A"),
    ("DTGC", "0xA2", "0x30C8"),
    ("DTGC", "0xB5", "0x011F"),
    ("DTGC", "0xB6", "0x4800"),
    ("DTGC", "0xA1", "0x1090"),
    ("DTGC", "0xA2", "0x03FF"),
    ("ADC", "0xC7", "0x2800"),
    ("ADC", "0xC6", "0x0000"),
    ("ADC", "0xC8", "0x0000"),
]

ACCENT = QColor("#20d6e8")
GREEN = QColor("#30d158")
ORANGE = QColor("#ffb020")
RED = QColor("#ff5c73")
BG = QColor("#071018")
CARD = QColor("#101b26")
GRID = QColor("#263848")
TEXT = QColor("#d8e7f2")
MUTED = QColor("#83a2b8")


def parse_word(value: Any) -> int:
    return uart_cfg.parse_bram_word(value, "word")


def words_from_section(config: dict[str, Any], section: str) -> list[int]:
    return [parse_word(value) for value in config.get("bram", {}).get(section, [])]


def signed_from_words(words: list[int], bits: int) -> list[int]:
    sign = 1 << (bits - 1)
    mask = 1 << bits
    return [word - mask if word & sign else word for word in (item & (mask - 1) for item in words)]


def sample_series(values: list[int] | list[float], max_points: int = 2048) -> list[float]:
    if len(values) <= max_points:
        return [float(value) for value in values]
    step = max(1, len(values) // max_points)
    return [float(values[index]) for index in range(0, len(values), step)]


def make_table_item(text: Any, editable: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def parse_table_int(text: str, bits: int, name: str) -> int:
    value = int(text.strip(), 0)
    return uart_cfg.require_range(value, bits, name)


def parse_table_float(text: str, name: str) -> float:
    try:
        return float(text.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def fmt_word(value: int) -> str:
    return f"0x{value & 0xFFFFFFFF:08X}"


def fmt_u16(value: int) -> str:
    return f"0x{value & 0xFFFF:04X}"


def fmt_u8(value: int) -> str:
    return f"0x{value & 0xFF:02X}"


def linspace(start: float, stop: float, count: int) -> list[float]:
    if count == 1:
        return [start]
    step = (stop - start) / (count - 1)
    return [start + step * index for index in range(count)]


def hamming_values(count: int) -> list[float]:
    if count == 1:
        return [1.0]
    return [0.54 - 0.46 * math.cos(2 * math.pi * index / (count - 1)) for index in range(count)]


def sinc(value: float) -> float:
    if abs(value) < 1e-12:
        return 1.0
    return math.sin(math.pi * value) / (math.pi * value)


def lowpass_fir1_coeffs(order: int, wn: float) -> list[float]:
    center = order / 2
    window = hamming_values(order + 1)
    coeffs = [wn * sinc(wn * (index - center)) * window[index] for index in range(order + 1)]
    gain = sum(coeffs)
    return [value / gain for value in coeffs]


def demod_float_preview(count: int = 512) -> tuple[list[float], list[float]]:
    fs = 25e6
    f0 = 4.3e6
    return (
        [math.sin(2 * math.pi * f0 * index / fs) for index in range(count)],
        [math.cos(2 * math.pi * f0 * index / fs) for index in range(count)],
    )


def delay_profile_float_lines() -> list[list[float]]:
    pitch = 0.254e-3
    element_num = 64
    speed = 1540.0
    focus = 80e-3
    angles = linspace(-45.0, 45.0, 64)
    element_x = [(index - (element_num - 1) / 2) * pitch for index in range(element_num)]
    lines: list[list[float]] = []
    for angle in angles:
        theta = math.radians(angle)
        focus_x = focus * math.sin(theta)
        focus_z = focus * math.cos(theta)
        line = []
        for x in element_x:
            length = math.sqrt((x - focus_x) ** 2 + focus_z**2)
            line.append(5e-6 - (length - focus) / speed)
        lines.append(line)
    return lines


def log_table_float_values() -> list[float]:
    return [math.log10(index) for index in range(1, 1025)]


def x_element_float_values() -> list[float]:
    element_num = 64
    pitch = 0.3
    return [((index) - (element_num - 1) / 2) * pitch for index in range(element_num)]


def sin_beta_float_values() -> list[float]:
    return [math.sin(math.radians(angle)) for angle in linspace(-45.0, 45.0, 64)]


class PlotWidget(QWidget):
    """Small Qt-native plot widget for preview curves."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.series: list[tuple[str, list[float], QColor]] = []
        self.setMinimumHeight(220)

    def set_series(self, series: list[tuple[str, list[float], QColor]]) -> None:
        self.series = series
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(14, 14, -14, -26)
        painter.fillRect(self.rect(), CARD)

        painter.setPen(QPen(GRID, 1))
        for i in range(6):
            x = rect.left() + rect.width() * i / 5
            y = rect.top() + rect.height() * i / 5
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        if not self.series:
            painter.setPen(MUTED)
            painter.drawText(rect, Qt.AlignCenter, "No preview data")
            return

        values = [value for _, data, _ in self.series for value in data]
        if not values:
            painter.setPen(MUTED)
            painter.drawText(rect, Qt.AlignCenter, "No preview data")
            return

        ymin = min(values)
        ymax = max(values)
        if ymin == ymax:
            ymin -= 1.0
            ymax += 1.0

        for label, data, color in self.series:
            if len(data) < 2:
                continue
            painter.setPen(QPen(color, 2))
            last_x = rect.left()
            last_y = rect.bottom() - (data[0] - ymin) / (ymax - ymin) * rect.height()
            for index, value in enumerate(data[1:], start=1):
                x = rect.left() + index / (len(data) - 1) * rect.width()
                y = rect.bottom() - (value - ymin) / (ymax - ymin) * rect.height()
                painter.drawLine(int(last_x), int(last_y), int(x), int(y))
                last_x, last_y = x, y

        painter.setPen(TEXT)
        legend_x = rect.left() + 4
        for label, _, color in self.series[:6]:
            painter.setPen(QPen(color, 3))
            painter.drawLine(legend_x, rect.bottom() + 16, legend_x + 18, rect.bottom() + 16)
            painter.setPen(TEXT)
            painter.drawText(legend_x + 24, rect.bottom() + 21, label)
            legend_x += 120
        if len(self.series) > 6:
            painter.setPen(MUTED)
            painter.drawText(legend_x, rect.bottom() + 21, f"+{len(self.series) - 6} lines")


class HeatmapWidget(QWidget):
    """Qt-native heatmap for large packed BRAM regions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.values: list[int] = []
        self.columns = 128
        self.setMinimumHeight(220)

    def set_values(self, values: list[int], columns: int = 128) -> None:
        self.values = values
        self.columns = columns
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), CARD)
        rect = self.rect().adjusted(14, 14, -14, -14)
        if not self.values:
            painter.setPen(MUTED)
            painter.drawText(rect, Qt.AlignCenter, "No heatmap data")
            return

        rows = max(1, (len(self.values) + self.columns - 1) // self.columns)
        cell_w = rect.width() / self.columns
        cell_h = rect.height() / rows
        vals = [value & 0xFFFF for value in self.values]
        vmin = min(vals)
        vmax = max(vals)
        span = max(1, vmax - vmin)

        for index, value in enumerate(vals):
            col = index % self.columns
            row = index // self.columns
            level = int((value - vmin) * 255 / span)
            color = QColor(5, min(255, 64 + level), min(255, 90 + level))
            x = rect.left() + col * cell_w
            y = rect.top() + row * cell_h
            painter.fillRect(int(x), int(y), max(1, int(cell_w) + 1), max(1, int(cell_h) + 1), color)


class TitleBar(QWidget):
    """VSCode-like frameless title bar."""

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.window = parent
        self.drag_position: QPoint | None = None
        self.setObjectName("titleBar")
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(8)

        app_name = QLabel("Ultrasound Config")
        app_name.setObjectName("appTitle")
        subtitle = QLabel("runtime JSON visualizer")
        subtitle.setObjectName("appSubtitle")
        layout.addWidget(app_name)
        layout.addWidget(subtitle, 1)

        self.min_button = QPushButton("−")
        self.max_button = QPushButton("□")
        self.close_button = QPushButton("×")
        for button in (self.min_button, self.max_button, self.close_button):
            button.setObjectName("titleButton")
            button.setFixedSize(38, 30)
            layout.addWidget(button)
        self.close_button.setObjectName("closeButton")

        self.min_button.clicked.connect(self.window.showMinimized)
        self.max_button.clicked.connect(self._toggle_maximized)
        self.close_button.clicked.connect(self.window.close)

    def _toggle_maximized(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
            self.max_button.setText("□")
        else:
            self.window.showMaximized()
            self.max_button.setText("❐")

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self.drag_position is not None and event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self.drag_position = None
        event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()


class SendWorker(QThread):
    log_line = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, port_name: str, baud: int, timeout_s: float, commands: list[str], seq: int) -> None:
        super().__init__()
        self.port_name = port_name
        self.baud = baud
        self.timeout_s = timeout_s
        self.commands = commands
        self.seq = seq

    def run(self) -> None:
        if serial is None:
            self.failed.emit("pyserial is required: pip install -r tools/requirements_gui.txt")
            return
        try:
            with serial.Serial(self.port_name, self.baud, timeout=0.1) as port:
                time.sleep(0.2)
                port.reset_input_buffer()
                cfg_started = False
                for line in self.commands:
                    self.log_line.emit(f"> {line}")
                    port.write((line + "\n").encode("ascii"))
                    port.flush()
                    response = self._read_response(port)
                    self.log_line.emit(f"< {response}")
                    if line.startswith("CFG BEGIN "):
                        cfg_started = True
                    elif line == "CFG END":
                        cfg_started = False
        except Exception as exc:  # pragma: no cover - board/port dependent
            if "cfg_started" in locals() and cfg_started:
                try:
                    self.log_line.emit("> CFG ABORT")
                    port.write(b"CFG ABORT\n")
                    port.flush()
                except Exception as abort_exc:
                    self.log_line.emit(f"warning: CFG ABORT failed: {abort_exc}")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()

    def _read_response(self, port: Any) -> str:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            raw = port.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith(f"@ACK {self.seq} ") or line.startswith(f"@RD {self.seq} "):
                return line
            if line.startswith(f"@NACK {self.seq} ") or line.startswith("@NACK 0 "):
                raise RuntimeError(line)
        raise TimeoutError("timed out waiting for PS acknowledgement")


class AfeReadWorker(QThread):
    log_line = Signal(str)
    readback = Signal(int, int)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(
        self,
        port_name: str,
        baud: int,
        timeout_s: float,
        reads: list[tuple[int, str, int]],
        seq: int,
    ) -> None:
        super().__init__()
        self.port_name = port_name
        self.baud = baud
        self.timeout_s = timeout_s
        self.reads = reads
        self.seq = seq

    def run(self) -> None:
        if serial is None:
            self.failed.emit("pyserial is required: pip install -r tools/requirements_gui.txt")
            return
        try:
            with serial.Serial(self.port_name, self.baud, timeout=0.1) as port:
                time.sleep(0.2)
                port.reset_input_buffer()
                self._send_expect_ack(port, f"CFG BEGIN {self.seq}", "CFG_BEGIN")
                cfg_started = True
                for row, reg_map, addr in self.reads:
                    line = f"AFE RD {reg_map} 0x{addr:02X}"
                    self.log_line.emit(f"> {line}")
                    port.write((line + "\n").encode("ascii"))
                    port.flush()
                    data = self._read_rd_response(port)
                    self.readback.emit(row, data)
                self._send_expect_ack(port, "CFG END", "CFG_END")
                cfg_started = False
        except Exception as exc:  # pragma: no cover - board/port dependent
            if "cfg_started" in locals() and cfg_started:
                try:
                    self.log_line.emit("> CFG ABORT")
                    port.write(b"CFG ABORT\n")
                    port.flush()
                except Exception as abort_exc:
                    self.log_line.emit(f"warning: CFG ABORT failed: {abort_exc}")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()

    def _send_expect_ack(self, port: Any, line: str, expected: str) -> None:
        self.log_line.emit(f"> {line}")
        port.write((line + "\n").encode("ascii"))
        port.flush()
        response = self._read_response(port)
        self.log_line.emit(f"< {response}")
        if not response.startswith(f"@ACK {self.seq} {expected}"):
            raise RuntimeError(f"unexpected response: {response}")

    def _read_rd_response(self, port: Any) -> int:
        response = self._read_response(port)
        self.log_line.emit(f"< {response}")
        parts = response.split()
        if len(parts) != 4 or parts[0] != "@RD":
            raise RuntimeError(f"unexpected read response: {response}")
        return int(parts[3], 0) & 0xFFFF

    def _read_response(self, port: Any) -> str:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            raw = port.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith(f"@ACK {self.seq} ") or line.startswith(f"@RD {self.seq} "):
                return line
            if line.startswith(f"@NACK {self.seq} ") or line.startswith("@NACK 0 "):
                raise RuntimeError(line)
        raise TimeoutError("timed out waiting for PS acknowledgement")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ultrasound Runtime Configuration")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(1440, 880)
        self.configs: dict[str, dict[str, Any]] = {}
        self.config_paths: dict[str, Path] = dict(DEFAULT_CONFIG_PATHS)
        self.current_target = "afe5832"
        self.rendering_table = False
        self.worker: SendWorker | None = None
        self.afe_read_worker: AfeReadWorker | None = None

        self._build_ui()
        self._load_defaults()
        self._refresh_ports()
        self._select_target("afe5832")

    def _build_ui(self) -> None:
        frame = QFrame()
        frame.setObjectName("windowFrame")
        self.setCentralWidget(frame)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(TitleBar(self))

        content = QWidget()
        content.setObjectName("contentArea")
        frame_layout.addWidget(content, 1)

        root = QHBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        self.target_list = QListWidget()
        self.target_list.setFixedWidth(190)
        self.target_list.setObjectName("targetList")
        self.target_list.setFocusPolicy(Qt.NoFocus)
        target_font = QFont()
        target_font.setBold(True)
        for target in TARGETS:
            item = QListWidgetItem(TARGET_TITLES[target])
            item.setData(Qt.UserRole, target)
            item.setFont(target_font)
            if target in ("delay_profile", "all"):
                item.setSizeHint(QSize(168, 58))
            else:
                item.setSizeHint(QSize(168, 44))
            self.target_list.addItem(item)
        self.target_list.currentItemChanged.connect(self._on_target_changed)
        root.addWidget(self.target_list)

        center = QVBoxLayout()
        root.addLayout(center, 1)

        self.title_label = QLabel()
        self.title_label.setObjectName("titleLabel")
        center.addWidget(self.title_label)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("summaryLabel")
        center.addWidget(self.summary_label)

        self.tabs = QTabWidget()
        center.addWidget(self.tabs, 1)

        self.plot = PlotWidget()
        self.heatmap = HeatmapWidget()
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dtgc_table = QTableWidget()
        self.dtgc_table.setAlternatingRowColors(True)
        self.dtgc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.commands_view = QPlainTextEdit()
        self.commands_view.setReadOnly(True)

        self.tabs.addTab(self.plot, "Preview")
        self.tabs.addTab(self.heatmap, "Heatmap")
        self.tabs.addTab(self.table, "Table")
        self.tabs.addTab(self.dtgc_table, "DTGC")
        self.tabs.addTab(self.commands_view, "Dry Run")

        right = QFrame()
        right.setObjectName("sidePanel")
        right.setFixedWidth(345)
        right_layout = QVBoxLayout(right)
        right_layout.setSpacing(12)
        root.addWidget(right)

        file_box = QGroupBox("Configuration")
        file_layout = QVBoxLayout(file_box)
        self.path_label = QLabel()
        self.path_label.setObjectName("pathLabel")
        self.path_label.setWordWrap(True)
        self.load_button = QPushButton("Load JSON")
        self.save_button = QPushButton("Save JSON")
        self.add_afe_row_button = QPushButton("Add AFE Row")
        self.delete_afe_row_button = QPushButton("Delete AFE Row")
        self.read_afe_rows_button = QPushButton("Read AFE Rows")
        self.validate_button = QPushButton("Validate")
        self.dry_run_button = QPushButton("Dry Run")
        for button in (
            self.load_button,
            self.save_button,
            self.add_afe_row_button,
            self.delete_afe_row_button,
            self.read_afe_rows_button,
            self.validate_button,
            self.dry_run_button,
        ):
            button.setObjectName("configButton")
        file_layout.addWidget(self.path_label)
        config_buttons = QGridLayout()
        config_buttons.setHorizontalSpacing(8)
        config_buttons.setVerticalSpacing(8)
        config_buttons.addWidget(self.load_button, 0, 0)
        config_buttons.addWidget(self.save_button, 0, 1)
        config_buttons.addWidget(self.add_afe_row_button, 1, 0)
        config_buttons.addWidget(self.delete_afe_row_button, 1, 1)
        config_buttons.addWidget(self.read_afe_rows_button, 2, 0, 1, 2)
        config_buttons.addWidget(self.validate_button, 3, 0)
        config_buttons.addWidget(self.dry_run_button, 3, 1)
        file_layout.addLayout(config_buttons)
        right_layout.addWidget(file_box)

        uart_box = QGroupBox("UART")
        uart_layout = QGridLayout(uart_box)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setFixedWidth(104)
        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.setFixedWidth(76)
        self.baud_combo = QComboBox()
        for baud in (9600, 115200, 460800, 921600):
            self.baud_combo.addItem(str(baud), baud)
        self.baud_combo.setCurrentText("460800")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(20)
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(1, 8)
        self.chunk_spin.setValue(8)
        uart_layout.addWidget(self._field_label("Port"), 0, 0)
        uart_layout.addWidget(self.port_combo, 0, 1)
        uart_layout.addWidget(self.refresh_ports_button, 0, 2)
        uart_layout.setHorizontalSpacing(12)
        uart_layout.addWidget(self._field_label("Baud"), 1, 0)
        uart_layout.addWidget(self.baud_combo, 1, 1, 1, 2)
        uart_layout.addWidget(self._field_label("Timeout"), 2, 0)
        uart_layout.addWidget(self.timeout_spin, 2, 1, 1, 2)
        uart_layout.addWidget(self._field_label("Chunk"), 3, 0)
        uart_layout.addWidget(self.chunk_spin, 3, 1, 1, 2)
        right_layout.addWidget(uart_box)

        self.send_button = QPushButton("Send Configuration")
        self.send_button.setObjectName("sendButton")
        right_layout.addWidget(self.send_button)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        right_layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1200)
        right_layout.addWidget(self.log_view, 1)

        self.load_button.clicked.connect(self._load_json_for_target)
        self.save_button.clicked.connect(self._save_current_json)
        self.add_afe_row_button.clicked.connect(self._add_afe_row)
        self.delete_afe_row_button.clicked.connect(self._delete_afe_rows)
        self.read_afe_rows_button.clicked.connect(self._read_afe_rows)
        self.validate_button.clicked.connect(self._validate_current)
        self.dry_run_button.clicked.connect(self._show_dry_run)
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        self.send_button.clicked.connect(self._send_current)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        label.setAlignment(Qt.AlignCenter)
        return label

    def _load_defaults(self) -> None:
        for target, path in DEFAULT_CONFIG_PATHS.items():
            try:
                self.configs[target] = self._read_json(path)
            except Exception as exc:
                self._log(f"Failed to load {path}: {exc}")

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText().strip()
        self.port_combo.clear()
        ports: list[str] = []
        if list_ports is not None:
            ports = [item.device for item in sorted(list_ports.comports(), key=lambda p: p.device)]
        if not ports:
            ports = [current or "COM6", "COM3"]
        for port in dict.fromkeys(ports):
            self.port_combo.addItem(port)

    def _on_target_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if previous is not None:
            try:
                previous_target = previous.data(Qt.UserRole)
                if previous_target is not None:
                    self._sync_table_to_config(previous_target)
            except Exception as exc:
                self._set_status(f"Table sync failed: {exc}", RED)
        if current is None:
            return
        self._select_target(current.data(Qt.UserRole))

    def _select_target(self, target: str) -> None:
        self.current_target = target
        matching = self.target_list.findItems(TARGET_TITLES[target], Qt.MatchExactly)
        if matching and self.target_list.currentItem() is not matching[0]:
            self.target_list.setCurrentItem(matching[0])
        self.title_label.setText(TARGET_TITLES[target])
        self.path_label.setText(self._path_text(target))
        self.path_label.setToolTip(str(self.config_paths.get(target, "")))
        is_afe = target == "afe5832"
        self.add_afe_row_button.setEnabled(is_afe)
        self.delete_afe_row_button.setEnabled(is_afe)
        self.read_afe_rows_button.setEnabled(is_afe)
        self._render_target(target)

    def _path_text(self, target: str) -> str:
        if target == "all":
            return "All loaded JSON files"
        return self.config_paths.get(target, Path("<not loaded>")).name

    def _load_json_for_target(self) -> None:
        target = self.current_target
        if target == "all":
            QMessageBox.information(self, "Load JSON", "Select a single target before loading a replacement JSON file.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Load {TARGET_TITLES[target]} JSON",
            str(self.config_paths.get(target, uart_cfg.SCRIPT_DIR)),
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            self.configs[target] = self._read_json(Path(path))
            self.config_paths[target] = Path(path)
            self._set_status(f"Loaded {TARGET_TITLES[target]}", GREEN)
            self._render_target(target)
        except Exception as exc:
            self._set_status(f"Load failed: {exc}", RED)
            QMessageBox.critical(self, "Load failed", str(exc))

    def _save_current_json(self) -> None:
        target = self.current_target
        if target == "all":
            QMessageBox.information(self, "Save JSON", "Switch to a single target before saving JSON.")
            return
        try:
            self._sync_table_to_config(target)
            self._write_config(target)
            self._set_status(f"Saved {self.config_paths[target].name}", GREEN)
            self._log(f"Saved {target}: {self.config_paths[target]}")
            self._render_target(target)
        except Exception as exc:
            self._set_status(f"Save failed: {exc}", RED)
            QMessageBox.critical(self, "Save failed", str(exc))

    def _write_config(self, target: str) -> None:
        if target not in self.config_paths:
            raise ValueError(f"No JSON path configured for {TARGET_TITLES[target]}")
        path = self.config_paths[target]
        path.write_text(json.dumps(self._require_config(target), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _sync_table_to_config(self, target: str) -> None:
        if self.rendering_table:
            return
        if target == "afe5832":
            self._sync_afe_table_to_config()
            self._sync_dtgc_table_to_config()
        elif target == "tx7332":
            self._sync_tx7332_table_to_config()

    def _sync_afe_table_to_config(self) -> None:
        rows = []
        for row in range(self.table.rowCount()):
            reg_map = self._table_text(row, 0).strip().upper()
            if not reg_map:
                continue
            if reg_map not in uart_cfg.VALID_MAPS:
                raise ValueError(f"AFE row {row + 1}: map must be one of {sorted(uart_cfg.VALID_MAPS)}")
            addr = parse_table_int(self._table_text(row, 1), 8, f"AFE row {row + 1} address")
            data = parse_table_int(self._table_text(row, 2), 16, f"AFE row {row + 1} data")
            rows.append({"map": reg_map, "addr": fmt_u8(addr), "data": fmt_u16(data)})

        cfg = self._require_config("afe5832")
        afe = cfg.setdefault("afe5832", {})
        if not isinstance(afe, dict):
            raise ValueError("afe5832 JSON node must be an object")
        afe["raw_registers"] = rows

    def _sync_dtgc_table_to_config(self) -> None:
        if self.dtgc_table.rowCount() == 0:
            return

        cfg = self._require_config("afe5832")
        afe = cfg.setdefault("afe5832", {})
        if not isinstance(afe, dict):
            raise ValueError("afe5832 JSON node must be an object")
        dtgc = afe.setdefault("dtgc", {})
        if not isinstance(dtgc, dict):
            raise ValueError("afe5832.dtgc must be an object")

        seen: set[str] = set()
        pending: dict[str, Any] = {}
        for row in range(self.dtgc_table.rowCount()):
            key = self._dtgc_table_text(row, 0).strip()
            if not key:
                continue
            if key not in DTGC_FIELD_ORDER:
                raise ValueError(f"DTGC row {row + 1}: unknown parameter {key!r}")
            text = self._dtgc_table_text(row, 1)
            if not text.strip():
                return
            if key in DTGC_FLOAT_FIELDS:
                pending[key] = parse_table_float(text, f"DTGC {key}")
            elif key in DTGC_INT_FIELDS:
                pending[key] = int(text.strip(), 0)
            seen.add(key)

        missing = [key for key in DTGC_FIELD_ORDER if key not in seen]
        if missing:
            raise ValueError(f"DTGC table is missing parameter(s): {', '.join(missing)}")
        dtgc.update(pending)
        self._sync_dtgc_gain_to_vca(afe, pending)

    def _sync_dtgc_gain_to_vca(self, afe: dict[str, Any], dtgc_updates: dict[str, Any]) -> None:
        gain_updates = {key: dtgc_updates[key] for key in DTGC_VCA_GAIN_FIELDS if key in dtgc_updates}
        if not gain_updates:
            return
        vca = afe.setdefault("vca", {})
        if not isinstance(vca, dict):
            raise ValueError("afe5832.vca must be an object")
        vca.update(gain_updates)

    def _sync_tx7332_table_to_config(self) -> None:
        if self.table.rowCount() != 64:
            raise ValueError(f"TX7332 table must contain 64 register rows, got {self.table.rowCount()}")
        words = []
        for row in range(self.table.rowCount()):
            addr = parse_table_int(self._table_text(row, 1), 32, f"TX7332 row {row + 1} register address")
            data = parse_table_int(self._table_text(row, 2), 32, f"TX7332 row {row + 1} register data")
            words.extend([fmt_word(addr), fmt_word(data)])

        old = self._require_config("tx7332")
        self.configs["tx7332"] = {
            "description": old.get("description", "TX7332 static register parameters edited by ultrasound_config_gui.py."),
            "tx7332": {"tx7332_config_data": words},
        }

    def _table_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return "" if item is None else item.text()

    def _dtgc_table_text(self, row: int, column: int) -> str:
        item = self.dtgc_table.item(row, column)
        return "" if item is None else item.text()

    def _add_afe_row(self) -> None:
        if self.current_target != "afe5832":
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        if self.table.columnCount() < 4:
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(("Map", "Address", "Data", "Readback"))
        for column, value in enumerate(("ADC", "0x00", "0x0000", "--")):
            self.table.setItem(row, column, make_table_item(value, column < 3))
        self.tabs.setCurrentWidget(self.table)
        self._set_status("Added editable AFE5832 register row", GREEN)

    def _delete_afe_rows(self) -> None:
        if self.current_target != "afe5832":
            return
        selected_rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            QMessageBox.information(self, "Delete AFE Row", "Select one or more AFE5832 table rows to delete.")
            return
        for row in selected_rows:
            self.table.removeRow(row)
        self._set_status(f"Deleted {len(selected_rows)} AFE5832 row(s)", ORANGE)

    def _read_afe_rows(self) -> None:
        if self.current_target != "afe5832":
            return
        if self.afe_read_worker is not None and self.afe_read_worker.isRunning():
            QMessageBox.warning(self, "Busy", "AFE5832 readback is already running.")
            return
        selected_rows = sorted({index.row() for index in self.table.selectedIndexes()})
        rows_to_read = selected_rows or list(range(self.table.rowCount()))
        reads: list[tuple[int, str, int]] = []
        try:
            for row in rows_to_read:
                reg_map = self._table_text(row, 0).strip().upper()
                if reg_map not in uart_cfg.VALID_MAPS:
                    raise ValueError(f"AFE row {row + 1}: map must be one of {sorted(uart_cfg.VALID_MAPS)}")
                addr = parse_table_int(self._table_text(row, 1), 8, f"AFE row {row + 1} address")
                reads.append((row, reg_map, addr))
        except Exception as exc:
            self._set_status(f"Readback setup failed: {exc}", RED)
            QMessageBox.critical(self, "Readback setup failed", str(exc))
            return
        if not reads:
            QMessageBox.information(self, "Read AFE Rows", "No AFE5832 rows to read.")
            return

        port_name = self.port_combo.currentText().strip()
        if not port_name:
            QMessageBox.warning(self, "UART", "Select or enter a serial port.")
            return

        for row, _, _ in reads:
            self._set_readback_cell(row, "--", MUTED)
        seq = int(time.time() * 1000) & 0xFFFFFFFF
        self.read_afe_rows_button.setEnabled(False)
        self._set_status(f"Reading {len(reads)} AFE5832 row(s)...", ORANGE)
        self.afe_read_worker = AfeReadWorker(
            port_name,
            int(self.baud_combo.currentData()),
            float(self.timeout_spin.value()),
            reads,
            seq,
        )
        self.afe_read_worker.log_line.connect(self._log)
        self.afe_read_worker.readback.connect(self._apply_afe_readback)
        self.afe_read_worker.finished_ok.connect(self._afe_read_finished)
        self.afe_read_worker.failed.connect(self._afe_read_failed)
        self.afe_read_worker.start()

    def _apply_afe_readback(self, row: int, data: int) -> None:
        expected = parse_table_int(self._table_text(row, 2), 16, f"AFE row {row + 1} data")
        color = GREEN if expected == data else ORANGE
        self._set_readback_cell(row, fmt_u16(data), color)

    def _set_readback_cell(self, row: int, text: str, color: QColor) -> None:
        if self.table.columnCount() < 4 or row >= self.table.rowCount():
            return
        item = make_table_item(text, False)
        item.setForeground(color)
        self.table.setItem(row, 3, item)

    def _afe_read_finished(self) -> None:
        self.read_afe_rows_button.setEnabled(self.current_target == "afe5832")
        self._set_status("AFE5832 readback completed", GREEN)
        self._log("PASS: AFE5832 readback completed")

    def _afe_read_failed(self, message: str) -> None:
        self.read_afe_rows_button.setEnabled(self.current_target == "afe5832")
        self._set_status(f"AFE readback failed: {message}", RED)
        self._log(f"ERROR: {message}")
        QMessageBox.critical(self, "AFE readback failed", message)

    def _validate_current(self) -> None:
        try:
            self._sync_table_to_config(self.current_target)
            commands, _ = self._build_commands(self.current_target)
            self._set_status(f"Valid: {len(commands)} UART lines", GREEN)
            self._log(f"Validated {self.current_target}: {len(commands)} UART lines")
        except Exception as exc:
            self._set_status(f"Invalid: {exc}", RED)
            QMessageBox.critical(self, "Validation failed", str(exc))

    def _show_dry_run(self) -> None:
        try:
            self._sync_table_to_config(self.current_target)
            commands, _ = self._build_commands(self.current_target)
            self.commands_view.setPlainText("\n".join(commands))
            self.tabs.setCurrentWidget(self.commands_view)
            self._set_status(f"Dry-run ready: {len(commands)} lines", GREEN)
        except Exception as exc:
            self._set_status(f"Dry-run failed: {exc}", RED)
            QMessageBox.critical(self, "Dry-run failed", str(exc))

    def _send_current(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "Busy", "A UART transaction is already running.")
            return
        try:
            self._save_before_send(self.current_target)
            commands, seq = self._build_commands(self.current_target)
        except Exception as exc:
            self._set_status(f"Invalid: {exc}", RED)
            QMessageBox.critical(self, "Validation failed", str(exc))
            return

        port_name = self.port_combo.currentText().strip()
        if not port_name:
            QMessageBox.warning(self, "UART", "Select or enter a serial port.")
            return

        self.send_button.setEnabled(False)
        self._set_status("Sending configuration...", ORANGE)
        self.worker = SendWorker(port_name, int(self.baud_combo.currentData()), float(self.timeout_spin.value()), commands, seq)
        self.worker.log_line.connect(self._log)
        self.worker.finished_ok.connect(self._send_finished)
        self.worker.failed.connect(self._send_failed)
        self.worker.start()

    def _save_before_send(self, target: str) -> None:
        self._sync_table_to_config(target)
        if target in ("afe5832", "tx7332"):
            self._write_config(target)
            self._log(f"Saved latest {target} JSON before sending")
        elif target == "all":
            self._write_config("afe5832")
            self._write_config("tx7332")
            self._log("Saved latest AFE5832/TX7332 JSON before all-target send")

    def _send_finished(self) -> None:
        self.send_button.setEnabled(True)
        self._set_status("Configuration sent successfully", GREEN)
        self._log("PASS: UART transaction completed")

    def _send_failed(self, message: str) -> None:
        self.send_button.setEnabled(True)
        self._set_status(f"Send failed: {message}", RED)
        self._log(f"ERROR: {message}")
        QMessageBox.critical(self, "Send failed", message)

    def _build_commands(self, target: str) -> tuple[list[str], int]:
        seq = int(time.time() * 1000) & 0xFFFFFFFF
        chunk_words = self.chunk_spin.value()
        commands = [f"CFG BEGIN {seq}"]

        if target in BRAM_TARGETS:
            commands.extend(uart_cfg.build_bram_commands(self._require_config(target), chunk_words))
        elif target == "all":
            for bram_target in BRAM_TARGETS:
                commands.extend(uart_cfg.build_bram_commands(self._require_config(bram_target), chunk_words))

        if target in ("tx7332", "all"):
            commands.extend(uart_cfg.build_tx7332_commands(self._require_config("tx7332"), chunk_words))

        if target in ("afe5832", "all"):
            commands.extend(uart_cfg.build_afe_commands(self._require_config("afe5832")))

        commands.append("CFG END")
        return commands, seq

    def _require_config(self, target: str) -> dict[str, Any]:
        if target not in self.configs:
            raise ValueError(f"{TARGET_TITLES[target]} JSON is not loaded")
        return self.configs[target]

    def _render_target(self, target: str) -> None:
        self.path_label.setText(self._path_text(target))
        self.path_label.setToolTip(str(self.config_paths.get(target, "")))
        try:
            if target != "afe5832":
                self._clear_dtgc_table()
            if target == "all":
                self._render_all()
            elif target == "afe5832":
                self._render_afe()
            elif target == "tx7332":
                self._render_tx7332()
            elif target == "demod_coeffs":
                self._render_demod()
            elif target == "delay_profile":
                self._render_delay_profile()
            elif target == "t_timing":
                self._render_t_timing()
            else:
                self._render_single_bram(target)
            self._sync_table_to_config(target)
            commands, _ = self._build_commands(target)
            self.commands_view.setPlainText("\n".join(commands))
        except Exception as exc:
            self.summary_label.setText(f"Render failed: {exc}")
            self.plot.set_series([])
            self.heatmap.set_values([])
            self.table.setRowCount(0)

    def _render_all(self) -> None:
        rows = []
        for target in TARGETS:
            if target == "all":
                continue
            rows.append((TARGET_TITLES[target], self._target_summary(target)))
        self.summary_label.setText("Combined transaction using all loaded JSON files.")
        self.plot.set_series([])
        self.heatmap.set_values([])
        self._fill_table(("Target", "Summary"), rows)

    def _target_summary(self, target: str) -> str:
        try:
            commands, _ = self._build_commands(target)
            return f"{len(commands)} UART lines"
        except Exception as exc:
            return f"invalid: {exc}"

    def _render_afe(self) -> None:
        rows = [(*row, "--") for row in self._afe_rows_for_display()]
        self.summary_label.setText(
            f"AFE5832 configuration: {len(rows)} register writes. "
            "Table edits only update afe5832.raw_registers; DTGC parameters are edited in the DTGC tab."
        )
        self.plot.set_series([])
        self.heatmap.set_values([])
        self._fill_table(("Map", "Address", "Data", "Readback"), rows, editable_columns={0, 1, 2})
        self._fill_dtgc_table()

    def _afe_rows_for_display(self) -> list[tuple[str, str, str]]:
        config = self._require_config("afe5832")
        afe = config.get("afe5832", {})
        raw_registers = afe.get("raw_registers", []) if isinstance(afe, dict) else []
        if not isinstance(raw_registers, list):
            raise ValueError("afe5832.raw_registers must be a list")
        rows = []
        for reg in raw_registers:
            reg_map = str(reg.get("map", "ADC")).upper()
            addr = parse_table_int(str(reg.get("addr", "0")), 8, "AFE raw address")
            data = parse_table_int(str(reg.get("data", "0")), 16, "AFE raw data")
            rows.append((reg_map, fmt_u8(addr), fmt_u16(data)))
        return rows

    def _afe_has_parameterized_dtgc(self) -> bool:
        afe = self._require_config("afe5832").get("afe5832", {})
        return uart_cfg.has_dtgc_calc_params(afe.get("dtgc"))

    def _fill_dtgc_table(self) -> None:
        config = self._require_config("afe5832")
        afe = config.get("afe5832", {})
        dtgc = afe.get("dtgc", {}) if isinstance(afe, dict) else {}
        if not isinstance(dtgc, dict):
            raise ValueError("afe5832.dtgc must be an object")

        self.rendering_table = True
        self.dtgc_table.clear()
        self.dtgc_table.setColumnCount(3)
        self.dtgc_table.setHorizontalHeaderLabels(("Parameter", "Value", "Type"))
        self.dtgc_table.setRowCount(len(DTGC_FIELD_ORDER))
        for row, key in enumerate(DTGC_FIELD_ORDER):
            value = dtgc.get(key, "")
            field_type = "float" if key in DTGC_FLOAT_FIELDS else "int"
            self.dtgc_table.setItem(row, 0, make_table_item(key, False))
            self.dtgc_table.setItem(row, 1, make_table_item(value, True))
            self.dtgc_table.setItem(row, 2, make_table_item(field_type, False))
        self.dtgc_table.resizeColumnsToContents()
        self.dtgc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.rendering_table = False

    def _clear_dtgc_table(self) -> None:
        self.dtgc_table.clear()
        self.dtgc_table.setColumnCount(0)
        self.dtgc_table.setRowCount(0)

    def _render_tx7332(self) -> None:
        cfg = self._require_config("tx7332")
        words = [parse_word(value) for value in cfg.get("tx7332", {}).get("tx7332_config_data", [])]
        self.summary_label.setText(f"TX7332 static configuration: {len(words)} words.")
        self.plot.set_series([])
        self.heatmap.set_values(words, 32)
        rows = []
        for index in range(0, len(words), 2):
            rows.append((index // 2, fmt_word(words[index]), fmt_word(words[index + 1])))
        self._fill_table(("Index", "Register Address", "Register Data"), rows, editable_columns={1, 2})

    def _render_delay_profile(self) -> None:
        words = words_from_section(self._require_config("delay_profile"), "delay_profile")
        addr_count = sum(1 for word in words[0::2] if word <= 0xFF)
        self.summary_label.setText(
            f"Delay profile: {len(words)} packed words. Preview shows 64 floating-point delay lines before quantization. "
            f"Potential register-address entries: {addr_count}."
        )
        delay_lines = delay_profile_float_lines()
        series = []
        for index, line in enumerate(delay_lines):
            hue = index / max(1, len(delay_lines) - 1)
            color = QColor.fromHsvF(0.52 + 0.22 * hue, 0.86, 0.95)
            series.append((f"L{index:02d}", line, color))
        self.plot.set_series(series)
        self.heatmap.set_values(words, 128)
        self._fill_word_table(words)

    def _render_demod(self) -> None:
        cfg = self._require_config("demod_coeffs")
        sin_words = words_from_section(cfg, "fdemod_sin")
        cos_words = words_from_section(cfg, "fdemod_cos")
        self.summary_label.setText(f"Demodulation: sin={len(sin_words)} words, cos={len(cos_words)} words.")
        sin_coef, cos_coef = demod_float_preview(512)
        self.plot.set_series(
            [
                ("sin_coef", sin_coef, ACCENT),
                ("cos_coef", cos_coef, ORANGE),
            ]
        )
        self.heatmap.set_values(sin_words + cos_words, 128)
        rows = []
        for index in range(min(256, len(sin_words), len(cos_words))):
            rows.append((index, f"0x{sin_words[index]:08X}", f"0x{cos_words[index]:08X}"))
        self._fill_table(("Index", "fdemod_sin", "fdemod_cos"), rows)

    def _render_t_timing(self) -> None:
        words = words_from_section(self._require_config("t_timing"), "t_timing")
        labels = ("rtc_dot", "rtc_period", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "rtc_down")
        rows = [(labels[index] if index < len(labels) else index, index, f"0x{word:08X}", word) for index, word in enumerate(words)]
        self.summary_label.setText("Beamforming scan timing control words.")
        self.plot.set_series([])
        self.heatmap.set_values(words, 10)
        self._fill_table(("Name", "Index", "Hex", "Decimal"), rows)

    def _render_single_bram(self, target: str) -> None:
        section = {
            "log_table": "log_table",
            "x_element": "x_element",
            "hamming": "hamming",
            "dfilter": "dfilter",
            "sin_beta": "sin_beta",
        }[target]
        words = words_from_section(self._require_config(target), section)
        plot_values = self._float_preview_for_section(section)
        self.summary_label.setText(f"{TARGET_TITLES[target]}: {len(words)} words in bram.{section}.")
        self.plot.set_series([(section, sample_series(plot_values), ACCENT)])
        self.heatmap.set_values(words, 64)
        self._fill_word_table(words)

    def _float_preview_for_section(self, section: str) -> list[float]:
        if section == "log_table":
            return log_table_float_values()
        if section == "x_element":
            return x_element_float_values()
        if section == "hamming":
            return hamming_values(64)
        if section == "dfilter":
            return lowpass_fir1_coeffs(63, 1.5e6 / (25e6 / 2))
        if section == "sin_beta":
            return sin_beta_float_values()
        return []

    def _fill_word_table(self, words: list[int], limit: int = 512) -> None:
        rows = [(index, f"0x{word:08X}", word) for index, word in enumerate(words[:limit])]
        if len(words) > limit:
            rows.append(("...", f"{len(words) - limit} more words", ""))
        self._fill_table(("Index", "Hex", "Decimal"), rows)

    def _fill_table(
        self,
        headers: tuple[str, ...],
        rows: list[tuple[Any, ...]],
        editable_columns: set[int] | None = None,
    ) -> None:
        editable_columns = editable_columns or set()
        self.rendering_table = True
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                self.table.setItem(row_idx, col_idx, make_table_item(value, col_idx in editable_columns))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.rendering_table = False

    def _set_status(self, message: str, color: QColor) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color.name()};")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")


def apply_dark_theme(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget {
            background: #071018;
            color: #d8e7f2;
            font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 10.5pt;
        }
        QFrame#windowFrame {
            background: #071018;
            border: 1px solid #1b3446;
            border-radius: 14px;
        }
        QWidget#contentArea {
            background: transparent;
            border-bottom-left-radius: 14px;
            border-bottom-right-radius: 14px;
        }
        QWidget#titleBar {
            background: #0b1118;
            border-top-left-radius: 14px;
            border-top-right-radius: 14px;
            border-bottom: 1px solid #1d2b3a;
        }
        QLabel#appTitle {
            color: #d8e7f2;
            font-size: 11pt;
            font-weight: 700;
        }
        QLabel#appSubtitle {
            color: #6f879b;
            font-size: 9.5pt;
        }
        QPushButton#titleButton, QPushButton#closeButton {
            background: transparent;
            border: 0;
            border-radius: 6px;
            color: #9fb6c8;
            font-size: 13pt;
            padding: 0;
        }
        QPushButton#titleButton:hover {
            background: #1b2a38;
            color: #d8e7f2;
        }
        QPushButton#closeButton:hover {
            background: #c42b1c;
            color: #ffffff;
        }
        QListWidget#targetList, QFrame#sidePanel, QGroupBox, QTabWidget::pane,
        QPlainTextEdit, QTableWidget {
            background: #101b26;
            border: 1px solid #203244;
            border-radius: 10px;
        }
        QGroupBox {
            margin-top: 20px;
            padding: 18px 10px 10px 10px;
            font-weight: 700;
            color: #20d6e8;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 8px;
            left: 10px;
        }
        QListWidget#targetList::item {
            padding: 9px 12px;
            border-radius: 11px;
            margin: 4px 7px;
            background: #0d1822;
            border-left: 3px solid transparent;
            border-top: 1px solid #172839;
            border-right: 1px solid #172839;
            border-bottom: 1px solid #172839;
            outline: 0;
            font-weight: 700;
        }
        QListWidget#targetList::item:hover {
            background: #132434;
            border-top: 1px solid #27445b;
            border-right: 1px solid #27445b;
            border-bottom: 1px solid #27445b;
        }
        QListWidget#targetList::item:selected {
            background: #123447;
            color: #20d6e8;
            border-left: 3px solid #20d6e8;
            border-top: 1px solid #24566a;
            border-right: 1px solid #24566a;
            border-bottom: 1px solid #24566a;
            font-weight: 700;
        }
        QListWidget#targetList::item:focus {
            outline: none;
        }
        QLabel#titleLabel {
            color: #20d6e8;
            font-size: 22pt;
            font-weight: 700;
        }
        QLabel#summaryLabel {
            color: #9fc4d8;
            padding: 8px 10px;
            background: #0d1822;
            border: 1px solid #203244;
            border-radius: 10px;
        }
        QLabel#pathLabel {
            color: #d8e7f2;
            font-weight: 700;
            padding: 6px 8px;
            background: #0d1822;
            border: 1px solid #203244;
            border-radius: 7px;
        }
        QLabel#fieldLabel {
            color: #9fc4d8;
            background: #0b1520;
            border: 1px solid #203244;
            border-radius: 10px;
            padding: 5px 8px;
            font-weight: 700;
        }
        QPushButton {
            background: #142636;
            border: 1px solid #27445b;
            border-radius: 8px;
            padding: 8px 10px;
            font-weight: 700;
        }
        QPushButton#configButton {
            font-weight: 400;
        }
        QPushButton:hover {
            border-color: #20d6e8;
            color: #20d6e8;
        }
        QPushButton#sendButton {
            background: #0f3b4a;
            border-color: #20d6e8;
            color: #e8fbff;
            font-weight: 700;
        }
        QComboBox, QSpinBox {
            background: #0d1822;
            border: 1px solid #27445b;
            border-radius: 7px;
            padding: 5px;
        }
        QHeaderView::section {
            background: #142636;
            color: #d8e7f2;
            border: 0;
            padding: 6px;
        }
        QTableWidget {
            gridline-color: #203244;
            alternate-background-color: #0b1520;
        }
        QTabBar::tab {
            background: #101b26;
            color: #9fc4d8;
            padding: 8px 14px;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
        }
        QTabBar::tab:selected {
            background: #12384a;
            color: #20d6e8;
        }
        """
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ultrasound Configuration GUI")
    app.setFont(QFont("Segoe UI", 10))
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
