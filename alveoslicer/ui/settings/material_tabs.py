"""Material settings tab builders: Filament Settings and Resin Settings."""

from __future__ import annotations

from ...core.config import FILAMENT_TYPES, SlicerConfig
from ...core.presets import PresetManager
from .base_tab import CategorySettingsTab, make_card, make_page_container


def build_filament_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    """Construct the Filament Settings tab (Material properties, Temperatures, Cooling, Retraction)."""
    tab = CategorySettingsTab("filament", preset_manager)
    f = cfg.fff

    g_fil, ff = make_card("Filament Properties")
    ff.addRow("Material type:", tab.bind_combo("fff.filament_type", list(FILAMENT_TYPES), f.filament_type))
    tab.add_dspin_rows(ff, [
        ("Diameter:", "fff.filament_diameter", f.filament_diameter, 1.0, 3.0, 0.05, 2, "mm"),
        ("Density:", "fff.filament_density_g_cm3", f.filament_density_g_cm3, 0.5, 5.0, 0.02, 2, "g/cm³"),
        ("Cost:", "fff.filament_cost_per_kg", f.filament_cost_per_kg, 0.0, 500.0, 1.0, 2, "/kg"),
        ("Extrusion multiplier:", "fff.flow_multiplier", f.flow_multiplier, 0.5, 1.5, 0.02, 2, ""),
    ])

    g_tmp, ft = make_card("Temperature (°C)")
    ft.addRow("Nozzle (first layer):", tab.bind_spin("fff.first_layer_nozzle_temp_c", f.first_layer_nozzle_temp_c, 0, 350, 5, "°C"))
    ft.addRow("Nozzle (other layers):", tab.bind_spin("fff.nozzle_temp_c", f.nozzle_temp_c, 0, 350, 5, "°C"))
    ft.addRow("Bed (first layer):", tab.bind_spin("fff.first_layer_bed_temp_c", f.first_layer_bed_temp_c, 0, 140, 5, "°C"))
    ft.addRow("Bed (other layers):", tab.bind_spin("fff.bed_temp_c", f.bed_temp_c, 0, 140, 5, "°C"))
    ft.addRow("Chamber (0 = off):", tab.bind_spin("fff.chamber_temp_c", f.chamber_temp_c, 0, 100, 5, "°C"))
    tab.add_section("Filament", make_page_container([g_fil, g_tmp]), "filament")

    g_cool, fc = make_card("Fan Settings && Cooling Thresholds")
    fc.addRow(tab.bind_checkbox("fff.fan_auto_cooling", "Enable auto cooling", f.fan_auto_cooling))
    fc.addRow("Max fan speed:", tab.bind_spin("fff.part_cooling_fan_pct", f.part_cooling_fan_pct, 0, 100, 5, "%"))
    fc.addRow("Min fan speed:", tab.bind_spin("fff.min_fan_speed_pct", f.min_fan_speed_pct, 0, 100, 5, "%"))
    fc.addRow("Bridges fan speed:", tab.bind_spin("fff.fan_bridge_pct", f.fan_bridge_pct, 0, 100, 5, "%"))
    fc.addRow("Disable fan for the first:", tab.bind_spin("fff.fan_disable_first_layers", f.fan_disable_first_layers, 0, 10, 1, "layers"))
    fc.addRow("Full fan speed at layer:", tab.bind_spin("fff.fan_full_speed_at_layer", f.fan_full_speed_at_layer, 0, 20, 1, "layer"))
    tab.add_dspin_rows(fc, [
        ("Slow down if layer print time is below:", "fff.slowdown_below_layer_time_s", f.slowdown_below_layer_time_s, 0.0, 120.0, 1.0, 1, "s"),
        ("Min print speed:", "fff.min_print_speed_mm_s", f.min_print_speed_mm_s, 1.0, 100.0, 1.0, 1, "mm/s"),
    ])
    tab.add_section("Cooling", make_page_container([g_cool]), "cooling")

    g_ret, fr = make_card("Retraction Overrides")
    tab.add_dspin_rows(fr, [
        ("Retraction length:", "fff.retract_length_mm", f.retract_length_mm, 0.0, 12.0, 0.2, 2, "mm"),
        ("Retraction speed:", "fff.retract_speed_mm_s", f.retract_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s"),
        ("Deretraction speed:", "fff.deretract_speed_mm_s", f.deretract_speed_mm_s, 0.0, 120.0, 5.0, 1, "mm/s"),
        ("Minimum travel after retraction:", "fff.retract_min_travel_mm", f.retract_min_travel_mm, 0.0, 20.0, 0.2, 2, "mm"),
        ("Lift Z (Z-hop):", "fff.z_hop_mm", f.z_hop_mm, 0.0, 5.0, 0.1, 2, "mm"),
    ])
    fr.addRow(tab.bind_checkbox("fff.wipe_on_retract", "Wipe while retracting", f.wipe_on_retract))
    fr.addRow(tab.bind_checkbox("fff.retract_on_layer_change", "Retract on layer change", f.retract_on_layer_change))
    tab.add_section("Filament Overrides", make_page_container([g_ret]), "settings")

    tab.load_from_config(cfg)
    return tab


def build_resin_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    """Construct the Resin Settings tab (Beer-Lambert curing, Viscosity/Flow, DP Cure Scheduler)."""
    tab = CategorySettingsTab("resin", preset_manager)
    r = cfg.resin

    g_phys, fp = make_card("Beer-Lambert Curing && Meniscus")
    tab.add_dspin_rows(fp, [
        ("Max cure depth (C_d):", "resin.max_cure_depth_mm", r.max_cure_depth_mm, 0.2, 10.0, 0.1, 2, "mm"),
        ("Cure depth safety factor (η):", "resin.cure_depth_safety_eta", r.cure_depth_safety_eta, 0.5, 1.0, 0.05, 2, ""),
        ("Meniscus headspace:", "resin.headspace_margin_mm", r.headspace_margin_mm, 0.0, 1.0, 0.05, 2, "mm"),
        ("Shrinkage compensation:", "resin.shrinkage_factor", r.shrinkage_factor, 0.90, 1.25, 0.01, 3, ""),
    ])
    tab.add_section("Curing & Exposure", make_page_container([g_phys]), "uv")

    g_fluid, ff = make_card("Viscosity && Channel Flow")
    ff.addRow("Max basin flow radius:", tab.bind_dspin("resin.max_flow_radius_mm", r.max_flow_radius_mm, 2.0, 50.0, 1.0, 1, "mm"))
    ff.addRow("Viscosity leveling dwell:", tab.bind_spin("resin.settle_dwell_ms", r.settle_dwell_ms, 0, 30000, 250, "ms"))
    ff.addRow("Ghost island filter area:", tab.bind_dspin(
        "resin.ghost_island_min_area_mm2", r.ghost_island_min_area_mm2, 0.0, 10.0, 0.05, 2, "mm²",
        tooltip="Tiny rasterized 2D islands below this area are skipped by the pump and their volume is merged into the touching or nearest real basin.",
    ))
    tab.add_section("Fluid & Flow", make_page_container([g_fluid]), "resin")

    g_dp, fd = make_card("Global DP Cure-Cycle Scheduler")
    sp_xmin = tab.bind_spin("resin.x_min_layers", r.x_min_layers, 1, 20, 1, "layers")
    sp_xmax = tab.bind_spin("resin.x_max_layers", r.x_max_layers, 1, 30, 1, "layers")
    fd.addRow("Min interval (x_min):", sp_xmin)
    fd.addRow("Max interval (x_max):", sp_xmax)
    tab.add_dspin_rows(fd, [
        ("UV shadow bleed radius:", "resin.uv_shadow_bleed_mm", r.uv_shadow_bleed_mm, 0.0, 2.0, 0.05, 2, "mm"),
        ("Max shadowed void ratio:", "resin.max_shadow_vol_ratio", r.max_shadow_vol_ratio, 0.0, 0.50, 0.01, 3, ""),
        ("DP cycle base cost:", "resin.dp_w_cycle", r.dp_w_cycle, 0.0, 500.0, 1.0, 1, ""),
        ("DP short-span penalty:", "resin.dp_w_short", r.dp_w_short, 0.0, 500.0, 5.0, 1, ""),
        ("DP shadow volume penalty:", "resin.dp_w_shadow", r.dp_w_shadow, 0.0, 1000.0, 10.0, 1, ""),
    ])

    def _clamp_window() -> None:
        if sp_xmax.value() < sp_xmin.value():
            sp_xmax.setValue(sp_xmin.value())

    tab.add_post_change_hook(_clamp_window)
    tab.add_section("Cure Schedule (DP)", make_page_container([g_dp]), "layers")

    tab.load_from_config(cfg)
    return tab
