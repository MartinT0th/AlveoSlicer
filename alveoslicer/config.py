"""Configuration dataclasses and JSON serialization for AlveoSlicer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar, Union

_T = TypeVar("_T")

CELL_PATTERNS = (
    "hexagonal",
    "voronoi",
    "square",
    "diamond",
    "triangular",
    "brick",
    "gyroid",
    "cubic",
)

INFILL_ENGINES = ("surface_normal_3d", "standard")
STANDARD_INFILL_PATTERNS = (
    "rectilinear",
    "aligned_rectilinear",
    "grid",
    "triangles",
    "stars",
    "cubic",
    "line",
    "concentric",
    "honeycomb",
    "3d_honeycomb",
    "gyroid",
    "hilbert_curve",
    "archimedean_chords",
    "octagram_spiral",
    "adaptive_cubic",
    "support_cubic",
    "lightning",
)
TOP_BOTTOM_FILL_PATTERNS = (
    "monotonic",
    "monotonic_lines",
    "rectilinear",
    "concentric",
    "hilbert_curve",
    "archimedean_chords",
    "octagram_spiral",
)
BRIM_TYPES = ("no_brim", "outer_only", "inner_only", "outer_and_inner")
SEAM_POSITIONS = ("aligned", "nearest", "rear", "random")
SUPPORT_STYLES = ("grid", "snug", "organic", "pillars")
SUPPORT_PATTERNS = ("rectilinear", "rectilinear_grid", "honeycomb")
SUPPORT_MODES = ("auto", "enforcers_only")
FUZZY_SKIN_MODES = ("none", "outside_walls", "all_walls")
BED_SHAPES = ("rectangular", "circular")
GCODE_FLAVORS = ("marlin2", "klipper", "prusa", "reprap")
FILAMENT_TYPES = ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC")


def _from_dict(cls: Type[_T], data: Dict[str, Any]) -> _T:
    """Instantiate a dataclass while ignoring unknown keys from older/newer configs."""
    valid_names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    filtered = {k: v for k, v in data.items() if k in valid_names}
    return cls(**filtered)


@dataclass
class FFFSettings:
    """PrusaSlicer-compatible FFF extrusion, shell, ironing, skirt/brim, support, speed, wipe tower, and cooling parameters."""

    # --- Layers & Perimeters ---
    layer_height: float = 0.20                      # mm (h)
    first_layer_height: float = 0.20                # mm
    perimeters: int = 2                             # Watertight outer skin perimeters
    spiral_vase: bool = False                       # Single continuous outer perimeter (vase mode)
    extra_perimeters_on_overhangs: bool = True      # Extra anchor walls on shallow slopes
    detect_thin_walls: bool = True                  # Single-pass thin wall detection
    avoid_crossing_perimeters: bool = False         # Route travels inside part perimeter
    external_perimeters_first: bool = False         # Print outer wall before inner perimeters
    seam_position: str = "aligned"                  # "aligned", "nearest", "rear", "random"
    bottom_solid_layers: int = 4
    top_solid_layers: int = 5
    min_top_shell_thickness_mm: float = 0.70        # Min vertical top skin thickness
    min_bottom_shell_thickness_mm: float = 0.50     # Min vertical bottom skin thickness
    infill_angle_deg: float = 45.0
    infill_overlap_pct: float = 20.0                # Infill/perimeter overlap %
    bridge_flow_ratio: float = 0.95                 # Flow multiplier on bridges
    combine_infill_every_n_layers: int = 1          # 1 = every layer
    ironing_enable: bool = False                    # Smooth top-surface micro-extrusion pass
    ironing_spacing_mm: float = 0.15                # Pass spacing on top solid faces
    ironing_flow_pct: float = 15.0                  # Flow rate % during ironing
    elephant_foot_compensation_mm: float = 0.15     # Layer 1 inward XY shrink (mm)
    xy_size_compensation_mm: float = 0.0            # Global XY dimensional offset (mm)
    xy_hole_compensation_mm: float = 0.0            # Inner hole XY radius compensation (mm)
    fuzzy_skin_mode: str = "none"                   # "none", "outside_walls", "all_walls"
    fuzzy_skin_thickness_mm: float = 0.25           # Fuzzy skin amplitude (mm)
    fuzzy_skin_point_dist_mm: float = 0.80          # Fuzzy skin segment length (mm)

    # --- Extrusion Widths ---
    nozzle_diameter: float = 0.40                   # mm
    line_width: float = 0.45                        # Default extrusion width (mm)
    first_layer_line_width: float = 0.42            # mm
    perimeter_line_width: float = 0.45              # mm
    external_perimeter_line_width: float = 0.45     # mm
    infill_line_width: float = 0.45                 # mm
    solid_infill_line_width: float = 0.45           # mm
    top_infill_line_width: float = 0.40             # mm
    support_line_width: float = 0.38                # mm

    # --- Speeds & Accelerations ---
    print_speed_mm_s: float = 50.0                  # Default / infill print speed
    perimeter_speed_mm_s: float = 45.0
    external_perimeter_speed_mm_s: float = 35.0
    small_perimeter_speed_mm_s: float = 25.0
    solid_infill_speed_mm_s: float = 55.0
    top_solid_infill_speed_mm_s: float = 40.0
    bridge_speed_mm_s: float = 25.0
    gap_fill_speed_mm_s: float = 30.0
    support_speed_mm_s: float = 50.0
    support_interface_speed_mm_s: float = 35.0
    first_layer_speed_mm_s: float = 20.0
    travel_speed_mm_s: float = 180.0
    z_travel_speed_mm_s: float = 12.0
    default_accel_mm_s2: float = 1000.0
    perimeter_accel_mm_s2: float = 800.0
    external_perimeter_accel_mm_s2: float = 600.0
    infill_accel_mm_s2: float = 1250.0
    bridge_accel_mm_s2: float = 1000.0
    first_layer_accel_mm_s2: float = 500.0
    max_volumetric_speed_mm3_s: float = 15.0        # Volumetric flow cap (0 = unlimited)

    # --- Skirt & Brim ---
    skirt_loops: int = 1
    skirt_distance_mm: float = 6.0
    skirt_height_layers: int = 1
    skirt_min_length_mm: float = 0.0
    draft_shield_enable: bool = False               # Full-height wind shield around model
    brim_type: str = "no_brim"                      # "no_brim", "outer_only", "inner_only", "outer_and_inner"
    brim_width_mm: float = 0.0
    brim_separation_gap_mm: float = 0.10

    # --- External Overhang Support Material & Paint-on Supports ---
    support_enable: bool = False
    support_mode: str = "auto"                      # "auto" (auto + enforcers - blockers) or "enforcers_only"
    support_style: str = "grid"                     # "grid", "snug", "organic", "pillars"
    support_pattern: str = "rectilinear"            # "rectilinear", "rectilinear_grid", "honeycomb"
    support_overhang_angle_deg: float = 45.0        # Degrees from horizontal
    support_buildplate_only: bool = True
    support_dont_support_bridges: bool = True
    support_xy_distance_mm: float = 0.60
    support_z_distance_mm: float = 0.20
    support_bottom_z_distance_mm: float = 0.20
    support_spacing_mm: float = 2.50
    support_interface_layers: int = 2
    support_bottom_interface_layers: int = 1
    support_interface_spacing_mm: float = 0.60
    support_raft_layers: int = 0
    support_first_layer_expansion_mm: float = 3.0
    support_branch_diameter_mm: float = 2.4         # Organic tree branch tip diameter
    support_branch_angle_deg: float = 40.0          # Organic tree max lean angle
    dry_support_spacing_mm: float = 4.50            # Sparse dry FFF support under hollow inner roofs

    # --- Purge / Wipe Tower ---
    wipe_tower_enable: bool = False
    wipe_tower_x: float = 20.0
    wipe_tower_y: float = 20.0
    wipe_tower_width_mm: float = 35.0
    wipe_tower_depth_mm: float = 15.0
    wipe_tower_brim_mm: float = 2.0
    wipe_tower_prime_volume_mm3: float = 15.0

    # --- Filament, Temperatures, Cooling & Retraction ---
    filament_type: str = "PLA"
    filament_diameter: float = 1.75                 # mm
    filament_density_g_cm3: float = 1.24
    filament_cost_per_kg: float = 25.0
    flow_multiplier: float = 1.0
    nozzle_temp_c: int = 210
    first_layer_nozzle_temp_c: int = 215
    bed_temp_c: int = 60
    first_layer_bed_temp_c: int = 60
    chamber_temp_c: int = 0
    fan_auto_cooling: bool = True
    part_cooling_fan_pct: int = 100
    min_fan_speed_pct: int = 35
    fan_bridge_pct: int = 100
    fan_disable_first_layers: int = 1
    fan_full_speed_at_layer: int = 3
    slowdown_below_layer_time_s: float = 12.0
    min_print_speed_mm_s: float = 15.0
    z_hop_mm: float = 0.40
    retract_length_mm: float = 1.20
    retract_speed_mm_s: float = 35.0
    deretract_speed_mm_s: float = 30.0
    retract_min_travel_mm: float = 2.0
    wipe_on_retract: bool = False
    retract_on_layer_change: bool = False


@dataclass
class InfillSettings:
    """Infill engine selection (3D Surface-Normal Cellular vs Standard PrusaSlicer Infill) and parameters."""

    # --- Infill Engine & Standard PrusaSlicer Infill ---
    infill_engine: str = "surface_normal_3d"  # "surface_normal_3d" or "standard"
    fill_density_pct: float = 15.0            # 0..100 % (used when infill_engine == "standard")
    standard_fill_pattern: str = "gyroid"     # Standard PrusaSlicer infill pattern
    top_fill_pattern: str = "monotonic"       # Top surface solid fill pattern
    bottom_fill_pattern: str = "monotonic"    # Bottom surface solid fill pattern
    only_infill_where_needed: bool = False    # Only generate infill under overhangs/top roofs
    solid_infill_every_layers: int = 0        # Insert full solid diaphragm every N layers (0 = off)
    solid_infill_threshold_area_mm2: float = 70.0  # Auto-solidify narrow areas below this mm^2
    infill_anchor_mm: float = 2.5             # Length of infill anchor connected to perimeter (mm)
    infill_anchor_max_mm: float = 12.0        # Maximum infill anchor length (mm)
    infill_before_perimeters: bool = False    # Print internal infill before walls
    bridge_angle_deg: float = 0.0             # Custom bridging angle (0 = auto shortest span)

    # --- 3D Surface-Normal Cellular Formwork & Core Topology ---
    pattern: str = "hexagonal"             # "hexagonal", "voronoi", "square", "diamond", "triangular", "brick", "gyroid", "cubic"
    mode: str = "hollow_shell"             # "hollow_shell" or "full_to_medial"
    hollow_wall_thickness: float = 6.0     # mm (clamped <= max_bridge_span)
    inner_skin_thickness: float = 0.90     # mm watertight inner skin in hollow_shell mode
    max_bridge_span: float = 8.0           # mm upper limit for horizontal bridges
    max_normal_depth: Optional[float] = None  # Optional isotropic deep core (full_to_medial)
    target_cell_pitch: float = 10.0        # mm center-to-center cell spacing
    strut_radius: float = 0.45             # Inside-perimeter wall thickness = 2 * strut_radius
    min_channel_clearance: float = 2.0     # mm continuous photopolymer channel width (w_resin)
    flatten_angle_deg: float = 30.0        # Flatten cell faces tilted < this angle (deg) to 0 deg
    max_divergence_ratio: float = 1.6      # Retained for config compatibility
    smin_blend_radius: float = 0.25        # mm optional corner fillet on cell polygons (0 = crisp)
    medial_buffer_ratio: float = 0.90      # Medial-axis detection threshold
    shock_cos_threshold: float = 0.45      # Retained for config compatibility


@dataclass
class ResinSettings:
    """Photopolymer curing physics, needle clearance, dosing, and DP scheduler weights."""

    x_min_layers: int = 3                  # Preferred min layers between injections
    x_max_layers: int = 6                  # Max layers between injections
    max_cure_depth_mm: float = 1.40        # Beer-Lambert C_d (mm)
    cure_depth_safety_eta: float = 0.85    # Safety multiplier [0.7, 0.9]
    headspace_margin_mm: float = 0.15      # Meniscus clearance below top FFF layer
    shrinkage_factor: float = 1.04         # Volumetric curing shrinkage compensation
    needle_outer_radius_mm: float = 0.45   # Outer radius of dispensing needle
    needle_safety_clear_mm: float = 0.25   # Wall collision margin
    pump_mm3_per_e_mm: float = 1.50        # Calibrated mm^3 dispensed per 1.0 mm pump E-move
    dispense_speed_e_mm_s: float = 4.0     # Pump stepper feedrate
    suckback_e_mm: float = 0.40            # Pump retraction to snap droplet
    suckback_speed_e_mm_s: float = 12.0
    max_flow_radius_mm: float = 10.0       # Subdivide long channels beyond this radius
    ghost_island_min_area_mm2: float = 0.85  # Ignore raster ghost islands < this area & merge volume into nearest island
    settle_dwell_ms: int = 2500            # Viscosity leveling pause before UV sweep
    uv_shadow_bleed_mm: float = 0.25       # Oblique LED cone / scatter bleed margin
    max_shadow_vol_ratio: float = 0.04     # Hard reject slab if shadowed void > 4%
    dp_w_cycle: float = 10.0               # Base time cost per inject+UV cycle
    dp_w_short: float = 45.0               # Quadratic penalty for intervals < x_min
    dp_w_shadow: float = 150.0             # Soft penalty per mm^3 of shadowed void


@dataclass
class UVSettings:
    """UV optical irradiance, thermal split-pass, and firmware command settings."""

    irradiance_mw_cm2: float = 25.0
    beam_width_cm: float = 1.5
    target_dose_mj_cm2_per_mm: float = 80.0
    min_passes: int = 2
    inter_pass_cooldown_ms: int = 1000
    focal_z_clearance_mm: float = 15.0
    min_scan_speed_mm_s: float = 2.0
    max_scan_speed_mm_s: float = 60.0
    uv_on_gcode: str = "M106 P2 S255"
    uv_off_gcode: str = "M106 P2 S0"


@dataclass
class PrinterProfile:
    """Machine kinematics, build volume, firmware limits, custom G-code, and needle/UV offsets."""

    printer_type: str = "bedslinger_prototype"  # "bedslinger_prototype" or "corexy_toolchanger"
    gcode_flavor: str = "marlin2"               # "marlin2", "klipper", "prusa", "reprap"
    bed_shape: str = "rectangular"              # "rectangular" or "circular"
    bed_size_x: float = 220.0
    bed_size_y: float = 220.0
    bed_size_z: float = 220.0
    z_offset_mm: float = 0.0
    use_relative_e_distances: bool = True
    use_firmware_retraction: bool = False
    min_layer_height_mm: float = 0.07
    max_layer_height_mm: float = 0.35

    # Machine motion limits (PrusaSlicer Machine Limits tab)
    max_feedrate_x_mm_s: float = 200.0
    max_feedrate_y_mm_s: float = 200.0
    max_feedrate_z_mm_s: float = 12.0
    max_feedrate_e_mm_s: float = 120.0
    max_accel_x_mm_s2: float = 1500.0
    max_accel_y_mm_s2: float = 1500.0
    max_accel_z_mm_s2: float = 200.0
    max_accel_e_mm_s2: float = 1500.0
    max_jerk_x_mm_s: float = 8.0
    max_jerk_y_mm_s: float = 8.0
    max_jerk_z_mm_s: float = 0.4
    max_jerk_e_mm_s: float = 4.5

    # Custom G-code hooks
    start_gcode: str = ""
    end_gcode: str = ""
    before_layer_gcode: str = ""
    after_layer_gcode: str = ""

    # Hybrid toolhead offsets & safety heights
    needle_offset_x: float = 28.0
    needle_offset_y: float = 0.0
    needle_offset_z: float = 1.0
    x_uv_shield_park: float = 0.0
    uv_strip_y_offset: float = 110.0
    tool_fff: str = "T0"
    tool_resin: str = "T1"
    tool_uv: str = "T2"
    resin_extruder_axis: str = "E"


@dataclass
class HybridSettings:
    """Master switches for hybrid photopolymer pumping and in-situ UV curing."""

    enable_resin_pumping: bool = True
    enable_uv_curing: bool = True

    @property
    def is_fff_only(self) -> bool:
        return (not self.enable_resin_pumping) and (not self.enable_uv_curing)


@dataclass
class SlicerConfig:
    """Top-level JSON-serializable configuration shared by the engine, CLI, and GUI."""

    voxel_pitch_mm: float = 0.50           # Used ONLY for 3D surface preview & deep-core seeding
    slice_raster_pitch_mm: float = 0.25    # Used ONLY for DP bitmasks, basin BFS & 2D UI preview
    step_tol_linear: float = 0.015         # Fine OpenCASCADE STEP linear tolerance (mm)
    step_tol_angular: float = 0.05         # ~3 degrees in radians for smooth CAD curves
    fff: FFFSettings = field(default_factory=FFFSettings)
    infill: InfillSettings = field(default_factory=InfillSettings)
    resin: ResinSettings = field(default_factory=ResinSettings)
    uv: UVSettings = field(default_factory=UVSettings)
    printer: PrinterProfile = field(default_factory=PrinterProfile)
    hybrid: HybridSettings = field(default_factory=HybridSettings)

    def to_json(self, path: Union[str, Path]) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "SlicerConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            voxel_pitch_mm=raw.get("voxel_pitch_mm", 0.50),
            slice_raster_pitch_mm=raw.get("slice_raster_pitch_mm", 0.25),
            step_tol_linear=raw.get("step_tol_linear", 0.015),
            step_tol_angular=raw.get("step_tol_angular", 0.05),
            fff=_from_dict(FFFSettings, raw.get("fff", {})),
            infill=_from_dict(InfillSettings, raw.get("infill", {})),
            resin=_from_dict(ResinSettings, raw.get("resin", {})),
            uv=_from_dict(UVSettings, raw.get("uv", {})),
            printer=_from_dict(PrinterProfile, raw.get("printer", {})),
            hybrid=_from_dict(HybridSettings, raw.get("hybrid", {})),
        )
