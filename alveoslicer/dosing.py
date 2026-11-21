"""Stage 5: 3D fluid basin segmentation, geodesic multi-point volume attribution, and TSP needle routing."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from scipy import ndimage

from .config import SlicerConfig
from .scheduler import CureCycle
from .slicing import SlicedLayer

_NEIGHBORS_3D = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)


@dataclass
class DosingPoint:
    """Single needle dispensing coordinate with exact attributed resin volume and stepper E-move."""

    x: float
    y: float
    volume_mm3: float
    pump_e_mm: float


def order_points_tsp(points: List[DosingPoint]) -> List[DosingPoint]:
    """Optimize needle travel order via Nearest-Neighbor followed by 2-opt local refinement."""
    n = len(points)
    if n <= 2:
        return points

    coords = np.array([[p.x, p.y] for p in points], dtype=np.float64)
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
    """
    Segment 3D liquid basins in a cure cycle slab, place needle entry points via EDT poles of
    inaccessibility, attribute exact fluid volumes via multi-source 3D geodesic BFS, and TSP-order targets.
    """
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
                covered = (sub_x - bx) ** 2 + (sub_y - by) ** 2 <= flow_rad_px**2
                remaining[ix_lo:ix_hi, iy_lo:iy_hi] &= ~covered

        if not seeds_for_bfs:
            continue

        owner = np.full((nz, nx, ny), -1, dtype=np.int32)
        queue: deque[Tuple[int, int, int, int]] = deque()
        for sz, sx, sy, sid in seeds_for_bfs:
            owner[sz, sx, sy] = sid
            queue.append((sz, sx, sy, sid))

        while queue:
            cz, cx, cy, sid = queue.popleft()
            for dz, dx, dy in _NEIGHBORS_3D:
                nz_, nx_, ny_ = cz + dz, cx + dx, cy + dy
                if (
                    0 <= nz_ < nz
                    and 0 <= nx_ < nx
                    and 0 <= ny_ < ny
                    and basin_mask[nz_, nx_, ny_]
                    and owner[nz_, nx_, ny_] == -1
                ):
                    owner[nz_, nx_, ny_] = sid
                    queue.append((nz_, nx_, ny_, sid))

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

    return order_points_tsp(dosing_points), bbox_xy
