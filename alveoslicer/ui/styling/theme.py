"""Theme system for AlveoSlicer supporting Dark, Light, and OS System modes.

Defines immutable ``ThemePalette`` color tokens for both Qt widgets and the
OpenGL viewport, OS dark/light auto-detection, persistent user preferences,
and a consolidated QSS stylesheet builder.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from ...assets import _assets_root

THEME_MODES: Tuple[str, ...] = ("dark", "light", "system")
EXPERIENCE_MODES: Tuple[str, ...] = ("simple", "advanced")

# Legacy color constants retained for compatibility with external imports
ACCENT_YELLOW = "#f5b82e"
ACCENT_YELLOW_HOVER = "#fbc950"
ACCENT_YELLOW_PRESSED = "#d99b18"
ACCENT_CYAN = "#818cf8"
BG_BASE = "#0e1217"
BG_SURFACE = "#151b23"
BG_ELEVATED = "#1c2430"
BORDER_SUBTLE = "#222b37"
TEXT_PRIMARY = "#e8eef5"
TEXT_MUTED = "#8797ab"


@dataclass(frozen=True)
class ThemePalette:
    """Semantic color token set shared by Qt stylesheets, plots, and the 3D OpenGL viewport."""

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
    uv_accent: str
    uv_fg: str
    uv_soft: str
    uv_border: str
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
    gl_accent: Tuple[float, float, float]
    gl_uv: Tuple[float, float, float]
    gl_model_default: Tuple[float, float, float]
    gl_model_selected: Tuple[float, float, float]


DARK_PALETTE = ThemePalette(
    mode="dark",
    bg_base="#0e1217",
    bg_bar="#090c10",
    bg_surface="#151b23",
    bg_elevated="#1c2430",
    bg_input="#0b0f14",
    bg_disabled="#12171e",
    border="#222b37",
    border_strong="#313e50",
    text_primary="#e8eef5",
    text_secondary="#b6c3d2",
    text_muted="#8797ab",
    text_disabled="#556275",
    accent="#f5b82e",
    accent_hover="#fbc950",
    accent_pressed="#d99b18",
    accent_fg="#120e04",
    accent_soft="rgba(245, 184, 46, 0.14)",
    accent_border_soft="rgba(245, 184, 46, 0.48)",
    uv_accent="#818cf8",
    uv_fg="#a5b4fc",
    uv_soft="rgba(129, 140, 248, 0.16)",
    uv_border="rgba(129, 140, 248, 0.48)",
    overlay_bg="rgba(15, 20, 27, 234)",
    overlay_border="#2a3545",
    danger_bg="#2b171c",
    danger_border="#5d2d39",
    danger_fg="#f896a0",
    danger_hover_bg="#3d1e26",
    splitter_handle="#1e2733",
    icon_fg="#cfd9e6",
    icon_accent="#f5b82e",
    gl_bg=(0.055, 0.071, 0.090),
    gl_bed_plate=(0.105, 0.132, 0.168),
    gl_grid_minor=(0.155, 0.192, 0.240),
    gl_grid_major=(0.245, 0.298, 0.365),
    gl_grid_border=(0.961, 0.722, 0.180),
    gl_cage=(0.185, 0.230, 0.288),
    gl_accent=(0.961, 0.722, 0.180),
    gl_uv=(0.506, 0.549, 0.973),
    gl_model_default=(0.62, 0.57, 0.44),
    gl_model_selected=(0.961, 0.722, 0.180),
)

LIGHT_PALETTE = ThemePalette(
    mode="light",
    bg_base="#f2f5f8",
    bg_bar="#e6ecf2",
    bg_surface="#ffffff",
    bg_elevated="#ebf0f5",
    bg_input="#ffffff",
    bg_disabled="#e9eef3",
    border="#d2dce6",
    border_strong="#b2c2d3",
    text_primary="#131a24",
    text_secondary="#354355",
    text_muted="#55667a",
    text_disabled="#94a3b5",
    accent="#e5a415",
    accent_hover="#f5b82e",
    accent_pressed="#c78a0a",
    accent_fg="#161206",
    accent_soft="rgba(229, 164, 21, 0.15)",
    accent_border_soft="rgba(180, 122, 6, 0.52)",
    uv_accent="#4f46e5",
    uv_fg="#4338ca",
    uv_soft="rgba(79, 70, 229, 0.11)",
    uv_border="rgba(79, 70, 229, 0.42)",
    overlay_bg="rgba(255, 255, 255, 238)",
    overlay_border="#c2d0de",
    danger_bg="#fff1f2",
    danger_border="#f1b4bc",
    danger_fg="#be123c",
    danger_hover_bg="#ffe4e6",
    splitter_handle="#cdd8e3",
    icon_fg="#273445",
    icon_accent="#b57c04",
    gl_bg=(0.915, 0.935, 0.955),
    gl_bed_plate=(0.825, 0.858, 0.892),
    gl_grid_minor=(0.735, 0.778, 0.825),
    gl_grid_major=(0.575, 0.632, 0.695),
    gl_grid_border=(0.85, 0.60, 0.06),
    gl_cage=(0.640, 0.695, 0.755),
    gl_accent=(0.85, 0.60, 0.06),
    gl_uv=(0.310, 0.275, 0.898),
    gl_model_default=(0.55, 0.50, 0.40),
    gl_model_selected=(0.90, 0.66, 0.11),
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


def _read_prefs() -> dict:
    p = _prefs_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_prefs(updates: dict) -> None:
    p = _prefs_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _read_prefs()
        data.update(updates)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_theme_preference() -> str:
    """Load the persisted theme mode from disk, defaulting to ``'dark'``."""
    m = str(_read_prefs().get("theme_mode", "dark")).lower()
    return m if m in THEME_MODES else "dark"


def save_theme_preference(mode: str) -> None:
    """Persist the chosen theme mode to ``~/.alveoslicer/ui_prefs.json``."""
    m = mode.lower() if mode.lower() in THEME_MODES else "dark"
    _write_prefs({"theme_mode": m})


def load_experience_mode() -> str:
    """Load the persisted settings experience level (``'simple'`` or ``'advanced'``)."""
    m = str(_read_prefs().get("experience_mode", "simple")).lower()
    return m if m in EXPERIENCE_MODES else "simple"


def save_experience_mode(mode: str) -> None:
    """Persist the settings experience level to ``~/.alveoslicer/ui_prefs.json``."""
    m = mode.lower() if mode.lower() in EXPERIENCE_MODES else "simple"
    _write_prefs({"experience_mode": m})


def build_stylesheet(mode: str = "dark") -> str:
    """Compile the complete Qt QSS stylesheet for ``'dark'``, ``'light'``, or ``'system'`` mode."""
    p = get_palette(mode)
    is_light = p.mode == "light"
    accent_text = "#9a6700" if is_light else p.accent
    scroll_handle = "#b8c5d4" if is_light else "#2e3948"
    table_alt = "#f7f9fc" if is_light else "#12171f"
    sans_font = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
    mono_font = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'
    check_svg = (_assets_root() / "check_dark.svg").as_posix()
    chev_down = (_assets_root() / "chevron_down.svg").as_posix()
    chev_up = (_assets_root() / "chevron_up.svg").as_posix()

    return f"""
/* ---- Base & Menus ---- */
QMainWindow, QWidget {{
    background-color: {p.bg_base}; color: {p.text_primary};
    font-family: {sans_font}; font-size: 12px;
}}
QMenuBar {{
    background-color: {p.bg_bar}; color: {p.text_primary};
    border-bottom: 1px solid {p.border}; padding: 3px 8px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; }}
QMenuBar::item:selected {{ background-color: {p.bg_elevated}; color: {accent_text}; }}
QMenu {{
    background-color: {p.bg_surface}; border: 1px solid {p.border_strong};
    border-radius: 8px; padding: 5px;
}}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {p.accent_soft}; color: {accent_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
QToolTip {{
    background-color: {p.bg_surface}; color: {p.text_primary};
    border: 1px solid {p.border_strong}; border-radius: 6px; padding: 5px 8px;
}}

/* ---- Tabs (Main Bar & Generic) ---- */
QTabWidget#mainTabs::pane {{ border: none; background-color: {p.bg_base}; }}
QTabWidget#mainTabs > QTabBar {{ background-color: {p.bg_bar}; border-bottom: 1px solid {p.border}; }}
QTabWidget#mainTabs > QTabBar::tab {{
    background-color: transparent; color: {p.text_muted};
    padding: 9px 15px; margin-right: 2px; margin-top: 4px;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
    font-weight: 600; font-size: 12px;
}}
QTabWidget#mainTabs > QTabBar::tab:hover {{ color: {p.text_primary}; background-color: {p.bg_surface}; }}
QTabWidget#mainTabs > QTabBar::tab:selected {{
    background-color: {p.bg_base}; color: {p.text_primary};
    border-color: {p.border}; border-top: 2px solid {p.accent};
}}
QTabWidget::pane {{ border: 1px solid {p.border}; background-color: {p.bg_surface}; border-radius: 8px; }}
QTabBar::tab {{
    background-color: {p.bg_base}; color: {p.text_muted}; padding: 6px 12px;
    border: 1px solid {p.border}; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;
}}
QTabBar::tab:selected {{ background-color: {p.bg_surface}; color: {accent_text}; border-top: 2px solid {p.accent}; }}

/* ---- Splitters ---- */
QSplitter::handle {{ background-color: {p.splitter_handle}; border-radius: 2px; }}
QSplitter::handle:horizontal {{ width: 3px; margin: 4px 1px; }}
QSplitter::handle:vertical {{ height: 3px; margin: 1px 4px; }}
QSplitter::handle:hover {{ background-color: {p.accent}; }}

/* ---- Cards, Tonal Shelves & Floating Viewport Overlays ---- */
QFrame#card, QFrame#sidebarCard, QFrame#presetBar, QFrame#toolStrip {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 8px;
}}
QFrame#settingsCard {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 8px;
}}
QFrame#settingsDivider {{
    background-color: {p.border}; max-height: 1px; min-height: 1px; border: none;
}}
QFrame#segmentedPill {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 7px;
}}
QFrame#statTile {{
    background-color: {p.bg_input}; border: 1px solid {p.border}; border-radius: 6px;
}}
QWidget#cardInner {{ background-color: transparent; }}
QFrame#floatingCard {{
    background-color: {p.overlay_bg}; border: 1px solid {p.overlay_border}; border-radius: 8px;
}}
QFrame#viewportRail, QFrame#viewportTopBar {{
    background-color: {p.overlay_bg}; border: 1px solid {p.overlay_border}; border-radius: 9px;
}}
QFrame#viewportPopover {{
    background-color: {p.overlay_bg}; border: 1px solid {p.accent_border_soft}; border-radius: 9px;
}}
QGroupBox {{
    background-color: {p.bg_surface}; border: 1px solid {p.border}; border-radius: 8px;
    margin-top: 12px; padding: 14px 12px 12px 12px; font-weight: 600; color: {p.text_primary};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 6px;
    background-color: {p.bg_surface}; border-radius: 3px;
}}

/* ---- Lists (Settings Categories & Plater Objects) ---- */
QListWidget#categoryList {{
    background-color: {p.bg_surface}; border: 1px solid {p.border};
    border-radius: 8px; padding: 6px; outline: none;
}}
QListWidget#categoryList::item {{
    padding: 8px 10px; margin: 2px 0; border-radius: 6px;
    border: 1px solid transparent;
    color: {p.text_secondary}; font-weight: 500;
}}
QListWidget#categoryList::item:hover {{
    background-color: {p.bg_elevated}; color: {p.text_primary};
}}
QListWidget#categoryList::item:selected {{
    background-color: {p.accent_soft}; color: {accent_text};
    border: 1px solid {p.accent_border_soft}; font-weight: 600;
}}
QListWidget#objectList {{
    background-color: {p.bg_input}; border: 1px solid {p.border};
    border-radius: 6px; padding: 3px; outline: none;
}}
QListWidget#objectList::item {{ padding: 0px; margin: 1px 0; border-radius: 5px; color: {p.text_primary}; }}
QListWidget#objectList::item:hover {{ background-color: {p.bg_elevated}; }}
QListWidget#objectList::item:selected {{
    background-color: {p.accent_soft}; border: 1px solid {p.accent_border_soft}; color: {accent_text};
}}

/* ---- Inputs & Checkboxes ---- */
QSpinBox, QDoubleSpinBox {{
    background-color: {p.bg_input}; border: 1px solid {p.border_strong};
    border-radius: 6px; padding: 4px 24px 4px 8px; color: {p.text_primary}; min-height: 20px;
    font-family: {mono_font}; font-size: 11.5px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 19px; border-left: 1px solid {p.border}; border-bottom: 1px solid {p.border};
    border-top-right-radius: 5px; background-color: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 19px; border-left: 1px solid {p.border};
    border-bottom-right-radius: 5px; background-color: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {p.bg_elevated};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 9px; height: 9px;
    image: url("{chev_up}");
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 9px; height: 9px;
    image: url("{chev_down}");
}}
QComboBox, QLineEdit {{
    background-color: {p.bg_input}; border: 1px solid {p.border_strong};
    border-radius: 6px; padding: 4px 9px; color: {p.text_primary}; min-height: 20px;
}}
QComboBox {{ padding-right: 24px; }}
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QLineEdit:hover {{
    border: 1px solid {p.text_muted};
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border: 1px solid {p.accent};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    background-color: {p.bg_disabled}; color: {p.text_disabled}; border-color: {p.border};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 22px; border: none;
}}
QComboBox::down-arrow {{
    width: 10px; height: 10px;
    image: url("{chev_down}");
}}
QComboBox QAbstractItemView {{
    background-color: {p.bg_surface}; border: 1px solid {p.border_strong}; border-radius: 6px;
    selection-background-color: {p.accent_soft}; selection-color: {accent_text};
    outline: none; padding: 3px;
}}
QCheckBox {{ spacing: 8px; color: {p.text_primary}; background-color: transparent; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {p.border_strong}; background-color: {p.bg_input};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background-color: {p.accent}; border-color: {p.accent};
    image: url("{check_svg}");
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 6px; padding: 6px 12px; color: {p.text_primary}; font-weight: 600;
}}
QPushButton:hover {{ background-color: {p.bg_surface}; border-color: {p.accent}; color: {accent_text}; }}
QPushButton:pressed {{ background-color: {p.accent_soft}; }}
QPushButton:disabled {{ background-color: {p.bg_disabled}; color: {p.text_disabled}; border-color: {p.border}; }}

QPushButton#primaryBtn {{
    background-color: {p.accent}; border: 1px solid {p.accent}; border-radius: 7px;
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
    padding: 4px 8px; min-height: 24px; font-size: 11px; font-weight: 600; color: {p.text_secondary};
}}
QPushButton#toolBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 6px 5px; font-size: 11px; color: {p.text_secondary};
}}
QPushButton#viewportToolBtn:hover, QPushButton#viewportPillBtn:hover, QPushButton#toolBtn:hover {{
    background-color: {p.bg_elevated}; border-color: {p.border_strong}; color: {p.text_primary};
}}
QPushButton#viewportPillBtn:checked, QPushButton#toolBtn:checked, QPushButton#iconSmallBtn:checked {{
    background-color: {p.accent_soft}; border: 1px solid {p.accent}; color: {accent_text}; font-weight: 700;
}}
QPushButton#viewportToolBtn:checked, QPushButton#plateTab:checked, QPushButton#themeBtn:checked,
QPushButton#viewToggle:checked, QPushButton#modeBtn:checked {{
    background-color: {p.accent}; border: 1px solid {p.accent}; color: {p.accent_fg}; font-weight: 700;
}}
QPushButton#plateTab {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong}; border-radius: 6px;
    padding: 4px 12px; min-width: 82px; font-size: 11px; color: {p.text_secondary}; font-weight: 700;
}}
QPushButton#plateTab:hover, QPushButton#viewToggle:hover {{ border-color: {p.accent}; color: {p.text_primary}; }}
QPushButton#iconSmallBtn {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 3px 6px; min-width: 22px; color: {p.text_secondary};
}}
QPushButton#iconSmallBtn:hover {{ border-color: {p.accent}; color: {accent_text}; }}
QPushButton#cardIconBtn {{
    background-color: transparent; border: 1px solid transparent;
    border-radius: 5px; padding: 2px; min-width: 24px; min-height: 24px; color: {p.text_secondary};
}}
QPushButton#cardIconBtn:hover {{ background-color: {p.bg_elevated}; border-color: {p.border_strong}; color: {accent_text}; }}
QPushButton#cardIconBtn:checked {{ background-color: {p.accent_soft}; border-color: {p.accent}; color: {accent_text}; }}
QPushButton#cardIconBtn:disabled {{ color: {p.text_disabled}; background-color: transparent; border-color: transparent; }}
QPushButton#miniChipBtn {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 5px; padding: 2px 7px; font-size: 11px; font-weight: 600; color: {p.text_secondary};
}}
QPushButton#miniChipBtn:hover {{ border-color: {p.accent}; color: {accent_text}; }}
QPushButton#miniChipBtn:disabled {{ background-color: {p.bg_disabled}; color: {p.text_disabled}; border-color: {p.border}; }}
QPushButton#unitToggle {{
    background-color: {p.bg_input}; border: 1px solid {p.border_strong};
    border-radius: 4px; padding: 1px 6px; min-width: 24px; min-height: 18px;
    font-size: 10px; font-weight: 700; color: {p.text_muted};
}}
QPushButton#unitToggle:hover {{ border-color: {p.accent}; color: {p.text_primary}; }}
QPushButton#unitToggle:checked {{
    background-color: {p.accent}; border-color: {p.accent}; color: {p.accent_fg};
}}
QPushButton#themeBtn, QPushButton#modeBtn {{
    background-color: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 3px 8px; font-size: 11px; font-weight: 600; color: {p.text_muted};
}}
QPushButton#themeBtn:hover, QPushButton#modeBtn:hover {{
    background-color: {p.bg_elevated}; color: {p.text_primary};
}}
QPushButton#viewToggle {{
    background-color: {p.bg_elevated}; border: 1px solid {p.border_strong};
    border-radius: 6px; padding: 6px 12px; color: {p.text_secondary}; font-weight: 600;
}}
QPushButton#dangerBtn {{ background-color: {p.danger_bg}; border: 1px solid {p.danger_border}; color: {p.danger_fg}; }}
QPushButton#dangerBtn:hover {{ background-color: {p.danger_hover_bg}; border-color: #f43f5e; color: {p.danger_fg}; }}

/* ---- Progress, Tables, Code Views & Sliders ---- */
QProgressBar {{
    border: 1px solid {p.border}; border-radius: 5px; background-color: {p.bg_input};
    text-align: center; color: {p.text_primary}; height: 18px; font-weight: 600;
    font-family: {mono_font}; font-size: 11px;
}}
QProgressBar::chunk {{ background-color: {p.accent}; border-radius: 4px; }}
QTableWidget {{
    background-color: {p.bg_input}; alternate-background-color: {table_alt};
    gridline-color: {p.border}; border: 1px solid {p.border}; border-radius: 6px;
}}
QTableWidget::item:selected {{ background-color: {p.accent_soft}; color: {accent_text}; }}
QHeaderView::section {{
    background-color: {p.bg_elevated}; color: {p.text_primary};
    padding: 6px 8px; border: 1px solid {p.border}; font-weight: 600;
}}
QPlainTextEdit {{
    background-color: {p.bg_input}; border: 1px solid {p.border}; border-radius: 6px;
    font-family: {mono_font}; font-size: 11px; color: {p.text_primary}; padding: 6px;
}}
QSlider::groove:horizontal {{ height: 5px; background: {p.border_strong}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p.accent}; border: 1px solid {p.accent_fg};
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent_hover}; }}

/* ---- Scrollbars ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; border-radius: 4px; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background: {scroll_handle}; min-height: 28px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background: {scroll_handle}; min-width: 28px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {p.accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ---- Labels & Badges ---- */
QLabel {{ background-color: transparent; }}
QLabel#sectionTitle {{ font-size: 12px; font-weight: 700; color: {p.text_primary}; }}
QLabel#cardHeader {{
    font-size: 10.5px; font-weight: 700; color: {p.text_muted}; letter-spacing: 0.7px;
}}
QLabel#settingsCardHeader {{
    font-size: 12.5px; font-weight: 700; color: {p.text_primary};
}}
QLabel#experienceHint {{
    background-color: {p.bg_elevated}; color: {p.text_secondary};
    border: 1px solid {p.border}; border-radius: 6px;
    padding: 5px 10px; font-size: 11px;
}}
QLabel#summaryBanner {{
    background-color: {p.bg_surface}; color: {p.text_secondary};
    border: 1px solid {p.border}; border-radius: 6px;
    padding: 7px 12px; font-family: {mono_font}; font-size: 11.5px; font-weight: 600;
}}
QLabel#uvBanner {{
    background-color: {p.uv_soft}; color: {p.uv_fg};
    border: 1px solid {p.uv_border}; border-radius: 6px;
    padding: 7px 12px; font-family: {mono_font}; font-size: 11.5px; font-weight: 700;
}}
QLabel#countPill {{
    background-color: {p.bg_elevated}; color: {p.text_secondary};
    border: 1px solid {p.border}; border-radius: 9px;
    padding: 0px 6px; font-size: 10px; font-weight: 700;
    font-family: {mono_font};
}}
QLabel#statLabel {{ color: {p.text_muted}; font-size: 10px; font-weight: 600; }}
QLabel#statValue {{
    color: {p.text_primary}; font-size: 11.5px; font-weight: 700; font-family: {mono_font};
}}
QLabel#monoValue {{
    color: {p.text_primary}; font-size: 11px; font-weight: 600; font-family: {mono_font};
}}
QLabel#mutedLabel {{ color: {p.text_muted}; font-size: 11px; }}
QLabel#accentValue {{
    color: {accent_text}; font-weight: 700; font-size: 11.5px; font-family: {mono_font};
}}
QLabel#modifiedPill {{
    background-color: {p.accent_soft}; color: {accent_text};
    border: 1px solid {p.accent_border_soft}; border-radius: 5px;
    padding: 2px 8px; font-weight: 700; font-size: 11px;
}}
QLabel#modeBannerHybrid {{
    background-color: {p.uv_soft}; border: 1px solid {p.uv_border};
    border-radius: 5px; padding: 2px 7px; color: {p.uv_fg}; font-size: 10px; font-weight: 700;
}}
QLabel#modeBannerFFF {{
    background-color: {p.accent_soft}; border: 1px solid {p.accent_border_soft};
    border-radius: 5px; padding: 2px 7px; color: {accent_text}; font-size: 10px; font-weight: 700;
}}
QLabel#uvBadge {{
    background-color: {p.uv_soft}; border: 1px solid {p.uv_border};
    border-radius: 5px; padding: 2px 6px; color: {p.uv_fg}; font-size: 10px; font-weight: 700;
}}
"""


DARK_STYLESHEET = build_stylesheet("dark")
LIGHT_STYLESHEET = build_stylesheet("light")
