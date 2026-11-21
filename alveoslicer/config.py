"""Configuration dataclasses and JSON serialization for AlveoSlicer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar, Union

_T = TypeVar("_T")


def _from_dict(cls: Type[_T], data: Dict[str, Any]) -> _T:
    """Instantiate a dataclass while ignoring unknown keys from older/newer configs."""
    valid_names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    filtered = {k: v for k, v in data.items() if k in valid_names}
    return cls(**filtered)


@dataclass
class FFFSettings:
    """Thermoplastic FFF extrusion, shell, and dry-core support parameters."""

    layer_height: float = 0.25             # mm (h)
    nozzle_diameter: float = 0.40          # mm
    line_width: float = 0.45               # mm
    filament_diameter: float = 1.75        # mm
    flow_multiplier: float = 1.0
    perimeters: int = 2                    # Watertight outer skin perimeters
    bottom_solid_layers: int = 3
    top_solid_layers: int = 3
    print_speed_mm_s: float = 45.0
    travel_speed_mm_s: float = 150.0
    z_hop_mm: float = 0.6
    retract_length_mm: float = 1.2
    retract_speed_mm_s: float = 35.0
    nozzle_temp_c: int = 210
    bed_temp_c: int = 60
    part_cooling_fan_pct: int = 100
    dry_support_spacing_mm: float = 4.5    # Sparse dry FFF support under hollow inner roofs


@dataclass
class InfillSettings:
    """3D surface-normal cellular formwork and core topology parameters."""

    pattern: str = "hexagonal"             # "voronoi" or "hexagonal"
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
    """Machine kinematics, build volume, tool commands, and needle/UV offsets."""

    printer_type: str = "bedslinger_prototype"  # "bedslinger_prototype" or "corexy_toolchanger"
    bed_size_x: float = 220.0
    bed_size_y: float = 220.0
    bed_size_z: float = 220.0
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
        )
