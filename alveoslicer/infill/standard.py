"""
Standard PrusaSlicer-Class 2D/3D Density-Driven Infill & Hatch Generators.

Provides reusable planar hatching primitives (`hatch_polygon`, `honeycomb_hatch_polygon`,
`hilbert_curve_points`) plus the full 17-pattern standard sparse infill generator
(`generate_standard_infill_layer`) supporting:
  rectilinear, aligned_rectilinear, grid, triangles, stars, cubic, adaptive_cubic,
  support_cubic, line, concentric, honeycomb, 3d_honeycomb, gyroid, hilbert_curve,
  archimedean_chords, octagram_spiral, and lightning.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import LineString, Polygon

from ..core.config import SlicerConfig
from ..core.geometry import (
    clean_geom,
    extract_polylines,
    iter_polygons,
    safe_buffer,
    safe_difference,
    safe_intersection,
)


def hatch_polygon(
    geom: shapely.Geometry,
    spacing: float,
    angle_deg: float,
    phase: float = 0.0,
) -> List[np.ndarray]:
    """Generate alternating monotonic parallel hatch lines clipped to ``geom`` at ``angle_deg``."""
    g = clean_geom(geom)
    if g.is_empty or g.area < 0.05:
        return []
    sp = max(0.3, float(spacing))
    rot = clean_geom(shapely.affinity.rotate(g, -angle_deg, origin=(0.0, 0.0)))
    if rot.is_empty:
        return []

    minx, miny, maxx, maxy = rot.bounds
    y_val = (
        math.floor((miny - phase) / sp) * sp + phase
        if phase != 0.0
        else miny + 0.5 * sp
    )
    lines: List[np.ndarray] = []
    flip = False
    while y_val <= maxy + 1e-6:
        inter = safe_intersection(rot, LineString([(minx - 2.0, y_val), (maxx + 2.0, y_val)]))
        for coords in extract_polylines(inter):
            if flip:
                coords = coords[::-1]
            unrot = shapely.affinity.rotate(LineString(coords), angle_deg, origin=(0.0, 0.0))
            lines.append(np.asarray(unrot.coords, dtype=np.float64))
        y_val += sp
        flip = not flip
    return lines


def honeycomb_hatch_polygon(geom: shapely.Geometry, spacing: float) -> List[np.ndarray]:
    """Generate a PrusaSlicer-style 2D hexagonal honeycomb polyline grid clipped to ``geom``."""
    g = clean_geom(geom)
    if g.is_empty or g.area < 0.05:
        return []
    minx, miny, maxx, maxy = g.bounds
    s = max(1.0, float(spacing))
    dx, dy = s * 0.5, s * math.sqrt(3.0) * 0.5
    lines: List[np.ndarray] = []
    row, y = 0, miny - dy
    while y <= maxy + dy:
        pts: List[Tuple[float, float]] = []
        x, col = minx - s, 0
        while x <= maxx + 2.0 * s:
            y_off = 0.0 if ((col + row) % 2 == 0) else (dy * 0.5)
            pts.extend(((x, y + y_off), (x + dx, y + y_off)))
            x += 1.5 * s
            col += 1
        if len(pts) >= 2:
            lines.extend(extract_polylines(safe_intersection(g, LineString(pts))))
        y += dy
        row += 1
    return lines


def hilbert_curve_points(order: int) -> np.ndarray:
    """Return normalized ``[0, 1]^2`` polyline coordinates of a Hilbert space-filling curve."""
    n = 1 << order
    pts = np.empty((n * n, 2), dtype=np.float64)
    for d in range(n * n):
        t, x, y, s = d, 0, 0, 1
        while s < n:
            rx = 1 & (t // 2)
            ry = 1 & (t ^ rx)
            if ry == 0:
                if rx == 1:
                    x, y = s - 1 - x, s - 1 - y
                x, y = y, x
            x += s * rx
            y += s * ry
            t //= 4
            s *= 2
        pts[d] = ((x + 0.5) / n, (y + 0.5) / n)
    return pts


def generate_standard_infill_layer(
    region: shapely.Geometry,
    z_mid: float,
    layer_idx0: int,
    lw: float,
    cfg: SlicerConfig,
    roof_support_mask: Optional[shapely.Geometry] = None,
) -> Tuple[List[np.ndarray], shapely.Geometry]:
    """
    Generate standard PrusaSlicer density-driven 2D/3D sparse infill polylines inside ``region``.
    Returns ``(infill_polylines, solid_diaphragm_geom_or_empty)``.
    """
    region = clean_geom(region)
    if region.is_empty or region.area < lw * lw:
        return [], Polygon()

    icfg, fcfg = cfg.infill, cfg.fff
    density = max(0.0, min(1.0, float(icfg.fill_density_pct) / 100.0))
    if density <= 1e-4:
        return [], Polygon()

    # Check forced-solid rules (100% density, periodic solid diaphragm, or narrow-island threshold)
    comb = max(1, int(fcfg.combine_infill_every_n_layers))
    force_solid = (
        density >= 0.995
        or (icfg.solid_infill_every_layers > 0 and ((layer_idx0 + 1) % icfg.solid_infill_every_layers == 0))
        or (icfg.solid_infill_threshold_area_mm2 > 0.0 and region.area <= icfg.solid_infill_threshold_area_mm2)
    )
    if (not force_solid) and comb > 1 and (layer_idx0 % comb != 0):
        return [], Polygon()

    # Optional "Only infill where needed" / Lightning roof projection clipping
    active_region = region
    if (icfg.only_infill_where_needed or icfg.standard_fill_pattern == "lightning") and not force_solid:
        if roof_support_mask is not None and not roof_support_mask.is_empty:
            active_region = clean_geom(safe_intersection(region, roof_support_mask))
            if active_region.is_empty or active_region.area < lw * lw:
                return [], Polygon()
        elif icfg.only_infill_where_needed:
            return [], Polygon()

    # Slight perimeter overlap for mechanical bonding
    ov_mm = (max(0.0, min(50.0, float(fcfg.infill_overlap_pct))) / 100.0) * 0.5 * lw
    fill_reg = safe_buffer(active_region, ov_mm, join_style=2) if ov_mm > 1e-4 else active_region
    if fill_reg.is_empty:
        return [], Polygon()

    base_ang = float(fcfg.infill_angle_deg)
    if force_solid:
        ang = base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)
        return hatch_polygon(fill_reg, lw, ang), fill_reg

    pat = str(icfg.standard_fill_pattern or "gyroid").lower()
    spacing = max(lw, lw / max(density, 0.01))
    lines: List[np.ndarray] = []

    if pat in ("rectilinear", "line"):
        lines.extend(hatch_polygon(fill_reg, spacing, base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)))
    elif pat == "aligned_rectilinear":
        lines.extend(hatch_polygon(fill_reg, spacing, base_ang))
    elif pat == "grid":
        sp2 = max(lw * 1.2, spacing * 2.0)
        lines.extend(hatch_polygon(fill_reg, sp2, base_ang))
        lines.extend(hatch_polygon(fill_reg, sp2, base_ang + 90.0))
    elif pat == "triangles":
        sp3 = max(lw * 1.4, spacing * 3.0)
        for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
            lines.extend(hatch_polygon(fill_reg, sp3, a))
    elif pat == "stars":
        sp3 = max(lw * 1.4, spacing * 3.0)
        lines.extend(hatch_polygon(fill_reg, sp3, base_ang, phase=0.0))
        lines.extend(hatch_polygon(fill_reg, sp3, base_ang + 60.0, phase=0.0))
        lines.extend(hatch_polygon(fill_reg, sp3, base_ang + 120.0, phase=0.5 * sp3))
    elif pat in ("cubic", "adaptive_cubic", "support_cubic"):
        sp3 = max(lw * 1.4, spacing * 3.0)
        phase = (z_mid * math.sqrt(2.0)) % sp3
        if pat == "adaptive_cubic":
            inner_core = safe_buffer(fill_reg, -sp3 * 1.2, join_style=2)
            shell_band = safe_difference(fill_reg, inner_core) if not inner_core.is_empty else fill_reg
            for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
                lines.extend(hatch_polygon(shell_band, sp3, a, phase=phase))
                if not inner_core.is_empty:
                    lines.extend(hatch_polygon(inner_core, sp3 * 2.0, a, phase=phase))
        else:
            for a in (base_ang, base_ang + 60.0, base_ang + 120.0):
                lines.extend(hatch_polygon(fill_reg, sp3, a, phase=phase))
    elif pat in ("honeycomb", "3d_honeycomb"):
        sp_hex = max(lw * 1.5, spacing * 1.65)
        if pat == "3d_honeycomb":
            shift = 0.35 * sp_hex * math.sin(2.0 * math.pi * z_mid / max(sp_hex * 1.6, 2.0))
            raw = honeycomb_hatch_polygon(shapely.affinity.translate(fill_reg, xoff=-shift, yoff=-0.5 * shift), sp_hex)
            for arr in raw:
                shifted_arr = arr.copy()
                shifted_arr[:, 0] += shift
                shifted_arr[:, 1] += 0.5 * shift
                lines.append(shifted_arr)
        else:
            lines.extend(honeycomb_hatch_polygon(fill_reg, sp_hex))
    elif pat == "gyroid":
        from skimage import measure as sk_measure

        wavelength = max(lw * 4.0, spacing * 2.05)
        k_w = 2.0 * math.pi / wavelength
        bx0, by0, bx1, by1 = fill_reg.bounds
        step_g = max(0.35, min(0.95, wavelength / 14.0))
        gx_arr = np.arange(bx0 - step_g, bx1 + 2.0 * step_g, step_g)
        gy_arr = np.arange(by0 - step_g, by1 + 2.0 * step_g, step_g)
        if len(gx_arr) >= 3 and len(gy_arr) >= 3:
            xx, yy = np.meshgrid(gx_arr, gy_arr, indexing="ij")
            kz = k_w * z_mid
            g_val = (
                np.sin(k_w * xx) * np.cos(k_w * yy)
                + np.sin(k_w * yy) * math.cos(kz)
                + math.sin(kz) * np.cos(k_w * xx)
            )
            for contour in sk_measure.find_contours(g_val, level=0.0):
                if len(contour) < 2:
                    continue
                ls = LineString(np.column_stack([bx0 - step_g + contour[:, 0] * step_g, by0 - step_g + contour[:, 1] * step_g]))
                lines.extend(extract_polylines(safe_intersection(fill_reg, ls)))
    elif pat == "concentric":
        cur = safe_buffer(fill_reg, -0.5 * lw, join_style=1)
        iters = 0
        while not cur.is_empty and cur.area > lw * lw and iters < 80:
            for poly in iter_polygons(cur):
                lines.append(np.asarray(poly.exterior.coords, dtype=np.float64))
                lines.extend(np.asarray(h.coords, dtype=np.float64) for h in poly.interiors)
            cur = safe_buffer(cur, -spacing, join_style=1)
            iters += 1
    elif pat == "hilbert_curve":
        bx0, by0, bx1, by1 = fill_reg.bounds
        span = max(bx1 - bx0, by1 - by0, spacing * 2.0)
        order = max(2, min(6, int(math.ceil(math.log2(max(4.0, span / max(spacing, 0.5)))))))
        h_pts = hilbert_curve_points(order)
        h_pts[:, 0] = bx0 + h_pts[:, 0] * span
        h_pts[:, 1] = by0 + h_pts[:, 1] * span
        lines.extend(extract_polylines(safe_intersection(fill_reg, LineString(h_pts))))
    elif pat == "archimedean_chords":
        bx0, by0, bx1, by1 = fill_reg.bounds
        cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
        max_r = math.hypot(bx1 - bx0, by1 - by0) * 0.55
        r = spacing
        while r <= max_r:
            n_seg = max(24, min(96, int(2.0 * math.pi * r / max(0.8, spacing * 0.4))))
            th = np.linspace(0.0, 2.0 * math.pi, n_seg + 1)
            ring = LineString(np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)]))
            lines.extend(extract_polylines(safe_intersection(fill_reg, ring)))
            r += spacing
    elif pat == "octagram_spiral":
        bx0, by0, bx1, by1 = fill_reg.bounds
        cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
        max_r = math.hypot(bx1 - bx0, by1 - by0) * 0.65
        r = spacing
        while r <= max_r:
            pts = [
                (
                    cx + (r if j % 2 == 0 else r * 0.58) * math.cos(j * (math.pi / 8.0) + math.radians(base_ang)),
                    cy + (r if j % 2 == 0 else r * 0.58) * math.sin(j * (math.pi / 8.0) + math.radians(base_ang)),
                )
                for j in range(17)
            ]
            lines.extend(extract_polylines(safe_intersection(fill_reg, LineString(pts))))
            r += spacing * 1.25
    elif pat == "lightning":
        sp_l = max(lw * 1.6, spacing * 1.8)
        lines.extend(hatch_polygon(fill_reg, sp_l, base_ang + (35.0 if (layer_idx0 % 2 == 0) else -35.0)))
    else:
        lines.extend(hatch_polygon(fill_reg, spacing, base_ang if (layer_idx0 % 2 == 0) else (base_ang + 90.0)))

    return lines, Polygon()
