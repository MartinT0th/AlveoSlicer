"""Slick PrusaSlicer-inspired dark CAD stylesheet with yellow accents and small rounded corners."""

from __future__ import annotations

# Core accent palette (Yellow replaces orange everywhere)
ACCENT_YELLOW = "#f5c518"
ACCENT_YELLOW_HOVER = "#ffd54f"
ACCENT_YELLOW_PRESSED = "#d4a70a"
ACCENT_CYAN = "#00d4ff"
BG_BASE = "#17181c"
BG_SURFACE = "#1e2026"
BG_ELEVATED = "#262932"
BORDER_SUBTLE = "#313542"
TEXT_PRIMARY = "#eceff4"
TEXT_MUTED = "#9aa2b1"

DARK_STYLESHEET = f"""
/* ---- Base ---- */
QMainWindow, QWidget {{
    background-color: {BG_BASE};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}}

QMenuBar {{
    background-color: #131418;
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid #262932;
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {BG_ELEVATED};
    color: {ACCENT_YELLOW};
}}
QMenu {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: rgba(245, 197, 24, 0.16);
    color: {ACCENT_YELLOW};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_SUBTLE};
    margin: 4px 6px;
}}

/* ---- Top-Level PrusaSlicer Tab Bar ---- */
QTabWidget#mainTabs::pane {{
    border: none;
    background-color: {BG_BASE};
}}
QTabWidget#mainTabs > QTabBar {{
    background-color: #131418;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QTabWidget#mainTabs > QTabBar::tab {{
    background-color: #131418;
    color: {TEXT_MUTED};
    padding: 9px 18px;
    margin-right: 3px;
    margin-top: 4px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: 600;
    font-size: 12px;
}}
QTabWidget#mainTabs > QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background-color: {BG_SURFACE};
}}
QTabWidget#mainTabs > QTabBar::tab:selected {{
    background-color: {BG_BASE};
    color: {ACCENT_YELLOW};
    border-color: {BORDER_SUBTLE};
    border-top: 2px solid {ACCENT_YELLOW};
}}

/* ---- Generic Tabs ---- */
QTabWidget::pane {{
    border: 1px solid {BORDER_SUBTLE};
    background-color: {BG_SURFACE};
    border-radius: 5px;
}}
QTabBar::tab {{
    background-color: {BG_BASE};
    color: {TEXT_MUTED};
    padding: 6px 12px;
    border: 1px solid {BORDER_SUBTLE};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {BG_SURFACE};
    color: {ACCENT_YELLOW};
    border-top: 2px solid {ACCENT_YELLOW};
}}

/* ---- Cards & GroupBoxes ---- */
QFrame#card, QFrame#floatingCard {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
}}
QFrame#floatingCard {{
    background-color: rgba(25, 27, 33, 232);
    border: 1px solid #3a3f4e;
    border-radius: 6px;
}}
QFrame#presetBar {{
    background-color: {BG_SURFACE};
    border-bottom: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
}}
QFrame#toolStrip {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
}}

QGroupBox {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: {ACCENT_YELLOW};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    background-color: {BG_SURFACE};
    border-radius: 3px;
}}

/* ---- Settings Category List (PrusaSlicer Left Pane) ---- */
QListWidget#categoryList {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    padding: 6px;
    outline: none;
}}
QListWidget#categoryList::item {{
    padding: 9px 12px;
    margin: 2px 0;
    border-radius: 5px;
    color: #c5cbd6;
    font-weight: 500;
}}
QListWidget#categoryList::item:hover {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
}}
QListWidget#categoryList::item:selected {{
    background-color: rgba(245, 197, 24, 0.16);
    color: {ACCENT_YELLOW};
    border-left: 3px solid {ACCENT_YELLOW};
    font-weight: 600;
}}

/* ---- Inputs ---- */
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
    background-color: #131418;
    border: 1px solid #333846;
    border-radius: 5px;
    padding: 5px 8px;
    color: #f3f5f8;
    min-height: 18px;
}}
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QLineEdit:hover {{
    border-color: #4a5164;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border: 1px solid {ACCENT_YELLOW};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    background-color: #181a20;
    color: #5c6373;
    border-color: #252831;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
    selection-background-color: rgba(245, 197, 24, 0.22);
    selection-color: {ACCENT_YELLOW};
    outline: none;
}}

/* ---- Checkboxes ---- */
QCheckBox {{
    spacing: 7px;
    color: {TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #484f60;
    background-color: #131418;
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT_YELLOW};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_YELLOW};
    border-color: {ACCENT_YELLOW};
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {BG_ELEVATED};
    border: 1px solid #3c4252;
    border-radius: 5px;
    padding: 6px 12px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #303441;
    border-color: {ACCENT_YELLOW};
    color: #ffffff;
}}
QPushButton:pressed {{
    background-color: #21242c;
}}
QPushButton:disabled {{
    background-color: #1c1e24;
    color: #585f6e;
    border-color: #272a33;
}}

QPushButton#primaryBtn {{
    background-color: {ACCENT_YELLOW};
    border: 1px solid {ACCENT_YELLOW};
    border-radius: 5px;
    color: #141518;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 700;
}}
QPushButton#primaryBtn:hover {{
    background-color: {ACCENT_YELLOW_HOVER};
    border-color: {ACCENT_YELLOW_HOVER};
    color: #101114;
}}
QPushButton#primaryBtn:pressed {{
    background-color: {ACCENT_YELLOW_PRESSED};
}}
QPushButton#primaryBtn:disabled {{
    background-color: #423c24;
    border-color: #423c24;
    color: #8c8462;
}}

QPushButton#toolBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 7px 6px;
    font-size: 11px;
    color: #cfd5df;
}}
QPushButton#toolBtn:hover {{
    background-color: {BG_ELEVATED};
    border-color: {BORDER_SUBTLE};
    color: {ACCENT_YELLOW};
}}

QPushButton#iconSmallBtn {{
    background-color: {BG_ELEVATED};
    border: 1px solid #383e4d;
    border-radius: 5px;
    padding: 4px 7px;
    min-width: 24px;
    color: #d5dbe6;
}}
QPushButton#iconSmallBtn:hover {{
    border-color: {ACCENT_YELLOW};
    color: {ACCENT_YELLOW};
}}

QPushButton#viewToggle {{
    background-color: rgba(30, 32, 38, 225);
    border: 1px solid #3b4050;
    border-radius: 5px;
    padding: 6px 12px;
    color: #c7cdd8;
    font-weight: 600;
}}
QPushButton#viewToggle:hover {{
    border-color: {ACCENT_YELLOW};
    color: #ffffff;
}}
QPushButton#viewToggle:checked {{
    background-color: {ACCENT_YELLOW};
    border-color: {ACCENT_YELLOW};
    color: #141518;
}}

QPushButton#dangerBtn {{
    background-color: #2c1e22;
    border: 1px solid #59333c;
    color: #f28b96;
}}
QPushButton#dangerBtn:hover {{
    background-color: #3d242a;
    border-color: #ef5350;
    color: #ffffff;
}}

/* ---- Progress Bar ---- */
QProgressBar {{
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
    background-color: #131418;
    text-align: center;
    color: #ffffff;
    height: 18px;
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: {ACCENT_YELLOW};
    border-radius: 4px;
}}

/* ---- Tables & Code Views ---- */
QTableWidget {{
    background-color: #131418;
    alternate-background-color: #1a1c22;
    gridline-color: #272b35;
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
}}
QTableWidget::item:selected {{
    background-color: rgba(245, 197, 24, 0.22);
    color: {ACCENT_YELLOW};
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: #dce1ea;
    padding: 6px;
    border: 1px solid {BORDER_SUBTLE};
    font-weight: 600;
}}
QPlainTextEdit {{
    background-color: #131418;
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 5px;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 11px;
    color: #cfd5df;
}}

/* ---- Sliders ---- */
QSlider::groove:horizontal {{
    height: 6px;
    background: #2c303c;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT_YELLOW};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT_YELLOW};
    border: 1px solid #141518;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 5px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_YELLOW_HOVER};
}}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #383e4d;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #525a6e;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #383e4d;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QLabel#sectionTitle {{
    font-size: 13px;
    font-weight: 700;
    color: {ACCENT_YELLOW};
}}
QLabel#mutedLabel {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#modeBannerHybrid {{
    background-color: rgba(0, 212, 255, 0.12);
    border: 1px solid rgba(0, 212, 255, 0.45);
    border-radius: 5px;
    color: #7ee8ff;
    padding: 8px 10px;
    font-weight: 600;
}}
QLabel#modeBannerFFF {{
    background-color: rgba(245, 197, 24, 0.14);
    border: 1px solid rgba(245, 197, 24, 0.55);
    border-radius: 5px;
    color: {ACCENT_YELLOW};
    padding: 8px 10px;
    font-weight: 600;
}}
"""
