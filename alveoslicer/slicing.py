"""Stage 2 & 3: Exact analytical vector mesh slicing, 3D spherical shell offsetting, and cell clipping."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import linemerge, unary_union

from .config import SlicerConfig
from .geometry import VolumetricField, disk_kernel, extract_polylines, rasterize_geom
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
    shell_geom: shapely.Geometry = field(default_factory=Polygon)
    cell_wall_geom: shapely.Geometry = field(default_factory=Polygon)
    inside_perimeter_lines: List[np.ndarray] = field(default_factory=list)
    cell_cap_geom: shapely.Geometry = field(default_factory=Polygon)
    empty_cell_geom: shapely.Geometry = field(default_factory=Polygon)
    resin_void_geom: shapely.Geometry = field(default_factory=Polygon)
    dry_support_lines: List[np.ndarray] = field(default_factory=list)


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

    acc = base.buffer(-radius_mm, quad_segs=16)
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
        eroded_j = outer_polys[j].buffer(-r_xy, quad_segs=12) if r_xy > 1e-4 else outer_polys[j]
        if eroded_j.is_empty:
            return Polygon()
        acc = acc.intersection(eroded_j)
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
        p_dry = dry_core_geoms[k]
        if p_dry.is_empty:
            support_col = Polygon()
            continue
        ceiling_here = inner_skin_geoms[k + 1].intersection(p_dry)
        support_col = unary_union([support_col, ceiling_here]).intersection(p_dry)
        if not support_col.is_empty and support_col.area > 0.2:
            bx0, by0, bx1, by1 = support_col.bounds
            lines_k: List[np.ndarray] = []
            gx_v = math.floor(bx0 / spacing) * spacing
            while gx_v <= bx1:
                inter = support_col.intersection(LineString([(gx_v, by0 - 1.0), (gx_v, by1 + 1.0)]))
                lines_k.extend(extract_polylines(inter))
                gx_v += spacing
            gy_v = math.floor(by0 / spacing) * spacing
            while gy_v <= by1:
                inter = support_col.intersection(LineString([(bx0 - 1.0, gy_v), (bx1 + 1.0, gy_v)]))
                lines_k.extend(extract_polylines(inter))
                gy_v += spacing
            lines_all[k] = lines_k
            if lines_k:
                polys_all[k] = unary_union(
                    [LineString(ln).buffer(0.5 * lw, cap_style=2) for ln in lines_k]
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
    """Slice active 3D Voronoi/Hexagonal cells at ``z_mid`` into anchored formwork, caps, air cells, and resin voids."""
    cell_outer_list: List[shapely.Geometry] = []
    cell_air_list: List[shapely.Geometry] = []
    cell_cap_list: List[shapely.Geometry] = []
    inside_perims: List[np.ndarray] = []

    # Anchor band embeds 0.50*lw INTO both outer and inner walls for watertight bonding
    outer_guard = p_mod.buffer(-0.5 * lw, join_style=2)
    p_comp_anchor = p_comp.buffer(0.50 * lw, join_style=2).intersection(outer_guard)
    if not p_dry.is_empty:
        p_comp_anchor = p_comp_anchor.difference(p_dry.buffer(0.5 * lw, join_style=2))
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
                rounded = raw_outer.buffer(-r_eff, join_style=1).buffer(r_eff, join_style=1)
                if not rounded.is_empty:
                    raw_outer = rounded

        clipped_outer = raw_outer.intersection(p_comp)
        if clipped_outer.is_empty or clipped_outer.area < 0.12:
            continue

        clipped_outer_anchor = raw_outer.intersection(p_comp_anchor)
        if clipped_outer_anchor.is_empty:
            continue

        # Empty air interior (3D plane inset + 2D horizontal printability inset)
        raw_air = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + t_cell_wall, bbox)
        if raw_air is not None:
            eroded_2d = raw_outer.buffer(-t_cell_wall, join_style=2)
            raw_air = raw_air.intersection(eroded_2d) if not eroded_2d.is_empty else Polygon()
            clipped_air = raw_air.intersection(p_comp) if not raw_air.is_empty else Polygon()
        else:
            clipped_air = Polygon()

        if clipped_air.is_empty or clipped_air.area <= 0.06:
            # Solid flat horizontal floor/ceiling bridge cap spanning outer wall <-> inner wall
            cell_outer_list.append(clipped_outer)
            cell_cap_list.append(clipped_outer_anchor)
            continue

        # Open empty cell layer: emit anchored inside perimeters separating resin & air
        cell_lines_raw: List[np.ndarray] = []
        for p_i in range(n_inside_perims):
            d_off = (p_i + 0.5) * lw
            loop_3d = clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + d_off, bbox)
            if loop_3d is None:
                continue
            loop_2d = raw_outer.buffer(-d_off, join_style=2)
            loop_poly = loop_3d.intersection(loop_2d) if not loop_2d.is_empty else Polygon()
            if loop_poly.is_empty or loop_poly.area < 0.02:
                continue
            clipped_arcs = loop_poly.boundary.intersection(p_comp_anchor)
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

        # Fill any wider sloped wall zone between anchored inside perimeters & empty air
        if raw_air is not None and not raw_air.is_empty:
            wall_strip = clipped_outer_anchor.difference(raw_air)
            covered = unary_union(
                [LineString(ln).buffer(0.60 * lw, cap_style=2) for ln in anchored_lines]
            )
            extra_ramp = wall_strip.difference(covered)
            if not extra_ramp.is_empty and extra_ramp.area > lw * lw:
                cell_cap_list.append(extra_ramp)

    all_cells_outer = unary_union(cell_outer_list) if cell_outer_list else Polygon()
    empty_cells_geom = unary_union(cell_air_list) if cell_air_list else Polygon()
    cell_cap_geom = unary_union(cell_cap_list) if cell_cap_list else Polygon()
    return all_cells_outer, empty_cells_geom, cell_cap_geom, inside_perims


def slice_model_to_layers(
    mesh: trimesh.Trimesh,
    field: VolumetricField,
    capsules: List[Capsule],
    cfg: SlicerConfig,
) -> Tuple[List[SlicedLayer], np.ndarray, np.ndarray]:
    """
    Exact vector planar XY slicing (Stages 2 & 3):
      - Outer & inner walls: exact triangle-plane intersections & spherical vector offsets
      - Voronoi / Hexagonal empty cells: exact analytical 2D half-plane slicing with
        flat horizontal bridge caps wherever cell faces tilt < flatten_angle_deg
    """
    del field  # Retained in public signature for engine/UI contract compatibility

    h = cfg.fff.layer_height
    lw = cfg.fff.line_width
    z_max = float(mesh.bounds[1, 2])
    num_layers = max(1, int(math.floor(z_max / h)))

    rp = cfg.slice_raster_pitch_mm
    pad_xy = 2.0
    x_min = float(mesh.bounds[0, 0]) - pad_xy
    x_max = float(mesh.bounds[1, 0]) + pad_xy
    y_min = float(mesh.bounds[0, 1]) - pad_xy
    y_max = float(mesh.bounds[1, 1]) + pad_xy

    xs = np.arange(x_min, x_max + rp, rp)
    ys = np.arange(y_min, y_max + rp, rp)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")

    outer_shell = cfg.fff.perimeters * lw
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

    # 1. Exact vector mesh slicing for all layers
    tri_verts = np.asarray(mesh.triangles, dtype=np.float64)
    tri_zmin = tri_verts[:, :, 2].min(axis=1)
    tri_zmax = tri_verts[:, :, 2].max(axis=1)

    outer_polys: List[shapely.Geometry] = [
        _slice_mesh_plane_exact(tri_verts, tri_zmin, tri_zmax, (k + 0.5) * h)
        for k in range(num_layers)
    ]

    # 2. Exact 3D vector shell, composite wall, inner skin, and dry hollow core per layer
    comp_geoms: List[shapely.Geometry] = []
    shell_geoms: List[shapely.Geometry] = []
    inner_skin_geoms: List[shapely.Geometry] = []
    dry_core_geoms: List[shapely.Geometry] = []

    b_bot = cfg.fff.bottom_solid_layers
    b_top = cfg.fff.top_solid_layers

    for k in range(num_layers):
        p_mod = outer_polys[k]
        if p_mod.is_empty:
            comp_geoms.append(Polygon())
            shell_geoms.append(Polygon())
            inner_skin_geoms.append(Polygon())
            dry_core_geoms.append(Polygon())
            continue

        p_in_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell, h)
        if k < b_bot or (k + b_top) >= num_layers:
            p_in_outer = Polygon()
        else:
            p_in_outer = (
                p_in_outer.intersection(outer_polys[k - b_bot]).intersection(outer_polys[k + b_top])
            )

        if cfg.infill.mode == "hollow_shell" and not p_in_outer.is_empty:
            p_core_outer = _erode_3d_spherical_vector(outer_polys, k, outer_shell + hollow_wall, h)
            p_dry = _erode_3d_spherical_vector(
                outer_polys, k, outer_shell + hollow_wall + inner_skin, h
            )
            p_inner_skin = p_core_outer.difference(p_dry) if not p_core_outer.is_empty else Polygon()
            p_comp = p_in_outer.difference(p_core_outer) if not p_core_outer.is_empty else p_in_outer
            p_shell = p_mod.difference(p_in_outer).union(p_inner_skin)
        else:
            p_inner_skin = Polygon()
            p_dry = Polygon()
            p_comp = p_in_outer
            p_shell = p_mod.difference(p_in_outer)

        comp_geoms.append(p_comp)
        shell_geoms.append(p_shell)
        inner_skin_geoms.append(p_inner_skin)
        dry_core_geoms.append(p_dry)

    # 3. Exact vector dry-core support generation under overhanging inner ceilings
    if cfg.infill.mode == "hollow_shell":
        dry_support_lines_all, dry_support_polys_all = _generate_dry_core_supports(
            dry_core_geoms, inner_skin_geoms, lw, cfg.fff.dry_support_spacing_mm
        )
    else:
        dry_support_lines_all = [[] for _ in range(num_layers)]
        dry_support_polys_all = [Polygon() for _ in range(num_layers)]

    # 4. Exact analytical half-plane slicing of Voronoi/Hexagonal empty cells & flat bridge caps
    has_lattice = isinstance(capsules, NormalVoronoiLattice) and len(capsules.seeds) > 0
    layers: List[SlicedLayer] = []

    for k in range(num_layers):
        z_mid = (k + 0.5) * h
        p_mod = outer_polys[k]
        p_comp = comp_geoms[k]
        p_shell = shell_geoms[k]
        p_dry = dry_core_geoms[k]

        if has_lattice and not p_comp.is_empty and p_comp.area > 0.1:
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
        else:
            all_cells_outer = Polygon()
            empty_cells_geom = Polygon()
            cell_cap_geom = Polygon()
            inside_perims_k = []

        wall_geoms_preview: List[shapely.Geometry] = []
        if inside_perims_k:
            wall_geoms_preview.extend(
                LineString(ln).buffer(0.5 * lw, cap_style=2) for ln in inside_perims_k
            )
        if not cell_cap_geom.is_empty:
            wall_geoms_preview.append(cell_cap_geom)
        cell_wall_geom = unary_union(wall_geoms_preview) if wall_geoms_preview else Polygon()

        resin_void_geom = (
            p_comp.difference(all_cells_outer) if not p_comp.is_empty else Polygon()
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
                z=(k + 1) * h,
                solid_mask=solid_m,
                shell_mask=shell_m,
                infill_mask=infill_m,
                dry_support_mask=dry_m,
                void_mask=void_m,
                accessible_mask=acc_m,
                shell_geom=p_shell,
                cell_wall_geom=cell_wall_geom,
                inside_perimeter_lines=inside_perims_k,
                cell_cap_geom=cell_cap_geom,
                empty_cell_geom=empty_cells_geom,
                resin_void_geom=resin_void_geom,
                dry_support_lines=dry_support_lines_all[k],
            )
        )

    return layers, xs, ys
