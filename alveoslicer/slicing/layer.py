"""
Planar Layer Data Model & Exact Mesh-Plane Triangle Slicer.

Defines `SlicedLayer` (exact vector geometries + lightweight acceleration bitmasks)
and exact floating-point mesh-plane intersection (`_slice_mesh_plane_exact`) plus
3D spherical erosion (`_erode_3d_spherical_vector`) for hollow composite wall offsets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
from shapely.geometry import Polygon

from ..core.geometry import safe_buffer, safe_intersection

@dataclass
class SlicedLayer:
    """Single planar XY layer containing both exact vector toolpath geometry and internal acceleration masks."""

    layer_idx: int                # 1-indexed
    z: float
    # Internal masks for Stage 4 (DP), Stage 5 (Basin BFS), and 2D UI inspector
    solid_mask: np.ndarray        # (nx, ny) bool: shell + inside perimeters + cell caps
    shell_mask: np.ndarray        # (nx, ny) bool: outer/inner watertight skins & solid skins
    infill_mask: np.ndarray       # (nx, ny) bool: inside perimeters & cell caps (#11)
    dry_support_mask: np.ndarray  # (nx, ny) bool: dry FFF support under hollow inner roofs
    void_mask: np.ndarray         # (nx, ny) bool: continuous photopolymer channel (#10)
    accessible_mask: np.ndarray   # (nx, ny) bool: needle-accessible photopolymer zone
    # Exact floating-point vector geometries used for G-code generation (ZERO rasterization)
    layer_height: float = 0.20
    outer_geom: shapely.Geometry = field(default_factory=Polygon)
    shell_geom: shapely.Geometry = field(default_factory=Polygon)
    cell_wall_geom: shapely.Geometry = field(default_factory=Polygon)
    inside_perimeter_lines: List[np.ndarray] = field(default_factory=list)
    cell_cap_geom: shapely.Geometry = field(default_factory=Polygon)
    empty_cell_geom: shapely.Geometry = field(default_factory=Polygon)
    resin_void_geom: shapely.Geometry = field(default_factory=Polygon)
    dry_support_lines: List[np.ndarray] = field(default_factory=list)
    skirt_brim_lines: List[np.ndarray] = field(default_factory=list)
    ext_support_lines: List[np.ndarray] = field(default_factory=list)
    ext_support_interface_lines: List[np.ndarray] = field(default_factory=list)
    wipe_tower_lines: List[np.ndarray] = field(default_factory=list)
    is_top_solid_layer: bool = False
    top_surface_geom: shapely.Geometry = field(default_factory=Polygon)
    overhang_geom: shapely.Geometry = field(default_factory=Polygon)
    bridge_geom: shapely.Geometry = field(default_factory=Polygon)


def _slice_mesh_plane_exact(
    tri_verts: np.ndarray, tri_zmin: np.ndarray, tri_zmax: np.ndarray, z_val: float
) -> shapely.Geometry:
    """Slice a triangle mesh analytically at ``z_val`` via edge-plane intersections and even-odd XOR."""
    z = z_val + 1.13e-7
    hit = (tri_zmin < z) & (tri_zmax > z)
    if not np.any(hit):
        return Polygon()

    tris = tri_verts[hit]
    above = tris[:, :, 2] > z
    n_above = above.sum(axis=1)
    valid = (n_above == 1) | (n_above == 2)
    if not np.any(valid):
        return Polygon()

    tris = tris[valid]
    above = above[valid]
    n_above = n_above[valid]

    is_lone = np.where(n_above[:, None] == 1, above, ~above)
    i0 = np.argmax(is_lone, axis=1)
    i1 = (i0 + 1) % 3
    i2 = (i0 + 2) % 3
    rows = np.arange(len(tris))

    p0 = tris[rows, i0]
    p1 = tris[rows, i1]
    p2 = tris[rows, i2]

    t1 = (z - p0[:, 2]) / (p1[:, 2] - p0[:, 2])
    t2 = (z - p0[:, 2]) / (p2[:, 2] - p0[:, 2])
    q1 = p0[:, :2] + t1[:, None] * (p1[:, :2] - p0[:, :2])
    q2 = p0[:, :2] + t2[:, None] * (p2[:, :2] - p0[:, :2])

    scale = 1e5
    adj: Dict[Tuple[int, int], List[Tuple[int, Tuple[int, int]]]] = {}
    pts_by_key: Dict[Tuple[int, int], Tuple[float, float]] = {}

    n_segs = len(q1)
    used = [False] * n_segs
    k1_arr = np.rint(q1 * scale).astype(np.int64)
    k2_arr = np.rint(q2 * scale).astype(np.int64)
    q1_list = q1.tolist()
    q2_list = q2.tolist()
    k1_list = k1_arr.tolist()
    k2_list = k2_arr.tolist()

    for s_idx in range(n_segs):
        k1 = (k1_list[s_idx][0], k1_list[s_idx][1])
        k2 = (k2_list[s_idx][0], k2_list[s_idx][1])
        if k1 == k2:
            used[s_idx] = True
            continue
        pts_by_key[k1] = (q1_list[s_idx][0], q1_list[s_idx][1])
        pts_by_key[k2] = (q2_list[s_idx][0], q2_list[s_idx][1])
        adj.setdefault(k1, []).append((s_idx, k2))
        adj.setdefault(k2, []).append((s_idx, k1))

    rings: List[Polygon] = []
    for start_key, edges in adj.items():
        for s_idx, nxt_key in edges:
            if used[s_idx]:
                continue
            used[s_idx] = True
            loop_pts = [pts_by_key[start_key], pts_by_key[nxt_key]]
            cur_key = nxt_key
            while cur_key != start_key:
                moved = False
                for e_idx, nb_key in adj.get(cur_key, ()):
                    if not used[e_idx]:
                        used[e_idx] = True
                        cur_key = nb_key
                        loop_pts.append(pts_by_key[cur_key])
                        moved = True
                        break
                if not moved:
                    break
            if len(loop_pts) >= 4:
                poly = Polygon(loop_pts)
                if not poly.is_valid:
                    poly = shapely.make_valid(poly)
                if not poly.is_empty and poly.area > 1e-4:
                    rings.append(poly)

    if not rings:
        return Polygon()

    rings.sort(key=lambda p: p.area, reverse=True)
    geom = rings[0]
    for r in rings[1:]:
        geom = geom.symmetric_difference(r)
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    return geom


def _erode_3d_spherical_vector(
    outer_polys: List[shapely.Geometry],
    k: int,
    radius_mm: float,
    h: float,
    erode_cache: Optional[Dict[Tuple[int, int], shapely.Geometry]] = None,
) -> shapely.Geometry:
    """Compute true 3D Euclidean inward shell offset at layer ``k`` purely via 2D vector algebra."""
    if radius_mm <= 1e-6:
        return outer_polys[k]
    base = outer_polys[k]
    if base.is_empty:
        return Polygon()

    n_layers = len(outer_polys)
    max_step = int(math.floor(radius_mm / h))
    if k - max_step < 0 or k + max_step >= n_layers:
        return Polygon()

    def _get_eroded(j: int, r_xy: float, quad_segs: int) -> shapely.Geometry:
        if r_xy <= 1e-4:
            return outer_polys[j]
        if erode_cache is not None:
            key = (j, int(round(r_xy * 1e4)))
            cached = erode_cache.get(key)
            if cached is not None:
                return cached
            res = safe_buffer(outer_polys[j], -r_xy, quad_segs=quad_segs)
            erode_cache[key] = res
            return res
        return safe_buffer(outer_polys[j], -r_xy, quad_segs=quad_segs)

    stride = max(1, int(round(0.5 / h)))
    offsets = {0, -max_step, max_step}
    for s in range(stride, max_step, stride):
        offsets.add(s)
        offsets.add(-s)

    acc = _get_eroded(k, radius_mm, quad_segs=12)
    if acc.is_empty:
        return Polygon()

    for dj in sorted(offsets):
        if dj == 0:
            continue
        j = k + dj
        if j < 0 or j >= n_layers or outer_polys[j].is_empty:
            return Polygon()
        dz = dj * h
        r_xy = math.sqrt(max(0.0, radius_mm * radius_mm - dz * dz))
        eroded_j = _get_eroded(j, r_xy, quad_segs=10)
        if eroded_j.is_empty:
            return Polygon()
        acc = safe_intersection(acc, eroded_j)
        if acc.is_empty:
            return Polygon()
    return acc

