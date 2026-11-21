"""Bundled visual assets (application icon and logo) with PyInstaller frozen-path support."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _assets_root() -> Path:
    """Resolve the ``alveoslicer/assets`` directory both from source and PyInstaller bundles."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "alveoslicer" / "assets"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent


def get_app_icon_path() -> Optional[Path]:
    """Return the path to the best available application icon (``.ico`` on Windows, else ``.png``)."""
    root = _assets_root()
    candidates = (
        ("alveoslicer.ico", "alveoslicer_icon.png", "AlveoSlicerLogo.png")
        if sys.platform == "win32"
        else ("alveoslicer_icon.png", "alveoslicer.ico", "AlveoSlicerLogo.png")
    )
    for name in candidates:
        p = root / name
        if p.is_file():
            return p
    return None


def get_logo_path() -> Optional[Path]:
    """Return the path to the transparent ``AlveoSlicerLogo.png`` branding image."""
    p = _assets_root() / "AlveoSlicerLogo.png"
    return p if p.is_file() else None


__all__ = ["get_app_icon_path", "get_logo_path"]
