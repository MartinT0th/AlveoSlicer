"""
3D Cellular Lattice Data Structures & Exact 2D Voronoi Polyhedron Slicer.

Defines the lightweight data structures shared between Stage 2–3 (3D surface-normal
cellular synthesis), planar slicing, and the 3D OpenGL wireframe preview:
  - `Capsule`: single 3D wireframe edge segment for 3D previewing.
  - `CellHalfPlane`: precomputed 3D bisector plane `(x - m) · u <= -inset`.
  - `NormalVoronoiLattice`: list subclass storing wireframe capsules + 3D half-planes.
  - `clip_voronoi_cell_2d`: exact Sutherland-Hodgman polygon clipper at arbitrary height `z_k`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import shapely
from shapely.geometry import Polygon


@dataclass
class Capsule:
    """Single 3D wireframe edge segment used for OpenGL cellular lattice preview."""

    a: np.ndarray
    b: np.ndarray
    radius: float
    z_min: float
    z_max: float


@dataclass
class CellHalfPlane:
    """Precomputed 3D bisector half-plane ``(x - m) . u <= -inset`` for exact planar slicing."""

    ux: float
    uy: float
    uz: float
    dot_m: float
    dist: float
    flattened: bool = False


class NormalVoronoiLattice(list):
    """
    Subclass of ``list[Capsule]`` so the 3D viewport renders true 3D cellular wireframes
    while the planar slicer evaluates exact 3D half-planes or analytic TPMS contours.
    """

    def __init__(
        self,
        capsules: List[Capsule],
        seeds: np.ndarray,
        cell_planes: List[List[CellHalfPlane]],
        seed_z_bounds: np.ndarray,
        pattern: str = "hexagonal",
        pitch: float = 10.0,
    ) -> None:
        """Store 3D wireframe capsules alongside exact half-plane cell definitions."""
        super().__init__(capsules)
        self.seeds = seeds
        self.cell_planes = cell_planes
        self.seed_z_bounds = seed_z_bounds
        self.pattern = pattern
        self.pitch = pitch


def combine_lattices(
    lattices: List[NormalVoronoiLattice],
    pattern: str = "hexagonal",
    pitch: float = 10.0,
) -> NormalVoronoiLattice:
    """Merge multiple independent per-body ``NormalVoronoiLattice`` instances into one combined preview lattice."""
    if not lattices:
        return NormalVoronoiLattice(
            capsules=[],
            seeds=np.zeros((0, 3), dtype=np.float64),
            cell_planes=[],
            seed_z_bounds=np.zeros((0, 2), dtype=np.float64),
            pattern=pattern,
            pitch=pitch,
        )
    if len(lattices) == 1:
        return lattices[0]

    all_caps: List[Capsule] = []
    seed_chunks: List[np.ndarray] = []
    zbnd_chunks: List[np.ndarray] = []
    all_planes: List[List[CellHalfPlane]] = []

    for lat in lattices:
        all_caps.extend(lat)
        if len(lat.seeds) > 0:
            seed_chunks.append(lat.seeds)
        if len(lat.seed_z_bounds) > 0:
            zbnd_chunks.append(lat.seed_z_bounds)
        all_planes.extend(lat.cell_planes)

    seeds = np.vstack(seed_chunks) if seed_chunks else np.zeros((0, 3), dtype=np.float64)
    z_bounds = np.vstack(zbnd_chunks) if zbnd_chunks else np.zeros((0, 2), dtype=np.float64)
    return NormalVoronoiLattice(
        capsules=all_caps,
        seeds=seeds,
        cell_planes=all_planes,
        seed_z_bounds=z_bounds,
        pattern=lattices[0].pattern,
        pitch=lattices[0].pitch,
    )


def clip_voronoi_cell_2d(
    planes: List[CellHalfPlane],
    z_k: float,
    inset: float,
    bbox: Tuple[float, float, float, float],
) -> Optional[Polygon]:
    """Clip a 3D cellular polyhedron by all neighbor half-planes at height ``z_k`` via Sutherland-Hodgman."""
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
                    out.append((p_prev[0] + t * (p_cur[0] - p_prev[0]), p_prev[1] + t * (p_cur[1] - p_prev[1])))
                out.append(p_cur)
            elif in_prev:
                t = d_prev / (d_prev - d_cur)
                out.append((p_prev[0] + t * (p_cur[0] - p_prev[0]), p_prev[1] + t * (p_cur[1] - p_prev[1])))

            p_prev, d_prev, in_prev = p_cur, d_cur, in_cur

        if len(out) < 3:
            return None
        poly = out

    p_geom = Polygon(poly)
    if p_geom.is_empty or p_geom.area < 1e-4:
        return None
    if not p_geom.is_valid:
        p_geom = shapely.make_valid(p_geom)
    return p_geom
