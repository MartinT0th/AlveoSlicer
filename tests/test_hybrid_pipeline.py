"""Tests for :mod:`alveoslicer.hybrid`, :mod:`alveoslicer.gcode`, and :mod:`alveoslicer.pipeline`."""

from __future__ import annotations

from typing import List
import unittest

import numpy as np

from alveoslicer import (
    BedslingerPrototypeHAL,
    CoreXYToolchangerHAL,
    PresetManager,
    SlicerConfig,
    create_hal,
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    parse_gcode,
    slice_mesh,
)
from alveoslicer.hybrid.dosing import plan_cycle_dosing
from alveoslicer.hybrid.scheduler import CureCycle, compute_optimal_cure_schedule
from alveoslicer.slicing.layer import SlicedLayer
from tests.test_infill_slicing import assert_prusaslicer_296_contract, make_watertight_xz_prism


class TestHybridPipelineAndGCode(unittest.TestCase):
    """Verify full hybrid/FFF pipeline, HAL safety interlocks, DP scheduler, and ghost-island dosing."""

    def _assert_hardware_safety_interlocks(self, gcode_text: str, cfg: SlicerConfig) -> None:
        lines = gcode_text.splitlines()
        for i, ln in enumerate(lines):
            if "; dispense photopolymer" in ln:
                self.assertIn("; pump suck-back", lines[i + 1])

        if cfg.printer.printer_type == "bedslinger_prototype":
            park_seen = False
            for ln in lines:
                if "; park head behind UV shield" in ln:
                    park_seen = True
                if cfg.uv.uv_on_gcode in ln:
                    self.assertTrue(park_seen, "UV ON emitted before parking needle behind shield")
                if f"{cfg.printer.tool_fff} ; return to FFF" in ln:
                    park_seen = False

    def test_curved_wall_bedslinger_pipeline_and_gcode_parser(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.50
        cfg.fff.first_layer_height = 0.40
        cfg.slice_raster_pitch_mm = 0.50
        cfg.voxel_pitch_mm = 0.80
        mesh = create_test_curved_wall_mesh(cfg)

        self.assertIsInstance(create_hal(cfg), BedslingerPrototypeHAL)
        res = slice_mesh(mesh, cfg)

        self.assertGreater(len(res.layers), 10)
        self.assertGreater(len(res.schedule), 0)
        self.assertGreater(res.total_resin_ml, 0.0)
        self.assertGreater(res.total_filament_mm, 0.0)
        self.assertGreater(res.est_print_time_s, 0.0)
        assert_prusaslicer_296_contract(self, res.gcode_text)
        self._assert_hardware_safety_interlocks(res.gcode_text, cfg)

        preview = parse_gcode(res.gcode_text)
        self.assertEqual(preview.num_layers, len(res.layers))
        self.assertGreater(len(preview.ext_p0), 50)
        self.assertGreater(len(preview.trv_p0), 10)
        self.assertGreater(len(preview.cure_layers), 0)
        self.assertGreater(len(preview.dosing_markers), 0)
        self.assertGreater(preview.total_filament_mm, 0.0)

    def test_prusaslicer_fff_features_skirt_brim_supports_wipe_tower_and_temps(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.50
        cfg.fff.first_layer_height = 0.35
        cfg.fff.perimeters = 3
        cfg.fff.external_perimeters_first = True
        cfg.fff.seam_position = "rear"
        cfg.fff.skirt_loops = 2
        cfg.fff.brim_type = "outer_only"
        cfg.fff.brim_width_mm = 4.0
        cfg.fff.support_enable = True
        cfg.fff.support_style = "grid"
        cfg.fff.support_overhang_angle_deg = 45.0
        cfg.fff.support_interface_layers = 2
        cfg.fff.wipe_tower_enable = True
        cfg.fff.wipe_tower_x = 15.0
        cfg.fff.wipe_tower_y = 15.0
        cfg.fff.first_layer_nozzle_temp_c = 220
        cfg.fff.nozzle_temp_c = 205
        cfg.fff.first_layer_bed_temp_c = 65
        cfg.fff.bed_temp_c = 55
        cfg.fff.fan_disable_first_layers = 2
        cfg.slice_raster_pitch_mm = 0.50
        cfg.voxel_pitch_mm = 0.85
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

        mesh = create_test_sphere_mesh(cfg, radius=14.0)
        res = slice_mesh(mesh, cfg)

        assert_prusaslicer_296_contract(self, res.gcode_text)
        self.assertIn(";HEIGHT:0.350", res.gcode_text)
        self.assertIn(";HEIGHT:0.500", res.gcode_text)
        self.assertIn(";TYPE:Skirt/Brim", res.gcode_text)
        self.assertIn(";TYPE:Wipe tower", res.gcode_text)
        self.assertIn(";TYPE:Top solid infill", res.gcode_text)
        self.assertIn(";TYPE:Support material", res.gcode_text)
        self.assertIn(";TYPE:Support material interface", res.gcode_text)
        self.assertIn("M109 S220", res.gcode_text)
        self.assertIn("M104 S205", res.gcode_text)
        self.assertIn("M190 S65", res.gcode_text)
        self.assertIn("M140 S55", res.gcode_text)

        preview = parse_gcode(res.gcode_text)
        self.assertEqual(preview.num_layers, len(res.layers))
        unique_types = set(int(t) for t in preview.ext_type)
        self.assertGreaterEqual(len(unique_types), 6)

    def test_sphere_corexy_pipeline(self) -> None:
        cfg = SlicerConfig()
        cfg.printer.printer_type = "corexy_toolchanger"
        cfg.fff.layer_height = 0.50
        cfg.slice_raster_pitch_mm = 0.50
        cfg.voxel_pitch_mm = 0.80
        mesh = create_test_sphere_mesh(cfg)

        self.assertIsInstance(create_hal(cfg), CoreXYToolchangerHAL)
        res = slice_mesh(mesh, cfg)

        self.assertGreater(len(res.layers), 10)
        self.assertGreater(len(res.schedule), 0)
        self.assertIn("T2 ; dock shaded resin head, pick up UV curing toolhead", res.gcode_text)
        assert_prusaslicer_296_contract(self, res.gcode_text)
        self._assert_hardware_safety_interlocks(res.gcode_text, cfg)

    def test_standard_fff_only_mode_disables_resin_and_uv(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.50
        cfg.slice_raster_pitch_mm = 0.50
        cfg.voxel_pitch_mm = 0.80
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False
        mesh = create_test_curved_wall_mesh(cfg)

        res = slice_mesh(mesh, cfg)
        self.assertEqual(len(res.schedule), 0)
        self.assertAlmostEqual(res.total_resin_ml, 0.0)
        self.assertNotIn("T1", res.gcode_text)
        self.assertNotIn(cfg.uv.uv_on_gcode, res.gcode_text)
        self.assertNotIn(cfg.uv.uv_off_gcode, res.gcode_text)
        self.assertNotIn("; dispense photopolymer", res.gcode_text)
        self.assertIn("; single_extruder_multi_material = 0", res.gcode_text)
        self.assertIn("; extruder_colour = #F5C518", res.gcode_text)
        assert_prusaslicer_296_contract(self, res.gcode_text)

        preview = parse_gcode(res.gcode_text)
        self.assertEqual(preview.num_layers, len(res.layers))
        self.assertEqual(len(preview.cure_layers), 0)
        self.assertEqual(len(preview.dosing_markers), 0)

    def test_ghost_island_suppression_and_volume_conservation(self) -> None:
        """Tiny raster ghost islands must be skipped by the pump while conserving exact total resin volume."""
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.20
        cfg.slice_raster_pitch_mm = 0.25
        cfg.resin.ghost_island_min_area_mm2 = 0.85
        cfg.resin.shrinkage_factor = 1.0
        cfg.resin.headspace_margin_mm = 0.05

        nx, ny = 40, 40
        xs = np.arange(nx, dtype=np.float64) * cfg.slice_raster_pitch_mm
        ys = np.arange(ny, dtype=np.float64) * cfg.slice_raster_pitch_mm

        def _make_layer(idx: int) -> SlicedLayer:
            void = np.zeros((nx, ny), dtype=bool)
            void[10:22, 10:22] = True
            void[9, 9] = True
            void[25, 15:17] = True
            acc = void.copy()
            solid = ~void
            empty = np.zeros((nx, ny), dtype=bool)
            return SlicedLayer(
                layer_idx=idx,
                z=idx * cfg.fff.layer_height,
                solid_mask=solid,
                shell_mask=solid,
                infill_mask=empty,
                dry_support_mask=empty,
                void_mask=void,
                accessible_mask=acc,
                layer_height=cfg.fff.layer_height,
            )

        layers = [_make_layer(1), _make_layer(2)]
        cycle = CureCycle(
            start_layer=1,
            end_layer=2,
            slab_thickness_mm=0.40,
            shadowed_vol_mm3=0.0,
            trigger="optimal_cadence",
        )
        pts, _uv_bounds = plan_cycle_dosing(layers, cycle, xs, ys, cfg)

        self.assertEqual(len(pts), 1, f"Expected 1 dosing point after ghost merging, got {len(pts)}: {pts}")
        self.assertGreater(pts[0].volume_mm3, 0.0)

        px_area = cfg.slice_raster_pitch_mm ** 2
        top_h = max(0.0, cfg.fff.layer_height - cfg.resin.headspace_margin_mm)
        expected_vol_mm3 = (
            int(np.count_nonzero(layers[0].void_mask)) * px_area * cfg.fff.layer_height
            + int(np.count_nonzero(layers[1].void_mask)) * px_area * top_h
        ) * cfg.resin.shrinkage_factor
        actual_vol_mm3 = sum(p.volume_mm3 for p in pts)
        self.assertAlmostEqual(actual_vol_mm3, expected_vol_mm3, places=5)

    def test_hard_roof_closure_minimized_by_dp_cadence_rebalancing(self) -> None:
        """DP scheduler must rebalance earlier pump cadences (e.g. 4+3) to avoid hard_roof_closure (6+1)."""
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.20
        cfg.slice_raster_pitch_mm = 0.50
        cfg.resin.max_slab_mm = 1.20
        cfg.resin.min_slab_layers = 3
        cfg.resin.max_shadow_vol_ratio = 0.50

        nx, ny = 30, 30
        layers: List[SlicedLayer] = []
        empty = np.zeros((nx, ny), dtype=bool)
        for idx in range(1, 14):
            void = np.zeros((nx, ny), dtype=bool)
            if idx <= 7:
                void[4:12, 6:22] = True
            void[18:26, 6:22] = True
            solid = ~void
            layers.append(
                SlicedLayer(
                    layer_idx=idx,
                    z=idx * cfg.fff.layer_height,
                    solid_mask=solid,
                    shell_mask=solid,
                    infill_mask=empty,
                    dry_support_mask=empty,
                    void_mask=void,
                    accessible_mask=void.copy(),
                    layer_height=cfg.fff.layer_height,
                )
            )

        schedule = compute_optimal_cure_schedule(layers, cfg)
        hard_roofs = [c for c in schedule if c.trigger == "hard_roof_closure"]
        self.assertEqual(
            len(hard_roofs),
            0,
            f"DP should rebalance earlier pump layers to avoid hard_roof_closure, got schedule={schedule}",
        )
        self.assertIn(7, [c.end_layer for c in schedule])

    def test_complete_fff_settings_spiral_vase_and_machine_limits(self) -> None:
        """Verify bridge flow/fan/accel, gap fill, thin walls, auto-cooling, M82, machine limits & spiral vase."""
        import trimesh

        bridge_arch = make_watertight_xz_prism(
            x_edges=[98.0, 103.0, 109.0, 114.0],
            z_edges=[0.0, 4.0, 6.0],
            filled_cells={(0, 0), (2, 0), (0, 1), (1, 1), (2, 1)},
        )

        cfg = SlicerConfig()
        cfg.printer.printer_capability = "fff_only"
        cfg.printer.use_relative_e_distances = False
        cfg.printer.max_feedrate_x_mm_s = 120.0
        cfg.printer.max_accel_x_mm_s2 = 3000.0
        cfg.printer.max_jerk_x_mm_s = 9.0
        cfg.fff.layer_height = 0.50
        cfg.fff.first_layer_height = 0.50
        cfg.fff.bridge_flow_ratio = 0.85
        cfg.fff.bridge_speed_mm_s = 22.0
        cfg.fff.fan_bridge_pct = 100
        cfg.fff.bridge_accel_mm_s2 = 650.0
        cfg.fff.detect_thin_walls = True
        cfg.fff.gap_fill_enabled = True
        cfg.fff.small_perimeter_speed_mm_s = 18.0
        cfg.fff.fan_auto_cooling = True
        cfg.fff.slowdown_below_layer_time_s = 20.0
        cfg.fff.min_print_speed_mm_s = 12.0
        cfg.fff.wipe_on_retract = True
        cfg.fff.retract_on_layer_change = True

        res = slice_mesh(bridge_arch, cfg)
        lines = res.gcode_text.splitlines()
        self.assertIn("M82 ; use absolute distances for extrusion", lines)
        retract_lc_indices = [idx for idx, ln in enumerate(lines) if "; retract on layer change" in ln]
        self.assertGreater(len(retract_lc_indices), 0)
        for idx in retract_lc_indices:
            self.assertEqual(lines[idx - 1], "G92 E0", "M82 must reset E to 0 BEFORE retract_on_layer_change")
        self.assertTrue(any(ln.startswith("M201 ") for ln in lines))
        self.assertTrue(any(ln.startswith("M203 ") for ln in lines))
        self.assertTrue(any(ln.startswith("M205 ") for ln in lines))
        self.assertTrue(any(ly.bridge_geom is not None and not ly.bridge_geom.is_empty for ly in res.layers))
        self.assertIn("M204 S650", lines)
        self.assertIn("M106 S255", lines)
        self.assertTrue(any("F720" in ln for ln in lines), "Expected auto-cooling slowdown down to min_print_speed_mm_s=12 (F720)")
        self.assertTrue(any(";TYPE:Gap fill" in ln or ";TYPE:Internal infill" in ln for ln in lines))

        # Test helical Z ramp and zero top layers in spiral_vase mode
        cyl = trimesh.creation.cylinder(radius=10.0, height=5.0, sections=24)
        cyl.apply_translation([110.0, 110.0, 2.5])
        vcfg = SlicerConfig()
        vcfg.printer.printer_capability = "fff_only"
        vcfg.fff.layer_height = 0.50
        vcfg.fff.first_layer_height = 0.50
        vcfg.fff.spiral_vase = True
        vcfg.fff.bottom_solid_layers = 2
        vres = slice_mesh(cyl, vcfg)
        self.assertNotIn(";TYPE:Top solid infill", vres.gcode_text)
        vase_zs = [
            float(tok[1:])
            for ln in vres.gcode_text.splitlines()
            if ln.startswith("G1 ") and " Z" in ln and " E" in ln
            for tok in ln.split()
            if tok.startswith("Z")
        ]
        self.assertGreater(len(set(vase_zs)), 20, "Spiral vase must emit smoothly ramping Z coordinates")

    def test_fff_only_printer_preset_disables_hybrid_pipeline(self) -> None:
        """Selecting Prusa MK4S or Bambu Lab X1C disables resin/UV while supporting BOTH 3D cellular & Standard FFF."""
        pm = PresetManager()
        cfg = SlicerConfig()
        # Deliberately leave hybrid switches ON in cfg and apply an FFF-only printer
        cfg.hybrid.enable_resin_pumping = True
        cfg.hybrid.enable_uv_curing = True
        cfg.fff.layer_height = 0.50
        cfg.infill.infill_engine = "surface_normal_3d"
        cfg.infill.slicing_generator = "mesh_direct"

        self.assertTrue(pm.apply_preset(cfg, "printer", "Prusa MK4S (250x210x220)"))
        self.assertFalse(cfg.is_hybrid_printer)
        self.assertTrue(cfg.effective_is_fff_only)

        wall = create_test_curved_wall_mesh(cfg)
        res = slice_mesh(wall, cfg)
        self.assertGreater(len(res.capsules), 10, "3D surface-normal cellular infill must work on FFF-only printers")
        self.assertTrue(any(len(ly.inside_perimeter_lines) > 0 for ly in res.layers))
        self.assertEqual(len(res.schedule), 0)
        self.assertEqual(res.total_resin_ml, 0.0)
        self.assertNotIn(cfg.printer.tool_resin, res.gcode_text)
        self.assertNotIn(cfg.printer.tool_uv, res.gcode_text)
        assert_prusaslicer_296_contract(self, res.gcode_text)

        self.assertTrue(pm.apply_preset(cfg, "printer", "Bambu Lab X1C (256x256x256)"))
        cfg.infill.infill_engine = "standard"
        self.assertFalse(cfg.is_hybrid_printer)
        self.assertEqual(cfg.printer.gcode_flavor, "klipper")
        self.assertEqual(cfg.printer.bed_size_x, 256.0)
        res_bambu = slice_mesh(wall, cfg)
        self.assertEqual(len(res_bambu.schedule), 0)
        self.assertEqual(res_bambu.total_resin_ml, 0.0)


if __name__ == "__main__":
    unittest.main()
