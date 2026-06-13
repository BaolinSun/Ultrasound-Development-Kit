import sys
import json

import numpy as np
from pathlib import Path
from PySide6.QtCore import QPoint, QProcess, QRect, Signal, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
try:
    from scipy.interpolate import PchipInterpolator
except ImportError:
    PchipInterpolator = None

from ultrasound_pipeline_adapter import UltrasoundPipeline


PS_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
PS_CONFIG_GUI = PS_TOOLS_DIR / "ultrasound_config_gui.py"
PS_UART_TOOL = PS_TOOLS_DIR / "ultrasound_config_uart.py"
PS_USB_TOOL = PS_TOOLS_DIR / "ultrasound_config_usb.py"
TGC_MIN_DB = -24.0
TGC_MAX_DB = 24.0
PS_UART_TARGETS = [
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
]


def get_app_icon():
    assets_dir = Path(__file__).resolve().parent / "assets"
    for icon_path in (assets_dir / "ultravision_icon.ico", assets_dir / "ultravision_icon.svg"):
        if icon_path.exists():
            return QIcon(str(icon_path))
    return QIcon()


class WindowTitleBar(QFrame):
    """VS Code-style custom title bar with basic window controls."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._drag_pos = None
        self.setObjectName("AppTitleBar")
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)

        icon_label = QLabel()
        icon = get_app_icon()
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(18, 18))
        icon_label.setFixedSize(20, 20)

        title = QLabel("UltraVision Workstation")
        title.setObjectName("TitleBarText")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        left_cluster = QWidget()
        left_cluster.setObjectName("TitleBarLeftCluster")
        left_layout = QHBoxLayout(left_cluster)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(icon_label)
        left_layout.addWidget(title)

        menu_items = []
        for item in ("File", "View", "Acquisition", "Tools", "Help"):
            button = QPushButton(item)
            button.setObjectName("TitleBarMenuButton")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setMinimumWidth(42)
            button.setFixedHeight(28)
            menu_items.append(button)
            left_layout.addWidget(button)

        self.center_title = QLabel("UltraVision Workstation - B-mode")
        self.center_title.setObjectName("TitleBarCenterText")
        self.center_title.setParent(self)
        self.center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.fullscreen_button = QPushButton("⛶")
        self.min_button = QPushButton("─")
        self.max_button = QPushButton("□")
        self.close_button = QPushButton("×")
        for button in (self.fullscreen_button, self.min_button, self.max_button, self.close_button):
            button.setObjectName("TitleBarButton")
            button.setFixedSize(46, 34)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fullscreen_button.setToolTip("Full screen (F11)")
        self.min_button.setToolTip("Minimize")
        self.max_button.setToolTip("Maximize to work area")
        self.close_button.setToolTip("Close")
        self.close_button.setObjectName("TitleBarCloseButton")

        self.fullscreen_button.clicked.connect(window.toggle_fullscreen)
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button.clicked.connect(window.toggle_custom_maximized)
        self.close_button.clicked.connect(window.close)

        layout.addWidget(left_cluster)
        layout.addStretch(1)
        layout.addWidget(self.fullscreen_button)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

    def set_mode(self, mode):
        self.center_title.setText(f"UltraVision Workstation - {mode}")

    def set_maximized_state(self, maximized):
        self.max_button.setText("❐" if maximized else "□")

    def set_fullscreen_state(self, fullscreen):
        self.fullscreen_button.setText("↙" if fullscreen else "⛶")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if getattr(self.window, "_custom_maximized", False):
                self.window.restore_from_custom_maximized_for_drag()
                self._drag_pos = QPoint(self.window.width() // 2, 20)
            self.window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window.toggle_custom_maximized()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        title_width = max(300, min(460, int(self.width() * 0.25)))
        title_height = 28
        self.center_title.setGeometry(
            (self.width() - title_width) // 2,
            (self.height() - title_height) // 2,
            title_width,
            title_height,
        )
        self.center_title.raise_()


class UltrasoundImageWidget(QWidget):
    """Aspect-ratio preserving display for grayscale or RGB/BGR ultrasound frames."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.overlay_state = {
            "mode": "B-mode",
            "frequency_mhz": 2.5,
            "dynamic_range_db": 30,
            "gain_db": 0,
            "fps": 0.0,
            "mi": 0.8,
            "tis": 0.2,
            "probe": "Unknown",
            "depth_mm": 126.1,
            "sampling_rate_mhz": 25.0,
            "focus_mm": 60.0,
        }
        self.setMinimumSize(420, 320)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_overlay_state(self, **state):
        self.overlay_state.update(state)
        self.update()

    def set_frame(self, frame):
        if frame is None:
            return

        array = np.asarray(frame)
        if array.ndim == 2:
            array = np.ascontiguousarray(array.astype(np.uint8, copy=False))
            height, width = array.shape
            qimage = QImage(
                array.data,
                width,
                height,
                array.strides[0],
                QImage.Format.Format_Grayscale8,
            ).copy()
        elif array.ndim == 3 and array.shape[2] == 3:
            # OpenCV-style frames are BGR; convert to RGB before handing them to Qt.
            array = np.ascontiguousarray(array[:, :, ::-1].astype(np.uint8, copy=False))
            height, width, _ = array.shape
            qimage = QImage(
                array.data,
                width,
                height,
                array.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
        else:
            return

        self._pixmap = QPixmap.fromImage(qimage)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#02060b"))

        border_pen = QPen(QColor("#24415d"))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        inner_pen = QPen(QColor("#0f3046"))
        inner_pen.setWidth(1)
        painter.setPen(inner_pen)
        painter.drawRoundedRect(self.rect().adjusted(4, 4, -5, -5), 5, 5)

        if self._pixmap.isNull():
            painter.setPen(QColor("#6f8598"))
            painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Medium))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image stream")
            return

        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        image_rect = QRect(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(image_rect, scaled)
        self._paint_overlay(painter, image_rect)

    def _paint_overlay(self, painter, image_rect):
        if image_rect.width() < 120 or image_rect.height() < 120:
            return

        accent = QColor("#45e6ff")
        muted = QColor("#8ca8bd")
        panel = QColor(3, 10, 18, 168)

        glow_pen = QPen(QColor(34, 211, 238, 95))
        glow_pen.setWidth(2)
        painter.setPen(glow_pen)
        painter.drawRoundedRect(image_rect.adjusted(3, 3, -4, -4), 5, 5)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        top_text = (
            f"{self.overlay_state['mode']} | "
            f"{self.overlay_state['frequency_mhz']:.1f} MHz | "
            f"MI {self.overlay_state['mi']:.1f} | "
            f"TIS {self.overlay_state['tis']:.1f} | "
            f"FPS {self.overlay_state['fps']:.1f}"
        )
        top_rect = QRect(image_rect.left() + 12, image_rect.top() + 10, image_rect.width() - 24, 26)
        painter.fillRect(top_rect, panel)
        painter.setPen(accent)
        painter.drawText(top_rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, top_text)

        depth_mm = max(1.0, float(self.overlay_state["depth_mm"]))
        tick_step = 20
        tick_x = image_rect.left() + 14
        painter.setFont(QFont("Segoe UI", 9))
        for depth in range(tick_step, int(depth_mm) + 1, tick_step):
            y = image_rect.top() + int(image_rect.height() * depth / depth_mm)
            if y >= image_rect.bottom() - 8:
                continue
            painter.setPen(QPen(QColor(69, 230, 255, 145), 1))
            painter.drawLine(tick_x, y, tick_x + 20, y)
            painter.setPen(muted)
            painter.drawText(tick_x + 26, y - 8, 54, 16, Qt.AlignmentFlag.AlignLeft, f"{depth} mm")

        focus_mm = min(depth_mm, max(0.0, float(self.overlay_state["focus_mm"])))
        focus_y = image_rect.top() + int(image_rect.height() * focus_mm / depth_mm)
        focus_x = image_rect.right() - 20
        marker = QPolygon(
            [
                QPoint(focus_x, focus_y),
                QPoint(focus_x + 13, focus_y - 8),
                QPoint(focus_x + 13, focus_y + 8),
            ]
        )
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(marker)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(accent)
        painter.drawText(focus_x - 70, focus_y - 9, 48, 18, Qt.AlignmentFlag.AlignRight, "FOCUS")

        info_w = 150
        info_h = 124
        info_rect = QRect(
            image_rect.right() - 170,
            image_rect.top() + 40,
            info_w,
            info_h,
        )
        painter.fillRect(info_rect, panel)
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#d8e6f3"))
        info_lines = [
            f"Probe: {self.overlay_state['probe']}",
            f"TX frequency: {self.overlay_state['frequency_mhz']:.1f} MHz",
            f"Depth: {self.overlay_state['depth_mm']:.1f} mm",
            f"Sampling: {self.overlay_state['sampling_rate_mhz']:.1f} MHz",
            f"Contrast Gain: {self.overlay_state['gain_db']:.2f}x",
            f"Dynamic Range: {int(self.overlay_state['dynamic_range_db'])} dB",
            f"FPS: {self.overlay_state['fps']:.1f}",
        ]
        for idx, line in enumerate(info_lines):
            painter.drawText(info_rect.adjusted(10, 8 + idx * 16, -10, 0), Qt.AlignmentFlag.AlignLeft, line)


class GraymapCurveWidget(QWidget):
    """Interactive 9-point graymap curve editor producing a 256-entry LUT."""

    lut_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl_x = np.linspace(0, 255, 9, dtype=np.float32)
        self.ctrl_y = np.linspace(0, 255, 9, dtype=np.float32)
        self.lut = np.arange(256, dtype=np.uint8)
        self._drag_index = None
        self.setMinimumHeight(180)
        self.setMouseTracking(True)

    def reset_linear(self):
        self.ctrl_x = np.linspace(0, 255, 9, dtype=np.float32)
        self.ctrl_y = np.linspace(0, 255, 9, dtype=np.float32)
        self._update_lut()

    def _plot_rect(self):
        return self.rect().adjusted(34, 18, -14, -30)

    def _point_to_pixel(self, x, y):
        rect = self._plot_rect()
        px = rect.left() + int((float(x) / 255.0) * rect.width())
        py = rect.bottom() - int((float(y) / 255.0) * rect.height())
        return QPoint(px, py)

    def _pixel_to_point(self, pos):
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / max(1, rect.width()) * 255.0
        y = (rect.bottom() - pos.y()) / max(1, rect.height()) * 255.0
        return float(np.clip(x, 0, 255)), float(np.clip(y, 0, 255))

    def _update_lut(self):
        order = np.argsort(self.ctrl_x)
        xs = self.ctrl_x[order]
        ys = self.ctrl_y[order]
        xq = np.arange(256, dtype=np.float32)
        if PchipInterpolator is not None:
            lut = PchipInterpolator(xs, ys, extrapolate=True)(xq)
        else:
            lut = np.interp(xq, xs, ys)
        self.lut = np.clip(np.round(lut), 0, 255).astype(np.uint8)
        self.lut_changed.emit(self.lut.copy())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b1725"))
        rect = self._plot_rect()

        painter.setPen(QPen(QColor("#20354d"), 1))
        painter.drawRect(rect)
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#7892aa"))
        painter.drawText(rect.left(), rect.bottom() + 20, "Input Gray")
        painter.save()
        painter.translate(12, rect.center().y() + 28)
        painter.rotate(-90)
        painter.drawText(0, 0, "Output Gray")
        painter.restore()

        tick_values = [0, 64, 128, 192, 255]
        painter.setFont(QFont("Segoe UI", 7))
        for value in tick_values:
            x = rect.left() + int(value / 255.0 * rect.width())
            y = rect.bottom() - int(value / 255.0 * rect.height())
            painter.setPen(QPen(QColor("#173049"), 1))
            painter.drawLine(x, rect.top(), x, rect.bottom())
            painter.drawLine(rect.left(), y, rect.right(), y)
            painter.setPen(QColor("#7892aa"))
            painter.drawLine(x, rect.bottom(), x, rect.bottom() + 4)
            painter.drawLine(rect.left() - 4, y, rect.left(), y)
            painter.drawText(x - 12, rect.bottom() + 16, 26, 12, Qt.AlignmentFlag.AlignCenter, str(value))
            painter.drawText(rect.left() - 32, y - 6, 26, 12, Qt.AlignmentFlag.AlignRight, str(value))

        points = [self._point_to_pixel(x, y) for x, y in zip(np.arange(256), self.lut)]
        painter.setPen(QPen(QColor("#45e6ff"), 2))
        if len(points) > 1:
            painter.drawPolyline(points)

        painter.setBrush(QColor("#67e8f9"))
        painter.setPen(QPen(QColor("#e6fbff"), 1))
        for x, y in zip(self.ctrl_x, self.ctrl_y):
            pt = self._point_to_pixel(x, y)
            painter.drawEllipse(pt, 5, 5)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        distances = []
        for x, y in zip(self.ctrl_x, self.ctrl_y):
            pt = self._point_to_pixel(x, y)
            distances.append((pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2)
        index = int(np.argmin(distances))
        if distances[index] <= 18 ** 2:
            self._drag_index = index

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        x, y = self._pixel_to_point(event.position().toPoint())
        if 0 < self._drag_index < len(self.ctrl_x) - 1:
            x = np.clip(x, self.ctrl_x[self._drag_index - 1] + 1, self.ctrl_x[self._drag_index + 1] - 1)
            self.ctrl_x[self._drag_index] = x
        self.ctrl_y[self._drag_index] = y
        self._update_lut()

    def mouseReleaseEvent(self, event):
        self._drag_index = None


class TgcCurveWidget(QWidget):
    """Interactive TGC curve editor storing dB points and emitting linear gain."""

    tgc_changed = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.num_samples = 6144
        self.min_db = TGC_MIN_DB
        self.max_db = TGC_MAX_DB
        self.ctrl_points = self.default_control_points()
        self.tgc_db_curve = self._make_db_curve()
        self.tgc_gain = self._make_gain()
        self._drag_index = None
        self._selected_index = None
        self.setMinimumHeight(180)
        self.setMouseTracking(True)

    def default_control_points(self):
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
                [6143, 0.0],
            ],
            dtype=np.float32,
        )

    def reset_default(self):
        self.ctrl_points = self.default_control_points()
        self._selected_index = None
        self._update_gain()

    def set_control_points(self, control_points, min_db=TGC_MIN_DB, max_db=TGC_MAX_DB, num_samples=6144):
        self.num_samples = int(num_samples)
        self.min_db = TGC_MIN_DB
        self.max_db = TGC_MAX_DB
        points = np.asarray(control_points, dtype=np.float32)
        if points.ndim != 2 or points.shape[1] != 2 or points.shape[0] < 2:
            raise ValueError("TGC control_points must be an Nx2 array with at least 2 points")
        points[:, 0] = np.clip(points[:, 0], 0, self.num_samples - 1)
        points[:, 1] = np.clip(points[:, 1], self.min_db, self.max_db)
        points = points[np.argsort(points[:, 0])]
        points[0, 0] = 0.0
        points[-1, 0] = float(self.num_samples - 1)
        for idx in range(1, len(points)):
            if points[idx, 0] <= points[idx - 1, 0]:
                points[idx, 0] = min(float(self.num_samples - 1), points[idx - 1, 0] + 1.0)
        points[-1, 0] = float(self.num_samples - 1)
        self.ctrl_points = points
        self._selected_index = None
        self._update_gain()

    def to_json_data(self):
        return {
            "num_samples": int(self.num_samples),
            "unit": "dB",
            "min_db": float(self.min_db),
            "max_db": float(self.max_db),
            "control_points": [[float(x), float(y)] for x, y in self.ctrl_points],
        }

    def selected_point_text(self):
        if self._selected_index is None:
            return f"{len(self.ctrl_points)} control points"
        x, y = self.ctrl_points[self._selected_index]
        return f"Point {self._selected_index + 1}: z={int(round(x))}, TGC={y:+.1f} dB"

    def _plot_rect(self):
        return self.rect().adjusted(42, 18, -14, -30)

    def _make_db_curve(self):
        xq = np.arange(self.num_samples, dtype=np.float32)
        return np.interp(xq, self.ctrl_points[:, 0], self.ctrl_points[:, 1]).astype(np.float32)

    def _make_gain(self):
        return np.power(10.0, self.tgc_db_curve / 20.0).astype(np.float32)

    def _update_gain(self):
        self.tgc_db_curve = self._make_db_curve()
        self.tgc_gain = self._make_gain()
        self.tgc_changed.emit(self.tgc_gain.copy(), self.ctrl_points.copy())
        self.update()

    def peak_summary_text(self):
        peak_db = float(np.max(self.tgc_db_curve))
        peak_gain = float(np.max(self.tgc_gain))
        return f"TGC range: {self.min_db:.1f} to {self.max_db:+.1f} dB    Peak gain: {peak_db:+.1f} dB / {peak_gain:.2f}x"

    def _point_to_pixel(self, x, y):
        rect = self._plot_rect()
        px = rect.left() + int((float(x) / max(1, self.num_samples - 1)) * rect.width())
        span = max(1e-6, self.max_db - self.min_db)
        py = rect.bottom() - int(((float(y) - self.min_db) / span) * rect.height())
        return QPoint(px, py)

    def _pixel_to_point(self, pos):
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / max(1, rect.width()) * (self.num_samples - 1)
        y = self.min_db + (rect.bottom() - pos.y()) / max(1, rect.height()) * (self.max_db - self.min_db)
        return float(np.clip(x, 0, self.num_samples - 1)), float(np.clip(y, self.min_db, self.max_db))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b1725"))
        rect = self._plot_rect()

        painter.setPen(QPen(QColor("#20354d"), 1))
        painter.drawRect(rect)
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#7892aa"))
        painter.drawText(rect.left(), rect.bottom() + 20, "Depth Sample")
        painter.save()
        painter.translate(12, rect.center().y() + 18)
        painter.rotate(-90)
        painter.drawText(0, 0, "TGC (dB)")
        painter.restore()

        x_ticks = [0, 1024, 2048, 3072, 4096, 5120, 6143]
        y_ticks = [-24, -12, 0, 12, 24]
        painter.setFont(QFont("Segoe UI", 7))
        for value in x_ticks:
            x = rect.left() + int(value / max(1, self.num_samples - 1) * rect.width())
            painter.setPen(QPen(QColor("#173049"), 1))
            painter.drawLine(x, rect.top(), x, rect.bottom())
            painter.setPen(QColor("#7892aa"))
            painter.drawText(x - 18, rect.bottom() + 16, 36, 12, Qt.AlignmentFlag.AlignCenter, str(value))
        for value in y_ticks:
            y = rect.bottom() - int((value - self.min_db) / (self.max_db - self.min_db) * rect.height())
            painter.setPen(QPen(QColor("#173049"), 1))
            painter.drawLine(rect.left(), y, rect.right(), y)
            painter.setPen(QColor("#7892aa"))
            painter.drawText(rect.left() - 38, y - 6, 32, 12, Qt.AlignmentFlag.AlignRight, str(value))

        curve_x = np.linspace(0, self.num_samples - 1, 256, dtype=np.float32)
        curve_y = np.interp(curve_x, np.arange(self.num_samples, dtype=np.float32), self.tgc_db_curve)
        points = [self._point_to_pixel(x, y) for x, y in zip(curve_x, curve_y)]
        painter.setPen(QPen(QColor("#45e6ff"), 2))
        if len(points) > 1:
            painter.drawPolyline(points)

        for idx, (x, y) in enumerate(self.ctrl_points):
            pt = self._point_to_pixel(x, y)
            painter.setBrush(QColor("#facc15") if idx == self._selected_index else QColor("#67e8f9"))
            painter.setPen(QPen(QColor("#e6fbff"), 1))
            painter.drawEllipse(pt, 5, 5)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        distances = []
        for x, y in self.ctrl_points:
            pt = self._point_to_pixel(x, y)
            distances.append((pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2)
        index = int(np.argmin(distances))
        if distances[index] <= 18 ** 2:
            self._drag_index = index
            self._selected_index = index
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        x, y = self._pixel_to_point(event.position().toPoint())
        if self._drag_index == 0:
            x = 0.0
        elif self._drag_index == len(self.ctrl_points) - 1:
            x = float(self.num_samples - 1)
        else:
            x = np.clip(
                x,
                self.ctrl_points[self._drag_index - 1, 0] + 1,
                self.ctrl_points[self._drag_index + 1, 0] - 1,
            )
        self.ctrl_points[self._drag_index] = [x, y]
        self._update_gain()

    def mouseReleaseEvent(self, event):
        self._drag_index = None


class UltrasoundMainWindow(QMainWindow):
    """Main PySide6 workstation window replacing cv2.imshow and the OpenCV Trackbar."""

    def __init__(self, demo_mode=False):
        super().__init__()
        self.pipeline = UltrasoundPipeline(demo_mode=demo_mode)
        self.params = self.pipeline.params
        self.demo_mode = demo_mode
        self._paused = False
        self.current_fps = 0.0
        self.display_status = "STOPPED"
        self.probe_status = "Connected" if demo_mode else "Unknown"
        self.latency_text = "-- ms"
        self.temperature_text = "-- deg C"
        self.focus_depth_mm = 60.0
        self.default_dynamic_range_db = float(self.params.get("dynamic_range_db", 45))
        self.default_brightness_db = float(self.params.get("brightness_db", 3.0))
        self.default_contrast_gain = float(self.params.get("contrast_gain", 1.0))
        self.default_noise_floor_db = float(self.params.get("noise_floor_db", -45.0))
        self.dynamic_range_db = self.default_dynamic_range_db
        self.brightness_db = self.default_brightness_db
        self.contrast_gain = self.default_contrast_gain
        self.noise_floor_db = self.default_noise_floor_db
        self._custom_maximized = False
        self._fullscreen_active = False
        self._normal_geometry = None
        self._restore_custom_maximized_after_fullscreen = False
        self._cleanup_started = False
        self._usb_config_in_progress = False
        self._usb_config_restart_after_finish = False
        self._usb_config_restore_paused = False
        self._usb_config_saved_bmode_values = None

        self.setWindowTitle("UltraVision Workstation")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        icon = get_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1360, 900)
        self._build_ui()
        self._connect_signals()
        self._apply_style()
        self._setup_shortcuts()
        self._update_params()
        self._set_status("Stopped")

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)
        self.root_layout = root_layout

        self.window_shell = QFrame()
        self.window_shell.setObjectName("WindowShell")
        root_layout.addWidget(self.window_shell)

        shell_layout = QVBoxLayout(self.window_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.title_bar = WindowTitleBar(self)
        shell_layout.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("AppContent")
        shell_layout.addWidget(content, 1)

        main_layout = QHBoxLayout(content)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("MainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        main_layout.addWidget(self.main_splitter, 1)

        image_panel = QWidget()
        image_panel.setObjectName("ImagePanel")
        image_panel.setMinimumWidth(520)
        image_column = QVBoxLayout(image_panel)
        image_column.setContentsMargins(0, 0, 0, 0)
        image_column.setSpacing(10)

        header = QFrame()
        header.setObjectName("HeaderPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)

        title_block = QVBoxLayout()
        self.title_label = QLabel("UltraVision Workstation")
        self.title_label.setObjectName("TitleLabel")
        self.subtitle_label = QLabel("Real-time Ultrasound Imaging Platform - B-mode")
        self.subtitle_label.setObjectName("SubtitleLabel")
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)

        self.status_badge = QLabel("Stopped")
        self.status_badge.setObjectName("StatusBadge")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fps_label = QLabel("FPS 0.0")
        self.fps_label.setObjectName("FpsBadge")
        self.fps_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout.addLayout(title_block)
        header_layout.addStretch(1)
        header_layout.addWidget(self.fps_label)
        header_layout.addWidget(self.status_badge)

        self.image_widget = UltrasoundImageWidget()
        image_column.addWidget(header)
        image_column.addWidget(self.image_widget, 1)
        image_column.addWidget(self._make_quick_controls_panel())
        self.main_splitter.addWidget(image_panel)

        side_panel = QFrame()
        side_panel.setObjectName("SidePanel")
        side_panel.setMinimumWidth(370)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(12)

        side_layout.addWidget(self._make_controls_panel())
        side_layout.addWidget(self._make_ps_uart_config_panel())
        side_layout.addWidget(self._make_ps_usb_config_panel())
        side_layout.addWidget(self._make_tgc_panel())
        side_layout.addWidget(self._make_graymap_panel())
        side_layout.addWidget(self._make_imaging_panel())
        side_layout.addStretch(1)

        side_scroll = QScrollArea()
        side_scroll.setObjectName("SideScrollArea")
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        side_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        side_scroll.setMinimumWidth(390)
        side_scroll.setWidget(side_panel)
        self.main_splitter.addWidget(side_scroll)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([1100, 430])

    def _make_controls_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        heading = QLabel("Acquisition")
        heading.setObjectName("PanelHeading")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["A-mode", "M-mode", "B-mode", "Doppler"])
        self.mode_combo.setCurrentText("B-mode")

        control_row = QHBoxLayout()
        control_row.setSpacing(8)
        self.start_stop_button = QPushButton("START")
        self.start_stop_button.setObjectName("LiveButton")
        self.start_stop_button.setProperty("state", "start")
        self.freeze_button = QPushButton("FREEZE")
        self.freeze_button.setObjectName("FreezeButton")
        self.freeze_button.setEnabled(False)
        control_row.addWidget(self.start_stop_button, 1)
        control_row.addWidget(self.freeze_button, 1)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(8)
        summary_grid.setVerticalSpacing(8)
        self.acq_status_value = QLabel(self.display_status)
        self.acq_probe_value = QLabel(self.probe_status)
        self.acq_fps_value = QLabel("0.0 FPS")
        self.acq_latency_value = QLabel(self.latency_text)
        self.acq_temp_value = QLabel(self.temperature_text)
        summary_items = [
            ("Status", self.acq_status_value),
            ("Probe", self.acq_probe_value),
            ("FPS", self.acq_fps_value),
            ("Latency", self.acq_latency_value),
            ("Temp", self.acq_temp_value),
        ]
        for index, (label, value_label) in enumerate(summary_items):
            summary_grid.addWidget(self._make_status_chip(label, value_label), index // 2, index % 2)

        layout.addWidget(heading)
        layout.addWidget(self._label_value_row("Mode", self.mode_combo))
        layout.addLayout(control_row)
        layout.addLayout(summary_grid)
        return panel

    def _make_status_chip(self, label_text, value_label):
        chip = QFrame()
        chip.setObjectName("StatusChip")
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        label = QLabel(label_text)
        label.setObjectName("ChipLabel")
        value_label.setObjectName("ChipValue")
        layout.addWidget(label)
        layout.addWidget(value_label)
        return chip

    def _make_metric_panel(self, title, rows):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        heading = QLabel(title)
        heading.setObjectName("PanelHeading")
        layout.addWidget(heading, 0, 0, 1, 2)

        for row_index, (name, value_label) in enumerate(rows, start=1):
            name_label = QLabel(name)
            name_label.setObjectName("ParamName")
            value_label.setObjectName("ParamValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(name_label, row_index, 0)
            layout.addWidget(value_label, row_index, 1)
        return panel

    def _make_system_status_panel(self):
        self.system_labels = {
            "status": QLabel(self.display_status),
            "fps": QLabel("0.0 FPS"),
            "probe": QLabel(self.probe_status),
            "latency": QLabel(self.latency_text),
            "temperature": QLabel(self.temperature_text),
        }
        return self._make_metric_panel(
            "System Status",
            [
                ("Status", self.system_labels["status"]),
                ("FPS", self.system_labels["fps"]),
                ("Probe", self.system_labels["probe"]),
                ("Latency", self.system_labels["latency"]),
                ("Temperature", self.system_labels["temperature"]),
            ],
        )
        layout.addWidget(self._label_value_row("Mode", self.mode_combo))
        return panel

    def _make_ps_uart_config_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("PS UART Config")
        heading.setObjectName("PanelHeading")

        self.ps_port_combo = QComboBox()
        self.ps_port_combo.setEditable(True)

        self.ps_refresh_button = QPushButton("Refresh")
        self.ps_refresh_button.setObjectName("ToolButton")

        port_controls = QWidget()
        port_layout = QHBoxLayout(port_controls)
        port_layout.setContentsMargins(0, 0, 0, 0)
        port_layout.setSpacing(8)
        port_layout.addWidget(self.ps_port_combo, 1)
        port_layout.addWidget(self.ps_refresh_button)

        self.ps_target_combo = QComboBox()
        self.ps_target_combo.addItems(PS_UART_TARGETS)
        self.ps_target_combo.setCurrentText("afe5832")

        self.ps_config_button = QPushButton("Configure PS")
        self.ps_config_button.setObjectName("PrimaryButton")
        self.ps_advanced_config_button = QPushButton("Advanced PS Config")
        self.ps_advanced_config_button.setObjectName("ToolButton")

        output_label = QLabel("PS Reply")
        output_label.setObjectName("ParamName")
        self.ps_uart_output = QPlainTextEdit()
        self.ps_uart_output.setObjectName("SerialOutput")
        self.ps_uart_output.setReadOnly(True)
        self.ps_uart_output.setMaximumBlockCount(500)
        self.ps_uart_output.setFixedHeight(92)

        layout.addWidget(heading)
        layout.addWidget(self._label_value_row("COM", port_controls))
        layout.addWidget(self._label_value_row("Target", self.ps_target_combo))
        layout.addWidget(self.ps_config_button)
        layout.addWidget(self.ps_advanced_config_button)
        layout.addWidget(output_label)
        layout.addWidget(self.ps_uart_output)

        self.ps_config_process = None
        self.ps_beam_process = None
        self.ps_advanced_config_process = None
        self._refresh_serial_ports()
        return panel

    def _make_ps_usb_config_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("PS USB Config")
        heading.setObjectName("PanelHeading")

        self.ps_usb_target_combo = QComboBox()
        self.ps_usb_target_combo.addItems(PS_UART_TARGETS)
        self.ps_usb_target_combo.setCurrentText("afe5832")

        vid_pid_label = QLabel("0x0424 / 0x4940")
        vid_pid_label.setObjectName("ParamValue")
        endpoint_label = QLabel("OUT 0x01 / IN 0x81")
        endpoint_label.setObjectName("ParamValue")

        self.ps_usb_config_button = QPushButton("Configure PS via USB")
        self.ps_usb_config_button.setObjectName("PrimaryButton")

        output_label = QLabel("USB Reply")
        output_label.setObjectName("ParamName")
        self.ps_usb_output = QPlainTextEdit()
        self.ps_usb_output.setObjectName("SerialOutput")
        self.ps_usb_output.setReadOnly(True)
        self.ps_usb_output.setMaximumBlockCount(500)
        self.ps_usb_output.setFixedHeight(92)

        layout.addWidget(heading)
        layout.addWidget(self._label_value_row("Target", self.ps_usb_target_combo))
        layout.addWidget(self._label_value_row("VID/PID", vid_pid_label))
        layout.addWidget(self._label_value_row("Endpoint", endpoint_label))
        layout.addWidget(self.ps_usb_config_button)
        layout.addWidget(output_label)
        layout.addWidget(self.ps_usb_output)

        self.ps_usb_config_process = None
        self.ps_usb_beam_process = None
        return panel

    def _make_imaging_panel(self):
        self.imaging_labels = {
            "depth": QLabel("--"),
            "frequency": QLabel("--"),
            "focus": QLabel(f"{self.focus_depth_mm:.0f} mm"),
            "dynamic_range": QLabel(f"{self.dynamic_range_db:.0f} dB"),
            "brightness": QLabel(f"{self.brightness_db:.1f} dB"),
            "contrast": QLabel(f"{self.contrast_gain:.2f}x"),
            "noise_floor": QLabel(f"{self.noise_floor_db:.1f} dB"),
        }
        return self._make_metric_panel(
            "Imaging Parameters",
            [
                ("Depth", self.imaging_labels["depth"]),
                ("Frequency", self.imaging_labels["frequency"]),
                ("Focus", self.imaging_labels["focus"]),
                ("Dynamic Range", self.imaging_labels["dynamic_range"]),
                ("Brightness", self.imaging_labels["brightness"]),
                ("Contrast Gain", self.imaging_labels["contrast"]),
                ("Noise Floor", self.imaging_labels["noise_floor"]),
            ],
        )

    def _make_graymap_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("Graymap Curve")
        heading.setObjectName("PanelHeading")
        self.graymap_curve = GraymapCurveWidget()
        self.graymap_curve.lut_changed.connect(self._graymap_lut_changed)
        self.pipeline.set_graymap_lut(self.graymap_curve.lut)

        reset_button = QPushButton("Reset Graymap")
        reset_button.setObjectName("ToolButton")
        reset_button.clicked.connect(self.graymap_curve.reset_linear)

        layout.addWidget(heading)
        layout.addWidget(self.graymap_curve)
        layout.addWidget(reset_button)
        return panel

    def _make_tgc_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("TGC Curve")
        heading.setObjectName("PanelHeading")
        self.tgc_enable_checkbox = QCheckBox("Enable TGC")
        self.tgc_enable_checkbox.setObjectName("TgcEnableCheck")

        self.tgc_curve = TgcCurveWidget()
        self.tgc_curve.tgc_changed.connect(self._tgc_curve_changed)

        self.tgc_point_label = QLabel(self.tgc_curve.selected_point_text())
        self.tgc_point_label.setObjectName("ParamName")
        self.tgc_max_gain_label = QLabel(self.tgc_curve.peak_summary_text())
        self.tgc_max_gain_label.setObjectName("ParamValue")

        tgc_button_row = QHBoxLayout()
        tgc_button_row.setSpacing(8)
        self.tgc_reset_button = QPushButton("Reset TGC")
        self.tgc_save_button = QPushButton("Save TGC")
        self.tgc_load_button = QPushButton("Load TGC")
        for button in (self.tgc_reset_button, self.tgc_save_button, self.tgc_load_button):
            button.setObjectName("ToolButton")
            button.setMinimumWidth(0)
            tgc_button_row.addWidget(button, 1)

        layout.addWidget(heading)
        layout.addWidget(self.tgc_enable_checkbox)
        layout.addWidget(self.tgc_curve)
        layout.addWidget(self.tgc_point_label)
        layout.addWidget(self.tgc_max_gain_label)
        layout.addLayout(tgc_button_row)

        self.pipeline.set_tgc_gain(self.tgc_curve.tgc_gain)
        self.pipeline.set_tgc_enabled(False)
        return panel

    def _make_quick_controls_panel(self):
        panel = QFrame()
        panel.setObjectName("QuickControls")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.dr_value_label = QLabel(f"{self.dynamic_range_db:.0f} dB")
        self.dr_slider = self._make_quick_slider(20, 70, int(self.dynamic_range_db))
        layout.addWidget(self._make_quick_slider_block("Dynamic", self.dr_slider, self.dr_value_label), 1)

        self.brightness_value_label = QLabel(f"{self.brightness_db:.1f} dB")
        self.brightness_slider = self._make_quick_slider(-10, 20, int(round(self.brightness_db)))
        self.brightness_slider.valueChanged.connect(self._brightness_changed)
        layout.addWidget(self._make_quick_slider_block("Brightness", self.brightness_slider, self.brightness_value_label), 1)

        self.contrast_value_label = QLabel(f"{self.contrast_gain:.2f}x")
        self.contrast_slider = self._make_quick_slider(50, 200, int(round(self.contrast_gain * 100)))
        self.contrast_slider.valueChanged.connect(self._contrast_changed)
        layout.addWidget(self._make_quick_slider_block("Contrast Gain", self.contrast_slider, self.contrast_value_label), 1)

        self.noise_floor_value_label = QLabel(f"{self.noise_floor_db:.1f} dB")
        self.noise_floor_slider = self._make_quick_slider(-60, -20, int(round(self.noise_floor_db)))
        self.noise_floor_slider.valueChanged.connect(self._noise_floor_changed)
        layout.addWidget(self._make_quick_slider_block("Noise Floor", self.noise_floor_slider, self.noise_floor_value_label), 1)

        reset_block = QFrame()
        reset_block.setObjectName("QuickResetBlock")
        reset_layout = QVBoxLayout(reset_block)
        reset_layout.setContentsMargins(8, 6, 8, 6)
        reset_layout.setSpacing(0)
        self.reset_bmode_button = QPushButton("Reset")
        self.reset_bmode_button.setObjectName("ResetBmodeButton")
        self.reset_bmode_button.setFixedWidth(92)
        reset_layout.addWidget(self.reset_bmode_button, 1)
        layout.addWidget(reset_block)
        return panel

    def _make_quick_slider(self, minimum, maximum, value):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(1)
        slider.setPageStep(5)
        slider.setValue(value)
        return slider

    def _make_quick_slider_block(self, label_text, slider, value_label):
        block = QFrame()
        block.setObjectName("QuickSliderBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        row = QHBoxLayout()
        name = QLabel(label_text)
        name.setObjectName("ParamName")
        value_label.setObjectName("ParamValue")
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(value_label)

        layout.addLayout(row)
        layout.addWidget(slider)
        return block

    def _label_value_row(self, label_text, widget):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        label.setObjectName("ParamName")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return row

    def _connect_signals(self):
        self.start_stop_button.clicked.connect(self._toggle_run)
        self.freeze_button.clicked.connect(self._toggle_freeze)
        self.dr_slider.valueChanged.connect(self._dynamic_range_changed)
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        self.reset_bmode_button.clicked.connect(self._reset_bmode_controls)
        self.ps_refresh_button.clicked.connect(self._refresh_serial_ports)
        self.ps_config_button.clicked.connect(self._start_ps_uart_config)
        self.ps_advanced_config_button.clicked.connect(self._open_advanced_ps_config)
        self.ps_usb_config_button.clicked.connect(self._start_ps_usb_config)
        self.tgc_enable_checkbox.toggled.connect(self._tgc_enabled_changed)
        self.tgc_reset_button.clicked.connect(self._reset_tgc)
        self.tgc_save_button.clicked.connect(self._save_tgc)
        self.tgc_load_button.clicked.connect(self._load_tgc)

        self.pipeline.frame_ready.connect(self.image_widget.set_frame)
        self.pipeline.fps_updated.connect(self._update_fps)
        self.pipeline.status_changed.connect(self._set_status)
        self.pipeline.error_occurred.connect(self._show_error)

    def _setup_shortcuts(self):
        fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        escape_shortcut.activated.connect(self.exit_fullscreen)

    def _set_window_margin_for_state(self):
        if not hasattr(self, "root_layout"):
            return
        margin = 0 if self._fullscreen_active else 2 if self._custom_maximized else 8
        self.root_layout.setContentsMargins(margin, margin, margin, margin)

    def _custom_maximized_geometry(self):
        screen = self._current_qt_screen()
        target_geometry = screen.availableGeometry() if screen is not None else self.frameGeometry()

        safety_inset = 8
        if target_geometry.width() > safety_inset * 2 and target_geometry.height() > safety_inset * 2:
            target_geometry = target_geometry.adjusted(
                safety_inset,
                safety_inset,
                -safety_inset,
                -safety_inset,
            )
        return target_geometry

    def _apply_window_geometry(self, geometry):
        self.move(geometry.topLeft())
        self.resize(geometry.size())

    def _current_qt_screen(self):
        window_center = self.frameGeometry().center()
        handle = self.windowHandle()
        current_screen = handle.screen() if handle is not None else None
        for screen in current_screen.virtualSiblings() if current_screen is not None else []:
            if screen.geometry().contains(window_center):
                return screen
        screen = self.screen()
        if screen is not None:
            return screen
        return current_screen

    def toggle_custom_maximized(self):
        if self._fullscreen_active:
            self.exit_fullscreen()
        if self._custom_maximized:
            self.restore_custom_maximized()
        else:
            self.enter_custom_maximized()

    def enter_custom_maximized(self):
        if self._custom_maximized:
            return
        self._normal_geometry = self.geometry()
        self._custom_maximized = True
        self._apply_window_geometry(self._custom_maximized_geometry())
        self._set_window_margin_for_state()
        if hasattr(self, "title_bar"):
            self.title_bar.set_maximized_state(True)

    def restore_custom_maximized(self):
        if not self._custom_maximized:
            return
        geometry = self._normal_geometry
        self._custom_maximized = False
        if geometry is not None:
            self._apply_window_geometry(geometry)
        self._set_window_margin_for_state()
        if hasattr(self, "title_bar"):
            self.title_bar.set_maximized_state(False)

    def restore_from_custom_maximized_for_drag(self):
        geometry = self._normal_geometry
        self._custom_maximized = False
        if geometry is not None:
            self.resize(geometry.size())
        self._set_window_margin_for_state()
        if hasattr(self, "title_bar"):
            self.title_bar.set_maximized_state(False)

    def toggle_fullscreen(self):
        if self._fullscreen_active:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        if self._fullscreen_active:
            return
        self._restore_custom_maximized_after_fullscreen = self._custom_maximized
        if not self._custom_maximized:
            self._normal_geometry = self.geometry()
        self._fullscreen_active = True
        self._set_window_margin_for_state()
        self.showFullScreen()
        if hasattr(self, "title_bar"):
            self.title_bar.set_fullscreen_state(True)

    def exit_fullscreen(self):
        if not self._fullscreen_active:
            return
        restore_custom_maximized = self._restore_custom_maximized_after_fullscreen
        self._fullscreen_active = False
        self.showNormal()
        if restore_custom_maximized:
            self._custom_maximized = True
            self._apply_window_geometry(self._custom_maximized_geometry())
            if hasattr(self, "title_bar"):
                self.title_bar.set_maximized_state(True)
        elif self._normal_geometry is not None:
            self._custom_maximized = False
            self._apply_window_geometry(self._normal_geometry)
            if hasattr(self, "title_bar"):
                self.title_bar.set_maximized_state(False)
        self._set_window_margin_for_state()
        if hasattr(self, "title_bar"):
            self.title_bar.set_fullscreen_state(False)

    def _append_ps_uart_output(self, text):
        if not text:
            return
        self.ps_uart_output.moveCursor(QTextCursor.MoveOperation.End)
        self.ps_uart_output.insertPlainText(text)
        self.ps_uart_output.moveCursor(QTextCursor.MoveOperation.End)

    def _append_ps_usb_output(self, text):
        if not text:
            return
        self.ps_usb_output.moveCursor(QTextCursor.MoveOperation.End)
        self.ps_usb_output.insertPlainText(text)
        self.ps_usb_output.moveCursor(QTextCursor.MoveOperation.End)

    def _refresh_serial_ports(self):
        current = self.ps_port_combo.currentText().strip() if self.ps_port_combo.count() else ""
        ports = []
        note = ""
        try:
            from serial.tools import list_ports

            ports = [port.device for port in list_ports.comports()]
        except Exception as exc:
            note = f"pyserial/list_ports unavailable: {exc}\n"

        if not ports:
            ports = ["COM3"]

        self.ps_port_combo.blockSignals(True)
        self.ps_port_combo.clear()
        self.ps_port_combo.addItems(ports)
        selected = current if current else ports[0]
        if selected not in ports:
            self.ps_port_combo.addItem(selected)
        self.ps_port_combo.setCurrentText(selected)
        self.ps_port_combo.blockSignals(False)

        if note:
            self._append_ps_uart_output(note)

    def _start_ps_uart_config(self):
        if self.ps_config_process and self.ps_config_process.state() != QProcess.ProcessState.NotRunning:
            self._append_ps_uart_output("Configuration is already running.\n")
            return

        port = self.ps_port_combo.currentText().strip() or "COM3"
        target = self.ps_target_combo.currentText().strip() or "afe5832"
        if not PS_UART_TOOL.exists():
            self.ps_uart_output.setPlainText(f"Missing UART config tool: {PS_UART_TOOL}\n")
            return

        self.ps_uart_output.clear()
        self._append_ps_uart_output(f"Port: {port}\nTarget: {target}\n\n")
        self.ps_config_button.setEnabled(False)

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(PS_UART_TOOL), "--port", port, "--target", target])
        process.setWorkingDirectory(str(PS_TOOLS_DIR))
        process.readyReadStandardOutput.connect(self._read_ps_uart_stdout)
        process.readyReadStandardError.connect(self._read_ps_uart_stderr)
        process.finished.connect(self._ps_uart_finished)
        process.errorOccurred.connect(self._ps_uart_process_error)
        self.ps_config_process = process
        process.start()

    def _open_advanced_ps_config(self):
        if (
            self.ps_advanced_config_process
            and self.ps_advanced_config_process.state() != QProcess.ProcessState.NotRunning
        ):
            self._append_ps_uart_output("Advanced PS config GUI is already running.\n")
            return
        if not PS_CONFIG_GUI.exists():
            self._append_ps_uart_output(f"Missing advanced PS config GUI: {PS_CONFIG_GUI}\n")
            return

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments([str(PS_CONFIG_GUI)])
        process.setWorkingDirectory(str(PS_TOOLS_DIR))
        process.readyReadStandardOutput.connect(self._read_advanced_ps_stdout)
        process.readyReadStandardError.connect(self._read_advanced_ps_stderr)
        process.finished.connect(self._advanced_ps_finished)
        process.errorOccurred.connect(self._advanced_ps_process_error)
        self.ps_advanced_config_process = process
        process.start()
        if process.waitForStarted(1000):
            self._append_ps_uart_output(f"Launched advanced PS config GUI: {PS_CONFIG_GUI}\n")
        else:
            self._append_ps_uart_output(f"Failed to launch advanced PS config GUI: {PS_CONFIG_GUI}\n")
            self.ps_advanced_config_process = None

    def _read_ps_uart_stdout(self):
        if not self.ps_config_process:
            return
        data = bytes(self.ps_config_process.readAllStandardOutput()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _read_ps_uart_stderr(self):
        if not self.ps_config_process:
            return
        data = bytes(self.ps_config_process.readAllStandardError()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _ps_uart_finished(self, exit_code, exit_status):
        self._read_ps_uart_stdout()
        self._read_ps_uart_stderr()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._append_ps_uart_output("\nConfiguration finished.\n")
        else:
            self._append_ps_uart_output(f"\nConfiguration failed, exit code {exit_code}.\n")
        self.ps_config_button.setEnabled(True)
        self.ps_config_process = None

    def _ps_uart_process_error(self, error):
        self._append_ps_uart_output(f"\nQProcess error: {error}\n")
        self.ps_config_button.setEnabled(True)
        if self.ps_config_process and self.ps_config_process.state() == QProcess.ProcessState.NotRunning:
            self.ps_config_process = None

    def _start_ps_usb_config(self):
        if self.ps_usb_config_process and self.ps_usb_config_process.state() != QProcess.ProcessState.NotRunning:
            self._append_ps_usb_output("USB configuration is already running.\n")
            return

        target = self.ps_usb_target_combo.currentText().strip() or "afe5832"
        if not PS_USB_TOOL.exists():
            self.ps_usb_output.setPlainText(f"Missing USB config tool: {PS_USB_TOOL}\n")
            return

        self.ps_usb_output.clear()
        self._append_ps_usb_output(
            "Transport: USB\nVID/PID: 0x0424 / 0x4940\nEndpoint: OUT 0x01 / IN 0x81\n"
            f"Target: {target}\n\n"
        )
        self._prepare_ps_usb_config_session()
        self.ps_usb_config_button.setEnabled(False)

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(PS_USB_TOOL), "--target", target])
        process.setWorkingDirectory(str(PS_TOOLS_DIR))
        process.readyReadStandardOutput.connect(self._read_ps_usb_stdout)
        process.readyReadStandardError.connect(self._read_ps_usb_stderr)
        process.finished.connect(self._ps_usb_finished)
        process.errorOccurred.connect(self._ps_usb_process_error)
        self.ps_usb_config_process = process
        process.start()

    def _prepare_ps_usb_config_session(self):
        self._usb_config_in_progress = True
        self._usb_config_restart_after_finish = bool(self.pipeline.running)
        self._usb_config_restore_paused = bool(self._paused)
        self._usb_config_saved_bmode_values = self._current_bmode_values()

        self.start_stop_button.setEnabled(False)
        self.freeze_button.setEnabled(False)

        if not self._usb_config_restart_after_finish:
            return

        self._append_ps_usb_output("Stopping live acquisition to release USB device...\n")
        self.pipeline.stop()
        self._paused = False
        self.freeze_button.setText("FREEZE")
        self.fps_label.setText("FPS 0.0")
        self._update_fps(0.0)
        self._append_ps_usb_output("USB acquisition stopped. Starting PS USB configuration...\n\n")

    def _finish_ps_usb_config_session(self):
        if self._cleanup_started:
            self._usb_config_in_progress = False
            self._usb_config_restart_after_finish = False
            self._usb_config_restore_paused = False
            self._usb_config_saved_bmode_values = None
            return

        if not self._usb_config_in_progress:
            self.ps_usb_config_button.setEnabled(True)
            self.start_stop_button.setEnabled(True)
            return

        restart = self._usb_config_restart_after_finish
        restore_paused = self._usb_config_restore_paused
        bmode_values = self._usb_config_saved_bmode_values or self._current_bmode_values()

        self._usb_config_in_progress = False
        self._usb_config_restart_after_finish = False
        self._usb_config_restore_paused = False
        self._usb_config_saved_bmode_values = None

        self.ps_usb_config_button.setEnabled(True)
        self.start_stop_button.setEnabled(True)

        if not restart:
            self.freeze_button.setEnabled(False)
            return

        self._append_ps_usb_output("\nRestarting live acquisition...\n")
        self._paused = False
        self.freeze_button.setText("FREEZE")
        self.pipeline.start(bmode_values)

        if restore_paused and self.pipeline.running:
            self._paused = True
            self.pipeline.pause(True)
            self.freeze_button.setText("RESUME")

        self._append_ps_usb_output("Acquisition restored.\n")

    def _read_ps_usb_stdout(self):
        if not self.ps_usb_config_process:
            return
        data = bytes(self.ps_usb_config_process.readAllStandardOutput()).decode(errors="replace")
        self._append_ps_usb_output(data)

    def _read_ps_usb_stderr(self):
        if not self.ps_usb_config_process:
            return
        data = bytes(self.ps_usb_config_process.readAllStandardError()).decode(errors="replace")
        self._append_ps_usb_output(data)

    def _ps_usb_finished(self, exit_code, exit_status):
        self._read_ps_usb_stdout()
        self._read_ps_usb_stderr()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._append_ps_usb_output("\nUSB configuration finished.\n")
        else:
            self._append_ps_usb_output(f"\nUSB configuration failed, exit code {exit_code}.\n")
        self.ps_usb_config_process = None
        self._finish_ps_usb_config_session()

    def _ps_usb_process_error(self, error):
        self._append_ps_usb_output(f"\nUSB QProcess error: {error}\n")
        failed_to_start = error == QProcess.ProcessError.FailedToStart
        if (
            failed_to_start
            or (
                self.ps_usb_config_process
                and self.ps_usb_config_process.state() == QProcess.ProcessState.NotRunning
            )
        ):
            self.ps_usb_config_process = None
            self._finish_ps_usb_config_session()

    def _start_beam_control(self, operation):
        if self.ps_usb_beam_process and self.ps_usb_beam_process.state() != QProcess.ProcessState.NotRunning:
            self._append_ps_usb_output(f"Beam {operation} skipped: previous USB beam command is still running.\n")
            return
        if not PS_USB_TOOL.exists():
            self._append_ps_usb_output(f"Missing USB config tool: {PS_USB_TOOL}\n")
            return

        self._append_ps_usb_output(f"\nBeam {operation.upper()} via USB\n")

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(PS_USB_TOOL), "--beam", operation])
        process.setWorkingDirectory(str(PS_TOOLS_DIR))
        process.readyReadStandardOutput.connect(self._read_usb_beam_stdout)
        process.readyReadStandardError.connect(self._read_usb_beam_stderr)
        process.finished.connect(self._usb_beam_finished)
        process.errorOccurred.connect(self._usb_beam_process_error)
        self.ps_usb_beam_process = process
        process.start()

    def _read_usb_beam_stdout(self):
        if not self.ps_usb_beam_process:
            return
        data = bytes(self.ps_usb_beam_process.readAllStandardOutput()).decode(errors="replace")
        self._append_ps_usb_output(data)

    def _read_usb_beam_stderr(self):
        if not self.ps_usb_beam_process:
            return
        data = bytes(self.ps_usb_beam_process.readAllStandardError()).decode(errors="replace")
        self._append_ps_usb_output(data)

    def _usb_beam_finished(self, exit_code, exit_status):
        self._read_usb_beam_stdout()
        self._read_usb_beam_stderr()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._append_ps_usb_output("USB beam command finished.\n")
        else:
            self._append_ps_usb_output(f"USB beam command failed, exit code {exit_code}.\n")
        self.ps_usb_beam_process = None

    def _usb_beam_process_error(self, error):
        self._append_ps_usb_output(f"USB beam QProcess error: {error}\n")
        if self.ps_usb_beam_process and self.ps_usb_beam_process.state() == QProcess.ProcessState.NotRunning:
            self.ps_usb_beam_process = None

    def _read_beam_stdout(self):
        if not self.ps_beam_process:
            return
        data = bytes(self.ps_beam_process.readAllStandardOutput()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _read_beam_stderr(self):
        if not self.ps_beam_process:
            return
        data = bytes(self.ps_beam_process.readAllStandardError()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _beam_finished(self, exit_code, exit_status):
        self._read_beam_stdout()
        self._read_beam_stderr()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._append_ps_uart_output("Beam command finished.\n")
        else:
            self._append_ps_uart_output(f"Beam command failed, exit code {exit_code}.\n")
        self.ps_beam_process = None

    def _beam_process_error(self, error):
        self._append_ps_uart_output(f"Beam QProcess error: {error}\n")
        if self.ps_beam_process and self.ps_beam_process.state() == QProcess.ProcessState.NotRunning:
            self.ps_beam_process = None

    def _read_advanced_ps_stdout(self):
        if not self.ps_advanced_config_process:
            return
        data = bytes(self.ps_advanced_config_process.readAllStandardOutput()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _read_advanced_ps_stderr(self):
        if not self.ps_advanced_config_process:
            return
        data = bytes(self.ps_advanced_config_process.readAllStandardError()).decode(errors="replace")
        self._append_ps_uart_output(data)

    def _advanced_ps_finished(self, exit_code, exit_status):
        self._read_advanced_ps_stdout()
        self._read_advanced_ps_stderr()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._append_ps_uart_output("Advanced PS config GUI closed.\n")
        else:
            self._append_ps_uart_output(f"Advanced PS config GUI exited, code {exit_code}.\n")
        self.ps_advanced_config_process = None

    def _advanced_ps_process_error(self, error):
        self._append_ps_uart_output(f"Advanced PS config QProcess error: {error}\n")
        if (
            self.ps_advanced_config_process
            and self.ps_advanced_config_process.state() == QProcess.ProcessState.NotRunning
        ):
            self.ps_advanced_config_process = None

    def _toggle_run(self):
        if self.pipeline.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._paused = False
        self.freeze_button.setText("FREEZE")
        self.freeze_button.setEnabled(True)
        self.pipeline.start(self._current_bmode_values())

    def _toggle_freeze(self):
        if not self.pipeline.running:
            return
        self._paused = not self._paused
        self.pipeline.pause(self._paused)
        self.freeze_button.setText("RESUME" if self._paused else "FREEZE")
        self._start_beam_control("stop" if self._paused else "start")

    def _stop(self):
        self._paused = False
        self.freeze_button.setText("FREEZE")
        self.freeze_button.setEnabled(False)
        self.pipeline.stop()
        self.fps_label.setText("FPS 0.0")
        self._update_fps(0.0)

    def _dynamic_range_changed(self, value):
        self.dynamic_range_db = float(value)
        self.dr_value_label.setText(f"{self.dynamic_range_db:.0f} dB")
        self.imaging_labels["dynamic_range"].setText(f"{self.dynamic_range_db:.0f} dB")
        self._sync_bmode_params()

    def _brightness_changed(self, value):
        self.brightness_db = float(value)
        self.brightness_value_label.setText(f"{self.brightness_db:.1f} dB")
        self.imaging_labels["brightness"].setText(f"{self.brightness_db:.1f} dB")
        self._sync_bmode_params()

    def _contrast_changed(self, value):
        self.contrast_gain = float(value) / 100.0
        self.contrast_value_label.setText(f"{self.contrast_gain:.2f}x")
        self.imaging_labels["contrast"].setText(f"{self.contrast_gain:.2f}x")
        self._sync_bmode_params()

    def _noise_floor_changed(self, value):
        self.noise_floor_db = float(value)
        self.noise_floor_value_label.setText(f"{self.noise_floor_db:.1f} dB")
        self.imaging_labels["noise_floor"].setText(f"{self.noise_floor_db:.1f} dB")
        self._sync_bmode_params()

    def _reset_bmode_controls(self):
        slider_values = [
            (self.dr_slider, int(round(self.default_dynamic_range_db))),
            (self.brightness_slider, int(round(self.default_brightness_db))),
            (self.contrast_slider, int(round(self.default_contrast_gain * 100))),
            (self.noise_floor_slider, int(round(self.default_noise_floor_db))),
        ]
        for slider, value in slider_values:
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)

        self.dynamic_range_db = float(self.default_dynamic_range_db)
        self.brightness_db = float(self.default_brightness_db)
        self.contrast_gain = float(self.default_contrast_gain)
        self.noise_floor_db = float(self.default_noise_floor_db)

        self.dr_value_label.setText(f"{self.dynamic_range_db:.0f} dB")
        self.brightness_value_label.setText(f"{self.brightness_db:.1f} dB")
        self.contrast_value_label.setText(f"{self.contrast_gain:.2f}x")
        self.noise_floor_value_label.setText(f"{self.noise_floor_db:.1f} dB")
        self.imaging_labels["dynamic_range"].setText(f"{self.dynamic_range_db:.0f} dB")
        self.imaging_labels["brightness"].setText(f"{self.brightness_db:.1f} dB")
        self.imaging_labels["contrast"].setText(f"{self.contrast_gain:.2f}x")
        self.imaging_labels["noise_floor"].setText(f"{self.noise_floor_db:.1f} dB")
        self._sync_bmode_params()

    def _current_bmode_values(self):
        return [
            float(self.dynamic_range_db),
            float(self.brightness_db),
            float(self.contrast_gain),
            float(self.noise_floor_db),
        ]

    def _sync_bmode_params(self):
        self.pipeline.set_bmode_params(*self._current_bmode_values())
        self._update_overlay()

    def _graymap_lut_changed(self, lut):
        self.pipeline.set_graymap_lut(lut)

    def _tgc_curve_changed(self, gain, control_points):
        self.pipeline.set_tgc_gain(gain)
        self.tgc_point_label.setText(self.tgc_curve.selected_point_text())
        self.tgc_max_gain_label.setText(self.tgc_curve.peak_summary_text())

    def _tgc_enabled_changed(self, enabled):
        self.pipeline.set_tgc_enabled(bool(enabled))

    def _reset_tgc(self):
        self.tgc_curve.reset_default()
        self.pipeline.set_tgc_enabled(self.tgc_enable_checkbox.isChecked())

    def _save_tgc(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save TGC Curve",
            str(Path.cwd() / "tgc_curve.json"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(self.tgc_curve.to_json_data(), indent=2), encoding="utf-8")

    def _load_tgc(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load TGC Curve",
            str(Path.cwd()),
            "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            control_points = np.asarray(data["control_points"], dtype=np.float32)
            unit = str(data.get("unit", "")).lower()
            if unit != "db" and "max_gain" in data:
                control_points = control_points.copy()
                control_points[:, 1] = 20.0 * np.log10(np.clip(control_points[:, 1], 1e-6, None))
            self.tgc_curve.set_control_points(
                control_points,
                min_db=float(data.get("min_db", TGC_MIN_DB)),
                max_db=float(data.get("max_db", TGC_MAX_DB)),
                num_samples=int(data.get("num_samples", 6144)),
            )
            self.pipeline.set_tgc_enabled(self.tgc_enable_checkbox.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, "Load TGC", f"Failed to load TGC curve:\n{exc}")

    def _mode_changed(self, mode):
        self.params["mode"] = mode
        self.subtitle_label.setText(f"Real-time Ultrasound Imaging Platform - {mode}")
        if hasattr(self, "title_bar"):
            self.title_bar.set_mode(mode)
        self._update_overlay()

    def _update_fps(self, fps):
        self.current_fps = float(fps)
        self.fps_label.setText(f"FPS {fps:.1f}")
        if hasattr(self, "acq_fps_value"):
            self.acq_fps_value.setText(f"{fps:.1f} FPS")
        self._update_overlay()

    def _set_status(self, status):
        display_status = {
            "Running": "LIVE",
            "Paused": "FREEZE",
            "Stopped": "STOPPED",
            "Error": "ERROR",
        }.get(status, status.upper())
        state_name = display_status.lower()
        self.display_status = display_status

        self.status_badge.setText(display_status)
        self.status_badge.setProperty("state", state_name)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

        if hasattr(self, "acq_status_value"):
            self.acq_status_value.setText(display_status)

        if status in ("Running", "Paused"):
            self.start_stop_button.setText("STOP")
            self.start_stop_button.setProperty("state", "stop")
            self.freeze_button.setEnabled(True)
        else:
            self.start_stop_button.setText("START")
            self.start_stop_button.setProperty("state", "start")
            self.freeze_button.setEnabled(False)
            self.freeze_button.setText("FREEZE")
        self.start_stop_button.style().unpolish(self.start_stop_button)
        self.start_stop_button.style().polish(self.start_stop_button)
        self._update_overlay()

    def _show_error(self, message):
        self._set_status("Error")
        QMessageBox.warning(self, "Ultrasound Pipeline", message)

    def _update_params(self):
        self.imaging_labels["depth"].setText(f"{self.params['imaging_depth_mm']:.1f} mm")
        self.imaging_labels["frequency"].setText(f"{self.params['probe_frequency_mhz']:.1f} MHz")
        self.imaging_labels["focus"].setText(f"{self.focus_depth_mm:.0f} mm")
        self.imaging_labels["dynamic_range"].setText(f"{self.dynamic_range_db:.0f} dB")
        self.imaging_labels["brightness"].setText(f"{self.brightness_db:.1f} dB")
        self.imaging_labels["contrast"].setText(f"{self.contrast_gain:.2f}x")
        self.imaging_labels["noise_floor"].setText(f"{self.noise_floor_db:.1f} dB")
        self._update_overlay()

    def _update_overlay(self):
        self.image_widget.set_overlay_state(
            mode=self.mode_combo.currentText(),
            frequency_mhz=float(self.params["probe_frequency_mhz"]),
            dynamic_range_db=float(self.dynamic_range_db),
            gain_db=float(self.contrast_gain),
            fps=float(self.current_fps),
            mi=0.8,
            tis=0.2,
            probe=self.probe_status,
            depth_mm=float(self.params["imaging_depth_mm"]),
            sampling_rate_mhz=float(self.params["sampling_rate_mhz"]),
            focus_mm=float(self.focus_depth_mm),
        )

    def _apply_style(self):
        self.setStyleSheet(
            """
            QMainWindow {
                background: transparent;
            }
            QWidget {
                background: #07111c;
                color: #d8e6f3;
                font-family: "Segoe UI", "Microsoft YaHei UI", Arial, sans-serif;
                font-size: 14px;
            }
            #AppRoot {
                background: transparent;
            }
            #WindowShell {
                background: #07111c;
                border: 1px solid #24384f;
                border-radius: 14px;
            }
            #AppTitleBar {
                background: #0b111a;
                border-bottom: 1px solid #1e2c3d;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }
            #TitleBarLeftCluster {
                background: transparent;
            }
            #TitleBarText {
                background: transparent;
                color: #c7d7e6;
                font-size: 13px;
                font-weight: 650;
            }
            #TitleBarCenterText {
                background: #111827;
                color: #dbe8f5;
                border: 1px solid #2a3d55;
                border-radius: 7px;
                padding: 4px 22px;
                font-size: 12px;
                font-weight: 650;
            }
            #TitleBarMenuButton {
                background: transparent;
                color: #9fb1c6;
                border: 0px;
                border-radius: 4px;
                padding: 0px 9px;
                font-size: 12px;
                font-weight: 500;
            }
            #TitleBarMenuButton:hover {
                background: #182638;
                color: #e8f3ff;
            }
            #TitleBarMenuButton:pressed {
                background: #22344a;
            }
            #TitleBarButton, #TitleBarCloseButton {
                background: transparent;
                color: #aebdd0;
                border: 0px;
                border-radius: 6px;
                padding: 0px;
                font-size: 16px;
                font-weight: 600;
            }
            #TitleBarButton:hover {
                background: #1a2a3e;
                color: #ffffff;
            }
            #TitleBarButton:pressed {
                background: #263a52;
            }
            #TitleBarCloseButton:hover {
                background: #8f1d2c;
                color: #ffffff;
            }
            #TitleBarCloseButton:pressed {
                background: #681521;
            }
            #AppContent {
                background: #07111c;
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }
            #MainSplitter::handle {
                background: #0b1725;
                border-left: 1px solid #1c3148;
                border-right: 1px solid #07111c;
                width: 8px;
            }
            #MainSplitter::handle:hover {
                background: #12324a;
                border-left: 1px solid #22d3ee;
            }
            #HeaderPanel, #SidePanel, #Panel {
                background: #101c2a;
                border: 1px solid #24384f;
                border-radius: 8px;
            }
            #HeaderPanel {
                background: #0c1927;
            }
            #SidePanel {
                background: #0b1623;
            }
            #SideScrollArea {
                background: #0b1623;
                border: 1px solid #24384f;
                border-radius: 8px;
            }
            #SideScrollArea QWidget {
                background: #0b1623;
            }
            QScrollBar:vertical {
                background: #081320;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #27435e;
                min-height: 28px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #38bdf8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            #TitleLabel {
                font-size: 22px;
                font-weight: 700;
                color: #f0f7ff;
            }
            #SubtitleLabel {
                color: #82a2ba;
            }
            #ParamName, #MutedValue {
                color: #8aa2b8;
            }
            #PanelHeading {
                color: #bfefff;
                font-size: 15px;
                font-weight: 700;
            }
            #ParamValue {
                color: #e7f4ff;
                font-weight: 650;
            }
            #FpsBadge, #StatusBadge {
                min-width: 82px;
                padding: 7px 11px;
                border-radius: 12px;
                font-weight: 700;
            }
            #FpsBadge {
                background: #092334;
                color: #67e8f9;
                border: 1px solid #1f5d75;
            }
            #StatusBadge[state="live"] {
                background: #06271f;
                color: #5eead4;
                border: 1px solid #167f72;
            }
            #StatusBadge[state="freeze"] {
                background: #2d2309;
                color: #facc15;
                border: 1px solid #8a6b12;
            }
            #StatusBadge[state="stopped"] {
                background: #182332;
                color: #9fb2c6;
                border: 1px solid #34475d;
            }
            #StatusBadge[state="error"] {
                background: #32141a;
                color: #fda4af;
                border: 1px solid #8a2f3c;
            }
            #StatusChip {
                background: #0b1725;
                border: 1px solid #20354d;
                border-radius: 7px;
            }
            #ChipLabel {
                background: transparent;
                color: #7892aa;
                font-size: 11px;
                font-weight: 700;
            }
            #ChipValue {
                background: transparent;
                color: #e7f4ff;
                font-size: 13px;
                font-weight: 800;
            }
            #QuickControls {
                background: #091421;
                border: 1px solid #1c3148;
                border-radius: 8px;
            }
            #QuickSliderBlock, #QuickResetBlock {
                background: #0d1a28;
                border: 1px solid #1f344b;
                border-radius: 7px;
            }
            #LiveButton, #FreezeButton, #ToolButton {
                border-radius: 8px;
                padding: 10px 12px;
                font-weight: 800;
                letter-spacing: 0px;
            }
            #LiveButton[state="start"] {
                background: #063344;
                color: #ecfeff;
                border: 1px solid #22d3ee;
            }
            #LiveButton[state="start"]:hover {
                background: #07506a;
                border-color: #67e8f9;
            }
            #LiveButton[state="stop"] {
                background: #34141b;
                color: #fecdd3;
                border: 1px solid #be4757;
            }
            #LiveButton[state="stop"]:hover {
                background: #4a1c25;
                border-color: #fb7185;
            }
            #FreezeButton {
                background: #2d2309;
                color: #fde68a;
                border: 1px solid #8a6b12;
            }
            #FreezeButton:hover {
                background: #49380e;
                border-color: #eab308;
            }
            #FreezeButton:disabled {
                background: #111d2b;
                color: #50667d;
                border: 1px solid #25384f;
            }
            #ToolButton {
                background: #13263a;
                color: #c7dff4;
                border: 1px solid #28445e;
                padding-left: 11px;
                padding-right: 11px;
            }
            #ToolButton:hover {
                background: #18334f;
                border-color: #38bdf8;
            }
            #ResetBmodeButton {
                background: #0b2636;
                color: #b8f4ff;
                border: 1px solid #22d3ee;
                border-radius: 7px;
                padding: 10px 12px;
                font-weight: 800;
            }
            #ResetBmodeButton:hover {
                background: #0f3a52;
                color: #ecfeff;
                border-color: #67e8f9;
            }
            #ResetBmodeButton:pressed {
                background: #082032;
                padding-top: 11px;
                padding-bottom: 9px;
            }
            QCheckBox {
                background: transparent;
                color: #c7dff4;
                font-weight: 700;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #28445e;
                background: #0b1725;
            }
            QCheckBox::indicator:hover {
                border-color: #38bdf8;
            }
            QCheckBox::indicator:checked {
                background: #0891b2;
                border-color: #22d3ee;
            }
            QPushButton {
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 10px 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                border-color: #38bdf8;
            }
            QPushButton:pressed {
                padding-top: 11px;
                padding-bottom: 9px;
            }
            #PrimaryButton {
                background: #0891b2;
                color: #ecfeff;
                border-color: #22d3ee;
            }
            #PrimaryButton:disabled {
                background: #102033;
                color: #5f7489;
                border-color: #25384f;
            }
            #SecondaryButton {
                background: #13263a;
                color: #c7dff4;
                border-color: #28445e;
            }
            #DangerButton {
                background: #2a151b;
                color: #fecdd3;
                border-color: #7f2d38;
            }
            QComboBox {
                background: #0b1725;
                color: #dcecff;
                border: 1px solid #263c55;
                border-radius: 6px;
                padding: 7px 9px;
            }
            QComboBox:hover {
                border-color: #38bdf8;
            }
            QComboBox QAbstractItemView {
                background: #0b1725;
                color: #dcecff;
                selection-background-color: #164e63;
                selection-color: #ecfeff;
                border: 1px solid #263c55;
            }
            QComboBox QLineEdit {
                background: #0b1725;
                color: #dcecff;
                border: 0px;
                selection-background-color: #164e63;
            }
            #SerialOutput {
                background: #050b12;
                color: #b9e8f5;
                border: 1px solid #1f344b;
                border-radius: 7px;
                padding: 8px;
                font-family: "Cascadia Mono", Consolas, monospace;
                font-size: 11px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #203348;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #22d3ee;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
                background: #e6fbff;
                border: 2px solid #38bdf8;
            }
            QSlider::handle:horizontal:hover {
                background: #ffffff;
                border-color: #67e8f9;
            }
            """
        )

    def _terminate_qprocess(self, attr_name, wait_ms=700):
        process = getattr(self, attr_name, None)
        if process is None:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(wait_ms):
                process.kill()
                process.waitForFinished(1000)
        setattr(self, attr_name, None)

    def cleanup(self):
        if self._cleanup_started:
            return
        self._cleanup_started = True
        self._terminate_qprocess("ps_config_process")
        self._terminate_qprocess("ps_beam_process")
        self._terminate_qprocess("ps_advanced_config_process")
        self._terminate_qprocess("ps_usb_config_process")
        self._terminate_qprocess("ps_usb_beam_process")
        self.pipeline.stop()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "root_layout"):
            self._set_window_margin_for_state()
