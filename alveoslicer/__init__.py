"""AlveoSlicer: Hybrid FFF + in-situ photopolymerization 3D printer slicer."""

from .config import (
    BED_SHAPES,
    BRIM_TYPES,
    CELL_PATTERNS,
    FILAMENT_TYPES,
    FUZZY_SKIN_MODES,
    GCODE_FLAVORS,
    INFILL_ENGINES,
    SEAM_POSITIONS,
    STANDARD_INFILL_PATTERNS,
    SUPPORT_MODES,
    SUPPORT_PATTERNS,
    SUPPORT_STYLES,
    TOP_BOTTOM_FILL_PATTERNS,
    FFFSettings,
    HybridSettings,
    InfillSettings,
    PrinterProfile,
    ResinSettings,
    SlicerConfig,
    UVSettings,
)
from .dosing import DosingPoint, order_points_tsp, plan_cycle_dosing
from .gcode_parser import DosingMarker, GCodePreviewData, parse_gcode
from .geometry import (
    VolumetricField,
    center_mesh_on_bed,
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    load_and_center_mesh,
)
from .hal import (
    BedslingerPrototypeHAL,
    CoreXYToolchangerHAL,
    GCodeHAL,
    create_hal,
)
from .infill import (
    Capsule,
    CellHalfPlane,
    NormalVoronoiLattice,
    clip_voronoi_cell_2d,
    generate_normal_dendritic_infill,
)
from .pipeline import SliceResult, run_slicer, slice_mesh
from .presets import PRESET_CATEGORIES, Preset, PresetManager
from .scene import (
    BuildPlate,
    ModelInstance,
    SimplifiedFace,
    WorkspaceScene,
    compute_placeable_faces,
    cut_mesh_by_plane,
)
from .scheduler import CureCycle, compute_optimal_cure_schedule
from .slicing import SlicedLayer, slice_model_to_layers
from .toolpath import PRUSA_TYPE_MAP, generate_fff_paths_for_layer

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
    # Scene
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
    # Scheduler
    "CureCycle",
    "compute_optimal_cure_schedule",
    # Dosing
    "DosingPoint",
    "order_points_tsp",
    "plan_cycle_dosing",
    # Toolpath & G-code Parser
    "PRUSA_TYPE_MAP",
    "generate_fff_paths_for_layer",
    "DosingMarker",
    "GCodePreviewData",
    "parse_gcode",
    # HAL
    "GCodeHAL",
    "BedslingerPrototypeHAL",
    "CoreXYToolchangerHAL",
    "create_hal",
    # Pipeline
    "SliceResult",
    "slice_mesh",
    "run_slicer",
]
