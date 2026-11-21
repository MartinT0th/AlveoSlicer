"""Stage 5: 3D fluid basin segmentation, geodesic multi-point volume attribution, and TSP needle routing."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from scipy import ndimage

from ..core.config import SlicerConfig
from ..slicing.layer import SlicedLayer
from .scheduler import CureCycle

# 10-connectivity for 3D BFS: 8-connected within each XY layer + vertical +/-Z faces
_NEIGHBORS_3D = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
    (0, 1, 1),
    (0, 1, -1),
    (0, -1, 1),
    (0, -1, -1),
)
_CONN_2D_8 = np.ones((3, 3), dtype=np.int32)
_CONN_3D_26 = np.ones((3, 3, 3), dtype=np.int32)


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


def _basin_volume_mm3(basin_mask: np.ndarray, px_area: float, h: float, top_h_eff: float, shrinkage: float) -> float:
    nz = basin_mask.shape[0]
    vol = 0.0
    for z_i in range(nz):
        h_eff = top_h_eff if z_i == nz - 1 else h
        cnt = int(np.count_nonzero(basin_mask[z_i]))
        if cnt > 0:
            vol += cnt * px_area * h_eff
    return vol * shrinkage


def plan_cycle_dosing(
    layers: List[SlicedLayer],
    cycle: CureCycle,
    xs: np.ndarray,
    ys: np.ndarray,
    cfg: SlicerConfig,
) -> Tuple[List[DosingPoint], Tuple[float, float, float, float]]:
    """
    Segment 3D liquid basins in a cure cycle slab, filter pixelated 2D ghost islands
    (merging their exact resin volume into the touching or nearest real island),
    place needle entry points via EDT poles of inaccessibility, attribute volumes via
    multi-source 3D geodesic BFS, and TSP-order targets.
    """
    rcfg = cfg.resin
    h = cfg.fff.layer_height
    rp = cfg.slice_raster_pitch_mm
    px_area = rp * rp
    top_h_eff = max(0.0, h - rcfg.headspace_margin_mm)

    ghost_min_area = max(0.0, float(getattr(rcfg, "ghost_island_min_area_mm2", 0.85)))
    ghost_min_px = max(2, int(math.ceil(ghost_min_area / max(px_area, 1e-6)))) if ghost_min_area > 0.0 else 0
    min_dose_vol_mm3 = max(0.05, ghost_min_area * min(h, 0.25))

    slab = np.stack(
        [layers[k - 1].void_mask for k in range(cycle.start_layer, cycle.end_layer + 1)],
        axis=0,
    )
    nz, nx, ny = slab.shape
    top_acc_global = layers[cycle.end_layer - 1].accessible_mask

    # Use 26-connectivity in 3D so diagonal raster corners never fracture into fake ghost basins
    lbl_3d, n_basins = ndimage.label(slab, structure=_CONN_3D_26)
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

    real_entries: List[Tuple[DosingPoint, int]] = []  # (DosingPoint, basin_id)
    ghost_basins: List[Tuple[np.ndarray, float, Tuple[float, float]]] = []  # (mask_3d, vol_mm3, centroid_xy)

    for bid in range(1, n_basins + 1):
        basin_mask = lbl_3d == bid
        top_open = basin_mask[-1]
        b_vol = _basin_volume_mm3(basin_mask, px_area, h, top_h_eff, rcfg.shrinkage_factor)
        if b_vol <= 1e-5:
            continue

        top_px = int(np.count_nonzero(top_open))
        # Ghost basin check: no real top opening, sub-ghost-threshold top pixels, or tiny unaccessible sliver
        if top_px == 0 or (top_px <= ghost_min_px and not np.any(top_open & top_acc_global)):
            pts_3d = np.argwhere(basin_mask)
            cx = float(xs[int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((basin_mask, b_vol, (cx, cy)))
            continue

        # 8-connected 2D island labeling on top opening prevents diagonal single-pixel ghost islands
        isl_lbl, n_isl = ndimage.label(top_open, structure=_CONN_2D_8)
        isl_counts = [int(np.count_nonzero(isl_lbl == iid)) for iid in range(1, n_isl + 1)]
        has_real_island = any(
            (cnt > ghost_min_px) or np.any((isl_lbl == (idx + 1)) & top_acc_global)
            for idx, cnt in enumerate(isl_counts)
        )

        seeds_for_bfs: List[Tuple[int, int, int, int]] = []
        seed_coords_mm: List[Tuple[float, float]] = []

        for iid in range(1, n_isl + 1):
            isl_mask = isl_lbl == iid
            cnt = isl_counts[iid - 1]
            valid_entry = isl_mask & top_acc_global
            # Skip sub-threshold ghost top islands when the same 3D basin already has a real entry
            if has_real_island and cnt <= ghost_min_px and not np.any(valid_entry):
                continue
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
            pts_3d = np.argwhere(basin_mask)
            cx = float(xs[int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((basin_mask, b_vol, (cx, cy)))
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

        # Fold any ultra-tiny sub-threshold seed volume into the nearest real seed in this basin
        basin_real_pts: List[DosingPoint] = []
        tiny_pts: List[Tuple[float, float, float]] = []
        for sid, (px_mm, py_mm) in enumerate(seed_coords_mm):
            v = float(vols[sid])
            if v <= 1e-5:
                continue
            if v < min_dose_vol_mm3:
                tiny_pts.append((px_mm, py_mm, v))
            else:
                basin_real_pts.append(
                    DosingPoint(
                        x=px_mm,
                        y=py_mm,
                        volume_mm3=v,
                        pump_e_mm=float(v / rcfg.pump_mm3_per_e_mm),
                    )
                )

        if basin_real_pts:
            for tx, ty, tv in tiny_pts:
                nearest = min(basin_real_pts, key=lambda p: (p.x - tx) ** 2 + (p.y - ty) ** 2)
                nearest.volume_mm3 += tv
                nearest.pump_e_mm = float(nearest.volume_mm3 / rcfg.pump_mm3_per_e_mm)
            for pt in basin_real_pts:
                real_entries.append((pt, bid))
        else:
            # Whole basin had tiny volume — defer to ghost merging
            pts_3d = np.argwhere(basin_mask)
            cx = float(xs[int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((basin_mask, b_vol, (cx, cy)))

    # Merge every ghost island/basin volume into its touching (dilated) real basin, or nearest real island
    if real_entries and ghost_basins:
        real_bids = {bid for _, bid in real_entries}
        for g_mask, g_vol, (gx, gy) in ghost_basins:
            if g_vol <= 1e-5:
                continue
            # Prefer a real basin that touches the ghost island (1-2 px dilation)
            dil = ndimage.binary_dilation(g_mask, structure=_CONN_3D_26, iterations=2)
            touch_labels = set(int(v) for v in np.unique(lbl_3d[dil]) if int(v) in real_bids)
            if touch_labels:
                candidates = [pt for pt, bid in real_entries if bid in touch_labels]
            else:
                candidates = [pt for pt, _ in real_entries]
            nearest = min(candidates, key=lambda p: (p.x - gx) ** 2 + (p.y - gy) ** 2)
            nearest.volume_mm3 += g_vol
            nearest.pump_e_mm = float(nearest.volume_mm3 / rcfg.pump_mm3_per_e_mm)

    dosing_points = [pt for pt, _ in real_entries if pt.volume_mm3 > 1e-4]
    return order_points_tsp(dosing_points), bbox_xy
