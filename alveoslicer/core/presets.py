"""
Per-Category Preset Manager (Built-In + Persistent JSON User Presets).

Handles extracting/applying flat category dictionaries onto `SlicerConfig`,
detecting unsaved modifications, and saving/deleting user presets on disk.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SlicerConfig
from .presets_data import (
    BUILTIN_PRESET_SPECS,
    CATEGORY_FIELDS,
    CATEGORY_LABELS,
    PRESET_CATEGORIES,
)


def field_key(section: Optional[str], name: str) -> str:
    """Format a canonical dotted configuration key (e.g. ``'fff.layer_height'``)."""
    return f"{section}.{name}" if section else name


def extract_category_values(cfg: SlicerConfig, category: str) -> Dict[str, Any]:
    """Extract every setting belonging to ``category`` as a flat ``key -> value`` dictionary."""
    return {
        field_key(sec, attr): getattr(getattr(cfg, sec) if sec else cfg, attr)
        for sec, attr in CATEGORY_FIELDS[category]
    }


def apply_category_values(cfg: SlicerConfig, category: str, values: Dict[str, Any]) -> None:
    """Apply a flat ``key -> value`` dictionary onto ``cfg`` for all keys in ``category``."""
    for sec, attr in CATEGORY_FIELDS[category]:
        k = field_key(sec, attr)
        if k in values:
            setattr(getattr(cfg, sec) if sec else cfg, attr, values[k])


def _values_equal(a: Any, b: Any) -> bool:
    """Compare two config values with float tolerance and strict boolean semantics."""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6
    return a == b


def _slugify(name: str) -> str:
    """Convert a preset display name into a filesystem-safe stem."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip()).strip("_") or "preset"


@dataclass
class Preset:
    """Single named preset storing flat field overrides for one settings category."""

    category: str
    name: str
    values: Dict[str, Any]
    builtin: bool = False


def _build_default_presets() -> Dict[str, List[Preset]]:
    """Materialize all declarative built-in presets against default `SlicerConfig` values."""
    default_cfg = SlicerConfig()
    out: Dict[str, List[Preset]] = {}
    for cat, specs in BUILTIN_PRESET_SPECS.items():
        base = extract_category_values(default_cfg, cat)
        out[cat] = [
            Preset(category=cat, name=name, values={**base, **overrides}, builtin=True)
            for name, overrides in specs
        ]
    return out


class PresetManager:
    """Manages factory built-in and persistent JSON user presets across all 5 settings categories."""

    def __init__(self, user_dir: Optional[Path] = None) -> None:
        """Initialize the preset store and load any saved user JSON files from ``user_dir``."""
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
                        self._user[cat][name] = Preset(category=cat, name=name, values=values, builtin=False)
                except Exception:
                    continue

    def list_presets(self, category: str) -> List[Preset]:
        """Return built-in presets followed by alphabetically sorted user presets."""
        return list(self._builtins[category]) + sorted(self._user[category].values(), key=lambda p: p.name.lower())

    def preset_names(self, category: str) -> List[str]:
        """Return display names of all presets available in ``category``."""
        return [p.name for p in self.list_presets(category)]

    def default_preset_name(self, category: str) -> str:
        """Return the name of the primary factory preset for ``category``."""
        return self._builtins[category][0].name

    def get_preset(self, category: str, name: str) -> Optional[Preset]:
        """Look up a preset by exact name (user presets take precedence over built-ins)."""
        if name in self._user[category]:
            return self._user[category][name]
        return next((p for p in self._builtins[category] if p.name == name), None)

    def is_builtin(self, category: str, name: str) -> bool:
        """Return True if ``name`` refers to an immutable factory built-in preset."""
        p = self.get_preset(category, name)
        return bool(p and p.builtin)

    def save_user_preset(self, category: str, name: str, values: Dict[str, Any]) -> Preset:
        """Persist a custom user preset to disk under ``user_dir/<category>/<slug>.json``."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Preset name cannot be empty.")
        if any(p.name == clean_name for p in self._builtins[category]):
            raise ValueError(f"Cannot overwrite built-in preset '{clean_name}'. Choose a new name.")

        preset = Preset(category=category, name=clean_name, values=dict(values), builtin=False)
        self._user[category][clean_name] = preset
        cdir = self._category_dir(category)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{_slugify(clean_name)}.json").write_text(
            json.dumps({"category": category, "name": clean_name, "values": preset.values}, indent=2),
            encoding="utf-8",
        )
        return preset

    def delete_user_preset(self, category: str, name: str) -> bool:
        """Delete a user preset from memory and disk; returns False for built-in or missing names."""
        if name not in self._user[category]:
            return False
        del self._user[category][name]
        cdir = self._category_dir(category)
        if cdir.exists():
            slug_fp = cdir / f"{_slugify(name)}.json"
            if slug_fp.exists():
                slug_fp.unlink()
            else:
                for fp in cdir.glob("*.json"):
                    try:
                        if json.loads(fp.read_text(encoding="utf-8")).get("name") == name:
                            fp.unlink()
                            break
                    except Exception:
                        continue
        return True

    def is_modified(self, cfg: SlicerConfig, category: str, preset_name: str) -> bool:
        """Return True if any field in ``cfg`` diverges from ``preset_name``."""
        preset = self.get_preset(category, preset_name)
        if preset is None:
            return True
        current = extract_category_values(cfg, category)
        return any(
            k in preset.values and not _values_equal(cur_val, preset.values[k])
            for k, cur_val in current.items()
        )

    def apply_preset(self, cfg: SlicerConfig, category: str, preset_name: str) -> bool:
        """Load ``preset_name`` into ``cfg``; returns False if the preset does not exist."""
        preset = self.get_preset(category, preset_name)
        if preset is None:
            return False
        apply_category_values(cfg, category, preset.values)
        return True


__all__ = [
    "PRESET_CATEGORIES",
    "CATEGORY_LABELS",
    "CATEGORY_FIELDS",
    "field_key",
    "extract_category_values",
    "apply_category_values",
    "Preset",
    "PresetManager",
]
