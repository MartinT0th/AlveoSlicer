"""Pipeline log and G-code preview tab widget."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class ConsoleGCodeView(QWidget):
    """Displays live pipeline logs alongside a preview of the generated G-code."""

    def __init__(self, preview_line_limit: int = 250, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._preview_line_limit = preview_line_limit

        vbox = QVBoxLayout(self)

        vbox.addWidget(QLabel("Slicer Pipeline Log:"))
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        vbox.addWidget(self.txt_log, stretch=1)

        vbox.addWidget(QLabel(f"G-Code Preview (first {preview_line_limit} lines):"))
        self.txt_gcode = QPlainTextEdit()
        self.txt_gcode.setReadOnly(True)
        vbox.addWidget(self.txt_gcode, stretch=1)

    def append_log(self, msg: str) -> None:
        self.txt_log.appendPlainText(msg)

    def clear_log(self) -> None:
        self.txt_log.clear()

    def set_gcode_preview(self, gcode_text: str) -> None:
        preview = "\n".join(gcode_text.splitlines()[: self._preview_line_limit])
        self.txt_gcode.setPlainText(preview)
