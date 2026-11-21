"""Tests for :mod:`alveoslicer.core.config` and :mod:`alveoslicer.core.presets`."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from alveoslicer import PRESET_CATEGORIES, PresetManager, SlicerConfig
from alveoslicer.core.presets import CATEGORY_FIELDS


class TestConfig(unittest.TestCase):
    """Verify :class:`SlicerConfig` serialization and schema evolution resilience."""

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
    """Verify preset category completeness and built-in/user preset persistence."""

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
            self.assertEqual(PRESET_CATEGORIES, ("print", "filament", "resin", "printer"))
            self.assertNotIn("hybrid", PRESET_CATEGORIES)
            for cat in PRESET_CATEGORIES:
                presets = pm.list_presets(cat)
                self.assertGreaterEqual(len(presets), 3, f"Category '{cat}' should have premade presets")

            cfg = SlicerConfig()
            self.assertTrue(
                pm.apply_preset(cfg, "print", "0.20mm STANDARD FFF (Gyroid 15%, No Resin/UV)")
            )
            self.assertTrue(cfg.hybrid.is_fff_only)
            self.assertEqual(cfg.infill.infill_engine, "standard")
            self.assertEqual(cfg.infill.standard_fill_pattern, "gyroid")
            self.assertFalse(
                pm.is_modified(cfg, "print", "0.20mm STANDARD FFF (Gyroid 15%, No Resin/UV)")
            )
            self.assertTrue(
                pm.apply_preset(cfg, "print", "0.20mm CELLULAR FFF ONLY (Hexagonal, No Resin/UV)")
            )
            self.assertTrue(cfg.hybrid.is_fff_only)
            self.assertEqual(cfg.infill.infill_engine, "surface_normal_3d")

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


if __name__ == "__main__":
    unittest.main()
