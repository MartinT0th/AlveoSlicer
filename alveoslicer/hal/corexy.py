"""CoreXY Toolchanger HAL: Independent docked FFF (T0), resin (T1), and UV (T2) toolheads."""

from __future__ import annotations

from typing import List, Tuple

from ..dosing import DosingPoint
from ..scheduler import CureCycle
from .base import GCodeHAL


class CoreXYToolchangerHAL(GCodeHAL):
    """Future CoreXY Toolchanger (Prusa XL / StealthChanger): T0=FFF, T1=Resin, T2=UV Head."""

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
        z_safe = z_k + max(4.0, f.z_hop_mm + 2.0)

        self.emit("")
        self.emit(
            f"; === COREXY TOOLCHANGER INJECT & UV CURE (Layers {cycle.start_layer}-{cycle.end_layer}, "
            f"slab={cycle.slab_thickness_mm:.2f} mm, trigger={cycle.trigger}) ==="
        )
        self._set_feature("Custom")
        self.emit(f"G1 Z{z_safe:.3f} F1200")

        if do_pump:
            self.emit(f"{p.tool_resin} ; pick up resin toolhead")
            self.emit("G92 E0")

            for idx, dp in enumerate(dosing_points):
                self.emit(f"; Channel point {idx + 1}: ({dp.x:.2f},{dp.y:.2f}) vol={dp.volume_mm3:.2f} mm^3")
                self.emit(f"G1 X{dp.x:.3f} Y{dp.y:.3f} F{f_travel:.0f}")
                self.emit(f"G1 Z{z_k + p.needle_offset_z:.3f} F900")
                self.emit(f"G1 E{dp.pump_e_mm:.5f} F{f_pump:.0f} ; dispense photopolymer")
                self.emit(f"G1 E-{r.suckback_e_mm:.5f} F{f_suck:.0f} ; pump suck-back")
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
            x_min_b, y_min_b, x_max_b, y_max_b = bbox_xy
            step_x = max(1.0, u.beam_width_cm * 10.0 * 0.75)

            self.emit(f"{p.tool_uv} ; dock shaded resin head, pick up UV curing toolhead")
            self.emit(f"G1 Z{z_uv:.3f} F1200")
            for p_idx in range(passes):
                x_cur = x_min_b
                flip = False
                while x_cur <= x_max_b + 1e-3:
                    ya, yb = (y_min_b, y_max_b) if not flip else (y_max_b, y_min_b)
                    self.emit(f"G1 X{x_cur:.3f} Y{ya:.3f} F{f_travel:.0f}")
                    self.emit(u.uv_on_gcode + " ; UV ON")
                    self.emit(f"G1 Y{yb:.3f} F{v_uv_mm_s * 60.0:.1f} ; UV cure sweep")
                    self.emit(u.uv_off_gcode + " ; UV OFF")
                    self.est_time_s += abs(yb - ya) / max(0.1, v_uv_mm_s)
                    x_cur += step_x
                    flip = not flip
                if p_idx + 1 < passes and u.inter_pass_cooldown_ms > 0:
                    self.emit(f"G4 P{u.inter_pass_cooldown_ms} ; thermal cooldown")
                    self.est_time_s += u.inter_pass_cooldown_ms / 1000.0
            z_end = z_uv

        self.emit(f"{p.tool_fff} ; return to FFF toolhead")
        self.emit("G92 E0")
        self.cur_z = z_end
