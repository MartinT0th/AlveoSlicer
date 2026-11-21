"""Stage 2 & 3: Exact analytical vector mesh slicing, 3D spherical shell offsetting, and cell clipping."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import linemerge
from skimage import measure

from .config import SlicerConfig
from .geometry import (
    VolumetricField,
    clean_geom,
    disk_kernel,
    extract_polylines,
    iter_polygons,
    rasterize_geom,
    safe_buffer,
    safe_difference,
    safe_intersection,
    safe_union,
)
from .infill import Capsule, NormalVoronoiLattice, clip_voronoi_cell_2d


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

    for s_idx in range(n_segs):
        x1, y1 = float(q1[s_idx, 0]), float(q1[s_idx, 1])
        x2, y2 = float(q2[s_idx, 0]), float(q2[s_idx, 1])
        k1 = (int(round(x1 * scale)), int(round(y1 * scale)))
        k2 = (int(round(x2 * scale)), int(round(y2 * scale)))
        if k1 == k2:
            used[s_idx] = True
            continue
        pts_by_key[k1] = (x1, y1)
        pts_by_key[k2] = (x2, y2)
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
    outer_polys: List[shapely.Geometry], k: int, radius_mm: float, h: float
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

    stride = max(1, int(round(0.5 / h)))
    offsets = {0, -max_step, max_step}
    for s in range(stride, max_step, stride):
        offsets.add(s)
        offsets.add(-s)

    acc = safe_buffer(base, -radius_mm, quad_segs=16)
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
        eroded_j = safe_buffer(outer_polys[j], -r_xy, quad_segs=12) if r_xy > 1e-4 else outer_polys[j]
        if eroded_j.is_empty:
            return Polygon()
        acc = clean_geom(safe_intersection(acc, eroded_j))
        if acc.is_empty:
            return Polygon()
    return acc


def _filter_anchored_inside_perimeters(
    raw_lines: List[np.ndarray],
    anchor_boundary: shapely.Geometry,
    min_open_len: float,
    anchor_tol: float = 0.08,
) -> List[np.ndarray]:
    """Keep closed loops and open wall-to-wall spans whose both endpoints anchor into the walls."""
    kept: List[np.ndarray] = []
    for coords in raw_lines:
        if len(coords) < 2:
            continue
        p_start = coords[0]
        p_end = coords[-1]
        is_closed = math.hypot(float(p_end[0] - p_start[0]), float(p_end[1] - p_start[1])) < 1e-3
        if is_closed:
            if LineString(coords).length >= min_open_len:
                kept.append(coords)
            continue

        if LineString(coords).length < min_open_len:
            continue
        d0 = anchor_boundary.distance(Point(float(p_start[0]), float(p_start[1])))
        d1 = anchor_boundary.distance(Point(float(p_end[0]), float(p_end[1])))
        if d0 <= anchor_tol and d1 <= anchor_tol:
            kept.append(coords)
    return kept


def _generate_dry_core_supports(
    dry_core_geoms: List[shapely.Geometry],
    inner_skin_geoms: List[shapely.Geometry],
    lw: float,
    spacing_mm: float,
) -> Tuple[List[List[np.ndarray]], List[shapely.Geometry]]:
    """Project sparse rectilinear dry FFF support strictly underneath overhanging hollow inner ceilings."""
    num_layers = len(dry_core_geoms)
    lines_all: List[List[np.ndarray]] = [[] for _ in range(num_layers)]
    polys_all: List[shapely.Geometry] = [Polygon() for _ in range(num_layers)]

    support_col: shapely.Geometry = Polygon()
    spacing = max(1.5, spacing_mm)
    for k in range(num_layers - 2, -1, -1):
        p_dry = clean_geom(dry_core_geoms[k])
        if p_dry.is_empty:
            support_col = Polygon()
            continue
        ceiling_here = clean_geom(safe_intersection(inner_skin_geoms[k + 1], p_dry))
        if not ceiling_here.is_empty:
            support_col = safe_union([support_col, ceiling_here]) if not support_col.is_empty else ceiling_here
        if not support_col.is_empty:
            support_col = clean_geom(safe_intersection(support_col, p_dry))
            if not support_col.is_empty:
                try:
                    support_col = clean_geom(support_col.simplify(0.04, preserve_topology=True))
                except Exception:
                    support_col = clean_geom(support_col)
        if not support_col.is_empty and support_col.area > 0.2:
            bx0, by0, bx1, by1 = support_col.bounds
            lines_k: List[np.ndarray] = []
            gx_v = math.floor(bx0 / spacing) * spacing
            while gx_v <= bx1:
                inter = safe_intersection(support_col, LineString([(gx_v, by0 - 1.0), (gx_v, by1 + 1.0)]))
                lines_k.extend(extract_polylines(inter))
                gx_v += spacing
            gy_v = math.floor(by0 / spacing) * spacing
            while gy_v <= by1:
                inter = safe_intersection(support_col, LineString([(bx0 - 1.0, gy_v), (bx1 + 1.0, gy_v)]))
                lines_k.extend(extract_polylines(inter))
                gy_v += spacing
            lines_all[k] = lines_k
            if lines_k:
                polys_all[k] = safe_union(
                    [safe_buffer(LineString(ln), 0.5 * lw, cap_style=2) for ln in lines_k]
                )
    return lines_all, polys_all


def _slice_layer_cells(
    lattice: NormalVoronoiLattice,
    z_mid: float,
    p_mod: shapely.Geometry,
    p_comp: shapely.Geometry,
    p_dry: shapely.Geometry,
    lw: float,
    w_resin: float,
    t_cell_wall: float,
    n_inside_perims: int,
    fillet_r: float,
) -> Tuple[shapely.Geometry, shapely.Geometry, shapely.Geometry, List[np.ndarray]]:
    """Slice active 3D cellular cells (or analytical Gyroid TPMS) at ``z_mid`` into formwork, caps, air, & resin."""
    if getattr(lattice, "pattern", "") == "gyroid":
        return _slice_gyroid_layer(
            pitch=getattr(lattice, "pitch", 10.0),
            z_mid=z_mid,
            p_mod=p_mod,
            p_comp=p_comp,
            p_dry=p_dry,
            lw=lw,
            w_resin=w_resin,
            t_cell_wall=t_cell_wall,
        )

    cell_outer_list: List[shapely.Geometry] = []
    cell_air_list: List[shapely.Geometry] = []
    cell_cap_list: List[shapely.Geometry] = []
    inside_perims: List[np.ndarray] = []

    # Anchor band embeds 0.50*lw INTO both outer and inner walls for watertight bonding
    outer_guard = safe_buffer(p_mod, -0.5 * lw, join_style=2)
    p_comp_anchor = clean_geom(safe_intersection(safe_buffer(p_comp, 0.50 * lw, join_style=2), outer_guard))
    if not p_dry.is_empty:
        p_comp_anchor = safe_difference(p_comp_anchor, safe_buffer(p_dry, 0.5 * lw, join_style=2))
    if p_comp_anchor.is_empty:
        p_comp_anchor = p_comp
    anchor_boundary = p_comp_anchor.boundary

    bx0, by0, bx1, by1 = p_comp_anchor.bounds
    bbox = (bx0 - 1.0, by0 - 1.0, bx1 + 1.0, by1 + 1.0)
    z_bnds = lattice.seed_z_bounds
    active_seeds = np.flatnonzero((z_bnds[:, 0] <= z_mid) & (z_bnds[:, 1] >= z_mid))

    for sid in active_seeds:
        planes = lattice.cell_planes[int(sid)]
        raw_outer = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin, bbox)
        if raw_outer is None:
            continue

        if fillet_r > 0.05:
            r_eff = min(fillet_r, 0.25 * math.sqrt(raw_outer.area))
            if r_eff > 0.05:
                rounded = safe_buffer(safe_buffer(raw_outer, -r_eff, join_style=1), r_eff, join_style=1)
                if not rounded.is_empty:
                    raw_outer = rounded

        clipped_outer = clean_geom(safe_intersection(raw_outer, p_comp))
        if clipped_outer.is_empty or clipped_outer.area < 0.12:
            continue

        clipped_outer_anchor = clean_geom(safe_intersection(raw_outer, p_comp_anchor))
        if clipped_outer_anchor.is_empty:
            continue

        # Empty air interior (3D plane inset + 2D horizontal printability inset)
        raw_air = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + t_cell_wall, bbox)
        if raw_air is not None:
            eroded_2d = safe_buffer(raw_outer, -t_cell_wall, join_style=2)
            raw_air = clean_geom(safe_intersection(raw_air, eroded_2d)) if not eroded_2d.is_empty else Polygon()
            clipped_air = clean_geom(safe_intersection(raw_air, p_comp)) if not raw_air.is_empty else Polygon()
        else:
            clipped_air = Polygon()

        if clipped_air.is_empty or clipped_air.area <= 0.06:
            cell_outer_list.append(clipped_outer)
            cell_cap_list.append(clipped_outer_anchor)
            continue

        cell_lines_raw: List[np.ndarray] = []
        for p_i in range(n_inside_perims):
            d_off = (p_i + 0.5) * lw
            loop_3d = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + d_off, bbox)
            if loop_3d is None:
                continue
            loop_2d = safe_buffer(raw_outer, -d_off, join_style=2)
            loop_poly = clean_geom(safe_intersection(loop_3d, loop_2d)) if not loop_2d.is_empty else Polygon()
            if loop_poly.is_empty or loop_poly.area < 0.02:
                continue
            clipped_arcs = safe_intersection(loop_poly.boundary, p_comp_anchor)
            if isinstance(clipped_arcs, MultiLineString):
                clipped_arcs = linemerge(clipped_arcs)
            cell_lines_raw.extend(extract_polylines(clipped_arcs))

        anchored_lines = _filter_anchored_inside_perimeters(
            cell_lines_raw, anchor_boundary, min_open_len=1.5 * lw
        )
        if not anchored_lines:
            continue

        inside_perims.extend(anchored_lines)
        cell_outer_list.append(clipped_outer)
        cell_air_list.append(clipped_air)

        if raw_air is not None and not raw_air.is_empty:
            wall_strip = safe_difference(clipped_outer_anchor, raw_air)
            covered = safe_union(
                [safe_buffer(LineString(ln), 0.60 * lw, cap_style=2) for ln in anchored_lines]
            )
            extra_ramp = safe_difference(wall_strip, covered)
            if not extra_ramp.is_empty and extra_ramp.area > lw * lw:
                cell_cap_list.append(extra_ramp)

    all_cells_outer = safe_union(cell_outer_list) if cell_outer_list else Polygon()
    empty_cells_geom = safe_union(cell_air_list) if cell_air_list else Polygon()
    cell_cap_geom = safe_union(cell_cap_list) if cell_cap_list else Polygon()
    return all_cells_outer, empty_cells_geom, cell_cap_geom, inside_perims


def _slice_gyroid_layer(
    pitch: float,
    z_mid: float,
    p_mod: shapely.Geometry,
    p_comp: shapely.Geometry,
    p_dry: shapely.Geometry,
    lw: float,
    w_resin: float,
    t_cell_wall: float,
) -> Tuple[shapely.Geometry, shapely.Geometry, shapely.Geometry, List[np.ndarray]]:
    """
    Slice the true 3D Gyroid TPMS (sin(kx)cos(ky) + sin(ky)cos(kz) + sin(kz)cos(kx))
    at ``z_mid`` into smooth sinusoidal wall toolpaths separating resin & air channels.
    """
    outer_guard = safe_buffer(p_mod, -0.5 * lw, join_style=2)
    p_comp_anchor = clean_geom(safe_intersection(safe_buffer(p_comp, 0.50 * lw, join_style=2), outer_guard))
    if not p_dry.is_empty:
        p_comp_anchor = safe_difference(p_comp_anchor, safe_buffer(p_dry, 0.5 * lw, join_style=2))
    if p_comp_anchor.is_empty:
        p_comp_anchor = p_comp

    bx0, by0, bx1, by1 = p_comp_anchor.bounds
    step = max(0.35, min(0.65, pitch / 18.0))
    xs = np.arange(bx0 - step, bx1 + 2.0 * step, step)
    ys = np.arange(by0 - step, by1 + 2.0 * step, step)
    if len(xs) < 3 or len(ys) < 3:
        return Polygon(), Polygon(), Polygon(), []

    k = 2.0 * math.pi / max(3.0, float(pitch))
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    sz, cz = math.sin(k * z_mid), math.cos(k * z_mid)
    val = np.sin(k * gx) * np.cos(k * gy) + np.sin(k * gy) * cz + sz * np.cos(k * gx)

    # Map physical wall/channel thickness into TPMS iso-level offset (|grad F| ~= 1.2 * k)
    iso_offset = min(0.75, max(0.15, 0.5 * w_resin * k * 0.75))
    inside_perims: List[np.ndarray] = []
    wall_polys: List[shapely.Geometry] = []

    for level in (-iso_offset, iso_offset):
        contours = measure.find_contours(val, level)
        for c in contours:
            if len(c) < 2:
                continue
            pts = np.column_stack([xs[0] + c[:, 0] * step, ys[0] + c[:, 1] * step])
            ls = LineString(pts).simplify(0.08, preserve_topology=False)
            clipped = safe_intersection(ls, p_comp_anchor)
            for seg in extract_polylines(clipped):
                if LineString(seg).length >= 1.5 * lw:
                    inside_perims.append(seg)
                    wall_polys.append(safe_buffer(LineString(seg), 0.5 * max(lw, t_cell_wall), cap_style=2))

    # Regions with |F| > iso_offset are enclosed air cells; |F| <= iso_offset is continuous resin channel
    cell_outer = safe_union(wall_polys) if wall_polys else Polygon()
    return cell_outer, Polygon(), Polygon(), inside_perims


def _hatch_polygon(
    geom: shapely.Geometry, spacing: float, angle_deg: float
) -> List[np.ndarray]:
    """Generate clean alternating monotonic hatch lines across a 2D Shapely polygon."""
    g = clean_geom(geom)
    if g.is_empty or g.area < 0.05:
        return []
    sp = max(0.3, float(spacing))
    rot = shapely.affinity.rotate(g, -angle_deg, origin=(0.0, 0.0))
    minx, miny, maxx, maxy = rot.bounds
    y_val = miny + 0.5 * sp
    lines: List[np.ndarray] = []
    flip = False
    while y_val <= maxy:
        inter = safe_intersection(rot, LineString([(minx - 1.0, y_val), (maxx + 1.0, y_val)]))
        for coords in extract_polylines(inter):
            if flip:
                coords = coords[::-1]
            unrot = shapely.affinity.rotate(LineString(coords), angle_deg, origin=(0.0, 0.0))
            lines.append(np.asarray(unrot.coords, dtype=np.float64))
        y_val += sp
        flip = not flip
    return lines


def _honeycomb_hatch_polygon(geom: shapely.Geometry, spacing: float) -> List[np.ndarray]:
    """Generate a PrusaSlicer-style 2D hexagonal honeycomb support grid clipped to ``geom``."""
    g = clean_geom(geom)
    if g.is_empty or g.area < 0.05:
        return []
    minx, miny, maxx, maxy = g.bounds
    s = max(1.0, float(spacing))
    dx = s * 0.5
    dy = s * math.sqrt(3.0) * 0.5
    lines: List[np.ndarray] = []
    row = 0
    y = miny - dy
    while y <= maxy + dy:
        pts: List[Tuple[float, float]] = []
        x = minx - s
        col = 0
        while x <= maxx + 2.0 * s:
            y_off = 0.0 if ((col + row) % 2 == 0) else (dy * 0.5)
            pts.append((x, y + y_off))
            pts.append((x + dx, y + y_off))
            x += 1.5 * s
            col += 1
        if len(pts) >= 2:
            inter = safe_intersection(g, LineString(pts))
            lines.extend(extract_polylines(inter))
        y += dy
        row += 1
    return lines


def _extract_painted_support_regions(
    mesh: trimesh.Trimesh,
    z_mids: List[float],
    layer_heights: List[float],
    lw: float,
) -> Tuple[List[shapely.Geometry], List[shapely.Geometry]]:
    """
    Project user paint-on support faces (``mesh.metadata["paint_support"]``: +1=enforcer, -1=blocker)
    into per-layer 2D Shapely polygons for exact integration with external supports.
    """
    n = len(z_mids)
    enforcers: List[shapely.Geometry] = [Polygon() for _ in range(n)]
    blockers: List[shapely.Geometry] = [Polygon() for _ in range(n)]

    paint = mesh.metadata.get("paint_support") if hasattr(mesh, "metadata") and isinstance(mesh.metadata, dict) else None
    if paint is None or len(paint) != len(mesh.faces):
        return enforcers, blockers

    paint_arr = np.asarray(paint, dtype=np.int8)
    if not np.any(paint_arr != 0):
        return enforcers, blockers

    tris = np.asarray(mesh.triangles, dtype=np.float64)
    z_arr = np.asarray(z_mids, dtype=np.float64)

    for label, target_list in ((1, enforcers), (-1, blockers)):
        idxs = np.flatnonzero(paint_arr == label)
        if len(idxs) == 0:
            continue
        per_layer_polys: List[List[shapely.Geometry]] = [[] for _ in range(n)]
        for fi in idxs:
            t = tris[fi]
            tz_min = float(t[:, 2].min())
            tz_max = float(t[:, 2].max())
            p2 = Polygon([(float(t[0, 0]), float(t[0, 1])), (float(t[1, 0]), float(t[1, 1])), (float(t[2, 0]), float(t[2, 1]))])
            if p2.is_empty or p2.area < 1e-4:
                p2 = safe_buffer(Point(float(t[:, 0].mean()), float(t[:, 1].mean())), max(0.4, lw))
            else:
                p2 = safe_buffer(p2, 0.25 * lw, join_style=2)
            # Assign triangle XY footprint to the layers spanning its Z range
            k0 = int(np.searchsorted(z_arr, tz_min - 0.5 * layer_heights[0], side="left"))
            k1 = int(np.searchsorted(z_arr, tz_max + 0.5 * layer_heights[-1], side="right"))
            k0 = max(1, min(n - 1, k0))
            k1 = max(k0 + 1, min(n, k1))
            for k in range(k0, k1):
                per_layer_polys[k].append(p2)
        for k in range(n):
            if per_layer_polys[k]:
                target_list[k] = safe_union(per_layer_polys[k])

    return enforcers, blockers


def _generate_organic_tree_supports(
    overhangs: List[shapely.Geometry],
    outer_simple: List[shapely.Geometry],
    layer_heights: List[float],
    z_gap_layers: int,
    iface_layers: int,
    cfg: SlicerConfig,
) -> Tuple[List[List[np.ndarray]], List[List[np.ndarray]]]:
    """
    PrusaSlicer-inspired Organic Tree Supports:
      - Clusters overhang tips into circular branches.
      - Grows branches downward while smoothly steering outward away from the model
        within ``support_branch_angle_deg`` and merging nearby branches into trunks.
      - Flares trunk bases on Layer 1 for rock-solid bed adhesion.
    """
    n = len(overhangs)
    sup_lines: List[List[np.ndarray]] = [[] for _ in range(n)]
    iface_lines: List[List[np.ndarray]] = [[] for _ in range(n)]
    f = cfg.fff
    lw_sup = max(0.2, float(f.support_line_width or f.line_width))
    tip_r = max(0.8, 0.5 * float(f.support_branch_diameter_mm))
    xy_gap = max(0.2, float(f.support_xy_distance_mm))
    max_lean_tan = math.tan(math.radians(max(10.0, min(65.0, float(f.support_branch_angle_deg)))))
    iface_spacing = max(0.35, float(f.support_interface_spacing_mm))
    base_flare = max(0.0, float(f.support_first_layer_expansion_mm))

    # Active branches: list of [cx, cy, radius]
    branches: List[List[float]] = []

    for k in range(n - 1, -1, -1):
        src_k = k + 1 + z_gap_layers
        if src_k < n and not overhangs[src_k].is_empty:
            for poly in iter_polygons(overhangs[src_k], min_area=0.15):
                # Sample branch tip attachment points across larger overhangs
                b0x, b0y, b1x, b1y = poly.bounds
                span = max(b1x - b0x, b1y - b0y)
                if span <= tip_r * 3.2:
                    rp = poly.representative_point()
                    branches.append([float(rp.x), float(rp.y), tip_r])
                else:
                    step = max(tip_r * 2.4, 3.0)
                    gx_v = b0x + 0.5 * step
                    while gx_v <= b1x:
                        gy_v = b0y + 0.5 * step
                        while gy_v <= b1y:
                            pt = Point(gx_v, gy_v)
                            if poly.contains(pt):
                                branches.append([float(gx_v), float(gy_v), tip_r])
                            gy_v += step
                        gx_v += step

        if not branches:
            continue

        # Merge closely spaced branches into stronger parent trunks
        merged: List[List[float]] = []
        used = [False] * len(branches)
        for i in range(len(branches)):
            if used[i]:
                continue
            cx, cy, r = branches[i]
            w_sum = r * r
            wx, wy = cx * w_sum, cy * w_sum
            used[i] = True
            for j in range(i + 1, len(branches)):
                if used[j]:
                    continue
                dx = branches[j][0] - cx
                dy = branches[j][1] - cy
                if math.hypot(dx, dy) <= (r + branches[j][2]) * 1.15:
                    rj = branches[j][2]
                    wj = rj * rj
                    wx += branches[j][0] * wj
                    wy += branches[j][1] * wj
                    w_sum += wj
                    used[j] = True
            new_r = min(max(tip_r * 2.8, 4.5), math.sqrt(w_sum) * 0.92 + 0.015 * layer_heights[k])
            merged.append([wx / w_sum, wy / w_sum, new_r])
        branches = merged

        # Steer branches smoothly away from the model cross-section on layer k
        excl = safe_buffer(outer_simple[k], xy_gap, join_style=1) if not outer_simple[k].is_empty else Polygon()
        max_shift = layer_heights[k] * max_lean_tan
        kept_branches: List[List[float]] = []
        branch_polys: List[shapely.Geometry] = []

        for cx, cy, r in branches:
            pt = Point(cx, cy)
            if not excl.is_empty and excl.distance(pt) < r + 0.1:
                # Push radially away from nearest model boundary
                try:
                    b_pt = shapely.ops.nearest_points(pt, excl.boundary)[1]
                    vx, vy = cx - float(b_pt.x), cy - float(b_pt.y)
                    if excl.contains(pt):
                        vx, vy = -vx, -vy
                    vl = math.hypot(vx, vy)
                    if vl > 1e-6:
                        cx += (vx / vl) * max_shift
                        cy += (vy / vl) * max_shift
                except Exception:
                    pass
            r_eff = r + (base_flare if k == 0 else (base_flare * 0.4 if k == 1 else 0.0))
            circ = Point(cx, cy).buffer(r_eff, quad_segs=14)
            circ_clean = safe_difference(circ, excl) if not excl.is_empty else circ
            if not circ_clean.is_empty and circ_clean.area > 0.15:
                branch_polys.append(circ_clean)
                kept_branches.append([cx, cy, r + 0.012 * layer_heights[k]])
            elif not f.support_buildplate_only:
                kept_branches.append([cx, cy, r])
        branches = kept_branches

        if not branch_polys:
            continue

        tree_geom = safe_union(branch_polys)
        # Emit hollow 2-perimeter organic shell rings (fast & easy to snap off!)
        for poly in iter_polygons(tree_geom, min_area=0.15):
            outer_ring = safe_buffer(poly, -0.5 * lw_sup, join_style=1)
            for p_ring in iter_polygons(outer_ring, min_area=0.05):
                sup_lines[k].append(np.asarray(p_ring.exterior.coords, dtype=np.float64))
            if k == 0:
                # Solid adhesion disk on Layer 1
                sup_lines[k].extend(_hatch_polygon(outer_ring, lw_sup * 1.1, 45.0))

        # Top interface pads right below overhang tips
        if iface_layers > 0:
            iface_acc = [
                overhangs[k + z_gap_layers + d_i]
                for d_i in range(1, iface_layers + 1)
                if (k + z_gap_layers + d_i) < n and not overhangs[k + z_gap_layers + d_i].is_empty
            ]
            if iface_acc:
                i_geom = clean_geom(safe_intersection(safe_union(iface_acc), tree_geom))
                if not i_geom.is_empty and i_geom.area > 0.15:
                    iface_lines[k].extend(_hatch_polygon(i_geom, iface_spacing, 45.0 if k % 2 == 0 else -45.0))

    return sup_lines, iface_lines


def _generate_external_supports(
    outer_polys: List[shapely.Geometry],
    layer_heights: List[float],
    cfg: SlicerConfig,
    mesh: Optional[trimesh.Trimesh] = None,
    z_mids: Optional[List[float]] = None,
) -> Tuple[List[List[np.ndarray]], List[List[np.ndarray]]]:
    """
    Generate PrusaSlicer-style external overhang support structures + dense interface layers:
      - Integrates Paint-on Support Enforcers (+1) and Blockers (-1) from ``mesh.metadata``.
      - Supports ``auto`` and ``enforcers_only`` modes.
      - Supports ``grid``, ``snug``, ``organic`` (tree), and ``pillars`` styles, plus
        ``rectilinear``, ``rectilinear_grid``, and ``honeycomb`` patterns & bottom interfaces.
    """
    n = len(outer_polys)
    sup_lines: List[List[np.ndarray]] = [[] for _ in range(n)]
    iface_lines: List[List[np.ndarray]] = [[] for _ in range(n)]
    f = cfg.fff
    if not f.support_enable or n < 2:
        return sup_lines, iface_lines

    angle_deg = max(5.0, min(85.0, float(f.support_overhang_angle_deg)))
    tan_a = math.tan(math.radians(angle_deg))
    xy_gap = max(0.1, float(f.support_xy_distance_mm))
    z_gap_layers = max(0, int(round(f.support_z_distance_mm / max(0.05, f.layer_height))))
    iface_layers = max(0, int(f.support_interface_layers))
    bot_iface_layers = max(0, int(f.support_bottom_interface_layers))
    sup_spacing = max(0.8, float(f.support_spacing_mm))
    iface_spacing = max(0.35, float(f.support_interface_spacing_mm))
    lw_sup = max(0.2, float(f.support_line_width or f.line_width))

    # 0. Extract Paint-on Support enforcer/blocker polygons if present on mesh
    if mesh is not None and z_mids is not None:
        enforcers, blockers = _extract_painted_support_regions(mesh, z_mids, layer_heights, lw_sup)
    else:
        enforcers = [Polygon() for _ in range(n)]
        blockers = [Polygon() for _ in range(n)]

    # 1. Identify overhanging areas per layer (combining auto overhangs + enforcers - blockers)
    overhangs: List[shapely.Geometry] = [Polygon() for _ in range(n)]
    has_any_overhang = False
    max_ov_layer = 0
    for k in range(1, n):
        p_cur = outer_polys[k]
        p_prev = outer_polys[k - 1]
        if p_cur.is_empty:
            continue

        ov_k: shapely.Geometry = Polygon()
        if f.support_mode != "enforcers_only":
            max_step_xy = layer_heights[k] / max(tan_a, 1e-3)
            supported_zone = safe_buffer(p_prev, max_step_xy, join_style=2) if not p_prev.is_empty else Polygon()
            ov = safe_difference(p_cur, supported_zone)
            if not ov.is_empty and ov.area > 0.25:
                ov_clean = safe_buffer(safe_buffer(ov, -0.15 * lw_sup), 0.15 * lw_sup)
                if not ov_clean.is_empty and ov_clean.area > 0.20:
                    ov_k = ov_clean

        # Merge painted Enforcers (+1) clipped to this layer's model footprint
        if not enforcers[k].is_empty:
            enf_here = clean_geom(safe_intersection(p_cur, enforcers[k]))
            if not enf_here.is_empty:
                ov_k = safe_union([ov_k, enf_here]) if not ov_k.is_empty else enf_here

        # Subtract painted Blockers (-1)
        if not ov_k.is_empty and not blockers[k].is_empty:
            ov_k = safe_difference(ov_k, blockers[k])

        if not ov_k.is_empty and ov_k.area > 0.18:
            try:
                ov_k = clean_geom(ov_k.simplify(0.05, preserve_topology=True))
            except Exception:
                ov_k = clean_geom(ov_k)
            if not ov_k.is_empty and ov_k.area > 0.18:
                overhangs[k] = ov_k
                has_any_overhang = True
                max_ov_layer = k

    if not has_any_overhang:
        return sup_lines, iface_lines

    outer_simple: List[shapely.Geometry] = []
    for p in outer_polys:
        if p.is_empty:
            outer_simple.append(Polygon())
        else:
            try:
                outer_simple.append(clean_geom(p.simplify(0.05, preserve_topology=True)))
            except Exception:
                outer_simple.append(clean_geom(p))

    # Organic tree support style
    if f.support_style == "organic":
        return _generate_organic_tree_supports(
            overhangs=overhangs,
            outer_simple=outer_simple,
            layer_heights=layer_heights,
            z_gap_layers=z_gap_layers,
            iface_layers=iface_layers,
            cfg=cfg,
        )

    # 2. Propagate support columns & top/bottom interface masks downward
    col_geoms: List[shapely.Geometry] = [Polygon() for _ in range(n)]
    iface_geoms: List[shapely.Geometry] = [Polygon() for _ in range(n)]
    active_col: shapely.Geometry = Polygon()

    below_union: List[shapely.Geometry] = [Polygon() for _ in range(n)]
    if f.support_buildplate_only:
        acc_b: shapely.Geometry = Polygon()
        for k in range(min(n, max_ov_layer + 1)):
            below_union[k] = acc_b
            if not outer_simple[k].is_empty:
                acc_b = (
                    clean_geom(safe_union([acc_b, outer_simple[k]]).simplify(0.05, preserve_topology=True))
                    if not acc_b.is_empty
                    else outer_simple[k]
                )

    for k in range(max_ov_layer - 1, -1, -1):
        src_k = k + 1 + z_gap_layers
        if src_k < n and not overhangs[src_k].is_empty:
            new_ov = overhangs[src_k]
            if f.support_style == "grid":
                new_ov = safe_buffer(new_ov, 0.5 * lw_sup, join_style=2)
            elif f.support_style == "pillars":
                new_ov = safe_buffer(new_ov, 0.25 * lw_sup, join_style=1)
            active_col = (
                clean_geom(safe_union([active_col, new_ov]).simplify(0.05, preserve_topology=True))
                if not active_col.is_empty
                else clean_geom(new_ov.simplify(0.05, preserve_topology=True))
            )

        if active_col.is_empty:
            continue

        excl = safe_buffer(outer_simple[k], xy_gap, join_style=2) if not outer_simple[k].is_empty else Polygon()
        if f.support_buildplate_only and not below_union[k].is_empty:
            excl = clean_geom(
                safe_union([excl, safe_buffer(below_union[k], xy_gap, join_style=2)]).simplify(
                    0.05, preserve_topology=True
                )
            )

        here = safe_difference(active_col, excl) if not excl.is_empty else active_col
        if here.is_empty or here.area < 0.25:
            if f.support_buildplate_only:
                active_col = here
            continue

        if k == 0 and f.support_first_layer_expansion_mm > 0.1:
            exp_g = safe_buffer(here, min(6.0, float(f.support_first_layer_expansion_mm)), join_style=1)
            here = safe_difference(exp_g, excl) if not excl.is_empty else exp_g

        here = clean_geom(here.simplify(0.05, preserve_topology=True))
        col_geoms[k] = here

        # Top interface layers directly below overhang ceiling
        iface_acc: List[shapely.Geometry] = []
        if iface_layers > 0:
            for d_i in range(1, iface_layers + 1):
                ov_idx = k + z_gap_layers + d_i
                if ov_idx < n and not overhangs[ov_idx].is_empty:
                    iface_acc.append(overhangs[ov_idx])
        # Bottom interface layers resting on model top surfaces
        if bot_iface_layers > 0 and not f.support_buildplate_only and k >= 1:
            for d_i in range(1, bot_iface_layers + 1):
                bi = k - d_i
                if bi >= 0 and not outer_simple[bi].is_empty:
                    on_model = clean_geom(safe_intersection(here, outer_simple[bi]))
                    if not on_model.is_empty and on_model.area > 0.2:
                        iface_acc.append(on_model)

        if iface_acc:
            i_geom = clean_geom(safe_intersection(safe_union(iface_acc), here))
            if not i_geom.is_empty and i_geom.area > 0.2:
                iface_geoms[k] = clean_geom(i_geom.simplify(0.05, preserve_topology=True))

    # 3. Convert support & interface polygons into exact vector polylines
    for k in range(n):
        c_geom = col_geoms[k]
        if c_geom.is_empty:
            continue
        i_geom = iface_geoms[k]
        body_geom = safe_difference(c_geom, i_geom) if not i_geom.is_empty else c_geom

        if not body_geom.is_empty and body_geom.area > 0.2:
            if f.support_style in ("grid", "pillars"):
                sheath = safe_buffer(body_geom, -0.5 * lw_sup, join_style=2)
                for poly in iter_polygons(sheath):
                    sup_lines[k].append(np.asarray(poly.exterior.coords, dtype=np.float64))
            if f.support_pattern == "honeycomb":
                sup_lines[k].extend(_honeycomb_hatch_polygon(body_geom, sup_spacing))
            else:
                angle = 0.0 if (f.support_style != "pillars" or k % 2 == 0) else 90.0
                sup_lines[k].extend(_hatch_polygon(body_geom, sup_spacing, angle))
                if f.support_pattern == "rectilinear_grid" or f.support_style == "grid":
                    sup_lines[k].extend(_hatch_polygon(body_geom, sup_spacing * 2.0, 90.0))

        if not i_geom.is_empty and i_geom.area > 0.2:
            i_angle = 45.0 if (k % 2 == 0) else -45.0
            iface_lines[k].extend(_hatch_polygon(i_geom, iface_spacing, i_angle))

    return sup_lines, iface_lines


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


def _hilbert_curve_points(order: int) -> np.ndarray:
    """Return normalized [0, 1]^2 polyline coordinates of a Hilbert curve of given order."""
    n = 1 << order
    pts = np.empty((n * n, 2), dtype=np.float64)
    for d in range(n * n):
        t = d
        x = y = 0
        s = 1
        while s < n:
            rx = 1 & (t // 2)
            ry = 1 & (t ^ rx)
            if ry == 0:
                if rx == 1:
                    x = s - 1 - x
                    y = s - 1 - y
                x, y = y, x
            x += s * rx
            y += s * ry
            t //= 4
            s *= 2
        pts[d, 0] = (x + 0.5) / n
        pts[d, 1] = (y + 0.5) / n
    return pts


def _generate_standard_infill_layer(
    region: shapely.Geometry,
    z_mid: float,
    layer_idx0: int,
    lw: float,
    cfg: SlicerConfig,
    roof_support_mask: Optional[shapely.Geometry] = None,
) -> Tuple[List[np.ndarray], shapely.Geometry]:
    """
    Generate classic PrusaSlicer density-driven 2D/3D infill polylines inside ``region``.
    Returns (infill_polylines, solid_diaphragm_geom_or_empty).
    """
    region = clean_geom(region)
    if region.is_empty or region.area < lw * lw:
        return [], Polygon()

    icfg = cfg.infill
    fcfg = cfg.fff
    density = max(0.0, min(1.0, float(icfg.fill_density_pct) / 100.0))
    if density <= 1e-4:
        return [], Polygon()

    # Combine infill every N layers (skip intermediate layers unless solid)
    comb = max(1, int(fcfg.combine_infill_every_n_layers))
    force_solid = (
        density >= 0.995
        or (icfg.solid_infill_every_layers > 0 and ((layer_idx0 + 1) % icfg.solid_infill_every_layers == 0))
        or (icfg.solid_infill_threshold_area_mm2 > 0.0 and region.area <= icfg.solid_infill_threshold_area_mm2)
    )
    if (not force_solid) and comb > 1 and (layer_idx0 % comb != 0):
        return [], Polygon()

    # Optional: Only infill where needed (clip to overhead roof/overhang projection)
    active_region = region
    if (icfg.only_infill_where_needed or icfg.standard_fill_pattern == "lightning") and not force_solid:
        if roof_support_mask is not None and not roof_support_mask.is_empty:
            active_region = clean_geom(safe_intersection(region, roof_support_mask))
            if active_region.is_empty or active_region.area < lw * lw:
                return [], Polygon()
        elif icfg.only_infill_where_needed:
            return [], Polygon()

    # Overlap region slightly into inner perimeter per infill_overlap_pct
    ov_mm = (max(0.0, min(50.0, float(fcfg.infill_overlap_pct))) / 100.0) * 0.5 * lw
    fill_reg = safe_buffer(active_region, ov_mm, join_style=2) if ov_mm > 1e-4 else active_region
    if fill_reg.is_empty:
        return [], Polygon()

    base_ang = float(fcfg.infill_angle_deg)
    if force_solid:
        ang = base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)
        return _hatch_polygon(fill_reg, lw, ang), fill_reg

    pat = str(icfg.standard_fill_pattern or "gyroid").lower()
    spacing = max(lw, lw / max(density, 0.01))
    lines: List[np.ndarray] = []

    def _shifted_hatch(geom: shapely.Geometry, sp: float, ang_deg: float, phase: float = 0.0) -> List[np.ndarray]:
        rot = clean_geom(shapely.affinity.rotate(geom, -ang_deg, origin=(0.0, 0.0)))
        if rot.is_empty:
            return []
        minx, miny, maxx, maxy = rot.bounds
        out: List[np.ndarray] = []
        y0 = math.floor((miny - phase) / sp) * sp + phase
        y_val = y0
        flip = False
        while y_val <= maxy + 1e-6:
            inter = safe_intersection(rot, LineString([(minx - 2.0, y_val), (maxx + 2.0, y_val)]))
            for c in extract_polylines(inter):
                if flip:
                    c = c[::-1]
                unrot = shapely.affinity.rotate(LineString(c), ang_deg, origin=(0.0, 0.0))
                out.append(np.asarray(unrot.coords, dtype=np.float64))
            y_val += sp
            flip = not flip
        return out

    if pat in ("rectilinear", "line"):
        ang = base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)
        lines.extend(_shifted_hatch(fill_reg, spacing, ang))
    elif pat == "aligned_rectilinear":
        lines.extend(_shifted_hatch(fill_reg, spacing, base_ang))
    elif pat == "grid":
        sp2 = max(lw * 1.2, spacing * 2.0)
        lines.extend(_shifted_hatch(fill_reg, sp2, base_ang))
        lines.extend(_shifted_hatch(fill_reg, sp2, base_ang + 90.0))
    elif pat == "triangles":
        sp3 = max(lw * 1.4, spacing * 3.0)
        for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
            lines.extend(_shifted_hatch(fill_reg, sp3, a))
    elif pat == "stars":
        sp3 = max(lw * 1.4, spacing * 3.0)
        lines.extend(_shifted_hatch(fill_reg, sp3, base_ang, phase=0.0))
        lines.extend(_shifted_hatch(fill_reg, sp3, base_ang + 60.0, phase=0.0))
        lines.extend(_shifted_hatch(fill_reg, sp3, base_ang + 120.0, phase=0.5 * sp3))
    elif pat in ("cubic", "adaptive_cubic", "support_cubic"):
        sp3 = max(lw * 1.4, spacing * 3.0)
        if pat == "adaptive_cubic":
            # Denser near shell, sparser deep inside
            inner_core = safe_buffer(fill_reg, -sp3 * 1.2, join_style=2)
            shell_band = safe_difference(fill_reg, inner_core) if not inner_core.is_empty else fill_reg
            phase = (z_mid * math.sqrt(2.0)) % sp3
            for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
                lines.extend(_shifted_hatch(shell_band, sp3, a, phase=phase))
                if not inner_core.is_empty:
                    lines.extend(_shifted_hatch(inner_core, sp3 * 2.0, a, phase=phase))
        else:
            phase = (z_mid * math.sqrt(2.0)) % sp3
            for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
                lines.extend(_shifted_hatch(fill_reg, sp3, a, phase=phase))
    elif pat in ("honeycomb", "3d_honeycomb"):
        sp_hex = max(lw * 1.5, spacing * 1.65)
        if pat == "3d_honeycomb":
            shift = 0.35 * sp_hex * math.sin(2.0 * math.pi * z_mid / max(sp_hex * 1.6, 2.0))
            shifted = shapely.affinity.translate(fill_reg, xoff=-shift, yoff=-0.5 * shift)
            raw = _honeycomb_hatch_polygon(shifted, sp_hex)
            for arr in raw:
                arr = arr.copy()
                arr[:, 0] += shift
                arr[:, 1] += 0.5 * shift
                lines.append(arr)
        else:
            lines.extend(_honeycomb_hatch_polygon(fill_reg, sp_hex))
    elif pat == "gyroid":
        from skimage import measure as sk_measure

        wavelength = max(lw * 4.0, spacing * 2.05)
        k_w = 2.0 * math.pi / wavelength
        bx0, by0, bx1, by1 = fill_reg.bounds
        step_g = max(0.35, min(0.95, wavelength / 14.0))
        gx_arr = np.arange(bx0 - step_g, bx1 + 2.0 * step_g, step_g)
        gy_arr = np.arange(by0 - step_g, by1 + 2.0 * step_g, step_g)
        if len(gx_arr) >= 3 and len(gy_arr) >= 3:
            xx, yy = np.meshgrid(gx_arr, gy_arr, indexing="ij")
            kz = k_w * z_mid
            g_val = (
                np.sin(k_w * xx) * np.cos(k_w * yy)
                + np.sin(k_w * yy) * math.cos(kz)
                + math.sin(kz) * np.cos(k_w * xx)
            )
            for contour in sk_measure.find_contours(g_val, level=0.0):
                if len(contour) < 2:
                    continue
                wx = bx0 - step_g + contour[:, 0] * step_g
                wy = by0 - step_g + contour[:, 1] * step_g
                ls = LineString(np.column_stack([wx, wy]))
                inter = safe_intersection(fill_reg, ls)
                lines.extend(extract_polylines(inter))
    elif pat == "concentric":
        cur = safe_buffer(fill_reg, -0.5 * lw, join_style=1)
        iters = 0
        while not cur.is_empty and cur.area > lw * lw and iters < 80:
            for poly in iter_polygons(cur):
                lines.append(np.asarray(poly.exterior.coords, dtype=np.float64))
                for hole in poly.interiors:
                    lines.append(np.asarray(hole.coords, dtype=np.float64))
            cur = safe_buffer(cur, -spacing, join_style=1)
            iters += 1
    elif pat == "hilbert_curve":
        bx0, by0, bx1, by1 = fill_reg.bounds
        span = max(bx1 - bx0, by1 - by0, spacing * 2.0)
        order = max(2, min(6, int(math.ceil(math.log2(max(4.0, span / max(spacing, 0.5)))))))
        h_pts = _hilbert_curve_points(order)
        h_pts[:, 0] = bx0 + h_pts[:, 0] * span
        h_pts[:, 1] = by0 + h_pts[:, 1] * span
        inter = safe_intersection(fill_reg, LineString(h_pts))
        lines.extend(extract_polylines(inter))
    elif pat == "archimedean_chords":
        bx0, by0, bx1, by1 = fill_reg.bounds
        cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
        max_r = math.hypot(bx1 - bx0, by1 - by0) * 0.55
        r = spacing
        while r <= max_r:
            n_seg = max(24, min(96, int(2.0 * math.pi * r / max(0.8, spacing * 0.4))))
            th = np.linspace(0.0, 2.0 * math.pi, n_seg + 1)
            ring = LineString(np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)]))
            inter = safe_intersection(fill_reg, ring)
            lines.extend(extract_polylines(inter))
            r += spacing
    elif pat == "octagram_spiral":
        bx0, by0, bx1, by1 = fill_reg.bounds
        cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
        max_r = math.hypot(bx1 - bx0, by1 - by0) * 0.65
        r = spacing
        while r <= max_r:
            pts = []
            for j in range(17):
                ang = j * (math.pi / 8.0) + math.radians(base_ang)
                rad = r if (j % 2 == 0) else (r * 0.58)
                pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
            inter = safe_intersection(fill_reg, LineString(pts))
            lines.extend(extract_polylines(inter))
            r += spacing * 1.25
    elif pat == "lightning":
        # Branching sparse support tree under roofs
        sp_l = max(lw * 1.6, spacing * 1.8)
        ang = base_ang + (35.0 if (layer_idx0 % 2 == 0) else -35.0)
        lines.extend(_shifted_hatch(fill_reg, sp_l, ang))
    else:
        ang = base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)
        lines.extend(_shifted_hatch(fill_reg, spacing, ang))

    return lines, Polygon()


def slice_model_to_layers(
    mesh: trimesh.Trimesh,
    field: VolumetricField,
    capsules: List[Capsule],
    cfg: SlicerConfig,
) -> Tuple[List[SlicedLayer], np.ndarray, np.ndarray]:
    """
    Exact vector planar XY slicing (Stages 2 & 3):
      - Variable first-layer height, XY size compensation, & Layer 1 elephant foot compensation
      - Outer & inner walls: exact triangle-plane intersections & spherical vector offsets
      - Choice of 3D Surface-Normal Cellular Infill OR Standard PrusaSlicer Infill (17 patterns)
      - External overhang supports + interfaces, Skirt & Brim, and Purge/Wipe Tower
    """
    del field  # Retained in public signature for engine/UI contract compatibility

    f = cfg.fff
    h = max(0.05, float(f.layer_height))
    h_first = max(0.05, float(f.first_layer_height))
    lw = max(0.15, float(f.line_width))
    lw_infill = max(0.15, float(f.infill_line_width or lw))
    z_max = float(mesh.bounds[1, 2])
    use_standard_infill = str(cfg.infill.infill_engine).lower() == "standard"

    # Build exact variable-height layer stack (Layer 1 = h_first, Layers 2..N = h)
    layer_heights: List[float] = []
    z_mids: List[float] = []
    z_tops: List[float] = []
    z_cur = 0.0
    while z_cur < z_max - 1e-4 or not layer_heights:
        hk = h_first if not layer_heights else h
        if z_cur + 0.5 * hk > z_max + 1e-4 and layer_heights:
            break
        layer_heights.append(hk)
        z_mids.append(z_cur + 0.5 * hk)
        z_cur += hk
        z_tops.append(z_cur)
    num_layers = len(layer_heights)

    rp = cfg.slice_raster_pitch_mm
    pad_xy = 2.0
    x_min = float(mesh.bounds[0, 0]) - pad_xy
    x_max = float(mesh.bounds[1, 0]) + pad_xy
    y_min = float(mesh.bounds[0, 1]) - pad_xy
    y_max = float(mesh.bounds[1, 1]) + pad_xy

    xs = np.arange(x_min, x_max + rp, rp)
    ys = np.arange(y_min, y_max + rp, rp)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")

    lw_perim = max(0.15, float(f.perimeter_line_width or lw))
    eff_perims = 1 if f.spiral_vase else max(1, f.perimeters)
    outer_shell = eff_perims * lw_perim
    hollow_wall = min(cfg.infill.hollow_wall_thickness, cfg.infill.max_bridge_span)
    inner_skin = cfg.infill.inner_skin_thickness
    w_resin = max(0.4, cfg.infill.min_channel_clearance)
    t_cell_wall = max(lw, 2.0 * cfg.infill.strut_radius)
    n_inside_perims = max(1, int(round(t_cell_wall / lw)))
    fillet_r = max(0.0, cfg.infill.smin_blend_radius)

    needle_erode_px = max(
        1,
        int(math.ceil((cfg.resin.needle_outer_radius_mm + cfg.resin.needle_safety_clear_mm) / rp)),
    )
    needle_disk = disk_kernel(needle_erode_px)

    # 1. Exact vector mesh slicing for all layers + XY Size, XY Hole & Elephant Foot compensations
    tri_verts = np.asarray(mesh.triangles, dtype=np.float64)
    tri_zmin = tri_verts[:, :, 2].min(axis=1)
    tri_zmax = tri_verts[:, :, 2].max(axis=1)

    outer_polys: List[shapely.Geometry] = []
    for k in range(num_layers):
        p = clean_geom(_slice_mesh_plane_exact(tri_verts, tri_zmin, tri_zmax, z_mids[k]))
        if not p.is_empty and abs(f.xy_size_compensation_mm) > 1e-4:
            p_comp_xy = safe_buffer(p, f.xy_size_compensation_mm, join_style=2)
            if not p_comp_xy.is_empty:
                p = p_comp_xy
        if not p.is_empty and abs(f.xy_hole_compensation_mm) > 1e-4:
            adjusted: List[shapely.Geometry] = []
            for poly in iter_polygons(p):
                if not poly.interiors:
                    adjusted.append(poly)
                else:
                    ext_p = Polygon(poly.exterior)
                    holes_adj = [
                        safe_buffer(Polygon(ring), f.xy_hole_compensation_mm, join_style=2)
                        for ring in poly.interiors
                    ]
                    holes_u = safe_union([hg for hg in holes_adj if not hg.is_empty])
                    adjusted.append(safe_difference(ext_p, holes_u) if not holes_u.is_empty else ext_p)
            p_hole = safe_union(adjusted)
            if not p_hole.is_empty:
                p = p_hole
        if k == 0 and not p.is_empty and f.elephant_foot_compensation_mm > 1e-4:
            p_ef = safe_buffer(p, -f.elephant_foot_compensation_mm, join_style=2)
            if not p_ef.is_empty and p_ef.area > 0.1:
                p = p_ef
        outer_polys.append(clean_geom(p))

    # 2. Exact 3D vector shell, composite wall, inner skin, and dry hollow core per layer
    comp_geoms: List[shapely.Geometry] = []
    shell_geoms: List[shapely.Geometry] = []
    inner_skin_geoms: List[shapely.Geometry] = []
    dry_core_geoms: List[shapely.Geometry] = []
    top_surface_geoms: List[shapely.Geometry] = []

    b_bot = max(0, int(f.bottom_solid_layers))
    if b_bot > 0 and f.min_bottom_shell_thickness_mm > 0.0:
        b_bot = max(b_bot, int(math.ceil(f.min_bottom_shell_thickness_mm / h)))

    b_top = 0 if f.spiral_vase else max(0, int(f.top_solid_layers))
    if b_top > 0 and f.min_top_shell_thickness_mm > 0.0:
        b_top = max(b_top, int(math.ceil(f.min_top_shell_thickness_mm / h)))

    for k in range(num_layers):
        p_mod = outer_polys[k]
        if p_mod.is_empty:
            comp_geoms.append(Polygon())
            shell_geoms.append(Polygon())
            inner_skin_geoms.append(Polygon())
            dry_core_geoms.append(Polygon())
            top_surface_geoms.append(Polygon())
            continue

        # Exposed top horizontal surface (used for PrusaSlicer Ironing & Lightning/Only-where-needed)
        if k == num_layers - 1:
            top_surf = p_mod
        else:
            top_surf = safe_difference(p_mod, outer_polys[k + 1])
        top_surface_geoms.append(top_surf)

        if f.spiral_vase:
            p_in_outer = Polygon() if k < b_bot else safe_buffer(p_mod, -outer_shell, join_style=2)
            p_inner_skin = Polygon()
            p_dry = Polygon()
            p_comp = Polygon()
            p_shell = safe_difference(p_mod, p_in_outer) if not p_in_outer.is_empty else p_mod
        else:
            p_in_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell, h)
            if k < b_bot or (b_top > 0 and (k + b_top) >= num_layers):
                p_in_outer = Polygon()
            else:
                if b_bot > 0:
                    p_in_outer = safe_intersection(p_in_outer, outer_polys[k - b_bot])
                if b_top > 0:
                    p_in_outer = safe_intersection(p_in_outer, outer_polys[k + b_top])

            if (not use_standard_infill) and cfg.infill.mode == "hollow_shell" and not p_in_outer.is_empty:
                p_core_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell + hollow_wall, h)
                p_dry = _erode_3d_spherical_vector(
                    outer_polys, k, outer_shell + hollow_wall + inner_skin, h
                )
                p_inner_skin = (
                    safe_difference(p_core_outer, p_dry) if not p_core_outer.is_empty else Polygon()
                )
                p_comp = (
                    safe_difference(p_in_outer, p_core_outer)
                    if not p_core_outer.is_empty
                    else p_in_outer
                )
                p_shell = safe_union([safe_difference(p_mod, p_in_outer), p_inner_skin])
            else:
                p_inner_skin = Polygon()
                p_dry = Polygon()
                p_comp = p_in_outer
                p_shell = safe_difference(p_mod, p_in_outer)

        comp_geoms.append(clean_geom(p_comp))
        shell_geoms.append(clean_geom(p_shell))
        inner_skin_geoms.append(clean_geom(p_inner_skin))
        dry_core_geoms.append(clean_geom(p_dry))

    # Optional downward roof-projection masks for Lightning / Only-infill-where-needed
    roof_proj_masks: List[shapely.Geometry] = [Polygon() for _ in range(num_layers)]
    if use_standard_infill and (
        cfg.infill.only_infill_where_needed or cfg.infill.standard_fill_pattern == "lightning"
    ):
        acc_roof: shapely.Geometry = Polygon()
        for k in range(num_layers - 1, -1, -1):
            if not top_surface_geoms[k].is_empty:
                acc_roof = safe_union([acc_roof, top_surface_geoms[k]]) if not acc_roof.is_empty else top_surface_geoms[k]
            if not acc_roof.is_empty and not comp_geoms[k].is_empty:
                # Taper lightning/support cone slightly downward
                acc_roof = clean_geom(safe_intersection(acc_roof, outer_polys[k]))
                roof_proj_masks[k] = acc_roof

    # 3. Exact vector dry-core supports, external overhang supports (with paint-on enforcers/blockers),
    #    skirt/brim/draft shield, & wipe tower
    if (not use_standard_infill) and cfg.infill.mode == "hollow_shell" and not f.spiral_vase:
        dry_support_lines_all, dry_support_polys_all = _generate_dry_core_supports(
            dry_core_geoms, inner_skin_geoms, lw, f.dry_support_spacing_mm
        )
    else:
        dry_support_lines_all = [[] for _ in range(num_layers)]
        dry_support_polys_all = [Polygon() for _ in range(num_layers)]

    ext_sup_lines_all, ext_iface_lines_all = _generate_external_supports(
        outer_polys, layer_heights, cfg, mesh=mesh, z_mids=z_mids
    )
    skirt_brim_all = _generate_skirt_and_brim(
        outer_polys[0] if num_layers > 0 else Polygon(),
        ext_sup_lines_all[0] if num_layers > 0 else [],
        num_layers,
        cfg,
    )
    wipe_tower_all = _generate_wipe_tower(num_layers, layer_heights, cfg)

    # 4. Exact analytical infill slicing (Standard PrusaSlicer Infill OR 3D Surface-Normal Cellular)
    has_lattice = (
        not use_standard_infill
        and not f.spiral_vase
        and isinstance(capsules, NormalVoronoiLattice)
        and (len(capsules.seeds) > 0 or capsules.pattern == "gyroid")
    )
    layers: List[SlicedLayer] = []

    for k in range(num_layers):
        z_mid = z_mids[k]
        p_mod = outer_polys[k]
        p_comp = comp_geoms[k]
        p_shell = shell_geoms[k]
        p_dry = dry_core_geoms[k]

        if use_standard_infill and not f.spiral_vase and not p_comp.is_empty and p_comp.area > 0.1:
            std_lines, solid_diaph = _generate_standard_infill_layer(
                p_comp,
                z_mid=z_mid,
                layer_idx0=k,
                lw=lw_infill,
                cfg=cfg,
                roof_support_mask=roof_proj_masks[k] if roof_proj_masks[k] and not roof_proj_masks[k].is_empty else None,
            )
            inside_perims_k = std_lines
            cell_cap_geom = solid_diaph
            empty_cells_geom = Polygon()
            wall_geoms_preview = [
                safe_buffer(LineString(ln), 0.5 * lw_infill, cap_style=2)
                for ln in inside_perims_k
                if len(ln) >= 2
            ]
            if not cell_cap_geom.is_empty:
                wall_geoms_preview.append(cell_cap_geom)
            cell_wall_geom = safe_union(wall_geoms_preview) if wall_geoms_preview else Polygon()
            all_cells_outer = cell_wall_geom
        elif has_lattice and not p_comp.is_empty and p_comp.area > 0.1:
            assert isinstance(capsules, NormalVoronoiLattice)
            all_cells_outer, empty_cells_geom, cell_cap_geom, inside_perims_k = _slice_layer_cells(
                lattice=capsules,
                z_mid=z_mid,
                p_mod=p_mod,
                p_comp=p_comp,
                p_dry=p_dry,
                lw=lw,
                w_resin=w_resin,
                t_cell_wall=t_cell_wall,
                n_inside_perims=n_inside_perims,
                fillet_r=fillet_r,
            )
            wall_geoms_preview = []
            if inside_perims_k:
                wall_geoms_preview.extend(
                    safe_buffer(LineString(ln), 0.5 * lw, cap_style=2)
                    for ln in inside_perims_k
                    if len(ln) >= 2
                )
            if not cell_cap_geom.is_empty:
                wall_geoms_preview.append(cell_cap_geom)
            cell_wall_geom = safe_union(wall_geoms_preview) if wall_geoms_preview else Polygon()
        else:
            all_cells_outer = Polygon()
            empty_cells_geom = Polygon()
            cell_cap_geom = Polygon()
            inside_perims_k = []
            cell_wall_geom = Polygon()

        resin_void_geom = (
            safe_difference(p_comp, all_cells_outer) if not p_comp.is_empty else Polygon()
        )

        # Internal 2D masks for Stage 4 (DP), Stage 5 (BFS), and 2D UI preview
        shell_m = rasterize_geom(p_shell, gx, gy)
        infill_m = rasterize_geom(cell_wall_geom, gx, gy) & (~shell_m)
        solid_m = shell_m | infill_m
        dry_m = rasterize_geom(dry_support_polys_all[k], gx, gy) & (~solid_m)
        void_m = rasterize_geom(resin_void_geom, gx, gy) & (~solid_m)
        acc_m = (
            ndimage.binary_erosion(void_m, structure=needle_disk)
            if np.any(void_m)
            else np.zeros_like(void_m)
        )

        layers.append(
            SlicedLayer(
                layer_idx=k + 1,
                z=z_tops[k],
                solid_mask=solid_m,
                shell_mask=shell_m,
                infill_mask=infill_m,
                dry_support_mask=dry_m,
                void_mask=void_m,
                accessible_mask=acc_m,
                layer_height=layer_heights[k],
                outer_geom=p_mod,
                shell_geom=p_shell,
                cell_wall_geom=cell_wall_geom,
                inside_perimeter_lines=inside_perims_k,
                cell_cap_geom=cell_cap_geom,
                empty_cell_geom=empty_cells_geom,
                resin_void_geom=resin_void_geom,
                dry_support_lines=dry_support_lines_all[k],
                skirt_brim_lines=skirt_brim_all[k],
                ext_support_lines=ext_sup_lines_all[k],
                ext_support_interface_lines=ext_iface_lines_all[k],
                wipe_tower_lines=wipe_tower_all[k],
                is_top_solid_layer=(k == num_layers - 1),
                top_surface_geom=top_surface_geoms[k],
            )
        )

    return layers, xs, ys
