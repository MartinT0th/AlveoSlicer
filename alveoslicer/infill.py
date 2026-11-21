"""Stage 2-3: 3D outer-surface Voronoi / Hexagonal cell synthesis and 2D half-plane clipping."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from skimage import measure

from .config import SlicerConfig
from .geometry import VolumetricField


@dataclass
class Capsule:
    """3D edge primitive used by the UI 3D preview (true Voronoi/Hexagon cell edges)."""

    a: np.ndarray
    b: np.ndarray
    radius: float
    z_min: float
    z_max: float


@dataclass
class CellHalfPlane:
    """Precomputed 3D bisector plane (x - m) . u <= -inset for exact 2D slicing."""

    ux: float
    uy: float
    uz: float
    dot_m: float
    dist: float
    flattened: bool = False


class NormalVoronoiLattice(list):
    """
    Subclass of ``list[Capsule]`` so the 3D preview displays true 3D Voronoi/Hexagonal
    cell wireframes while ``slice_model_to_layers`` slices exact 3D half-planes.
    """

    def __init__(
        self,
        capsules: List[Capsule],
        seeds: np.ndarray,
        cell_planes: List[List[CellHalfPlane]],
        seed_z_bounds: np.ndarray,
    ):
        super().__init__(capsules)
        self.seeds = seeds
        self.cell_planes = cell_planes
        self.seed_z_bounds = seed_z_bounds


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


def _generate_outer_surface_seeds(
    mesh: trimesh.Trimesh, pattern: str, pitch: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Distribute cell seeds strictly on the 3D outer surface of the model:
      - 'hexagonal': geodesic icosphere on spheres, exact triangular grid on planes, 35-iter CVT elsewhere
      - 'voronoi': jittered Poisson-disk sampling + 2-iter Lloyd CVT
    """
    surf_pts, _ = trimesh.sample.sample_surface(mesh, count=22000)
    surf_pts = np.asarray(surf_pts, dtype=np.float64)
    if len(surf_pts) == 0:
        return np.zeros((0, 3), dtype=np.float64), surf_pts

    pt_tree = cKDTree(surf_pts)
    mean_pt = surf_pts.mean(axis=0)
    centered = surf_pts - mean_pt

    if pattern == "hexagonal":
        # Case 1: Nearly spherical surface -> geodesic icosahedral hex dome
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

        # Case 2: Nearly planar surface -> exact regular 2D triangular grid (exact hexagons)
        cov = (centered.T @ centered) / max(1, len(surf_pts))
        eigvals, eigvecs = np.linalg.eigh(cov)
        if eigvals[0] < 0.02 * max(eigvals[2], 1e-6):
            u_axis, v_axis = eigvecs[:, 2], eigvecs[:, 1]
            proj_u, proj_v = centered @ u_axis, centered @ v_axis
            u_min, u_max = float(proj_u.min()), float(proj_u.max())
            v_min, v_max = float(proj_v.min()), float(proj_v.max())
            row_step = pitch * math.sqrt(3.0) * 0.5
            grid_pts = []
            row_idx = 0
            v_val = v_min
            while v_val <= v_max + row_step:
                u_off = 0.5 * pitch if (row_idx % 2 == 1) else 0.0
                u_val = u_min - u_off
                while u_val <= u_max + pitch:
                    p3 = mean_pt + u_val * u_axis + v_val * v_axis
                    dist, snap_idx = pt_tree.query(p3, k=1)
                    if dist <= 0.65 * pitch:
                        grid_pts.append(surf_pts[snap_idx])
                    u_val += pitch
                v_val += row_step
                row_idx += 1
            if len(grid_pts) >= 4:
                return np.asarray(grid_pts, dtype=np.float64), surf_pts

    min_r = pitch * (0.88 if pattern == "hexagonal" else 0.84)
    seeds = _poisson_disk_sample(surf_pts, min_radius=min_r, seed=42)
    if len(seeds) < 3:
        return seeds, surf_pts

    iters = 35 if pattern == "hexagonal" else 2
    return _fast_lloyd_cvt(surf_pts, seeds, iters=iters), surf_pts


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


def clip_voronoi_cell_2d(
    planes: List[CellHalfPlane],
    z_k: float,
    inset: float,
    bbox: Tuple[float, float, float, float],
) -> Optional[Polygon]:
    """Clip a 3D Voronoi cell by all neighbor half-planes at slice height ``z_k`` using Sutherland-Hodgman."""
    x0, y0, x1, y1 = bbox
    poly: List[Tuple[float, float]] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    for hp in planes:
        c = hp.dot_m - inset - hp.uz * z_k
        a, b = hp.ux, hp.uy

        if a * a + b * b < 1e-12:
            if c < 0.0:
                return None
            continue

        out: List[Tuple[float, float]] = []
        p_prev = poly[-1]
        d_prev = a * p_prev[0] + b * p_prev[1] - c
        in_prev = d_prev <= 0.0

        for p_cur in poly:
            d_cur = a * p_cur[0] + b * p_cur[1] - c
            in_cur = d_cur <= 0.0

            if in_cur:
                if not in_prev:
                    t = d_prev / (d_prev - d_cur)
                    out.append(
                        (
                            p_prev[0] + t * (p_cur[0] - p_prev[0]),
                            p_prev[1] + t * (p_cur[1] - p_prev[1]),
                        )
                    )
                out.append(p_cur)
            elif in_prev:
                t = d_prev / (d_prev - d_cur)
                out.append(
                    (
                        p_prev[0] + t * (p_cur[0] - p_prev[0]),
                        p_prev[1] + t * (p_cur[1] - p_prev[1]),
                    )
                )

            p_prev = p_cur
            d_prev = d_cur
            in_prev = in_cur

        if len(out) < 3:
            return None
        poly = out

    p_geom = Polygon(poly)
    if p_geom.is_empty or p_geom.area < 1e-4:
        return None
    if not p_geom.is_valid:
        p_geom = shapely.make_valid(p_geom)
    return p_geom


def generate_normal_dendritic_infill(
    field: VolumetricField, cfg: SlicerConfig
) -> NormalVoronoiLattice:
    """
    Stage 3: Build the 3D outer-surface Voronoi / Hexagonal seed field and precompute
    exact 3D bisector half-planes, applying the shallow-face flattening rule:
        If plane tilt from horizontal arccos(|u_z|) < flatten_angle_deg -> flatten to 0 deg
        anchored at the outer-wall height z_anchor.
    """
    icfg = cfg.infill
    pitch = max(2.5, icfg.target_cell_pitch)

    seeds, surf_pts = _generate_outer_surface_seeds(field.mesh, icfg.pattern, pitch)
    n_surface_seeds = len(seeds)

    # Optional deep isotropic 3D core seeds in full_to_medial mode when max_normal_depth is set
    if icfg.mode == "full_to_medial" and icfg.max_normal_depth is not None:
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
        return NormalVoronoiLattice([], seeds, [], np.zeros((0, 2)))

    surf_tree = cKDTree(surf_pts) if len(surf_pts) > 0 else None
    flatten_deg = max(0.0, float(icfg.flatten_angle_deg))

    k_nbrs = min(n_seeds, 28)
    tree = cKDTree(seeds)
    dists, nbr_indices = tree.query(seeds, k=k_nbrs)
    if k_nbrs == 1:
        dists = dists[:, None]
        nbr_indices = nbr_indices[:, None]

    cell_planes: List[List[CellHalfPlane]] = []
    z_bounds = np.zeros((n_seeds, 2), dtype=np.float64)
    mesh_zmin = float(field.mesh.bounds[0, 2])
    mesh_zmax = float(field.mesh.bounds[1, 2])

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

            # Angle of the bisector plane relative to the horizontal XY plane (0 deg = flat, 90 deg = vertical)
            tilt_from_horiz_deg = math.degrees(math.acos(min(1.0, abs(float(u[2])))))

            if flatten_deg > 0.0 and tilt_from_horiz_deg < (flatten_deg - 1e-3):
                # Snap midpoint symmetrically onto outer wall so the exterior cell pattern is preserved
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

        # Put flattened horizontal planes FIRST so out-of-range slices exit in O(1)
        planes_i.sort(key=lambda hp: (0 if hp.flattened else 1, -abs(hp.uz), hp.dist))
        cell_planes.append(planes_i)
        z_bounds[i, 0] = max(mesh_zmin - 1.0, min(z_lo, si[2] - max_r))
        z_bounds[i, 1] = min(mesh_zmax + 1.0, max(z_hi, si[2] + max_r))

    capsules = _build_voronoi_surface_wireframe(
        field, seeds[:n_surface_seeds], pitch, icfg.strut_radius
    )
    return NormalVoronoiLattice(capsules, seeds, cell_planes, z_bounds)
