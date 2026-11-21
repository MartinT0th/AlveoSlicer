"""
Support Generation Engine: External Overhang Supports (Grid/Snug/Organic/Pillars),
Paint-on Support Enforcers/Blockers, Dense Interfaces, and Dry-Core Roof Supports.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from shapely.geometry import LineString, Point, Polygon

from ..core.config import SlicerConfig
from ..core.geometry import (
    clean_geom,
    extract_polylines,
    iter_polygons,
    safe_buffer,
    safe_difference,
    safe_intersection,
    safe_union,
)
from ..infill.standard import hatch_polygon as _hatch_polygon
from ..infill.standard import honeycomb_hatch_polygon as _honeycomb_hatch_polygon

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


generate_dry_core_supports = _generate_dry_core_supports


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

