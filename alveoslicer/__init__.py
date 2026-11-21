"""AlveoSlicer: Hybrid FFF + in-situ UV photopolymerization 3D printer slicer.

Public facade re-exporting configuration models, geometry/scene operations,
3D surface-normal and standard infill engines, exact vector slicing, hybrid
cure-cycle scheduling & resin dosing, G-code generation/parsing, hardware
abstraction layers, and the end-to-end slicing pipeline.
"""

from .core import (
    BED_SHAPES,
    BRIM_TYPES,
    CELL_PATTERNS,
    FILAMENT_TYPES,
    FUZZY_SKIN_MODES,
    GCODE_FLAVORS,
    INFILL_ENGINES,
    PRESET_CATEGORIES,
    SEAM_POSITIONS,
    STANDARD_INFILL_PATTERNS,
    SUPPORT_MODES,
    SUPPORT_PATTERNS,
    SUPPORT_STYLES,
    TOP_BOTTOM_FILL_PATTERNS,
    BuildPlate,
    FFFSettings,
    HybridSettings,
    InfillSettings,
    ModelInstance,
    Preset,
    PresetManager,
    PrinterProfile,
    ResinSettings,
    SimplifiedFace,
    SlicerConfig,
    UVSettings,
    VolumetricField,
    WorkspaceScene,
    center_mesh_on_bed,
    compute_placeable_faces,
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    cut_mesh_by_plane,
    load_and_center_mesh,
)
from .gcode import (
    PRUSA_TYPE_MAP,
    BedslingerPrototypeHAL,
    CoreXYToolchangerHAL,
    DosingMarker,
    GCodeHAL,
    GCodePreviewData,
    create_hal,
    generate_fff_paths_for_layer,
    parse_gcode,
)
from .hybrid import (
    CureCycle,
    DosingPoint,
    compute_optimal_cure_schedule,
    order_points_tsp,
    plan_cycle_dosing,
)
from .infill import (
    Capsule,
    CellHalfPlane,
    NormalVoronoiLattice,
    clip_voronoi_cell_2d,
    generate_normal_dendritic_infill,
)
from .pipeline import SliceResult, run_slicer, slice_mesh
from .slicing import SlicedLayer, slice_model_to_layers

__all__ = [
    # Config
    "CELL_PATTERNS",
    "INFILL_ENGINES",
    "STANDARD_INFILL_PATTERNS",
    "TOP_BOTTOM_FILL_PATTERNS",
    "BRIM_TYPES",
    "SEAM_POSITIONS",
    "SUPPORT_STYLES",
    "SUPPORT_PATTERNS",
    "SUPPORT_MODES",
    "FUZZY_SKIN_MODES",
    "BED_SHAPES",
    "GCODE_FLAVORS",
    "FILAMENT_TYPES",
    "FFFSettings",
    "InfillSettings",
    "ResinSettings",
    "UVSettings",
    "PrinterProfile",
    "HybridSettings",
    "SlicerConfig",
    # Presets
    "PRESET_CATEGORIES",
    "Preset",
    "PresetManager",
    # Scene & Mesh Operations
    "SimplifiedFace",
    "ModelInstance",
    "BuildPlate",
    "WorkspaceScene",
    "compute_placeable_faces",
    "cut_mesh_by_plane",
    # Geometry
    "VolumetricField",
    "center_mesh_on_bed",
    "load_and_center_mesh",
    "create_test_sphere_mesh",
    "create_test_curved_wall_mesh",
    # Infill
    "Capsule",
    "CellHalfPlane",
    "NormalVoronoiLattice",
    "clip_voronoi_cell_2d",
    "generate_normal_dendritic_infill",
    # Slicing
    "SlicedLayer",
    "slice_model_to_layers",
    # Hybrid Scheduler & Dosing
    "CureCycle",
    "compute_optimal_cure_schedule",
    "DosingPoint",
    "order_points_tsp",
    "plan_cycle_dosing",
    # Toolpath, G-code Parser & HAL
    "PRUSA_TYPE_MAP",
    "generate_fff_paths_for_layer",
    "DosingMarker",
    "GCodePreviewData",
    "parse_gcode",
    "GCodeHAL",
    "BedslingerPrototypeHAL",
    "CoreXYToolchangerHAL",
    "create_hal",
    # Pipeline
    "SliceResult",
    "slice_mesh",
    "run_slicer",
]
