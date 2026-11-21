"""Desktop GUI application bootstrap for AlveoSlicer."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..assets import get_app_icon_path
from .main_window import AlveoSlicerWindow
from .styling.theme import DARK_STYLESHEET


def _configure_windows_app_id() -> None:
    """Assign an explicit Windows AppUserModelID so the taskbar displays the AlveoSlicer icon."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlveoSlicer.Desktop.App")
        except Exception:
            pass


def main() -> None:
    """Create the Qt application, bind the AlveoSlicer icon, and run :class:`AlveoSlicerWindow`."""
    _configure_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AlveoSlicer")
    app.setOrganizationName("AlveoSlicer")

    icon_path = get_app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    app.setStyleSheet(DARK_STYLESHEET)
    win = AlveoSlicerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
