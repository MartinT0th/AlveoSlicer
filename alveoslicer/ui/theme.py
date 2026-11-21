"""Dark CAD / slicer stylesheet and shared plot colors for AlveoSlicer GUI."""

from __future__ import annotations

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #16171a;
    color: #e4e6eb;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}
QTabWidget::pane {
    border: 1px solid #2c2f36;
    background-color: #1c1e22;
    border-radius: 4px;
}
QTabBar::tab {
    background-color: #16171a;
    color: #9aa0aa;
    padding: 7px 12px;
    border: 1px solid #2c2f36;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1c1e22;
    color: #ffffff;
    border-top: 2px solid #ff5722;
}
QGroupBox {
    border: 1px solid #2c2f36;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
    color: #ff7043;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
    background-color: #111215;
    border: 1px solid #32363e;
    border-radius: 4px;
    padding: 4px 6px;
    color: #f0f2f5;
}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {
    border: 1px solid #ff5722;
}
QPushButton {
    background-color: #272a31;
    border: 1px solid #3a3f4a;
    border-radius: 4px;
    padding: 6px 12px;
    color: #eef0f4;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #323640;
    border-color: #ff7043;
}
QPushButton:disabled {
    background-color: #1c1e22;
    color: #5c626e;
    border-color: #262930;
}
QPushButton#primaryBtn {
    background-color: #e64a19;
    border: 1px solid #ff7043;
    color: #ffffff;
    padding: 8px 14px;
    font-size: 13px;
}
QPushButton#primaryBtn:hover {
    background-color: #ff5722;
}
QProgressBar {
    border: 1px solid #2c2f36;
    border-radius: 4px;
    background-color: #111215;
    text-align: center;
    color: #ffffff;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #ff5722;
    border-radius: 3px;
}
QTableWidget {
    background-color: #111215;
    alternate-background-color: #181a1f;
    gridline-color: #262930;
    border: 1px solid #2c2f36;
}
QHeaderView::section {
    background-color: #22252b;
    color: #d8dce3;
    padding: 5px;
    border: 1px solid #2c2f36;
    font-weight: 600;
}
QPlainTextEdit {
    background-color: #111215;
    border: 1px solid #2c2f36;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 11px;
    color: #cfd5df;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2c2f36;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ff5722;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
"""
