"""Build a standalone, portable single-file ``AlveoSlicer.exe`` via PyInstaller.

Usage::

    python packaging/build_exe.py

Outputs ``dist/AlveoSlicer.exe`` with the embedded honeycomb application icon and
all Python/Qt/OpenGL/scientific dependencies bundled so it can be moved and run
from anywhere on Windows (e.g., Desktop or USB drive) without Python installed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "alveoslicer" / "assets"
ICON_PATH = ASSETS_DIR / "alveoslicer.ico"
LAUNCHER = ROOT / "packaging" / "launch_gui.py"


def build_executable() -> Path:
    """Invoke PyInstaller to produce a single-file portable GUI executable in ``dist/``."""
    if not ICON_PATH.is_file():
        raise FileNotFoundError(f"Application icon not found at {ICON_PATH}")

    sep = ";" if sys.platform == "win32" else ":"
    add_assets = f"{ASSETS_DIR}{sep}alveoslicer/assets"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "AlveoSlicer",
        "--icon",
        str(ICON_PATH),
        "--add-data",
        add_assets,
        "--paths",
        str(ROOT),
        # Ensure dynamic scientific/Qt backends are always bundled
        "--hidden-import",
        "PySide6.QtOpenGL",
        "--hidden-import",
        "PySide6.QtOpenGLWidgets",
        "--hidden-import",
        "matplotlib.backends.backend_qtagg",
        "--hidden-import",
        "scipy.spatial.transform._rotation_groups",
        "--hidden-import",
        "skimage.measure._marching_cubes_lewiner",
        # Trim unused large GUI/testing modules to keep binary lean
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "PyQt5",
        "--exclude-module",
        "PyQt6",
        "--exclude-module",
        "pytest",
        str(LAUNCHER),
    ]

    print("Running PyInstaller:")
    print(" ", " ".join(cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    exe_name = "AlveoSlicer.exe" if sys.platform == "win32" else "AlveoSlicer"
    out_path = ROOT / "dist" / exe_name
    if not out_path.is_file():
        raise RuntimeError(f"Expected executable was not produced at {out_path}")
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nBuild complete: {out_path} ({size_mb:.1f} MB)")
    return out_path


if __name__ == "__main__":
    build_executable()
