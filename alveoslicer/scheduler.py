"""Stage 4: Global Dynamic Programming cure-cycle scheduler enforcing Beer-Lambert, airlock, needle, and UV shadow laws."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

import numpy as np
from scipy import ndimage

from .config import SlicerConfig
from .geometry import disk_kernel
from .slicing import SlicedLayer


@dataclass
class CureCycle:
    """Scheduled photopolymer injection + UV curing cycle spanning [start_layer..end_layer] (1-indexed)."""

    start_layer: int
    end_layer: int
    slab_thickness_mm: float
    shadowed_vol_mm3: float
    trigger: str


def _slab_3d_needle_accessible(layers: List[SlicedLayer], start_idx0: int, end_idx0: int) -> bool:
    """Verify every 3D connected resin basin in [start_idx0..end_idx0] has needle access on the top slice."""
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
    """Solve the globally optimal cure schedule across all layers via Dynamic Programming."""
    rcfg = cfg.resin
    h = cfg.fff.layer_height
    rp = cfg.slice_raster_pitch_mm
    px_vol_mm3 = rp * rp * h
    num_layers = len(layers)

    x_max_beer = int(math.floor((rcfg.max_cure_depth_mm * rcfg.cure_depth_safety_eta) / h))
    x_max_eff = max(1, min(rcfg.x_max_layers, x_max_beer))

    bleed_px = int(round(rcfg.uv_shadow_bleed_mm / rp))
    bleed_disk = disk_kernel(bleed_px) if bleed_px > 0 else None

    inf = float("inf")
    dp_cost = [inf] * (num_layers + 1)
    dp_prev = [-1] * (num_layers + 1)
    dp_shadow = [0.0] * (num_layers + 1)
    dp_trigger = ["none"] * (num_layers + 1)
    dp_cost[0] = 0.0

    void_counts = [int(np.count_nonzero(ly.void_mask)) for ly in layers]

    for k in range(1, num_layers + 1):
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

                # Airlock-free monotonic buoyancy check
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

            if dp_cost[p] == inf:
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
                if k == num_layers:
                    trig = "final_top_layer"
                elif span < rcfg.x_min_layers:
                    trig = "hard_roof_closure"
                dp_cost[k] = total_cost
                dp_prev[k] = p
                dp_shadow[k] = shadow_mm3
                dp_trigger[k] = trig

    schedule: List[CureCycle] = []
    cur = num_layers
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
