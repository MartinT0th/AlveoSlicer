"""Pure CPU-side OpenGL vertex buffer builders for meshes, toolpaths, resin cavities, beds, and gizmos.

Every builder returns contiguous ``float32`` arrays formatted for either:
  - Lit shader VBOs: ``(V, 9)`` -> ``[x, y, z, nx, ny, nz, r, g, b]``
  - Line shader VBOs: ``(V, 6)`` -> ``[x, y, z, r, g, b]``
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
import shapely
import trimesh

from ...core.geometry import iter_polygons
from ...gcode.parser import FEATURE_COLORS, FEATURE_TYPES
from ...pipeline import SliceResult
from ..styling.theme import ThemePalette
from .math_utils import euler_matrix_xyz

# 8-vertex rounded obround (stadium/capsule) cross-section profile around (+n_left, +Z)
_RING_U = np.array([0.56, 0.98, 0.98, 0.56, -0.56, -0.98, -0.98, -0.56], dtype=np.float32)
_RING_V = np.array([1.00, 0.52, -0.52, -1.00, -1.00, -0.52, 0.52, 1.00], dtype=np.float32)
_RING_NU = np.array([0.22, 0.86, 0.86, 0.22, -0.22, -0.86, -0.86, -0.22], dtype=np.float32)
_RING_NV = np.array([0.975, 0.51, -0.51, -0.975, -0.975, -0.51, 0.51, 0.975], dtype=np.float32)
_RING_K = np.arange(8, dtype=np.int32)
_RING_KN = (np.arange(8, dtype=np.int32) + 1) % 8


def mesh_to_lit_buffer(
    mesh: trimesh.Trimesh,
    color: Tuple[float, float, float],
    paint_support: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Convert a ``trimesh.Trimesh`` into interleaved ``(V, 9)`` float32 triangles with paint-on support colors."""
    if mesh is None or len(mesh.faces) == 0:
        return np.zeros((0, 9), dtype=np.float32)
    tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float32)
    fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-6)

    n_f = len(tris)
    buf = np.empty((n_f, 3, 9), dtype=np.float32)
    buf[:, :, 0:3] = tris
    buf[:, :, 3:6] = fn[:, None, :]

    face_cols = np.broadcast_to(np.array(color, dtype=np.float32), (n_f, 3)).copy()
    if paint_support is not None and len(paint_support) == n_f:
        enf, blk = paint_support > 0, paint_support < 0
        if np.any(enf):
            face_cols[enf] = np.array([0.14, 0.84, 0.44], dtype=np.float32)  # Emerald Enforcer
        if np.any(blk):
            face_cols[blk] = np.array([0.92, 0.24, 0.24], dtype=np.float32)  # Crimson Blocker

    buf[:, :, 6:9] = face_cols[:, None, :]
    return buf.reshape(-1, 9)


def build_extrusion_beads(
    p0: np.ndarray,
    p1: np.ndarray,
    widths: np.ndarray,
    heights: np.ndarray,
    type_ids: np.ndarray,
    layers: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized rounded-obround continuous filament tube builder (PrusaSlicer-style).

    Joins continuous extrusion chains (including closed loops) with clamped miter
    cross-sections and emits rounded dome endcaps only at open chain endpoints.

    Returns ``(beads (N, 48, 9), caps (M, 24, 9), caps_seg_idx (M,))``.
    """
    n = len(p0)
    if n == 0:
        return (
            np.zeros((0, 48, 9), dtype=np.float32),
            np.zeros((0, 24, 9), dtype=np.float32),
            np.zeros((0,), dtype=np.int32),
        )

    palette = np.array([FEATURE_COLORS[t] for t in FEATURE_TYPES], dtype=np.float32)
    colors = palette[np.clip(type_ids, 0, len(palette) - 1)]

    d = (p1 - p0).astype(np.float32)
    len_xy = np.hypot(d[:, 0], d[:, 1])
    valid_len = len_xy > 1e-5
    safe_len = np.where(valid_len, len_xy, 1.0).astype(np.float32)
    dx = np.where(valid_len, d[:, 0] / safe_len, 1.0).astype(np.float32)
    dy = np.where(valid_len, d[:, 1] / safe_len, 0.0).astype(np.float32)
    nx, ny = -dy, dx

    hw = (0.5 * np.maximum(0.08, widths)).astype(np.float32)
    hz = (0.5 * np.maximum(0.05, heights)).astype(np.float32)

    c0 = p0.astype(np.float32).copy()
    c1 = p1.astype(np.float32).copy()
    c0[:, 2] -= hz
    c1[:, 2] -= hz

    # Detect continuous extrusion chains
    conn_prev = np.zeros(n, dtype=bool)
    if n > 1:
        gap = np.linalg.norm(p0[1:] - p1[:-1], axis=1)
        same = (
            (gap <= 0.025)
            & (type_ids[1:] == type_ids[:-1])
            & (np.abs(widths[1:] - widths[:-1]) <= 0.06)
            & (np.abs(heights[1:] - heights[:-1]) <= 0.06)
            & ((dx[:-1] * dx[1:] + dy[:-1] * dy[1:]) > -0.55)
        )
        if layers is not None and len(layers) == n:
            same &= layers[1:] == layers[:-1]
        conn_prev[1:] = same

    conn_next = np.zeros(n, dtype=bool)
    if n > 1:
        conn_next[:-1] = conn_prev[1:]

    m0x, m0y, m1x, m1y = nx.copy(), ny.copy(), nx.copy(), ny.copy()
    un0x, un0y, un1x, un1y = nx.copy(), ny.copy(), nx.copy(), ny.copy()

    def _apply_miter(prev_idx: np.ndarray, next_idx: np.ndarray) -> None:
        bx = nx[prev_idx] + nx[next_idx]
        by = ny[prev_idx] + ny[next_idx]
        blen = np.hypot(bx, by)
        ok_b = blen > 1e-4
        safe_b = np.where(ok_b, blen, 1.0)
        ubx = np.where(ok_b, bx / safe_b, nx[next_idx]).astype(np.float32)
        uby = np.where(ok_b, by / safe_b, ny[next_idx]).astype(np.float32)
        cos_half = np.maximum(ubx * nx[next_idx] + uby * ny[next_idx], 0.55)
        ms = np.minimum(1.0 / cos_half, 1.65).astype(np.float32)
        m1x[prev_idx] = m0x[next_idx] = (ubx * ms).astype(np.float32)
        m1y[prev_idx] = m0y[next_idx] = (uby * ms).astype(np.float32)
        un1x[prev_idx] = un0x[next_idx] = ubx
        un1y[prev_idx] = un0y[next_idx] = uby

    joint_i = np.flatnonzero(conn_prev)
    if len(joint_i) > 0:
        _apply_miter(joint_i - 1, joint_i)

    # Seamlessly miter closed perimeter loops
    starts, ends = np.flatnonzero(~conn_prev), np.flatnonzero(~conn_next)
    if len(starts) == len(ends) and len(starts) > 0:
        multi = ends > starts + 1
        if np.any(multi):
            s_idx, e_idx = starts[multi], ends[multi]
            closed = (
                (np.linalg.norm(p1[e_idx] - p0[s_idx], axis=1) <= 0.025)
                & ((dx[e_idx] * dx[s_idx] + dy[e_idx] * dy[s_idx]) > -0.55)
            )
            if np.any(closed):
                cs, ce = s_idx[closed], e_idx[closed]
                _apply_miter(ce, cs)
                conn_prev[cs] = True
                conn_next[ce] = True

    # Build 8-vertex rings & smooth outward normals
    u_hw = hw[:, None] * _RING_U[None, :]
    v_hz = hz[:, None] * _RING_V[None, :]

    r0 = np.empty((n, 8, 3), dtype=np.float32)
    r0[:, :, 0] = c0[:, 0:1] + m0x[:, None] * u_hw
    r0[:, :, 1] = c0[:, 1:2] + m0y[:, None] * u_hw
    r0[:, :, 2] = c0[:, 2:3] + v_hz

    r1 = np.empty((n, 8, 3), dtype=np.float32)
    r1[:, :, 0] = c1[:, 0:1] + m1x[:, None] * u_hw
    r1[:, :, 1] = c1[:, 1:2] + m1y[:, None] * u_hw
    r1[:, :, 2] = c1[:, 2:3] + v_hz

    n0 = np.empty((n, 8, 3), dtype=np.float32)
    n0[:, :, 0] = un0x[:, None] * _RING_NU[None, :]
    n0[:, :, 1] = un0y[:, None] * _RING_NU[None, :]
    n0[:, :, 2] = _RING_NV[None, :]

    n1 = np.empty((n, 8, 3), dtype=np.float32)
    n1[:, :, 0] = un1x[:, None] * _RING_NU[None, :]
    n1[:, :, 1] = un1y[:, None] * _RING_NU[None, :]
    n1[:, :, 2] = _RING_NV[None, :]

    out = np.empty((n, 8, 6, 9), dtype=np.float32)
    out[:, :, 0, 0:3], out[:, :, 0, 3:6] = r0[:, _RING_K, :], n0[:, _RING_K, :]
    out[:, :, 1, 0:3], out[:, :, 1, 3:6] = r1[:, _RING_K, :], n1[:, _RING_K, :]
    out[:, :, 2, 0:3], out[:, :, 2, 3:6] = r1[:, _RING_KN, :], n1[:, _RING_KN, :]
    out[:, :, 3, 0:3], out[:, :, 3, 3:6] = r0[:, _RING_K, :], n0[:, _RING_K, :]
    out[:, :, 4, 0:3], out[:, :, 4, 3:6] = r1[:, _RING_KN, :], n1[:, _RING_KN, :]
    out[:, :, 5, 0:3], out[:, :, 5, 3:6] = r0[:, _RING_KN, :], n0[:, _RING_KN, :]
    out[:, :, :, 6:9] = colors[:, None, None, :]
    beads = out.reshape(n, 48, 9)

    # Rounded dome endcaps at actual open start/end endpoints
    cap_chunks: List[np.ndarray] = []
    cap_idx_chunks: List[np.ndarray] = []

    def _emit_caps(indices: np.ndarray, centers: np.ndarray, rings: np.ndarray, norms: np.ndarray, sign: float) -> None:
        if len(indices) == 0:
            return
        m = len(indices)
        ext = (0.42 * hw[indices])[:, None]
        dv = np.stack([dx[indices], dy[indices], np.zeros(m, dtype=np.float32)], axis=1) * sign
        apex = centers[indices] + dv * ext
        cb = np.empty((m, 8, 3, 9), dtype=np.float32)
        cb[:, :, 0, 0:3], cb[:, :, 0, 3:6] = apex[:, None, :], dv[:, None, :]
        k_a, k_b = (_RING_K, _RING_KN) if sign < 0 else (_RING_KN, _RING_K)
        cb[:, :, 1, 0:3], cb[:, :, 1, 3:6] = rings[indices][:, k_a, :], norms[indices][:, k_a, :]
        cb[:, :, 2, 0:3], cb[:, :, 2, 3:6] = rings[indices][:, k_b, :], norms[indices][:, k_b, :]
        cb[:, :, :, 6:9] = colors[indices][:, None, None, :]
        cap_chunks.append(cb.reshape(m, 24, 9))
        cap_idx_chunks.append(indices.astype(np.int32))

    _emit_caps(np.flatnonzero(~conn_prev), c0, r0, n0, -1.0)
    _emit_caps(np.flatnonzero(~conn_next), c1, r1, n1, +1.0)

    if cap_chunks:
        return beads, np.concatenate(cap_chunks, axis=0), np.concatenate(cap_idx_chunks, axis=0)
    return beads, np.zeros((0, 24, 9), dtype=np.float32), np.zeros((0,), dtype=np.int32)


def triangulate_resin_layer_slab(
    geom,
    z_bot: float,
    z_top: float,
    color: Tuple[float, float, float],
    include_top: bool = True,
    include_bottom: bool = True,
) -> np.ndarray:
    """Triangulate a 2D Shapely photopolymer void polygon into a 3D lit ``(V, 9)`` slab."""
    if geom is None or geom.is_empty:
        return np.zeros((0, 9), dtype=np.float32)
    try:
        clean = geom.simplify(0.06, preserve_topology=True).buffer(0)
    except Exception:
        clean = geom
    if clean.is_empty:
        return np.zeros((0, 9), dtype=np.float32)

    col_arr = np.array(color, dtype=np.float32)
    chunks: List[np.ndarray] = []

    if include_top or include_bottom:
        try:
            cdt = shapely.constrained_delaunay_triangles(clean)
            if not cdt.is_empty:
                raw_coords = shapely.get_coordinates(cdt)
                if len(raw_coords) >= 4 and len(raw_coords) % 4 == 0:
                    tri2d = raw_coords.reshape(-1, 4, 2)[:, :3, :].astype(np.float32)
                    e1, e2 = tri2d[:, 1] - tri2d[:, 0], tri2d[:, 2] - tri2d[:, 0]
                    cw = (e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) < 0.0
                    if np.any(cw):
                        tri2d[cw, 1], tri2d[cw, 2] = tri2d[cw, 2].copy(), tri2d[cw, 1].copy()
                    nt = len(tri2d)
                    if include_top:
                        tb = np.empty((nt, 3, 9), dtype=np.float32)
                        tb[:, :, 0:2], tb[:, :, 2] = tri2d, float(z_top)
                        tb[:, :, 3:6], tb[:, :, 6:9] = (0.0, 0.0, 1.0), col_arr
                        chunks.append(tb.reshape(-1, 9))
                    if include_bottom:
                        bb = np.empty((nt, 3, 9), dtype=np.float32)
                        bb[:, 0, 0:2], bb[:, 1, 0:2], bb[:, 2, 0:2] = tri2d[:, 0], tri2d[:, 2], tri2d[:, 1]
                        bb[:, :, 2], bb[:, :, 3:6], bb[:, :, 6:9] = float(z_bot), (0.0, 0.0, -1.0), col_arr
                        chunks.append(bb.reshape(-1, 9))
        except Exception:
            pass

    side_list: List[np.ndarray] = []
    for poly in iter_polygons(clean):
        if poly.area < 0.04:
            continue
        for r_idx, ring in enumerate([poly.exterior] + list(poly.interiors)):
            pts = np.asarray(ring.coords, dtype=np.float32)
            if len(pts) < 3:
                continue
            a, b = pts[:-1], pts[1:]
            seg = b - a
            slen = np.hypot(seg[:, 0], seg[:, 1])
            keep = slen > 1e-4
            if not np.any(keep):
                continue
            a, b, seg, slen = a[keep], b[keep], seg[keep], slen[keep]
            sign = 1.0 if (r_idx == 0 and ring.is_ccw) or (r_idx > 0 and not ring.is_ccw) else -1.0
            nx = (sign * seg[:, 1] / slen).astype(np.float32)
            ny = (-sign * seg[:, 0] / slen).astype(np.float32)
            sb = np.empty((len(a), 6, 9), dtype=np.float32)
            sb[:, 0, 0:2], sb[:, 0, 2] = a, float(z_bot)
            sb[:, 1, 0:2], sb[:, 1, 2] = b, float(z_bot)
            sb[:, 2, 0:2], sb[:, 2, 2] = b, float(z_top)
            sb[:, 3, 0:2], sb[:, 3, 2] = a, float(z_bot)
            sb[:, 4, 0:2], sb[:, 4, 2] = b, float(z_top)
            sb[:, 5, 0:2], sb[:, 5, 2] = a, float(z_top)
            sb[:, :, 3], sb[:, :, 4], sb[:, :, 5] = nx[:, None], ny[:, None], 0.0
            sb[:, :, 6:9] = col_arr
            side_list.append(sb.reshape(-1, 9))

    if side_list:
        chunks.append(np.vstack(side_list))
    return np.vstack(chunks) if chunks else np.zeros((0, 9), dtype=np.float32)


def build_filled_resin_cycles(
    slice_result: Optional[SliceResult],
) -> List[Tuple[int, List[Tuple[int, np.ndarray]]]]:
    """Build per-``CureCycle`` triangulated resin volumes keyed by ``cycle.end_layer``."""
    if slice_result is None or not slice_result.schedule or not slice_result.layers:
        return []
    resin_col = FEATURE_COLORS.get("Filled resin", (0.04, 0.76, 0.95))
    layer_by_idx = {ld.layer_idx: ld for ld in slice_result.layers}
    cycles_out: List[Tuple[int, List[Tuple[int, np.ndarray]]]] = []

    for cyc in slice_result.schedule:
        dosing_pts = slice_result.dosing_by_layer.get(cyc.end_layer, [])
        if not dosing_pts or sum(float(dp.volume_mm3) for dp in dosing_pts) <= 1e-4:
            continue
        layer_meshes: List[Tuple[int, np.ndarray]] = []
        for k in range(cyc.start_layer, cyc.end_layer + 1):
            ld = layer_by_idx.get(k)
            if ld is None or ld.resin_void_geom is None or ld.resin_void_geom.is_empty:
                continue
            lh = max(0.05, float(ld.layer_height))
            z_bot = max(0.02, float(ld.z) - lh + 0.01)
            z_top = max(z_bot + 0.02, float(ld.z) - (0.045 if k == cyc.end_layer else 0.005))
            verts = triangulate_resin_layer_slab(
                ld.resin_void_geom, z_bot=z_bot, z_top=z_top, color=resin_col,
                include_top=(k == cyc.end_layer), include_bottom=(k == cyc.start_layer),
            )
            if len(verts) > 0:
                layer_meshes.append((k, verts))
        if layer_meshes:
            cycles_out.append((int(cyc.end_layer), layer_meshes))
    return cycles_out


def build_nozzle_cone(x: float, y: float, z: float) -> np.ndarray:
    """Build a small brass nozzle indicator cone positioned above ``(x, y, z)``."""
    sides, r, h = 10, 1.6, 5.0
    col = np.array([0.98, 0.82, 0.16], dtype=np.float32)
    tip = np.array([x, y, z + 0.1], dtype=np.float32)
    tris: List[List[float]] = []
    for i in range(sides):
        a0, a1 = 2.0 * math.pi * i / sides, 2.0 * math.pi * (i + 1) / sides
        p0 = np.array([x + r * math.cos(a0), y + r * math.sin(a0), z + h], dtype=np.float32)
        p1 = np.array([x + r * math.cos(a1), y + r * math.sin(a1), z + h], dtype=np.float32)
        norm = np.cross(p1 - tip, p0 - tip)
        nl = float(np.linalg.norm(norm))
        if nl > 1e-6:
            norm = norm / nl
        for v in (tip, p1, p0):
            tris.append([v[0], v[1], v[2], norm[0], norm[1], norm[2], col[0], col[1], col[2]])
    return np.asarray(tris, dtype=np.float32)


def build_bed_geometry(
    bx: float, by: float, bz: float, bed_shape: str, pal: ThemePalette
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build ``(plate_lit, active_grid_lines, inactive_grid_lines)`` for rectangular or circular beds."""
    z0, zg = -0.08, -0.02
    col = tuple(min(1.0, c * 1.18 + 0.03) for c in pal.gl_bed_plate)
    n = (0.0, 0.0, 1.0)
    lines_act: List[List[float]] = []
    lines_inact: List[List[float]] = []

    def _seg(tgt: List[List[float]], a: Tuple[float, float, float], b: Tuple[float, float, float], c: Tuple[float, float, float]) -> None:
        tgt.append([a[0], a[1], a[2], c[0], c[1], c[2]])
        tgt.append([b[0], b[1], b[2], c[0], c[1], c[2]])

    minor_c, major_c = pal.gl_grid_minor, pal.gl_grid_major
    border_act, border_inact, cage_c = (0.96, 0.78, 0.14), pal.gl_grid_border, pal.gl_cage

    if bed_shape == "circular":
        rad, cx, cy, n_seg = 0.5 * min(bx, by), 0.5 * bx, 0.5 * by, 64
        plate_tris: List[List[float]] = []
        for i in range(n_seg):
            a0, a1 = 2.0 * math.pi * i / n_seg, 2.0 * math.pi * (i + 1) / n_seg
            p0 = (cx + rad * math.cos(a0), cy + rad * math.sin(a0))
            p1 = (cx + rad * math.cos(a1), cy + rad * math.sin(a1))
            plate_tris.extend([[cx, cy, z0, *n, *col], [p0[0], p0[1], z0, *n, *col], [p1[0], p1[1], z0, *n, *col]])
            _seg(lines_act, (p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), border_act)
            _seg(lines_inact, (p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), border_inact)
        plate_cpu = np.asarray(plate_tris, dtype=np.float32)

        x = math.ceil((cx - rad) / 10.0) * 10.0
        while x < cx + rad - 1e-3:
            dy = math.sqrt(max(0.0, rad * rad - (x - cx) ** 2))
            c = major_c if (int(round(x - cx)) % 50 == 0) else minor_c
            _seg(lines_act, (x, cy - dy, zg), (x, cy + dy, zg), c)
            _seg(lines_inact, (x, cy - dy, zg), (x, cy + dy, zg), c)
            x += 10.0
        y = math.ceil((cy - rad) / 10.0) * 10.0
        while y < cy + rad - 1e-3:
            dx = math.sqrt(max(0.0, rad * rad - (y - cy) ** 2))
            c = major_c if (int(round(y - cy)) % 50 == 0) else minor_c
            _seg(lines_act, (cx - dx, y, zg), (cx + dx, y, zg), c)
            _seg(lines_inact, (cx - dx, y, zg), (cx + dx, y, zg), c)
            y += 10.0
    else:
        plate_cpu = np.array([
            [0.0, 0.0, z0, *n, *col], [bx, 0.0, z0, *n, *col], [bx, by, z0, *n, *col],
            [0.0, 0.0, z0, *n, *col], [bx, by, z0, *n, *col], [0.0, by, z0, *n, *col],
        ], dtype=np.float32)
        for x in np.arange(10.0, bx - 1e-3, 10.0):
            c = major_c if (int(round(float(x))) % 50 == 0) else minor_c
            _seg(lines_act, (float(x), 0.0, zg), (float(x), by, zg), c)
            _seg(lines_inact, (float(x), 0.0, zg), (float(x), by, zg), c)
        for y in np.arange(10.0, by - 1e-3, 10.0):
            c = major_c if (int(round(float(y))) % 50 == 0) else minor_c
            _seg(lines_act, (0.0, float(y), zg), (bx, float(y), zg), c)
            _seg(lines_inact, (0.0, float(y), zg), (bx, float(y), zg), c)
        for tgt, bc in ((lines_act, border_act), (lines_inact, border_inact)):
            _seg(tgt, (0.0, 0.0, 0.0), (bx, 0.0, 0.0), bc)
            _seg(tgt, (bx, 0.0, 0.0), (bx, by, 0.0), bc)
            _seg(tgt, (bx, by, 0.0), (0.0, by, 0.0), bc)
            _seg(tgt, (0.0, by, 0.0), (0.0, 0.0, 0.0), bc)
        guide_z = min(bz, 40.0)
        for cx_, cy_ in ((0.0, 0.0), (bx, 0.0), (bx, by), (0.0, by)):
            _seg(lines_act, (cx_, cy_, 0.0), (cx_, cy_, guide_z), cage_c)

    axis_len = 24.0
    _seg(lines_act, (0.0, 0.0, 0.05), (axis_len, 0.0, 0.05), (0.95, 0.28, 0.28))
    _seg(lines_act, (0.0, 0.0, 0.05), (0.0, axis_len, 0.05), (0.24, 0.86, 0.40))
    _seg(lines_act, (0.0, 0.0, 0.05), (0.0, 0.0, axis_len), (0.24, 0.56, 0.96))

    return plate_cpu, np.asarray(lines_act, dtype=np.float32), np.asarray(lines_inact, dtype=np.float32)


def build_gizmo_geometry(
    edit_tool: str, center: np.ndarray, size: float
) -> Tuple[List[Tuple[str, np.ndarray, np.ndarray]], np.ndarray, np.ndarray]:
    """Build 3D Move/Rotate/Scale axis gizmo picking rays, line segments, and lit arrowheads."""
    axes: List[Tuple[str, np.ndarray, np.ndarray]] = []
    axis_specs = [
        ("x", np.array([1.0, 0.0, 0.0]), (0.95, 0.25, 0.25)),
        ("y", np.array([0.0, 1.0, 0.0]), (0.20, 0.86, 0.38)),
        ("z", np.array([0.0, 0.0, 1.0]), (0.24, 0.58, 0.98)),
    ]
    lines: List[List[float]] = []
    lit_chunks: List[np.ndarray] = []

    if edit_tool in ("move", "scale"):
        for name, axis, col in axis_specs:
            tip = center + axis * size
            axes.append((name, center.copy(), tip.copy()))
            lines.extend([[*center, *col], [*tip, *col]])
            if edit_tool == "move":
                cone = trimesh.creation.cone(radius=size * 0.085, height=size * 0.24, sections=10)
                if name == "x":
                    cone.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2.0, [0, 1, 0]))
                elif name == "y":
                    cone.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2.0, [1, 0, 0]))
                cone.apply_translation(tip)
                lit_chunks.append(mesh_to_lit_buffer(cone, col))
            else:
                cube = trimesh.creation.box(extents=[size * 0.14] * 3)
                cube.apply_translation(tip)
                lit_chunks.append(mesh_to_lit_buffer(cube, col))
    elif edit_tool == "rotate":
        n_seg, rad = 48, size * 0.88
        for name, axis, col in axis_specs:
            u = np.array([0.0, 1.0, 0.0]) if abs(axis[0]) > 0.5 else np.array([1.0, 0.0, 0.0])
            u = u - np.dot(u, axis) * axis
            u /= max(float(np.linalg.norm(u)), 1e-9)
            v = np.cross(axis, u)
            axes.append((name, center.copy(), center + axis * rad))
            for i in range(n_seg):
                a0, a1 = 2.0 * math.pi * i / n_seg, 2.0 * math.pi * (i + 1) / n_seg
                p0 = center + rad * (math.cos(a0) * u + math.sin(a0) * v)
                p1 = center + rad * (math.cos(a1) * u + math.sin(a1) * v)
                lines.extend([[*p0, *col], [*p1, *col]])

    lines_cpu = np.asarray(lines, dtype=np.float32) if lines else np.zeros((0, 6), dtype=np.float32)
    lit_cpu = np.vstack(lit_chunks) if lit_chunks else np.zeros((0, 9), dtype=np.float32)
    return axes, lines_cpu, lit_cpu


def build_cut_plane_geometry(
    origin: np.ndarray, rx_deg: float, ry_deg: float, rz_deg: float, half_size: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Build translucent double-sided yellow cutting plane + handle sphere and wireframe border."""
    rot = euler_matrix_xyz(rx_deg, ry_deg, rz_deg)
    u = rot @ np.array([1.0, 0.0, 0.0], dtype=np.float64)
    v = rot @ np.array([0.0, 1.0, 0.0], dtype=np.float64)
    n = rot @ np.array([0.0, 0.0, 1.0], dtype=np.float64)

    p00 = origin - half_size * u - half_size * v
    p10 = origin + half_size * u - half_size * v
    p11 = origin + half_size * u + half_size * v
    p01 = origin - half_size * u + half_size * v

    col_q, nf = (0.98, 0.82, 0.14), n.astype(np.float32)
    quad = np.array([
        [*p00, *nf, *col_q], [*p10, *nf, *col_q], [*p11, *nf, *col_q],
        [*p00, *nf, *col_q], [*p11, *nf, *col_q], [*p01, *nf, *col_q],
        [*p00, *(-nf), *col_q], [*p11, *(-nf), *col_q], [*p10, *(-nf), *col_q],
        [*p00, *(-nf), *col_q], [*p01, *(-nf), *col_q], [*p11, *(-nf), *col_q],
    ], dtype=np.float32)

    handle = trimesh.creation.icosphere(subdivisions=1, radius=max(2.0, half_size * 0.065))
    handle.apply_translation(origin)
    lit_cpu = np.vstack([quad, mesh_to_lit_buffer(handle, (1.0, 0.90, 0.25))])

    fc, nc = (1.0, 0.88, 0.18), (0.12, 0.88, 1.0)
    lines = np.asarray([
        [*p00, *fc], [*p10, *fc], [*p10, *fc], [*p11, *fc],
        [*p11, *fc], [*p01, *fc], [*p01, *fc], [*p00, *fc],
        [*(origin - half_size * u), *fc], [*(origin + half_size * u), *fc],
        [*(origin - half_size * v), *fc], [*(origin + half_size * v), *fc],
        [*origin, *nc], [*(origin + n * (half_size * 0.55)), *nc],
    ], dtype=np.float32)
    return lit_cpu, lines


def build_brush_reticle(
    pt: np.ndarray, normal: np.ndarray, radius: float, mode: int
) -> np.ndarray:
    """Build a surface-tangent circular 3D reticle ring for the Paint-on-Supports brush."""
    p = pt + normal * 0.20
    u = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
    u = u - np.dot(u, normal) * normal
    u /= max(float(np.linalg.norm(u)), 1e-9)
    v = np.cross(normal, u)
    col = (0.18, 0.96, 0.50) if mode > 0 else ((0.98, 0.28, 0.28) if mode < 0 else (0.96, 0.96, 0.96))

    n_seg = 28
    lines: List[List[float]] = []
    for i in range(n_seg):
        a0, a1 = 2.0 * math.pi * i / n_seg, 2.0 * math.pi * (i + 1) / n_seg
        q0 = p + radius * (math.cos(a0) * u + math.sin(a0) * v)
        q1 = p + radius * (math.cos(a1) * u + math.sin(a1) * v)
        lines.extend([[*q0, *col], [*q1, *col]])
    return np.asarray(lines, dtype=np.float32)
