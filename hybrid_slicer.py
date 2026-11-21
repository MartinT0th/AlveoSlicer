#!/usr/bin/env python3
"""
Hybrid FFF + In-Situ Photopolymer UV Slicer (Exact Vector Engine, PrusaSlicer 2.9.6 Compatible)

Pipeline:
  1. Exact analytical mesh-plane slicing (vector outer/inner walls, zero rasterization)
  2. Exact 3D spherical vector shell offsetting (HollowShell w/ dry-core support OR FullToMedialAxis)
  3. 3D outer-surface Voronoi / Hexagonal dual-wall structure:
       - Exact analytical 2D half-plane slicing of polygonal empty cells + thin formwork walls
       - Shallow face flattening rule: any cell face tilted < flatten_angle_deg from horizontal
         is converted to a flat 0-deg horizontal bridge anchored at its outer-wall height
       - Single continuous interconnected photopolymer channel network between all cells
  4. Global Dynamic Programming cure-cycle scheduler ([x_min, x_max], airlock & UV shadow checks)
  5. 3D geodesic fluid-basin volume attribution (dosing strictly inside the continuous resin cavity)
  6. Hardware Abstraction Layer emitting PrusaSlicer 2.9.6 G-code Viewer compatible output
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, unary_union
from skimage import measure


# =============================================================================
# Configuration & Printer Profiles (JSON-Serializable, UI Drop-In Compatible)
# =============================================================================

@dataclass
class FFFSettings:
    layer_height: float = 0.25             # mm (h)
    nozzle_diameter: float = 0.40          # mm
    line_width: float = 0.45               # mm
    filament_diameter: float = 1.75        # mm
    flow_multiplier: float = 1.0
    perimeters: int = 2                    # Watertight outer skin perimeters
    bottom_solid_layers: int = 3
    top_solid_layers: int = 3
    print_speed_mm_s: float = 45.0
    travel_speed_mm_s: float = 150.0
    z_hop_mm: float = 0.6
    retract_length_mm: float = 1.2
    retract_speed_mm_s: float = 35.0
    nozzle_temp_c: int = 210
    bed_temp_c: int = 60
    part_cooling_fan_pct: int = 100
    dry_support_spacing_mm: float = 4.5    # Sparse dry FFF support under hollow inner roofs


@dataclass
class InfillSettings:
    pattern: str = "hexagonal"             # "voronoi" or "hexagonal"
    mode: str = "hollow_shell"             # "hollow_shell" or "full_to_medial"
    hollow_wall_thickness: float = 6.0     # mm (clamped <= max_bridge_span)
    inner_skin_thickness: float = 0.90     # mm watertight inner skin in hollow_shell mode
    max_bridge_span: float = 8.0           # mm upper limit for horizontal bridges
    max_normal_depth: Optional[float] = None  # Optional isotropic deep core (full_to_medial)
    target_cell_pitch: float = 10.0        # mm center-to-center cell spacing
    strut_radius: float = 0.45             # Inside-perimeter wall thickness = 2 * strut_radius
    min_channel_clearance: float = 2.0     # mm continuous photopolymer channel width (w_resin)
    flatten_angle_deg: float = 30.0        # Flatten cell faces tilted < this angle (deg) to 0 deg
    max_divergence_ratio: float = 1.6      # Retained for config compatibility
    smin_blend_radius: float = 0.25        # mm optional corner fillet on cell polygons (0 = crisp)
    medial_buffer_ratio: float = 0.90      # Medial-axis detection threshold
    shock_cos_threshold: float = 0.45      # Retained for config compatibility


@dataclass
class ResinSettings:
    x_min_layers: int = 3                  # Preferred min layers between injections
    x_max_layers: int = 6                  # Max layers between injections
    max_cure_depth_mm: float = 1.40        # Beer-Lambert C_d (mm)
    cure_depth_safety_eta: float = 0.85    # Safety multiplier [0.7, 0.9]
    headspace_margin_mm: float = 0.15      # Meniscus clearance below top FFF layer
    shrinkage_factor: float = 1.04         # Volumetric curing shrinkage compensation
    needle_outer_radius_mm: float = 0.45   # Outer radius of dispensing needle
    needle_safety_clear_mm: float = 0.25   # Wall collision margin
    pump_mm3_per_e_mm: float = 1.50        # Calibrated mm^3 dispensed per 1.0 mm pump E-move
    dispense_speed_e_mm_s: float = 4.0     # Pump stepper feedrate
    suckback_e_mm: float = 0.40            # Pump retraction to snap droplet
    suckback_speed_e_mm_s: float = 12.0
    max_flow_radius_mm: float = 10.0       # Subdivide long channels beyond this radius
    settle_dwell_ms: int = 2500            # Viscosity leveling pause before UV sweep
    uv_shadow_bleed_mm: float = 0.25       # Oblique LED cone / scatter bleed margin
    max_shadow_vol_ratio: float = 0.04     # Hard reject slab if shadowed void > 4%
    dp_w_cycle: float = 10.0               # Base time cost per inject+UV cycle
    dp_w_short: float = 45.0               # Quadratic penalty for intervals < x_min
    dp_w_shadow: float = 150.0             # Soft penalty per mm^3 of shadowed void


@dataclass
class UVSettings:
    irradiance_mw_cm2: float = 25.0
    beam_width_cm: float = 1.5
    target_dose_mj_cm2_per_mm: float = 80.0
    min_passes: int = 2
    inter_pass_cooldown_ms: int = 1000
    focal_z_clearance_mm: float = 15.0
    min_scan_speed_mm_s: float = 2.0
    max_scan_speed_mm_s: float = 60.0
    uv_on_gcode: str = "M106 P2 S255"
    uv_off_gcode: str = "M106 P2 S0"


@dataclass
class PrinterProfile:
    printer_type: str = "bedslinger_prototype"  # "bedslinger_prototype" or "corexy_toolchanger"
    bed_size_x: float = 220.0
    bed_size_y: float = 220.0
    bed_size_z: float = 220.0
    needle_offset_x: float = 28.0
    needle_offset_y: float = 0.0
    needle_offset_z: float = 1.0
    x_uv_shield_park: float = 0.0
    uv_strip_y_offset: float = 110.0
    tool_fff: str = "T0"
    tool_resin: str = "T1"
    tool_uv: str = "T2"
    resin_extruder_axis: str = "E"


@dataclass
class SlicerConfig:
    voxel_pitch_mm: float = 0.50           # Used ONLY for 3D surface preview & deep-core seeding
    slice_raster_pitch_mm: float = 0.25    # Used ONLY for DP bitmasks, basin BFS & 2D UI preview
    step_tol_linear: float = 0.015         # Fine OpenCASCADE STEP linear tolerance (mm)
    step_tol_angular: float = 0.05         # ~3 degrees in radians for smooth CAD curves
    fff: FFFSettings = field(default_factory=FFFSettings)
    infill: InfillSettings = field(default_factory=InfillSettings)
    resin: ResinSettings = field(default_factory=ResinSettings)
    uv: UVSettings = field(default_factory=UVSettings)
    printer: PrinterProfile = field(default_factory=PrinterProfile)

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "SlicerConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            voxel_pitch_mm=raw.get("voxel_pitch_mm", 0.50),
            slice_raster_pitch_mm=raw.get("slice_raster_pitch_mm", 0.25),
            step_tol_linear=raw.get("step_tol_linear", 0.015),
            step_tol_angular=raw.get("step_tol_angular", 0.05),
            fff=FFFSettings(**raw.get("fff", {})),
            infill=InfillSettings(**raw.get("infill", {})),
            resin=ResinSettings(**raw.get("resin", {})),
            uv=UVSettings(**raw.get("uv", {})),
            printer=PrinterProfile(**raw.get("printer", {})),
        )


# =============================================================================
# Stage 1: Multi-Format Ingestion & Lightweight Surface Volumetric Field
# =============================================================================

def load_and_center_mesh(filepath: Path, cfg: SlicerConfig) -> trimesh.Trimesh:
    ext = filepath.suffix.lower()
    kwargs = {}
    if ext in {".step", ".stp"}:
        kwargs = {
            "tol_linear": cfg.step_tol_linear,
            "tol_angular": cfg.step_tol_angular,
        }
    try:
        loaded = trimesh.load(str(filepath), **kwargs)
    except Exception as exc:
        if ext in {".step", ".stp"}:
            raise RuntimeError(
                f"Failed to load STEP file '{filepath}'. Install cascadio (`pip install cascadio`). "
                f"Original error: {exc}"
            ) from exc
        raise

    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError(f"No triangle meshes found in '{filepath}'.")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise TypeError(f"Unsupported geometry type: {type(loaded)}")

    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_normals(mesh)

    bounds = mesh.bounds
    center_xy = 0.5 * (bounds[0, :2] + bounds[1, :2])
    shift = np.array([
        cfg.printer.bed_size_x * 0.5 - center_xy[0],
        cfg.printer.bed_size_y * 0.5 - center_xy[1],
        -bounds[0, 2],
    ])
    mesh.apply_translation(shift)
    return mesh


def create_test_curved_wall_mesh(cfg: SlicerConfig) -> trimesh.Trimesh:
    """Creates a wavy 60x25x5 mm curved shell matching the physical test print."""
    nu = 64
    length, height, thick = 60.0, 25.0, 5.0
    amp = 4.5
    us = np.linspace(-0.5 * length, 0.5 * length, nu)
    yc = amp * np.sin(2.0 * np.pi * us / length)
    dydx = amp * (2.0 * np.pi / length) * np.cos(2.0 * np.pi * us / length)
    norm_len = np.sqrt(1.0 + dydx * dydx)
    nx = -dydx / norm_len
    ny = 1.0 / norm_len

    front = np.column_stack([us + 0.5 * thick * nx, yc + 0.5 * thick * ny])
    back = np.column_stack([us - 0.5 * thick * nx, yc - 0.5 * thick * ny])[::-1]
    poly = Polygon(np.vstack([front, back]))
    mesh = trimesh.creation.extrude_polygon(poly, height=height)
    bounds = mesh.bounds
    center_xy = 0.5 * (bounds[0, :2] + bounds[1, :2])
    mesh.apply_translation([
        cfg.printer.bed_size_x * 0.5 - center_xy[0],
        cfg.printer.bed_size_y * 0.5 - center_xy[1],
        -bounds[0, 2],
    ])
    return mesh


class VolumetricField:
    """Lightweight 3D distance field for outer-surface wireframe & deep-core seeding."""

    def __init__(self, mesh: trimesh.Trimesh, pitch: float):
        self.mesh = mesh
        self.pitch = pitch
        vg = mesh.voxelized(pitch=pitch).fill()
        mat = vg.matrix.astype(bool)

        pad = 3
        self.vol = np.pad(mat, pad_width=pad, mode="constant", constant_values=False)
        self.origin = vg.transform[:3, 3] - pad * pitch
        self.shape = self.vol.shape

        self.dist_in = ndimage.distance_transform_edt(self.vol) * pitch
        dist_out = ndimage.distance_transform_edt(~self.vol) * pitch
        self.sdf = dist_out - self.dist_in


# =============================================================================
# Stage 2 & 3: 3D Outer-Surface Voronoi / Hexagonal Pattern + Flat-Face Rule
# =============================================================================

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
    Subclass of `list[Capsule]` so `hybrid_slicer_ui.py` displays the true 3D Voronoi/Hexagonal
    cell wireframe while `slice_model_to_layers` slices exact 3D half-planes.
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
    Returns `(seeds, surf_pts)` distributed strictly on the 3D outer surface of the model:
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
                nv = 10.0 * (4 ** sub) + 2.0
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

    # General 3D Poisson-disk seeding on the outer surface
    rng = np.random.default_rng(42)
    min_r = pitch * (0.88 if pattern == "hexagonal" else 0.84)
    min_r_sq = min_r * min_r
    order = rng.permutation(len(surf_pts))
    chosen: List[np.ndarray] = []

    for idx in order:
        p = surf_pts[idx]
        if not chosen:
            chosen.append(p)
            continue
        diffs = np.asarray(chosen) - p
        if np.min(np.einsum("ij,ij->i", diffs, diffs)) >= min_r_sq:
            chosen.append(p)

    seeds = np.asarray(chosen, dtype=np.float64)
    if len(seeds) < 3:
        return seeds, surf_pts

    iters = 35 if pattern == "hexagonal" else 2
    return _fast_lloyd_cvt(surf_pts, seeds, iters=iters), surf_pts


def _build_voronoi_surface_wireframe(
    field: VolumetricField, seeds: np.ndarray, pitch: float, strut_radius: float
) -> List[Capsule]:
    """Extracts true 3D Voronoi / Hexagonal cell boundary polygons on the outer surface for 3D UI preview."""
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

    for _pair, nids in pair_to_nids.items():
        if len(nids) == 2:
            u, v = min(nids[0], nids[1]), max(nids[0], nids[1])
            if u != v and (u, v) not in seen and np.linalg.norm(nodes_arr[u] - nodes_arr[v]) <= max_edge:
                seen.add((u, v))
                a, b = nodes_arr[u], nodes_arr[v]
                capsules.append(Capsule(a=a, b=b, radius=strut_radius, z_min=float(min(a[2], b[2])), z_max=float(max(a[2], b[2]))))
        elif len(nids) > 2:
            sub = nodes_arr[nids]
            sub_tree = cKDTree(sub)
            for i_local, u in enumerate(nids):
                _, nbrs = sub_tree.query(sub[i_local], k=min(3, len(nids)))
                for j_local in np.atleast_1d(nbrs)[1:]:
                    v = nids[int(j_local)]
                    key = (min(u, v), max(u, v))
                    if u != v and key not in seen and np.linalg.norm(nodes_arr[u] - nodes_arr[v]) <= max_edge:
                        seen.add(key)
                        a, b = nodes_arr[u], nodes_arr[v]
                        capsules.append(Capsule(a=a, b=b, radius=strut_radius, z_min=float(min(a[2], b[2])), z_max=float(max(a[2], b[2]))))

    return capsules


def generate_normal_dendritic_infill(
    field: VolumetricField, cfg: SlicerConfig
) -> NormalVoronoiLattice:
    """
    Stage 3: Builds the 3D outer-surface Voronoi / Hexagonal seed field and precomputes
    exact 3D bisector half-planes, applying the shallow-face flattening rule:
        If plane tilt from horizontal arccos(|u_z|) < flatten_angle_deg -> flatten to 0 deg
        anchored at the outer-wall height z_anchor!
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
            rng = np.random.default_rng(7)
            min_r_sq = (pitch * 0.85) ** 2
            chosen_core: List[np.ndarray] = []
            for idx in rng.permutation(len(core_pts)):
                p = core_pts[idx]
                if not chosen_core:
                    chosen_core.append(p)
                    continue
                diffs = np.asarray(chosen_core) - p
                if np.min(np.einsum("ij,ij->i", diffs, diffs)) >= min_r_sq:
                    chosen_core.append(p)
            if chosen_core:
                core_arr = np.asarray(chosen_core, dtype=np.float64)
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
                # Snap midpoint symmetrically onto outer wall so the hexagon pattern on the outside is preserved
                if surf_tree is not None and i < n_surface_seeds and j < n_surface_seeds:
                    _, snap_idx = surf_tree.query(m, k=1)
                    z_anchor = float(surf_pts[snap_idx, 2])
                    # Keep z_anchor safely between the two seeds' Z heights
                    z_anchor = max(min(float(si[2]), float(sj[2])), min(max(float(si[2]), float(sj[2])), z_anchor))
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

    capsules = _build_voronoi_surface_wireframe(field, seeds[:n_surface_seeds], pitch, icfg.strut_radius)
    return NormalVoronoiLattice(capsules, seeds, cell_planes, z_bounds)


# =============================================================================
# Exact Analytical Mesh-Plane Slicing & 2D Half-Plane Clipping
# =============================================================================

def _extract_polylines_from_geom(geom) -> List[np.ndarray]:
    lines: List[np.ndarray] = []
    if geom is None or geom.is_empty:
        return lines
    if isinstance(geom, LineString):
        coords = np.asarray(geom.coords)
        if len(coords) >= 2:
            lines.append(coords)
    elif isinstance(geom, MultiLineString):
        for g in geom.geoms:
            coords = np.asarray(g.coords)
            if len(coords) >= 2:
                lines.append(coords)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            lines.extend(_extract_polylines_from_geom(g))
    return lines


def _iter_polygons(geom: shapely.Geometry) -> List[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 1e-5 else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > 1e-5]
    if hasattr(geom, "geoms"):
        out: List[Polygon] = []
        for g in geom.geoms:
            out.extend(_iter_polygons(g))
        return out
    return []


def _slice_mesh_plane_exact(
    tri_verts: np.ndarray, tri_zmin: np.ndarray, tri_zmax: np.ndarray, z_val: float
) -> shapely.Geometry:
    z = z_val + 1.13e-7
    hit = (tri_zmin < z) & (tri_zmax > z)
    if not np.any(hit):
        return Polygon()

    T = tri_verts[hit]
    above = T[:, :, 2] > z
    n_above = above.sum(axis=1)
    valid = (n_above == 1) | (n_above == 2)
    if not np.any(valid):
        return Polygon()

    T = T[valid]
    above = above[valid]
    n_above = n_above[valid]

    is_lone = np.where(n_above[:, None] == 1, above, ~above)
    i0 = np.argmax(is_lone, axis=1)
    i1 = (i0 + 1) % 3
    i2 = (i0 + 2) % 3
    rows = np.arange(len(T))

    p0 = T[rows, i0]
    p1 = T[rows, i1]
    p2 = T[rows, i2]

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


def _clip_voronoi_cell_2d(
    planes: List[CellHalfPlane],
    z_k: float,
    inset: float,
    bbox: Tuple[float, float, float, float],
) -> Optional[Polygon]:
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
                    out.append((p_prev[0] + t * (p_cur[0] - p_prev[0]),
                                p_prev[1] + t * (p_cur[1] - p_prev[1])))
                out.append(p_cur)
            elif in_prev:
                t = d_prev / (d_prev - d_cur)
                out.append((p_prev[0] + t * (p_cur[0] - p_prev[0]),
                            p_prev[1] + t * (p_cur[1] - p_prev[1])))

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


def _erode_3d_spherical_vector(
    outer_polys: List[shapely.Geometry], k: int, radius_mm: float, h: float
) -> shapely.Geometry:
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


def _rasterize_geom(geom: shapely.Geometry, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    if geom is None or geom.is_empty:
        return np.zeros(gx.shape, dtype=bool)
    return shapely.contains_xy(geom, gx, gy)


def _disk_kernel(radius_px: int) -> np.ndarray:
    r = max(1, radius_px)
    yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= (r * r)


def _filter_anchored_inside_perimeters(
    raw_lines: List[np.ndarray],
    anchor_boundary: shapely.Geometry,
    min_open_len: float,
    anchor_tol: float = 0.08,
) -> List[np.ndarray]:
    """Keeps closed loops and open wall-to-wall spans whose both endpoints anchor into the walls."""
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


@dataclass
class SlicedLayer:
    layer_idx: int                # 1-indexed
    z: float
    # Internal masks for Stage 4 (DP), Stage 5 (Basin BFS), and 2D UI inspector
    solid_mask: np.ndarray        # (nx, ny) bool: shell + inside perimeters + cell caps
    shell_mask: np.ndarray        # (nx, ny) bool: outer/inner watertight skins & solid skins
    infill_mask: np.ndarray       # (nx, ny) bool: inside perimeters & cell caps (#11)
    dry_support_mask: np.ndarray  # (nx, ny) bool: dry FFF support under hollow inner roofs
    void_mask: np.ndarray         # (nx, ny) bool: continuous photopolymer channel (#10)
    accessible_mask: np.ndarray   # (nx, ny) bool: needle-accessible photopolymer zone
    # Exact floating-point vector geometries used for G-code generation (ZERO rasterization!)
    shell_geom: shapely.Geometry = field(default_factory=Polygon)
    cell_wall_geom: shapely.Geometry = field(default_factory=Polygon)
    inside_perimeter_lines: List[np.ndarray] = field(default_factory=list)
    cell_cap_geom: shapely.Geometry = field(default_factory=Polygon)
    empty_cell_geom: shapely.Geometry = field(default_factory=Polygon)
    resin_void_geom: shapely.Geometry = field(default_factory=Polygon)
    dry_support_lines: List[np.ndarray] = field(default_factory=list)


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
    needle_disk = _disk_kernel(needle_erode_px)

    # 1. Exact vector mesh slicing for all layers
    tri_verts = np.asarray(mesh.triangles, dtype=np.float64)
    tri_zmin = tri_verts[:, :, 2].min(axis=1)
    tri_zmax = tri_verts[:, :, 2].max(axis=1)

    outer_polys: List[shapely.Geometry] = []
    for k in range(num_layers):
        z_mid = (k + 0.5) * h
        outer_polys.append(_slice_mesh_plane_exact(tri_verts, tri_zmin, tri_zmax, z_mid))

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
            p_in_outer = p_in_outer.intersection(outer_polys[k - b_bot]).intersection(outer_polys[k + b_top])

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
    dry_support_lines_all: List[List[np.ndarray]] = [[] for _ in range(num_layers)]
    dry_support_polys_all: List[shapely.Geometry] = [Polygon() for _ in range(num_layers)]
    if cfg.infill.mode == "hollow_shell":
        support_col: shapely.Geometry = Polygon()
        spacing = max(1.5, cfg.fff.dry_support_spacing_mm)
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
                    lines_k.extend(_extract_polylines_from_geom(inter))
                    gx_v += spacing
                gy_v = math.floor(by0 / spacing) * spacing
                while gy_v <= by1:
                    inter = support_col.intersection(LineString([(bx0 - 1.0, gy_v), (bx1 + 1.0, gy_v)]))
                    lines_k.extend(_extract_polylines_from_geom(inter))
                    gy_v += spacing
                dry_support_lines_all[k] = lines_k
                if lines_k:
                    dry_support_polys_all[k] = unary_union([
                        LineString(ln).buffer(0.5 * lw, cap_style=2) for ln in lines_k
                    ])

    # 4. Exact analytical half-plane slicing of Voronoi/Hexagonal empty cells & flat bridge caps
    has_lattice = isinstance(capsules, NormalVoronoiLattice) and len(capsules.seeds) > 0
    layers: List[SlicedLayer] = []

    for k in range(num_layers):
        z_mid = (k + 0.5) * h
        p_mod = outer_polys[k]
        p_comp = comp_geoms[k]
        p_shell = shell_geoms[k]
        p_dry = dry_core_geoms[k]

        cell_outer_list: List[shapely.Geometry] = []
        cell_air_list: List[shapely.Geometry] = []
        cell_cap_list: List[shapely.Geometry] = []
        inside_perims_k: List[np.ndarray] = []

        if has_lattice and not p_comp.is_empty and p_comp.area > 0.1:
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
            z_bnds = capsules.seed_z_bounds
            active_seeds = np.flatnonzero((z_bnds[:, 0] <= z_mid) & (z_bnds[:, 1] >= z_mid))

            for sid in active_seeds:
                planes = capsules.cell_planes[int(sid)]
                raw_outer = _clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin, bbox)
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

                # Compute empty air interior (3D plane inset + 2D horizontal printability inset)
                raw_air = _clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + t_cell_wall, bbox)
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
                else:
                    # Open empty cell layer: emit anchored inside perimeters separating resin & air
                    cell_lines_raw: List[np.ndarray] = []
                    for p_i in range(n_inside_perims):
                        d_off = (p_i + 0.5) * lw
                        loop_3d = _clip_voronoi_cell_2d(planes, z_mid, 0.5 * w_resin + d_off, bbox)
                        if loop_3d is None:
                            continue
                        loop_2d = raw_outer.buffer(-d_off, join_style=2)
                        loop_poly = loop_3d.intersection(loop_2d) if not loop_2d.is_empty else Polygon()
                        if loop_poly.is_empty or loop_poly.area < 0.02:
                            continue
                        clipped_arcs = loop_poly.boundary.intersection(p_comp_anchor)
                        if isinstance(clipped_arcs, MultiLineString):
                            clipped_arcs = linemerge(clipped_arcs)
                        cell_lines_raw.extend(_extract_polylines_from_geom(clipped_arcs))

                    anchored_lines = _filter_anchored_inside_perimeters(
                        cell_lines_raw, anchor_boundary, min_open_len=1.5 * lw
                    )
                    if not anchored_lines:
                        continue

                    inside_perims_k.extend(anchored_lines)
                    cell_outer_list.append(clipped_outer)
                    cell_air_list.append(clipped_air)

                    # Fill any wider sloped wall zone between anchored inside perimeters & empty air
                    if raw_air is not None and not raw_air.is_empty:
                        wall_strip = clipped_outer_anchor.difference(raw_air)
                        covered = unary_union([
                            LineString(ln).buffer(0.60 * lw, cap_style=2) for ln in anchored_lines
                        ])
                        extra_ramp = wall_strip.difference(covered)
                        if not extra_ramp.is_empty and extra_ramp.area > lw * lw:
                            cell_cap_list.append(extra_ramp)

        all_cells_outer = unary_union(cell_outer_list) if cell_outer_list else Polygon()
        empty_cells_geom = unary_union(cell_air_list) if cell_air_list else Polygon()
        cell_cap_geom = unary_union(cell_cap_list) if cell_cap_list else Polygon()

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
        shell_m = _rasterize_geom(p_shell, gx, gy)
        infill_m = _rasterize_geom(cell_wall_geom, gx, gy) & (~shell_m)
        solid_m = shell_m | infill_m
        dry_m = _rasterize_geom(dry_support_polys_all[k], gx, gy) & (~solid_m)
        void_m = _rasterize_geom(resin_void_geom, gx, gy) & (~solid_m)
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


# =============================================================================
# Stage 4: Global Dynamic Programming Cure-Cycle Scheduler
# =============================================================================

@dataclass
class CureCycle:
    start_layer: int
    end_layer: int
    slab_thickness_mm: float
    shadowed_vol_mm3: float
    trigger: str


def _slab_3d_needle_accessible(layers: List[SlicedLayer], start_idx0: int, end_idx0: int) -> bool:
    top_void = layers[end_idx0].void_mask
    top_acc = layers[end_idx0].accessible_mask
    if not np.any(top_void):
        return True

    lbl_2d, n_2d = ndimage.label(top_void)
    all_2d_ok = True
    for i in range(1, n_2d + 1):
        if not np.any(top_acc[lbl_2d == i]):
            all_2d_ok = False
            break
    if all_2d_ok:
        return True

    slab = np.stack([layers[i].void_mask for i in range(start_idx0, end_idx0 + 1)], axis=0)
    lbl_3d, n_3d = ndimage.label(slab)
    top_slice_lbl = lbl_3d[-1]
    for cid in range(1, n_3d + 1):
        if not np.any(top_acc[top_slice_lbl == cid]):
            return False
    return True


def compute_optimal_cure_schedule(
    layers: List[SlicedLayer], cfg: SlicerConfig
) -> List[CureCycle]:
    rcfg = cfg.resin
    h = cfg.fff.layer_height
    rp = cfg.slice_raster_pitch_mm
    px_vol_mm3 = rp * rp * h
    K = len(layers)

    x_max_beer = int(math.floor((rcfg.max_cure_depth_mm * rcfg.cure_depth_safety_eta) / h))
    x_max_eff = max(1, min(rcfg.x_max_layers, x_max_beer))

    bleed_px = int(round(rcfg.uv_shadow_bleed_mm / rp))
    bleed_disk = _disk_kernel(bleed_px) if bleed_px > 0 else None

    INF = float("inf")
    dp_cost = [INF] * (K + 1)
    dp_prev = [-1] * (K + 1)
    dp_shadow = [0.0] * (K + 1)
    dp_trigger = ["none"] * (K + 1)
    dp_cost[0] = 0.0

    void_counts = [int(np.count_nonzero(ly.void_mask)) for ly in layers]

    for k in range(1, K + 1):
        ly_k = layers[k - 1]
        if void_counts[k - 1] == 0:
            dp_cost[k] = dp_cost[k - 1]
            dp_prev[k] = k - 1
            dp_trigger[k] = "none"
            continue

        solid_above = np.zeros_like(ly_k.solid_mask)
        air_vent_reach = ly_k.void_mask.copy()
        cum_shadow_px = 0
        cum_void_px = void_counts[k - 1]

        p_min = max(0, k - x_max_eff)
        for p in range(k - 1, p_min - 1, -1):
            if p < k - 1:
                j_idx0 = p
                if void_counts[j_idx0] == 0:
                    break

                solid_above |= layers[j_idx0 + 1].solid_mask

                vent_seeds = layers[j_idx0].void_mask & air_vent_reach
                air_vent_reach = ndimage.binary_propagation(vent_seeds, mask=layers[j_idx0].void_mask)
                if int(np.count_nonzero(air_vent_reach)) < void_counts[j_idx0]:
                    break

                dark_core = (
                    ndimage.binary_erosion(solid_above, structure=bleed_disk)
                    if bleed_disk is not None
                    else solid_above
                )
                cum_shadow_px += int(np.count_nonzero(layers[j_idx0].void_mask & dark_core))
                cum_void_px += void_counts[j_idx0]

            if dp_cost[p] == INF:
                continue

            if p < k - 1 and not _slab_3d_needle_accessible(layers, p, k - 1):
                break

            shadow_ratio = cum_shadow_px / max(1, cum_void_px)
            if p < k - 1 and shadow_ratio > rcfg.max_shadow_vol_ratio:
                break

            span = k - p
            shortfall = max(0, rcfg.x_min_layers - span)
            shadow_mm3 = cum_shadow_px * px_vol_mm3
            step_cost = (
                rcfg.dp_w_cycle
                + rcfg.dp_w_short * (shortfall * shortfall)
                + rcfg.dp_w_shadow * shadow_mm3
            )
            total_cost = dp_cost[p] + step_cost

            if total_cost < dp_cost[k]:
                trig = "optimal_cadence"
                if k == K:
                    trig = "final_top_layer"
                elif span < rcfg.x_min_layers:
                    trig = "hard_roof_closure"
                dp_cost[k] = total_cost
                dp_prev[k] = p
                dp_shadow[k] = shadow_mm3
                dp_trigger[k] = trig

    schedule: List[CureCycle] = []
    cur = K
    while cur > 0:
        prev = dp_prev[cur]
        if prev < 0:
            raise RuntimeError(f"DP scheduler failed to find feasible cure path at layer {cur}.")
        if dp_trigger[cur] != "none":
            schedule.append(
                CureCycle(
                    start_layer=prev + 1,
                    end_layer=cur,
                    slab_thickness_mm=(cur - prev) * h,
                    shadowed_vol_mm3=dp_shadow[cur],
                    trigger=dp_trigger[cur],
                )
            )
        cur = prev

    schedule.reverse()
    return schedule


# =============================================================================
# Stage 5: 3D Fluid Basin Segmentation & Geodesic Proportional Multi-Point Dosing
# =============================================================================

@dataclass
class DosingPoint:
    x: float
    y: float
    volume_mm3: float
    pump_e_mm: float


def _order_points_tsp(points: List[DosingPoint]) -> List[DosingPoint]:
    n = len(points)
    if n <= 2:
        return points

    coords = np.array([[p.x, p.y] for p in points])
    unvisited = set(range(1, n))
    route = [0]
    while unvisited:
        last = route[-1]
        nxt = min(unvisited, key=lambda i: float(np.sum((coords[last] - coords[i]) ** 2)))
        route.append(nxt)
        unvisited.remove(nxt)

    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = route[i - 1], route[i]
                c, d = route[j], route[j + 1] if j + 1 < n else -1
                if d == -1:
                    continue
                d_cur = np.linalg.norm(coords[a] - coords[b]) + np.linalg.norm(coords[c] - coords[d])
                d_new = np.linalg.norm(coords[a] - coords[c]) + np.linalg.norm(coords[b] - coords[d])
                if d_new + 1e-6 < d_cur:
                    route[i : j + 1] = reversed(route[i : j + 1])
                    improved = True

    return [points[i] for i in route]


def plan_cycle_dosing(
    layers: List[SlicedLayer],
    cycle: CureCycle,
    xs: np.ndarray,
    ys: np.ndarray,
    cfg: SlicerConfig,
) -> Tuple[List[DosingPoint], Tuple[float, float, float, float]]:
    rcfg = cfg.resin
    h = cfg.fff.layer_height
    rp = cfg.slice_raster_pitch_mm
    px_area = rp * rp
    top_h_eff = max(0.0, h - rcfg.headspace_margin_mm)

    slab = np.stack(
        [layers[k - 1].void_mask for k in range(cycle.start_layer, cycle.end_layer + 1)],
        axis=0,
    )
    nz, nx, ny = slab.shape
    top_acc_global = layers[cycle.end_layer - 1].accessible_mask

    lbl_3d, n_basins = ndimage.label(slab)
    dosing_points: List[DosingPoint] = []
    flow_rad_px = rcfg.max_flow_radius_mm / rp

    any_xy = np.any(slab, axis=0)
    xy_idx = np.argwhere(any_xy)
    if len(xy_idx) == 0:
        return [], (0.0, 0.0, 0.0, 0.0)
    bbox_xy = (
        float(xs[xy_idx[:, 0].min()]),
        float(ys[xy_idx[:, 1].min()]),
        float(xs[xy_idx[:, 0].max()]),
        float(ys[xy_idx[:, 1].max()]),
    )

    neighbors_3d = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))

    for bid in range(1, n_basins + 1):
        basin_mask = lbl_3d == bid
        top_open = basin_mask[-1]
        if not np.any(top_open):
            continue

        isl_lbl, n_isl = ndimage.label(top_open)
        seeds_for_bfs: List[Tuple[int, int, int, int]] = []
        seed_coords_mm: List[Tuple[float, float]] = []

        for iid in range(1, n_isl + 1):
            isl_mask = isl_lbl == iid
            valid_entry = isl_mask & top_acc_global
            if not np.any(valid_entry):
                valid_entry = isl_mask

            edt = ndimage.distance_transform_edt(isl_mask) * valid_entry
            if np.max(edt) <= 0.0:
                continue

            remaining = valid_entry.copy()
            while np.any(remaining):
                masked_edt = edt * remaining
                flat_best = int(np.argmax(masked_edt))
                bx, by = divmod(flat_best, ny)
                sid = len(seed_coords_mm)
                seed_coords_mm.append((float(xs[bx]), float(ys[by])))
                seeds_for_bfs.append((nz - 1, bx, by, sid))

                ix_lo = max(0, int(bx - flow_rad_px))
                ix_hi = min(nx, int(bx + flow_rad_px + 1))
                iy_lo = max(0, int(by - flow_rad_px))
                iy_hi = min(ny, int(by + flow_rad_px + 1))
                sub_x, sub_y = np.ogrid[ix_lo:ix_hi, iy_lo:iy_hi]
                covered = (sub_x - bx) ** 2 + (sub_y - by) ** 2 <= flow_rad_px ** 2
                remaining[ix_lo:ix_hi, iy_lo:iy_hi] &= ~covered

        if not seeds_for_bfs:
            continue

        owner = np.full((nz, nx, ny), -1, dtype=np.int32)
        q: deque[Tuple[int, int, int, int]] = deque()
        for sz, sx, sy, sid in seeds_for_bfs:
            owner[sz, sx, sy] = sid
            q.append((sz, sx, sy, sid))

        while q:
            cz, cx, cy, sid = q.popleft()
            for dz, dx, dy in neighbors_3d:
                nz_, nx_, ny_ = cz + dz, cx + dx, cy + dy
                if (
                    0 <= nz_ < nz
                    and 0 <= nx_ < nx
                    and 0 <= ny_ < ny
                    and basin_mask[nz_, nx_, ny_]
                    and owner[nz_, nx_, ny_] == -1
                ):
                    owner[nz_, nx_, ny_] = sid
                    q.append((nz_, nx_, ny_, sid))

        vols = np.zeros(len(seed_coords_mm), dtype=np.float64)
        for z_i in range(nz):
            h_eff = top_h_eff if z_i == nz - 1 else h
            slice_own = owner[z_i]
            valid_own = slice_own[slice_own >= 0]
            if len(valid_own) > 0:
                counts = np.bincount(valid_own, minlength=len(seed_coords_mm))
                vols += counts * (px_area * h_eff)

        vols *= rcfg.shrinkage_factor
        for sid, (px_mm, py_mm) in enumerate(seed_coords_mm):
            if vols[sid] > 1e-4:
                dosing_points.append(
                    DosingPoint(
                        x=px_mm,
                        y=py_mm,
                        volume_mm3=float(vols[sid]),
                        pump_e_mm=float(vols[sid] / rcfg.pump_mm3_per_e_mm),
                    )
                )

    return _order_points_tsp(dosing_points), bbox_xy


# =============================================================================
# Stage 6: Exact Vector FFF Toolpathing & PrusaSlicer 2.9.6 Compatible HAL
# =============================================================================

_PRUSA_TYPE_MAP = {
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


def generate_fff_paths_for_layer(
    layer: SlicedLayer, xs: np.ndarray, ys: np.ndarray, cfg: SlicerConfig
) -> List[Tuple[str, np.ndarray]]:
    lw = cfg.fff.line_width
    paths: List[Tuple[str, np.ndarray]] = []

    def emit_vector_region(
        region: shapely.Geometry, tag: str, num_perims: int, fill_angle_deg: float
    ) -> None:
        if region is None or region.is_empty:
            return

        cur = region.buffer(-0.5 * lw, join_style=2)
        for p_i in range(num_perims):
            if cur.is_empty:
                break
            ext_tag = f"{tag}_EXT_PERIM" if (tag == "SHELL" and p_i == 0) else f"{tag}_PERIM"
            for poly in _iter_polygons(cur):
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
                for coords in _extract_polylines_from_geom(inter):
                    if flip:
                        coords = coords[::-1]
                    unrot = shapely.affinity.rotate(LineString(coords), fill_angle_deg, origin=(0.0, 0.0))
                    paths.append((f"{tag}_FILL", np.asarray(unrot.coords)))
                y_val += lw
                flip = not flip

    angle = 45.0 if (layer.layer_idx % 2 == 0) else -45.0

    # 1. Exact outer & inner watertight shells + top/bottom skins
    emit_vector_region(layer.shell_geom, "SHELL", cfg.fff.perimeters, angle)

    # 2. Anchored Voronoi/Hexagonal inside perimeters separating resin cavity from empty cells
    for ln in layer.inside_perimeter_lines:
        paths.append(("INSIDE_PERIM", ln))

    # 3. Solid flat horizontal floor/ceiling bridge caps & sloped ramp reinforcements
    if not layer.cell_cap_geom.is_empty:
        emit_vector_region(layer.cell_cap_geom, "CELL_CAP", 1, angle + 90.0)

    # 4. Dry-core support lines (HollowShell mode only)
    for ln in layer.dry_support_lines:
        paths.append(("DRY_SUPPORT", ln))

    return paths


def _format_duration_prusa(seconds: float) -> str:
    total_s = max(1, int(round(seconds)))
    days, rem = divmod(total_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if mins or hours or days:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


class GCodeHAL:
    """Hardware Abstraction Layer emitting PrusaSlicer 2.9.6 G-code Viewer compatible output."""

    def __init__(self, cfg: SlicerConfig):
        self.cfg = cfg
        self.lines: List[str] = []
        self.cur_x = 0.0
        self.cur_y = 0.0
        self.cur_z = 0.0

        self.total_e_fff_mm = 0.0
        self.total_e_resin_mm = 0.0
        self.total_resin_mm3 = 0.0
        self.est_time_s = 0.0
        self._active_type: Optional[str] = None
        self._active_width: Optional[float] = None

        h = cfg.fff.layer_height
        w = cfg.fff.line_width
        a_line = (w - h) * h + math.pi * (0.5 * h) ** 2 if w > h else w * h
        a_fil = math.pi * (0.5 * cfg.fff.filament_diameter) ** 2
        self.e_per_mm = cfg.fff.flow_multiplier * (a_line / a_fil)
        self.fil_area_mm2 = a_fil

    def emit(self, cmd: str) -> None:
        self.lines.append(cmd)

    def _set_feature(self, prusa_type: str, width_mm: Optional[float] = None) -> None:
        if prusa_type != self._active_type:
            self.emit(f";TYPE:{prusa_type}")
            self._active_type = prusa_type
        if width_mm is not None and (
            self._active_width is None or abs(width_mm - self._active_width) > 1e-4
        ):
            self.emit(f";WIDTH:{width_mm:.3f}")
            self._active_width = width_mm

    def start_gcode(self) -> None:
        f = self.cfg.fff
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d at %H:%M:%S UTC")
        self.emit(f"; generated by PrusaSlicer 2.9.6 on {ts}")
        self.emit("; Hybrid FFF + In-Situ UV Photopolymer Slicer (Exact Vector Engine)")
        self.emit(f"; Printer Profile: {self.cfg.printer.printer_type}")
        self.emit(
            f"; Infill: {self.cfg.infill.pattern} | Mode: {self.cfg.infill.mode} | "
            f"Resin Channel: {self.cfg.infill.min_channel_clearance:.2f} mm | "
            f"Flatten Angle: <{self.cfg.infill.flatten_angle_deg:.1f} deg"
        )
        self.emit("")
        self.emit(";TYPE:Custom")
        self._active_type = "Custom"
        self.emit("G21 ; set units to millimeters")
        self.emit("G90 ; use absolute coordinates")
        self.emit("M83 ; use relative distances for extrusion")
        self.emit(f"M140 S{f.bed_temp_c} ; set bed temp")
        self.emit(f"M104 S{f.nozzle_temp_c} ; set extruder temp")
        self.emit("G28 ; home all axes")
        self.emit(f"M190 S{f.bed_temp_c} ; wait for bed temp")
        self.emit(f"M109 S{f.nozzle_temp_c} ; wait for extruder temp")
        self.emit(self.cfg.uv.uv_off_gcode + " ; ensure UV LEDs OFF")
        self.emit(f"{self.cfg.printer.tool_fff} ; select FFF extruder")
        self.emit("G92 E0")

    def _emit_prusaslicer_config_footer(self) -> None:
        f = self.cfg.fff
        p = self.cfg.printer
        r = self.cfg.resin
        bx, by, bz = int(round(p.bed_size_x)), int(round(p.bed_size_y)), int(round(p.bed_size_z))

        fil_cm3 = (self.total_e_fff_mm * self.fil_area_mm2) / 1000.0
        fil_g = fil_cm3 * 1.24
        resin_cm3 = self.total_resin_mm3 / 1000.0
        resin_g = resin_cm3 * 1.15

        is_tc = p.printer_type == "corexy_toolchanger"
        n_ext = 3 if is_tc else 2

        def _csv(vals: List[str]) -> str:
            return ",".join(vals)

        def _semi(vals: List[str]) -> str:
            return ";".join(vals)

        if is_tc:
            nozzles = _csv([f"{f.nozzle_diameter:.2f}", f"{r.needle_outer_radius_mm * 2.0:.2f}", "0.40"])
            colors = _semi(["#FF5722", "#00B0FF", "#AA00FF"])
            offsets = _csv(["0x0", "0x0", "0x0"])
            temps = _csv([str(f.nozzle_temp_c), "0", "0"])
            fil_used_mm = f"{self.total_e_fff_mm:.2f}, {self.total_e_resin_mm:.2f}, 0.00"
            fil_used_cm3 = f"{fil_cm3:.2f}, {resin_cm3:.2f}, 0.00"
            fil_used_g = f"{fil_g:.2f}, {resin_g:.2f}, 0.00"
        else:
            nozzles = _csv([f"{f.nozzle_diameter:.2f}", f"{r.needle_outer_radius_mm * 2.0:.2f}"])
            colors = _semi(["#FF5722", "#00B0FF"])
            offsets = _csv(["0x0", f"{p.needle_offset_x:.2f}x{p.needle_offset_y:.2f}"])
            temps = _csv([str(f.nozzle_temp_c), "0"])
            fil_used_mm = f"{self.total_e_fff_mm:.2f}, {self.total_e_resin_mm:.2f}"
            fil_used_cm3 = f"{fil_cm3:.2f}, {resin_cm3:.2f}"
            fil_used_g = f"{fil_g:.2f}, {resin_g:.2f}"

        fil_dias = _csv([f"{f.filament_diameter:.2f}"] * n_ext)
        fil_dens = _csv(["1.24"] * n_ext)
        fil_cost = _csv(["25"] * n_ext)
        fil_types = _semi(["PLA"] * n_ext)
        bed_temps = _csv([str(f.bed_temp_c)] * n_ext)
        ret_lens = _csv([f"{f.retract_length_mm:.2f}"] * n_ext)
        ret_spds = _csv([f"{f.retract_speed_mm_s:.1f}"] * n_ext)
        deret_spds = _csv(["0"] * n_ext)
        ret_lifts = _csv([f"{f.z_hop_mm:.2f}"] * n_ext)
        ret_trav = _csv(["2"] * n_ext)
        ret_lc = _csv(["0"] * n_ext)
        wipes = _csv(["0"] * n_ext)
        min_lh = _csv(["0.07"] * n_ext)
        max_lh = _csv([f"{max(0.30, f.layer_height):.2f}"] * n_ext)
        coolings = _csv(["1"] * n_ext)
        fans_on = _csv(["1"] * n_ext)
        fan_spds = _csv([str(f.part_cooling_fan_pct)] * n_ext)
        zero_vec = _csv(["0"] * n_ext)
        one_vec = _csv(["1"] * n_ext)

        self.emit("")
        self.emit(f"; filament used [mm] = {fil_used_mm}")
        self.emit(f"; filament used [cm3] = {fil_used_cm3}")
        self.emit(f"; filament used [g] = {fil_used_g}")
        self.emit(f"; total filament used [g] = {fil_g + resin_g:.2f}")
        self.emit(f"; photopolymer used [mL] = {resin_cm3:.3f}")
        self.emit(f"; estimated printing time (normal mode) = {_format_duration_prusa(self.est_time_s)}")
        self.emit("")
        self.emit("; prusaslicer_config = begin")
        config_pairs = [
            ("avoid_crossing_perimeters", "0"),
            ("bed_shape", f"0x0,{bx}x0,{bx}x{by},0x{by}"),
            ("bed_temperature", bed_temps),
            ("bottom_fill_pattern", "monotonic"),
            ("bottom_solid_layers", str(f.bottom_solid_layers)),
            ("bottom_solid_min_thickness", "0.5"),
            ("bridge_acceleration", "1000"),
            ("bridge_angle", "0"),
            ("bridge_fan_speed", fan_spds),
            ("bridge_flow_ratio", "1"),
            ("bridge_speed", "25"),
            ("brim_separation", "0.1"),
            ("brim_type", "no_brim"),
            ("brim_width", "0"),
            ("complete_objects", "0"),
            ("cooling", coolings),
            ("default_acceleration", "1000"),
            ("deretract_speed", deret_spds),
            ("disable_fan_first_layers", one_vec),
            ("dont_support_bridges", "1"),
            ("draft_shield", "disabled"),
            ("elefant_foot_compensation", "0"),
            ("external_perimeter_acceleration", "800"),
            ("external_perimeter_extrusion_width", f"{f.line_width:.3f}"),
            ("external_perimeter_speed", f"{f.print_speed_mm_s:.1f}"),
            ("external_perimeters_first", "0"),
            ("extra_perimeters", "1"),
            ("extruder_clearance_height", "20"),
            ("extruder_clearance_radius", "20"),
            ("extruder_colour", colors),
            ("extruder_offset", offsets),
            ("extrusion_axis", "E"),
            ("extrusion_multiplier", _csv([f"{f.flow_multiplier:.2f}"] * n_ext)),
            ("extrusion_width", f"{f.line_width:.3f}"),
            ("fan_always_on", fans_on),
            ("fan_below_layer_time", _csv(["60"] * n_ext)),
            ("filament_colour", colors),
            ("filament_cost", fil_cost),
            ("filament_density", fil_dens),
            ("filament_diameter", fil_dias),
            ("filament_max_volumetric_speed", zero_vec),
            ("filament_type", fil_types),
            ("fill_angle", "45"),
            ("fill_density", "20%"),
            ("fill_pattern", "grid"),
            ("first_layer_acceleration", "500"),
            ("first_layer_bed_temperature", bed_temps),
            ("first_layer_extrusion_width", f"{f.line_width:.3f}"),
            ("first_layer_height", f"{f.layer_height:.3f}"),
            ("first_layer_speed", f"{min(25.0, f.print_speed_mm_s):.1f}"),
            ("first_layer_temperature", temps),
            ("gap_fill_enabled", "1"),
            ("gap_fill_speed", "20"),
            ("gcode_flavor", "marlin2"),
            ("infill_acceleration", "1000"),
            ("infill_every_layers", "1"),
            ("infill_extruder", "1"),
            ("infill_extrusion_width", f"{f.line_width:.3f}"),
            ("infill_first", "0"),
            ("infill_overlap", "25%"),
            ("infill_speed", f"{f.print_speed_mm_s:.1f}"),
            ("ironing", "0"),
            ("layer_height", f"{f.layer_height:.3f}"),
            ("max_fan_speed", fan_spds),
            ("max_layer_height", max_lh),
            ("max_print_height", str(bz)),
            ("max_print_speed", "80"),
            ("max_volumetric_speed", "0"),
            ("min_fan_speed", fan_spds),
            ("min_layer_height", min_lh),
            ("min_print_speed", _csv(["10"] * n_ext)),
            ("min_skirt_length", "0"),
            ("nozzle_diameter", nozzles),
            ("only_retract_when_crossing_perimeters", "0"),
            ("ooze_prevention", "0"),
            ("overhangs", "1"),
            ("perimeter_acceleration", "800"),
            ("perimeter_extruder", "1"),
            ("perimeter_extrusion_width", f"{f.line_width:.3f}"),
            ("perimeter_generator", "arachne"),
            ("perimeter_speed", f"{f.print_speed_mm_s:.1f}"),
            ("perimeters", str(f.perimeters)),
            ("raft_layers", "0"),
            ("resolution", "0"),
            ("retract_before_travel", ret_trav),
            ("retract_before_wipe", _csv(["0%"] * n_ext)),
            ("retract_layer_change", ret_lc),
            ("retract_length", ret_lens),
            ("retract_lift", ret_lifts),
            ("retract_speed", ret_spds),
            ("seam_position", "aligned"),
            ("single_extruder_multi_material", "0"),
            ("skirt_distance", "6"),
            ("skirt_height", "1"),
            ("skirts", "0"),
            ("slice_closing_radius", "0.049"),
            ("small_perimeter_speed", "25"),
            ("solid_infill_below_area", "0"),
            ("solid_infill_every_layers", "0"),
            ("solid_infill_extruder", "1"),
            ("solid_infill_extrusion_width", f"{f.line_width:.3f}"),
            ("solid_infill_speed", f"{f.print_speed_mm_s:.1f}"),
            ("spiral_vase", "0"),
            ("support_material", "0"),
            ("support_material_angle", "0"),
            ("support_material_auto", "0"),
            ("support_material_extruder", "1"),
            ("support_material_extrusion_width", f"{f.line_width:.3f}"),
            ("support_material_interface_layers", "2"),
            ("support_material_speed", f"{f.print_speed_mm_s:.1f}"),
            ("temperature", temps),
            ("thin_walls", "1"),
            ("top_fill_pattern", "monotonic"),
            ("top_infill_extrusion_width", f"{f.line_width:.3f}"),
            ("top_solid_infill_speed", f"{f.print_speed_mm_s:.1f}"),
            ("top_solid_layers", str(f.top_solid_layers)),
            ("top_solid_min_thickness", "0.5"),
            ("travel_speed", f"{f.travel_speed_mm_s:.1f}"),
            ("travel_speed_z", "12"),
            ("use_firmware_retraction", "0"),
            ("use_relative_e_distances", "1"),
            ("use_volumetric_e", "0"),
            ("variable_layer_height", "0"),
            ("wipe", wipes),
            ("wipe_tower", "0"),
            ("xy_size_compensation", "0"),
        ]
        for key, val in config_pairs:
            self.emit(f"; {key} = {val}")
        self.emit("; prusaslicer_config = end")

    def end_gcode(self) -> None:
        self.emit("")
        self.emit(";TYPE:Custom")
        self.emit(self.cfg.uv.uv_off_gcode + " ; UV OFF")
        self.emit(f"G1 Z{self.cur_z + 15.0:.3f} F1200 ; raise Z")
        self.emit("M104 S0 ; turn off temperature")
        self.emit("M140 S0 ; turn off heatbed")
        self.emit("M107 ; turn off fan")
        self.emit("M84 ; disable motors")
        self._emit_prusaslicer_config_footer()

    def emit_fff_layer(self, layer_idx: int, z: float, paths: List[Tuple[str, np.ndarray]]) -> None:
        f = self.cfg.fff
        f_print = f.print_speed_mm_s * 60.0
        f_travel = f.travel_speed_mm_s * 60.0
        f_ret = f.retract_speed_mm_s * 60.0

        self.emit("")
        self.emit(";LAYER_CHANGE")
        self.emit(f";Z:{z:.3f}")
        self.emit(f";HEIGHT:{f.layer_height:.3f}")
        self.emit(";BEFORE_LAYER_CHANGE")
        self.emit("G92 E0")
        self.emit(f";{z:.3f}")
        self.emit(";AFTER_LAYER_CHANGE")
        self.emit(f";{z:.3f}")

        if layer_idx == 2:
            fan_pwm = int(round(255 * f.part_cooling_fan_pct / 100.0))
            self.emit(f"M106 S{fan_pwm}")

        self.emit(f"G1 Z{z:.3f} F1200 ; move to layer Z")
        self.cur_z = z
        self._active_type = None
        self._active_width = None

        for tag, poly in paths:
            if len(poly) < 2:
                continue

            prusa_type = _PRUSA_TYPE_MAP.get(tag, "Internal infill")
            self._set_feature(prusa_type, f.line_width)

            x0, y0 = float(poly[0, 0]), float(poly[0, 1])
            dist_travel = math.hypot(x0 - self.cur_x, y0 - self.cur_y)
            if dist_travel > 1.5:
                self.emit(f"G1 E-{f.retract_length_mm:.5f} F{f_ret:.0f} ; retract")
                if f.z_hop_mm > 0.0:
                    self.emit(f"G1 Z{z + f.z_hop_mm:.3f} F1200 ; lift Z")
                self.emit(f"G1 X{x0:.3f} Y{y0:.3f} F{f_travel:.0f} ; move to start")
                if f.z_hop_mm > 0.0:
                    self.emit(f"G1 Z{z:.3f} F1200 ; restore layer Z")
                self.emit(f"G1 E{f.retract_length_mm:.5f} F{f_ret:.0f} ; unretract")
                self.est_time_s += (dist_travel / f.travel_speed_mm_s) + 0.15
            else:
                self.emit(f"G1 X{x0:.3f} Y{y0:.3f} F{f_travel:.0f}")
                self.est_time_s += dist_travel / f.travel_speed_mm_s
            self.cur_x, self.cur_y = x0, y0

            self.emit(f"G1 F{f_print:.0f}")
            for pt in poly[1:]:
                nx_, ny_ = float(pt[0]), float(pt[1])
                seg_len = math.hypot(nx_ - self.cur_x, ny_ - self.cur_y)
                if seg_len < 1e-4:
                    continue
                e_val = seg_len * self.e_per_mm
                self.emit(f"G1 X{nx_:.3f} Y{ny_:.3f} E{e_val:.5f}")
                self.total_e_fff_mm += e_val
                self.est_time_s += seg_len / f.print_speed_mm_s
                self.cur_x, self.cur_y = nx_, ny_

    def compute_uv_speed_and_passes(self, slab_thickness_mm: float) -> Tuple[float, int]:
        u = self.cfg.uv
        target_dose = u.target_dose_mj_cm2_per_mm * max(0.2, slab_thickness_mm)
        passes = max(1, u.min_passes)
        v_mm_s = 10.0 * (u.irradiance_mw_cm2 * u.beam_width_cm * passes) / max(1e-3, target_dose)
        while v_mm_s < u.min_scan_speed_mm_s and passes < 12:
            passes += 1
            v_mm_s = 10.0 * (u.irradiance_mw_cm2 * u.beam_width_cm * passes) / max(1e-3, target_dose)
        v_mm_s = min(u.max_scan_speed_mm_s, max(u.min_scan_speed_mm_s, v_mm_s))
        return v_mm_s, passes

    def emit_resin_and_uv_cycle(
        self,
        cycle: CureCycle,
        z_k: float,
        dosing_points: List[DosingPoint],
        bbox_xy: Tuple[float, float, float, float],
    ) -> None:
        raise NotImplementedError


class BedslingerPrototypeHAL(GCodeHAL):
    """Prototype 1: External stepper pump + needle on X carriage + overhead X-axis UV strip."""

    def emit_resin_and_uv_cycle(
        self,
        cycle: CureCycle,
        z_k: float,
        dosing_points: List[DosingPoint],
        bbox_xy: Tuple[float, float, float, float],
    ) -> None:
        p = self.cfg.printer
        r = self.cfg.resin
        u = self.cfg.uv
        f = self.cfg.fff
        f_travel = f.travel_speed_mm_s * 60.0
        f_pump = r.dispense_speed_e_mm_s * 60.0
        f_suck = r.suckback_speed_e_mm_s * 60.0
        z_safe = z_k + max(4.0, f.z_hop_mm + p.needle_offset_z + 2.0)
        z_disp = z_k + p.needle_offset_z

        self.emit("")
        self.emit(
            f"; === PHOTOPOLYMER INJECT & UV CURE (Layers {cycle.start_layer}-{cycle.end_layer}, "
            f"slab={cycle.slab_thickness_mm:.2f} mm, trigger={cycle.trigger}) ==="
        )
        self._set_feature("Custom")
        self.emit(f"G1 E-{f.retract_length_mm * 1.5:.5f} F{f.retract_speed_mm_s * 60.0:.0f} ; retract FFF")
        self.emit(f"G1 Z{z_safe:.3f} F1200 ; lift Z for resin dosing")
        if p.resin_extruder_axis == "E":
            self.emit(f"{p.tool_resin} ; select external stepper pump extruder")
            self.emit("G92 E0")

        ax = p.resin_extruder_axis
        for idx, dp in enumerate(dosing_points):
            tx = dp.x - p.needle_offset_x
            ty = dp.y - p.needle_offset_y
            self.emit(f"; Channel point {idx + 1}: target=({dp.x:.2f},{dp.y:.2f}) vol={dp.volume_mm3:.2f} mm^3")
            self.emit(f"G1 X{tx:.3f} Y{ty:.3f} F{f_travel:.0f}")
            self.emit(f"G1 Z{z_disp:.3f} F900")
            self.emit(f"G1 {ax}{dp.pump_e_mm:.5f} F{f_pump:.0f} ; dispense photopolymer")
            self.emit(f"G1 {ax}-{r.suckback_e_mm:.5f} F{f_suck:.0f} ; pump suck-back")
            self.emit(f"G1 Z{z_safe:.3f} F1200")
            self.total_e_resin_mm += dp.pump_e_mm
            self.total_resin_mm3 += dp.volume_mm3
            self.est_time_s += (dp.pump_e_mm / max(0.1, r.dispense_speed_e_mm_s)) + 0.6

        self.emit(f"G4 P{r.settle_dwell_ms} ; resin leveling dwell")
        self.est_time_s += r.settle_dwell_ms / 1000.0

        v_uv_mm_s, passes = self.compute_uv_speed_and_passes(cycle.slab_thickness_mm)
        z_uv = min(p.bed_size_z, z_k + u.focal_z_clearance_mm)
        half_beam_mm = 0.5 * u.beam_width_cm * 10.0
        _, y_min_b, _, y_max_b = bbox_xy

        y_start = max(0.0, min(p.bed_size_y, p.uv_strip_y_offset + (p.bed_size_y * 0.5 - y_max_b) - half_beam_mm))
        y_end = max(0.0, min(p.bed_size_y, p.uv_strip_y_offset + (p.bed_size_y * 0.5 - y_min_b) + half_beam_mm))
        if abs(y_end - y_start) < 1.0:
            y_start = max(0.0, y_min_b - half_beam_mm)
            y_end = min(p.bed_size_y, y_max_b + half_beam_mm)

        self.emit(f"; UV Hardening: {passes} passes @ {v_uv_mm_s:.2f} mm/s (Z_focal={z_uv:.2f} mm)")
        self.emit(f"G1 X{p.x_uv_shield_park:.3f} Z{z_uv:.3f} F{f_travel:.0f} ; park head behind UV shield")
        for p_idx in range(passes):
            ya, yb = (y_start, y_end) if (p_idx % 2 == 0) else (y_end, y_start)
            self.emit(f"G1 Y{ya:.3f} F{f_travel:.0f}")
            self.emit(u.uv_on_gcode + f" ; UV ON (pass {p_idx + 1}/{passes})")
            self.emit(f"G1 Y{yb:.3f} F{v_uv_mm_s * 60.0:.1f} ; UV cure sweep")
            self.emit(u.uv_off_gcode + " ; UV OFF")
            self.est_time_s += abs(yb - ya) / max(0.1, v_uv_mm_s)
            if p_idx + 1 < passes and u.inter_pass_cooldown_ms > 0:
                self.emit(f"G4 P{u.inter_pass_cooldown_ms} ; thermal cooldown")
                self.est_time_s += u.inter_pass_cooldown_ms / 1000.0

        if p.resin_extruder_axis == "E":
            self.emit(f"{p.tool_fff} ; return to FFF extruder")
            self.emit("G92 E0")
        self.emit(f"G1 E{f.retract_length_mm * 1.5:.5f} F{f.retract_speed_mm_s * 60.0:.0f} ; unretract FFF")
        self.cur_x = p.x_uv_shield_park
        self.cur_y = y_end if (passes % 2 == 1) else y_start
        self.cur_z = z_uv


class CoreXYToolchangerHAL(GCodeHAL):
    """Future CoreXY Toolchanger (Prusa XL / StealthChanger): T0=FFF, T1=Resin, T2=UV Head."""

    def emit_resin_and_uv_cycle(
        self,
        cycle: CureCycle,
        z_k: float,
        dosing_points: List[DosingPoint],
        bbox_xy: Tuple[float, float, float, float],
    ) -> None:
        p = self.cfg.printer
        r = self.cfg.resin
        u = self.cfg.uv
        f = self.cfg.fff
        f_travel = f.travel_speed_mm_s * 60.0
        f_pump = r.dispense_speed_e_mm_s * 60.0
        f_suck = r.suckback_speed_e_mm_s * 60.0
        z_safe = z_k + max(4.0, f.z_hop_mm + 2.0)

        self.emit("")
        self.emit(
            f"; === COREXY TOOLCHANGER INJECT & UV CURE (Layers {cycle.start_layer}-{cycle.end_layer}, "
            f"slab={cycle.slab_thickness_mm:.2f} mm, trigger={cycle.trigger}) ==="
        )
        self._set_feature("Custom")
        self.emit(f"G1 Z{z_safe:.3f} F1200")
        self.emit(f"{p.tool_resin} ; pick up resin toolhead")
        self.emit("G92 E0")

        for idx, dp in enumerate(dosing_points):
            self.emit(f"; Channel point {idx + 1}: ({dp.x:.2f},{dp.y:.2f}) vol={dp.volume_mm3:.2f} mm^3")
            self.emit(f"G1 X{dp.x:.3f} Y{dp.y:.3f} F{f_travel:.0f}")
            self.emit(f"G1 Z{z_k + p.needle_offset_z:.3f} F900")
            self.emit(f"G1 E{dp.pump_e_mm:.5f} F{f_pump:.0f} ; dispense photopolymer")
            self.emit(f"G1 E-{r.suckback_e_mm:.5f} F{f_suck:.0f} ; pump suck-back")
            self.emit(f"G1 Z{z_safe:.3f} F1200")
            self.total_e_resin_mm += dp.pump_e_mm
            self.total_resin_mm3 += dp.volume_mm3
            self.est_time_s += (dp.pump_e_mm / max(0.1, r.dispense_speed_e_mm_s)) + 0.6

        self.emit(f"G4 P{r.settle_dwell_ms} ; resin leveling dwell")
        self.est_time_s += r.settle_dwell_ms / 1000.0

        v_uv_mm_s, passes = self.compute_uv_speed_and_passes(cycle.slab_thickness_mm)
        z_uv = min(p.bed_size_z, z_k + u.focal_z_clearance_mm)
        x_min_b, y_min_b, x_max_b, y_max_b = bbox_xy
        step_x = max(1.0, u.beam_width_cm * 10.0 * 0.75)

        self.emit(f"{p.tool_uv} ; dock shaded resin head, pick up UV curing toolhead")
        self.emit(f"G1 Z{z_uv:.3f} F1200")
        for p_idx in range(passes):
            x_cur = x_min_b
            flip = False
            while x_cur <= x_max_b + 1e-3:
                ya, yb = (y_min_b, y_max_b) if not flip else (y_max_b, y_min_b)
                self.emit(f"G1 X{x_cur:.3f} Y{ya:.3f} F{f_travel:.0f}")
                self.emit(u.uv_on_gcode + " ; UV ON")
                self.emit(f"G1 Y{yb:.3f} F{v_uv_mm_s * 60.0:.1f} ; UV cure sweep")
                self.emit(u.uv_off_gcode + " ; UV OFF")
                self.est_time_s += abs(yb - ya) / max(0.1, v_uv_mm_s)
                x_cur += step_x
                flip = not flip
            if p_idx + 1 < passes and u.inter_pass_cooldown_ms > 0:
                self.emit(f"G4 P{u.inter_pass_cooldown_ms} ; thermal cooldown")
                self.est_time_s += u.inter_pass_cooldown_ms / 1000.0

        self.emit(f"{p.tool_fff} ; return to FFF toolhead")
        self.emit("G92 E0")
        self.cur_z = z_uv


# =============================================================================
# Pipeline Orchestrator & CLI
# =============================================================================

def run_slicer(mesh: trimesh.Trimesh, output_gcode: Path, cfg: SlicerConfig) -> None:
    print(f"[Stage 1] Building lightweight surface field (pitch={cfg.voxel_pitch_mm} mm)...")
    field = VolumetricField(mesh, pitch=cfg.voxel_pitch_mm)

    print(
        f"[Stage 2-3] Synthesizing 3D outer-surface {cfg.infill.pattern} pattern "
        f"(pitch={cfg.infill.target_cell_pitch} mm, resin_channel={cfg.infill.min_channel_clearance} mm, "
        f"flatten_angle=<{cfg.infill.flatten_angle_deg} deg)..."
    )
    lattice = generate_normal_dendritic_infill(field, cfg)
    print(f"            Placed {len(lattice.seeds)} cells ({len(lattice)} 3D wireframe edges).")

    print(f"[Stage 3] Exact vector XY slicing (h={cfg.fff.layer_height} mm)...")
    layers, xs, ys = slice_model_to_layers(mesh, field, lattice, cfg)
    print(f"          Sliced {len(layers)} exact vector layers.")

    print(
        f"[Stage 4] Solving global DP cure schedule "
        f"(window=[{cfg.resin.x_min_layers}, {cfg.resin.x_max_layers}] layers)..."
    )
    schedule = compute_optimal_cure_schedule(layers, cfg)
    cure_by_layer = {c.end_layer: c for c in schedule}
    print(f"          Scheduled {len(schedule)} inject+UV cycles.")

    hal: GCodeHAL = (
        CoreXYToolchangerHAL(cfg)
        if cfg.printer.printer_type == "corexy_toolchanger"
        else BedslingerPrototypeHAL(cfg)
    )
    hal.start_gcode()

    total_resin_mm3 = 0.0
    print("[Stage 5-6] Generating exact vector FFF toolpaths, resin channel dosing & G-code...")
    for ly in layers:
        fff_paths = generate_fff_paths_for_layer(ly, xs, ys, cfg)
        hal.emit_fff_layer(ly.layer_idx, ly.z, fff_paths)

        if ly.layer_idx in cure_by_layer:
            cycle = cure_by_layer[ly.layer_idx]
            dosing_pts, bbox_xy = plan_cycle_dosing(layers, cycle, xs, ys, cfg)
            if dosing_pts:
                total_resin_mm3 += sum(d.volume_mm3 for d in dosing_pts)
                hal.emit_resin_and_uv_cycle(cycle, ly.z, dosing_pts, bbox_xy)

    hal.end_gcode()
    output_gcode.write_text("\n".join(hal.lines) + "\n", encoding="utf-8")
    print(
        f"[Done] Wrote {len(hal.lines)} G-code lines to '{output_gcode}' "
        f"(total photopolymer: {total_resin_mm3 / 1000.0:.2f} mL across {len(schedule)} cures)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone Hybrid FFF + In-Situ UV Photopolymer Slicer (Exact Vector Engine)"
    )
    parser.add_argument("input", nargs="?", type=Path, help="Input 3D model (STL, OBJ, 3MF, STEP)")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.gcode"), help="Output G-code path")
    parser.add_argument("-c", "--config", type=Path, help="Load slicer/printer settings from JSON")
    parser.add_argument("--dump-default-config", type=Path, help="Write default JSON config and exit")
    parser.add_argument("--printer-type", choices=["bedslinger_prototype", "corexy_toolchanger"])
    parser.add_argument("--infill-mode", choices=["hollow_shell", "full_to_medial"])
    parser.add_argument("--pattern", choices=["voronoi", "hexagonal"])
    parser.add_argument("--flatten-angle", type=float, help="Flatten faces tilted < angle (deg) to horizontal")
    parser.add_argument("--test-sphere", action="store_true", help="Slice a built-in 30 mm sphere")
    parser.add_argument("--test-wall", action="store_true", help="Slice a built-in 60x25x5 mm curved wavy wall")
    args = parser.parse_args()

    if args.dump_default_config:
        SlicerConfig().to_json(args.dump_default_config)
        print(f"Wrote default configuration to '{args.dump_default_config}'.")
        return

    cfg = SlicerConfig.from_json(args.config) if args.config else SlicerConfig()
    if args.printer_type:
        cfg.printer.printer_type = args.printer_type
    if args.infill_mode:
        cfg.infill.mode = args.infill_mode
    if args.pattern:
        cfg.infill.pattern = args.pattern
    if args.flatten_angle is not None:
        cfg.infill.flatten_angle_deg = args.flatten_angle

    if args.test_wall:
        mesh = create_test_curved_wall_mesh(cfg)
    elif args.test_sphere:
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=15.0)
        mesh.apply_translation([cfg.printer.bed_size_x * 0.5, cfg.printer.bed_size_y * 0.5, 15.0])
    elif args.input:
        mesh = load_and_center_mesh(args.input, cfg)
    else:
        parser.error("Provide an input model file or pass --test-wall / --test-sphere.")

    run_slicer(mesh, args.output, cfg)


if __name__ == "__main__":
    main()