"""Stage 6a: Exact floating-point vector FFF toolpath generation for skirt/brim, wipe tower, shells, cellular walls, caps, and supports."""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString

from ..core.config import SlicerConfig
from ..core.geometry import clean_geom, extract_polylines, iter_polygons, safe_buffer, safe_intersection
from ..slicing.layer import SlicedLayer

PRUSA_TYPE_MAP = {
    "SKIRT_BRIM": "Skirt/Brim",
    "WIPE_TOWER": "Wipe tower",
    "SHELL_EXT_PERIM": "External perimeter",
    "SHELL_PERIM": "Perimeter",
    "SHELL_HOLE": "Perimeter",
    "SHELL_FILL": "Solid infill",
    "SHELL_TOP_FILL": "Top solid infill",
    "SHELL_IRONING": "Ironing",
    "INSIDE_PERIM": "Internal infill",
    "CELL_CAP_PERIM": "Solid infill",
    "CELL_CAP_HOLE": "Solid infill",
    "CELL_CAP_FILL": "Solid infill",
    "DRY_SUPPORT": "Support material",
    "EXT_SUPPORT": "Support material",
    "EXT_SUPPORT_INTERFACE": "Support material interface",
}


def _align_loop_seam(
    coords: np.ndarray, seam_mode: str, layer_idx: int, bed_y: float
) -> np.ndarray:
    """Rotate a closed loop's starting vertex to implement PrusaSlicer seam placement."""
    del bed_y
    if len(coords) < 4:
        return coords
    ring = coords[:-1]
    n = len(ring)
    if n < 3:
        return coords

    if seam_mode == "rear":
        best = int(np.argmax(ring[:, 1]))
    elif seam_mode == "random":
        best = int((layer_idx * 2654435761) % n)
    elif seam_mode == "aligned":
        scores = ring[:, 1] + 0.35 * ring[:, 0]
        best = int(np.argmax(scores))
    else:
        return coords

    if best == 0:
        return coords
    rolled = np.roll(ring, -best, axis=0)
    return np.vstack([rolled, rolled[0:1]])


def _apply_fuzzy_skin(
    coords: np.ndarray,
    point_dist_mm: float,
    thickness_mm: float,
    layer_idx: int,
) -> np.ndarray:
    """Resample a closed contour and apply deterministic PrusaSlicer-style fuzzy skin normal jitter."""
    if len(coords) < 3 or thickness_mm <= 1e-4 or point_dist_mm <= 1e-4:
        return coords
    ls = LineString(coords)
    total_len = float(ls.length)
    if total_len < point_dist_mm * 1.5:
        return coords

    n_pts = max(4, int(round(total_len / point_dist_mm)))
    dists = np.linspace(0.0, total_len, n_pts, endpoint=False)
    pts = np.array([ls.interpolate(d).coords[0][:2] for d in dists], dtype=np.float64)

    # Compute local outward normals via central differences
    prev_pts = np.roll(pts, 1, axis=0)
    next_pts = np.roll(pts, -1, axis=0)
    tang = next_pts - prev_pts
    norms = np.linalg.norm(tang, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    tang = tang / norms
    normals = np.column_stack([tang[:, 1], -tang[:, 0]])

    rng = np.random.default_rng(seed=(layer_idx * 7919 + n_pts * 104729) & 0xFFFFFFFF)
    offsets = rng.uniform(-thickness_mm, thickness_mm, size=(n_pts, 1))
    jittered = pts + normals * offsets
    return np.vstack([jittered, jittered[0:1]])


def _emit_vector_region(
    region: shapely.Geometry,
    tag: str,
    num_perims: int,
    fill_angle_deg: float,
    lw_ext: float,
    lw_perim: float,
    lw_fill: float,
    paths: List[Tuple[str, np.ndarray]],
    external_first: bool = False,
    seam_mode: str = "aligned",
    layer_idx: int = 1,
    bed_y: float = 220.0,
    fill_tag_override: str = "",
    infill_overlap_pct: float = 25.0,
    fuzzy_mode: str = "none",
    fuzzy_point_dist_mm: float = 0.8,
    fuzzy_thickness_mm: float = 0.3,
    solid_fill_pattern: str = "monotonic",
) -> None:
    """Convert a 2D Shapely polygon region into concentric perimeter loops and monotonic solid infill lines."""
    if region is None or region.is_empty:
        return

    perim_rings: List[Tuple[str, List[np.ndarray]]] = []
    cur = safe_buffer(clean_geom(region), -0.5 * lw_ext, join_style=2)
    overlap_factor = max(0.50, min(1.0, 1.0 - (infill_overlap_pct / 100.0) * 0.5))

    for p_i in range(max(1, num_perims)):
        if cur.is_empty:
            break
        is_ext = tag == "SHELL" and p_i == 0
        ext_tag = f"{tag}_EXT_PERIM" if is_ext else f"{tag}_PERIM"
        apply_fuzzy_ext = tag == "SHELL" and (
            (fuzzy_mode == "outside_walls" and is_ext) or (fuzzy_mode == "all_walls")
        )
        apply_fuzzy_hole = tag == "SHELL" and (fuzzy_mode == "all_walls")

        ring_ext: List[np.ndarray] = []
        ring_holes: List[np.ndarray] = []
        for poly in iter_polygons(cur):
            c_ext = _align_loop_seam(np.asarray(poly.exterior.coords), seam_mode, layer_idx, bed_y)
            if apply_fuzzy_ext:
                c_ext = _apply_fuzzy_skin(c_ext, fuzzy_point_dist_mm, fuzzy_thickness_mm, layer_idx)
            ring_ext.append(c_ext)
            for interior in poly.interiors:
                c_h = _align_loop_seam(np.asarray(interior.coords), seam_mode, layer_idx, bed_y)
                if apply_fuzzy_hole:
                    c_h = _apply_fuzzy_skin(c_h, fuzzy_point_dist_mm, fuzzy_thickness_mm, layer_idx)
                ring_holes.append(c_h)
        perim_rings.append((ext_tag, ring_ext))
        if ring_holes:
            perim_rings.append((f"{tag}_HOLE", ring_holes))

        step = lw_perim if p_i + 1 < num_perims else overlap_factor * lw_perim
        cur = safe_buffer(cur, -step, join_style=2)

    if not external_first and len(perim_rings) > 1:
        perim_rings.reverse()

    for r_tag, loops in perim_rings:
        for lp in loops:
            paths.append((r_tag, lp))

    if not cur.is_empty and cur.area > lw_fill * lw_fill:
        fill_tag = fill_tag_override or f"{tag}_FILL"
        pat = str(solid_fill_pattern or "monotonic").lower()
        if pat == "concentric":
            c_cur = safe_buffer(cur, -0.5 * lw_fill, join_style=1)
            iters = 0
            while not c_cur.is_empty and c_cur.area > lw_fill * lw_fill * 0.4 and iters < 100:
                for poly in iter_polygons(c_cur):
                    paths.append((fill_tag, np.asarray(poly.exterior.coords)))
                    for hole in poly.interiors:
                        paths.append((fill_tag, np.asarray(hole.coords)))
                c_cur = safe_buffer(c_cur, -lw_fill, join_style=1)
                iters += 1
        elif pat in ("archimedean_chords", "octagram_spiral"):
            bx0, by0, bx1, by1 = cur.bounds
            cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
            max_r = math.hypot(bx1 - bx0, by1 - by0) * 0.6
            r = lw_fill
            while r <= max_r:
                if pat == "archimedean_chords":
                    n_seg = max(24, min(96, int(2.0 * math.pi * r / max(0.6, lw_fill * 1.5))))
                    th = np.linspace(0.0, 2.0 * math.pi, n_seg + 1)
                    ring = LineString(np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)]))
                else:
                    pts = []
                    for j in range(17):
                        ang = j * (math.pi / 8.0) + math.radians(fill_angle_deg)
                        rad = r if (j % 2 == 0) else (r * 0.62)
                        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
                    ring = LineString(pts)
                inter = safe_intersection(cur, ring)
                for coords in extract_polylines(inter):
                    paths.append((fill_tag, coords))
                r += lw_fill
        else:
            rot = clean_geom(shapely.affinity.rotate(cur, -fill_angle_deg, origin=(0.0, 0.0)))
            if rot.is_empty:
                return
            minx, miny, maxx, maxy = rot.bounds
            y_val = miny + 0.5 * lw_fill
            flip = False
            monotonic = pat in ("monotonic", "monotonic_lines")
            while y_val <= maxy:
                seg = LineString([(minx - 1.0, y_val), (maxx + 1.0, y_val)])
                inter = safe_intersection(rot, seg)
                for coords in extract_polylines(inter):
                    if (not monotonic) and flip:
                        coords = coords[::-1]
                    unrot = shapely.affinity.rotate(LineString(coords), fill_angle_deg, origin=(0.0, 0.0))
                    paths.append((fill_tag, np.asarray(unrot.coords)))
                y_val += lw_fill
                flip = not flip


def _emit_ironing_pass(
    top_region: shapely.Geometry,
    spacing_mm: float,
    angle_deg: float,
    inset_mm: float,
    paths: List[Tuple[str, np.ndarray]],
) -> None:
    """Emit PrusaSlicer-style micro-extrusion ironing scanlines across exposed top solid surfaces."""
    if top_region is None or top_region.is_empty:
        return
    iron_area = safe_buffer(clean_geom(top_region), -inset_mm, join_style=2)
    if iron_area.is_empty or iron_area.area < 0.25:
        return

    step = max(0.05, float(spacing_mm))
    rot = clean_geom(shapely.affinity.rotate(iron_area, -angle_deg, origin=(0.0, 0.0)))
    if rot.is_empty:
        return
    minx, miny, maxx, maxy = rot.bounds
    y_val = miny + 0.5 * step
    flip = False
    while y_val <= maxy:
        seg = LineString([(minx - 1.0, y_val), (maxx + 1.0, y_val)])
        inter = safe_intersection(rot, seg)
        for coords in extract_polylines(inter):
            if flip:
                coords = coords[::-1]
            unrot = shapely.affinity.rotate(LineString(coords), angle_deg, origin=(0.0, 0.0))
            paths.append(("SHELL_IRONING", np.asarray(unrot.coords)))
        y_val += step
        flip = not flip


def generate_fff_paths_for_layer(
    layer: SlicedLayer, xs: np.ndarray, ys: np.ndarray, cfg: SlicerConfig
) -> List[Tuple[str, np.ndarray]]:
    """Generate exact vector FFF polylines for a single sliced layer (zero rasterization)."""
    del xs, ys  # Retained in public signature for engine/UI contract compatibility

    f = cfg.fff
    is_first = layer.layer_idx == 1
    lw_def = max(0.15, float(f.first_layer_line_width if is_first else f.line_width))
    lw_ext = max(0.15, float(f.first_layer_line_width if is_first else (f.external_perimeter_line_width or lw_def)))
    lw_perim = max(0.15, float(f.first_layer_line_width if is_first else (f.perimeter_line_width or lw_def)))
    lw_solid = max(
        0.15,
        float(
            f.first_layer_line_width
            if is_first
            else (f.top_infill_line_width if layer.is_top_solid_layer else (f.solid_infill_line_width or lw_def))
        ),
    )

    paths: List[Tuple[str, np.ndarray]] = []
    base_angle = float(f.infill_angle_deg)
    angle = base_angle if (layer.layer_idx % 2 == 1) else (base_angle + 90.0)

    # 0. Skirt & Brim (priming loops & adhesion brim first!)
    for ln in layer.skirt_brim_lines:
        paths.append(("SKIRT_BRIM", ln))

    # 0b. Purge / Wipe Tower
    for ln in layer.wipe_tower_lines:
        paths.append(("WIPE_TOWER", ln))

    # Optional: Print internal infill before outer perimeters (PrusaSlicer infill_before_perimeters)
    if cfg.infill.infill_before_perimeters:
        for ln in layer.inside_perimeter_lines:
            paths.append(("INSIDE_PERIM", ln))

    # 1. Exact outer & inner watertight shells + top/bottom skins
    fill_override = "SHELL_TOP_FILL" if layer.is_top_solid_layer else "SHELL_FILL"
    solid_pat = (
        cfg.infill.top_fill_pattern
        if layer.is_top_solid_layer
        else (cfg.infill.bottom_fill_pattern if is_first else "monotonic")
    )
    eff_perims = 1 if f.spiral_vase else f.perimeters
    _emit_vector_region(
        layer.shell_geom,
        "SHELL",
        eff_perims,
        angle,
        lw_ext=lw_ext,
        lw_perim=lw_perim,
        lw_fill=lw_solid,
        paths=paths,
        external_first=f.external_perimeters_first,
        seam_mode=f.seam_position,
        layer_idx=layer.layer_idx,
        bed_y=cfg.printer.bed_size_y,
        fill_tag_override=fill_override,
        infill_overlap_pct=f.infill_overlap_pct,
        fuzzy_mode=f.fuzzy_skin_mode,
        fuzzy_point_dist_mm=f.fuzzy_skin_point_dist_mm,
        fuzzy_thickness_mm=f.fuzzy_skin_thickness_mm,
        solid_fill_pattern=solid_pat,
    )

    # 2. Anchored cellular / standard internal infill lines
    if not cfg.infill.infill_before_perimeters:
        for ln in layer.inside_perimeter_lines:
            paths.append(("INSIDE_PERIM", ln))

    # 3. Solid flat horizontal floor/ceiling bridge caps & sloped ramp reinforcements
    if not layer.cell_cap_geom.is_empty:
        _emit_vector_region(
            layer.cell_cap_geom,
            "CELL_CAP",
            1,
            angle + 90.0,
            lw_ext=lw_def,
            lw_perim=lw_def,
            lw_fill=lw_solid,
            paths=paths,
            external_first=False,
            seam_mode=f.seam_position,
            layer_idx=layer.layer_idx,
            bed_y=cfg.printer.bed_size_y,
            infill_overlap_pct=f.infill_overlap_pct,
        )

    # 4. Dry-core support lines (HollowShell mode)
    for ln in layer.dry_support_lines:
        paths.append(("DRY_SUPPORT", ln))

    # 5. External overhang supports & top/bottom support interface layers
    for ln in layer.ext_support_lines:
        paths.append(("EXT_SUPPORT", ln))
    for ln in layer.ext_support_interface_lines:
        paths.append(("EXT_SUPPORT_INTERFACE", ln))

    # 6. PrusaSlicer Top-Surface Ironing pass
    if f.ironing_enable and not f.spiral_vase:
        if layer.top_surface_geom is not None and not layer.top_surface_geom.is_empty:
            iron_region = (
                layer.shell_geom
                if layer.is_top_solid_layer
                else safe_intersection(layer.shell_geom, layer.top_surface_geom)
            )
            _emit_ironing_pass(
                iron_region,
                spacing_mm=f.ironing_spacing_mm,
                angle_deg=angle + 45.0,
                inset_mm=0.5 * lw_ext,
                paths=paths,
            )

    return paths
