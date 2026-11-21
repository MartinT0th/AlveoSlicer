"""Prototype 1 HAL: Cartesian bedslinger with carriage-mounted needle and overhead X-axis UV strip."""

from __future__ import annotations

from typing import List, Tuple

from ..dosing import DosingPoint
from ..scheduler import CureCycle
from .base import GCodeHAL


class BedslingerPrototypeHAL(GCodeHAL):
    """Prototype 1: External stepper pump + needle on X carriage + overhead X-axis UV strip."""

    def emit_resin_and_uv_cycle(
        self,
        cycle: CureCycle,
        z_k: float,
        dosing_points: List[DosingPoint],
        bbox_xy: Tuple[float, float, float, float],
    ) -> None:
        p = self.cfg.printer
        r = self.cfg.resin
        u = self.cfg.uv
        f = self.cfg.fff
        hyb = self.cfg.hybrid
        if hyb.is_fff_only:
            return

        do_pump = hyb.enable_resin_pumping and bool(dosing_points)
        do_uv = hyb.enable_uv_curing
        if not do_pump and not do_uv:
            return

        f_travel = f.travel_speed_mm_s * 60.0
        f_pump = r.dispense_speed_e_mm_s * 60.0
        f_suck = r.suckback_speed_e_mm_s * 60.0
        z_safe = z_k + max(4.0, f.z_hop_mm + p.needle_offset_z + 2.0)
        z_disp = z_k + p.needle_offset_z

        self.emit("")
        self.emit(
            f"; === PHOTOPOLYMER INJECT & UV CURE (Layers {cycle.start_layer}-{cycle.end_layer}, "
            f"slab={cycle.slab_thickness_mm:.2f} mm, trigger={cycle.trigger}) ==="
        )
        self._set_feature("Custom")
        self.emit(f"G1 E-{f.retract_length_mm * 1.5:.5f} F{f.retract_speed_mm_s * 60.0:.0f} ; retract FFF")
        self.emit(f"G1 Z{z_safe:.3f} F1200 ; lift Z for hybrid cycle")

        switched_tool = False
        if do_pump:
            if p.resin_extruder_axis == "E":
                self.emit(f"{p.tool_resin} ; select external stepper pump extruder")
                self.emit("G92 E0")
                switched_tool = True

            ax = p.resin_extruder_axis
            for idx, dp in enumerate(dosing_points):
                tx = dp.x - p.needle_offset_x
                ty = dp.y - p.needle_offset_y
                self.emit(
                    f"; Channel point {idx + 1}: target=({dp.x:.2f},{dp.y:.2f}) vol={dp.volume_mm3:.2f} mm^3"
                )
                self.emit(f"G1 X{tx:.3f} Y{ty:.3f} F{f_travel:.0f}")
                self.emit(f"G1 Z{z_disp:.3f} F900")
                self.emit(f"G1 {ax}{dp.pump_e_mm:.5f} F{f_pump:.0f} ; dispense photopolymer")
                self.emit(f"G1 {ax}-{r.suckback_e_mm:.5f} F{f_suck:.0f} ; pump suck-back")
                self.emit(f"G1 Z{z_safe:.3f} F1200")
                self.total_e_resin_mm += dp.pump_e_mm
                self.total_resin_mm3 += dp.volume_mm3
                self.est_time_s += (dp.pump_e_mm / max(0.1, r.dispense_speed_e_mm_s)) + 0.6

            self.emit(f"G4 P{r.settle_dwell_ms} ; resin leveling dwell")
            self.est_time_s += r.settle_dwell_ms / 1000.0

        z_end = z_safe
        if do_uv:
            v_uv_mm_s, passes = self.compute_uv_speed_and_passes(cycle.slab_thickness_mm)
            z_uv = min(p.bed_size_z, z_k + u.focal_z_clearance_mm)
            half_beam_mm = 0.5 * u.beam_width_cm * 10.0
            _, y_min_b, _, y_max_b = bbox_xy

            y_start = max(
                0.0, min(p.bed_size_y, p.uv_strip_y_offset + (p.bed_size_y * 0.5 - y_max_b) - half_beam_mm)
            )
            y_end = max(
                0.0, min(p.bed_size_y, p.uv_strip_y_offset + (p.bed_size_y * 0.5 - y_min_b) + half_beam_mm)
            )
            if abs(y_end - y_start) < 1.0:
                y_start = max(0.0, y_min_b - half_beam_mm)
                y_end = min(p.bed_size_y, y_max_b + half_beam_mm)

            self.emit(f"; UV Hardening: {passes} passes @ {v_uv_mm_s:.2f} mm/s (Z_focal={z_uv:.2f} mm)")
            self.emit(f"G1 X{p.x_uv_shield_park:.3f} Z{z_uv:.3f} F{f_travel:.0f} ; park head behind UV shield")
            for p_idx in range(passes):
                ya, yb = (y_start, y_end) if (p_idx % 2 == 0) else (y_end, y_start)
                self.emit(f"G1 Y{ya:.3f} F{f_travel:.0f}")
                self.emit(u.uv_on_gcode + f" ; UV ON (pass {p_idx + 1}/{passes})")
                self.emit(f"G1 Y{yb:.3f} F{v_uv_mm_s * 60.0:.1f} ; UV cure sweep")
                self.emit(u.uv_off_gcode + " ; UV OFF")
                self.est_time_s += abs(yb - ya) / max(0.1, v_uv_mm_s)
                if p_idx + 1 < passes and u.inter_pass_cooldown_ms > 0:
                    self.emit(f"G4 P{u.inter_pass_cooldown_ms} ; thermal cooldown")
                    self.est_time_s += u.inter_pass_cooldown_ms / 1000.0
            self.cur_x = p.x_uv_shield_park
            self.cur_y = y_end if (passes % 2 == 1) else y_start
            z_end = z_uv

        if switched_tool:
            self.emit(f"{p.tool_fff} ; return to FFF extruder")
            self.emit("G92 E0")
        self.emit(f"G1 E{f.retract_length_mm * 1.5:.5f} F{f.retract_speed_mm_s * 60.0:.0f} ; unretract FFF")
        self.cur_z = z_end
