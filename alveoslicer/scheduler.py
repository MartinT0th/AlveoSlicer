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


_CONN_2D_8 = np.ones((3, 3), dtype=np.int32)
_CONN_3D_26 = np.ones((3, 3, 3), dtype=np.int32)


def _slab_3d_needle_accessible(
    layers: List[SlicedLayer],
    start_idx0: int,
    end_idx0: int,
    ghost_min_px: int = 2,
) -> bool:
    """
    Verify every non-ghost 3D connected resin basin in [start_idx0..end_idx0]
    has needle access on the top slice.
    """
    top_void = layers[end_idx0].void_mask
    top_acc = layers[end_idx0].accessible_mask
    if not np.any(top_void):
        return True

    lbl_2d, n_2d = ndimage.label(top_void, structure=_CONN_2D_8)
    all_2d_ok = True
    for i in range(1, n_2d + 1):
        m = lbl_2d == i
        if int(np.count_nonzero(m)) <= ghost_min_px:
            continue
        if not np.any(top_acc[m]):
            all_2d_ok = False
            break
    if all_2d_ok:
        return True

    slab = np.stack([layers[i].void_mask for i in range(start_idx0, end_idx0 + 1)], axis=0)
    lbl_3d, n_3d = ndimage.label(slab, structure=_CONN_3D_26)
    top_slice_lbl = lbl_3d[-1]
    for cid in range(1, n_3d + 1):
        top_c = top_slice_lbl == cid
        if int(np.count_nonzero(top_c)) <= ghost_min_px:
            continue
        if not np.any(top_acc[top_c]):
            return False
    return True


def compute_optimal_cure_schedule(
    layers: List[SlicedLayer], cfg: SlicerConfig
) -> List[CureCycle]:
    """
    Solve the globally optimal cure schedule across all layers via Dynamic Programming.
    Minimizes ``hard_roof_closure`` occurrences by globally rebalancing earlier injection
    cadences and ignoring sub-needle 1-2 px raster staircase ghost slivers.
    """
    rcfg = cfg.resin
    h = cfg.fff.layer_height
    rp = cfg.slice_raster_pitch_mm
    px_area = rp * rp
    px_vol_mm3 = px_area * h
    num_layers = len(layers)

    x_max_beer = int(math.floor((rcfg.max_cure_depth_mm * rcfg.cure_depth_safety_eta) / h))
    x_max_eff = max(1, min(rcfg.x_max_layers, x_max_beer))
    x_min_eff = max(1, min(rcfg.x_min_layers, x_max_eff))

    ghost_min_area = max(0.0, float(getattr(rcfg, "ghost_island_min_area_mm2", 0.85)))
    ghost_min_px = max(2, int(math.ceil(ghost_min_area / max(px_area, 1e-6)))) if ghost_min_area > 0.0 else 0

    bleed_px = int(round(rcfg.uv_shadow_bleed_mm / rp))
    bleed_disk = disk_kernel(bleed_px) if bleed_px > 0 else None

    # Lexicographic-tier penalty so the global DP ALWAYS prefers adjusting earlier pump layers
    # (e.g., 5+4+4 instead of 6+6+1) over emitting an avoidable hard_roof_closure cycle.
    hard_roof_base_penalty = 5000.0

    inf = float("inf")
    dp_cost = [inf] * (num_layers + 1)
    dp_prev = [-1] * (num_layers + 1)
    dp_shadow = [0.0] * (num_layers + 1)
    dp_trigger = ["none"] * (num_layers + 1)
    dp_cost[0] = 0.0

    void_counts = [int(np.count_nonzero(ly.void_mask)) for ly in layers]
    last_void_layer = 0
    for idx_v, vc in enumerate(void_counts):
        if vc > ghost_min_px:
            last_void_layer = idx_v + 1

    for k in range(1, num_layers + 1):
        ly_k = layers[k - 1]
        if void_counts[k - 1] <= ghost_min_px:
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
                if void_counts[j_idx0] <= ghost_min_px:
                    break

                solid_above |= layers[j_idx0 + 1].solid_mask

                # Airlock-free monotonic buoyancy check (8-connected, immune to single-pixel raster ghosts)
                vent_seeds = layers[j_idx0].void_mask & air_vent_reach
                air_vent_reach = ndimage.binary_propagation(
                    vent_seeds, structure=_CONN_2D_8, mask=layers[j_idx0].void_mask
                )
                trapped_mask = layers[j_idx0].void_mask & (~air_vent_reach)
                trapped_px = int(np.count_nonzero(trapped_mask))
                if trapped_px > 0:
                    if trapped_px > max(ghost_min_px, int(0.02 * void_counts[j_idx0])):
                        break
                    t_lbl, n_t = ndimage.label(trapped_mask, structure=_CONN_2D_8)
                    if n_t > 0:
                        t_sizes = np.bincount(t_lbl.ravel())[1:]
                        if int(t_sizes.max()) > ghost_min_px:
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

            if p < k - 1 and not _slab_3d_needle_accessible(layers, p, k - 1, ghost_min_px=ghost_min_px):
                break

            shadow_ratio = cum_shadow_px / max(1, cum_void_px)
            if p < k - 1 and shadow_ratio > rcfg.max_shadow_vol_ratio:
                break

            span = k - p
            shortfall = max(0, x_min_eff - span)
            shadow_mm3 = cum_shadow_px * px_vol_mm3
            is_final = (k == num_layers) or (k == last_void_layer)
            is_hard_roof = (shortfall > 0) and (not is_final)

            # Dominant penalty for avoidable hard_roof_closure so DP shifts earlier pump cadence
            short_pen = rcfg.dp_w_short * (shortfall * shortfall)
            if is_hard_roof:
                short_pen += hard_roof_base_penalty

            # Gentle tie-breaker prefers balanced spans near x_max_eff when roof count is tied
            cadence_bias = 0.08 * float(x_max_eff - span)
            step_cost = (
                rcfg.dp_w_cycle
                + short_pen
                + rcfg.dp_w_shadow * shadow_mm3
                + cadence_bias
            )
            total_cost = dp_cost[p] + step_cost

            if total_cost < dp_cost[k]:
                trig = "optimal_cadence"
                if is_final:
                    trig = "final_top_layer"
                elif is_hard_roof:
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
