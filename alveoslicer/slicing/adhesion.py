"""
Bed Adhesion & Purge Helpers: Skirt, Full-Height Draft Shield, Brim, and Wipe Tower.
"""

from __future__ import annotations

from typing import List

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box

from ..core.config import SlicerConfig
from ..core.geometry import clean_geom, iter_polygons, safe_buffer, safe_union
from ..infill.standard import hatch_polygon as _hatch_polygon

def _generate_skirt_and_brim(
    layer0_outer: shapely.Geometry,
    layer0_support_lines: List[np.ndarray],
    num_layers: int,
    cfg: SlicerConfig,
) -> List[List[np.ndarray]]:
    """Generate exact vector Brim (Layer 1) and Skirt / Draft Shield loops."""
    lines_all: List[List[np.ndarray]] = [[] for _ in range(num_layers)]
    if num_layers == 0 or layer0_outer.is_empty:
        return lines_all

    f = cfg.fff
    lw1 = max(0.2, float(f.first_layer_line_width or f.line_width))
    brim_extent = clean_geom(layer0_outer)

    # 1. Brim on Layer 1
    if f.brim_type != "no_brim" and f.brim_width_mm > 0.1:
        n_brim = max(1, int(round(f.brim_width_mm / lw1)))
        gap = max(0.0, float(f.brim_separation_gap_mm))

        if f.brim_type in ("outer_only", "outer_and_inner"):
            for i in range(n_brim):
                off = gap + (i + 0.5) * lw1
                ring = safe_buffer(layer0_outer, off, join_style=1)
                for poly in iter_polygons(ring):
                    lines_all[0].append(np.asarray(poly.exterior.coords, dtype=np.float64))
            brim_extent = safe_buffer(layer0_outer, gap + n_brim * lw1, join_style=1)

        if f.brim_type in ("inner_only", "outer_and_inner"):
            for poly in iter_polygons(layer0_outer):
                for hole in poly.interiors:
                    hole_poly = Polygon(hole)
                    for i in range(n_brim):
                        off = gap + (i + 0.5) * lw1
                        shrunk = safe_buffer(hole_poly, -off, join_style=1)
                        if shrunk.is_empty:
                            break
                        for sp in iter_polygons(shrunk):
                            lines_all[0].append(np.asarray(sp.exterior.coords, dtype=np.float64))

    # 2. Skirt & Optional Full-Height Draft Shield
    n_skirt_loops = max(int(f.skirt_loops), 1 if f.draft_shield_enable else 0)
    s_height = num_layers if f.draft_shield_enable else min(num_layers, max(0, int(f.skirt_height_layers)))
    if n_skirt_loops > 0 and s_height > 0:
        footprint_parts = [brim_extent]
        if layer0_support_lines:
            footprint_parts.extend(safe_buffer(LineString(ln), lw1) for ln in layer0_support_lines if len(ln) >= 2)
        hull = safe_union(footprint_parts).convex_hull
        s_dist = max(0.5, float(f.skirt_distance_mm))

        skirt_loops_coords: List[np.ndarray] = []
        total_len = 0.0
        i = 0
        while i < n_skirt_loops or (f.skirt_min_length_mm > 0.1 and total_len < f.skirt_min_length_mm and i < 12):
            off = s_dist + (i + 0.5) * lw1
            loop_geom = safe_buffer(hull, off, join_style=1)
            for poly in iter_polygons(loop_geom):
                coords = np.asarray(poly.exterior.coords, dtype=np.float64)
                skirt_loops_coords.append(coords)
                total_len += float(poly.exterior.length)
            i += 1

        for k in range(s_height):
            lines_all[k].extend(skirt_loops_coords)

    return lines_all


def _generate_wipe_tower(
    num_layers: int,
    layer_heights: List[float],
    cfg: SlicerConfig,
) -> List[List[np.ndarray]]:
    """Generate exact vector Purge / Wipe Tower rectangle + prime infill lines."""
    lines_all: List[List[np.ndarray]] = [[] for _ in range(num_layers)]
    f = cfg.fff
    if not f.wipe_tower_enable or num_layers == 0:
        return lines_all

    lw = max(0.2, float(f.line_width))
    x0 = float(f.wipe_tower_x)
    y0 = float(f.wipe_tower_y)
    w = max(8.0, float(f.wipe_tower_width_mm))
    d = max(5.0, float(f.wipe_tower_depth_mm))
    tower_box = box(x0, y0, x0 + w, y0 + d)
    inner_box = safe_buffer(tower_box, -0.5 * lw, join_style=2)
    fill_box = safe_buffer(tower_box, -1.2 * lw, join_style=2)

    if f.wipe_tower_brim_mm > 0.1:
        n_b = max(1, int(round(f.wipe_tower_brim_mm / lw)))
        for i in range(n_b):
            b_ring = safe_buffer(tower_box, (i + 0.5) * lw, join_style=2)
            for poly in iter_polygons(b_ring):
                lines_all[0].append(np.asarray(poly.exterior.coords, dtype=np.float64))

    for k in range(num_layers):
        for poly in iter_polygons(inner_box):
            lines_all[k].append(np.asarray(poly.exterior.coords, dtype=np.float64))

        h_k = layer_heights[k]
        area_needed = f.wipe_tower_prime_volume_mm3 / max(0.05, h_k)
        avail_area = max(1.0, fill_box.area)
        density = min(1.0, max(0.25, area_needed / avail_area))
        spacing = lw / density
        angle = 0.0 if (k % 2 == 0) else 90.0
        lines_all[k].extend(_hatch_polygon(fill_box, spacing, angle))

    return lines_all

