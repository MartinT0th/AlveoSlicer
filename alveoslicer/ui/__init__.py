"""PySide6 desktop GUI subpackage for AlveoSlicer."""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["AlveoSlicerWindow", "AlveoSplashScreen", "main"]


def __getattr__(name: str) -> Any:
    """Lazily resolve GUI classes and entry point so splash startup remains immediate."""
    if name == "main":
        return importlib.import_module(".app", __name__).main
    if name == "AlveoSlicerWindow":
        return importlib.import_module(".main_window", __name__).AlveoSlicerWindow
    if name == "AlveoSplashScreen":
        return importlib.import_module(".splash", __name__).AlveoSplashScreen
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
