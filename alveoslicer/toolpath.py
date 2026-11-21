"""Stage 6a: Exact floating-point vector FFF toolpath generation for shells, inside perimeters, caps, and supports."""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString

from .config import SlicerConfig
from .geometry import extract_polylines, iter_polygons
from .slicing import SlicedLayer

PRUSA_TYPE_MAP = {
    "SHELL_EXT_PERIM": "External perimeter",
    "SHELL_PERIM": "Perimeter",
    "SHELL_HOLE": "Perimeter",
    "SHELL_FILL": "Solid infill",
    "INSIDE_PERIM": "Internal infill",
    "CELL_CAP_PERIM": "Solid infill",
    "CELL_CAP_HOLE": "Solid infill",
    "CELL_CAP_FILL": "Solid infill",
    "DRY_SUPPORT": "Support material",
}


def _emit_vector_region(
    region: shapely.Geometry,
    tag: str,
    num_perims: int,
    fill_angle_deg: float,
    lw: float,
    paths: List[Tuple[str, np.ndarray]],
) -> None:
    """Convert a 2D Shapely polygon region into concentric perimeter loops and monotonic solid infill lines."""
    if region is None or region.is_empty:
        return

    cur = region.buffer(-0.5 * lw, join_style=2)
    for p_i in range(num_perims):
        if cur.is_empty:
            break
        ext_tag = f"{tag}_EXT_PERIM" if (tag == "SHELL" and p_i == 0) else f"{tag}_PERIM"
        for poly in iter_polygons(cur):
            paths.append((ext_tag, np.asarray(poly.exterior.coords)))
            for interior in poly.interiors:
                paths.append((f"{tag}_HOLE", np.asarray(interior.coords)))
        if p_i + 1 < num_perims:
            cur = cur.buffer(-lw, join_style=2)
        else:
            cur = cur.buffer(-0.9 * lw, join_style=2)

    if not cur.is_empty and cur.area > lw * lw:
        rot = shapely.affinity.rotate(cur, -fill_angle_deg, origin=(0.0, 0.0))
        minx, miny, maxx, maxy = rot.bounds
        y_val = miny + 0.5 * lw
        flip = False
        while y_val <= maxy:
            seg = LineString([(minx - 1.0, y_val), (maxx + 1.0, y_val)])
            inter = rot.intersection(seg)
            for coords in extract_polylines(inter):
                if flip:
                    coords = coords[::-1]
                unrot = shapely.affinity.rotate(LineString(coords), fill_angle_deg, origin=(0.0, 0.0))
                paths.append((f"{tag}_FILL", np.asarray(unrot.coords)))
            y_val += lw
            flip = not flip


def generate_fff_paths_for_layer(
    layer: SlicedLayer, xs: np.ndarray, ys: np.ndarray, cfg: SlicerConfig
) -> List[Tuple[str, np.ndarray]]:
    """Generate exact vector FFF polylines for a single sliced layer (zero rasterization)."""
    del xs, ys  # Retained in public signature for engine/UI contract compatibility

    lw = cfg.fff.line_width
    paths: List[Tuple[str, np.ndarray]] = []
    angle = 45.0 if (layer.layer_idx % 2 == 0) else -45.0

    # 1. Exact outer & inner watertight shells + top/bottom skins
    _emit_vector_region(layer.shell_geom, "SHELL", cfg.fff.perimeters, angle, lw, paths)

    # 2. Anchored Voronoi/Hexagonal inside perimeters separating resin cavity from empty cells
    for ln in layer.inside_perimeter_lines:
        paths.append(("INSIDE_PERIM", ln))

    # 3. Solid flat horizontal floor/ceiling bridge caps & sloped ramp reinforcements
    if not layer.cell_cap_geom.is_empty:
        _emit_vector_region(layer.cell_cap_geom, "CELL_CAP", 1, angle + 90.0, lw, paths)

    # 4. Dry-core support lines (HollowShell mode only)
    for ln in layer.dry_support_lines:
        paths.append(("DRY_SUPPORT", ln))

    return paths
