"""Tabbed parameter settings panel synchronizing UI controls with SlicerConfig."""

from __future__ import annotations

from copy import deepcopy
from typing import Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    FFFSettings,
    InfillSettings,
    PrinterProfile,
    ResinSettings,
    SlicerConfig,
    UVSettings,
)
from .widgets import make_double_spin, make_spin, wrap_scroll


class SettingsPanel(QTabWidget):
    """5-tab parameter editor (FFF, 3D Infill, Resin & DP, UV & Printer, Engine)."""

    def __init__(self, initial_cfg: SlicerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_cfg = deepcopy(initial_cfg)

        self.addTab(self._build_fff_tab(), "FFF")
        self.addTab(self._build_infill_tab(), "3D Infill")
        self.addTab(self._build_resin_tab(), "Resin & DP")
        self.addTab(self._build_uv_printer_tab(), "UV & Printer")
        self.addTab(self._build_engine_tab(), "Engine")

        self.load_config(initial_cfg)

    def _build_fff_tab(self) -> QScrollArea:
        f = self._base_cfg.fff
        w = QWidget()
        form = QFormLayout(w)

        self.sp_layer_h = make_double_spin(f.layer_height, 0.05, 1.0, 0.05, 2, "mm")
        self.sp_nozzle = make_double_spin(f.nozzle_diameter, 0.15, 1.5, 0.05, 2, "mm")
        self.sp_line_w = make_double_spin(f.line_width, 0.20, 2.0, 0.05, 2, "mm")
        self.sp_fil_dia = make_double_spin(f.filament_diameter, 1.0, 3.0, 0.05, 2, "mm")
        self.sp_flow = make_double_spin(f.flow_multiplier, 0.5, 1.5, 0.02, 2)
        self.sp_perims = make_spin(f.perimeters, 1, 10)
        self.sp_bot_layers = make_spin(f.bottom_solid_layers, 1, 20)
        self.sp_top_layers = make_spin(f.top_solid_layers, 1, 20)
        self.sp_print_spd = make_double_spin(f.print_speed_mm_s, 5.0, 300.0, 5.0, 1, "mm/s")
        self.sp_travel_spd = make_double_spin(f.travel_speed_mm_s, 20.0, 500.0, 10.0, 1, "mm/s")
        self.sp_zhop = make_double_spin(f.z_hop_mm, 0.0, 5.0, 0.1, 2, "mm")
        self.sp_retract = make_double_spin(f.retract_length_mm, 0.0, 10.0, 0.2, 2, "mm")
        self.sp_retract_spd = make_double_spin(f.retract_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s")
        self.sp_nozzle_t = make_spin(f.nozzle_temp_c, 0, 350, 5, "C")
        self.sp_bed_t = make_spin(f.bed_temp_c, 0, 130, 5, "C")
        self.sp_fan = make_spin(f.part_cooling_fan_pct, 0, 100, 5, "%")
        self.sp_dry_sup = make_double_spin(f.dry_support_spacing_mm, 1.5, 20.0, 0.5, 1, "mm")

        form.addRow("Layer height:", self.sp_layer_h)
        form.addRow("Nozzle diameter:", self.sp_nozzle)
        form.addRow("Line width:", self.sp_line_w)
        form.addRow("Filament diameter:", self.sp_fil_dia)
        form.addRow("Flow multiplier:", self.sp_flow)
        form.addRow("Watertight perimeters:", self.sp_perims)
        form.addRow("Bottom solid layers:", self.sp_bot_layers)
        form.addRow("Top solid layers:", self.sp_top_layers)
        form.addRow("Print speed:", self.sp_print_spd)
        form.addRow("Travel speed:", self.sp_travel_spd)
        form.addRow("Z-hop:", self.sp_zhop)
        form.addRow("Retraction length:", self.sp_retract)
        form.addRow("Retraction speed:", self.sp_retract_spd)
        form.addRow("Nozzle temp:", self.sp_nozzle_t)
        form.addRow("Bed temp:", self.sp_bed_t)
        form.addRow("Part cooling fan:", self.sp_fan)
        form.addRow("Dry core support spacing:", self.sp_dry_sup)
        return wrap_scroll(w)

    def _build_infill_tab(self) -> QScrollArea:
        i = self._base_cfg.infill
        w = QWidget()
        form = QFormLayout(w)

        self.cb_pattern = QComboBox()
        self.cb_pattern.addItems(["voronoi", "hexagonal"])

        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["hollow_shell", "full_to_medial"])
        self.cb_mode.currentTextChanged.connect(self._update_dependent_controls)

        self.sp_hollow_wall = make_double_spin(i.hollow_wall_thickness, 1.5, 30.0, 0.5, 2, "mm")
        self.sp_inner_skin = make_double_spin(i.inner_skin_thickness, 0.4, 5.0, 0.1, 2, "mm")
        self.sp_max_bridge = make_double_spin(i.max_bridge_span, 2.0, 30.0, 0.5, 2, "mm")

        self.chk_max_norm_depth = QCheckBox("Cap normal depth (isotropic core)")
        self.chk_max_norm_depth.toggled.connect(self._update_dependent_controls)
        self.sp_max_norm_depth = make_double_spin(10.0, 2.0, 80.0, 1.0, 1, "mm")

        self.sp_cell_pitch = make_double_spin(i.target_cell_pitch, 2.0, 25.0, 0.5, 2, "mm")
        self.sp_strut_r = make_double_spin(i.strut_radius, 0.2, 3.0, 0.05, 2, "mm")
        self.sp_chan_clear = make_double_spin(i.min_channel_clearance, 0.4, 10.0, 0.1, 2, "mm")
        self.sp_flatten_angle = make_double_spin(i.flatten_angle_deg, 0.0, 80.0, 1.0, 1, "deg")
        self.sp_div_ratio = make_double_spin(i.max_divergence_ratio, 1.2, 3.0, 0.1, 2)
        self.sp_smin_r = make_double_spin(i.smin_blend_radius, 0.0, 2.0, 0.05, 2, "mm")
        self.sp_medial_ratio = make_double_spin(i.medial_buffer_ratio, 0.50, 0.99, 0.02, 2)
        self.sp_shock_cos = make_double_spin(i.shock_cos_threshold, -0.5, 0.95, 0.05, 2)

        form.addRow("Normal pattern:", self.cb_pattern)
        form.addRow("Interior mode:", self.cb_mode)
        form.addRow("Hollow wall thickness:", self.sp_hollow_wall)
        form.addRow("Hollow inner skin:", self.sp_inner_skin)
        form.addRow("Max bridgeable span:", self.sp_max_bridge)
        form.addRow(self.chk_max_norm_depth)
        form.addRow("Core transition depth:", self.sp_max_norm_depth)
        form.addRow("Surface cell pitch:", self.sp_cell_pitch)
        form.addRow("Strut radius (w/2):", self.sp_strut_r)
        form.addRow("Min fluid channel gap:", self.sp_chan_clear)
        form.addRow("Flatten face angle (<):", self.sp_flatten_angle)
        form.addRow("Divergence split ratio:", self.sp_div_ratio)
        form.addRow("Cell corner fillet radius:", self.sp_smin_r)
        form.addRow("Medial weld ratio:", self.sp_medial_ratio)
        form.addRow("Shock cos threshold:", self.sp_shock_cos)
        return wrap_scroll(w)

    def _build_resin_tab(self) -> QScrollArea:
        r = self._base_cfg.resin
        w = QWidget()
        form = QFormLayout(w)

        self.sp_xmin = make_spin(r.x_min_layers, 1, 20, 1, "layers")
        self.sp_xmax = make_spin(r.x_max_layers, 1, 30, 1, "layers")
        self.sp_cure_depth = make_double_spin(r.max_cure_depth_mm, 0.2, 10.0, 0.1, 2, "mm")
        self.sp_cure_eta = make_double_spin(r.cure_depth_safety_eta, 0.5, 1.0, 0.05, 2)
        self.sp_headspace = make_double_spin(r.headspace_margin_mm, 0.0, 1.0, 0.05, 2, "mm")
        self.sp_shrink = make_double_spin(r.shrinkage_factor, 0.90, 1.25, 0.01, 3)
        self.sp_needle_r = make_double_spin(r.needle_outer_radius_mm, 0.1, 3.0, 0.05, 2, "mm")
        self.sp_needle_clr = make_double_spin(r.needle_safety_clear_mm, 0.0, 2.0, 0.05, 2, "mm")
        self.sp_pump_cal = make_double_spin(r.pump_mm3_per_e_mm, 0.05, 50.0, 0.1, 3, "mm3/E")
        self.sp_pump_spd = make_double_spin(r.dispense_speed_e_mm_s, 0.5, 50.0, 0.5, 1, "mm/s")
        self.sp_suckback = make_double_spin(r.suckback_e_mm, 0.0, 10.0, 0.1, 2, "mm")
        self.sp_suck_spd = make_double_spin(r.suckback_speed_e_mm_s, 1.0, 80.0, 1.0, 1, "mm/s")
        self.sp_flow_rad = make_double_spin(r.max_flow_radius_mm, 2.0, 50.0, 1.0, 1, "mm")
        self.sp_dwell = make_spin(r.settle_dwell_ms, 0, 30000, 250, "ms")
        self.sp_bleed = make_double_spin(r.uv_shadow_bleed_mm, 0.0, 2.0, 0.05, 2, "mm")
        self.sp_max_shadow = make_double_spin(r.max_shadow_vol_ratio, 0.0, 0.50, 0.01, 3)

        form.addRow("Cure interval min (x_min):", self.sp_xmin)
        form.addRow("Cure interval max (x_max):", self.sp_xmax)
        form.addRow("Beer-Lambert cure depth:", self.sp_cure_depth)
        form.addRow("Cure depth safety factor:", self.sp_cure_eta)
        form.addRow("Meniscus headspace:", self.sp_headspace)
        form.addRow("Shrinkage compensation:", self.sp_shrink)
        form.addRow("Needle outer radius:", self.sp_needle_r)
        form.addRow("Needle wall clearance:", self.sp_needle_clr)
        form.addRow("Pump calibration:", self.sp_pump_cal)
        form.addRow("Dispense speed:", self.sp_pump_spd)
        form.addRow("Suck-back retraction:", self.sp_suckback)
        form.addRow("Suck-back speed:", self.sp_suck_spd)
        form.addRow("Max basin flow radius:", self.sp_flow_rad)
        form.addRow("Leveling dwell:", self.sp_dwell)
        form.addRow("UV shadow bleed radius:", self.sp_bleed)
        form.addRow("Max shadowed void ratio:", self.sp_max_shadow)
        return wrap_scroll(w)

    def _build_uv_printer_tab(self) -> QScrollArea:
        p = self._base_cfg.printer
        u = self._base_cfg.uv
        w = QWidget()
        vbox = QVBoxLayout(w)

        g_pr = QGroupBox("Printer Kinematics & Offsets")
        f_pr = QFormLayout(g_pr)
        self.cb_printer = QComboBox()
        self.cb_printer.addItems(["bedslinger_prototype", "corexy_toolchanger"])
        self.cb_printer.currentTextChanged.connect(self._update_dependent_controls)

        self.sp_bed_x = make_double_spin(p.bed_size_x, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_bed_y = make_double_spin(p.bed_size_y, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_bed_z = make_double_spin(p.bed_size_z, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_noff_x = make_double_spin(p.needle_offset_x, -150.0, 150.0, 1.0, 2, "mm")
        self.sp_noff_y = make_double_spin(p.needle_offset_y, -150.0, 150.0, 1.0, 2, "mm")
        self.sp_noff_z = make_double_spin(p.needle_offset_z, -10.0, 20.0, 0.2, 2, "mm")
        self.sp_x_park = make_double_spin(p.x_uv_shield_park, 0.0, 600.0, 5.0, 1, "mm")
        self.sp_uv_yoff = make_double_spin(p.uv_strip_y_offset, 0.0, 600.0, 5.0, 1, "mm")
        self.ed_t_fff = QLineEdit(p.tool_fff)
        self.ed_t_resin = QLineEdit(p.tool_resin)
        self.ed_t_uv = QLineEdit(p.tool_uv)
        self.ed_resin_axis = QLineEdit(p.resin_extruder_axis)

        f_pr.addRow("Printer HAL profile:", self.cb_printer)
        f_pr.addRow("Bed size X:", self.sp_bed_x)
        f_pr.addRow("Bed size Y:", self.sp_bed_y)
        f_pr.addRow("Bed size Z:", self.sp_bed_z)
        f_pr.addRow("Needle offset X:", self.sp_noff_x)
        f_pr.addRow("Needle offset Y:", self.sp_noff_y)
        f_pr.addRow("Needle dispense Z:", self.sp_noff_z)
        f_pr.addRow("X UV-shield park:", self.sp_x_park)
        f_pr.addRow("UV strip Y position:", self.sp_uv_yoff)
        f_pr.addRow("FFF tool command:", self.ed_t_fff)
        f_pr.addRow("Resin tool command:", self.ed_t_resin)
        f_pr.addRow("UV tool command:", self.ed_t_uv)
        f_pr.addRow("Resin pump axis:", self.ed_resin_axis)
        vbox.addWidget(g_pr)

        g_uv = QGroupBox("UV Hardening System")
        f_uv = QFormLayout(g_uv)
        self.sp_uv_irr = make_double_spin(u.irradiance_mw_cm2, 1.0, 500.0, 1.0, 1, "mW/cm2")
        self.sp_uv_beam = make_double_spin(u.beam_width_cm, 0.1, 20.0, 0.1, 2, "cm")
        self.sp_uv_dose = make_double_spin(u.target_dose_mj_cm2_per_mm, 5.0, 1000.0, 5.0, 1, "mJ/cm2/mm")
        self.sp_uv_passes = make_spin(u.min_passes, 1, 12)
        self.sp_uv_cool = make_spin(u.inter_pass_cooldown_ms, 0, 20000, 250, "ms")
        self.sp_uv_zfocal = make_double_spin(u.focal_z_clearance_mm, 2.0, 150.0, 1.0, 1, "mm")
        self.ed_uv_on = QLineEdit(u.uv_on_gcode)
        self.ed_uv_off = QLineEdit(u.uv_off_gcode)

        f_uv.addRow("UV irradiance:", self.sp_uv_irr)
        f_uv.addRow("Beam width:", self.sp_uv_beam)
        f_uv.addRow("Target dose / mm:", self.sp_uv_dose)
        f_uv.addRow("Min thermal passes:", self.sp_uv_passes)
        f_uv.addRow("Inter-pass cooldown:", self.sp_uv_cool)
        f_uv.addRow("Focal Z clearance:", self.sp_uv_zfocal)
        f_uv.addRow("UV ON G-code:", self.ed_uv_on)
        f_uv.addRow("UV OFF G-code:", self.ed_uv_off)
        vbox.addWidget(g_uv)
        vbox.addStretch(1)
        return wrap_scroll(w)

    def _build_engine_tab(self) -> QScrollArea:
        c = self._base_cfg
        w = QWidget()
        form = QFormLayout(w)
        self.sp_vox_pitch = make_double_spin(c.voxel_pitch_mm, 0.15, 2.0, 0.05, 2, "mm")
        self.sp_ras_pitch = make_double_spin(c.slice_raster_pitch_mm, 0.10, 1.0, 0.05, 2, "mm")
        self.sp_step_lin = make_double_spin(c.step_tol_linear, 0.005, 0.5, 0.01, 3, "mm")
        self.sp_step_ang = make_double_spin(c.step_tol_angular, 0.01, 0.5, 0.01, 3, "rad")

        form.addRow("3D SDF voxel pitch:", self.sp_vox_pitch)
        form.addRow("2D slice raster pitch:", self.sp_ras_pitch)
        form.addRow("STEP linear tol:", self.sp_step_lin)
        form.addRow("STEP angular tol:", self.sp_step_ang)

        note = QLabel(
            "Tip: Keep SDF pitch around 0.4-0.6 mm and raster pitch 0.25-0.35 mm "
            "for fast interactive previews; lower them for final production slices."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa0aa; margin-top: 8px;")
        form.addRow(note)
        return wrap_scroll(w)

    def _update_dependent_controls(self) -> None:
        is_hollow = self.cb_mode.currentText() == "hollow_shell"
        self.sp_hollow_wall.setEnabled(is_hollow)
        self.sp_inner_skin.setEnabled(is_hollow)
        self.chk_max_norm_depth.setEnabled(not is_hollow)
        self.sp_max_norm_depth.setEnabled((not is_hollow) and self.chk_max_norm_depth.isChecked())

        is_bed = self.cb_printer.currentText() == "bedslinger_prototype"
        self.sp_noff_x.setEnabled(is_bed)
        self.sp_noff_y.setEnabled(is_bed)
        self.sp_x_park.setEnabled(is_bed)
        self.sp_uv_yoff.setEnabled(is_bed)
        self.ed_t_uv.setEnabled(not is_bed)

    def bed_size_xy(self) -> Tuple[float, float]:
        return self.sp_bed_x.value(), self.sp_bed_y.value()

    def load_config(self, cfg: SlicerConfig) -> None:
        self._base_cfg = deepcopy(cfg)
        f, i, r, u, p = cfg.fff, cfg.infill, cfg.resin, cfg.uv, cfg.printer

        self.sp_layer_h.setValue(f.layer_height)
        self.sp_nozzle.setValue(f.nozzle_diameter)
        self.sp_line_w.setValue(f.line_width)
        self.sp_fil_dia.setValue(f.filament_diameter)
        self.sp_flow.setValue(f.flow_multiplier)
        self.sp_perims.setValue(f.perimeters)
        self.sp_bot_layers.setValue(f.bottom_solid_layers)
        self.sp_top_layers.setValue(f.top_solid_layers)
        self.sp_print_spd.setValue(f.print_speed_mm_s)
        self.sp_travel_spd.setValue(f.travel_speed_mm_s)
        self.sp_zhop.setValue(f.z_hop_mm)
        self.sp_retract.setValue(f.retract_length_mm)
        self.sp_retract_spd.setValue(f.retract_speed_mm_s)
        self.sp_nozzle_t.setValue(f.nozzle_temp_c)
        self.sp_bed_t.setValue(f.bed_temp_c)
        self.sp_fan.setValue(f.part_cooling_fan_pct)
        self.sp_dry_sup.setValue(f.dry_support_spacing_mm)

        self.cb_pattern.setCurrentText(i.pattern)
        self.cb_mode.setCurrentText(i.mode)
        self.sp_hollow_wall.setValue(i.hollow_wall_thickness)
        self.sp_inner_skin.setValue(i.inner_skin_thickness)
        self.sp_max_bridge.setValue(i.max_bridge_span)
        self.chk_max_norm_depth.setChecked(i.max_normal_depth is not None)
        if i.max_normal_depth is not None:
            self.sp_max_norm_depth.setValue(i.max_normal_depth)
        self.sp_cell_pitch.setValue(i.target_cell_pitch)
        self.sp_strut_r.setValue(i.strut_radius)
        self.sp_chan_clear.setValue(i.min_channel_clearance)
        self.sp_flatten_angle.setValue(i.flatten_angle_deg)
        self.sp_div_ratio.setValue(i.max_divergence_ratio)
        self.sp_smin_r.setValue(i.smin_blend_radius)
        self.sp_medial_ratio.setValue(i.medial_buffer_ratio)
        self.sp_shock_cos.setValue(i.shock_cos_threshold)

        self.sp_xmin.setValue(r.x_min_layers)
        self.sp_xmax.setValue(r.x_max_layers)
        self.sp_cure_depth.setValue(r.max_cure_depth_mm)
        self.sp_cure_eta.setValue(r.cure_depth_safety_eta)
        self.sp_headspace.setValue(r.headspace_margin_mm)
        self.sp_shrink.setValue(r.shrinkage_factor)
        self.sp_needle_r.setValue(r.needle_outer_radius_mm)
        self.sp_needle_clr.setValue(r.needle_safety_clear_mm)
        self.sp_pump_cal.setValue(r.pump_mm3_per_e_mm)
        self.sp_pump_spd.setValue(r.dispense_speed_e_mm_s)
        self.sp_suckback.setValue(r.suckback_e_mm)
        self.sp_suck_spd.setValue(r.suckback_speed_e_mm_s)
        self.sp_flow_rad.setValue(r.max_flow_radius_mm)
        self.sp_dwell.setValue(r.settle_dwell_ms)
        self.sp_bleed.setValue(r.uv_shadow_bleed_mm)
        self.sp_max_shadow.setValue(r.max_shadow_vol_ratio)

        self.cb_printer.setCurrentText(p.printer_type)
        self.sp_bed_x.setValue(p.bed_size_x)
        self.sp_bed_y.setValue(p.bed_size_y)
        self.sp_bed_z.setValue(p.bed_size_z)
        self.sp_noff_x.setValue(p.needle_offset_x)
        self.sp_noff_y.setValue(p.needle_offset_y)
        self.sp_noff_z.setValue(p.needle_offset_z)
        self.sp_x_park.setValue(p.x_uv_shield_park)
        self.sp_uv_yoff.setValue(p.uv_strip_y_offset)
        self.ed_t_fff.setText(p.tool_fff)
        self.ed_t_resin.setText(p.tool_resin)
        self.ed_t_uv.setText(p.tool_uv)
        self.ed_resin_axis.setText(p.resin_extruder_axis)

        self.sp_uv_irr.setValue(u.irradiance_mw_cm2)
        self.sp_uv_beam.setValue(u.beam_width_cm)
        self.sp_uv_dose.setValue(u.target_dose_mj_cm2_per_mm)
        self.sp_uv_passes.setValue(u.min_passes)
        self.sp_uv_cool.setValue(u.inter_pass_cooldown_ms)
        self.sp_uv_zfocal.setValue(u.focal_z_clearance_mm)
        self.ed_uv_on.setText(u.uv_on_gcode)
        self.ed_uv_off.setText(u.uv_off_gcode)

        self.sp_vox_pitch.setValue(cfg.voxel_pitch_mm)
        self.sp_ras_pitch.setValue(cfg.slice_raster_pitch_mm)
        self.sp_step_lin.setValue(cfg.step_tol_linear)
        self.sp_step_ang.setValue(cfg.step_tol_angular)
        self._update_dependent_controls()

    def read_config(self) -> SlicerConfig:
        x_min = self.sp_xmin.value()
        x_max = max(x_min, self.sp_xmax.value())
        if x_max != self.sp_xmax.value():
            self.sp_xmax.setValue(x_max)

        base_r = self._base_cfg.resin
        base_u = self._base_cfg.uv

        return SlicerConfig(
            voxel_pitch_mm=self.sp_vox_pitch.value(),
            slice_raster_pitch_mm=self.sp_ras_pitch.value(),
            step_tol_linear=self.sp_step_lin.value(),
            step_tol_angular=self.sp_step_ang.value(),
            fff=FFFSettings(
                layer_height=self.sp_layer_h.value(),
                nozzle_diameter=self.sp_nozzle.value(),
                line_width=self.sp_line_w.value(),
                filament_diameter=self.sp_fil_dia.value(),
                flow_multiplier=self.sp_flow.value(),
                perimeters=self.sp_perims.value(),
                bottom_solid_layers=self.sp_bot_layers.value(),
                top_solid_layers=self.sp_top_layers.value(),
                print_speed_mm_s=self.sp_print_spd.value(),
                travel_speed_mm_s=self.sp_travel_spd.value(),
                z_hop_mm=self.sp_zhop.value(),
                retract_length_mm=self.sp_retract.value(),
                retract_speed_mm_s=self.sp_retract_spd.value(),
                nozzle_temp_c=self.sp_nozzle_t.value(),
                bed_temp_c=self.sp_bed_t.value(),
                part_cooling_fan_pct=self.sp_fan.value(),
                dry_support_spacing_mm=self.sp_dry_sup.value(),
            ),
            infill=InfillSettings(
                pattern=self.cb_pattern.currentText(),
                mode=self.cb_mode.currentText(),
                hollow_wall_thickness=self.sp_hollow_wall.value(),
                inner_skin_thickness=self.sp_inner_skin.value(),
                max_bridge_span=self.sp_max_bridge.value(),
                max_normal_depth=(
                    self.sp_max_norm_depth.value() if self.chk_max_norm_depth.isChecked() else None
                ),
                target_cell_pitch=self.sp_cell_pitch.value(),
                strut_radius=self.sp_strut_r.value(),
                min_channel_clearance=self.sp_chan_clear.value(),
                flatten_angle_deg=self.sp_flatten_angle.value(),
                max_divergence_ratio=self.sp_div_ratio.value(),
                smin_blend_radius=self.sp_smin_r.value(),
                medial_buffer_ratio=self.sp_medial_ratio.value(),
                shock_cos_threshold=self.sp_shock_cos.value(),
            ),
            resin=ResinSettings(
                x_min_layers=x_min,
                x_max_layers=x_max,
                max_cure_depth_mm=self.sp_cure_depth.value(),
                cure_depth_safety_eta=self.sp_cure_eta.value(),
                headspace_margin_mm=self.sp_headspace.value(),
                shrinkage_factor=self.sp_shrink.value(),
                needle_outer_radius_mm=self.sp_needle_r.value(),
                needle_safety_clear_mm=self.sp_needle_clr.value(),
                pump_mm3_per_e_mm=self.sp_pump_cal.value(),
                dispense_speed_e_mm_s=self.sp_pump_spd.value(),
                suckback_e_mm=self.sp_suckback.value(),
                suckback_speed_e_mm_s=self.sp_suck_spd.value(),
                max_flow_radius_mm=self.sp_flow_rad.value(),
                settle_dwell_ms=self.sp_dwell.value(),
                uv_shadow_bleed_mm=self.sp_bleed.value(),
                max_shadow_vol_ratio=self.sp_max_shadow.value(),
                dp_w_cycle=base_r.dp_w_cycle,
                dp_w_short=base_r.dp_w_short,
                dp_w_shadow=base_r.dp_w_shadow,
            ),
            uv=UVSettings(
                irradiance_mw_cm2=self.sp_uv_irr.value(),
                beam_width_cm=self.sp_uv_beam.value(),
                target_dose_mj_cm2_per_mm=self.sp_uv_dose.value(),
                min_passes=self.sp_uv_passes.value(),
                inter_pass_cooldown_ms=self.sp_uv_cool.value(),
                focal_z_clearance_mm=self.sp_uv_zfocal.value(),
                min_scan_speed_mm_s=base_u.min_scan_speed_mm_s,
                max_scan_speed_mm_s=base_u.max_scan_speed_mm_s,
                uv_on_gcode=self.ed_uv_on.text().strip() or "M106 P2 S255",
                uv_off_gcode=self.ed_uv_off.text().strip() or "M106 P2 S0",
            ),
            printer=PrinterProfile(
                printer_type=self.cb_printer.currentText(),
                bed_size_x=self.sp_bed_x.value(),
                bed_size_y=self.sp_bed_y.value(),
                bed_size_z=self.sp_bed_z.value(),
                needle_offset_x=self.sp_noff_x.value(),
                needle_offset_y=self.sp_noff_y.value(),
                needle_offset_z=self.sp_noff_z.value(),
                x_uv_shield_park=self.sp_x_park.value(),
                uv_strip_y_offset=self.sp_uv_yoff.value(),
                tool_fff=self.ed_t_fff.text().strip() or "T0",
                tool_resin=self.ed_t_resin.text().strip() or "T1",
                tool_uv=self.ed_t_uv.text().strip() or "T2",
                resin_extruder_axis=self.ed_resin_axis.text().strip() or "E",
            ),
        )
