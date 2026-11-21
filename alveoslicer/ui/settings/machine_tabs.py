"""Machine settings tab builder: Printer Settings."""

from __future__ import annotations

from ...core.config import BED_SHAPES, GCODE_FLAVORS, SlicerConfig
from ...core.presets import PresetManager
from .base_tab import CategorySettingsTab, make_card, make_page_container


def build_printer_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    """Construct the Printer Settings tab (General, Extruder/Tools, Machine Limits, Custom G-code, Offsets)."""
    tab = CategorySettingsTab("printer", preset_manager)
    p, f = cfg.printer, cfg.fff

    # ---- 1. General ----
    g_gen, fg = make_card("Size, Coordinates && Firmware")
    cb_pr = tab.bind_combo("printer.printer_type", ["bedslinger_prototype", "corexy_toolchanger"], p.printer_type)
    fg.addRow("Printer HAL architecture:", cb_pr)
    fg.addRow("G-code flavor:", tab.bind_combo("printer.gcode_flavor", list(GCODE_FLAVORS), p.gcode_flavor))
    fg.addRow("Bed shape:", tab.bind_combo("printer.bed_shape", list(BED_SHAPES), p.bed_shape))
    tab.add_dspin_rows(fg, [
        ("Bed size X:", "printer.bed_size_x", p.bed_size_x, 50.0, 600.0, 10.0, 1, "mm"),
        ("Bed size Y:", "printer.bed_size_y", p.bed_size_y, 50.0, 600.0, 10.0, 1, "mm"),
        ("Max print height Z:", "printer.bed_size_z", p.bed_size_z, 50.0, 600.0, 10.0, 1, "mm"),
        ("Z offset:", "printer.z_offset_mm", p.z_offset_mm, -5.0, 5.0, 0.02, 3, "mm"),
    ])
    fg.addRow(tab.bind_checkbox("printer.use_relative_e_distances", "Use relative E distances (M83)", p.use_relative_e_distances))
    fg.addRow(tab.bind_checkbox("printer.use_firmware_retraction", "Use firmware retraction (G10 / G11)", p.use_firmware_retraction))
    tab.add_dspin_rows(fg, [
        ("Min layer height limit:", "printer.min_layer_height_mm", p.min_layer_height_mm, 0.02, 0.5, 0.01, 2, "mm"),
        ("Max layer height limit:", "printer.max_layer_height_mm", p.max_layer_height_mm, 0.10, 1.5, 0.05, 2, "mm"),
    ])
    tab.add_section("General", make_page_container([g_gen]), "printer")

    # ---- 2. Extruder & Tools ----
    g_ext, fe = make_card("Extruder && Tool Commands")
    fe.addRow("Nozzle diameter:", tab.bind_dspin("fff.nozzle_diameter", f.nozzle_diameter, 0.15, 1.5, 0.05, 2, "mm"))
    fe.addRow("FFF tool command:", tab.bind_line_edit("printer.tool_fff", p.tool_fff, fallback="T0"))
    fe.addRow("Resin pump tool command:", tab.bind_line_edit("printer.tool_resin", p.tool_resin, fallback="T1"))
    ed_uv_tool = tab.bind_line_edit("printer.tool_uv", p.tool_uv, fallback="T2")
    fe.addRow("UV toolhead command:", ed_uv_tool)
    fe.addRow("Resin pump axis:", tab.bind_line_edit("printer.resin_extruder_axis", p.resin_extruder_axis, fallback="E"))
    tab.add_section("Extruder & Tools", make_page_container([g_ext]), "filament")

    # ---- 3. Machine Limits ----
    g_lim_f, flf = make_card("Maximum Feedrates")
    tab.add_dspin_rows(flf, [
        ("Max feedrate X:", "printer.max_feedrate_x_mm_s", p.max_feedrate_x_mm_s, 10.0, 1500.0, 10.0, 0, "mm/s"),
        ("Max feedrate Y:", "printer.max_feedrate_y_mm_s", p.max_feedrate_y_mm_s, 10.0, 1500.0, 10.0, 0, "mm/s"),
        ("Max feedrate Z:", "printer.max_feedrate_z_mm_s", p.max_feedrate_z_mm_s, 2.0, 200.0, 2.0, 0, "mm/s"),
        ("Max feedrate E:", "printer.max_feedrate_e_mm_s", p.max_feedrate_e_mm_s, 5.0, 300.0, 5.0, 0, "mm/s"),
    ])

    g_lim_a, fla = make_card("Maximum Accelerations")
    tab.add_dspin_rows(fla, [
        ("Max acceleration X:", "printer.max_accel_x_mm_s2", p.max_accel_x_mm_s2, 100.0, 50000.0, 500.0, 0, "mm/s²"),
        ("Max acceleration Y:", "printer.max_accel_y_mm_s2", p.max_accel_y_mm_s2, 100.0, 50000.0, 500.0, 0, "mm/s²"),
        ("Max acceleration Z:", "printer.max_accel_z_mm_s2", p.max_accel_z_mm_s2, 50.0, 5000.0, 50.0, 0, "mm/s²"),
        ("Max acceleration E:", "printer.max_accel_e_mm_s2", p.max_accel_e_mm_s2, 100.0, 25000.0, 250.0, 0, "mm/s²"),
    ])

    g_lim_j, flj = make_card("Maximum Jerk / Junction Limits")
    tab.add_dspin_rows(flj, [
        ("Max jerk X:", "printer.max_jerk_x_mm_s", p.max_jerk_x_mm_s, 0.1, 50.0, 1.0, 1, "mm/s"),
        ("Max jerk Y:", "printer.max_jerk_y_mm_s", p.max_jerk_y_mm_s, 0.1, 50.0, 1.0, 1, "mm/s"),
        ("Max jerk Z:", "printer.max_jerk_z_mm_s", p.max_jerk_z_mm_s, 0.05, 10.0, 0.1, 2, "mm/s"),
        ("Max jerk E:", "printer.max_jerk_e_mm_s", p.max_jerk_e_mm_s, 0.1, 30.0, 0.5, 1, "mm/s"),
    ])
    tab.add_section("Machine Limits", make_page_container([g_lim_f, g_lim_a, g_lim_j]), "speed")

    # ---- 4. Custom G-code ----
    g_gc, fgc = make_card("Custom G-Code Hooks")
    fgc.addRow("Start G-code:", tab.bind_plain_text("printer.start_gcode", p.start_gcode, "; Optional custom lines appended after heating/homing", 80))
    fgc.addRow("End G-code:", tab.bind_plain_text("printer.end_gcode", p.end_gcode, "; Optional custom lines before cooldown/shutdown", 80))
    fgc.addRow("Before layer change:", tab.bind_plain_text("printer.before_layer_gcode", p.before_layer_gcode, "; Runs before each layer (supports {layer_num}, {layer_z})", 68))
    fgc.addRow("After layer change:", tab.bind_plain_text("printer.after_layer_gcode", p.after_layer_gcode, "; Runs after each layer (supports {layer_num}, {layer_z})", 68))
    tab.add_section("Custom G-code", make_page_container([g_gc]), "gcode")

    # ---- 5. Needle & UV Offsets ----
    g_off, fo = make_card("Needle && Overhead UV Strip Offsets")
    sp_nx, sp_ny, _sp_nz, sp_xp, sp_uy = tab.add_dspin_rows(fo, [
        ("Needle offset X:", "printer.needle_offset_x", p.needle_offset_x, -150.0, 150.0, 1.0, 2, "mm"),
        ("Needle offset Y:", "printer.needle_offset_y", p.needle_offset_y, -150.0, 150.0, 1.0, 2, "mm"),
        ("Needle dispense Z clearance:", "printer.needle_offset_z", p.needle_offset_z, -10.0, 20.0, 0.2, 2, "mm"),
        ("X UV-shield park position:", "printer.x_uv_shield_park", p.x_uv_shield_park, 0.0, 600.0, 5.0, 1, "mm"),
        ("Overhead UV strip Y position:", "printer.uv_strip_y_offset", p.uv_strip_y_offset, 0.0, 600.0, 5.0, 1, "mm"),
    ])

    def _sync_printer_type() -> None:
        is_bed = cb_pr.currentText() == "bedslinger_prototype"
        for w in (sp_nx, sp_ny, sp_xp, sp_uy):
            w.setEnabled(is_bed)
        ed_uv_tool.setEnabled(not is_bed)

    tab.add_post_change_hook(_sync_printer_type)
    tab.add_section("Needle & UV Offsets", make_page_container([g_off]), "uv")

    tab.load_from_config(cfg)
    return tab
