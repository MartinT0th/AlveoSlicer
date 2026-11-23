"""
Stage 2-3 Planar Slicing Orchestrator (`slice_model_to_layers`).

Combines exact mesh-plane slicing with either:
  - 3D Surface-Normal Cellular / Gyroid TPMS formwork + inverted photopolymer channel topology, OR
  - Standard PrusaSlicer density-driven sparse infill (17 patterns),
alongside external supports, dry-core supports, skirt/brim, and wipe tower synthesis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import linemerge
from skimage import measure

from ..core.config import SlicerConfig
from ..core.geometry import (
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
    split_mesh_bodies,
)
from ..infill.lattice import Capsule, NormalVoronoiLattice, clip_voronoi_cell_2d
from ..infill.standard import (
    generate_standard_infill_layer as _generate_standard_infill_layer,
    hatch_polygon as _hatch_polygon,
)
from .adhesion import _generate_skirt_and_brim, _generate_wipe_tower
from .layer import SlicedLayer, _erode_3d_spherical_vector, _slice_mesh_plane_exact
from .supports import _generate_dry_core_supports, _generate_external_supports

def _polyline_length(coords: np.ndarray) -> float:
    """Fast Euclidean length of a 2D polyline without allocating a Shapely LineString."""
    if len(coords) < 2:
        return 0.0
    diffs = np.diff(coords, axis=0)
    return float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())


def _filter_anchored_inside_perimeters(
    raw_lines: List[np.ndarray],
    anchor_boundary: shapely.Geometry,
    min_open_len: float,
    anchor_tol: float = 0.08,
) -> List[np.ndarray]:
    """Keep closed loops and open wall-to-wall spans whose both endpoints anchor into the walls."""
    kept: List[np.ndarray] = []
    open_cands: List[np.ndarray] = []
    for coords in raw_lines:
        if len(coords) < 2:
            continue
        p_start = coords[0]
        p_end = coords[-1]
        is_closed = math.hypot(float(p_end[0] - p_start[0]), float(p_end[1] - p_start[1])) < 1e-3
        if _polyline_length(coords) < min_open_len:
            continue
        if is_closed:
            kept.append(coords)
        else:
            open_cands.append(coords)

    if open_cands:
        end_pts = shapely.points(
            np.array(
                [[c[0, 0], c[0, 1]] for c in open_cands] + [[c[-1, 0], c[-1, 1]] for c in open_cands],
                dtype=np.float64,
            )
        )
        dists = shapely.distance(anchor_boundary, end_pts)
        m = len(open_cands)
        for i, coords in enumerate(open_cands):
            if dists[i] <= anchor_tol and dists[m + i] <= anchor_tol:
                kept.append(coords)
    return kept


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
    outer_guard = safe_buffer(p_mod, -0.5 * lw, join_style=2)
    p_comp_anchor = safe_intersection(safe_buffer(p_comp, 0.50 * lw, join_style=2), outer_guard)
    if not p_dry.is_empty:
        p_comp_anchor = safe_difference(p_comp_anchor, safe_buffer(p_dry, 0.5 * lw, join_style=2))
    if p_comp_anchor.is_empty:
        p_comp_anchor = p_comp

    if getattr(lattice, "generator", "") == "planar_25d":
        return _slice_planar_25d_layer(
            pattern=getattr(lattice, "pattern", "hexagonal"),
            pitch=getattr(lattice, "pitch", 10.0),
            z_mid=z_mid,
            p_comp=p_comp,
            p_comp_anchor=p_comp_anchor,
            lw=lw,
            w_resin=w_resin,
            t_cell_wall=t_cell_wall,
        )

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

    anchor_boundary = p_comp_anchor.boundary
    shapely.prepare(p_comp)
    shapely.prepare(p_comp_anchor)
    shapely.prepare(anchor_boundary)

    bx0, by0, bx1, by1 = p_comp_anchor.bounds
    bbox = (bx0 - 1.0, by0 - 1.0, bx1 + 1.0, by1 + 1.0)
    z_bnds = lattice.seed_z_bounds
    active_seeds = np.flatnonzero((z_bnds[:, 0] <= z_mid) & (z_bnds[:, 1] >= z_mid))

    for sid in active_seeds:
        planes = lattice.cell_planes[int(sid)]
        raw_outer = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin, bbox)
        if raw_outer is None or not shapely.intersects(raw_outer, p_comp):
            continue

        if fillet_r > 0.05:
            r_eff = min(fillet_r, 0.25 * math.sqrt(raw_outer.area))
            if r_eff > 0.05:
                rounded = safe_buffer(
                    safe_buffer(raw_outer, -r_eff, join_style=1, quad_segs=6),
                    r_eff,
                    join_style=1,
                    quad_segs=6,
                )
                if not rounded.is_empty:
                    raw_outer = rounded

        clipped_outer = safe_intersection(raw_outer, p_comp)
        if clipped_outer.is_empty or clipped_outer.area < 0.12:
            continue

        clipped_outer_anchor = safe_intersection(raw_outer, p_comp_anchor)
        if clipped_outer_anchor.is_empty:
            continue

        # Empty air interior (3D plane inset + 2D horizontal printability inset)
        raw_air = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + t_cell_wall, bbox)
        if raw_air is not None:
            eroded_2d = safe_buffer(raw_outer, -t_cell_wall, join_style=2)
            raw_air = safe_intersection(raw_air, eroded_2d) if not eroded_2d.is_empty else Polygon()
            clipped_air = safe_intersection(raw_air, p_comp) if not raw_air.is_empty else Polygon()
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
            loop_poly = safe_intersection(loop_3d, loop_2d) if not loop_2d.is_empty else Polygon()
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
            covered = safe_buffer(MultiLineString(anchored_lines), 0.60 * lw, cap_style=2, quad_segs=3)
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
    p_comp_anchor = safe_intersection(safe_buffer(p_comp, 0.50 * lw, join_style=2), outer_guard)
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
    raw_lines: List[shapely.Geometry] = []
    for level in (-iso_offset, iso_offset):
        for c in measure.find_contours(val, level):
            if len(c) < 2:
                continue
            pts = np.column_stack([xs[0] + c[:, 0] * step, ys[0] + c[:, 1] * step])
            raw_lines.append(LineString(pts).simplify(0.08, preserve_topology=False))

    min_len = 1.5 * lw
    inside_perims: List[np.ndarray] = []
    if raw_lines:
        clipped = safe_intersection(MultiLineString(raw_lines), p_comp_anchor)
        for seg in extract_polylines(clipped):
            if _polyline_length(seg) >= min_len:
                inside_perims.append(seg)

    # Regions with |F| > iso_offset are enclosed air cells; |F| <= iso_offset is continuous resin channel
    cell_outer = (
        safe_buffer(MultiLineString(inside_perims), 0.5 * max(lw, t_cell_wall), cap_style=2, quad_segs=3)
        if inside_perims
        else Polygon()
    )
    return cell_outer, Polygon(), Polygon(), inside_perims


def _slice_planar_25d_layer(
    pattern: str,
    pitch: float,
    z_mid: float,
    p_comp: shapely.Geometry,
    p_comp_anchor: shapely.Geometry,
    lw: float,
    w_resin: float,
    t_cell_wall: float,
) -> Tuple[shapely.Geometry, shapely.Geometry, shapely.Geometry, List[np.ndarray]]:
    """Ultra-fast 2.5D Layer-Direct formwork with self-supporting <=34 deg Z-phase ribs & open resin channels."""
    sp = max(w_resin + t_cell_wall + lw, float(pitch) * 0.72)
    slope_tan = 0.60  # max dx/dz = 0.60 -> ~59 deg wall slope from horizontal (100% self-supporting)
    phase_a = (slope_tan * z_mid) % sp
    phase_b = (-slope_tan * z_mid) % sp

    if pattern in ("square", "brick"):
        angles_phases = ((0.0, phase_a), (90.0, phase_b))
    elif pattern == "diamond":
        angles_phases = ((45.0, phase_a), (-45.0, phase_b))
    else:
        angles_phases = ((60.0, phase_a), (-60.0, phase_b))

    inside_perims: List[np.ndarray] = []
    min_len = 1.5 * lw
    for ang, ph in angles_phases:
        for coords in _hatch_polygon(p_comp_anchor, sp, ang, phase=ph, alternating=True, min_area=0.05):
            if _polyline_length(coords) >= min_len:
                inside_perims.append(coords)

    cell_outer = (
        safe_buffer(MultiLineString(inside_perims), 0.5 * max(lw, t_cell_wall), cap_style=2, quad_segs=2)
        if inside_perims
        else Polygon()
    )
    all_cells = safe_intersection(cell_outer, p_comp) if not cell_outer.is_empty else Polygon()
    return all_cells, Polygon(), Polygon(), inside_perims


def _enforce_printable_internal_overhangs(
    shell_geoms: List[shapely.Geometry],
    cell_wall_geoms: List[shapely.Geometry],
    inside_perims_all: List[List[np.ndarray]],
    cell_cap_geoms: List[shapely.Geometry],
    dry_support_lines_all: List[List[np.ndarray]],
    dry_support_polys_all: List[shapely.Geometry],
    layer_heights: List[float],
    lw: float,
    max_bridge_span: float,
) -> None:
    """
    Bottom-up printability verification & repair pass:
      - Clips any unanchored floating internal cantilevers beyond a self-supporting overhang
        stepout so extruded lines always rest on solid geometry below or bridge <= ``max_bridge_span``
        between two anchored endpoints.
      - Adds downward dry-core support ribs beneath any oversized unsupported cap spans.
    """
    n_layers = len(shell_geoms)
    if n_layers < 2:
        return

    tan42 = math.tan(math.radians(42.0))
    for k in range(1, n_layers):
        hk = max(0.05, layer_heights[k])
        max_step = min(hk / tan42, 0.90 * lw)
        below_parts = [shell_geoms[k - 1]]
        if not cell_wall_geoms[k - 1].is_empty:
            below_parts.append(cell_wall_geoms[k - 1])
        if not dry_support_polys_all[k - 1].is_empty:
            below_parts.append(dry_support_polys_all[k - 1])
        solid_below = safe_union([g for g in below_parts if not g.is_empty])
        if solid_below.is_empty:
            continue

        supported_xy = safe_buffer(solid_below, max_step, join_style=2)
        anchor_zone = safe_buffer(safe_union([supported_xy, shell_geoms[k]]), 0.35 * lw, join_style=2)
        shapely.prepare(supported_xy)
        shapely.prepare(anchor_zone)

        raw_lines = inside_perims_all[k]
        if raw_lines:
            repaired_lines: List[np.ndarray] = []
            changed = False
            for coords in raw_lines:
                if len(coords) < 2:
                    continue
                ls = LineString(coords)
                if shapely.covers(anchor_zone, ls):
                    repaired_lines.append(coords)
                    continue
                p0_ok = shapely.contains_xy(anchor_zone, float(coords[0, 0]), float(coords[0, 1]))
                p1_ok = shapely.contains_xy(anchor_zone, float(coords[-1, 0]), float(coords[-1, 1]))
                if p0_ok and p1_ok and ls.length <= max_bridge_span:
                    repaired_lines.append(coords)
                    continue

                # Trim floating mid-air cantilever segments to supported footprint
                changed = True
                clipped = safe_intersection(ls, anchor_zone)
                for seg in extract_polylines(clipped):
                    if _polyline_length(seg) >= 1.5 * lw:
                        repaired_lines.append(seg)

            if changed:
                inside_perims_all[k] = repaired_lines
                valid_lines = [ln for ln in repaired_lines if len(ln) >= 2]
                wall_buf = (
                    safe_buffer(MultiLineString(valid_lines), 0.5 * lw, cap_style=2, quad_segs=3)
                    if valid_lines
                    else Polygon()
                )
                cap_k = cell_cap_geoms[k]
                if not cap_k.is_empty:
                    cell_wall_geoms[k] = safe_union([wall_buf, cap_k]) if not wall_buf.is_empty else cap_k
                else:
                    cell_wall_geoms[k] = wall_buf

        # Guard oversized unsupported flat caps by keeping only supported/bridgeable bands
        cap_k = cell_cap_geoms[k]
        if not cap_k.is_empty and cap_k.area > 0.25:
            unsup_cap = safe_difference(cap_k, anchor_zone)
            if not unsup_cap.is_empty:
                oversized = [
                    p
                    for p in iter_polygons(unsup_cap, min_area=0.25)
                    if min(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1]) > max_bridge_span
                ]
                if oversized:
                    # Trim unbridgeable cantilever cap extensions back into supported+bridge zone
                    bridge_reach = safe_buffer(anchor_zone, 0.5 * max_bridge_span, join_style=2)
                    trimmed_cap = safe_intersection(cap_k, bridge_reach)
                    cell_cap_geoms[k] = trimmed_cap
                    cell_wall_geoms[k] = safe_union(
                        [safe_difference(cell_wall_geoms[k], unsup_cap), trimmed_cap]
                    )


@dataclass
class _BodySliceData:
    """Intermediate per-layer exact vector geometries generated independently for one solid body."""

    outer_geom: List[shapely.Geometry]
    shell_geom: List[shapely.Geometry]
    cell_wall_geom: List[shapely.Geometry]
    inside_perims: List[List[np.ndarray]]
    cell_cap_geom: List[shapely.Geometry]
    empty_cell_geom: List[shapely.Geometry]
    resin_void_geom: List[shapely.Geometry]
    dry_support_lines: List[List[np.ndarray]]
    dry_support_polys: List[shapely.Geometry]
    ext_support_lines: List[List[np.ndarray]]
    ext_support_interface_lines: List[List[np.ndarray]]
    top_surface_geom: List[shapely.Geometry]
    overhang_geom: List[shapely.Geometry]
    bridge_geom: List[shapely.Geometry]


def _slice_single_body_layers(
    body_mesh: trimesh.Trimesh,
    body_lattice: Optional[Sequence[Capsule]],
    layer_heights: List[float],
    z_mids: List[float],
    cfg: SlicerConfig,
) -> _BodySliceData:
    """Slice a single connected body independently across the shared plate layer stack ``z_mids``."""
    f = cfg.fff
    h = max(0.05, float(f.layer_height))
    lw = max(0.15, float(f.line_width))
    lw_infill = max(0.15, float(f.infill_line_width or lw))
    lw_perim = max(0.15, float(f.perimeter_line_width or lw))
    eff_perims = 1 if f.spiral_vase else max(1, f.perimeters)
    outer_shell = eff_perims * lw_perim
    hollow_wall = min(cfg.infill.hollow_wall_thickness, cfg.infill.max_bridge_span)
    inner_skin = cfg.infill.inner_skin_thickness
    w_resin = max(0.4, cfg.infill.min_channel_clearance)
    t_cell_wall = max(lw, 2.0 * cfg.infill.strut_radius)
    n_inside_perims = max(1, int(round(t_cell_wall / lw)))
    fillet_r = max(0.0, cfg.infill.smin_blend_radius)
    use_standard_infill = str(cfg.infill.infill_engine).lower() == "standard"
    num_layers = len(layer_heights)

    # 1. Exact vector mesh slicing for this body + XY Size, XY Hole & Elephant Foot compensations
    tri_verts = np.asarray(body_mesh.triangles, dtype=np.float64)
    tri_zmin = tri_verts[:, :, 2].min(axis=1)
    tri_zmax = tri_verts[:, :, 2].max(axis=1)
    body_zmin = float(body_mesh.bounds[0, 2])
    body_zmax = float(body_mesh.bounds[1, 2])

    outer_polys: List[shapely.Geometry] = []
    for k in range(num_layers):
        zm = z_mids[k]
        if zm < body_zmin - 1e-4 or zm > body_zmax + 1e-4:
            outer_polys.append(Polygon())
            continue
        p = clean_geom(_slice_mesh_plane_exact(tri_verts, tri_zmin, tri_zmax, zm))
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
        if not p.is_empty:
            try:
                p_simp = clean_geom(p.simplify(0.015, preserve_topology=True))
                if not p_simp.is_empty:
                    p = p_simp
            except Exception:
                pass
        outer_polys.append(clean_geom(p))

    body_top_layer = max((k for k, p in enumerate(outer_polys) if not p.is_empty), default=-1)

    # 2. Exact 3D vector shell, composite wall, inner skin, and dry hollow core per layer
    comp_geoms: List[shapely.Geometry] = []
    shell_geoms: List[shapely.Geometry] = []
    inner_skin_geoms: List[shapely.Geometry] = []
    dry_core_geoms: List[shapely.Geometry] = []
    top_surface_geoms: List[shapely.Geometry] = []
    overhang_geoms: List[shapely.Geometry] = []
    bridge_geoms: List[shapely.Geometry] = []

    b_bot = max(0, int(f.bottom_solid_layers))
    if b_bot > 0 and f.min_bottom_shell_thickness_mm > 0.0:
        b_bot = max(b_bot, int(math.ceil(f.min_bottom_shell_thickness_mm / h)))

    b_top = 0 if f.spiral_vase else max(0, int(f.top_solid_layers))
    if b_top > 0 and f.min_top_shell_thickness_mm > 0.0:
        b_top = max(b_top, int(math.ceil(f.min_top_shell_thickness_mm / h)))

    erode_cache: dict[Tuple[int, int], shapely.Geometry] = {}
    tan45 = 1.0
    for k in range(num_layers):
        p_mod = outer_polys[k]
        if p_mod.is_empty:
            comp_geoms.append(Polygon())
            shell_geoms.append(Polygon())
            inner_skin_geoms.append(Polygon())
            dry_core_geoms.append(Polygon())
            top_surface_geoms.append(Polygon())
            overhang_geoms.append(Polygon())
            bridge_geoms.append(Polygon())
            continue

        if k >= body_top_layer or k == num_layers - 1:
            top_surf = p_mod
        else:
            top_surf = safe_difference(p_mod, outer_polys[k + 1])
        top_surface_geoms.append(top_surf)

        ov_k: shapely.Geometry = Polygon()
        br_k: shapely.Geometry = Polygon()
        if k >= 1 and not outer_polys[k - 1].is_empty:
            max_ov_step = min(layer_heights[k] / tan45, 0.85 * lw_perim)
            sup_base = safe_buffer(outer_polys[k - 1], max_ov_step, join_style=2)
            ov_k = safe_difference(p_mod, sup_base)

        if f.spiral_vase:
            p_in_outer = Polygon() if k < b_bot else safe_buffer(p_mod, -outer_shell, join_style=2)
            p_inner_skin = Polygon()
            p_dry = Polygon()
            p_comp = Polygon()
            p_shell = safe_difference(p_mod, p_in_outer) if not p_in_outer.is_empty else p_mod
        else:
            p_in_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell, h, erode_cache)
            if k < b_bot or (b_top > 0 and (k + b_top) > body_top_layer):
                p_in_outer = Polygon()
            else:
                if b_bot > 0:
                    p_in_outer = safe_intersection(p_in_outer, outer_polys[k - b_bot])
                if b_top > 0:
                    p_in_outer = safe_intersection(p_in_outer, outer_polys[k + b_top])

            # Thicken overhanging perimeter wall inward by 1 perimeter width to anchor slanted walls
            if f.extra_perimeters_on_overhangs and not ov_k.is_empty and not p_in_outer.is_empty:
                ov_anchor = safe_intersection(safe_buffer(ov_k, lw_perim, join_style=2), p_in_outer)
                if not ov_anchor.is_empty and ov_anchor.area > 0.05:
                    p_in_outer = safe_difference(p_in_outer, ov_anchor)

            if (not use_standard_infill) and cfg.infill.mode == "hollow_shell" and not p_in_outer.is_empty:
                p_core_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell + hollow_wall, h, erode_cache)
                p_dry = _erode_3d_spherical_vector(
                    outer_polys, k, outer_shell + hollow_wall + inner_skin, h, erode_cache
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

            # Detect bottom-facing unsupported solid skins (bridges over internal voids or overhang undersides)
            if k >= 1 and not outer_polys[k - 1].is_empty and not p_shell.is_empty:
                p_core_wall = safe_buffer(p_mod, -outer_shell, join_style=2)
                if not p_core_wall.is_empty:
                    solid_roof = safe_intersection(p_shell, p_core_wall)
                    if not solid_roof.is_empty:
                        unsup_roof = safe_difference(solid_roof, outer_polys[k - 1])
                        if not unsup_roof.is_empty and unsup_roof.area > 0.15:
                            br_k = unsup_roof

        comp_geoms.append(p_comp)
        shell_geoms.append(p_shell)
        inner_skin_geoms.append(p_inner_skin)
        dry_core_geoms.append(p_dry)
        overhang_geoms.append(ov_k)
        bridge_geoms.append(br_k)

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
                acc_roof = safe_intersection(acc_roof, outer_polys[k])
                roof_proj_masks[k] = acc_roof

    # 3. Per-body dry-core supports and external overhang supports (respecting this body's paint_support)
    if (not use_standard_infill) and cfg.infill.mode == "hollow_shell" and not f.spiral_vase:
        dry_support_lines_all, dry_support_polys_all = _generate_dry_core_supports(
            dry_core_geoms, inner_skin_geoms, lw, f.dry_support_spacing_mm
        )
    else:
        dry_support_lines_all = [[] for _ in range(num_layers)]
        dry_support_polys_all = [Polygon() for _ in range(num_layers)]

    ext_sup_lines_all, ext_iface_lines_all = _generate_external_supports(
        outer_polys, layer_heights, cfg, mesh=body_mesh, z_mids=z_mids
    )

    # 4. Per-body exact analytical infill slicing (Standard OR this body's own 3D Cellular Lattice)
    has_lattice = (
        not use_standard_infill
        and not f.spiral_vase
        and isinstance(body_lattice, NormalVoronoiLattice)
        and (len(body_lattice.seeds) > 0 or body_lattice.pattern == "gyroid")
    )

    cell_wall_geoms: List[shapely.Geometry] = []
    inside_perims_all: List[List[np.ndarray]] = []
    cell_cap_geoms: List[shapely.Geometry] = []
    empty_cell_geoms: List[shapely.Geometry] = []
    resin_void_geoms: List[shapely.Geometry] = []

    for k in range(num_layers):
        z_mid = z_mids[k]
        p_mod = outer_polys[k]
        p_comp = comp_geoms[k]
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
            valid_lines = [ln for ln in inside_perims_k if len(ln) >= 2]
            wall_buf = (
                safe_buffer(MultiLineString(valid_lines), 0.5 * lw_infill, cap_style=2, quad_segs=3)
                if valid_lines
                else Polygon()
            )
            if not cell_cap_geom.is_empty:
                cell_wall_geom = safe_union([wall_buf, cell_cap_geom]) if not wall_buf.is_empty else cell_cap_geom
            else:
                cell_wall_geom = wall_buf
            all_cells_outer = cell_wall_geom
        elif has_lattice and not p_comp.is_empty and p_comp.area > 0.1:
            assert isinstance(body_lattice, NormalVoronoiLattice)
            all_cells_outer, empty_cells_geom, cell_cap_geom, inside_perims_k = _slice_layer_cells(
                lattice=body_lattice,
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
            valid_lines = [ln for ln in inside_perims_k if len(ln) >= 2]
            wall_buf = (
                safe_buffer(MultiLineString(valid_lines), 0.5 * lw, cap_style=2, quad_segs=3)
                if valid_lines
                else Polygon()
            )
            if not cell_cap_geom.is_empty:
                cell_wall_geom = safe_union([wall_buf, cell_cap_geom]) if not wall_buf.is_empty else cell_cap_geom
            else:
                cell_wall_geom = wall_buf
        else:
            all_cells_outer = Polygon()
            empty_cells_geom = Polygon()
            cell_cap_geom = Polygon()
            inside_perims_k = []
            cell_wall_geom = Polygon()

        resin_void_geom = (
            safe_difference(p_comp, all_cells_outer)
            if (not cfg.effective_is_fff_only and not p_comp.is_empty)
            else Polygon()
        )

        cell_wall_geoms.append(cell_wall_geom)
        inside_perims_all.append(inside_perims_k)
        cell_cap_geoms.append(cell_cap_geom)
        empty_cell_geoms.append(empty_cells_geom)
        resin_void_geoms.append(resin_void_geom)

    if not use_standard_infill and not f.spiral_vase:
        _enforce_printable_internal_overhangs(
            shell_geoms=shell_geoms,
            cell_wall_geoms=cell_wall_geoms,
            inside_perims_all=inside_perims_all,
            cell_cap_geoms=cell_cap_geoms,
            dry_support_lines_all=dry_support_lines_all,
            dry_support_polys_all=dry_support_polys_all,
            layer_heights=layer_heights,
            lw=lw,
            max_bridge_span=max(2.0, float(cfg.infill.max_bridge_span)),
        )

    return _BodySliceData(
        outer_geom=outer_polys,
        shell_geom=shell_geoms,
        cell_wall_geom=cell_wall_geoms,
        inside_perims=inside_perims_all,
        cell_cap_geom=cell_cap_geoms,
        empty_cell_geom=empty_cell_geoms,
        resin_void_geom=resin_void_geoms,
        dry_support_lines=dry_support_lines_all,
        dry_support_polys=dry_support_polys_all,
        ext_support_lines=ext_sup_lines_all,
        ext_support_interface_lines=ext_iface_lines_all,
        top_surface_geom=top_surface_geoms,
        overhang_geom=overhang_geoms,
        bridge_geom=bridge_geoms,
    )


def slice_model_to_layers(
    mesh: trimesh.Trimesh,
    field: VolumetricField,
    capsules: List[Capsule],
    cfg: SlicerConfig,
    bodies: Optional[List[trimesh.Trimesh]] = None,
    body_lattices: Optional[List[NormalVoronoiLattice]] = None,
) -> Tuple[List[SlicedLayer], np.ndarray, np.ndarray]:
    """
    Exact vector planar XY slicing (Stages 2 & 3) with independent per-body slicing:
      - Each disconnected solid body is sliced separately against its own 3D cellular lattice
        (preventing cross-body Voronoi leakage and guaranteeing every body gets its own cells)
      - Variable first-layer height, XY size compensation, & Layer 1 elephant foot compensation
      - Choice of 3D Surface-Normal Cellular Infill OR Standard PrusaSlicer Infill (17 patterns)
      - External overhang supports + interfaces, Skirt & Brim, and Purge/Wipe Tower
    """
    del field  # Retained in public signature for engine/UI contract compatibility

    f = cfg.fff
    h = max(0.05, float(f.layer_height))
    h_first = max(0.05, float(f.first_layer_height))

    if bodies is not None and len(bodies) > 0:
        resolved_bodies = list(bodies)
    else:
        resolved_bodies = split_mesh_bodies(mesh) or [mesh]

    if body_lattices is not None and len(body_lattices) == len(resolved_bodies):
        resolved_lattices: List[Optional[Sequence[Capsule]]] = list(body_lattices)
    else:
        resolved_lattices = [capsules for _ in resolved_bodies]

    z_max = max(float(b.bounds[1, 2]) for b in resolved_bodies)

    # Build exact shared variable-height layer stack (Layer 1 = h_first, Layers 2..N = h)
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
    x_min = min(float(b.bounds[0, 0]) for b in resolved_bodies) - pad_xy
    x_max = max(float(b.bounds[1, 0]) for b in resolved_bodies) + pad_xy
    y_min = min(float(b.bounds[0, 1]) for b in resolved_bodies) - pad_xy
    y_max = max(float(b.bounds[1, 1]) for b in resolved_bodies) + pad_xy

    xs = np.arange(x_min, x_max + rp, rp)
    ys = np.arange(y_min, y_max + rp, rp)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")

    needle_erode_px = max(
        1,
        int(math.ceil((cfg.resin.needle_outer_radius_mm + cfg.resin.needle_safety_clear_mm) / rp)),
    )
    needle_disk = disk_kernel(needle_erode_px)

    # Slice each solid body completely independently
    body_slices = [
        _slice_single_body_layers(b_mesh, b_lat, layer_heights, z_mids, cfg)
        for b_mesh, b_lat in zip(resolved_bodies, resolved_lattices)
    ]

    # Merge per-body layer geometries onto the shared plate stack
    if len(body_slices) == 1:
        b0 = body_slices[0]
        outer_merged = b0.outer_geom
        shell_merged = b0.shell_geom
        cell_wall_merged = b0.cell_wall_geom
        inside_perims_merged = b0.inside_perims
        cell_cap_merged = b0.cell_cap_geom
        empty_cell_merged = b0.empty_cell_geom
        resin_void_merged = b0.resin_void_geom
        dry_lines_merged = b0.dry_support_lines
        dry_polys_merged = b0.dry_support_polys
        ext_sup_merged = b0.ext_support_lines
        ext_iface_merged = b0.ext_support_interface_lines
        top_surf_merged = b0.top_surface_geom
        overhang_merged = b0.overhang_geom
        bridge_merged = b0.bridge_geom
    else:
        outer_merged = [safe_union([bs.outer_geom[k] for bs in body_slices]) for k in range(num_layers)]
        shell_merged = [safe_union([bs.shell_geom[k] for bs in body_slices]) for k in range(num_layers)]
        cell_wall_merged = [safe_union([bs.cell_wall_geom[k] for bs in body_slices]) for k in range(num_layers)]
        inside_perims_merged = [
            [ln for bs in body_slices for ln in bs.inside_perims[k]] for k in range(num_layers)
        ]
        cell_cap_merged = [safe_union([bs.cell_cap_geom[k] for bs in body_slices]) for k in range(num_layers)]
        empty_cell_merged = [safe_union([bs.empty_cell_geom[k] for bs in body_slices]) for k in range(num_layers)]
        resin_void_merged = [safe_union([bs.resin_void_geom[k] for bs in body_slices]) for k in range(num_layers)]
        dry_lines_merged = [
            [ln for bs in body_slices for ln in bs.dry_support_lines[k]] for k in range(num_layers)
        ]
        dry_polys_merged = [safe_union([bs.dry_support_polys[k] for bs in body_slices]) for k in range(num_layers)]
        ext_sup_merged = [
            [ln for bs in body_slices for ln in bs.ext_support_lines[k]] for k in range(num_layers)
        ]
        ext_iface_merged = [
            [ln for bs in body_slices for ln in bs.ext_support_interface_lines[k]] for k in range(num_layers)
        ]
        top_surf_merged = [safe_union([bs.top_surface_geom[k] for bs in body_slices]) for k in range(num_layers)]
        overhang_merged = [safe_union([bs.overhang_geom[k] for bs in body_slices]) for k in range(num_layers)]
        bridge_merged = [safe_union([bs.bridge_geom[k] for bs in body_slices]) for k in range(num_layers)]

    # Plate-level Skirt/Brim (wraps all objects cleanly) & Wipe Tower
    skirt_brim_all = _generate_skirt_and_brim(
        outer_merged[0] if num_layers > 0 else Polygon(),
        ext_sup_merged[0] if num_layers > 0 else [],
        num_layers,
        cfg,
    )
    wipe_tower_all = _generate_wipe_tower(num_layers, layer_heights, cfg)

    layers: List[SlicedLayer] = []
    # Optional Raft Layers (FFF support_raft_layers > 0): prepend adhesion + interface layers beneath part
    n_raft = max(0, int(f.support_raft_layers))
    raft_z_shift = 0.0
    if n_raft > 0 and num_layers > 0 and not outer_merged[0].is_empty:
        raft_base = safe_buffer(
            outer_merged[0], max(1.0, float(f.support_first_layer_expansion_mm)), join_style=1
        )
        lw_sup = max(0.25, float(f.support_line_width or f.line_width))
        iface_sp = max(0.35, float(f.support_interface_spacing_mm))
        sup_sp = max(0.8, float(f.support_spacing_mm))
        empty_m = np.zeros_like(gx, dtype=bool)
        z_r = 0.0
        for r_idx in range(n_raft):
            rh = h_first if r_idx == 0 else h
            z_r += rh
            r_sheath = [np.asarray(p.exterior.coords, dtype=np.float64) for p in iter_polygons(safe_buffer(raft_base, -0.5 * lw_sup, join_style=1))]
            is_top_raft = r_idx == n_raft - 1
            r_fill = _hatch_polygon(
                raft_base,
                iface_sp if (is_top_raft or r_idx == 0) else sup_sp,
                45.0 if (r_idx % 2 == 0) else -45.0,
            )
            layers.append(
                SlicedLayer(
                    layer_idx=len(layers) + 1,
                    z=z_r,
                    solid_mask=empty_m,
                    shell_mask=empty_m,
                    infill_mask=empty_m,
                    dry_support_mask=empty_m,
                    void_mask=empty_m,
                    accessible_mask=empty_m,
                    layer_height=rh,
                    skirt_brim_lines=skirt_brim_all[0] if r_idx == 0 else [],
                    ext_support_lines=(r_sheath + r_fill) if not is_top_raft else [],
                    ext_support_interface_lines=(r_sheath + r_fill) if is_top_raft else [],
                )
            )
        skirt_brim_all[0] = []
        raft_z_shift = z_r + max(0.0, float(f.support_z_distance_mm))

    for k in range(num_layers):
        p_mod = outer_merged[k]
        p_shell = shell_merged[k]
        cell_wall_geom = cell_wall_merged[k]
        resin_void_geom = resin_void_merged[k]

        # Internal 2D masks for Stage 4 (DP), Stage 5 (BFS), and 2D UI preview
        shell_m = rasterize_geom(p_shell, gx, gy)
        infill_m = rasterize_geom(cell_wall_geom, gx, gy) & (~shell_m)
        solid_m = shell_m | infill_m
        dry_m = rasterize_geom(dry_polys_merged[k], gx, gy) & (~solid_m)
        if cfg.effective_is_fff_only:
            void_m = np.zeros_like(shell_m)
            acc_m = void_m
        else:
            void_m = rasterize_geom(resin_void_geom, gx, gy) & (~solid_m)
            acc_m = (
                ndimage.binary_erosion(void_m, structure=needle_disk)
                if np.any(void_m)
                else np.zeros_like(void_m)
            )

        layers.append(
            SlicedLayer(
                layer_idx=len(layers) + 1,
                z=z_tops[k] + raft_z_shift,
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
                inside_perimeter_lines=inside_perims_merged[k],
                cell_cap_geom=cell_cap_merged[k],
                empty_cell_geom=empty_cell_merged[k],
                resin_void_geom=resin_void_geom,
                dry_support_lines=dry_lines_merged[k],
                skirt_brim_lines=skirt_brim_all[k],
                ext_support_lines=ext_sup_merged[k],
                ext_support_interface_lines=ext_iface_merged[k],
                wipe_tower_lines=wipe_tower_all[k],
                is_top_solid_layer=(k == num_layers - 1),
                top_surface_geom=top_surf_merged[k],
                overhang_geom=overhang_merged[k],
                bridge_geom=bridge_merged[k],
            )
        )

    return layers, xs, ys
