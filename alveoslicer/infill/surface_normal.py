"""
Stage 2-3: 3D Surface-Normal Cellular & TPMS Formwork Synthesis.

Synthesizes 3D surface-conformal cellular lattices along a model's exterior boundary
(Hexagonal, Voronoi, Square, Diamond, Triangular, Brick, Gyroid TPMS, and 3D Cubic)
with shallow-face flattening for support-free printing inside hybrid photopolymer shells.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from skimage import measure

from ..core.config import SlicerConfig
from ..core.geometry import VolumetricField
from .lattice import Capsule, CellHalfPlane, NormalVoronoiLattice

# -----------------------------------------------------------------------------
# Surface Sampling & Regularization Helpers
# -----------------------------------------------------------------------------


def _poisson_disk_sample(points: np.ndarray, min_radius: float, seed: int) -> np.ndarray:
    """Greedy Poisson-disk subsampling of a 3D point cloud with deterministic RNG seed."""
    if len(points) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    rng = np.random.default_rng(seed)
    min_r_sq = min_radius * min_radius
    chosen: List[np.ndarray] = []
    for idx in rng.permutation(len(points)):
        p = points[idx]
        if not chosen:
            chosen.append(p)
            continue
        diffs = np.asarray(chosen) - p
        if np.min(np.einsum("ij,ij->i", diffs, diffs)) >= min_r_sq:
            chosen.append(p)
    return np.asarray(chosen, dtype=np.float64)


def _fast_lloyd_cvt(surf_pts: np.ndarray, seeds: np.ndarray, iters: int) -> np.ndarray:
    """Vectorized surface Lloyd's CVT relaxation using np.bincount."""
    pt_tree = cKDTree(surf_pts)
    n_seeds = len(seeds)
    cur = seeds.copy()
    for _ in range(iters):
        stree = cKDTree(cur)
        _, labels = stree.query(surf_pts, k=1)
        counts = np.bincount(labels, minlength=n_seeds)
        nonzero = counts > 0
        if not np.any(nonzero):
            break
        sx = np.bincount(labels, weights=surf_pts[:, 0], minlength=n_seeds)
        sy = np.bincount(labels, weights=surf_pts[:, 1], minlength=n_seeds)
        sz = np.bincount(labels, weights=surf_pts[:, 2], minlength=n_seeds)
        centroids = cur.copy()
        centroids[nonzero, 0] = sx[nonzero] / counts[nonzero]
        centroids[nonzero, 1] = sy[nonzero] / counts[nonzero]
        centroids[nonzero, 2] = sz[nonzero] / counts[nonzero]
        _, snap_idx = pt_tree.query(centroids, k=1)
        cur = surf_pts[snap_idx]
    return cur


def _equalize_hex_edge_lengths(
    surf_pts: np.ndarray, seeds: np.ndarray, target_pitch: float, iters: int = 16
) -> np.ndarray:
    """
    Surface-constrained spring relaxation driving each seed's 6 nearest neighbor distances
    toward ``target_pitch`` so dual cells converge to equal-side regular hexagons.
    """
    n = len(seeds)
    if n < 7:
        return seeds
    pt_tree = cKDTree(surf_pts)
    cur = seeds.copy()
    k = min(7, n)
    for step in range(iters):
        stree = cKDTree(cur)
        dists, nbrs = stree.query(cur, k=k)
        # Neighbor vectors: (N, k-1, 3)
        nbr_pts = cur[nbrs[:, 1:]]
        d = dists[:, 1:]
        valid = (d > 1e-4) & (d < target_pitch * 1.48)
        diff = nbr_pts - cur[:, None, :]  # pointing from cur -> neighbor
        unit = diff / np.maximum(d[:, :, None], 1e-6)
        # Positive (d - target_pitch) pulls toward neighbor; negative pushes away
        disp_per_nbr = np.where(
            valid[:, :, None],
            (d - target_pitch)[:, :, None] * unit,
            0.0,
        )
        counts = np.maximum(valid.sum(axis=1, keepdims=True), 1)
        step_alpha = 0.28 * (1.0 - 0.5 * step / max(1, iters))
        moved = cur + step_alpha * (disp_per_nbr.sum(axis=1) / counts)
        _, snap = pt_tree.query(moved, k=1)
        cur = surf_pts[snap]
    return cur


def _extract_arc_length_contour_xy(
    mesh: trimesh.Trimesh, z_val: float
) -> Optional[np.ndarray]:
    """Extract the longest closed XY contour of ``mesh`` at ``z_val`` as ordered (M, 2) points."""
    try:
        sec = mesh.section(plane_origin=[0.0, 0.0, z_val], plane_normal=[0.0, 0.0, 1.0])
    except Exception:
        return None
    if sec is None or len(sec.entities) == 0:
        return None
    best: Optional[np.ndarray] = None
    best_len = 0.0
    for ent in sec.entities:
        pts3 = sec.vertices[ent.points]
        if len(pts3) < 4:
            continue
        pts2 = np.asarray(pts3[:, :2], dtype=np.float64)
        seg_l = float(np.linalg.norm(np.diff(pts2, axis=0), axis=1).sum())
        if seg_l > best_len:
            best_len = seg_l
            best = pts2
    return best


def _sample_contour_at_arc_steps(contour_xy: np.ndarray, ds: float, min_3d_sep: float) -> np.ndarray:
    """Sample a 2D polyline at exact uniform arc-length intervals ``ds``, deduplicating thin-wall opposite faces."""
    diffs = np.diff(contour_xy, axis=0)
    seg_lens = np.linalg.norm(diffs, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total_len = float(cum[-1])
    if total_len < ds * 0.5:
        return contour_xy[:1]

    n_steps = max(2, int(round(total_len / ds)))
    step_exact = total_len / n_steps
    s_targets = np.arange(n_steps, dtype=np.float64) * step_exact

    xs = np.interp(s_targets, cum, contour_xy[:, 0])
    ys = np.interp(s_targets, cum, contour_xy[:, 1])
    raw = np.column_stack([xs, ys])

    # Deduplicate opposing sides of a thin curved wall so front/back share aligned regular cells
    kept: List[np.ndarray] = []
    min_sep_sq = min_3d_sep * min_3d_sep
    for p in raw:
        if not kept:
            kept.append(p)
            continue
        arr_k = np.asarray(kept)
        d2 = np.sum((arr_k - p) ** 2, axis=1)
        if float(np.min(d2)) >= min_sep_sq:
            kept.append(p)
    return np.asarray(kept, dtype=np.float64)


def _lattice_grid_params(pattern: str, pitch: float) -> Tuple[float, float, float, float]:
    """
    Return (col_step_u, row_step_v, odd_col_v_shift, odd_row_u_shift) for regular 2D tilings.
    - 'hexagonal': flat-top regular hexagons (6 equal sides, 0 deg top/bottom, 60 deg walls!)
    - 'square':    orthogonal equal-side squares
    - 'diamond':   45-deg rotated equal-side diamonds (all walls 45 deg self-supporting!)
    - 'brick':     running-bond staggered horizontal bricks
    """
    if pattern == "hexagonal":
        return pitch * math.sqrt(3.0) * 0.5, pitch, 0.5 * pitch, 0.0
    if pattern == "square":
        return pitch, pitch, 0.0, 0.0
    if pattern == "diamond":
        d = pitch * math.sqrt(2.0)
        return 0.5 * d, d, 0.5 * d, 0.0
    if pattern == "brick":
        return pitch * 1.35, pitch * 0.72, 0.0, 0.5 * pitch * 1.35
    return pitch * math.sqrt(3.0) * 0.5, pitch, 0.5 * pitch, 0.0


def _generate_developable_or_wall_seeds(
    mesh: trimesh.Trimesh,
    surf_pts: np.ndarray,
    pt_tree: cKDTree,
    pattern: str,
    pitch: float,
) -> Optional[np.ndarray]:
    """
    For extruded / wavy-wall / cylindrical / vertical-sided shells, parameterize the
    horizontal wall contour by exact arc-length ``s`` and elevation ``z`` so the cell
    lattice is isometric (mathematically exact equal-side hexagons / squares / diamonds / bricks).
    """
    face_normals = np.asarray(mesh.face_normals, dtype=np.float64)
    face_areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total_area = float(face_areas.sum())
    if total_area <= 1e-6:
        return None

    # Measure fraction of surface area that is mostly vertical (|nz| < 0.35)
    vert_area_frac = float(face_areas[np.abs(face_normals[:, 2]) < 0.35].sum() / total_area)
    if vert_area_frac < 0.55:
        return None

    z_min = float(mesh.bounds[0, 2])
    z_max = float(mesh.bounds[1, 2])
    z_span = z_max - z_min
    if z_span < pitch * 0.6:
        return None

    contour = _extract_arc_length_contour_xy(mesh, 0.5 * (z_min + z_max))
    if contour is None or len(contour) < 4:
        return None

    du, dv, odd_col_dv, odd_row_du = _lattice_grid_params(pattern, pitch)

    # For brick pattern (row-shifted), sample finely along s and step by odd_row_du
    if abs(odd_row_du) > 1e-6:
        cols_even = _sample_contour_at_arc_steps(contour, du, min_3d_sep=du * 0.72)
        # Shift starting contour point by half a brick for odd rows
        diffs = np.diff(contour, axis=0)
        cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(diffs, axis=1))])
        total_len = float(cum[-1])
        if total_len < du:
            return None
        n_steps = max(2, int(round(total_len / du)))
        step_ex = total_len / n_steps
        s_odd = (np.arange(n_steps, dtype=np.float64) * step_ex + 0.5 * step_ex) % total_len
        raw_odd = np.column_stack(
            [np.interp(s_odd, cum, contour[:, 0]), np.interp(s_odd, cum, contour[:, 1])]
        )
        cols_odd_list: List[np.ndarray] = []
        min_sq = (du * 0.72) ** 2
        for p in raw_odd:
            if not cols_odd_list or float(np.min(np.sum((np.asarray(cols_odd_list) - p) ** 2, axis=1))) >= min_sq:
                cols_odd_list.append(p)
        cols_odd = np.asarray(cols_odd_list, dtype=np.float64) if cols_odd_list else cols_even

        pts: List[np.ndarray] = []
        row = 0
        z_cur = z_min + 0.5 * dv
        while z_cur <= z_max + 0.35 * dv:
            col_set = cols_odd if (row % 2 == 1) else cols_even
            for xy in col_set:
                p3 = np.array([xy[0], xy[1], min(z_max, max(z_min, z_cur))], dtype=np.float64)
                dist, snap_idx = pt_tree.query(p3, k=1)
                if dist <= 0.65 * pitch:
                    pts.append(surf_pts[snap_idx])
            z_cur += dv
            row += 1
        return np.asarray(pts, dtype=np.float64) if len(pts) >= 4 else None

    # Column-shifted lattices (hexagonal, diamond, square)
    col_xy = _sample_contour_at_arc_steps(contour, du, min_3d_sep=du * 0.72)
    if len(col_xy) < 2:
        return None

    pts = []
    for c_idx, xy in enumerate(col_xy):
        z_shift = odd_col_dv if (c_idx % 2 == 1) else 0.0
        z_cur = z_min + z_shift
        while z_cur <= z_max + 0.35 * dv:
            p3 = np.array([xy[0], xy[1], min(z_max, max(z_min, z_cur))], dtype=np.float64)
            dist, snap_idx = pt_tree.query(p3, k=1)
            if dist <= 0.65 * pitch:
                pts.append(surf_pts[snap_idx])
            z_cur += dv

    if len(pts) < 4:
        return None
    return np.asarray(pts, dtype=np.float64)


def _generate_triplanar_lattice_seeds(
    mesh: trimesh.Trimesh,
    surf_pts: np.ndarray,
    surf_normals: np.ndarray,
    pt_tree: cKDTree,
    pattern: str,
    pitch: float,
) -> np.ndarray:
    """
    Generate a clean 3D regular surface lattice for general shapes (boxes, cones, arbitrary CAD)
    by laying exact 2D lattices on the 3 principal projections (XY, XZ, YZ) filtered by face
    normal alignment, then merging with minimum-spacing deduplication.
    """
    bmin, bmax = mesh.bounds[0], mesh.bounds[1]
    du, dv, odd_col_dv, odd_row_du = _lattice_grid_params(pattern, pitch)
    min_sep = pitch * (0.76 if pattern != "brick" else 0.60)
    min_sep_sq = min_sep * min_sep

    candidates: List[np.ndarray] = []

    # Three projections: (u_axis_idx, v_axis_idx, normal_axis_idx)
    # Keeping global Z (2) as the v-axis for vertical sides preserves flat-top hexagons & 45 deg diamonds!
    planes = (
        (0, 2, 1),  # Front/Back walls (normal ~ Y), u=X, v=Z
        (1, 2, 0),  # Left/Right walls (normal ~ X), u=Y, v=Z
        (0, 1, 2),  # Top/Bottom faces (normal ~ Z), u=X, v=Y
    )

    for u_ax, v_ax, n_ax in planes:
        mask = np.abs(surf_normals[:, n_ax]) >= 0.52
        if not np.any(mask):
            continue
        sub_pts = surf_pts[mask]
        sub_tree = cKDTree(sub_pts)
        u0, u1 = float(bmin[u_ax]), float(bmax[u_ax])
        v0, v1 = float(bmin[v_ax]), float(bmax[v_ax])
        n_mid = 0.5 * float(bmin[n_ax] + bmax[n_ax])

        # Also query both sides along n_ax (min & max surface)
        n_probes = [float(bmin[n_ax]), float(bmax[n_ax])]
        if (bmax[n_ax] - bmin[n_ax]) > pitch * 2.5:
            n_probes.append(n_mid)

        u_vals = np.arange(u0, u1 + du, du)
        for col_idx, u_val in enumerate(u_vals):
            v_shift = odd_col_dv if (col_idx % 2 == 1) else 0.0
            v_vals = np.arange(v0 + v_shift, v1 + dv, dv)
            for row_idx, v_val in enumerate(v_vals):
                u_eff = u_val + (odd_row_du if (row_idx % 2 == 1) else 0.0)
                for n_val in n_probes:
                    q = np.zeros(3, dtype=np.float64)
                    q[u_ax] = u_eff
                    q[v_ax] = v_val
                    q[n_ax] = n_val
                    dist, s_idx = sub_tree.query(q, k=1)
                    if dist <= max(du, dv) * 0.85:
                        candidates.append(sub_pts[s_idx])

    if not candidates:
        return _poisson_disk_sample(surf_pts, min_radius=pitch * 0.85, seed=42)

    kept: List[np.ndarray] = []
    for p in candidates:
        if not kept:
            kept.append(p)
            continue
        arr_k = np.asarray(kept)
        if float(np.min(np.sum((arr_k - p) ** 2, axis=1))) >= min_sep_sq:
            kept.append(p)
    return np.asarray(kept, dtype=np.float64)


def _generate_outer_surface_seeds(
    mesh: trimesh.Trimesh, pattern: str, pitch: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Distribute cell seeds strictly on the 3D outer surface of the model:
      - 'hexagonal': isometric equal-side flat-top hex lattice (geodesic on spheres,
                     arc-length (s,z) on curved walls/cylinders, tri-planar + spring
                     edge-equalization on arbitrary 3D shapes)
      - 'square' / 'diamond' / 'brick': exact regular surface lattice
      - 'triangular': starts from regular hexagonal dual vertices (dualized into triangles)
      - 'voronoi': jittered Poisson-disk sampling + 2-iter Lloyd CVT
    """
    surf_pts, face_idx = trimesh.sample.sample_surface(mesh, count=24000)
    surf_pts = np.asarray(surf_pts, dtype=np.float64)
    if len(surf_pts) == 0:
        return np.zeros((0, 3), dtype=np.float64), surf_pts
    surf_normals = np.asarray(mesh.face_normals[face_idx], dtype=np.float64)

    pt_tree = cKDTree(surf_pts)
    mean_pt = surf_pts.mean(axis=0)
    centered = surf_pts - mean_pt

    base_pattern = "hexagonal" if pattern == "triangular" else pattern

    if base_pattern == "voronoi":
        seeds = _poisson_disk_sample(surf_pts, min_radius=pitch * 0.84, seed=42)
        if len(seeds) < 3:
            return seeds, surf_pts
        return _fast_lloyd_cvt(surf_pts, seeds, iters=2), surf_pts

    # 1. Nearly spherical surface -> geodesic icosphere (optimal regular hexagonal dome)
    if base_pattern == "hexagonal":
        radii = np.linalg.norm(centered, axis=1)
        r_mean = float(radii.mean())
        r_std = float(radii.std())
        if r_mean > 1e-3 and (r_std / r_mean) < 0.06:
            area = 4.0 * math.pi * r_mean * r_mean
            hex_area = (math.sqrt(3.0) * 0.5) * pitch * pitch
            target_n = max(12.0, area / max(hex_area, 1e-3))
            best_sub = 1
            best_err = abs(42.0 - target_n)
            for sub in range(1, 5):
                nv = 10.0 * (4**sub) + 2.0
                err = abs(nv - target_n)
                if err < best_err:
                    best_err = err
                    best_sub = sub
            ico = trimesh.creation.icosphere(subdivisions=best_sub, radius=r_mean)
            ico_verts = np.asarray(ico.vertices, dtype=np.float64) + mean_pt
            _, snap = pt_tree.query(ico_verts, k=1)
            return surf_pts[snap], surf_pts

    # 2. Developable / Curved-wall / Extruded / Cylindrical surface -> exact isometric (s, z) lattice
    wall_seeds = _generate_developable_or_wall_seeds(mesh, surf_pts, pt_tree, base_pattern, pitch)
    if wall_seeds is not None and len(wall_seeds) >= 4:
        return wall_seeds, surf_pts

    # 3. Nearly planar surface -> exact 2D regular lattice aligned with global Z
    cov = (centered.T @ centered) / max(1, len(surf_pts))
    eigvals, eigvecs = np.linalg.eigh(cov)
    if eigvals[0] < 0.02 * max(eigvals[2], 1e-6):
        n_axis = eigvecs[:, 0]
        # Align v_axis with global Z projected onto the plane so hexagons stay flat-top!
        z_proj = np.array([0.0, 0.0, 1.0]) - float(n_axis[2]) * n_axis
        if np.linalg.norm(z_proj) > 0.2:
            v_axis = z_proj / np.linalg.norm(z_proj)
            u_axis = np.cross(v_axis, n_axis)
            u_axis = u_axis / max(np.linalg.norm(u_axis), 1e-9)
        else:
            u_axis, v_axis = eigvecs[:, 2], eigvecs[:, 1]

        proj_u, proj_v = centered @ u_axis, centered @ v_axis
        u_min, u_max = float(proj_u.min()), float(proj_u.max())
        v_min, v_max = float(proj_v.min()), float(proj_v.max())
        du, dv, odd_col_dv, odd_row_du = _lattice_grid_params(base_pattern, pitch)
        grid_pts = []
        col_idx = 0
        u_val = u_min
        while u_val <= u_max + du:
            v_shift = odd_col_dv if (col_idx % 2 == 1) else 0.0
            row_idx = 0
            v_val = v_min + v_shift
            while v_val <= v_max + dv:
                u_eff = u_val + (odd_row_du if (row_idx % 2 == 1) else 0.0)
                p3 = mean_pt + u_eff * u_axis + v_val * v_axis
                dist, snap_idx = pt_tree.query(p3, k=1)
                if dist <= 0.65 * pitch:
                    grid_pts.append(surf_pts[snap_idx])
                v_val += dv
                row_idx += 1
            u_val += du
            col_idx += 1
        if len(grid_pts) >= 4:
            return np.asarray(grid_pts, dtype=np.float64), surf_pts

    # 4. General arbitrary 3D surface -> tri-planar regular lattice + equal-edge spring relaxation
    seeds = _generate_triplanar_lattice_seeds(mesh, surf_pts, surf_normals, pt_tree, base_pattern, pitch)
    if len(seeds) >= 7 and base_pattern == "hexagonal":
        seeds = _equalize_hex_edge_lengths(surf_pts, seeds, target_pitch=pitch, iters=16)
        seeds = _fast_lloyd_cvt(surf_pts, seeds, iters=6)
    return seeds, surf_pts


# -----------------------------------------------------------------------------
# Triangular Dual Cell Synthesis (Equilateral Iso-Grid)
# -----------------------------------------------------------------------------


def _build_triangular_dual_lattice(
    hex_seeds: np.ndarray,
    surf_pts: np.ndarray,
    pitch: float,
    strut_radius: float,
    flatten_deg: float,
    mesh_zmin: float,
    mesh_zmax: float,
) -> NormalVoronoiLattice:
    """
    Dualize a regular hexagonal seed grid into equilateral triangular cells:
    every mutually-adjacent triple of lattice vertices (A, B, C) defines one triangular
    cell bounded by 3 exact edge half-planes of equal length, creating an iso-grid
    photopolymer channel web.
    """
    n = len(hex_seeds)
    if n < 3:
        return NormalVoronoiLattice([], hex_seeds, [], np.zeros((0, 2)))

    tree = cKDTree(hex_seeds)
    max_edge = pitch * 1.32
    nbr_lists = tree.query_ball_point(hex_seeds, r=max_edge)
    adj = [set(nb) - {i} for i, nb in enumerate(nbr_lists)]

    # Enumerate unique equilateral triangles (i < j < k) with all 3 edges <= max_edge
    tri_tuples: List[Tuple[int, int, int]] = []
    for i in range(n):
        for j in adj[i]:
            if j <= i:
                continue
            common = adj[i].intersection(adj[j])
            for k in common:
                if k > j:
                    tri_tuples.append((i, j, k))

    if not tri_tuples:
        return NormalVoronoiLattice([], hex_seeds, [], np.zeros((0, 2)))

    surf_tree = cKDTree(surf_pts) if len(surf_pts) > 0 else None
    tri_centers: List[np.ndarray] = []
    cell_planes: List[List[CellHalfPlane]] = []
    z_bounds_list: List[Tuple[float, float]] = []
    capsules: List[Capsule] = []
    seen_edges = set()

    for i, j, k in tri_tuples:
        pa, pb, pc = hex_seeds[i], hex_seeds[j], hex_seeds[k]
        # Reject degenerate sliver triangles
        e_ab = pb - pa
        e_ac = pc - pa
        tri_n = np.cross(e_ab, e_ac)
        area2 = float(np.linalg.norm(tri_n))
        if area2 < 0.15 * pitch * pitch:
            continue
        tri_n = tri_n / area2
        center = (pa + pb + pc) / 3.0

        planes_t: List[CellHalfPlane] = []
        z_lo = float(min(pa[2], pb[2], pc[2])) - 1.0
        z_hi = float(max(pa[2], pb[2], pc[2])) + 1.0

        for u_idx, v_idx, p0, p1, p_opp in (
            (i, j, pa, pb, pc),
            (j, k, pb, pc, pa),
            (k, i, pc, pa, pb),
        ):
            edge_vec = p1 - p0
            e_len = float(np.linalg.norm(edge_vec))
            if e_len < 1e-5:
                continue
            # Outward unit normal in the triangle plane, perpendicular to edge (p0, p1)
            out_n = np.cross(edge_vec / e_len, tri_n)
            on_len = float(np.linalg.norm(out_n))
            if on_len < 1e-6:
                continue
            out_n = out_n / on_len
            # Orient outward (away from opposite vertex p_opp)
            m = 0.5 * (p0 + p1)
            if float(np.dot(out_n, p_opp - m)) > 0.0:
                out_n = -out_n

            tilt_from_horiz_deg = math.degrees(math.acos(min(1.0, abs(float(out_n[2])))))
            if flatten_deg > 0.0 and tilt_from_horiz_deg < (flatten_deg - 1e-3):
                if surf_tree is not None:
                    _, snap_idx = surf_tree.query(m, k=1)
                    z_anchor = float(surf_pts[snap_idx, 2])
                else:
                    z_anchor = float(m[2])
                sign_z = 1.0 if out_n[2] >= 0.0 else -1.0
                planes_t.append(
                    CellHalfPlane(
                        ux=0.0,
                        uy=0.0,
                        uz=sign_z,
                        dot_m=sign_z * z_anchor,
                        dist=e_len,
                        flattened=True,
                    )
                )
            else:
                planes_t.append(
                    CellHalfPlane(
                        ux=float(out_n[0]),
                        uy=float(out_n[1]),
                        uz=float(out_n[2]),
                        dot_m=float(np.dot(m, out_n)),
                        dist=e_len,
                        flattened=False,
                    )
                )

            ekey = (min(u_idx, v_idx), max(u_idx, v_idx))
            if ekey not in seen_edges:
                seen_edges.add(ekey)
                capsules.append(
                    Capsule(
                        a=p0.copy(),
                        b=p1.copy(),
                        radius=strut_radius,
                        z_min=float(min(p0[2], p1[2])),
                        z_max=float(max(p0[2], p1[2])),
                    )
                )

        if len(planes_t) == 3:
            planes_t.sort(key=lambda hp: (0 if hp.flattened else 1, -abs(hp.uz), hp.dist))
            tri_centers.append(center)
            cell_planes.append(planes_t)
            z_bounds_list.append((max(mesh_zmin - 1.0, z_lo), min(mesh_zmax + 1.0, z_hi)))

    if not tri_centers:
        return NormalVoronoiLattice([], hex_seeds, [], np.zeros((0, 2)))

    return NormalVoronoiLattice(
        capsules=capsules,
        seeds=np.asarray(tri_centers, dtype=np.float64),
        cell_planes=cell_planes,
        seed_z_bounds=np.asarray(z_bounds_list, dtype=np.float64),
    )


# -----------------------------------------------------------------------------
# 3D Wireframe Extraction & Exact 2D Half-Plane Clipping
# -----------------------------------------------------------------------------


def _build_voronoi_surface_wireframe(
    field: VolumetricField, seeds: np.ndarray, pitch: float, strut_radius: float
) -> List[Capsule]:
    """Extract true 3D Voronoi / Hexagonal cell boundary polygons on the outer surface for 3D UI preview."""
    if len(seeds) < 4:
        return []
    try:
        verts, faces, _, _ = measure.marching_cubes(
            field.sdf, level=0.0, spacing=(field.pitch, field.pitch, field.pitch)
        )
    except Exception:
        return []
    verts = verts + field.origin

    stree = cKDTree(seeds)
    _, v_labels = stree.query(verts, k=1)
    tri_lbl = v_labels[faces]

    is_corner = (
        (tri_lbl[:, 0] != tri_lbl[:, 1])
        & (tri_lbl[:, 1] != tri_lbl[:, 2])
        & (tri_lbl[:, 0] != tri_lbl[:, 2])
    )
    corner_f = np.flatnonzero(is_corner)
    if len(corner_f) == 0:
        return []

    node_coords: List[np.ndarray] = []
    triple_to_nid: Dict[Tuple[int, int, int], int] = {}
    pair_to_nids: Dict[Tuple[int, int], List[int]] = {}

    for f_idx in corner_f:
        triple = tuple(sorted((int(tri_lbl[f_idx, 0]), int(tri_lbl[f_idx, 1]), int(tri_lbl[f_idx, 2]))))
        c = verts[faces[f_idx]].mean(axis=0)
        if triple not in triple_to_nid:
            nid = len(node_coords)
            triple_to_nid[triple] = nid
            node_coords.append(c)
            for pair in ((triple[0], triple[1]), (triple[1], triple[2]), (triple[0], triple[2])):
                pair_to_nids.setdefault(pair, []).append(nid)
        else:
            nid = triple_to_nid[triple]
            node_coords[nid] = 0.5 * (node_coords[nid] + c)

    nodes_arr = np.asarray(node_coords, dtype=np.float64)
    capsules: List[Capsule] = []
    seen = set()
    max_edge = pitch * 1.45

    def _add_edge(u: int, v: int) -> None:
        key = (min(u, v), max(u, v))
        if u != v and key not in seen and np.linalg.norm(nodes_arr[u] - nodes_arr[v]) <= max_edge:
            seen.add(key)
            a, b = nodes_arr[u], nodes_arr[v]
            capsules.append(
                Capsule(
                    a=a,
                    b=b,
                    radius=strut_radius,
                    z_min=float(min(a[2], b[2])),
                    z_max=float(max(a[2], b[2])),
                )
            )

    for _pair, nids in pair_to_nids.items():
        if len(nids) == 2:
            _add_edge(nids[0], nids[1])
        elif len(nids) > 2:
            sub = nodes_arr[nids]
            sub_tree = cKDTree(sub)
            for i_local, u in enumerate(nids):
                _, nbrs = sub_tree.query(sub[i_local], k=min(3, len(nids)))
                for j_local in np.atleast_1d(nbrs)[1:]:
                    _add_edge(u, nids[int(j_local)])

    return capsules


def _build_gyroid_lattice(
    field: VolumetricField, pitch: float, strut_radius: float
) -> NormalVoronoiLattice:
    """
    Build a true 3D Triply Periodic Minimal Surface (Gyroid) wireframe preview:
        F(x, y, z) = sin(kx)*cos(ky) + sin(ky)*cos(kz) + sin(kz)*cos(kx) = 0
    Actual layer slicing evaluates F(x, y, z_mid) analytically at every Z layer.
    """
    k = 2.0 * math.pi / max(3.0, pitch)
    bounds = field.mesh.bounds
    z0, z1 = float(bounds[0, 2]), float(bounds[1, 2])
    x0, x1 = float(bounds[0, 0]), float(bounds[1, 0])
    y0, y1 = float(bounds[0, 1]), float(bounds[1, 1])

    # Sample coarse 3D wireframe slices along Z for crisp 3D preview
    step_xy = max(0.65, pitch / 14.0)
    xs = np.arange(x0, x1 + step_xy, step_xy)
    ys = np.arange(y0, y1 + step_xy, step_xy)
    capsules: List[Capsule] = []
    seeds_list: List[np.ndarray] = []

    if len(xs) >= 3 and len(ys) >= 3:
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        n_slices = max(4, min(14, int(round((z1 - z0) / max(1.5, pitch * 0.28)))))
        zs = np.linspace(z0 + 0.8, max(z0 + 0.9, z1 - 0.8), n_slices)
        sx, cx = np.sin(k * gx), np.cos(k * gx)
        sy, cy = np.sin(k * gy), np.cos(k * gy)

        for zv in zs:
            sz, cz = math.sin(k * zv), math.cos(k * zv)
            val = sx * cy + sy * cz + sz * cx
            contours = measure.find_contours(val, 0.0)
            for c in contours:
                if len(c) < 2:
                    continue
                px = x0 + c[:, 0] * step_xy
                py = y0 + c[:, 1] * step_xy
                # Keep segments inside the voxelized model volume
                ix = np.clip(np.round((px - field.origin[0]) / field.pitch).astype(int), 0, field.shape[0] - 1)
                iy = np.clip(np.round((py - field.origin[1]) / field.pitch).astype(int), 0, field.shape[1] - 1)
                iz = int(np.clip(round((zv - field.origin[2]) / field.pitch), 0, field.shape[2] - 1))
                inside = field.vol[ix, iy, iz]
                for m in range(len(px) - 1):
                    if inside[m] and inside[m + 1]:
                        pa = np.array([px[m], py[m], zv], dtype=np.float64)
                        pb = np.array([px[m + 1], py[m + 1], zv], dtype=np.float64)
                        capsules.append(Capsule(a=pa, b=pb, radius=strut_radius, z_min=zv, z_max=zv))
                        if m % 6 == 0:
                            seeds_list.append(pa)

    seeds = np.asarray(seeds_list, dtype=np.float64) if seeds_list else np.zeros((0, 3), dtype=np.float64)
    return NormalVoronoiLattice(
        capsules=capsules,
        seeds=seeds,
        cell_planes=[],
        seed_z_bounds=np.zeros((len(seeds), 2), dtype=np.float64),
        pattern="gyroid",
        pitch=pitch,
    )


def _generate_cubic_3d_seeds(mesh: trimesh.Trimesh, pitch: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate a true 3D tilted cubic lattice (PrusaSlicer Cubic orientation: [1,1,1] aligned with +Z)
    so every cell wall is inclined at 54.7 deg from horizontal (100% self-supporting).
    """
    bounds = mesh.bounds
    center = 0.5 * (bounds[0] + bounds[1])
    radius = float(np.linalg.norm(bounds[1] - bounds[0])) * 0.6 + pitch

    # Orthonormal basis with e3 along [1, 1, 1] / sqrt(3) -> cube faces tilt 54.7 deg from XY
    r1 = np.array([1.0, -1.0, 0.0], dtype=np.float64) / math.sqrt(2.0)
    r2 = np.array([1.0, 1.0, -2.0], dtype=np.float64) / math.sqrt(6.0)
    r3 = np.array([1.0, 1.0, 1.0], dtype=np.float64) / math.sqrt(3.0)
    rot = np.vstack([r1, r2, r3])  # maps world -> cube axes; rot.T maps cube -> world

    n_half = max(2, int(math.ceil(radius / pitch)))
    grid_1d = np.arange(-n_half, n_half + 1, dtype=np.float64) * pitch
    gx, gy, gz = np.meshgrid(grid_1d, grid_1d, grid_1d, indexing="ij")
    pts_cube = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    pts_world = center + pts_cube @ rot

    # Keep seeds inside expanded bounding box of the mesh
    margin = pitch * 0.65
    in_box = (
        (pts_world[:, 0] >= bounds[0, 0] - margin)
        & (pts_world[:, 0] <= bounds[1, 0] + margin)
        & (pts_world[:, 1] >= bounds[0, 1] - margin)
        & (pts_world[:, 1] <= bounds[1, 1] + margin)
        & (pts_world[:, 2] >= bounds[0, 2] - margin)
        & (pts_world[:, 2] <= bounds[1, 2] + margin)
    )
    seeds = pts_world[in_box]
    surf_pts, _ = trimesh.sample.sample_surface(mesh, count=min(6000, max(1200, len(mesh.faces) * 4)))
    return seeds, np.asarray(surf_pts, dtype=np.float64)


def generate_normal_dendritic_infill(
    field: VolumetricField, cfg: SlicerConfig
) -> NormalVoronoiLattice:
    """
    Stage 3: Build the 3D cellular seed field and precompute exact 3D bisector half-planes
    for Hexagonal (regular equal-side flat-top), Voronoi, Square, Diamond (45-deg),
    Triangular (equilateral iso-grid), Brick, Gyroid (TPMS), or Cubic (54.7-deg tilted 3D).
    """
    icfg = cfg.infill
    pitch = max(2.5, icfg.target_cell_pitch)
    pattern = icfg.pattern

    if pattern == "gyroid":
        return _build_gyroid_lattice(field, pitch=pitch, strut_radius=icfg.strut_radius)

    if pattern == "cubic":
        seeds, surf_pts = _generate_cubic_3d_seeds(field.mesh, pitch)
    else:
        seeds, surf_pts = _generate_outer_surface_seeds(field.mesh, pattern, pitch)
    n_surface_seeds = len(seeds)
    mesh_zmin = float(field.mesh.bounds[0, 2])
    mesh_zmax = float(field.mesh.bounds[1, 2])
    flatten_deg = 0.0 if pattern == "cubic" else max(0.0, float(icfg.flatten_angle_deg))

    # Special dual construction for Equilateral Triangular Iso-Grid cells
    if pattern == "triangular" and n_surface_seeds >= 3:
        tri_lat = _build_triangular_dual_lattice(
            hex_seeds=seeds,
            surf_pts=surf_pts,
            pitch=pitch,
            strut_radius=icfg.strut_radius,
            flatten_deg=flatten_deg,
            mesh_zmin=mesh_zmin,
            mesh_zmax=mesh_zmax,
        )
        if len(tri_lat.seeds) > 0:
            tri_lat.pattern = pattern
            tri_lat.pitch = pitch
            return tri_lat

    # Optional deep isotropic 3D core seeds in full_to_medial mode when max_normal_depth is set
    if icfg.mode == "full_to_medial" and icfg.max_normal_depth is not None and pattern != "cubic":
        outer_shell = cfg.fff.perimeters * cfg.fff.line_width
        core_depth = outer_shell + icfg.max_normal_depth
        core_idx = np.argwhere(field.vol & (field.dist_in >= core_depth))
        if len(core_idx) > 0:
            core_pts = field.origin + core_idx * field.pitch
            core_arr = _poisson_disk_sample(core_pts, min_radius=pitch * 0.85, seed=7)
            if len(core_arr) > 0:
                seeds = np.vstack([seeds, core_arr]) if len(seeds) > 0 else core_arr

    n_seeds = len(seeds)
    if n_seeds == 0:
        return NormalVoronoiLattice([], seeds, [], np.zeros((0, 2)), pattern=pattern, pitch=pitch)

    surf_tree = cKDTree(surf_pts) if len(surf_pts) > 0 else None

    k_nbrs = min(n_seeds, 28)
    tree = cKDTree(seeds)
    dists, nbr_indices = tree.query(seeds, k=k_nbrs)
    if k_nbrs == 1:
        dists = dists[:, None]
        nbr_indices = nbr_indices[:, None]

    cell_planes: List[List[CellHalfPlane]] = []
    z_bounds = np.zeros((n_seeds, 2), dtype=np.float64)

    for i in range(n_seeds):
        si = seeds[i]
        planes_i: List[CellHalfPlane] = []
        max_r = pitch * 1.6
        z_lo = si[2] - max_r
        z_hi = si[2] + max_r

        for slot in range(1, k_nbrs):
            j = int(nbr_indices[i, slot])
            if j == i or j >= n_seeds:
                continue
            d = float(dists[i, slot])
            if d < 1e-6:
                continue
            sj = seeds[j]
            u = (sj - si) / d
            m = 0.5 * (si + sj)

            # Angle of the bisector plane relative to horizontal XY plane (0 deg = flat, 90 deg = vertical)
            tilt_from_horiz_deg = math.degrees(math.acos(min(1.0, abs(float(u[2])))))

            if flatten_deg > 0.0 and tilt_from_horiz_deg < (flatten_deg - 1e-3):
                if surf_tree is not None and i < n_surface_seeds and j < n_surface_seeds:
                    _, snap_idx = surf_tree.query(m, k=1)
                    z_anchor = float(surf_pts[snap_idx, 2])
                    z_anchor = max(
                        min(float(si[2]), float(sj[2])),
                        min(max(float(si[2]), float(sj[2])), z_anchor),
                    )
                else:
                    z_anchor = float(m[2])

                sign_z = 1.0 if u[2] >= 0.0 else -1.0
                planes_i.append(
                    CellHalfPlane(
                        ux=0.0,
                        uy=0.0,
                        uz=sign_z,
                        dot_m=sign_z * z_anchor,
                        dist=d,
                        flattened=True,
                    )
                )
                z_lo = min(z_lo, z_anchor - 1.0)
                z_hi = max(z_hi, z_anchor + 1.0)
            else:
                dot_m = float(np.dot(m, u))
                planes_i.append(
                    CellHalfPlane(
                        ux=float(u[0]),
                        uy=float(u[1]),
                        uz=float(u[2]),
                        dot_m=dot_m,
                        dist=d,
                        flattened=False,
                    )
                )

            if slot <= 8:
                max_r = max(max_r, d * 0.95)

        planes_i.sort(key=lambda hp: (0 if hp.flattened else 1, -abs(hp.uz), hp.dist))
        cell_planes.append(planes_i)
        z_bounds[i, 0] = max(mesh_zmin - 1.0, min(z_lo, si[2] - max_r))
        z_bounds[i, 1] = min(mesh_zmax + 1.0, max(z_hi, si[2] + max_r))

    capsules = _build_voronoi_surface_wireframe(
        field, seeds[:n_surface_seeds], pitch, icfg.strut_radius
    )
    return NormalVoronoiLattice(capsules, seeds, cell_planes, z_bounds, pattern=pattern, pitch=pitch)
