"""Base Hardware Abstraction Layer (HAL) emitting PrusaSlicer 2.9.6 G-code Viewer compatible output."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from ...core.config import SlicerConfig
from ...hybrid.dosing import DosingPoint
from ...hybrid.scheduler import CureCycle
from ..toolpath import PRUSA_TYPE_MAP


def format_duration_prusa(seconds: float) -> str:
    """Format duration in PrusaSlicer style (e.g. '1h 12m 34s')."""
    total_s = max(1, int(round(seconds)))
    days, rem = divmod(total_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if mins or hours or days:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


class GCodeHAL:
    """Hardware Abstraction Layer emitting PrusaSlicer 2.9.6 G-code Viewer compatible output."""

    def __init__(self, cfg: SlicerConfig):
        self.cfg = cfg
        self.lines: List[str] = []
        self.cur_x = 0.0
        self.cur_y = 0.0
        self.cur_z = 0.0
        self.total_e_fff_mm = 0.0
        self.total_e_resin_mm = 0.0
        self.total_resin_mm3 = 0.0
        self.est_time_s = 0.0
        self._active_type: Optional[str] = None
        self._active_width: Optional[float] = None
        self._active_accel: Optional[int] = None

        h = cfg.fff.layer_height
        w = cfg.fff.line_width
        a_line = (w - h) * h + math.pi * (0.5 * h) ** 2 if w > h else w * h
        a_fil = math.pi * (0.5 * cfg.fff.filament_diameter) ** 2
        self.e_per_mm = cfg.fff.flow_multiplier * (a_line / a_fil)
        self.fil_area_mm2 = a_fil

    def emit(self, cmd: str) -> None:
        """Append a single G-code line or comment to the output buffer."""
        self.lines.append(cmd)

    def _emit_custom_gcode(self, block: str, **subs: object) -> None:
        """Emit multi-line custom G-code with optional PrusaSlicer-style [key] placeholder substitution."""
        if not block or not block.strip():
            return
        text = block
        for key, val in subs.items():
            text = text.replace(f"[{key}]", str(val))
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line:
                self.emit(line)

    def _set_feature(self, prusa_type: str, width_mm: Optional[float] = None) -> None:
        if prusa_type != self._active_type:
            self.emit(f";TYPE:{prusa_type}")
            self._active_type = prusa_type
        if width_mm is not None and (
            self._active_width is None or abs(width_mm - self._active_width) > 1e-4
        ):
            self.emit(f";WIDTH:{width_mm:.3f}")
            self._active_width = width_mm

    def _set_accel(self, accel_mm_s2: float) -> None:
        val = int(round(accel_mm_s2))
        if val > 0 and val != self._active_accel:
            self.emit(f"M204 S{val}")
            self._active_accel = val

    def _e_per_mm(self, width_mm: float, height_mm: float, flow_scale: float = 1.0) -> float:
        w = max(0.08, float(width_mm))
        h = max(0.05, float(height_mm))
        a_line = (w - h) * h + math.pi * (0.5 * h) ** 2 if w > h else w * h
        return self.cfg.fff.flow_multiplier * flow_scale * (a_line / self.fil_area_mm2)

    def _feature_params(self, tag: str, layer_idx: int) -> Tuple[float, float, float, float]:
        """Return (width_mm, speed_mm_s, flow_scale, accel_mm_s2) for a toolpath tag."""
        f = self.cfg.fff
        is_first = layer_idx == 1
        lw_def = max(0.15, float(f.line_width))
        if is_first:
            w = max(0.15, float(f.first_layer_line_width or lw_def))
            spd = max(5.0, float(f.first_layer_speed_mm_s))
            acc = float(f.first_layer_accel_mm_s2 or f.default_accel_mm_s2)
            return w, spd, 1.0, acc

        if tag == "SHELL_IRONING":
            w = max(0.08, float(f.ironing_spacing_mm))
            spd = max(10.0, 0.45 * float(f.top_solid_infill_speed_mm_s))
            flow = max(0.02, float(f.ironing_flow_pct) / 100.0)
            return w, spd, flow, float(f.default_accel_mm_s2)
        if tag == "SHELL_EXT_PERIM":
            return (
                max(0.15, float(f.external_perimeter_line_width or lw_def)),
                max(5.0, float(f.external_perimeter_speed_mm_s)),
                1.0,
                float(f.external_perimeter_accel_mm_s2 or f.perimeter_accel_mm_s2),
            )
        if tag in ("SHELL_PERIM", "SHELL_HOLE"):
            return (
                max(0.15, float(f.perimeter_line_width or lw_def)),
                max(5.0, float(f.perimeter_speed_mm_s)),
                1.0,
                float(f.perimeter_accel_mm_s2 or f.default_accel_mm_s2),
            )
        if tag == "SHELL_TOP_FILL":
            return (
                max(0.15, float(f.top_infill_line_width or lw_def)),
                max(5.0, float(f.top_solid_infill_speed_mm_s)),
                1.0,
                float(f.infill_accel_mm_s2 or f.default_accel_mm_s2),
            )
        if tag in ("SHELL_FILL", "CELL_CAP_PERIM", "CELL_CAP_HOLE", "CELL_CAP_FILL"):
            return (
                max(0.15, float(f.solid_infill_line_width or lw_def)),
                max(5.0, float(f.solid_infill_speed_mm_s)),
                1.0,
                float(f.infill_accel_mm_s2 or f.default_accel_mm_s2),
            )
        if tag == "EXT_SUPPORT_INTERFACE":
            return (
                max(0.15, float(f.support_line_width or lw_def)),
                max(5.0, float(f.support_interface_speed_mm_s or f.support_speed_mm_s)),
                1.0,
                float(f.default_accel_mm_s2),
            )
        if tag in ("EXT_SUPPORT", "DRY_SUPPORT"):
            return (
                max(0.15, float(f.support_line_width or lw_def)),
                max(5.0, float(f.support_speed_mm_s)),
                1.0,
                float(f.default_accel_mm_s2),
            )
        if tag == "INSIDE_PERIM":
            return (
                max(0.15, float(f.infill_line_width or lw_def)),
                max(5.0, float(f.print_speed_mm_s)),
                1.0,
                float(f.infill_accel_mm_s2 or f.default_accel_mm_s2),
            )
        return lw_def, max(5.0, float(f.print_speed_mm_s)), 1.0, float(f.default_accel_mm_s2)

    def _feature_width_and_speed(self, tag: str, layer_idx: int) -> Tuple[float, float]:
        w, spd, _, _ = self._feature_params(tag, layer_idx)
        return w, spd

    def start_gcode(self) -> None:
        """Emit PrusaSlicer 2.9.6 header, heating/homing commands, and initial tool selection."""
        f = self.cfg.fff
        p = self.cfg.printer
        hyb = self.cfg.hybrid
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d at %H:%M:%S UTC")
        self.emit(f"; generated by PrusaSlicer 2.9.6 on {ts}")
        self.emit("; AlveoSlicer - Hybrid FFF + In-Situ UV Photopolymer Slicer (Exact Vector Engine)")
        self.emit(f"; Printer Profile: {p.printer_type} ({p.gcode_flavor})")
        mode_desc = (
            "Standard FFF Only (Resin & UV Disabled)"
            if hyb.is_fff_only
            else f"Hybrid (Pumping={'ON' if hyb.enable_resin_pumping else 'OFF'}, "
            f"UV={'ON' if hyb.enable_uv_curing else 'OFF'})"
        )
        self.emit(f"; Process Mode: {mode_desc}")
        self.emit(
            f"; Infill: {self.cfg.infill.pattern} | Mode: {self.cfg.infill.mode} | "
            f"Resin Channel: {self.cfg.infill.min_channel_clearance:.2f} mm | "
            f"Flatten Angle: <{self.cfg.infill.flatten_angle_deg:.1f} deg"
        )
        self.emit("")
        self.emit(";TYPE:Custom")
        self._active_type = "Custom"
        self.emit("G21 ; set units to millimeters")
        self.emit("G90 ; use absolute coordinates")
        self.emit("M83 ; use relative distances for extrusion")
        if p.start_gcode and p.start_gcode.strip():
            self._emit_custom_gcode(
                p.start_gcode,
                first_layer_temperature=f.first_layer_nozzle_temp_c,
                first_layer_bed_temperature=f.first_layer_bed_temp_c,
                temperature=f.nozzle_temp_c,
                bed_temperature=f.bed_temp_c,
            )
        else:
            self.emit(f"M140 S{f.first_layer_bed_temp_c} ; set first layer bed temp")
            self.emit(f"M104 S{f.first_layer_nozzle_temp_c} ; set first layer extruder temp")
            self.emit("G28 ; home all axes")
            self.emit(f"M190 S{f.first_layer_bed_temp_c} ; wait for bed temp")
            self.emit(f"M109 S{f.first_layer_nozzle_temp_c} ; wait for extruder temp")
        if f.chamber_temp_c > 0:
            self.emit(f"M141 S{f.chamber_temp_c} ; set chamber temp")
        if hyb.enable_uv_curing:
            self.emit(self.cfg.uv.uv_off_gcode + " ; ensure UV LEDs OFF")
        if not hyb.is_fff_only:
            self.emit(f"{p.tool_fff} ; select FFF extruder")
        self.emit("G92 E0")

    def _emit_prusaslicer_config_footer(self) -> None:
        f = self.cfg.fff
        p = self.cfg.printer
        r = self.cfg.resin
        hyb = self.cfg.hybrid
        bx, by, bz = int(round(p.bed_size_x)), int(round(p.bed_size_y)), int(round(p.bed_size_z))

        if p.bed_shape == "circular":
            rad = 0.5 * min(p.bed_size_x, p.bed_size_y)
            cx, cy = 0.5 * p.bed_size_x, 0.5 * p.bed_size_y
            bed_pts = [
                f"{cx + rad * math.cos(2.0 * math.pi * i / 36.0):.2f}x{cy + rad * math.sin(2.0 * math.pi * i / 36.0):.2f}"
                for i in range(36)
            ]
            bed_shape_str = ",".join(bed_pts)
        else:
            bed_shape_str = f"0x0,{bx}x0,{bx}x{by},0x{by}"

        fil_cm3 = (self.total_e_fff_mm * self.fil_area_mm2) / 1000.0
        fil_g = fil_cm3 * max(0.5, float(f.filament_density_g_cm3))
        resin_cm3 = self.total_resin_mm3 / 1000.0
        resin_g = resin_cm3 * 1.15

        is_fff_only = hyb.is_fff_only
        is_tc = (not is_fff_only) and (p.printer_type == "corexy_toolchanger")
        n_ext = 1 if is_fff_only else (3 if is_tc else 2)

        def _csv(vals: List[str]) -> str:
            return ",".join(vals)

        def _semi(vals: List[str]) -> str:
            return ";".join(vals)

        if is_fff_only:
            nozzles = f"{f.nozzle_diameter:.2f}"
            colors = "#F5C518"
            offsets = "0x0"
            temps = str(f.nozzle_temp_c)
            first_temps = str(f.first_layer_nozzle_temp_c)
            fil_used_mm = f"{self.total_e_fff_mm:.2f}"
            fil_used_cm3 = f"{fil_cm3:.2f}"
            fil_used_g = f"{fil_g:.2f}"
        elif is_tc:
            nozzles = _csv([f"{f.nozzle_diameter:.2f}", f"{r.needle_outer_radius_mm * 2.0:.2f}", "0.40"])
            colors = _semi(["#F5C518", "#00B0FF", "#AA00FF"])
            offsets = _csv(["0x0", "0x0", "0x0"])
            temps = _csv([str(f.nozzle_temp_c), "0", "0"])
            first_temps = _csv([str(f.first_layer_nozzle_temp_c), "0", "0"])
            fil_used_mm = f"{self.total_e_fff_mm:.2f}, {self.total_e_resin_mm:.2f}, 0.00"
            fil_used_cm3 = f"{fil_cm3:.2f}, {resin_cm3:.2f}, 0.00"
            fil_used_g = f"{fil_g:.2f}, {resin_g:.2f}, 0.00"
        else:
            nozzles = _csv([f"{f.nozzle_diameter:.2f}", f"{r.needle_outer_radius_mm * 2.0:.2f}"])
            colors = _semi(["#F5C518", "#00B0FF"])
            offsets = _csv(["0x0", f"{p.needle_offset_x:.2f}x{p.needle_offset_y:.2f}"])
            temps = _csv([str(f.nozzle_temp_c), "0"])
            first_temps = _csv([str(f.first_layer_nozzle_temp_c), "0"])
            fil_used_mm = f"{self.total_e_fff_mm:.2f}, {self.total_e_resin_mm:.2f}"
            fil_used_cm3 = f"{fil_cm3:.2f}, {resin_cm3:.2f}"
            fil_used_g = f"{fil_g:.2f}, {resin_g:.2f}"

        fil_dias = _csv([f"{f.filament_diameter:.2f}"] * n_ext)
        fil_dens = _csv([f"{f.filament_density_g_cm3:.2f}"] * n_ext)
        fil_cost = _csv([f"{f.filament_cost_per_kg:.1f}"] * n_ext)
        fil_types = _semi([str(f.filament_type)] * n_ext)
        bed_temps = _csv([str(f.bed_temp_c)] * n_ext)
        first_bed_temps = _csv([str(f.first_layer_bed_temp_c)] * n_ext)
        ret_lens = _csv([f"{f.retract_length_mm:.2f}"] * n_ext)
        ret_spds = _csv([f"{f.retract_speed_mm_s:.1f}"] * n_ext)
        deret_spds = _csv([f"{f.deretract_speed_mm_s:.1f}"] * n_ext)
        ret_lifts = _csv([f"{f.z_hop_mm:.2f}"] * n_ext)
        ret_trav = _csv([f"{f.retract_min_travel_mm:.1f}"] * n_ext)
        ret_lc = _csv(["1" if f.retract_on_layer_change else "0"] * n_ext)
        wipes = _csv(["1" if f.wipe_on_retract else "0"] * n_ext)
        min_lh = _csv([f"{p.min_layer_height_mm:.2f}"] * n_ext)
        max_lh = _csv([f"{max(p.max_layer_height_mm, f.layer_height):.2f}"] * n_ext)
        coolings = _csv(["1" if f.fan_auto_cooling else "0"] * n_ext)
        fans_on = _csv(["1" if f.part_cooling_fan_pct > 0 else "0"] * n_ext)
        fan_spds = _csv([str(f.part_cooling_fan_pct)] * n_ext)
        bridge_fans = _csv([str(f.fan_bridge_pct)] * n_ext)
        min_fan_spds = _csv([str(f.min_fan_speed_pct)] * n_ext)
        disable_fans = _csv([str(f.fan_disable_first_layers)] * n_ext)
        vol_vec = _csv([f"{f.max_volumetric_speed_mm3_s:.1f}"] * n_ext)
        iron_spd = max(10.0, 0.45 * float(f.top_solid_infill_speed_mm_s))

        self.emit("")
        self.emit(f"; filament used [mm] = {fil_used_mm}")
        self.emit(f"; filament used [cm3] = {fil_used_cm3}")
        self.emit(f"; filament used [g] = {fil_used_g}")
        self.emit(f"; total filament used [g] = {fil_g + resin_g:.2f}")
        self.emit(f"; filament cost = {(fil_g / 1000.0) * f.filament_cost_per_kg:.2f}")
        self.emit(f"; photopolymer used [mL] = {resin_cm3:.3f}")
        self.emit(
            f"; estimated printing time (normal mode) = {format_duration_prusa(self.est_time_s)}"
        )
        self.emit("")
        self.emit("; prusaslicer_config = begin")
        config_pairs = [
            ("avoid_crossing_perimeters", "1" if f.avoid_crossing_perimeters else "0"),
            ("bed_shape", bed_shape_str),
            ("bed_temperature", bed_temps),
            ("bottom_fill_pattern", "monotonic"),
            ("bottom_solid_layers", str(f.bottom_solid_layers)),
            ("bottom_solid_min_thickness", f"{f.min_bottom_shell_thickness_mm:.2f}"),
            ("bridge_acceleration", str(int(f.bridge_accel_mm_s2))),
            ("bridge_angle", "0"),
            ("bridge_fan_speed", bridge_fans),
            ("bridge_flow_ratio", f"{f.bridge_flow_ratio:.2f}"),
            ("bridge_speed", f"{f.bridge_speed_mm_s:.1f}"),
            ("brim_separation", f"{f.brim_separation_gap_mm:.2f}"),
            ("brim_type", f.brim_type),
            ("brim_width", f"{f.brim_width_mm:.2f}"),
            ("complete_objects", "0"),
            ("cooling", coolings),
            ("default_acceleration", str(int(f.default_accel_mm_s2))),
            ("deretract_speed", deret_spds),
            ("disable_fan_first_layers", disable_fans),
            ("dont_support_bridges", "1" if f.support_dont_support_bridges else "0"),
            ("draft_shield", "enabled" if f.draft_shield_enable else "disabled"),
            ("elefant_foot_compensation", f"{f.elephant_foot_compensation_mm:.2f}"),
            ("external_perimeter_acceleration", str(int(f.external_perimeter_accel_mm_s2))),
            ("external_perimeter_extrusion_width", f"{f.external_perimeter_line_width:.3f}"),
            ("external_perimeter_speed", f"{f.external_perimeter_speed_mm_s:.1f}"),
            ("external_perimeters_first", "1" if f.external_perimeters_first else "0"),
            ("extra_perimeters", "1" if f.extra_perimeters_on_overhangs else "0"),
            ("extruder_clearance_height", "20"),
            ("extruder_clearance_radius", "20"),
            ("extruder_colour", colors),
            ("extruder_offset", offsets),
            ("extrusion_axis", "E"),
            ("extrusion_multiplier", _csv([f"{f.flow_multiplier:.2f}"] * n_ext)),
            ("extrusion_width", f"{f.line_width:.3f}"),
            ("fan_always_on", fans_on),
            ("fan_below_layer_time", _csv([str(int(f.slowdown_below_layer_time_s))] * n_ext)),
            ("filament_colour", colors),
            ("filament_cost", fil_cost),
            ("filament_density", fil_dens),
            ("filament_diameter", fil_dias),
            ("filament_max_volumetric_speed", vol_vec),
            ("filament_type", fil_types),
            ("fill_angle", f"{f.infill_angle_deg:.0f}"),
            ("fill_density", "20%"),
            ("fill_pattern", self.cfg.infill.pattern),
            ("first_layer_acceleration", str(int(f.first_layer_accel_mm_s2))),
            ("first_layer_bed_temperature", first_bed_temps),
            ("first_layer_extrusion_width", f"{f.first_layer_line_width:.3f}"),
            ("first_layer_height", f"{f.first_layer_height:.3f}"),
            ("first_layer_speed", f"{f.first_layer_speed_mm_s:.1f}"),
            ("first_layer_temperature", first_temps),
            ("fuzzy_skin", f.fuzzy_skin_mode),
            ("fuzzy_skin_point_dist", f"{f.fuzzy_skin_point_dist_mm:.2f}"),
            ("fuzzy_skin_thickness", f"{f.fuzzy_skin_thickness_mm:.2f}"),
            ("gap_fill_enabled", "1"),
            ("gap_fill_speed", f"{f.gap_fill_speed_mm_s:.1f}"),
            ("gcode_flavor", p.gcode_flavor),
            ("infill_acceleration", str(int(f.infill_accel_mm_s2))),
            ("infill_every_layers", str(f.combine_infill_every_n_layers)),
            ("infill_extruder", "1"),
            ("infill_extrusion_width", f"{f.infill_line_width:.3f}"),
            ("infill_first", "0"),
            ("infill_overlap", f"{f.infill_overlap_pct:.0f}%"),
            ("infill_speed", f"{f.print_speed_mm_s:.1f}"),
            ("ironing", "1" if f.ironing_enable else "0"),
            ("ironing_flowrate", f"{f.ironing_flow_pct:.1f}%"),
            ("ironing_spacing", f"{f.ironing_spacing_mm:.2f}"),
            ("ironing_speed", f"{iron_spd:.1f}"),
            ("ironing_type", "top"),
            ("layer_height", f"{f.layer_height:.3f}"),
            ("max_fan_speed", fan_spds),
            ("max_layer_height", max_lh),
            ("max_print_height", str(bz)),
            ("max_print_speed", f"{max(f.print_speed_mm_s, f.travel_speed_mm_s):.1f}"),
            ("max_volumetric_speed", f"{f.max_volumetric_speed_mm3_s:.1f}"),
            ("min_fan_speed", min_fan_spds),
            ("min_layer_height", min_lh),
            ("min_print_speed", _csv([f"{f.min_print_speed_mm_s:.1f}"] * n_ext)),
            ("min_skirt_length", f"{f.skirt_min_length_mm:.1f}"),
            ("nozzle_diameter", nozzles),
            ("only_retract_when_crossing_perimeters", "0"),
            ("ooze_prevention", "0"),
            ("overhangs", "1"),
            ("perimeter_acceleration", str(int(f.perimeter_accel_mm_s2))),
            ("perimeter_extruder", "1"),
            ("perimeter_extrusion_width", f"{f.perimeter_line_width:.3f}"),
            ("perimeter_generator", "arachne"),
            ("perimeter_speed", f"{f.perimeter_speed_mm_s:.1f}"),
            ("perimeters", str(1 if f.spiral_vase else f.perimeters)),
            ("raft_layers", str(f.support_raft_layers)),
            ("resolution", "0"),
            ("retract_before_travel", ret_trav),
            ("retract_before_wipe", _csv(["0%"] * n_ext)),
            ("retract_layer_change", ret_lc),
            ("retract_length", ret_lens),
            ("retract_lift", ret_lifts),
            ("retract_speed", ret_spds),
            ("seam_position", f.seam_position),
            ("single_extruder_multi_material", "0"),
            ("skirt_distance", f"{f.skirt_distance_mm:.1f}"),
            ("skirt_height", str(f.skirt_height_layers)),
            ("skirts", str(f.skirt_loops)),
            ("slice_closing_radius", "0.049"),
            ("small_perimeter_speed", f"{f.small_perimeter_speed_mm_s:.1f}"),
            ("solid_infill_below_area", "0"),
            ("solid_infill_every_layers", "0"),
            ("solid_infill_extruder", "1"),
            ("solid_infill_extrusion_width", f"{f.solid_infill_line_width:.3f}"),
            ("solid_infill_speed", f"{f.solid_infill_speed_mm_s:.1f}"),
            ("spiral_vase", "1" if f.spiral_vase else "0"),
            ("support_material", "1" if f.support_enable else "0"),
            ("support_material_angle", "0"),
            ("support_material_auto", "1" if (f.support_enable and f.support_mode == "auto") else "0"),
            ("support_material_bottom_interface_layers", str(f.support_bottom_interface_layers)),
            ("support_material_extruder", "1"),
            ("support_material_extrusion_width", f"{f.support_line_width:.3f}"),
            ("support_material_interface_layers", str(f.support_interface_layers)),
            ("support_material_interface_speed", f"{f.support_interface_speed_mm_s:.1f}"),
            ("support_material_pattern", f.support_pattern),
            ("support_material_speed", f"{f.support_speed_mm_s:.1f}"),
            ("support_material_style", f.support_style),
            ("temperature", temps),
            ("thin_walls", "1" if f.detect_thin_walls else "0"),
            ("top_fill_pattern", "monotonic"),
            ("top_infill_extrusion_width", f"{f.top_infill_line_width:.3f}"),
            ("top_solid_infill_speed", f"{f.top_solid_infill_speed_mm_s:.1f}"),
            ("top_solid_layers", str(0 if f.spiral_vase else f.top_solid_layers)),
            ("top_solid_min_thickness", f"{f.min_top_shell_thickness_mm:.2f}"),
            ("travel_speed", f"{f.travel_speed_mm_s:.1f}"),
            ("travel_speed_z", f"{f.z_travel_speed_mm_s:.1f}"),
            ("use_firmware_retraction", "1" if p.use_firmware_retraction else "0"),
            ("use_relative_e_distances", "1" if p.use_relative_e_distances else "0"),
            ("use_volumetric_e", "0"),
            ("variable_layer_height", "0"),
            ("wipe", wipes),
            ("wipe_tower", "1" if f.wipe_tower_enable else "0"),
            ("xy_size_compensation", f"{f.xy_size_compensation_mm:.3f}"),
            ("z_offset", f"{p.z_offset_mm:.3f}"),
        ]
        for key, val in config_pairs:
            self.emit(f"; {key} = {val}")
        self.emit("; prusaslicer_config = end")

    def end_gcode(self) -> None:
        """Emit safe print shutdown sequence and the PrusaSlicer 2.9.6 configuration footer."""
        p = self.cfg.printer
        self.emit("")
        self.emit(";TYPE:Custom")
        if self.cfg.hybrid.enable_uv_curing:
            self.emit(self.cfg.uv.uv_off_gcode + " ; UV OFF")
        if p.end_gcode and p.end_gcode.strip():
            self._emit_custom_gcode(p.end_gcode, max_layer_z=f"{self.cur_z:.3f}")
        else:
            self.emit(f"G1 Z{self.cur_z + 15.0:.3f} F1200 ; raise Z")
            self.emit("M104 S0 ; turn off temperature")
            self.emit("M140 S0 ; turn off heatbed")
            self.emit("M107 ; turn off fan")
            self.emit("M84 ; disable motors")
        self._emit_prusaslicer_config_footer()

    def emit_fff_layer(
        self,
        layer_idx: int,
        z: float,
        paths: List[Tuple[str, np.ndarray]],
        layer_height: Optional[float] = None,
    ) -> None:
        """Emit complete FFF layer G-code (temperatures, fan ramp, retractions, M204, extrusions)."""
        f = self.cfg.fff
        p = self.cfg.printer
        h_layer = float(layer_height) if layer_height is not None else (
            f.first_layer_height if layer_idx == 1 else f.layer_height
        )
        z_eff = max(0.05, z + float(p.z_offset_mm))
        f_travel = f.travel_speed_mm_s * 60.0
        f_travel_z = max(2.0, float(f.z_travel_speed_mm_s)) * 60.0
        f_ret = f.retract_speed_mm_s * 60.0
        f_deret = (f.deretract_speed_mm_s if f.deretract_speed_mm_s > 0.1 else f.retract_speed_mm_s) * 60.0
        min_ret_travel = max(0.2, float(f.retract_min_travel_mm))

        self.emit("")
        self.emit(";LAYER_CHANGE")
        self.emit(f";Z:{z_eff:.3f}")
        self.emit(f";HEIGHT:{h_layer:.3f}")
        self.emit(";BEFORE_LAYER_CHANGE")
        if p.before_layer_gcode and p.before_layer_gcode.strip():
            self._emit_custom_gcode(p.before_layer_gcode, layer_num=layer_idx, layer_z=f"{z_eff:.3f}")
        self.emit("G92 E0")
        self.emit(f";{z_eff:.3f}")
        self.emit(";AFTER_LAYER_CHANGE")
        self.emit(f";{z_eff:.3f}")
        if p.after_layer_gcode and p.after_layer_gcode.strip():
            self._emit_custom_gcode(p.after_layer_gcode, layer_num=layer_idx, layer_z=f"{z_eff:.3f}")

        if layer_idx == 2:
            if f.nozzle_temp_c != f.first_layer_nozzle_temp_c:
                self.emit(f"M104 S{f.nozzle_temp_c} ; set running extruder temp")
            if f.bed_temp_c != f.first_layer_bed_temp_c:
                self.emit(f"M140 S{f.bed_temp_c} ; set running bed temp")

        # Part cooling fan ramp from fan_disable_first_layers to fan_full_speed_at_layer
        fan_start_layer = max(1, int(f.fan_disable_first_layers) + 1)
        fan_full_layer = max(fan_start_layer, int(f.fan_full_speed_at_layer))
        if f.part_cooling_fan_pct > 0 and fan_start_layer <= layer_idx <= fan_full_layer:
            if fan_full_layer == fan_start_layer:
                pct = f.part_cooling_fan_pct
            else:
                frac = (layer_idx - fan_start_layer) / float(fan_full_layer - fan_start_layer)
                pct = f.min_fan_speed_pct + frac * (f.part_cooling_fan_pct - f.min_fan_speed_pct)
            fan_pwm = int(round(255 * max(0.0, min(100.0, pct)) / 100.0))
            self.emit(f"M106 S{fan_pwm}")

        self.emit(f"G1 Z{z_eff:.3f} F{f_travel_z:.0f} ; move to layer Z")
        self.cur_z = z_eff
        self._active_type = None
        self._active_width = None

        max_vol = float(f.max_volumetric_speed_mm3_s)

        for tag, poly in paths:
            if len(poly) < 2:
                continue

            prusa_type = PRUSA_TYPE_MAP.get(tag, "Internal infill")
            feat_w, feat_spd, flow_scale, feat_acc = self._feature_params(tag, layer_idx)
            # Volumetric speed clamp (PrusaSlicer max_volumetric_speed)
            if max_vol > 0.1 and flow_scale > 0.01:
                vol_per_mm = feat_w * h_layer * flow_scale
                if vol_per_mm > 1e-4:
                    feat_spd = min(feat_spd, max(5.0, max_vol / vol_per_mm))

            f_print = feat_spd * 60.0
            e_per_mm = self._e_per_mm(feat_w, h_layer, flow_scale=flow_scale)
            self._set_feature(prusa_type, feat_w)
            self._set_accel(feat_acc)

            x0, y0 = float(poly[0, 0]), float(poly[0, 1])
            dist_travel = math.hypot(x0 - self.cur_x, y0 - self.cur_y)
            if dist_travel > min_ret_travel:
                if f.retract_length_mm > 0.0:
                    if p.use_firmware_retraction:
                        self.emit("G10 ; firmware retract")
                    else:
                        self.emit(f"G1 E-{f.retract_length_mm:.5f} F{f_ret:.0f} ; retract")
                if f.z_hop_mm > 0.0:
                    self.emit(f"G1 Z{z_eff + f.z_hop_mm:.3f} F{f_travel_z:.0f} ; lift Z")
                self.emit(f"G1 X{x0:.3f} Y{y0:.3f} F{f_travel:.0f} ; move to start")
                if f.z_hop_mm > 0.0:
                    self.emit(f"G1 Z{z_eff:.3f} F{f_travel_z:.0f} ; restore layer Z")
                if f.retract_length_mm > 0.0:
                    if p.use_firmware_retraction:
                        self.emit("G11 ; firmware unretract")
                    else:
                        self.emit(f"G1 E{f.retract_length_mm:.5f} F{f_deret:.0f} ; unretract")
                self.est_time_s += (dist_travel / f.travel_speed_mm_s) + 0.15
            else:
                self.emit(f"G1 X{x0:.3f} Y{y0:.3f} F{f_travel:.0f}")
                self.est_time_s += dist_travel / f.travel_speed_mm_s
            self.cur_x, self.cur_y = x0, y0

            self.emit(f"G1 F{f_print:.0f}")
            for pt in poly[1:]:
                nx_, ny_ = float(pt[0]), float(pt[1])
                seg_len = math.hypot(nx_ - self.cur_x, ny_ - self.cur_y)
                if seg_len < 1e-4:
                    continue
                e_val = seg_len * e_per_mm
                self.emit(f"G1 X{nx_:.3f} Y{ny_:.3f} E{e_val:.5f}")
                self.total_e_fff_mm += e_val
                self.est_time_s += seg_len / feat_spd
                self.cur_x, self.cur_y = nx_, ny_

    def compute_uv_speed_and_passes(self, slab_thickness_mm: float) -> Tuple[float, int]:
        """Compute Beer-Lambert UV scan speed (mm/s) and pass count for ``slab_thickness_mm``."""
        u = self.cfg.uv
        target_dose = u.target_dose_mj_cm2_per_mm * max(0.2, slab_thickness_mm)
        passes = max(1, u.min_passes)
        v_mm_s = 10.0 * (u.irradiance_mw_cm2 * u.beam_width_cm * passes) / max(1e-3, target_dose)
        while v_mm_s < u.min_scan_speed_mm_s and passes < 12:
            passes += 1
            v_mm_s = 10.0 * (u.irradiance_mw_cm2 * u.beam_width_cm * passes) / max(1e-3, target_dose)
        v_mm_s = min(u.max_scan_speed_mm_s, max(u.min_scan_speed_mm_s, v_mm_s))
        return v_mm_s, passes

    def emit_resin_and_uv_cycle(
        self,
        cycle: CureCycle,
        z_k: float,
        dosing_points: List[DosingPoint],
        bbox_xy: Tuple[float, float, float, float],
    ) -> None:
        """Emit machine-specific photopolymer injection and UV curing passes for ``cycle``."""
        raise NotImplementedError
