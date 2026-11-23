"""Desktop GUI application bootstrap for AlveoSlicer with instant startup splash screen."""

from __future__ import annotations

import ctypes
import importlib
import sys
import threading
from typing import List

from PySide6.QtCore import QElapsedTimer, QEventLoop, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..assets import get_app_icon_path
from .splash import AlveoSplashScreen

# Heavy non-Qt scientific dependencies pre-imported on a background thread so
# the splash screen appears immediately and its throbber animates smoothly.
_PRELOAD_MODULES = (
    ("Loading numerical kernel...", "numpy"),
    ("Loading 2D geometry engine...", "shapely"),
    ("Loading volumetric field engine...", "scipy.ndimage"),
    ("Loading spatial indexing...", "scipy.spatial"),
    ("Loading mesh kernel...", "trimesh"),
    ("Loading surface reconstruction...", "skimage.measure"),
    ("Loading 2D basin inspector...", "matplotlib"),
)


def _configure_windows_app_id() -> None:
    """Assign an explicit Windows AppUserModelID so the taskbar displays the AlveoSlicer icon."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlveoSlicer.Desktop.App")
        except Exception:
            pass


def _close_pyi_splash() -> None:
    """Close PyInstaller's instant bootloader splash once the live Qt splash is painted."""
    if getattr(sys, "frozen", False):
        try:
            import pyi_splash  # type: ignore[import-not-found]

            pyi_splash.close()
        except Exception:
            pass


def _pump_events(app: QApplication, ticks: int = 2) -> None:
    """Process pending Qt paint and timer events so the splash throbber animates smoothly."""
    for _ in range(ticks):
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)


def main() -> None:
    """Show :class:`AlveoSplashScreen` immediately, preload modules smoothly, and launch GUI."""
    _configure_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AlveoSlicer")
    app.setOrganizationName("AlveoSlicer")

    # Show & flush the splash card as the very first visual step
    splash = AlveoSplashScreen()
    splash.show()
    splash.raise_()
    splash.repaint()
    elapsed = QElapsedTimer()
    elapsed.start()
    _pump_events(app, ticks=3)
    _close_pyi_splash()

    icon_path = get_app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    # Pre-import heavy scientific libraries off the UI thread so the throbber never freezes
    preload_status: List[str] = ["Loading numerical kernel..."]
    preload_error: List[BaseException] = []

    def _preload_worker() -> None:
        try:
            for label, mod_name in _PRELOAD_MODULES:
                preload_status[0] = label
                importlib.import_module(mod_name)
        except BaseException as exc:  # pragma: no cover - surfaced on main thread
            preload_error.append(exc)

    loader = threading.Thread(target=_preload_worker, name="AlveoPreload", daemon=True)
    loader.start()
    last_label = ""
    while loader.is_alive():
        cur_label = preload_status[0]
        if cur_label != last_label:
            splash.set_status(cur_label)
            last_label = cur_label
        _pump_events(app, ticks=2)
        QThread.msleep(16)
    loader.join()
    if preload_error:
        raise preload_error[0]

    splash.set_status("Loading 3D viewport & interface...")
    _pump_events(app, ticks=2)
    from .styling.theme import build_stylesheet, load_theme_preference

    app.setStyleSheet(build_stylesheet(load_theme_preference()))

    from .main_window import AlveoSlicerWindow

    splash.set_status("Preparing build plates & presets...")
    _pump_events(app, ticks=2)
    win = AlveoSlicerWindow()

    splash.set_status("Ready")
    _pump_events(app, ticks=2)

    # Minimal 250 ms floor only prevents an abrupt flicker on ultra-fast cached launches
    while elapsed.elapsed() < 250:
        _pump_events(app, ticks=2)
        QThread.msleep(16)

    win.show()
    splash.finish(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
