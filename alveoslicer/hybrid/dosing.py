"""Stage 5: 3D fluid basin segmentation, geodesic multi-point volume attribution, and TSP needle routing."""

from __future__ import annotations

import math
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


def _basin_volume_mm3(
    basin_mask: np.ndarray,
    z_start: int,
    total_nz: int,
    px_area: float,
    h: float,
    top_h_eff: float,
    shrinkage: float,
) -> float:
    """Compute shrinkage- and headspace-compensated volume for a cropped 3D basin mask."""
    z_counts = np.count_nonzero(basin_mask, axis=(1, 2))
    snz = basin_mask.shape[0]
    h_vec = np.full(snz, h, dtype=np.float64)
    if z_start + snz == total_nz:
        h_vec[-1] = top_h_eff
    return float(np.dot(z_counts, h_vec) * px_area * shrinkage)


def _vectorized_multiseed_bfs_volumes(
    sub_mask: np.ndarray,
    z_start: int,
    x_start: int,
    y_start: int,
    total_nz: int,
    seeds_for_bfs: List[Tuple[int, int, int, int]],
    px_area: float,
    h: float,
    top_h_eff: float,
    shrinkage: float,
) -> np.ndarray:
    """Vectorized 10-connected 3D geodesic BFS volume attribution on a tight padded basin sub-box."""
    snz, snx, sny = sub_mask.shape
    pz, px_s, py_s = snz + 2, snx + 2, sny + 2
    padded_mask = np.zeros((pz, px_s, py_s), dtype=bool)
    padded_mask[1:-1, 1:-1, 1:-1] = sub_mask
    flat_mask = padded_mask.ravel()

    flat_owner = np.full(flat_mask.size, -1, dtype=np.int32)
    stride_z = px_s * py_s
    stride_x = py_s
    d_offs = np.array(
        [dz * stride_z + dx * stride_x + dy for dz, dx, dy in _NEIGHBORS_3D],
        dtype=np.int32,
    )

    f_idx_list = []
    f_sid_list = []
    for sz, sx, sy, sid in seeds_for_bfs:
        fidx = (sz - z_start + 1) * stride_z + (sx - x_start + 1) * stride_x + (sy - y_start + 1)
        if flat_owner[fidx] == -1:
            flat_owner[fidx] = sid
            f_idx_list.append(fidx)
            f_sid_list.append(sid)

    frontier = np.asarray(f_idx_list, dtype=np.int32)
    f_sids = np.asarray(f_sid_list, dtype=np.int32)
    n_nbrs = len(d_offs)

    while frontier.size > 0:
        nbrs = (frontier[:, None] + d_offs[None, :]).ravel()
        ok = flat_mask[nbrs] & (flat_owner[nbrs] == -1)
        if not np.any(ok):
            break
        cand_idx = nbrs[ok]
        cand_sid = np.repeat(f_sids, n_nbrs)[ok]
        uniq_idx, first_pos = np.unique(cand_idx, return_index=True)
        uniq_sid = cand_sid[first_pos]
        flat_owner[uniq_idx] = uniq_sid
        frontier = uniq_idx
        f_sids = uniq_sid

    owner_3d = flat_owner.reshape((pz, px_s, py_s))[1:-1, 1:-1, 1:-1]
    n_seeds = len(seeds_for_bfs)
    vols = np.zeros(n_seeds, dtype=np.float64)
    for z_local in range(snz):
        h_eff = top_h_eff if (z_start + z_local == total_nz - 1) else h
        slice_own = owner_3d[z_local]
        valid_own = slice_own[slice_own >= 0]
        if valid_own.size > 0:
            vols += np.bincount(valid_own, minlength=n_seeds) * (px_area * h_eff)
    return vols * shrinkage


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
    if not np.any(any_xy):
        return [], (0.0, 0.0, 0.0, 0.0)
    any_x = np.flatnonzero(np.any(any_xy, axis=1))
    any_y = np.flatnonzero(np.any(any_xy, axis=0))
    bbox_xy = (
        float(xs[any_x[0]]),
        float(ys[any_y[0]]),
        float(xs[any_x[-1]]),
        float(ys[any_y[-1]]),
    )

    real_entries: List[Tuple[DosingPoint, int]] = []  # (DosingPoint, basin_id)
    ghost_basins: List[Tuple[Tuple[slice, slice, slice], np.ndarray, float, Tuple[float, float]]] = []
    basin_slices = ndimage.find_objects(lbl_3d)

    for bid in range(1, n_basins + 1):
        sl = basin_slices[bid - 1]
        if sl is None:
            continue
        sl_z, sl_x, sl_y = sl
        z0, x0, y0 = sl_z.start, sl_x.start, sl_y.start
        sub_mask = lbl_3d[sl] == bid
        b_vol = _basin_volume_mm3(sub_mask, z0, nz, px_area, h, top_h_eff, rcfg.shrinkage_factor)
        if b_vol <= 1e-5:
            continue

        reaches_top = sl_z.stop == nz
        top_open_sub = sub_mask[-1] if reaches_top else None
        top_acc_sub = top_acc_global[sl_x, sl_y]
        top_px = int(np.count_nonzero(top_open_sub)) if top_open_sub is not None else 0

        # Ghost basin check: no real top opening, sub-ghost-threshold top pixels, or tiny unaccessible sliver
        if top_px == 0 or (top_px <= ghost_min_px and not np.any(top_open_sub & top_acc_sub)):
            pts_3d = np.argwhere(sub_mask)
            cx = float(xs[x0 + int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[y0 + int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((sl, sub_mask, b_vol, (cx, cy)))
            continue

        assert top_open_sub is not None
        # 8-connected 2D island labeling on cropped top opening prevents diagonal single-pixel ghost islands
        isl_lbl, n_isl = ndimage.label(top_open_sub, structure=_CONN_2D_8)
        isl_counts = np.bincount(isl_lbl.ravel(), minlength=n_isl + 1)
        isl_acc_counts = np.bincount(isl_lbl[top_acc_sub], minlength=n_isl + 1)
        has_real_island = bool(np.any((isl_counts[1:] > ghost_min_px) | (isl_acc_counts[1:] > 0)))
        isl_slices = ndimage.find_objects(isl_lbl)

        seeds_for_bfs: List[Tuple[int, int, int, int]] = []
        seed_coords_mm: List[Tuple[float, float]] = []

        for iid in range(1, n_isl + 1):
            cnt = int(isl_counts[iid])
            has_acc = int(isl_acc_counts[iid]) > 0
            # Skip sub-threshold ghost top islands when the same 3D basin already has a real entry
            if has_real_island and cnt <= ghost_min_px and not has_acc:
                continue
            isl_sl = isl_slices[iid - 1]
            if isl_sl is None:
                continue
            ix_off, iy_off = isl_sl[0].start, isl_sl[1].start
            isl_box = isl_lbl[isl_sl] == iid
            valid_entry = (isl_box & top_acc_sub[isl_sl]) if has_acc else isl_box

            # Pad by 1 pixel so EDT measures true distance to island boundary even on sub-box edges
            padded_isl = np.pad(isl_box, 1, mode="constant", constant_values=False)
            edt = ndimage.distance_transform_edt(padded_isl)[1:-1, 1:-1] * valid_entry
            if float(np.max(edt)) <= 0.0:
                continue

            bnx, bny = isl_box.shape
            remaining = valid_entry.copy()
            while np.any(remaining):
                masked_edt = edt * remaining
                flat_best = int(np.argmax(masked_edt))
                lbx, lby = divmod(flat_best, bny)
                gbx = x0 + ix_off + lbx
                gby = y0 + iy_off + lby
                sid = len(seed_coords_mm)
                seed_coords_mm.append((float(xs[gbx]), float(ys[gby])))
                seeds_for_bfs.append((nz - 1, gbx, gby, sid))

                ix_lo = max(0, int(lbx - flow_rad_px))
                ix_hi = min(bnx, int(lbx + flow_rad_px + 1))
                iy_lo = max(0, int(lby - flow_rad_px))
                iy_hi = min(bny, int(lby + flow_rad_px + 1))
                sub_x, sub_y = np.ogrid[ix_lo:ix_hi, iy_lo:iy_hi]
                covered = (sub_x - lbx) ** 2 + (sub_y - lby) ** 2 <= flow_rad_px**2
                remaining[ix_lo:ix_hi, iy_lo:iy_hi] &= ~covered

        if not seeds_for_bfs:
            pts_3d = np.argwhere(sub_mask)
            cx = float(xs[x0 + int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[y0 + int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((sl, sub_mask, b_vol, (cx, cy)))
            continue

        if len(seeds_for_bfs) == 1:
            vols = np.array([b_vol], dtype=np.float64)
        else:
            vols = _vectorized_multiseed_bfs_volumes(
                sub_mask, z0, x0, y0, nz, seeds_for_bfs, px_area, h, top_h_eff, rcfg.shrinkage_factor
            )

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
            pts_3d = np.argwhere(sub_mask)
            cx = float(xs[x0 + int(round(float(pts_3d[:, 1].mean())))])
            cy = float(ys[y0 + int(round(float(pts_3d[:, 2].mean())))])
            ghost_basins.append((sl, sub_mask, b_vol, (cx, cy)))

    # Merge every ghost island/basin volume into its touching (dilated) real basin, or nearest real island
    if real_entries and ghost_basins:
        real_bids = {bid for _, bid in real_entries}
        for (sl_z, sl_x, sl_y), g_sub, g_vol, (gx, gy) in ghost_basins:
            if g_vol <= 1e-5:
                continue
            # Prefer a real basin that touches the ghost island (1-2 px dilation on cropped sub-box)
            ez0, ez1 = max(0, sl_z.start - 2), min(nz, sl_z.stop + 2)
            ex0, ex1 = max(0, sl_x.start - 2), min(nx, sl_x.stop + 2)
            ey0, ey1 = max(0, sl_y.start - 2), min(ny, sl_y.stop + 2)
            local_g = np.zeros((ez1 - ez0, ex1 - ex0, ey1 - ey0), dtype=bool)
            local_g[
                sl_z.start - ez0 : sl_z.stop - ez0,
                sl_x.start - ex0 : sl_x.stop - ex0,
                sl_y.start - ey0 : sl_y.stop - ey0,
            ] = g_sub
            dil = ndimage.binary_dilation(local_g, structure=_CONN_3D_26, iterations=2)
            local_lbl = lbl_3d[ez0:ez1, ex0:ex1, ey0:ey1]
            touch_labels = {int(v) for v in np.unique(local_lbl[dil]) if int(v) in real_bids}
            if touch_labels:
                candidates = [pt for pt, bid in real_entries if bid in touch_labels]
            else:
                candidates = [pt for pt, _ in real_entries]
            nearest = min(candidates, key=lambda p: (p.x - gx) ** 2 + (p.y - gy) ** 2)
            nearest.volume_mm3 += g_vol
            nearest.pump_e_mm = float(nearest.volume_mm3 / rcfg.pump_mm3_per_e_mm)

    dosing_points = [pt for pt, _ in real_entries if pt.volume_mm3 > 1e-4]
    return order_points_tsp(dosing_points), bbox_xy
