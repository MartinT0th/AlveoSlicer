"""Print Settings tab builder (Layers & Perimeters, Infill, Skirt/Brim, Supports, Speed, Wipe Tower, Advanced)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from ...core.config import (
    BRIM_TYPES,
    CELL_PATTERNS,
    FUZZY_SKIN_MODES,
    INFILL_ENGINES,
    SEAM_POSITIONS,
    STANDARD_INFILL_PATTERNS,
    SUPPORT_MODES,
    SUPPORT_PATTERNS,
    SUPPORT_STYLES,
    TOP_BOTTOM_FILL_PATTERNS,
    SlicerConfig,
)
from ...core.presets import PresetManager
from .base_tab import CategorySettingsTab, make_card, make_page_container


def build_print_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    """Construct the PrusaSlicer-style Print Settings tab covering all FFF & infill controls."""
    tab = CategorySettingsTab("print", preset_manager)
    f, i = cfg.fff, cfg.infill

    # ---- 1. Layers and perimeters ----
    g_layer, fl = make_card("Layer Height")
    tab.add_dspin_rows(fl, [
        ("Layer height:", "fff.layer_height", f.layer_height, 0.05, 1.0, 0.05, 2, "mm"),
        ("First layer height:", "fff.first_layer_height", f.first_layer_height, 0.08, 1.0, 0.05, 2, "mm"),
    ])

    g_vert, fv = make_card("Vertical Shells")
    fv.addRow("Perimeters:", tab.bind_spin("fff.perimeters", f.perimeters, 1, 12))
    fv.addRow(tab.bind_checkbox("fff.spiral_vase", "Spiral vase (single continuous perimeter, no top solid layers)", f.spiral_vase))
    fv.addRow(tab.bind_checkbox("fff.extra_perimeters_on_overhangs", "Extra perimeters on overhangs", f.extra_perimeters_on_overhangs))
    fv.addRow(tab.bind_checkbox("fff.detect_thin_walls", "Detect thin walls", f.detect_thin_walls))
    fv.addRow(tab.bind_checkbox("fff.avoid_crossing_perimeters", "Avoid crossing perimeters", f.avoid_crossing_perimeters))
    fv.addRow(tab.bind_checkbox(
        "fff.external_perimeters_first", "External perimeters first", f.external_perimeters_first,
        tooltip="Print the outermost wall before inner perimeters for sharper dimensional accuracy.",
    ))
    fv.addRow("Seam position:", tab.bind_combo("fff.seam_position", list(SEAM_POSITIONS), f.seam_position))

    g_horiz, fh = make_card("Horizontal Shells && Surface Finish")
    fh.addRow("Top solid layers:", tab.bind_spin("fff.top_solid_layers", f.top_solid_layers, 0, 30))
    fh.addRow("Bottom solid layers:", tab.bind_spin("fff.bottom_solid_layers", f.bottom_solid_layers, 0, 30))
    tab.add_dspin_rows(fh, [
        ("Minimum top shell thickness:", "fff.min_top_shell_thickness_mm", f.min_top_shell_thickness_mm, 0.0, 10.0, 0.1, 2, "mm"),
        ("Minimum bottom shell thickness:", "fff.min_bottom_shell_thickness_mm", f.min_bottom_shell_thickness_mm, 0.0, 10.0, 0.1, 2, "mm"),
        ("Solid fill angle:", "fff.infill_angle_deg", f.infill_angle_deg, 0.0, 180.0, 15.0, 1, "°"),
    ])
    chk_iron = tab.bind_checkbox("fff.ironing_enable", "Enable Top-Surface Ironing (smooth hot-nozzle finishing pass)", f.ironing_enable)
    fh.addRow(chk_iron)
    sp_iron_sp, sp_iron_fl = tab.add_dspin_rows(fh, [
        ("Ironing line spacing:", "fff.ironing_spacing_mm", f.ironing_spacing_mm, 0.05, 0.6, 0.02, 2, "mm"),
        ("Ironing flow rate:", "fff.ironing_flow_pct", f.ironing_flow_pct, 0.0, 50.0, 1.0, 1, "%"),
    ])

    g_fuzzy, ffz = make_card("Fuzzy Skin (Textured Surface)")
    cb_fuzzy = tab.bind_combo("fff.fuzzy_skin_mode", list(FUZZY_SKIN_MODES), f.fuzzy_skin_mode)
    ffz.addRow("Fuzzy skin:", cb_fuzzy)
    sp_fz_th, sp_fz_pd = tab.add_dspin_rows(ffz, [
        ("Fuzzy skin thickness:", "fff.fuzzy_skin_thickness_mm", f.fuzzy_skin_thickness_mm, 0.05, 2.0, 0.05, 2, "mm"),
        ("Fuzzy skin point distance:", "fff.fuzzy_skin_point_dist_mm", f.fuzzy_skin_point_dist_mm, 0.2, 5.0, 0.1, 2, "mm"),
    ])

    g_comp, fcmp = make_card("Dimensional Compensation")
    fcmp.addRow("Elephant foot compensation:", tab.bind_dspin(
        "fff.elephant_foot_compensation_mm", f.elephant_foot_compensation_mm, 0.0, 1.0, 0.05, 2, "mm",
        tooltip="Shrinks Layer 1 XY contour inward to compensate for first-layer squish.",
    ))
    fcmp.addRow("XY size compensation:", tab.bind_dspin(
        "fff.xy_size_compensation_mm", f.xy_size_compensation_mm, -2.0, 2.0, 0.05, 2, "mm",
        tooltip="Global XY expansion (+) or shrinkage (-) applied to all object contours.",
    ))
    fcmp.addRow("XY hole compensation:", tab.bind_dspin(
        "fff.xy_hole_compensation_mm", f.xy_hole_compensation_mm, -2.0, 2.0, 0.05, 2, "mm",
        tooltip="Dedicated XY expansion/shrinkage applied strictly to internal holes.",
    ))

    def _sync_layers_finish() -> None:
        sp_iron_sp.setEnabled(chk_iron.isChecked())
        sp_iron_fl.setEnabled(chk_iron.isChecked())
        fz_on = cb_fuzzy.currentText() != "none"
        sp_fz_th.setEnabled(fz_on)
        sp_fz_pd.setEnabled(fz_on)

    tab.add_post_change_hook(_sync_layers_finish)
    tab.add_section("Layers and perimeters", make_page_container([g_layer, g_vert, g_horiz, g_fuzzy, g_comp]), "layers")

    # ---- 2. Infill (Standard PrusaSlicer vs 3D Surface-Normal Cellular) ----
    g_eng, feng = make_card("Infill Engine")
    cb_engine = tab.bind_combo(
        "infill.infill_engine", list(INFILL_ENGINES), i.infill_engine,
        tooltip=(
            "surface_normal_3d: AlveoSlicer 3D surface-normal cellular formwork & photopolymer channels | "
            "standard: PrusaSlicer-style density-driven infill (Rectilinear, Grid, Gyroid, Honeycomb, Cubic, Lightning, etc.)"
        ),
    )
    lbl_eng_info = QLabel()
    lbl_eng_info.setWordWrap(True)
    feng.addRow("Infill architecture:", cb_engine)
    feng.addRow(lbl_eng_info)

    g_std, fstd = make_card("Standard PrusaSlicer Infill")
    fstd.addRow("Fill density:", tab.bind_dspin(
        "infill.fill_density_pct", i.fill_density_pct, 0.0, 100.0, 5.0, 1, "%",
        tooltip="Fill density percentage for Standard PrusaSlicer infill (0% = hollow, 100% = solid).",
    ))
    fstd.addRow("Fill pattern:", tab.bind_combo(
        "infill.standard_fill_pattern", list(STANDARD_INFILL_PATTERNS), i.standard_fill_pattern,
        tooltip="Standard PrusaSlicer sparse infill pattern.",
    ))
    fstd.addRow(tab.bind_checkbox(
        "infill.only_infill_where_needed", "Only infill where needed (support overhanging ceilings only)", i.only_infill_where_needed,
    ))
    fstd.addRow("Solid infill every:", tab.bind_spin(
        "infill.solid_infill_every_layers", i.solid_infill_every_layers, 0, 200, 1, "layers",
        tooltip="Force a full solid diaphragm layer every N layers (0 = disabled).",
    ))
    fstd.addRow("Solid infill threshold area:", tab.bind_dspin(
        "infill.solid_infill_threshold_area_mm2", i.solid_infill_threshold_area_mm2, 0.0, 500.0, 5.0, 1, "mm²",
        tooltip="Regions smaller than this cross-section area are printed 100% solid.",
    ))
    fstd.addRow("Length of the infill anchor:", tab.bind_dspin(
        "infill.infill_anchor_mm", i.infill_anchor_mm, 0.0, 20.0, 0.5, 2, "mm",
        tooltip="Length of infill line anchor tucked into adjacent perimeters.",
    ))
    fstd.addRow("Maximum length of the infill anchor:", tab.bind_dspin(
        "infill.infill_anchor_max_mm", i.infill_anchor_max_mm, 0.0, 50.0, 1.0, 1, "mm",
        tooltip="Maximum anchor extension along perimeter wall.",
    ))

    g_pat, fp = make_card("3D Surface-Normal Cellular && TPMS Pattern")
    fp.addRow("Cellular / TPMS pattern:", tab.bind_combo(
        "infill.pattern", list(CELL_PATTERNS), i.pattern,
        tooltip=(
            "hexagonal: equal-side regular 60°/0° honeycombs | gyroid: continuous 3D TPMS sine wave | "
            "cubic: tilted 3D isotropic cubes | diamond: 45° self-supporting | triangular: iso-grid | "
            "voronoi: organic | square: orthogonal | brick: running-bond"
        ),
    ))
    cb_mode = tab.bind_combo("infill.mode", ["hollow_shell", "full_to_medial"], i.mode)
    fp.addRow("Core topology mode:", cb_mode)
    sp_hw, sp_is, _sp_br = tab.add_dspin_rows(fp, [
        ("Hollow composite wall:", "infill.hollow_wall_thickness", i.hollow_wall_thickness, 1.5, 30.0, 0.5, 2, "mm"),
        ("Hollow inner skin:", "infill.inner_skin_thickness", i.inner_skin_thickness, 0.4, 5.0, 0.1, 2, "mm"),
        ("Max bridgeable span:", "infill.max_bridge_span", i.max_bridge_span, 2.0, 30.0, 0.5, 2, "mm"),
    ])
    chk_nd, sp_nd = tab.bind_optional_dspin(
        "infill.max_normal_depth", "Cap normal depth (isotropic core)", 10.0, 2.0, 80.0, 1.0, 1, "mm"
    )
    fp.addRow(chk_nd)
    fp.addRow("Core transition depth:", sp_nd)

    g_geo, fg = make_card("Cell Formwork && Photopolymer Channel")
    tab.add_dspin_rows(fg, [
        ("Target cell pitch:", "infill.target_cell_pitch", i.target_cell_pitch, 2.5, 25.0, 0.5, 2, "mm"),
        ("Cell wall half-thickness (r):", "infill.strut_radius", i.strut_radius, 0.20, 3.0, 0.05, 2, "mm"),
        ("Photopolymer channel width:", "infill.min_channel_clearance", i.min_channel_clearance, 0.40, 10.0, 0.1, 2, "mm"),
        ("Flatten shallow faces (<):", "infill.flatten_angle_deg", i.flatten_angle_deg, 0.0, 80.0, 1.0, 1, "°"),
        ("Cell corner fillet radius:", "infill.smin_blend_radius", i.smin_blend_radius, 0.0, 2.0, 0.05, 2, "mm"),
        ("Divergence split ratio:", "infill.max_divergence_ratio", i.max_divergence_ratio, 1.2, 3.0, 0.1, 2, ""),
        ("Medial weld ratio:", "infill.medial_buffer_ratio", i.medial_buffer_ratio, 0.50, 0.99, 0.02, 2, ""),
        ("Shock cos threshold:", "infill.shock_cos_threshold", i.shock_cos_threshold, -0.5, 0.95, 0.05, 2, ""),
    ])

    g_inf_adv, fia = make_card("Top / Bottom Patterns, Anchoring && Bridging")
    fia.addRow("Top fill pattern:", tab.bind_combo("infill.top_fill_pattern", list(TOP_BOTTOM_FILL_PATTERNS), i.top_fill_pattern))
    fia.addRow("Bottom fill pattern:", tab.bind_combo("infill.bottom_fill_pattern", list(TOP_BOTTOM_FILL_PATTERNS), i.bottom_fill_pattern))
    tab.add_dspin_rows(fia, [
        ("Infill/perimeters overlap:", "fff.infill_overlap_pct", f.infill_overlap_pct, 0.0, 60.0, 5.0, 1, "%"),
        ("Bridge flow ratio:", "fff.bridge_flow_ratio", f.bridge_flow_ratio, 0.5, 1.5, 0.05, 2, ""),
        ("Bridging angle (0 = auto):", "infill.bridge_angle_deg", i.bridge_angle_deg, 0.0, 180.0, 15.0, 1, "°"),
    ])
    fia.addRow("Combine infill every:", tab.bind_spin("fff.combine_infill_every_n_layers", f.combine_infill_every_n_layers, 1, 5, 1, "layers"))
    fia.addRow(tab.bind_checkbox("infill.infill_before_perimeters", "Infill before perimeters (print infill before outer shells)", i.infill_before_perimeters))

    def _sync_mode() -> None:
        is_std = cb_engine.currentText() == "standard"
        g_std.setEnabled(is_std)
        g_pat.setEnabled(not is_std)
        g_geo.setEnabled(not is_std)
        if is_std:
            lbl_eng_info.setObjectName("modeBannerFFF")
            lbl_eng_info.setText(
                "Standard PrusaSlicer Infill Active — Uses density-driven sparse infill patterns "
                "(Gyroid, Grid, Honeycomb, Cubic, Lightning, etc.) without 3D Voronoi formwork."
            )
        else:
            lbl_eng_info.setObjectName("modeBannerHybrid")
            lbl_eng_info.setText(
                "3D Surface-Normal Cellular Infill Active — Synthesizes surface-conformal cellular "
                "formwork walls and connected photopolymer resin channels."
            )
        lbl_eng_info.style().unpolish(lbl_eng_info)
        lbl_eng_info.style().polish(lbl_eng_info)

        is_hollow = (not is_std) and (cb_mode.currentText() == "hollow_shell")
        sp_hw.setEnabled(is_hollow)
        sp_is.setEnabled(is_hollow)
        chk_nd.setEnabled((not is_std) and (not is_hollow))
        sp_nd.setEnabled((not is_std) and (not is_hollow) and chk_nd.isChecked())

    tab.add_post_change_hook(_sync_mode)
    tab.add_section("Infill", make_page_container([g_eng, g_std, g_pat, g_geo, g_inf_adv]), "infill")

    # ---- 3. Skirt and brim ----
    g_sk, fsk = make_card("Skirt && Draft Shield")
    fsk.addRow("Loops (minimum):", tab.bind_spin("fff.skirt_loops", f.skirt_loops, 0, 10))
    fsk.addRow("Distance from object:", tab.bind_dspin("fff.skirt_distance_mm", f.skirt_distance_mm, 0.0, 30.0, 1.0, 1, "mm"))
    fsk.addRow("Skirt height:", tab.bind_spin("fff.skirt_height_layers", f.skirt_height_layers, 1, 50, 1, "layers"))
    fsk.addRow("Minimum extrusion length:", tab.bind_dspin("fff.skirt_min_length_mm", f.skirt_min_length_mm, 0.0, 500.0, 5.0, 1, "mm"))
    fsk.addRow(tab.bind_checkbox("fff.draft_shield_enable", "Draft shield (full-height skirt around part to prevent warping)", f.draft_shield_enable))

    g_br, fbr = make_card("Brim")
    cb_brim = tab.bind_combo("fff.brim_type", list(BRIM_TYPES), f.brim_type)
    fbr.addRow("Brim type:", cb_brim)
    sp_brim_w, sp_brim_gap = tab.add_dspin_rows(fbr, [
        ("Brim width:", "fff.brim_width_mm", f.brim_width_mm, 0.0, 30.0, 1.0, 1, "mm"),
        ("Brim separation gap:", "fff.brim_separation_gap_mm", f.brim_separation_gap_mm, 0.0, 2.0, 0.05, 2, "mm"),
    ])

    def _sync_brim() -> None:
        en = cb_brim.currentText() != "no_brim"
        sp_brim_w.setEnabled(en)
        sp_brim_gap.setEnabled(en)

    tab.add_post_change_hook(_sync_brim)
    tab.add_section("Skirt and brim", make_page_container([g_sk, g_br]), "skirt")

    # ---- 4. Support material ----
    g_sup, fsu = make_card("External Overhang && Paint-on Supports")
    chk_sup = tab.bind_checkbox("fff.support_enable", "Generate support material", f.support_enable)
    cb_sup_mode = tab.bind_combo(
        "fff.support_mode", list(SUPPORT_MODES), f.support_mode,
        tooltip="auto: overhangs + paint enforcers - blockers | enforcers_only: only painted green regions",
    )
    cb_sup_style = tab.bind_combo(
        "fff.support_style", list(SUPPORT_STYLES), f.support_style,
        tooltip="organic: branching tree supports | snug: tight overhang columns | grid: full rectangular columns",
    )
    cb_sup_pat = tab.bind_combo("fff.support_pattern", list(SUPPORT_PATTERNS), f.support_pattern)
    sp_sup_ang = tab.bind_dspin("fff.support_overhang_angle_deg", f.support_overhang_angle_deg, 10.0, 80.0, 5.0, 1, "°")
    chk_sup_bp = tab.bind_checkbox("fff.support_buildplate_only", "Support on build plate only", f.support_buildplate_only)
    chk_sup_br = tab.bind_checkbox("fff.support_dont_support_bridges", "Don't support bridges", f.support_dont_support_bridges)
    sp_sup_xy = tab.bind_dspin("fff.support_xy_distance_mm", f.support_xy_distance_mm, 0.10, 5.0, 0.10, 2, "mm")
    sp_sup_z = tab.bind_dspin("fff.support_z_distance_mm", f.support_z_distance_mm, 0.0, 1.0, 0.05, 2, "mm")
    sp_sup_bz = tab.bind_dspin("fff.support_bottom_z_distance_mm", f.support_bottom_z_distance_mm, 0.0, 1.0, 0.05, 2, "mm")
    sp_sup_sp = tab.bind_dspin("fff.support_spacing_mm", f.support_spacing_mm, 0.8, 12.0, 0.25, 2, "mm")
    sp_sup_if_l = tab.bind_spin("fff.support_interface_layers", f.support_interface_layers, 0, 8, 1, "layers")
    sp_sup_bif_l = tab.bind_spin("fff.support_bottom_interface_layers", f.support_bottom_interface_layers, 0, 8, 1, "layers")
    sp_sup_if_s = tab.bind_dspin("fff.support_interface_spacing_mm", f.support_interface_spacing_mm, 0.2, 4.0, 0.1, 2, "mm")
    sp_sup_raft = tab.bind_spin("fff.support_raft_layers", f.support_raft_layers, 0, 10, 1, "layers")
    sp_sup_exp = tab.bind_dspin("fff.support_first_layer_expansion_mm", f.support_first_layer_expansion_mm, 0.0, 15.0, 0.5, 1, "mm")

    fsu.addRow(chk_sup)
    for lbl, w in [
        ("Support generation mode:", cb_sup_mode), ("Style:", cb_sup_style), ("Pattern:", cb_sup_pat),
        ("Overhang threshold:", sp_sup_ang), (None, chk_sup_bp), (None, chk_sup_br),
        ("XY separation:", sp_sup_xy), ("Top contact Z distance:", sp_sup_z), ("Bottom contact Z distance:", sp_sup_bz),
        ("Pattern spacing:", sp_sup_sp), ("Top interface layers:", sp_sup_if_l), ("Bottom interface layers:", sp_sup_bif_l),
        ("Interface pattern spacing:", sp_sup_if_s), ("Raft layers:", sp_sup_raft), ("First layer expansion:", sp_sup_exp),
    ]:
        fsu.addRow(w) if lbl is None else fsu.addRow(lbl, w)

    g_org, forg = make_card("Organic Tree Supports")
    sp_org_dia, sp_org_ang = tab.add_dspin_rows(forg, [
        ("Tip / branch diameter:", "fff.support_branch_diameter_mm", f.support_branch_diameter_mm, 0.8, 12.0, 0.2, 1, "mm"),
        ("Max branch lean angle:", "fff.support_branch_angle_deg", f.support_branch_angle_deg, 15.0, 65.0, 5.0, 1, "°"),
    ])

    sup_controls = (
        cb_sup_mode, cb_sup_style, cb_sup_pat, sp_sup_ang, chk_sup_bp, chk_sup_br,
        sp_sup_xy, sp_sup_z, sp_sup_bz, sp_sup_sp, sp_sup_if_l, sp_sup_bif_l, sp_sup_if_s, sp_sup_raft, sp_sup_exp,
    )

    def _sync_support() -> None:
        en = chk_sup.isChecked()
        for w in sup_controls:
            w.setEnabled(en)
        is_org = en and cb_sup_style.currentText() == "organic"
        sp_org_dia.setEnabled(is_org)
        sp_org_ang.setEnabled(is_org)

    tab.add_post_change_hook(_sync_support)

    g_dry, fdry = make_card("Hollow Dry-Core Internal Support")
    fdry.addRow("Internal roof support spacing:", tab.bind_dspin("fff.dry_support_spacing_mm", f.dry_support_spacing_mm, 1.5, 20.0, 0.5, 1, "mm"))
    note_sup = QLabel(
        "Sparse dry FFF support is automatically projected strictly underneath overhanging inner ceilings "
        "inside the dry hollow core (zero resin contact)."
    )
    note_sup.setObjectName("mutedLabel")
    note_sup.setWordWrap(True)
    fdry.addRow(note_sup)
    tab.add_section("Support material", make_page_container([g_sup, g_org, g_dry]), "support")

    # ---- 5. Speed & Acceleration ----
    g_spd, fsp = make_card("Speed for Print Moves")
    tab.add_dspin_rows(fsp, [
        ("Perimeters:", "fff.perimeter_speed_mm_s", f.perimeter_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Small perimeters:", "fff.small_perimeter_speed_mm_s", f.small_perimeter_speed_mm_s, 5.0, 300.0, 5.0, 1, "mm/s"),
        ("External perimeters:", "fff.external_perimeter_speed_mm_s", f.external_perimeter_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Cellular infill / default:", "fff.print_speed_mm_s", f.print_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Solid infill:", "fff.solid_infill_speed_mm_s", f.solid_infill_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Top solid infill:", "fff.top_solid_infill_speed_mm_s", f.top_solid_infill_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Bridges:", "fff.bridge_speed_mm_s", f.bridge_speed_mm_s, 5.0, 200.0, 5.0, 1, "mm/s"),
        ("Gap fill:", "fff.gap_fill_speed_mm_s", f.gap_fill_speed_mm_s, 5.0, 200.0, 5.0, 1, "mm/s"),
        ("Support material:", "fff.support_speed_mm_s", f.support_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"),
        ("Support material interface:", "fff.support_interface_speed_mm_s", f.support_interface_speed_mm_s, 5.0, 250.0, 5.0, 1, "mm/s"),
    ])

    g_trv, ftr = make_card("First Layer && Non-Print Moves")
    tab.add_dspin_rows(ftr, [
        ("First layer speed:", "fff.first_layer_speed_mm_s", f.first_layer_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s"),
        ("Travel speed:", "fff.travel_speed_mm_s", f.travel_speed_mm_s, 20.0, 600.0, 10.0, 1, "mm/s"),
        ("Z travel speed:", "fff.z_travel_speed_mm_s", f.z_travel_speed_mm_s, 2.0, 80.0, 2.0, 1, "mm/s"),
    ])

    g_acc, facc = make_card("Acceleration Control && Volumetric Autospeed")
    tab.add_dspin_rows(facc, [
        ("Default acceleration:", "fff.default_accel_mm_s2", f.default_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"),
        ("Perimeters acceleration:", "fff.perimeter_accel_mm_s2", f.perimeter_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"),
        ("External perimeters accel:", "fff.external_perimeter_accel_mm_s2", f.external_perimeter_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"),
        ("Infill acceleration:", "fff.infill_accel_mm_s2", f.infill_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"),
        ("Bridge acceleration:", "fff.bridge_accel_mm_s2", f.bridge_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"),
        ("First layer acceleration:", "fff.first_layer_accel_mm_s2", f.first_layer_accel_mm_s2, 0.0, 15000.0, 250.0, 0, "mm/s²"),
        ("Max volumetric speed:", "fff.max_volumetric_speed_mm3_s", f.max_volumetric_speed_mm3_s, 0.0, 100.0, 1.0, 1, "mm³/s"),
    ])
    tab.add_section("Speed", make_page_container([g_spd, g_trv, g_acc]), "speed")

    # ---- 6. Wipe tower ----
    g_wt, fwt = make_card("Purge / Wipe Tower")
    chk_wt = tab.bind_checkbox("fff.wipe_tower_enable", "Enable wipe tower", f.wipe_tower_enable)
    fwt.addRow(chk_wt)
    wt_spins = tab.add_dspin_rows(fwt, [
        ("Position X:", "fff.wipe_tower_x", f.wipe_tower_x, 0.0, 550.0, 5.0, 1, "mm"),
        ("Position Y:", "fff.wipe_tower_y", f.wipe_tower_y, 0.0, 550.0, 5.0, 1, "mm"),
        ("Width:", "fff.wipe_tower_width_mm", f.wipe_tower_width_mm, 10.0, 120.0, 5.0, 1, "mm"),
        ("Depth:", "fff.wipe_tower_depth_mm", f.wipe_tower_depth_mm, 6.0, 80.0, 2.0, 1, "mm"),
        ("Brim width:", "fff.wipe_tower_brim_mm", f.wipe_tower_brim_mm, 0.0, 15.0, 0.5, 1, "mm"),
        ("Prime volume per layer:", "fff.wipe_tower_prime_volume_mm3", f.wipe_tower_prime_volume_mm3, 2.0, 120.0, 2.0, 1, "mm³"),
    ])

    def _sync_wt() -> None:
        en = chk_wt.isChecked()
        for w in wt_spins:
            w.setEnabled(en)

    tab.add_post_change_hook(_sync_wt)
    tab.add_section("Wipe tower", make_page_container([g_wt]), "tower")

    # ---- 7. Hybrid Mode, Resin Pump & Needle, and UV Hardening ----
    r, u, h = cfg.resin, cfg.uv, cfg.hybrid

    g_hmode, fhm = make_card("Hybrid Process Switches")
    banner = QLabel()
    banner.setWordWrap(True)
    chk_pump = tab.bind_checkbox(
        "hybrid.enable_resin_pumping",
        "Enable Photopolymer Pumping (resin && needle dosing G-code)",
        h.enable_resin_pumping,
        tooltip="Uncheck together with UV Curing to export clean G-code for a regular FFF printer.",
    )
    chk_uv = tab.bind_checkbox(
        "hybrid.enable_uv_curing",
        "Enable In-Situ UV Curing (UV LED sweeps && ON/OFF G-code)",
        h.enable_uv_curing,
        tooltip="Uncheck together with Photopolymer Pumping to export clean G-code for a regular FFF printer.",
    )
    fhm.addRow(banner)
    fhm.addRow(chk_pump)
    fhm.addRow(chk_uv)

    note_fff = QLabel(
        "Tip: Disabling both Photopolymer Pumping and UV Curing keeps AlveoSlicer's 3D surface-normal "
        "cellular geometry while emitting clean single-extruder G-code ready for any standard FFF printer."
    )
    note_fff.setObjectName("mutedLabel")
    note_fff.setWordWrap(True)
    fhm.addRow(note_fff)

    g_pump, fpump = make_card("Dispenser Pump && Needle Geometry")
    tab.add_dspin_rows(fpump, [
        ("Needle outer radius:", "resin.needle_outer_radius_mm", r.needle_outer_radius_mm, 0.1, 3.0, 0.05, 2, "mm"),
        ("Needle wall safety clearance:", "resin.needle_safety_clear_mm", r.needle_safety_clear_mm, 0.0, 2.0, 0.05, 2, "mm"),
        ("Pump calibration:", "resin.pump_mm3_per_e_mm", r.pump_mm3_per_e_mm, 0.05, 50.0, 0.1, 3, "mm³/E"),
        ("Dispense speed:", "resin.dispense_speed_e_mm_s", r.dispense_speed_e_mm_s, 0.5, 50.0, 0.5, 1, "mm/s"),
        ("Suck-back retraction:", "resin.suckback_e_mm", r.suckback_e_mm, 0.0, 10.0, 0.1, 2, "mm"),
        ("Suck-back speed:", "resin.suckback_speed_e_mm_s", r.suckback_speed_e_mm_s, 1.0, 80.0, 1.0, 1, "mm/s"),
    ])

    g_uv, fuv = make_card("UV Optics && Thermal Split-Passes")
    tab.add_dspin_rows(fuv, [
        ("UV irradiance:", "uv.irradiance_mw_cm2", u.irradiance_mw_cm2, 1.0, 500.0, 1.0, 1, "mW/cm²"),
        ("Beam width:", "uv.beam_width_cm", u.beam_width_cm, 0.1, 20.0, 0.1, 2, "cm"),
        ("Target dose / mm:", "uv.target_dose_mj_cm2_per_mm", u.target_dose_mj_cm2_per_mm, 5.0, 1000.0, 5.0, 1, "mJ/cm²/mm"),
    ])
    fuv.addRow("Min thermal passes:", tab.bind_spin("uv.min_passes", u.min_passes, 1, 12))
    fuv.addRow("Inter-pass cooldown:", tab.bind_spin("uv.inter_pass_cooldown_ms", u.inter_pass_cooldown_ms, 0, 20000, 250, "ms"))
    tab.add_dspin_rows(fuv, [
        ("Focal Z clearance:", "uv.focal_z_clearance_mm", u.focal_z_clearance_mm, 2.0, 150.0, 1.0, 1, "mm"),
        ("Min scan speed:", "uv.min_scan_speed_mm_s", u.min_scan_speed_mm_s, 0.5, 100.0, 0.5, 1, "mm/s"),
        ("Max scan speed:", "uv.max_scan_speed_mm_s", u.max_scan_speed_mm_s, 1.0, 200.0, 2.0, 1, "mm/s"),
    ])
    fuv.addRow("UV ON G-code:", tab.bind_line_edit("uv.uv_on_gcode", u.uv_on_gcode, fallback="M106 P2 S255"))
    fuv.addRow("UV OFF G-code:", tab.bind_line_edit("uv.uv_off_gcode", u.uv_off_gcode, fallback="M106 P2 S0"))

    def _sync_hybrid_toggles() -> None:
        p_on, u_on = chk_pump.isChecked(), chk_uv.isChecked()
        g_pump.setEnabled(p_on)
        g_uv.setEnabled(u_on)
        if not p_on and not u_on:
            banner.setObjectName("modeBannerFFF")
            banner.setText(
                "Standard FFF Mode Active — Resin pumping and UV curing are disabled. "
                "Output G-code is clean single-extruder FFF ready for any regular 3D printer."
            )
        elif p_on and u_on:
            banner.setObjectName("modeBannerHybrid")
            banner.setText("Full Hybrid Mode Active — Photopolymer dosing and in-situ UV curing sweeps are enabled.")
        elif p_on:
            banner.setObjectName("modeBannerFFF")
            banner.setText("Partial Hybrid Mode — Photopolymer pumping ON, in-situ UV curing OFF.")
        else:
            banner.setObjectName("modeBannerFFF")
            banner.setText("Partial Hybrid Mode — Photopolymer pumping OFF, in-situ UV curing ON.")
        banner.style().unpolish(banner)
        banner.style().polish(banner)

    tab.add_post_change_hook(_sync_hybrid_toggles)
    tab.add_section("Hybrid Mode", make_page_container([g_hmode]), "uv")
    tab.add_section("Resin Pump & Needle", make_page_container([g_pump]), "resin")
    tab.add_section("UV Hardening", make_page_container([g_uv]), "uv")

    # ---- 8. Advanced (Extrusion widths & Engine Precision) ----
    g_ew, few = make_card("Extrusion Width")
    tab.add_dspin_rows(few, [
        ("Default extrusion width:", "fff.line_width", f.line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("First layer:", "fff.first_layer_line_width", f.first_layer_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("Perimeters:", "fff.perimeter_line_width", f.perimeter_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("External perimeters:", "fff.external_perimeter_line_width", f.external_perimeter_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("Cellular infill:", "fff.infill_line_width", f.infill_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("Solid infill:", "fff.solid_infill_line_width", f.solid_infill_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("Top solid infill:", "fff.top_infill_line_width", f.top_infill_line_width, 0.20, 2.0, 0.02, 2, "mm"),
        ("Support material:", "fff.support_line_width", f.support_line_width, 0.20, 2.0, 0.02, 2, "mm"),
    ])

    g_eng2, fe = make_card("Slicing Engine && CAD Tessellation")
    tab.add_dspin_rows(fe, [
        ("3D SDF voxel pitch:", "voxel_pitch_mm", cfg.voxel_pitch_mm, 0.15, 2.0, 0.05, 2, "mm"),
        ("2D DP/BFS raster pitch:", "slice_raster_pitch_mm", cfg.slice_raster_pitch_mm, 0.10, 1.0, 0.05, 2, "mm"),
        ("STEP linear tolerance:", "step_tol_linear", cfg.step_tol_linear, 0.005, 0.5, 0.005, 3, "mm"),
        ("STEP angular tolerance:", "step_tol_angular", cfg.step_tol_angular, 0.01, 0.5, 0.01, 3, "rad"),
    ])
    note_eng = QLabel(
        "Outer and inner walls are always sliced in exact floating-point vector geometry. "
        "The raster pitch is used exclusively for DP airlock/shadow masks and 3D fluid basin BFS."
    )
    note_eng.setObjectName("mutedLabel")
    note_eng.setWordWrap(True)
    fe.addRow(note_eng)
    tab.add_section("Advanced", make_page_container([g_ew, g_eng2]), "settings")

    tab.load_from_config(cfg)
    return tab
