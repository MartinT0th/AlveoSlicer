"""Desktop GUI application bootstrap for AlveoSlicer with startup splash screen."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QElapsedTimer, QEventLoop, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..assets import get_app_icon_path
from .splash import AlveoSplashScreen


def _configure_windows_app_id() -> None:
    """Assign an explicit Windows AppUserModelID so the taskbar displays the AlveoSlicer icon."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlveoSlicer.Desktop.App")
        except Exception:
            pass


def _pump_events(app: QApplication, ticks: int = 3) -> None:
    """Process pending Qt paint and timer events so the splash throbber animates smoothly."""
    for _ in range(ticks):
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)


def main() -> None:
    """Create the Qt application, display the splash screen, and launch :class:`AlveoSlicerWindow`."""
    _configure_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AlveoSlicer")
    app.setOrganizationName("AlveoSlicer")

    icon_path = get_app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    splash = AlveoSplashScreen()
    splash.show()
    elapsed = QElapsedTimer()
    elapsed.start()
    _pump_events(app, ticks=4)

    splash.set_status("Loading 3D geometry & slicing engine...")
    _pump_events(app)
    from .styling.theme import DARK_STYLESHEET

    app.setStyleSheet(DARK_STYLESHEET)

    splash.set_status("Loading workspace & viewport...")
    _pump_events(app)
    from .main_window import AlveoSlicerWindow

    splash.set_status("Preparing build plates & presets...")
    _pump_events(app)
    win = AlveoSlicerWindow()

    splash.set_status("Ready")
    _pump_events(app)

    # Keep splash visible briefly (~850 ms minimum) so it never flashes abruptly on fast boots
    while elapsed.elapsed() < 850:
        _pump_events(app, ticks=2)
        QThread.msleep(20)

    win.show()
    splash.finish(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
