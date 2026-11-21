"""Fast G-code parser producing GPU-ready layer, extrusion, travel, and resin dosing preview data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# Canonical PrusaSlicer feature types and default viewer colors (RGB float 0..1)
FEATURE_TYPES: Tuple[str, ...] = (
    "External perimeter",
    "Perimeter",
    "Internal infill",
    "Solid infill",
    "Top solid infill",
    "Ironing",
    "Skirt/Brim",
    "Support material",
    "Support material interface",
    "Wipe tower",
    "Filled resin",
    "Custom",
)

FEATURE_TYPE_TO_ID: Dict[str, int] = {name: idx for idx, name in enumerate(FEATURE_TYPES)}

# Slick AlveoSlicer yellow-accented PrusaSlicer-inspired palette
FEATURE_COLORS: Dict[str, Tuple[float, float, float]] = {
    "External perimeter": (0.96, 0.77, 0.09),          # Vivid AlveoSlicer yellow
    "Perimeter": (0.92, 0.58, 0.16),                   # Warm gold-amber
    "Internal infill": (0.93, 0.33, 0.31),             # Coral red (inside perimeters)
    "Solid infill": (0.69, 0.36, 0.85),                # Violet (solid skins & flat cell caps)
    "Top solid infill": (0.88, 0.44, 0.72),            # Magenta-rose (top finish skin)
    "Ironing": (0.74, 0.88, 0.98),                     # Crisp ice-silver (top ironing pass)
    "Skirt/Brim": (0.18, 0.72, 0.68),                  # Teal (skirt & brim)
    "Support material": (0.26, 0.72, 0.40),            # Emerald green (supports)
    "Support material interface": (0.48, 0.88, 0.42),  # Bright lime (support interface)
    "Wipe tower": (0.42, 0.56, 0.92),                  # Cobalt blue (purge / wipe tower)
    "Filled resin": (0.04, 0.76, 0.95),                # Glossy translucent cyan photopolymer
    "Custom": (0.58, 0.64, 0.72),                      # Neutral steel
}

TRAVEL_COLOR: Tuple[float, float, float] = (0.22, 0.72, 0.96)
DOSING_COLOR: Tuple[float, float, float] = (0.00, 0.90, 1.00)

_RE_CHANNEL_PT = re.compile(
    r"Channel point \d+:\s*(?:target=)?\(\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*vol=([+-]?\d+(?:\.\d+)?)"
)


@dataclass
class DosingMarker:
    """Parsed resin dosing target inside G-code."""

    layer_idx: int
    x: float
    y: float
    z: float
    volume_mm3: float


@dataclass
class GCodePreviewData:
    """Vectorized G-code geometry ready for interactive OpenGL layer/move scrubbing."""

    # Extrusion segments: shape (N, 3) float32
    ext_p0: np.ndarray
    ext_p1: np.ndarray
    ext_width: np.ndarray     # (N,) float32
    ext_height: np.ndarray    # (N,) float32
    ext_type: np.ndarray      # (N,) int32 (index into FEATURE_TYPES)
    ext_layer: np.ndarray     # (N,) int32 (1-indexed layer number)
    ext_move_idx: np.ndarray  # (N,) int32 (sequential move index within layer)

    # Travel segments: shape (M, 3) float32
    trv_p0: np.ndarray
    trv_p1: np.ndarray
    trv_layer: np.ndarray     # (M,) int32
    trv_move_idx: np.ndarray  # (M,) int32

    # Per-layer metadata (indexed 0..num_layers-1 for layers 1..num_layers)
    layer_zs: List[float] = field(default_factory=list)
    layer_move_counts: List[int] = field(default_factory=list)
    cure_layers: List[int] = field(default_factory=list)
    dosing_markers: List[DosingMarker] = field(default_factory=list)

    # Summary stats
    total_filament_mm: float = 0.0
    total_resin_ml: float = 0.0
    est_time_str: str = ""
    bounds_min: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    bounds_max: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))

    @property
    def num_layers(self) -> int:
        return len(self.layer_zs)


def parse_gcode(gcode_text: str) -> GCodePreviewData:
    """Parse PrusaSlicer / AlveoSlicer G-code into vectorized 3D preview buffers."""
    cur_x = 0.0
    cur_y = 0.0
    cur_z = 0.0
    cur_layer = 0
    cur_height = 0.25
    cur_width = 0.45
    cur_type_id = FEATURE_TYPE_TO_ID["Custom"]
    relative_e = True
    cur_e = 0.0
    active_tool = 0
    move_in_layer = 0

    ext_p0: List[Tuple[float, float, float]] = []
    ext_p1: List[Tuple[float, float, float]] = []
    ext_w: List[float] = []
    ext_h: List[float] = []
    ext_t: List[int] = []
    ext_l: List[int] = []
    ext_m: List[int] = []

    trv_p0: List[Tuple[float, float, float]] = []
    trv_p1: List[Tuple[float, float, float]] = []
    trv_l: List[int] = []
    trv_m: List[int] = []

    layer_zs: List[float] = []
    layer_move_counts: List[int] = []
    cure_layers_set = set()
    dosing_markers: List[DosingMarker] = []

    total_filament_mm = 0.0
    total_resin_ml = 0.0
    est_time_str = ""
    in_custom_block = True

    for raw_line in gcode_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith(";"):
            if line == ";LAYER_CHANGE":
                if cur_layer > 0:
                    layer_move_counts.append(move_in_layer)
                cur_layer += 1
                move_in_layer = 0
                in_custom_block = False
                layer_zs.append(cur_z)
            elif line.startswith(";Z:"):
                try:
                    cur_z = float(line[3:].strip())
                    if layer_zs:
                        layer_zs[-1] = cur_z
                except ValueError:
                    pass
            elif line.startswith(";HEIGHT:"):
                try:
                    cur_height = float(line[8:].strip())
                except ValueError:
                    pass
            elif line.startswith(";WIDTH:"):
                try:
                    cur_width = float(line[7:].strip())
                except ValueError:
                    pass
            elif line.startswith(";TYPE:"):
                tname = line[6:].strip()
                cur_type_id = FEATURE_TYPE_TO_ID.get(tname, FEATURE_TYPE_TO_ID["Internal infill"])
                in_custom_block = tname == "Custom"
            elif "PHOTOPOLYMER INJECT & UV CURE" in line or "COREXY TOOLCHANGER INJECT & UV CURE" in line:
                if cur_layer > 0:
                    cure_layers_set.add(cur_layer)
            elif line.startswith("; Channel point"):
                m = _RE_CHANNEL_PT.search(line)
                if m and cur_layer > 0:
                    dosing_markers.append(
                        DosingMarker(
                            layer_idx=cur_layer,
                            x=float(m.group(1)),
                            y=float(m.group(2)),
                            z=cur_z,
                            volume_mm3=float(m.group(3)),
                        )
                    )
            elif line.startswith("; photopolymer used [mL] ="):
                try:
                    total_resin_ml = float(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("; estimated printing time (normal mode) ="):
                est_time_str = line.split("=", 1)[1].strip()
            continue

        # Strip inline comment
        cmd_part = line.split(";", 1)[0].strip()
        if not cmd_part:
            continue

        tokens = cmd_part.split()
        head = tokens[0].upper()

        if head == "M83":
            relative_e = True
            continue
        if head == "M82":
            relative_e = False
            continue
        if head == "G92":
            for tok in tokens[1:]:
                if tok.upper().startswith("E"):
                    try:
                        cur_e = float(tok[1:])
                    except ValueError:
                        pass
            continue
        if head.startswith("T") and len(head) >= 2 and head[1:].isdigit():
            active_tool = int(head[1:])
            continue

        if head not in ("G0", "G1"):
            continue

        nx, ny, nz = cur_x, cur_y, cur_z
        de = 0.0
        has_xy = False
        for tok in tokens[1:]:
            axis = tok[0].upper()
            try:
                val = float(tok[1:])
            except ValueError:
                continue
            if axis == "X":
                nx = val
                has_xy = True
            elif axis == "Y":
                ny = val
                has_xy = True
            elif axis == "Z":
                nz = val
            elif axis == "E":
                if relative_e:
                    de = val
                    cur_e += val
                else:
                    de = val - cur_e
                    cur_e = val

        if cur_layer > 0 and active_tool == 0 and not in_custom_block:
            if has_xy and de > 1e-6:
                move_in_layer += 1
                ext_p0.append((cur_x, cur_y, cur_z))
                ext_p1.append((nx, ny, nz))
                ext_w.append(cur_width)
                ext_h.append(cur_height)
                ext_t.append(cur_type_id)
                ext_l.append(cur_layer)
                ext_m.append(move_in_layer)
                total_filament_mm += de
            elif has_xy and (abs(nx - cur_x) > 1e-4 or abs(ny - cur_y) > 1e-4):
                move_in_layer += 1
                trv_p0.append((cur_x, cur_y, cur_z))
                trv_p1.append((nx, ny, nz))
                trv_l.append(cur_layer)
                trv_m.append(move_in_layer)

        cur_x, cur_y, cur_z = nx, ny, nz

    if cur_layer > 0:
        layer_move_counts.append(move_in_layer)

    def _arr3(items: List[Tuple[float, float, float]]) -> np.ndarray:
        if not items:
            return np.zeros((0, 3), dtype=np.float32)
        return np.asarray(items, dtype=np.float32)

    p0_arr = _arr3(ext_p0)
    p1_arr = _arr3(ext_p1)
    tp0_arr = _arr3(trv_p0)
    tp1_arr = _arr3(trv_p1)

    if len(p0_arr) > 0:
        bmin = np.minimum(p0_arr.min(axis=0), p1_arr.min(axis=0))
        bmax = np.maximum(p0_arr.max(axis=0), p1_arr.max(axis=0))
    else:
        bmin = np.zeros(3, dtype=np.float32)
        bmax = np.array([220.0, 220.0, 20.0], dtype=np.float32)

    return GCodePreviewData(
        ext_p0=p0_arr,
        ext_p1=p1_arr,
        ext_width=np.asarray(ext_w, dtype=np.float32),
        ext_height=np.asarray(ext_h, dtype=np.float32),
        ext_type=np.asarray(ext_t, dtype=np.int32),
        ext_layer=np.asarray(ext_l, dtype=np.int32),
        ext_move_idx=np.asarray(ext_m, dtype=np.int32),
        trv_p0=tp0_arr,
        trv_p1=tp1_arr,
        trv_layer=np.asarray(trv_l, dtype=np.int32),
        trv_move_idx=np.asarray(trv_m, dtype=np.int32),
        layer_zs=layer_zs,
        layer_move_counts=layer_move_counts,
        cure_layers=sorted(cure_layers_set),
        dosing_markers=dosing_markers,
        total_filament_mm=total_filament_mm,
        total_resin_ml=total_resin_ml,
        est_time_str=est_time_str,
        bounds_min=bmin,
        bounds_max=bmax,
    )
