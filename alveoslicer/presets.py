"""PrusaSlicer-style per-category preset management (built-in + persistent user presets)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SlicerConfig

PRESET_CATEGORIES: Tuple[str, ...] = ("print", "filament", "resin", "printer", "hybrid")

CATEGORY_LABELS: Dict[str, str] = {
    "print": "Print Settings",
    "filament": "Filament",
    "resin": "Resin",
    "printer": "Printer",
    "hybrid": "Hybrid",
}

# Disjoint mapping of every SlicerConfig field across the 5 preset categories.
# Each entry is (section_name_or_None_for_root, field_name).
CATEGORY_FIELDS: Dict[str, Tuple[Tuple[Optional[str], str], ...]] = {
    "print": (
        ("fff", "layer_height"),
        ("fff", "line_width"),
        ("fff", "perimeters"),
        ("fff", "bottom_solid_layers"),
        ("fff", "top_solid_layers"),
        ("fff", "print_speed_mm_s"),
        ("fff", "travel_speed_mm_s"),
        ("fff", "dry_support_spacing_mm"),
        ("infill", "pattern"),
        ("infill", "mode"),
        ("infill", "hollow_wall_thickness"),
        ("infill", "inner_skin_thickness"),
        ("infill", "max_bridge_span"),
        ("infill", "max_normal_depth"),
        ("infill", "target_cell_pitch"),
        ("infill", "strut_radius"),
        ("infill", "min_channel_clearance"),
        ("infill", "flatten_angle_deg"),
        ("infill", "max_divergence_ratio"),
        ("infill", "smin_blend_radius"),
        ("infill", "medial_buffer_ratio"),
        ("infill", "shock_cos_threshold"),
        (None, "voxel_pitch_mm"),
        (None, "slice_raster_pitch_mm"),
        (None, "step_tol_linear"),
        (None, "step_tol_angular"),
    ),
    "filament": (
        ("fff", "filament_diameter"),
        ("fff", "flow_multiplier"),
        ("fff", "nozzle_temp_c"),
        ("fff", "bed_temp_c"),
        ("fff", "part_cooling_fan_pct"),
        ("fff", "retract_length_mm"),
        ("fff", "retract_speed_mm_s"),
        ("fff", "z_hop_mm"),
    ),
    "resin": (
        ("resin", "x_min_layers"),
        ("resin", "x_max_layers"),
        ("resin", "max_cure_depth_mm"),
        ("resin", "cure_depth_safety_eta"),
        ("resin", "headspace_margin_mm"),
        ("resin", "shrinkage_factor"),
        ("resin", "max_flow_radius_mm"),
        ("resin", "settle_dwell_ms"),
        ("resin", "uv_shadow_bleed_mm"),
        ("resin", "max_shadow_vol_ratio"),
        ("resin", "dp_w_cycle"),
        ("resin", "dp_w_short"),
        ("resin", "dp_w_shadow"),
    ),
    "printer": (
        ("fff", "nozzle_diameter"),
        ("printer", "printer_type"),
        ("printer", "bed_size_x"),
        ("printer", "bed_size_y"),
        ("printer", "bed_size_z"),
        ("printer", "needle_offset_x"),
        ("printer", "needle_offset_y"),
        ("printer", "needle_offset_z"),
        ("printer", "x_uv_shield_park"),
        ("printer", "uv_strip_y_offset"),
        ("printer", "tool_fff"),
        ("printer", "tool_resin"),
        ("printer", "tool_uv"),
        ("printer", "resin_extruder_axis"),
    ),
    "hybrid": (
        ("hybrid", "enable_resin_pumping"),
        ("hybrid", "enable_uv_curing"),
        ("resin", "needle_outer_radius_mm"),
        ("resin", "needle_safety_clear_mm"),
        ("resin", "pump_mm3_per_e_mm"),
        ("resin", "dispense_speed_e_mm_s"),
        ("resin", "suckback_e_mm"),
        ("resin", "suckback_speed_e_mm_s"),
        ("uv", "irradiance_mw_cm2"),
        ("uv", "beam_width_cm"),
        ("uv", "target_dose_mj_cm2_per_mm"),
        ("uv", "min_passes"),
        ("uv", "inter_pass_cooldown_ms"),
        ("uv", "focal_z_clearance_mm"),
        ("uv", "min_scan_speed_mm_s"),
        ("uv", "max_scan_speed_mm_s"),
        ("uv", "uv_on_gcode"),
        ("uv", "uv_off_gcode"),
    ),
}


def field_key(section: Optional[str], name: str) -> str:
    return f"{section}.{name}" if section else name


def extract_category_values(cfg: SlicerConfig, category: str) -> Dict[str, Any]:
    """Extract all settings belonging to ``category`` as a flat key -> value dict."""
    out: Dict[str, Any] = {}
    for section, attr in CATEGORY_FIELDS[category]:
        obj = getattr(cfg, section) if section else cfg
        out[field_key(section, attr)] = getattr(obj, attr)
    return out


def apply_category_values(cfg: SlicerConfig, category: str, values: Dict[str, Any]) -> None:
    """Apply a flat key -> value dict onto ``cfg`` for ``category``."""
    for section, attr in CATEGORY_FIELDS[category]:
        k = field_key(section, attr)
        if k not in values:
            continue
        obj = getattr(cfg, section) if section else cfg
        setattr(obj, attr, values[k])


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6
    return a == b


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip()).strip("_")
    return slug or "preset"


@dataclass
class Preset:
    """Single named preset within one category."""

    category: str
    name: str
    values: Dict[str, Any]
    builtin: bool = False


def _make_builtin_preset(category: str, name: str, overrides: Dict[str, Any]) -> Preset:
    base = extract_category_values(SlicerConfig(), category)
    base.update(overrides)
    return Preset(category=category, name=name, values=base, builtin=True)


def _build_default_presets() -> Dict[str, List[Preset]]:
    return {
        "print": [
            _make_builtin_preset(
                "print",
                "0.25mm Hexagonal Hollow Shell",
                {},
            ),
            _make_builtin_preset(
                "print",
                "0.20mm Fine Hexagonal Shell",
                {
                    "fff.layer_height": 0.20,
                    "fff.perimeters": 3,
                    "fff.bottom_solid_layers": 4,
                    "fff.top_solid_layers": 4,
                    "fff.print_speed_mm_s": 40.0,
                    "infill.target_cell_pitch": 8.5,
                    "infill.min_channel_clearance": 1.8,
                },
            ),
            _make_builtin_preset(
                "print",
                "0.25mm Organic Voronoi Shell",
                {
                    "infill.pattern": "voronoi",
                    "infill.target_cell_pitch": 9.5,
                    "infill.smin_blend_radius": 0.35,
                },
            ),
            _make_builtin_preset(
                "print",
                "0.25mm Hexagonal Full-to-Medial",
                {
                    "infill.mode": "full_to_medial",
                    "infill.target_cell_pitch": 10.0,
                },
            ),
            _make_builtin_preset(
                "print",
                "0.30mm Fast Draft Prototype",
                {
                    "fff.layer_height": 0.30,
                    "fff.line_width": 0.50,
                    "fff.perimeters": 2,
                    "fff.print_speed_mm_s": 60.0,
                    "infill.target_cell_pitch": 12.0,
                    "voxel_pitch_mm": 0.60,
                    "slice_raster_pitch_mm": 0.30,
                },
            ),
        ],
        "filament": [
            _make_builtin_preset(
                "filament",
                "Generic PLA",
                {},
            ),
            _make_builtin_preset(
                "filament",
                "Translucent PLA (UV-Friendly)",
                {
                    "fff.nozzle_temp_c": 205,
                    "fff.bed_temp_c": 60,
                    "fff.part_cooling_fan_pct": 100,
                    "fff.retract_length_mm": 1.0,
                },
            ),
            _make_builtin_preset(
                "filament",
                "Generic PETG",
                {
                    "fff.nozzle_temp_c": 240,
                    "fff.bed_temp_c": 80,
                    "fff.part_cooling_fan_pct": 40,
                    "fff.retract_length_mm": 1.4,
                    "fff.retract_speed_mm_s": 30.0,
                    "fff.z_hop_mm": 0.8,
                },
            ),
            _make_builtin_preset(
                "filament",
                "Generic ASA / ABS",
                {
                    "fff.nozzle_temp_c": 255,
                    "fff.bed_temp_c": 100,
                    "fff.part_cooling_fan_pct": 20,
                    "fff.retract_length_mm": 0.8,
                    "fff.retract_speed_mm_s": 40.0,
                },
            ),
        ],
        "resin": [
            _make_builtin_preset(
                "resin",
                "Standard 405nm Photopolymer",
                {},
            ),
            _make_builtin_preset(
                "resin",
                "Fast-Cure Clear Resin",
                {
                    "resin.x_min_layers": 4,
                    "resin.x_max_layers": 7,
                    "resin.max_cure_depth_mm": 1.65,
                    "resin.settle_dwell_ms": 1500,
                    "resin.max_flow_radius_mm": 12.0,
                    "resin.shrinkage_factor": 1.03,
                },
            ),
            _make_builtin_preset(
                "resin",
                "Tough Engineering Resin",
                {
                    "resin.x_min_layers": 3,
                    "resin.x_max_layers": 5,
                    "resin.max_cure_depth_mm": 1.20,
                    "resin.cure_depth_safety_eta": 0.82,
                    "resin.settle_dwell_ms": 3500,
                    "resin.shrinkage_factor": 1.05,
                    "resin.max_flow_radius_mm": 8.5,
                },
            ),
            _make_builtin_preset(
                "resin",
                "High-Viscosity Structural Resin",
                {
                    "resin.x_min_layers": 2,
                    "resin.x_max_layers": 5,
                    "resin.max_cure_depth_mm": 1.15,
                    "resin.settle_dwell_ms": 4500,
                    "resin.max_flow_radius_mm": 7.0,
                    "resin.headspace_margin_mm": 0.18,
                },
            ),
        ],
        "printer": [
            _make_builtin_preset(
                "printer",
                "Alveo Prototype 1 (Bedslinger 220)",
                {},
            ),
            _make_builtin_preset(
                "printer",
                "CoreXY Toolchanger (360x360)",
                {
                    "printer.printer_type": "corexy_toolchanger",
                    "printer.bed_size_x": 360.0,
                    "printer.bed_size_y": 360.0,
                    "printer.bed_size_z": 360.0,
                    "printer.needle_offset_x": 0.0,
                    "printer.needle_offset_y": 0.0,
                    "printer.needle_offset_z": 1.0,
                },
            ),
            _make_builtin_preset(
                "printer",
                "Standard FFF Printer (250x210)",
                {
                    "printer.printer_type": "bedslinger_prototype",
                    "printer.bed_size_x": 250.0,
                    "printer.bed_size_y": 210.0,
                    "printer.bed_size_z": 220.0,
                    "printer.needle_offset_x": 0.0,
                    "printer.needle_offset_y": 0.0,
                },
            ),
        ],
        "hybrid": [
            _make_builtin_preset(
                "hybrid",
                "Standard Hybrid (Pump + UV)",
                {},
            ),
            _make_builtin_preset(
                "hybrid",
                "Thermal-Safe Multi-Pass UV (3 Passes)",
                {
                    "hybrid.enable_resin_pumping": True,
                    "hybrid.enable_uv_curing": True,
                    "uv.min_passes": 3,
                    "uv.inter_pass_cooldown_ms": 1800,
                    "uv.target_dose_mj_cm2_per_mm": 95.0,
                },
            ),
            _make_builtin_preset(
                "hybrid",
                "Standard FFF Only (No Resin / No UV)",
                {
                    "hybrid.enable_resin_pumping": False,
                    "hybrid.enable_uv_curing": False,
                },
            ),
            _make_builtin_preset(
                "hybrid",
                "Dosing Only (Manual External UV)",
                {
                    "hybrid.enable_resin_pumping": True,
                    "hybrid.enable_uv_curing": False,
                },
            ),
        ],
    }


class PresetManager:
    """Manages built-in and persistent JSON user presets across all 5 settings categories."""

    def __init__(self, user_dir: Optional[Path] = None) -> None:
        self.user_dir = user_dir if user_dir is not None else (Path.home() / ".alveoslicer" / "presets")
        self._builtins: Dict[str, List[Preset]] = _build_default_presets()
        self._user: Dict[str, Dict[str, Preset]] = {cat: {} for cat in PRESET_CATEGORIES}
        self._load_user_presets()

    def _category_dir(self, category: str) -> Path:
        return self.user_dir / category

    def _load_user_presets(self) -> None:
        for cat in PRESET_CATEGORIES:
            cdir = self._category_dir(cat)
            if not cdir.exists():
                continue
            for fp in sorted(cdir.glob("*.json")):
                try:
                    raw = json.loads(fp.read_text(encoding="utf-8"))
                    name = str(raw.get("name") or fp.stem)
                    values = dict(raw.get("values") or {})
                    if values:
                        self._user[cat][name] = Preset(
                            category=cat, name=name, values=values, builtin=False
                        )
                except Exception:
                    continue

    def list_presets(self, category: str) -> List[Preset]:
        """Return built-in presets followed by alphabetically sorted user presets."""
        builtins = list(self._builtins[category])
        users = sorted(self._user[category].values(), key=lambda p: p.name.lower())
        return builtins + users

    def preset_names(self, category: str) -> List[str]:
        return [p.name for p in self.list_presets(category)]

    def default_preset_name(self, category: str) -> str:
        return self._builtins[category][0].name

    def get_preset(self, category: str, name: str) -> Optional[Preset]:
        if name in self._user[category]:
            return self._user[category][name]
        for p in self._builtins[category]:
            if p.name == name:
                return p
        return None

    def is_builtin(self, category: str, name: str) -> bool:
        p = self.get_preset(category, name)
        return bool(p and p.builtin)

    def save_user_preset(self, category: str, name: str, values: Dict[str, Any]) -> Preset:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Preset name cannot be empty.")
        if any(p.name == clean_name for p in self._builtins[category]):
            raise ValueError(f"Cannot overwrite built-in preset '{clean_name}'. Choose a new name.")

        preset = Preset(category=category, name=clean_name, values=dict(values), builtin=False)
        self._user[category][clean_name] = preset

        cdir = self._category_dir(category)
        cdir.mkdir(parents=True, exist_ok=True)
        fp = cdir / f"{_slugify(clean_name)}.json"
        fp.write_text(
            json.dumps({"category": category, "name": clean_name, "values": preset.values}, indent=2),
            encoding="utf-8",
        )
        return preset

    def delete_user_preset(self, category: str, name: str) -> bool:
        if name not in self._user[category]:
            return False
        del self._user[category][name]
        cdir = self._category_dir(category)
        if cdir.exists():
            # Remove matching file by slug or stored name
            slug_fp = cdir / f"{_slugify(name)}.json"
            if slug_fp.exists():
                slug_fp.unlink()
            else:
                for fp in cdir.glob("*.json"):
                    try:
                        raw = json.loads(fp.read_text(encoding="utf-8"))
                        if raw.get("name") == name:
                            fp.unlink()
                            break
                    except Exception:
                        continue
        return True

    def is_modified(self, cfg: SlicerConfig, category: str, preset_name: str) -> bool:
        preset = self.get_preset(category, preset_name)
        if preset is None:
            return True
        current = extract_category_values(cfg, category)
        for k, cur_val in current.items():
            if k in preset.values and not _values_equal(cur_val, preset.values[k]):
                return True
        return False

    def apply_preset(self, cfg: SlicerConfig, category: str, preset_name: str) -> bool:
        preset = self.get_preset(category, preset_name)
        if preset is None:
            return False
        apply_category_values(cfg, category, preset.values)
        return True
