"""Unit and integration tests for AlveoSlicer's modular engine, cellular patterns, FFF features, scene manager, presets, and UI."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

import numpy as np
import trimesh

from shapely.geometry import Polygon

from alveoslicer import (
    CELL_PATTERNS,
    INFILL_ENGINES,
    PRESET_CATEGORIES,
    STANDARD_INFILL_PATTERNS,
    TOP_BOTTOM_FILL_PATTERNS,
    BedslingerPrototypeHAL,
    CoreXYToolchangerHAL,
    PresetManager,
    SlicerConfig,
    VolumetricField,
    WorkspaceScene,
    compute_placeable_faces,
    create_hal,
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    cut_mesh_by_plane,
    generate_normal_dendritic_infill,
    parse_gcode,
    slice_mesh,
)
from alveoslicer.dosing import plan_cycle_dosing
from alveoslicer.geometry import (
    clean_geom,
    safe_buffer,
    safe_difference,
    safe_intersection,
    safe_union,
)
from alveoslicer.presets import CATEGORY_FIELDS
from alveoslicer.scheduler import CureCycle, compute_optimal_cure_schedule
from alveoslicer.slicing import SlicedLayer, _generate_dry_core_supports


class TestConfig(unittest.TestCase):
    def test_json_roundtrip_and_unknown_key_resilience(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.20
        cfg.fff.first_layer_height = 0.25
        cfg.fff.brim_type = "outer_only"
        cfg.fff.brim_width_mm = 5.0
        cfg.fff.support_enable = True
        cfg.fff.wipe_tower_enable = True
        cfg.infill.pattern = "diamond"
        cfg.infill.flatten_angle_deg = 25.0
        cfg.resin.dp_w_cycle = 17.5
        cfg.printer.printer_type = "corexy_toolchanger"
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cfg.json"
            cfg.to_json(p)

            data = json.loads(p.read_text(encoding="utf-8"))
            data["fff"]["future_unknown_field"] = 123
            p.write_text(json.dumps(data), encoding="utf-8")

            loaded = SlicerConfig.from_json(p)
            self.assertAlmostEqual(loaded.fff.layer_height, 0.20)
            self.assertAlmostEqual(loaded.fff.first_layer_height, 0.25)
            self.assertEqual(loaded.fff.brim_type, "outer_only")
            self.assertTrue(loaded.fff.support_enable)
            self.assertTrue(loaded.fff.wipe_tower_enable)
            self.assertEqual(loaded.infill.pattern, "diamond")
            self.assertAlmostEqual(loaded.infill.flatten_angle_deg, 25.0)
            self.assertAlmostEqual(loaded.resin.dp_w_cycle, 17.5)
            self.assertEqual(loaded.printer.printer_type, "corexy_toolchanger")
            self.assertTrue(loaded.hybrid.is_fff_only)


class TestPresets(unittest.TestCase):
    def test_category_fields_cover_entire_slicer_config_without_duplicates(self) -> None:
        cfg = SlicerConfig()
        expected = set()
        for f in fields(cfg):
            val = getattr(cfg, f.name)
            if hasattr(val, "__dataclass_fields__"):
                for sf in fields(val):
                    expected.add((f.name, sf.name))
            else:
                expected.add((None, f.name))

        seen = []
        for cat in PRESET_CATEGORIES:
            seen.extend(CATEGORY_FIELDS[cat])

        self.assertEqual(len(seen), len(set(seen)), "Duplicate field across preset categories")
        self.assertEqual(set(seen), expected, "CATEGORY_FIELDS must cover 100% of SlicerConfig")

    def test_builtin_and_user_preset_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pm = PresetManager(user_dir=Path(tmp))
            for cat in PRESET_CATEGORIES:
                presets = pm.list_presets(cat)
                self.assertGreaterEqual(len(presets), 3, f"Category '{cat}' should have premade presets")

            cfg = SlicerConfig()
            self.assertTrue(pm.apply_preset(cfg, "hybrid", "Standard FFF Only (No Resin / No UV)"))
            self.assertTrue(cfg.hybrid.is_fff_only)
            self.assertFalse(pm.is_modified(cfg, "hybrid", "Standard FFF Only (No Resin / No UV)"))

            cfg.fff.nozzle_temp_c = 245
            self.assertTrue(pm.is_modified(cfg, "filament", pm.default_preset_name("filament")))

            saved = pm.save_user_preset(
                "filament",
                "My Custom ASA",
                {
                    "fff.filament_diameter": 1.75,
                    "fff.flow_multiplier": 0.98,
                    "fff.nozzle_temp_c": 255,
                    "fff.first_layer_nozzle_temp_c": 260,
                    "fff.bed_temp_c": 100,
                    "fff.first_layer_bed_temp_c": 105,
                    "fff.part_cooling_fan_pct": 15,
                    "fff.min_fan_speed_pct": 10,
                    "fff.fan_disable_first_layers": 3,
                    "fff.retract_length_mm": 0.8,
                    "fff.retract_speed_mm_s": 35.0,
                    "fff.z_hop_mm": 0.2,
                },
            )
            self.assertFalse(saved.builtin)

            pm2 = PresetManager(user_dir=Path(tmp))
            cfg2 = SlicerConfig()
            self.assertTrue(pm2.apply_preset(cfg2, "filament", "My Custom ASA"))
            self.assertEqual(cfg2.fff.nozzle_temp_c, 255)
            self.assertEqual(cfg2.fff.first_layer_bed_temp_c, 105)

            self.assertTrue(pm2.delete_user_preset("filament", "My Custom ASA"))
            self.assertIsNone(pm2.get_preset("filament", "My Custom ASA"))


class TestGeometryAndScene(unittest.TestCase):
    def test_builtin_meshes_are_watertight_and_grounded(self) -> None:
        cfg = SlicerConfig()
        for builder in (create_test_sphere_mesh, create_test_curved_wall_mesh):
            mesh = builder(cfg)
            self.assertTrue(mesh.is_watertight, f"{builder.__name__} must be watertight")
            self.assertAlmostEqual(float(mesh.bounds[0, 2]), 0.0, places=5)
            self.assertGreater(mesh.volume, 100.0)

    def test_headless_core_does_not_import_gui_libs(self) -> None:
        code = (
            "import sys, alveoslicer; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'matplotlib' not in sys.modules"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_multi_plate_instances_arrange_and_place_on_face(self) -> None:
        cfg = SlicerConfig()
        scene = WorkspaceScene()
        box = trimesh.creation.box(extents=[24.0, 16.0, 12.0])

        inst1 = scene.add_mesh(box, "Bracket", 220.0, 220.0)
        inst2 = scene.duplicate_selected(220.0, 220.0)
        self.assertIsNotNone(inst2)
        self.assertEqual(len(scene.active_plate.instances), 2)

        # Auto-arrange guarantees zero XY overlap between instances
        scene.active_plate.auto_arrange(220.0, 220.0, spacing=8.0)
        b1 = inst1.transformed_mesh().bounds
        b2 = inst2.transformed_mesh().bounds
        sep_x = (b1[1, 0] < b2[0, 0]) or (b2[1, 0] < b1[0, 0])
        sep_y = (b1[1, 1] < b2[0, 1]) or (b2[1, 1] < b1[0, 1])
        self.assertTrue(sep_x or sep_y, "Arranged instances must not overlap in XY")

        # Multi-plate creation and moving instances between plates
        plate2 = scene.add_plate()
        self.assertEqual(len(scene.plates), 2)
        self.assertEqual(plate2.name, "Plate 2")
        scene.set_active_plate(0)
        scene.selected_instance_id = inst2.instance_id
        self.assertTrue(scene.move_selected_to_plate(1, 220.0, 220.0))
        self.assertEqual(len(scene.plates[0].instances), 1)
        self.assertEqual(len(scene.plates[1].instances), 1)

        # Place-on-Face: rotate box arbitrarily, detect 6 simplified faces, and lay a tilted face flat on Z=0
        inst1.rot_x = 37.0
        inst1.rot_y = 22.0
        inst1.pos_z = 5.0
        inst1.invalidate()
        faces = compute_placeable_faces(inst1.transformed_mesh())
        self.assertEqual(len(faces), 6, "A box should cluster into exactly 6 simplified landing faces")

        # Pick a face whose normal is currently tilted upward/sideways and align it to the bed
        target_face = max(faces, key=lambda f: abs(f.normal[0]) + abs(f.normal[1]))
        inst1.align_normal_to_bed(target_face.normal)
        aligned_mesh = inst1.transformed_mesh()
        self.assertAlmostEqual(float(aligned_mesh.bounds[0, 2]), 0.0, places=4)
        # After placing one of the box's 6 faces flat on the bed, its Z height must be one of {12, 16, 24} mm
        z_ext = float(aligned_mesh.extents[2])
        min_err = min(abs(z_ext - d) for d in (12.0, 16.0, 24.0))
        self.assertLess(min_err, 0.15, f"Expected aligned box height in (12, 16, 24), got {z_ext}")

    def test_topology_exception_resilience_on_invalid_self_intersecting_polygons(self) -> None:
        # Bowtie self-intersecting polygon that triggers GEOS TopologyException in raw Shapely ops
        bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
        self.assertFalse(bowtie.is_valid)
        cleaned = clean_geom(bowtie)
        self.assertTrue(cleaned.is_valid)
        self.assertGreater(cleaned.area, 0.0)

        # Safe boolean wrappers must never crash on invalid geometries
        b2 = Polygon([(2, 0), (12, 10), (12, 0), (2, 10), (2, 0)])
        self.assertTrue(safe_union([bowtie, b2]).is_valid)
        self.assertTrue(safe_intersection(bowtie, b2).is_valid)
        self.assertTrue(safe_difference(bowtie, b2).is_valid)
        self.assertTrue(safe_buffer(bowtie, 0.4).is_valid)

        # Regression test for user screenshot crash in _generate_dry_core_supports
        dry_cores = [bowtie, bowtie, Polygon()]
        inner_skins = [Polygon(), Polygon(), Polygon([(0, 0), (12, 0), (12, 12), (0, 12)])]
        lines_all, polys_all = _generate_dry_core_supports(dry_cores, inner_skins, lw=0.42, spacing_mm=2.0)
        self.assertEqual(len(lines_all), 3)
        self.assertEqual(len(polys_all), 3)
        self.assertGreater(len(lines_all[0]), 0)
        self.assertGreater(len(lines_all[1]), 0)

    def test_watertight_3d_plane_cut_and_scene_cut(self) -> None:
        cfg = SlicerConfig()
        sphere = create_test_sphere_mesh(cfg, radius=14.0)
        orig_vol = float(sphere.volume)
        center = 0.5 * (sphere.bounds[0] + sphere.bounds[1])

        # Tilted 3D plane cut
        normal = np.array([0.25, -0.15, 1.0], dtype=np.float64)
        upper, lower = cut_mesh_by_plane(sphere, center, normal, keep_upper=True, keep_lower=True)
        self.assertIsNotNone(upper)
        self.assertIsNotNone(lower)
        self.assertTrue(upper.is_watertight, "Upper cut half must be watertight")
        self.assertTrue(lower.is_watertight, "Lower cut half must be watertight")
        self.assertAlmostEqual(
            float(upper.volume + lower.volume) / orig_vol,
            1.0,
            delta=0.05,
            msg="Cut halves must preserve total volume",
        )

        # WorkspaceScene cut replaces selected instance with 2 grounded instances
        scene = WorkspaceScene()
        scene.add_mesh(sphere, "Sphere", 220.0, 220.0)
        created = scene.cut_selected_instance(
            plane_origin=center,
            plane_normal=np.array([0.0, 0.0, 1.0]),
            bed_x=220.0,
            bed_y=220.0,
            keep_upper=True,
            keep_lower=True,
            place_upper_on_bed=True,
            place_lower_on_bed=False,
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(len(scene.active_plate.instances), 2)
        for inst in created:
            m = inst.transformed_mesh()
            self.assertAlmostEqual(float(m.bounds[0, 2]), 0.0, places=3)


class TestCellularPatterns(unittest.TestCase):
    def test_regular_hexagonal_cell_uniformity_and_all_patterns(self) -> None:
        cfg = SlicerConfig()
        cfg.voxel_pitch_mm = 0.80
        cfg.infill.target_cell_pitch = 9.0
        wall = trimesh.creation.box(extents=[54.0, 8.0, 32.0])
        wall.apply_translation([110.0, 110.0, 16.0])
        field = VolumetricField(wall, cfg.voxel_pitch_mm)

        for pat in CELL_PATTERNS:
            cfg.infill.pattern = pat
            lattice = generate_normal_dendritic_infill(field, cfg)
            self.assertGreater(len(lattice), 10, f"Pattern '{pat}' produced too few preview capsules")
            if pat == "gyroid":
                # Gyroid is a continuous TPMS implicit surface sliced analytically per Z layer
                self.assertEqual(lattice.pattern, "gyroid")
            else:
                self.assertGreater(len(lattice.cell_planes), 4, f"Pattern '{pat}' produced too few cells")

            if pat == "hexagonal":
                ratios = []
                regular_hex_count = 0
                for idx_s, planes in enumerate(lattice.cell_planes):
                    s_pt = lattice.seeds[idx_s]
                    if not (90.0 <= s_pt[0] <= 130.0 and 7.0 <= s_pt[2] <= 25.0):
                        continue
                    nearest_6 = sorted(planes, key=lambda hp: hp.dist)[:6]
                    if len(nearest_6) < 6:
                        continue
                    dists = [hp.dist for hp in nearest_6]
                    ratio = max(dists) / max(min(dists), 1e-6)
                    ratios.append(ratio)
                    n_horiz = sum(1 for hp in nearest_6 if abs(float(hp.uz)) > 0.92)
                    n_60 = sum(1 for hp in nearest_6 if 0.35 <= abs(float(hp.uz)) <= 0.65)
                    if n_horiz == 2 and n_60 == 4 and ratio < 1.12:
                        regular_hex_count += 1
                self.assertGreater(
                    regular_hex_count,
                    0,
                    "Hexagonal pattern must produce interior equal-side 0°/60° regular hexagons",
                )
                median_ratio = float(np.median(ratios))
                self.assertLess(
                    median_ratio,
                    1.12,
                    f"Hexagonal cells not regular enough: median max/min neighbor distance ratio={median_ratio:.3f}",
                )


class TestPipelineAndGCodeContract(unittest.TestCase):
    def _assert_prusaslicer_296_contract(self, gcode_text: str) -> None:
        lines = gcode_text.splitlines()
        self.assertTrue(
            lines[0].startswith("; generated by PrusaSlicer 2.9.6 on "),
            f"Unexpected line 1 header: {lines[0]}",
        )
        self.assertIn(";LAYER_CHANGE", lines)
        self.assertTrue(any(ln.startswith(";Z:") for ln in lines))
        self.assertTrue(any(ln.startswith(";HEIGHT:") for ln in lines))
        self.assertTrue(any(ln.startswith(";TYPE:External perimeter") for ln in lines))
        self.assertTrue(any(ln.startswith(";WIDTH:") for ln in lines))

        begin_idx = lines.index("; prusaslicer_config = begin")
        end_idx = lines.index("; prusaslicer_config = end")
        config_lines = [ln for ln in lines[begin_idx + 1 : end_idx] if ln.startswith("; ") and " = " in ln]
        self.assertGreaterEqual(len(config_lines), 110, "PrusaSlicer 2.9.6 requires ~115 config keys")

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
        self._assert_prusaslicer_296_contract(res.gcode_text)
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

        # Sphere has strong lower overhangs -> exercises external supports & dense interface layers
        mesh = create_test_sphere_mesh(cfg, radius=14.0)
        res = slice_mesh(mesh, cfg)

        self._assert_prusaslicer_296_contract(res.gcode_text)
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
        # Confirm parser recognizes Skirt/Brim, Wipe tower, and Support material types
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
        self._assert_prusaslicer_296_contract(res.gcode_text)
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
        self._assert_prusaslicer_296_contract(res.gcode_text)

        preview = parse_gcode(res.gcode_text)
        self.assertEqual(preview.num_layers, len(res.layers))
        self.assertEqual(len(preview.cure_layers), 0)
        self.assertEqual(len(preview.dosing_markers), 0)

    def test_gyroid_tpms_and_cubic_3d_infill_slicing(self) -> None:
        for pat in ("gyroid", "cubic"):
            cfg = SlicerConfig()
            cfg.fff.layer_height = 0.60
            cfg.slice_raster_pitch_mm = 0.60
            cfg.voxel_pitch_mm = 0.90
            cfg.infill.pattern = pat
            cfg.infill.target_cell_pitch = 8.0
            cfg.hybrid.enable_resin_pumping = False
            cfg.hybrid.enable_uv_curing = False
            mesh = create_test_curved_wall_mesh(cfg)
            res = slice_mesh(mesh, cfg)
            self._assert_prusaslicer_296_contract(res.gcode_text)
            total_infill = sum(len(ly.inside_perimeter_lines) for ly in res.layers)
            self.assertGreater(total_infill, 10, f"Pattern '{pat}' should emit internal cellular toolpaths")

    def test_organic_tree_supports_honeycomb_ironing_and_fuzzy_skin(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.60
        cfg.slice_raster_pitch_mm = 0.60
        cfg.voxel_pitch_mm = 0.90
        cfg.fff.support_enable = True
        cfg.fff.support_style = "organic"
        cfg.fff.support_pattern = "honeycomb"
        cfg.fff.support_overhang_angle_deg = 45.0
        cfg.fff.support_interface_layers = 2
        cfg.fff.support_bottom_interface_layers = 1
        cfg.fff.ironing_enable = True
        cfg.fff.fuzzy_skin_mode = "outside"
        cfg.fff.default_accel_mm_s2 = 2500.0
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

        box_overhang = create_test_sphere_mesh(cfg, radius=13.0)
        res = slice_mesh(box_overhang, cfg)
        self._assert_prusaslicer_296_contract(res.gcode_text)
        self.assertIn(";TYPE:Support material", res.gcode_text)
        self.assertIn(";TYPE:Ironing", res.gcode_text)
        self.assertIn("M204", res.gcode_text)
        preview = parse_gcode(res.gcode_text)
        self.assertGreater(len(preview.ext_p0), 100)

    def test_paint_on_supports_enforcers_only_and_blockers(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.60
        cfg.slice_raster_pitch_mm = 0.60
        cfg.voxel_pitch_mm = 0.90
        cfg.fff.support_enable = True
        cfg.fff.support_style = "snug"
        cfg.fff.support_mode = "enforcers_only"
        cfg.infill.mode = "full_to_medial"
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

        # 1. Unpainted sphere with enforcers_only -> zero external supports
        scene = WorkspaceScene()
        sphere = create_test_sphere_mesh(cfg, radius=13.0)
        inst = scene.add_mesh(sphere, "Sphere", 220.0, 220.0)
        res_empty = slice_mesh(scene.active_plate.combined_mesh(), cfg)
        self.assertEqual(sum(len(ly.ext_support_lines) for ly in res_empty.layers), 0)
        self.assertNotIn(";TYPE:Support material", res_empty.gcode_text)

        # 2. Smart-paint overhangs as Enforcers (+1) -> supports ARE generated
        n_marked = inst.smart_paint_overhangs(overhang_angle_deg=45.0)
        self.assertGreater(n_marked, 10)
        res_enforced = slice_mesh(scene.active_plate.combined_mesh(), cfg)
        self.assertGreater(sum(len(ly.ext_support_lines) for ly in res_enforced.layers), 0)
        self.assertIn(";TYPE:Support material", res_enforced.gcode_text)

        # 3. Paint all faces as Blockers (-1) in auto mode -> supports suppressed
        inst.clear_paint()
        inst.paint_support = np.full(len(inst.base_mesh.faces), -1, dtype=np.int8)
        cfg.fff.support_mode = "auto"
        res_blocked = slice_mesh(scene.active_plate.combined_mesh(), cfg)
        self.assertEqual(sum(len(ly.ext_support_lines) for ly in res_blocked.layers), 0)
        self.assertNotIn(";TYPE:Support material", res_blocked.gcode_text)

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
            # Main real resin cavity: 12x12 px = 144 px = 9.0 mm^2
            void[10:22, 10:22] = True
            # Ghost island #1: single diagonal pixel touching corner of main cavity (like screenshot #8)
            void[9, 9] = True
            # Ghost island #2: isolated 2-pixel raster sliver nearby (area = 0.125 mm^2 < 0.85 mm^2)
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

        # Must only target the single real basin (zero ghost-island stops)
        self.assertEqual(len(pts), 1, f"Expected 1 dosing point after ghost merging, got {len(pts)}: {pts}")
        self.assertGreater(pts[0].volume_mm3, 0.0)

        # Exact volume conservation: all void pixels (144 + 1 + 2 = 147 px per layer) accounted for
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
        cfg.resin.max_slab_mm = 1.20           # x_max_eff = 6 layers
        cfg.resin.min_slab_layers = 3          # x_min_eff = 3 layers -> span < 3 triggers hard_roof_closure
        cfg.resin.max_shadow_vol_ratio = 0.50

        nx, ny = 30, 30
        layers: List[SlicedLayer] = []
        empty = np.zeros((nx, ny), dtype=bool)
        # Layers 1..7: pocket A (left) + pocket B (right) both open
        # Layers 8..13: pocket A is capped solid by a roof, while pocket B continues upward!
        # That forces a mandatory cure boundary at layer 7.
        # Greedy (6 + 1) triggers hard_roof_closure at layer 7; optimal DP rebalances to (4 + 3) = ZERO hard roofs!
        for idx in range(1, 14):
            void = np.zeros((nx, ny), dtype=bool)
            if idx <= 7:
                void[4:12, 6:22] = True   # Pocket A closes after layer 7
            void[18:26, 6:22] = True      # Pocket B stays open through layer 13
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
        # Verify layer 7 is indeed a cycle end boundary (respecting airlock law for pocket A)
        self.assertIn(7, [c.end_layer for c in schedule])

    def test_standard_prusaslicer_infill_engine_patterns_and_options(self) -> None:
        """Verify Standard PrusaSlicer Infill engine works across representative patterns and options."""
        self.assertEqual(INFILL_ENGINES, ("surface_normal_3d", "standard"))
        self.assertGreaterEqual(len(STANDARD_INFILL_PATTERNS), 15)
        self.assertGreaterEqual(len(TOP_BOTTOM_FILL_PATTERNS), 6)

        test_patterns = ("gyroid", "grid", "honeycomb", "concentric", "cubic", "hilbert_curve", "lightning")
        base_cfg = SlicerConfig()
        box = trimesh.creation.box(extents=(24.0, 24.0, 6.0))
        box.apply_translation([base_cfg.printer.bed_size_x * 0.5, base_cfg.printer.bed_size_y * 0.5, 3.0])

        for pat in test_patterns:
            cfg = SlicerConfig()
            cfg.fff.layer_height = 0.60
            cfg.fff.first_layer_height = 0.40
            cfg.fff.top_solid_layers = 2
            cfg.fff.bottom_solid_layers = 2
            cfg.slice_raster_pitch_mm = 0.60
            cfg.infill.infill_engine = "standard"
            cfg.infill.standard_fill_pattern = pat
            cfg.infill.fill_density_pct = 20.0
            cfg.infill.infill_before_perimeters = (pat == "grid")
            cfg.infill.top_fill_pattern = "concentric" if pat == "gyroid" else "monotonic_lines"
            cfg.hybrid.enable_resin_pumping = False
            cfg.hybrid.enable_uv_curing = False

            res = slice_mesh(box, cfg)
            self.assertEqual(len(res.capsules), 0, f"Standard infill '{pat}' should skip 3D Voronoi capsules")
            self._assert_prusaslicer_296_contract(res.gcode_text)
            self.assertIn(";TYPE:Internal infill", res.gcode_text, f"Pattern '{pat}' missing Internal infill")
            total_infill = sum(len(ly.inside_perimeter_lines) for ly in res.layers)
            self.assertGreater(total_infill, 5, f"Standard infill pattern '{pat}' emitted too few lines")


class TestUISmoke(unittest.TestCase):
    def test_prusaslicer_ui_multi_plate_presets_transforms_and_gcode_preview(self) -> None:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        code = (
            "import tempfile\n"
            "import numpy as np\n"
            "from pathlib import Path\n"
            "from PySide6.QtWidgets import QApplication\n"
            "from alveoslicer import PresetManager, SlicerConfig, slice_mesh\n"
            "from alveoslicer.presets import CATEGORY_FIELDS, field_key\n"
            "from alveoslicer.ui import AlveoSlicerWindow\n"
            "app = QApplication([])\n"
            "with tempfile.TemporaryDirectory() as tmp:\n"
            "    pm = PresetManager(user_dir=Path(tmp))\n"
            "    win = AlveoSlicerWindow(preset_manager=pm)\n"
            "    assert win.tabs.count() == 7, win.tabs.count()\n"
            "    # Verify every single SlicerConfig field in CATEGORY_FIELDS is bound in UI tabs\n"
            "    for cat, items in CATEGORY_FIELDS.items():\n"
            "        bound = set(win.settings_tabs[cat]._bindings.keys())\n"
            "        expected = {field_key(sec, attr) for sec, attr in items}\n"
            "        assert bound == expected, f'{cat} mismatch: missing={expected - bound} extra={bound - expected}'\n"
            "    # Test Dark, Light, and System theme switching\n"
            "    win.set_theme_mode('light', save=False)\n"
            "    assert win.resolved_theme == 'light'\n"
            "    win.set_theme_mode('system', save=False)\n"
            "    assert win.resolved_theme in ('dark', 'light')\n"
            "    win.set_theme_mode('dark', save=False)\n"
            "    assert win.resolved_theme == 'dark'\n"
            "    # Test bottom-up camera hides print build plate (eye_z < 0)\n"
            "    win.plater.viewport.set_camera_preset('bottom')\n"
            "    assert win.plater.viewport.is_looking_from_bottom, 'Bottom camera must look from underneath Z<0'\n"
            "    win.plater.viewport.set_camera_preset('iso')\n"
            "    assert not win.plater.viewport.is_looking_from_bottom\n"
            "    # Test switching to Standard FFF Only preset via Hybrid settings tab\n"
            "    win._on_preset_selected('hybrid', 'Standard FFF Only (No Resin / No UV)')\n"
            "    assert win.cfg.hybrid.is_fff_only\n"
            "    assert 'STANDARD FFF' in win.plater.lbl_mode_badge.text()\n"
            "    # Test Paint-on Supports tool & smart overhang fill\n"
            "    win.plater.set_edit_tool('paint_supports')\n"
            "    win._on_paint_smart_overhangs(40.0)\n"
            "    win._on_paint_clear()\n"
            "    assert win.scene.selected_instance.paint_counts() == (0, 0)\n"
            "    # Test 3D Plane Cut tool via UI\n"
            "    win.plater.set_edit_tool('cut')\n"
            "    c = 0.5 * (win.scene.selected_instance.transformed_mesh().bounds[0] + win.scene.selected_instance.transformed_mesh().bounds[1])\n"
            "    win._on_cut_performed(float(c[0]), float(c[1]), float(c[2]), 0.0, 0.0, 1.0, True, True, False)\n"
            "    assert len(win.scene.active_plate.instances) == 2\n"
            "    assert win.plater._current_tool == 'select'\n"
            "    win._arrange_active_plate()\n"
            "    win._add_plate()\n"
            "    assert len(win.scene.plates) == 2\n"
            "    win._load_test_sphere(fit_camera=False)\n"
            "    assert len(win.scene.active_plate.instances) == 1\n"
            "    win._on_plate_selected(0)\n"
            "    assert len(win.scene.active_plate.instances) == 2\n"
            "    # Test 3D non-destructive transform & Place-on-Face\n"
            "    inst = win.scene.selected_instance\n"
            "    orig_h = float(inst.transformed_mesh().extents[2])\n"
            "    win._on_plater_transform_changed(110.0, 110.0, 0.0, 30.0, 0.0, 45.0, 150.0, 150.0, 150.0, True)\n"
            "    assert abs(inst.scale_z - 150.0) < 1e-3\n"
            "    win.plater.set_edit_tool('place_on_face')\n"
            "    faces = inst.placeable_faces()\n"
            "    assert len(faces) >= 4\n"
            "    win._on_viewport_face_picked(faces[0].normal)\n"
            "    assert win.plater._current_tool == 'select'\n"
            "    win._reset_selected_transform()\n"
            "    assert abs(float(inst.transformed_mesh().extents[2]) - orig_h) < 0.25\n"
            "    # Test slicing active plate into G-Code Previewer\n"
            "    cfg = SlicerConfig()\n"
            "    cfg.fff.layer_height = 0.60\n"
            "    cfg.fff.brim_type = 'outer_only'\n"
            "    cfg.fff.brim_width_mm = 3.0\n"
            "    cfg.fff.wipe_tower_enable = True\n"
            "    cfg.slice_raster_pitch_mm = 0.60\n"
            "    cfg.voxel_pitch_mm = 0.90\n"
            "    win.scene.remove_selected()\n"
            "    res = slice_mesh(win.current_mesh, cfg)\n"
            "    win._slicing_plate_idx = 0\n"
            "    win._on_slice_done(res)\n"
            "    assert win.plater.btn_mode_preview.isEnabled()\n"
            "    assert win.plater.viewport.mode == 'preview'\n"
            "    win.plater.layer_bar.set_top_layer(max(1, len(res.layers) // 2))\n"
            "    win.plater.set_view_mode('basins')\n"
            "    win.plater.set_view_mode('editor')\n"
            "    win.close()\n"
            "print('UI_SMOKE_ALL_OK')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("UI_SMOKE_ALL_OK", proc.stdout)

    def test_request5_ui_zero_jitter_badge_rounded_beads_filled_resin_and_multibed_grid(self) -> None:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        code = (
            "import tempfile\n"
            "import numpy as np\n"
            "from pathlib import Path\n"
            "from PySide6.QtWidgets import QApplication\n"
            "from alveoslicer import PresetManager, SlicerConfig, slice_mesh\n"
            "from alveoslicer.ui import AlveoSlicerWindow\n"
            "from alveoslicer.ui.viewport_gl import _build_extrusion_beads\n"
            "app = QApplication([])\n"
            "# 1. Rounded continuous extrusion beads: closed square loop -> 0 endcaps, open 3-seg chain -> 2 endcaps\n"
            "sq_p0 = np.array([[0,0,0.2],[10,0,0.2],[10,10,0.2],[0,10,0.2]], dtype=np.float32)\n"
            "sq_p1 = np.array([[10,0,0.2],[10,10,0.2],[0,10,0.2],[0,0,0.2]], dtype=np.float32)\n"
            "w = np.full(4, 0.45, dtype=np.float32)\n"
            "h = np.full(4, 0.20, dtype=np.float32)\n"
            "ty = np.zeros(4, dtype=np.int32)\n"
            "ly = np.ones(4, dtype=np.int32)\n"
            "beads_loop, caps_loop, _ = _build_extrusion_beads(sq_p0, sq_p1, w, h, ty, ly)\n"
            "assert beads_loop.shape == (4, 48, 9), beads_loop.shape\n"
            "assert len(caps_loop) == 0, f'Closed loop should have 0 open dome caps, got {len(caps_loop)}'\n"
            "# Miter continuity check: segment 0 end-ring vertex must exactly match segment 1 start-ring vertex\n"
            "assert np.allclose(beads_loop[0, 1, :3], beads_loop[1, 0, :3], atol=1e-4)\n"
            "op_p0 = np.array([[0,0,0.2],[10,0,0.2],[20,5,0.2]], dtype=np.float32)\n"
            "op_p1 = np.array([[10,0,0.2],[20,5,0.2],[30,5,0.2]], dtype=np.float32)\n"
            "beads_op, caps_op, caps_idx = _build_extrusion_beads(op_p0, op_p1, w[:3], h[:3], ty[:3], ly[:3])\n"
            "assert beads_op.shape == (3, 48, 9)\n"
            "assert len(caps_op) == 2 and set(caps_idx.tolist()) == {0, 2}, (len(caps_op), caps_idx)\n"
            "\n"
            "with tempfile.TemporaryDirectory() as tmp:\n"
            "    pm = PresetManager(user_dir=Path(tmp))\n"
            "    win = AlveoSlicerWindow(preset_manager=pm)\n"
            "    win.resize(1380, 860)\n"
            "    win.show()\n"
            "    app.processEvents()\n"
            "\n"
            "    # 2. Multi-bed 3D grid, empty-space deselection, and click-to-activate bed\n"
            "    win._add_plate()\n"
            "    win._load_test_sphere(fit_camera=False)\n"
            "    win._add_plate()\n"
            "    win._load_test_wall(fit_camera=False)\n"
            "    vp = win.plater.viewport\n"
            "    assert len(vp._plate_offsets) == 3, vp._plate_offsets\n"
            "    assert len(set(vp._plate_offsets)) == 3, 'All 3 beds must have distinct 3D grid positions'\n"
            "    assert win.scene.selected_instance_id is not None\n"
            "    vp.selection_cleared.emit()\n"
            "    assert win.scene.selected_instance_id is None, 'Empty-space click must deselect selected model'\n"
            "    vp.plate_selected.emit(1)\n"
            "    assert win.scene.active_plate_idx == 1, f'Expected Plate 2 active, got {win.scene.active_plate_idx}'\n"
            "\n"
            "    # 3. Standard PrusaSlicer Infill preset & UI toggle\n"
            "    win._on_preset_selected('print', '0.20mm STANDARD FFF (Gyroid 15%)')\n"
            "    assert win.cfg.infill.infill_engine == 'standard'\n"
            "    assert win.cfg.infill.standard_fill_pattern == 'gyroid'\n"
            "    win._on_preset_selected('print', pm.default_preset_name('print'))\n"
            "    assert win.cfg.infill.infill_engine == 'surface_normal_3d'\n"
            "\n"
            "    # 4. Hybrid slice -> zero-jitter UV CURE badge + post-dosing Filled resin appearance\n"
            "    cfg = SlicerConfig()\n"
            "    cfg.fff.layer_height = 0.50\n"
            "    cfg.slice_raster_pitch_mm = 0.50\n"
            "    cfg.voxel_pitch_mm = 0.85\n"
            "    res = slice_mesh(win.current_mesh, cfg)\n"
            "    assert len(res.schedule) >= 1\n"
            "    first_cure = res.schedule[0].end_layer\n"
            "    assert first_cure >= 2\n"
            "    win._slicing_plate_idx = win.scene.active_plate_idx\n"
            "    win._on_slice_done(res)\n"
            "    app.processEvents()\n"
            "\n"
            "    # Check zero-jitter LayerRangeBar across non-cure vs cure layer\n"
            "    lb = win.plater.layer_bar\n"
            "    lb.set_top_layer(first_cure - 1)\n"
            "    app.processEvents()\n"
            "    geom_non_cure = lb.track.geometry()\n"
            "    h_non_cure = lb.lbl_cure.height()\n"
            "    assert not lb.lbl_cure.isVisible()\n"
            "    lb.set_top_layer(first_cure)\n"
            "    app.processEvents()\n"
            "    geom_cure = lb.track.geometry()\n"
            "    h_cure = lb.lbl_cure.height()\n"
            "    assert lb.lbl_cure.isVisible()\n"
            "    assert geom_non_cure == geom_cure, f'Slider jumped! {geom_non_cure} vs {geom_cure}'\n"
            "    assert h_non_cure == h_cure, f'Badge slot height changed! {h_non_cure} vs {h_cure}'\n"
            "\n"
            "    # Check post-dosing Filled resin rule\n"
            "    from alveoslicer.gcode_parser import FEATURE_TYPE_TO_ID\n"
            "    resin_tid = FEATURE_TYPE_TO_ID['Filled resin']\n"
            "    assert vp.visible_types.get(resin_tid, False)\n"
            "    vp.set_preview_filter(layer_min=1, layer_max=first_cure - 1, top_move_limit=999999)\n"
            "    assert len(vp._active_resin_cpu) == 0, 'Resin must NOT appear before dosing layer is reached'\n"
            "    vp.set_preview_filter(layer_min=1, layer_max=first_cure, top_move_limit=999999)\n"
            "    assert len(vp._active_resin_cpu) > 0, 'Filled resin must flood cycle slab once dosing layer finishes'\n"
            "    vp.set_preview_filter(top_move_limit=1)\n"
            "    assert len(vp._active_resin_cpu) == 0, 'Resin must NOT appear mid-layer before FFF finishes & dosing triggers'\n"
            "    vp.set_preview_filter(top_move_limit=999999)\n"
            "    assert len(vp._active_resin_cpu) > 0\n"
            "    vt_off = dict(vp.visible_types)\n"
            "    vt_off[resin_tid] = False\n"
            "    vp.set_preview_filter(visible_types=vt_off)\n"
            "    assert len(vp._active_resin_cpu) == 0, 'Unchecking Filled resin must hide resin slabs'\n"
            "    vt_on = dict(vp.visible_types)\n"
            "    vt_on[resin_tid] = True\n"
            "    vp.set_preview_filter(visible_types=vt_on)\n"
            "    assert len(vp._active_resin_cpu) > 0\n"
            "    win.close()\n"
            "print('REQUEST5_UI_ALL_OK')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("REQUEST5_UI_ALL_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
