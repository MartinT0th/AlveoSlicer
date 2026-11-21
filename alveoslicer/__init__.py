"""AlveoSlicer: Hybrid FFF + in-situ UV photopolymerization 3D printer slicer.

Public facade re-exporting configuration models, geometry/scene operations,
3D surface-normal and standard infill engines, exact vector slicing, hybrid
cure-cycle scheduling & resin dosing, G-code generation/parsing, hardware
abstraction layers, and the end-to-end slicing pipeline via lightweight lazy
imports so GUI startup displays the splash screen immediately.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

from ._version import __version__

_LAZY_EXPORTS: Dict[str, Tuple[str, str]] = {
    # Config
    "CELL_PATTERNS": (".core", "CELL_PATTERNS"),
    "INFILL_ENGINES": (".core", "INFILL_ENGINES"),
    "STANDARD_INFILL_PATTERNS": (".core", "STANDARD_INFILL_PATTERNS"),
    "TOP_BOTTOM_FILL_PATTERNS": (".core", "TOP_BOTTOM_FILL_PATTERNS"),
    "BRIM_TYPES": (".core", "BRIM_TYPES"),
    "SEAM_POSITIONS": (".core", "SEAM_POSITIONS"),
    "SUPPORT_STYLES": (".core", "SUPPORT_STYLES"),
    "SUPPORT_PATTERNS": (".core", "SUPPORT_PATTERNS"),
    "SUPPORT_MODES": (".core", "SUPPORT_MODES"),
    "FUZZY_SKIN_MODES": (".core", "FUZZY_SKIN_MODES"),
    "BED_SHAPES": (".core", "BED_SHAPES"),
    "GCODE_FLAVORS": (".core", "GCODE_FLAVORS"),
    "FILAMENT_TYPES": (".core", "FILAMENT_TYPES"),
    "FFFSettings": (".core", "FFFSettings"),
    "InfillSettings": (".core", "InfillSettings"),
    "ResinSettings": (".core", "ResinSettings"),
    "UVSettings": (".core", "UVSettings"),
    "PrinterProfile": (".core", "PrinterProfile"),
    "HybridSettings": (".core", "HybridSettings"),
    "SlicerConfig": (".core", "SlicerConfig"),
    # Presets
    "PRESET_CATEGORIES": (".core", "PRESET_CATEGORIES"),
    "Preset": (".core", "Preset"),
    "PresetManager": (".core", "PresetManager"),
    # Scene & Mesh Operations
    "SimplifiedFace": (".core", "SimplifiedFace"),
    "ModelInstance": (".core", "ModelInstance"),
    "BuildPlate": (".core", "BuildPlate"),
    "WorkspaceScene": (".core", "WorkspaceScene"),
    "compute_placeable_faces": (".core", "compute_placeable_faces"),
    "cut_mesh_by_plane": (".core", "cut_mesh_by_plane"),
    # Geometry
    "VolumetricField": (".core", "VolumetricField"),
    "center_mesh_on_bed": (".core", "center_mesh_on_bed"),
    "load_and_center_mesh": (".core", "load_and_center_mesh"),
    "create_test_sphere_mesh": (".core", "create_test_sphere_mesh"),
    "create_test_curved_wall_mesh": (".core", "create_test_curved_wall_mesh"),
    # Infill
    "Capsule": (".infill", "Capsule"),
    "CellHalfPlane": (".infill", "CellHalfPlane"),
    "NormalVoronoiLattice": (".infill", "NormalVoronoiLattice"),
    "clip_voronoi_cell_2d": (".infill", "clip_voronoi_cell_2d"),
    "generate_normal_dendritic_infill": (".infill", "generate_normal_dendritic_infill"),
    # Slicing
    "SlicedLayer": (".slicing", "SlicedLayer"),
    "slice_model_to_layers": (".slicing", "slice_model_to_layers"),
    # Hybrid Scheduler & Dosing
    "CureCycle": (".hybrid", "CureCycle"),
    "compute_optimal_cure_schedule": (".hybrid", "compute_optimal_cure_schedule"),
    "DosingPoint": (".hybrid", "DosingPoint"),
    "order_points_tsp": (".hybrid", "order_points_tsp"),
    "plan_cycle_dosing": (".hybrid", "plan_cycle_dosing"),
    # Toolpath, G-code Parser & HAL
    "PRUSA_TYPE_MAP": (".gcode", "PRUSA_TYPE_MAP"),
    "generate_fff_paths_for_layer": (".gcode", "generate_fff_paths_for_layer"),
    "DosingMarker": (".gcode", "DosingMarker"),
    "GCodePreviewData": (".gcode", "GCodePreviewData"),
    "parse_gcode": (".gcode", "parse_gcode"),
    "GCodeHAL": (".gcode", "GCodeHAL"),
    "BedslingerPrototypeHAL": (".gcode", "BedslingerPrototypeHAL"),
    "CoreXYToolchangerHAL": (".gcode", "CoreXYToolchangerHAL"),
    "create_hal": (".gcode", "create_hal"),
    # Pipeline
    "SliceResult": (".pipeline", "SliceResult"),
    "slice_mesh": (".pipeline", "slice_mesh"),
    "run_slicer": (".pipeline", "run_slicer"),
}

__all__ = ["__version__", *_LAZY_EXPORTS.keys()]


def __getattr__(name: str) -> Any:
    """Lazily resolve public AlveoSlicer symbols on first attribute access."""
    if name in _LAZY_EXPORTS:
        mod_name, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(mod_name, __name__)
        val = getattr(module, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
