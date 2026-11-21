"""Hardware Abstraction Layer (HAL) implementations and factory for AlveoSlicer."""

from __future__ import annotations

from ..config import SlicerConfig
from .base import GCodeHAL, format_duration_prusa
from .bedslinger import BedslingerPrototypeHAL
from .corexy import CoreXYToolchangerHAL


def create_hal(cfg: SlicerConfig) -> GCodeHAL:
    """Instantiate the concrete GCodeHAL matching ``cfg.printer.printer_type``."""
    if cfg.printer.printer_type == "corexy_toolchanger":
        return CoreXYToolchangerHAL(cfg)
    return BedslingerPrototypeHAL(cfg)


__all__ = [
    "GCodeHAL",
    "BedslingerPrototypeHAL",
    "CoreXYToolchangerHAL",
    "create_hal",
    "format_duration_prusa",
]
