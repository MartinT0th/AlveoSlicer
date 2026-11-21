"""Desktop GUI application bootstrap for AlveoSlicer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import AlveoSlicerWindow
from .theme import DARK_STYLESHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    win = AlveoSlicerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
