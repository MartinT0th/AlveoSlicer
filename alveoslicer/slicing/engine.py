"""
Stage 2-3 Planar Slicing Orchestrator (`slice_model_to_layers`).

Combines exact mesh-plane slicing with either:
  - 3D Surface-Normal Cellular / Gyroid TPMS formwork + inverted photopolymer channel topology, OR
  - Standard PrusaSlicer density-driven sparse infill (17 patterns),
alongside external supports, dry-core supports, skirt/brim, and wipe tower synthesis.
"""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, Point, Polygon
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
)
from ..infill.lattice import NormalVoronoiLattice, clip_voronoi_cell_2d
from ..infill.standard import generate_standard_infill_layer as _generate_standard_infill_layer
from .adhesion import _generate_skirt_and_brim, _generate_wipe_tower
from .layer import SlicedLayer, _erode_3d_spherical_vector, _slice_mesh_plane_exact
from .supports import _generate_dry_core_supports, _generate_external_supports

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
