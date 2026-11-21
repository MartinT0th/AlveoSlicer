"""
Preset Category Schema & Built-In Preset Definitions.

Separates declarative preset metadata (`CATEGORY_FIELDS`, `BUILTIN_PRESET_SPECS`)
from `PresetManager` I/O logic so adding new slicer parameters or factory profiles
requires editing only this registry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

PRESET_CATEGORIES: Tuple[str, ...] = ("print", "filament", "resin", "printer")

CATEGORY_LABELS: Dict[str, str] = {
    "print": "Print Settings",
    "filament": "Filament",
    "resin": "Resin",
    "printer": "Printer",
}

# Disjoint mapping of 100% of SlicerConfig fields across the 4 preset categories
# (Print Settings includes both FFF and Hybrid Process / Pump / UV settings).
# Each entry is ``(section_name_or_None_for_root, attr_name)``.
CATEGORY_FIELDS: Dict[str, Tuple[Tuple[Optional[str], str], ...]] = {
    "print": (
        # Layers & Perimeters
        ("fff", "layer_height"), ("fff", "first_layer_height"), ("fff", "perimeters"),
        ("fff", "spiral_vase"), ("fff", "extra_perimeters_on_overhangs"),
        ("fff", "detect_thin_walls"), ("fff", "avoid_crossing_perimeters"),
        ("fff", "external_perimeters_first"), ("fff", "seam_position"),
        ("fff", "bottom_solid_layers"), ("fff", "top_solid_layers"),
        ("fff", "min_top_shell_thickness_mm"), ("fff", "min_bottom_shell_thickness_mm"),
        ("fff", "infill_angle_deg"), ("fff", "infill_overlap_pct"),
        ("fff", "bridge_flow_ratio"), ("fff", "combine_infill_every_n_layers"),
        ("fff", "ironing_enable"), ("fff", "ironing_spacing_mm"), ("fff", "ironing_flow_pct"),
        ("fff", "elephant_foot_compensation_mm"), ("fff", "xy_size_compensation_mm"),
        ("fff", "xy_hole_compensation_mm"), ("fff", "fuzzy_skin_mode"),
        ("fff", "fuzzy_skin_thickness_mm"), ("fff", "fuzzy_skin_point_dist_mm"),
        # Extrusion Widths
        ("fff", "line_width"), ("fff", "first_layer_line_width"),
        ("fff", "perimeter_line_width"), ("fff", "external_perimeter_line_width"),
        ("fff", "infill_line_width"), ("fff", "solid_infill_line_width"),
        ("fff", "top_infill_line_width"), ("fff", "support_line_width"),
        # Speeds & Accelerations
        ("fff", "print_speed_mm_s"), ("fff", "perimeter_speed_mm_s"),
        ("fff", "external_perimeter_speed_mm_s"), ("fff", "small_perimeter_speed_mm_s"),
        ("fff", "solid_infill_speed_mm_s"), ("fff", "top_solid_infill_speed_mm_s"),
        ("fff", "bridge_speed_mm_s"), ("fff", "gap_fill_speed_mm_s"),
        ("fff", "support_speed_mm_s"), ("fff", "support_interface_speed_mm_s"),
        ("fff", "first_layer_speed_mm_s"), ("fff", "travel_speed_mm_s"),
        ("fff", "z_travel_speed_mm_s"), ("fff", "default_accel_mm_s2"),
        ("fff", "perimeter_accel_mm_s2"), ("fff", "external_perimeter_accel_mm_s2"),
        ("fff", "infill_accel_mm_s2"), ("fff", "bridge_accel_mm_s2"),
        ("fff", "first_layer_accel_mm_s2"), ("fff", "max_volumetric_speed_mm3_s"),
        # Skirt & Brim
        ("fff", "skirt_loops"), ("fff", "skirt_distance_mm"),
        ("fff", "skirt_height_layers"), ("fff", "skirt_min_length_mm"),
        ("fff", "draft_shield_enable"), ("fff", "brim_type"),
        ("fff", "brim_width_mm"), ("fff", "brim_separation_gap_mm"),
        # External Support Material & Paint-on Supports
        ("fff", "support_enable"), ("fff", "support_mode"), ("fff", "support_style"),
        ("fff", "support_pattern"), ("fff", "support_overhang_angle_deg"),
        ("fff", "support_buildplate_only"), ("fff", "support_dont_support_bridges"),
        ("fff", "support_xy_distance_mm"), ("fff", "support_z_distance_mm"),
        ("fff", "support_bottom_z_distance_mm"), ("fff", "support_spacing_mm"),
        ("fff", "support_interface_layers"), ("fff", "support_bottom_interface_layers"),
        ("fff", "support_interface_spacing_mm"), ("fff", "support_raft_layers"),
        ("fff", "support_first_layer_expansion_mm"), ("fff", "support_branch_diameter_mm"),
        ("fff", "support_branch_angle_deg"), ("fff", "dry_support_spacing_mm"),
        # Purge / Wipe Tower
        ("fff", "wipe_tower_enable"), ("fff", "wipe_tower_x"), ("fff", "wipe_tower_y"),
        ("fff", "wipe_tower_width_mm"), ("fff", "wipe_tower_depth_mm"),
        ("fff", "wipe_tower_brim_mm"), ("fff", "wipe_tower_prime_volume_mm3"),
        # Infill Engine & Standard PrusaSlicer Infill
        ("infill", "infill_engine"), ("infill", "fill_density_pct"),
        ("infill", "standard_fill_pattern"), ("infill", "top_fill_pattern"),
        ("infill", "bottom_fill_pattern"), ("infill", "only_infill_where_needed"),
        ("infill", "solid_infill_every_layers"), ("infill", "solid_infill_threshold_area_mm2"),
        ("infill", "infill_anchor_mm"), ("infill", "infill_anchor_max_mm"),
        ("infill", "infill_before_perimeters"), ("infill", "bridge_angle_deg"),
        # 3D Surface-Normal Cellular Infill
        ("infill", "pattern"), ("infill", "mode"), ("infill", "hollow_wall_thickness"),
        ("infill", "inner_skin_thickness"), ("infill", "max_bridge_span"),
        ("infill", "max_normal_depth"), ("infill", "target_cell_pitch"),
        ("infill", "strut_radius"), ("infill", "min_channel_clearance"),
        ("infill", "flatten_angle_deg"), ("infill", "max_divergence_ratio"),
        ("infill", "smin_blend_radius"), ("infill", "medial_buffer_ratio"),
        ("infill", "shock_cos_threshold"),
        # Hybrid Process, Resin Pump/Needle & UV Curing (merged into Print Settings)
        ("hybrid", "enable_resin_pumping"), ("hybrid", "enable_uv_curing"),
        ("resin", "needle_outer_radius_mm"), ("resin", "needle_safety_clear_mm"),
        ("resin", "pump_mm3_per_e_mm"), ("resin", "dispense_speed_e_mm_s"),
        ("resin", "suckback_e_mm"), ("resin", "suckback_speed_e_mm_s"),
        ("uv", "irradiance_mw_cm2"), ("uv", "beam_width_cm"),
        ("uv", "target_dose_mj_cm2_per_mm"), ("uv", "min_passes"),
        ("uv", "inter_pass_cooldown_ms"), ("uv", "focal_z_clearance_mm"),
        ("uv", "min_scan_speed_mm_s"), ("uv", "max_scan_speed_mm_s"),
        ("uv", "uv_on_gcode"), ("uv", "uv_off_gcode"),
        # Engine Resolution
        (None, "voxel_pitch_mm"), (None, "slice_raster_pitch_mm"),
        (None, "step_tol_linear"), (None, "step_tol_angular"),
    ),
    "filament": (
        ("fff", "filament_type"), ("fff", "filament_diameter"),
        ("fff", "filament_density_g_cm3"), ("fff", "filament_cost_per_kg"),
        ("fff", "flow_multiplier"), ("fff", "nozzle_temp_c"),
        ("fff", "first_layer_nozzle_temp_c"), ("fff", "bed_temp_c"),
        ("fff", "first_layer_bed_temp_c"), ("fff", "chamber_temp_c"),
        ("fff", "fan_auto_cooling"), ("fff", "part_cooling_fan_pct"),
        ("fff", "min_fan_speed_pct"), ("fff", "fan_bridge_pct"),
        ("fff", "fan_disable_first_layers"), ("fff", "fan_full_speed_at_layer"),
        ("fff", "slowdown_below_layer_time_s"), ("fff", "min_print_speed_mm_s"),
        ("fff", "retract_length_mm"), ("fff", "retract_speed_mm_s"),
        ("fff", "deretract_speed_mm_s"), ("fff", "retract_min_travel_mm"),
        ("fff", "z_hop_mm"), ("fff", "wipe_on_retract"), ("fff", "retract_on_layer_change"),
    ),
    "resin": (
        ("resin", "x_min_layers"), ("resin", "x_max_layers"),
        ("resin", "max_cure_depth_mm"), ("resin", "cure_depth_safety_eta"),
        ("resin", "headspace_margin_mm"), ("resin", "shrinkage_factor"),
        ("resin", "max_flow_radius_mm"), ("resin", "ghost_island_min_area_mm2"),
        ("resin", "settle_dwell_ms"), ("resin", "uv_shadow_bleed_mm"),
        ("resin", "max_shadow_vol_ratio"), ("resin", "dp_w_cycle"),
        ("resin", "dp_w_short"), ("resin", "dp_w_shadow"),
    ),
    "printer": (
        ("fff", "nozzle_diameter"), ("printer", "printer_type"),
        ("printer", "gcode_flavor"), ("printer", "bed_shape"),
        ("printer", "bed_size_x"), ("printer", "bed_size_y"), ("printer", "bed_size_z"),
        ("printer", "z_offset_mm"), ("printer", "use_relative_e_distances"),
        ("printer", "use_firmware_retraction"), ("printer", "min_layer_height_mm"),
        ("printer", "max_layer_height_mm"), ("printer", "max_feedrate_x_mm_s"),
        ("printer", "max_feedrate_y_mm_s"), ("printer", "max_feedrate_z_mm_s"),
        ("printer", "max_feedrate_e_mm_s"), ("printer", "max_accel_x_mm_s2"),
        ("printer", "max_accel_y_mm_s2"), ("printer", "max_accel_z_mm_s2"),
        ("printer", "max_accel_e_mm_s2"), ("printer", "max_jerk_x_mm_s"),
        ("printer", "max_jerk_y_mm_s"), ("printer", "max_jerk_z_mm_s"),
        ("printer", "max_jerk_e_mm_s"), ("printer", "start_gcode"),
        ("printer", "end_gcode"), ("printer", "before_layer_gcode"),
        ("printer", "after_layer_gcode"), ("printer", "needle_offset_x"),
        ("printer", "needle_offset_y"), ("printer", "needle_offset_z"),
        ("printer", "x_uv_shield_park"), ("printer", "uv_strip_y_offset"),
        ("printer", "tool_fff"), ("printer", "tool_resin"),
        ("printer", "tool_uv"), ("printer", "resin_extruder_axis"),
    ),
}

# Declarative factory presets: category -> list of (name, override_dict)
BUILTIN_PRESET_SPECS: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {
    "print": [
        ("0.20mm QUALITY (Hexagonal Hybrid)", {}),
        ("0.15mm DETAIL (Hexagonal Hybrid)", {
            "fff.layer_height": 0.15, "fff.first_layer_height": 0.20, "fff.perimeters": 3,
            "fff.bottom_solid_layers": 5, "fff.top_solid_layers": 6, "fff.print_speed_mm_s": 45.0,
            "fff.perimeter_speed_mm_s": 35.0, "fff.external_perimeter_speed_mm_s": 25.0,
            "infill.pattern": "hexagonal", "infill.target_cell_pitch": 8.5, "infill.min_channel_clearance": 1.8,
        }),
        ("0.25mm DRAFT (Diamond 45° Hybrid)", {
            "fff.layer_height": 0.25, "fff.first_layer_height": 0.25, "fff.line_width": 0.50,
            "fff.perimeter_line_width": 0.50, "fff.external_perimeter_line_width": 0.48,
            "fff.print_speed_mm_s": 65.0, "fff.perimeter_speed_mm_s": 55.0,
            "fff.external_perimeter_speed_mm_s": 45.0, "infill.pattern": "diamond",
            "infill.target_cell_pitch": 11.0, "voxel_pitch_mm": 0.60, "slice_raster_pitch_mm": 0.30,
        }),
        ("0.20mm THERMAL-SAFE UV (3-Pass Hybrid)", {
            "hybrid.enable_resin_pumping": True, "hybrid.enable_uv_curing": True,
            "uv.min_passes": 3, "uv.inter_pass_cooldown_ms": 1800, "uv.target_dose_mj_cm2_per_mm": 95.0,
        }),
        ("0.20mm DOSING ONLY (Manual External UV)", {
            "hybrid.enable_resin_pumping": True, "hybrid.enable_uv_curing": False,
        }),
        ("0.20mm STRUCTURAL (Triangular Iso-Grid)", {
            "fff.perimeters": 3, "fff.brim_type": "outer_only", "fff.brim_width_mm": 5.0,
            "infill.pattern": "triangular", "infill.target_cell_pitch": 10.5, "infill.smin_blend_radius": 0.15,
        }),
        ("0.20mm ORGANIC (Voronoi Shell)", {
            "infill.pattern": "voronoi", "infill.target_cell_pitch": 9.5, "infill.smin_blend_radius": 0.35,
        }),
        ("0.20mm RUNNING-BOND (Brick)", {
            "infill.pattern": "brick", "infill.target_cell_pitch": 10.0, "infill.smin_blend_radius": 0.15,
        }),
        ("0.20mm ORTHOGONAL (Square Grid)", {
            "infill.pattern": "square", "infill.target_cell_pitch": 10.0, "infill.smin_blend_radius": 0.15,
        }),
        ("0.20mm FULL-TO-MEDIAL (Supports + Brim)", {
            "infill.pattern": "hexagonal", "infill.mode": "full_to_medial", "fff.brim_type": "outer_only",
            "fff.brim_width_mm": 5.0, "fff.support_enable": True, "fff.support_style": "snug",
            "fff.wipe_tower_enable": True,
        }),
        ("0.20mm GYROID TPMS (Organic Supports)", {
            "infill.pattern": "gyroid", "infill.target_cell_pitch": 11.0, "fff.support_enable": True,
            "fff.support_style": "organic", "fff.support_branch_diameter_mm": 2.6, "fff.support_branch_angle_deg": 40.0,
        }),
        ("0.20mm CUBIC 3D (Ironing Top)", {
            "infill.pattern": "cubic", "infill.target_cell_pitch": 10.0,
            "fff.ironing_enable": True, "fff.ironing_spacing_mm": 0.15, "fff.ironing_flow_pct": 15.0,
        }),
        ("0.20mm STANDARD FFF (Gyroid 15%, No Resin/UV)", {
            "infill.infill_engine": "standard", "infill.fill_density_pct": 15.0,
            "infill.standard_fill_pattern": "gyroid", "infill.top_fill_pattern": "monotonic",
            "infill.bottom_fill_pattern": "monotonic",
            "hybrid.enable_resin_pumping": False, "hybrid.enable_uv_curing": False,
        }),
        ("0.20mm STANDARD FFF (Grid 20%, No Resin/UV)", {
            "infill.infill_engine": "standard", "infill.fill_density_pct": 20.0,
            "infill.standard_fill_pattern": "grid", "infill.top_fill_pattern": "monotonic",
            "infill.bottom_fill_pattern": "monotonic",
            "hybrid.enable_resin_pumping": False, "hybrid.enable_uv_curing": False,
        }),
        ("0.20mm CELLULAR FFF ONLY (Hexagonal, No Resin/UV)", {
            "infill.infill_engine": "surface_normal_3d", "infill.pattern": "hexagonal",
            "hybrid.enable_resin_pumping": False, "hybrid.enable_uv_curing": False,
        }),
    ],
    "filament": [
        ("Generic PLA", {}),
        ("Translucent PLA (UV-Friendly)", {
            "fff.nozzle_temp_c": 205, "fff.first_layer_nozzle_temp_c": 210, "fff.bed_temp_c": 60,
            "fff.first_layer_bed_temp_c": 60, "fff.part_cooling_fan_pct": 100, "fff.min_fan_speed_pct": 60,
            "fff.fan_disable_first_layers": 1, "fff.retract_length_mm": 1.0,
        }),
        ("Prusament PETG", {
            "fff.nozzle_temp_c": 240, "fff.first_layer_nozzle_temp_c": 235, "fff.bed_temp_c": 85,
            "fff.first_layer_bed_temp_c": 85, "fff.part_cooling_fan_pct": 50, "fff.min_fan_speed_pct": 30,
            "fff.fan_disable_first_layers": 3, "fff.retract_length_mm": 1.4, "fff.retract_speed_mm_s": 30.0,
            "fff.z_hop_mm": 0.6,
        }),
        ("Generic ASA / ABS", {
            "fff.nozzle_temp_c": 255, "fff.first_layer_nozzle_temp_c": 260, "fff.bed_temp_c": 105,
            "fff.first_layer_bed_temp_c": 105, "fff.part_cooling_fan_pct": 20, "fff.min_fan_speed_pct": 15,
            "fff.fan_disable_first_layers": 4, "fff.retract_length_mm": 0.8, "fff.retract_speed_mm_s": 40.0,
            "fff.z_hop_mm": 0.4,
        }),
    ],
    "resin": [
        ("Standard 405nm Photopolymer", {}),
        ("Fast-Cure Clear Resin", {
            "resin.x_min_layers": 4, "resin.x_max_layers": 7, "resin.max_cure_depth_mm": 1.65,
            "resin.settle_dwell_ms": 1500, "resin.max_flow_radius_mm": 12.0, "resin.shrinkage_factor": 1.03,
        }),
        ("Tough Engineering Resin", {
            "resin.x_min_layers": 3, "resin.x_max_layers": 5, "resin.max_cure_depth_mm": 1.20,
            "resin.cure_depth_safety_eta": 0.82, "resin.settle_dwell_ms": 3500,
            "resin.shrinkage_factor": 1.05, "resin.max_flow_radius_mm": 8.5,
        }),
        ("High-Viscosity Structural Resin", {
            "resin.x_min_layers": 2, "resin.x_max_layers": 5, "resin.max_cure_depth_mm": 1.15,
            "resin.settle_dwell_ms": 4500, "resin.max_flow_radius_mm": 7.0, "resin.headspace_margin_mm": 0.18,
        }),
    ],
    "printer": [
        ("Alveo Prototype 1 (Bedslinger 220)", {}),
        ("CoreXY Toolchanger (360x360)", {
            "printer.printer_type": "corexy_toolchanger", "printer.bed_size_x": 360.0,
            "printer.bed_size_y": 360.0, "printer.bed_size_z": 360.0,
            "printer.needle_offset_x": 0.0, "printer.needle_offset_y": 0.0, "printer.needle_offset_z": 1.0,
        }),
        ("Standard FFF Printer (250x210)", {
            "printer.printer_type": "bedslinger_prototype", "printer.bed_size_x": 250.0,
            "printer.bed_size_y": 210.0, "printer.bed_size_z": 220.0,
            "printer.needle_offset_x": 0.0, "printer.needle_offset_y": 0.0,
        }),
    ],
}
