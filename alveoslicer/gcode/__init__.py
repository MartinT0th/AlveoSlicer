"""Stage 6 G-Code Package: exact vector FFF toolpath synthesis, HAL exporters, and preview parser."""

from .hal import BedslingerPrototypeHAL, CoreXYToolchangerHAL, GCodeHAL, create_hal
from .parser import (
    DOSING_COLOR,
    FEATURE_COLORS,
    FEATURE_TYPE_TO_ID,
    FEATURE_TYPES,
    TRAVEL_COLOR,
    DosingMarker,
    GCodePreviewData,
    parse_gcode,
)
from .toolpath import PRUSA_TYPE_MAP, generate_fff_paths_for_layer

__all__ = [
    "PRUSA_TYPE_MAP",
    "generate_fff_paths_for_layer",
    "FEATURE_TYPES",
    "FEATURE_COLORS",
    "FEATURE_TYPE_TO_ID",
    "TRAVEL_COLOR",
    "DOSING_COLOR",
    "DosingMarker",
    "GCodePreviewData",
    "parse_gcode",
    "GCodeHAL",
    "BedslingerPrototypeHAL",
    "CoreXYToolchangerHAL",
    "create_hal",
]
