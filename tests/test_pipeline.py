"""Unit and integration tests for AlveoSlicer's modular engine, presets, G-code parser, HAL contract, and UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from alveoslicer import (
    PRESET_CATEGORIES,
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
from alveoslicer.presets import CATEGORY_FIELDS


class TestConfig(unittest.TestCase):
    def test_json_roundtrip_and_unknown_key_resilience(self) -> None:
        cfg = SlicerConfig()
        cfg.fff.layer_height = 0.20
        cfg.infill.pattern = "voronoi"
        cfg.infill.flatten_angle_deg = 25.0
        cfg.resin.dp_w_cycle = 17.5
        cfg.printer.printer_type = "corexy_toolchanger"
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cfg.json"
            cfg.to_json(p)

            # Inject an extra forward-compatible key to verify graceful deserialization
            data = json.loads(p.read_text(encoding="utf-8"))
            data["fff"]["future_unknown_field"] = 123
            p.write_text(json.dumps(data), encoding="utf-8")

            loaded = SlicerConfig.from_json(p)
            self.assertAlmostEqual(loaded.fff.layer_height, 0.20)
            self.assertEqual(loaded.infill.pattern, "voronoi")
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
                self.assertGreaterEqual(len(presets), 2, f"Category '{cat}' should have premade presets")

            cfg = SlicerConfig()
            self.assertTrue(pm.apply_preset(cfg, "hybrid", "Standard FFF Only (No Resin / No UV)"))
            self.assertTrue(cfg.hybrid.is_fff_only)
            self.assertFalse(pm.is_modified(cfg, "hybrid", "Standard FFF Only (No Resin / No UV)"))

            cfg.fff.nozzle_temp_c = 245.0
            self.assertTrue(pm.is_modified(cfg, "filament", pm.default_preset_name("filament")))

            # Save a custom user preset and reload in a fresh PresetManager
            saved = pm.save_user_preset(
                "filament",
                "My Custom ASA",
                {
                    "fff.filament_diameter": 1.75,
                    "fff.flow_multiplier": 0.98,
                    "fff.nozzle_temp_c": 255.0,
                    "fff.bed_temp_c": 100.0,
                    "fff.part_cooling_fan_pct": 15,
                    "fff.retract_length_mm": 0.8,
                    "fff.retract_speed_mm_s": 35.0,
                    "fff.z_hop_mm": 0.2,
                },
            )
            self.assertFalse(saved.builtin)

            pm2 = PresetManager(user_dir=Path(tmp))
            cfg2 = SlicerConfig()
            self.assertTrue(pm2.apply_preset(cfg2, "filament", "My Custom ASA"))
            self.assertAlmostEqual(cfg2.fff.nozzle_temp_c, 255.0)
            self.assertAlmostEqual(cfg2.fff.bed_temp_c, 100.0)

            self.assertTrue(pm2.delete_user_preset("filament", "My Custom ASA"))
            self.assertIsNone(pm2.get_preset("filament", "My Custom ASA"))


class TestGeometry(unittest.TestCase):
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
        # Every dispense must be immediately followed by pump suck-back
        for i, ln in enumerate(lines):
            if "; dispense photopolymer" in ln:
                self.assertIn("; pump suck-back", lines[i + 1])

        # Bedslinger must park X behind UV shield before UV ON
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

        # Verify G-code parser produces valid preview buffers
        preview = parse_gcode(res.gcode_text)
        self.assertEqual(preview.num_layers, len(res.layers))
        self.assertGreater(len(preview.ext_p0), 50)
        self.assertGreater(len(preview.trv_p0), 10)
        self.assertGreater(len(preview.cure_layers), 0)
        self.assertGreater(len(preview.dosing_markers), 0)
        self.assertGreater(preview.total_filament_mm, 0.0)

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


class TestUISmoke(unittest.TestCase):
    def test_prusaslicer_ui_tabs_presets_transforms_and_gcode_preview(self) -> None:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        code = (
            "import tempfile\n"
            "from pathlib import Path\n"
            "from PySide6.QtWidgets import QApplication\n"
            "from alveoslicer import PresetManager, SlicerConfig, parse_gcode, slice_mesh\n"
            "from alveoslicer.ui import AlveoSlicerWindow\n"
            "app = QApplication([])\n"
            "with tempfile.TemporaryDirectory() as tmp:\n"
            "    pm = PresetManager(user_dir=Path(tmp))\n"
            "    win = AlveoSlicerWindow(preset_manager=pm)\n"
            "    assert win.tabs.count() == 7, win.tabs.count()\n"
            "    # Test switching to Standard FFF Only preset via Hybrid settings tab\n"
            "    win._on_preset_selected('hybrid', 'Standard FFF Only (No Resin / No UV)')\n"
            "    assert win.cfg.hybrid.is_fff_only\n"
            "    assert 'STANDARD FFF' in win.plater.lbl_mode_badge.text()\n"
            "    # Test editing hidden-previously DP & UV fields in tabs and reading back\n"
            "    win.cfg.resin.dp_w_cycle = 42.0\n"
            "    win.cfg.uv.max_scan_speed_mm_s = 77.0\n"
            "    win.settings_tabs['resin'].load_from_config(win.cfg)\n"
            "    win.settings_tabs['hybrid'].load_from_config(win.cfg)\n"
            "    out = win._collect_full_config()\n"
            "    assert abs(out.resin.dp_w_cycle - 42.0) < 1e-6\n"
            "    assert abs(out.uv.max_scan_speed_mm_s - 77.0) < 1e-6\n"
            "    # Test non-destructive transform\n"
            "    orig_h = float(win.current_mesh.extents[2])\n"
            "    win._on_plater_transform_changed(150.0, 45.0, 110.0, 110.0)\n"
            "    assert abs(float(win.current_mesh.extents[2]) - orig_h * 1.5) < 0.2\n"
            "    win._reset_transform()\n"
            "    assert abs(float(win.current_mesh.extents[2]) - orig_h) < 0.2\n"
            "    # Test slicing & loading into G-Code Previewer + LayerRangeBar + Move Scrubber\n"
            "    cfg = SlicerConfig()\n"
            "    cfg.fff.layer_height = 0.60\n"
            "    cfg.slice_raster_pitch_mm = 0.60\n"
            "    cfg.voxel_pitch_mm = 0.90\n"
            "    res = slice_mesh(win.current_mesh, cfg)\n"
            "    win._on_slice_done(res)\n"
            "    assert win.plater.btn_mode_preview.isEnabled()\n"
            "    assert win.plater.viewport.mode == 'preview'\n"
            "    win.plater.layer_bar.set_top_layer(max(1, len(res.layers) // 2))\n"
            "    win.plater.set_view_mode('basins')\n"
            "    win.plater.set_view_mode('editor')\n"
            "    win.close()\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
