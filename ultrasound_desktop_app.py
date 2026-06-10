import argparse
import multiprocessing as mp
import sys

from PySide6.QtWidgets import QApplication

from ultrasound_gui import UltrasoundMainWindow, get_app_icon


def configure_windows_taskbar_icon():
    """Give Windows a stable app id so the taskbar uses the Qt window icon."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        app_id = "UltraVision.Workstation.RealTimeImaging"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # Icon setup should never prevent the imaging workstation from starting.
        pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="PySide6 desktop workstation for real-time ultrasound imaging."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with synthetic ultrasound frames instead of USB hardware.",
    )
    return parser.parse_args()


def main():
    mp.freeze_support()
    configure_windows_taskbar_icon()
    QApplication.setApplicationName("UltraVision Workstation")
    QApplication.setOrganizationName("UltraVision")
    args = parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("UltraVision Workstation")
    app.setOrganizationName("UltraVision")
    icon = get_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = UltrasoundMainWindow(demo_mode=args.demo)
    if not icon.isNull():
        window.setWindowIcon(icon)
    app.aboutToQuit.connect(window.cleanup)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
