"""Theme system for AlveoSlicer supporting Dark, Light, and OS System modes.

Defines immutable ``ThemePalette`` color tokens for both Qt widgets and the
OpenGL viewport, OS dark/light auto-detection, persistent user preferences,
and a consolidated QSS stylesheet builder with rounded CAD controls and yellow accents.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

THEME_MODES: Tuple[str, ...] = ("dark", "light", "system")

# Legacy color constants retained for compatibility with helper modules
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


@dataclass(frozen=True)
class ThemePalette:
    """Semantic color token set shared by Qt stylesheets and the 3D OpenGL viewport."""

    mode: str
    bg_base: str
    bg_bar: str
    bg_surface: str
    bg_elevated: str
    bg_input: str
    bg_disabled: str
    border: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_muted: str
    text_disabled: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_fg: str
    accent_soft: str
    accent_border_soft: str
    overlay_bg: str
    overlay_border: str
    danger_bg: str
    danger_border: str
    danger_fg: str
    danger_hover_bg: str
    splitter_handle: str
    icon_fg: str
    icon_accent: str
    # OpenGL viewport colors (RGB floats in [0, 1])
    gl_bg: Tuple[float, float, float]
    gl_bed_plate: Tuple[float, float, float]
    gl_grid_minor: Tuple[float, float, float]
    gl_grid_major: Tuple[float, float, float]
    gl_grid_border: Tuple[float, float, float]
    gl_cage: Tuple[float, float, float]


DARK_PALETTE = ThemePalette(
    mode="dark",
    bg_base="#15171b",
    bg_bar="#101216",
    bg_surface="#1c1f26",
    bg_elevated="#252933",
    bg_input="#121419",
    bg_disabled="#181a20",
    border="#2e3340",
    border_strong="#404758",
    text_primary="#edf0f5",
    text_secondary="#c7cedb",
    text_muted="#8d96a8",
    text_disabled="#586070",
    accent="#f5c518",
    accent_hover="#ffd54f",
    accent_pressed="#d4a70a",
    accent_fg="#121316",
    accent_soft="rgba(245, 197, 24, 0.18)",
    accent_border_soft="rgba(245, 197, 24, 0.55)",
    overlay_bg="rgba(20, 22, 28, 232)",
    overlay_border="#3a4050",
    danger_bg="#2c1d21",
    danger_border="#5a323c",
    danger_fg="#f48b96",
    danger_hover_bg="#3f232a",
    splitter_handle="#292d38",
    icon_fg="#dfe4ee",
    icon_accent="#f5c518",
    gl_bg=(0.082, 0.088, 0.106),
    gl_bed_plate=(0.145, 0.158, 0.192),
    gl_grid_minor=(0.20, 0.22, 0.27),
    gl_grid_major=(0.31, 0.34, 0.42),
    gl_grid_border=(0.88, 0.72, 0.12),
    gl_cage=(0.24, 0.27, 0.34),
)

LIGHT_PALETTE = ThemePalette(
    mode="light",
    bg_base="#f1f3f7",
    bg_bar="#e5e9f0",
    bg_surface="#ffffff",
    bg_elevated="#eef1f6",
    bg_input="#ffffff",
    bg_disabled="#eaedf2",
    border="#d4dbe6",
    border_strong="#b4bfd0",
    text_primary="#181c24",
    text_secondary="#384050",
    text_muted="#5f6a7d",
    text_disabled="#9ca6b8",
    accent="#eab308",
    accent_hover="#f5c518",
    accent_pressed="#ca9a04",
    accent_fg="#141518",
    accent_soft="rgba(234, 179, 8, 0.18)",
    accent_border_soft="rgba(202, 154, 4, 0.65)",
    overlay_bg="rgba(255, 255, 255, 236)",
    overlay_border="#c4cddb",
    danger_bg="#fff1f2",
    danger_border="#f3b2b9",
    danger_fg="#c62828",
    danger_hover_bg="#ffe2e5",
    splitter_handle="#d5dce7",
    icon_fg="#273040",
    icon_accent="#c99300",
    gl_bg=(0.895, 0.912, 0.942),
    gl_bed_plate=(0.80, 0.83, 0.88),
    gl_grid_minor=(0.71, 0.75, 0.81),
    gl_grid_major=(0.56, 0.61, 0.69),
    gl_grid_border=(0.78, 0.58, 0.02),
    gl_cage=(0.64, 0.68, 0.76),
)


def detect_system_theme() -> str:
    """Detect the operating system color-scheme preference (``'dark'`` or ``'light'``)."""
    app = QGuiApplication.instance()
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return "light"
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
        except Exception:
            pass

    if sys.platform.startswith("win"):
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(val) == 1 else "dark"
        except Exception:
            pass
    return "dark"


def resolve_theme_mode(mode: str) -> str:
    """Resolve ``'dark'``, ``'light'``, or ``'system'`` into concrete ``'dark'`` or ``'light'``."""
    m = (mode or "dark").strip().lower()
    if m == "system":
        return detect_system_theme()
    return "light" if m == "light" else "dark"


def get_palette(mode: str) -> ThemePalette:
    """Return the concrete ``ThemePalette`` matching ``mode``."""
    return LIGHT_PALETTE if resolve_theme_mode(mode) == "light" else DARK_PALETTE


def _prefs_path() -> Path:
    """Return the path to the user's persisted UI preferences JSON file."""
    return Path.home() / ".alveoslicer" / "ui_prefs.json"


def load_theme_preference() -> str:
    """Load the persisted theme mode from disk, defaulting to ``'dark'``."""
    p = _prefs_path()
    try:
        if p.exists():
            m = str(json.loads(p.read_text(encoding="utf-8")).get("theme_mode", "dark")).lower()
            if m in THEME_MODES:
                return m
    except Exception:
        pass
    return "dark"


def save_theme_preference(mode: str) -> None:
    """Persist the chosen theme mode to ``~/.alveoslicer/ui_prefs.json``."""
    m = mode.lower() if mode.lower() in THEME_MODES else "dark"
    p = _prefs_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data["theme_mode"] = m
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def build_stylesheet(mode: str = "dark") -> str:
    """Compile the complete Qt QSS stylesheet for ``'dark'``, ``'light'``, or ``'system'`` mode."""
    p = get_palette(mode)
    is_light = p.mode == "light"
    accent_text = "#9e7400" if is_light else p.accent
    banner_hybrid_bg = "rgba(234, 179, 8, 0.16)" if is_light else "rgba(245, 197, 24, 0.15)"
    banner_hybrid_fg = "#8f6900" if is_light else "#f5c518"
    banner_fff_bg = "rgba(2, 132, 199, 0.12)" if is_light else "rgba(0, 212, 255, 0.13)"
    banner_fff_fg = "#0369a1" if is_light else "#4fdfff"
    banner_fff_bd = "rgba(2, 132, 199, 0.45)" if is_light else "rgba(0, 212, 255, 0.45)"
    scroll_handle = "#b8c2d2" if is_light else "#343947"
    table_alt = "#f7f9fc" if is_light else "#181b21"

    return f"""
/* ---- Base & Menus ---- */
QMainWindow, QWidget {{
    background-color: {p.bg_base}; color: {p.text_primary};
    font-family: "Segoe UI", "Inter", sans-serif; font-size: 12px;
}}
QMenuBar {{
    background-color: {p.bg_bar}; color: {p.text_primary};
    border-bottom: 1px solid {p.border}; padding: 2px 6px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background-color: {p.bg_elevated}; color: {accent_text}; }}
QMenu {{
    background-color: {p.bg_surface}; border: 1px solid {p.border};
    border-radius: 6px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {p.accent_soft}; color: {accent_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 6px; }}

/* ---- Tabs (PrusaSlicer Main Bar & Generic) ---- */
QTabWidget#mainTabs::pane {{ border: none; background-color: {p.bg_base}; }}
QTabWidget#mainTabs > QTabBar {{ background-color: {p.bg_bar}; border-bottom: 1px solid {p.border}; }}
QTabWidget#mainTabs > QTabBar::tab {{
    background-color: {p.bg_bar}; color: {p.text_muted};
    padding: 9px 16px; margin-right: 3px; margin-top: 4px;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    font-weight: 600; font-size: 12px;
}}
QTabWidget#mainTabs > QTabBar::tab:hover {{ color: {p.text_primary}; background-color: {p.bg_surface}; }}
QTabWidget#mainTabs > QTabBar::tab:selected {{
    background-color: {p.bg_base}; color: {accent_text};
    border-color: {p.border}; border-top: 2px solid {p.accent};
}}
QTabWidget::pane {{ border: 1px solid {p.border}; background-color: {p.bg_surface}; border-radius: 6px; }}
QTabBar::tab {{
    background-color: {p.bg_base}; color: {p.text_muted}; padding: 6px 12px;
    border: 1px solid {p.border}; border-bottom: none;
    border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 2px;
}}
QTabBar::tab:selected {{ background-color: {p.bg_surface}; color: {accent_text}; border-top: 2px solid {p.accent}; }}

/* ---- Splitters ---- */
QSplitter::handle {{ background-color: {p.splitter_handle}; border-radius: 2px; }}
QSplitter::handle:horizontal {{ width: 4px; margin: 4px 1px; }}
QSplitter::handle:vertical {{ height: 4px; margin: 1px 4px; }}
QSplitter::handle:hover {{ background-color: {p.accent}; }}

/* ---- Cards, Floating Viewport Rails & GroupBoxes ---- */
QFrame#card, QFrame#presetBar, QFrame#toolStrip {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 6px;
}}
QFrame#floatingCard {{
    background-color: {p.overlay_bg}; border: 1px solid {p.overlay_border}; border-radius: 6px;
}}
QFrame#viewportRail, QFrame#viewportTopBar {{
    background-color: {p.overlay_bg}; border: 1px solid {p.overlay_border}; border-radius: 8px;
}}
QFrame#viewportPopover {{
    background-color: {p.overlay_bg}; border: 1px solid {p.accent_border_soft}; border-radius: 8px;
}}
QGroupBox {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 6px;
    margin-top: 12px; padding: 12px 10px 10px 10px; font-weight: 600; color: {accent_text};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 6px;
    background-color: {p.bg_surface}; border-radius: 3px;
}}

/* ---- Lists (Settings Categories & Plater Objects) ---- */
QListWidget#categoryList {{
    background-color: {p.bg_surface}; border: 1px solid {p.border};
    border-radius: 6px; padding: 6px; outline: none;
}}
QListWidget#categoryList::item {{
    padding: 9px 10px; margin: 2px 0; border-radius: 5px;
    color: {p.text_secondary}; font-weight: 500;
}}
QListWidget#categoryList::item:hover {{ background-color: {p.bg_elevated}; color: {p.text_primary}; }}
QListWidget#categoryList::item:selected {{
    background-color: {p.accent_soft}; color: {accent_text};
    border-left: 3px solid {p.accent}; font-weight: 600;
}}
QListWidget#objectList {{
    background-color: {p.bg_input}; border: 1px solid {p.border};
    border-radius: 5px; padding: 3px; outline: none;
}}
QListWidget#objectList::item {{ padding: 5px 8px; margin: 1px 0; border-radius: 4px; color: {p.text_primary}; }}
QListWidget#objectList::item:hover {{ background-color: {p.bg_elevated}; }}
QListWidget#objectList::item:selected {{ background-color: {p.accent_soft}; color: {accent_text}; font-weight: 600; }}

/* ---- Inputs & Checkboxes ---- */
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
    background-color: {p.bg_input}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 4px 8px; color: {p.text_primary}; min-height: 18px;
}}
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QLineEdit:hover,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{ border: 1px solid {p.accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    background-color: {p.bg_disabled}; color: {p.text_disabled}; border-color: {p.border};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 5px;
    selection-background-color: {p.accent_soft}; selection-color: {accent_text}; outline: none;
}}
QCheckBox {{ spacing: 7px; color: {p.text_primary}; background-color: transparent; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {p.border_strong}; background-color: {p.bg_input};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background-color: {p.accent}; border-color: {p.accent}; }}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 6px 12px; color: {p.text_primary}; font-weight: 600;
}}
QPushButton:hover {{ background-color: {p.bg_surface}; border-color: {p.accent}; color: {accent_text}; }}
QPushButton:pressed {{ background-color: {p.accent_soft}; }}
QPushButton:disabled {{ background-color: {p.bg_disabled}; color: {p.text_disabled}; border-color: {p.border}; }}

QPushButton#primaryBtn {{
    background-color: {p.accent}; border: 1px solid {p.accent}; border-radius: 6px;
    color: {p.accent_fg}; padding: 9px 16px; font-size: 13px; font-weight: 700;
}}
QPushButton#primaryBtn:hover {{ background-color: {p.accent_hover}; border-color: {p.accent_hover}; color: {p.accent_fg}; }}
QPushButton#primaryBtn:pressed {{ background-color: {p.accent_pressed}; }}
QPushButton#primaryBtn:disabled {{ background-color: {p.bg_disabled}; border-color: {p.border}; color: {p.text_disabled}; }}

QPushButton#viewportToolBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px; min-width: 34px; min-height: 34px; color: {p.text_secondary};
}}
QPushButton#viewportPillBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 4px 7px; min-height: 24px; font-size: 11px; font-weight: 600; color: {p.text_secondary};
}}
QPushButton#toolBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 6px 5px; font-size: 11px; color: {p.text_secondary};
}}
QPushButton#viewportToolBtn:hover, QPushButton#viewportPillBtn:hover, QPushButton#toolBtn:hover {{
    background-color: {p.bg_elevated}; border-color: {p.border_strong}; color: {accent_text};
}}
QPushButton#viewportPillBtn:checked, QPushButton#toolBtn:checked, QPushButton#iconSmallBtn:checked {{
    background-color: {p.accent_soft}; border: 1px solid {p.accent}; color: {accent_text}; font-weight: 700;
}}
QPushButton#viewportToolBtn:checked, QPushButton#plateTab:checked, QPushButton#themeBtn:checked, QPushButton#viewToggle:checked {{
    background-color: {p.accent}; border: 1px solid {p.accent}; color: {p.accent_fg}; font-weight: 700;
}}
QPushButton#plateTab {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong}; border-radius: 5px;
    padding: 4px 14px; min-width: 90px; font-size: 11px; color: {p.text_secondary}; font-weight: 700;
}}
QPushButton#plateTab:hover, QPushButton#viewToggle:hover {{ border-color: {p.accent}; color: {p.text_primary}; }}
QPushButton#iconSmallBtn {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 4px 8px; min-width: 24px; color: {p.text_secondary};
}}
QPushButton#iconSmallBtn:hover {{ border-color: {p.accent}; color: {accent_text}; }}
QPushButton#themeBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 4px;
    padding: 3px 8px; font-size: 11px; font-weight: 600; color: {p.text_muted};
}}
QPushButton#themeBtn:hover {{ background-color: {p.bg_elevated}; color: {p.text_primary}; }}
QPushButton#viewToggle {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 6px 12px; color: {p.text_secondary}; font-weight: 600;
}}
QPushButton#dangerBtn {{ background-color: {p.danger_bg}; border: 1px solid {p.danger_border}; color: {p.danger_fg}; }}
QPushButton#dangerBtn:hover {{ background-color: {p.danger_hover_bg}; border-color: #ef5350; color: {p.danger_fg}; }}

/* ---- Progress, Tables, Code Views & Sliders ---- */
QProgressBar {{
    border: 1px solid {p.border}; border-radius: 5px; background-color: {p.bg_input};
    text-align: center; color: {p.text_primary}; height: 18px; font-weight: 600;
}}
QProgressBar::chunk {{ background-color: {p.accent}; border-radius: 4px; }}
QTableWidget {{
    background-color: {p.bg_input}; alternate-background-color: {table_alt};
    gridline-color: {p.border}; border: 1px solid {p.border}; border-radius: 5px;
}}
QTableWidget::item:selected {{ background-color: {p.accent_soft}; color: {accent_text}; }}
QHeaderView::section {{
    background-color: {p.bg_elevated}; color: {p.text_primary};
    padding: 6px; border: 1px solid {p.border}; font-weight: 600;
}}
QPlainTextEdit {{
    background-color: {p.bg_input}; border: 1px solid {p.border}; border-radius: 5px;
    font-family: "Consolas", "JetBrains Mono", monospace; font-size: 11px; color: {p.text_primary};
}}
QSlider::groove:horizontal {{ height: 6px; background: {p.border}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {p.accent}; border: 1px solid {p.accent_fg};
    width: 14px; height: 14px; margin: -4px 0; border-radius: 5px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent_hover}; }}

/* ---- Scrollbars ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {p.bg_base}; width: 9px; margin: 2px; border-radius: 4px; }}
QScrollBar:horizontal {{ background: {p.bg_base}; height: 9px; margin: 2px; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background: {scroll_handle}; min-height: 28px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background: {scroll_handle}; min-width: 28px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {p.accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ---- Labels & Badges ---- */
QLabel {{ background-color: transparent; }}
QLabel#sectionTitle {{ font-size: 12px; font-weight: 700; color: {accent_text}; }}
QLabel#mutedLabel {{ color: {p.text_muted}; font-size: 11px; }}
QLabel#accentValue {{ color: {accent_text}; font-weight: 700; font-size: 11px; }}
QLabel#modifiedPill {{
    background-color: {p.accent_soft}; color: {accent_text};
    border: 1px solid {p.accent_border_soft}; border-radius: 4px;
    padding: 2px 8px; font-weight: 700; font-size: 11px;
}}
QLabel#modeBannerHybrid {{
    background-color: {banner_hybrid_bg}; border: 1px solid {p.accent_border_soft};
    border-radius: 5px; padding: 6px 10px; color: {banner_hybrid_fg}; font-weight: 700;
}}
QLabel#modeBannerFFF {{
    background-color: {banner_fff_bg}; border: 1px solid {banner_fff_bd};
    border-radius: 5px; padding: 6px 10px; color: {banner_fff_fg}; font-weight: 700;
}}
"""


DARK_STYLESHEET = build_stylesheet("dark")
LIGHT_STYLESHEET = build_stylesheet("light")
