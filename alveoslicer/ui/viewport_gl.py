"""GPU-accelerated PySide6 OpenGL 3D viewport for multi-instance editing, 3D gizmos, Plane Cutting, Paint-on Supports, & G-code previewing."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (
    QMatrix4x4,
    QMouseEvent,
    QOpenGLFunctions,
    QVector2D,
    QVector3D,
    QVector4D,
    QWheelEvent,
)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from ..gcode_parser import (
    DOSING_COLOR,
    FEATURE_COLORS,
    FEATURE_TYPE_TO_ID,
    FEATURE_TYPES,
    TRAVEL_COLOR,
    GCodePreviewData,
)
from ..geometry import iter_polygons
from ..infill import Capsule
from ..pipeline import SliceResult
from ..scene import BuildPlate, ModelInstance, SimplifiedFace
from .theme import ThemePalette, get_palette

# OpenGL constants (avoiding external PyOpenGL dependency)
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100

_LIT_VERT_SRC = """
attribute vec3 a_pos;
attribute vec3 a_norm;
attribute vec3 a_color;
uniform mat4 u_mvp;
varying vec3 v_norm;
varying vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_norm = a_norm;
    v_color = a_color;
}
"""

_LIT_FRAG_SRC = """
uniform vec2 u_alpha_v;
uniform vec3 u_view_dir;
varying vec3 v_norm;
varying vec3 v_color;
void main() {
    vec3 n = normalize(v_norm);
    vec3 l1 = normalize(vec3(0.45, -0.60, 0.85));
    vec3 l2 = normalize(vec3(-0.65, 0.55, 0.35));
    float d1 = max(dot(n, l1), 0.0);
    float d2 = max(dot(n, l2), 0.0);
    float hemi = 0.5 + 0.5 * n.z;
    vec3 half_v = normalize(l1 + normalize(u_view_dir));
    float spec = pow(max(dot(n, half_v), 0.0), 28.0) * 0.20;
    float shade = 0.32 + 0.16 * hemi + 0.50 * d1 + 0.22 * d2;
    vec3 col = clamp(v_color * shade + vec3(spec), 0.0, 1.0);
    gl_FragColor = vec4(col, u_alpha_v.x);
}
"""

_LINE_VERT_SRC = """
attribute vec3 a_pos;
attribute vec3 a_color;
uniform mat4 u_mvp;
varying vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_color = a_color;
}
"""

_LINE_FRAG_SRC = """
uniform vec2 u_alpha_v;
varying vec3 v_color;
void main() {
    gl_FragColor = vec4(v_color, u_alpha_v.x);
}
"""


def _mesh_to_lit_buffer(
    mesh: trimesh.Trimesh,
    color: Tuple[float, float, float],
    paint_support: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Convert a trimesh into interleaved (V, 9) float32 [x,y,z, nx,ny,nz, r,g,b] with crisp normals & paint colors."""
    if mesh is None or len(mesh.faces) == 0:
        return np.zeros((0, 9), dtype=np.float32)
    tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float32)
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    fn = np.cross(e1, e2)
    fl = np.linalg.norm(fn, axis=1, keepdims=True)
    fn = fn / np.maximum(fl, 1e-6)

    n_f = len(tris)
    buf = np.empty((n_f, 3, 9), dtype=np.float32)
    buf[:, :, 0:3] = tris
    buf[:, :, 3:6] = fn[:, None, :]

    face_cols = np.broadcast_to(np.array(color, dtype=np.float32), (n_f, 3)).copy()
    if paint_support is not None and len(paint_support) == n_f:
        enf = paint_support > 0
        blk = paint_support < 0
        if np.any(enf):
            face_cols[enf] = np.array([0.14, 0.84, 0.44], dtype=np.float32)  # Emerald Enforcer
        if np.any(blk):
            face_cols[blk] = np.array([0.92, 0.24, 0.24], dtype=np.float32)  # Crimson Blocker

    buf[:, :, 6:9] = face_cols[:, None, :]
    return buf.reshape(-1, 9)


def _euler_matrix_xyz(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return mz @ my @ mx


def _ray_intersects_aabb(
    orig: np.ndarray, inv_dir: np.ndarray, bmin: np.ndarray, bmax: np.ndarray
) -> bool:
    t1 = (bmin - orig) * inv_dir
    t2 = (bmax - orig) * inv_dir
    tmin = float(np.max(np.minimum(t1, t2)))
    tmax = float(np.min(np.maximum(t1, t2)))
    return tmax >= max(0.0, tmin)


def _ray_triangles_hit(
    orig: np.ndarray, direction: np.ndarray, tris: np.ndarray
) -> Optional[float]:
    """Vectorized Möller–Trumbore ray-triangle intersection returning nearest positive t, or None."""
    if len(tris) == 0:
        return None
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    h = np.cross(direction[None, :], e2)
    a = np.einsum("ij,ij->i", e1, h)
    valid = np.abs(a) > 1e-8
    if not np.any(valid):
        return None

    f = np.zeros_like(a)
    f[valid] = 1.0 / a[valid]
    s = orig[None, :] - v0
    u = f * np.einsum("ij,ij->i", s, h)
    valid &= (u >= -1e-6) & (u <= 1.0 + 1e-6)
    if not np.any(valid):
        return None

    q = np.cross(s, e1)
    v = f * np.einsum("j,ij->i", direction, q)
    valid &= (v >= -1e-6) & (u + v <= 1.0 + 1e-6)
    if not np.any(valid):
        return None

    t = f * np.einsum("ij,ij->i", e2, q)
    valid &= t > 1e-4
    if not np.any(valid):
        return None
    return float(np.min(t[valid]))


def _ray_triangles_hit_full(
    orig: np.ndarray, direction: np.ndarray, tris: np.ndarray
) -> Optional[Tuple[float, np.ndarray, np.ndarray]]:
    """Return (t, hit_point, face_normal) for nearest ray-triangle hit, or None."""
    if len(tris) == 0:
        return None
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    h = np.cross(direction[None, :], e2)
    a = np.einsum("ij,ij->i", e1, h)
    valid = np.abs(a) > 1e-8
    if not np.any(valid):
        return None

    f = np.zeros_like(a)
    f[valid] = 1.0 / a[valid]
    s = orig[None, :] - v0
    u = f * np.einsum("ij,ij->i", s, h)
    valid &= (u >= -1e-6) & (u <= 1.0 + 1e-6)
    if not np.any(valid):
        return None

    q = np.cross(s, e1)
    v = f * np.einsum("j,ij->i", direction, q)
    valid &= (v >= -1e-6) & (u + v <= 1.0 + 1e-6)
    if not np.any(valid):
        return None

    t = f * np.einsum("ij,ij->i", e2, q)
    valid &= t > 1e-4
    if not np.any(valid):
        return None

    valid_idxs = np.flatnonzero(valid)
    best_local = int(np.argmin(t[valid_idxs]))
    best_i = int(valid_idxs[best_local])
    best_t = float(t[best_i])
    hit_pt = orig + best_t * direction
    fn = np.cross(e1[best_i], e2[best_i])
    nl = float(np.linalg.norm(fn))
    if nl > 1e-9:
        fn = fn / nl
    else:
        fn = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return best_t, hit_pt, fn


def _ray_segment_dist(
    orig: np.ndarray, direction: np.ndarray, a: np.ndarray, b: np.ndarray
) -> Tuple[float, float]:
    """Shortest 3D distance between ray (orig + t*dir, t>=0) and segment [a, b]. Returns (dist, ray_t)."""
    u = direction
    v = b - a
    w = orig - a
    aa = float(np.dot(u, u))
    bb = float(np.dot(u, v))
    cc = float(np.dot(v, v))
    dd = float(np.dot(u, w))
    ee = float(np.dot(v, w))
    denom = aa * cc - bb * bb
    if denom < 1e-9:
        sc = 0.0
        tc = max(0.0, min(1.0, ee / max(cc, 1e-9)))
    else:
        sc = max(0.0, (bb * ee - cc * dd) / denom)
        tc = max(0.0, min(1.0, (aa * ee - bb * dd) / denom))
    dp = (orig + sc * u) - (a + tc * v)
    return float(np.linalg.norm(dp)), sc


# 8-vertex rounded obround (stadium/capsule) cross-section profile around (+n_left, +Z)
_RING_U = np.array([0.56, 0.98, 0.98, 0.56, -0.56, -0.98, -0.98, -0.56], dtype=np.float32)
_RING_V = np.array([1.00, 0.52, -0.52, -1.00, -1.00, -0.52, 0.52, 1.00], dtype=np.float32)
_RING_NU = np.array([0.22, 0.86, 0.86, 0.22, -0.22, -0.86, -0.86, -0.22], dtype=np.float32)
_RING_NV = np.array([0.975, 0.51, -0.51, -0.975, -0.975, -0.51, 0.51, 0.975], dtype=np.float32)
_RING_K = np.arange(8, dtype=np.int32)
_RING_KN = (np.arange(8, dtype=np.int32) + 1) % 8


def _plate_grid_offset(plate_idx: int, num_plates: int, bed_x: float, bed_y: float) -> Tuple[float, float]:
    """Compute world-space (ox, oy) grid offset for multi-bed side-by-side / grid layout."""
    if num_plates <= 1 or plate_idx <= 0:
        return 0.0, 0.0
    cols = num_plates if num_plates <= 3 else int(math.ceil(math.sqrt(num_plates)))
    gap = 38.0
    col = plate_idx % cols
    row = plate_idx // cols
    return float(col * (bed_x + gap)), float(row * (bed_y + gap))


def _build_extrusion_beads(
    p0: np.ndarray,
    p1: np.ndarray,
    widths: np.ndarray,
    heights: np.ndarray,
    type_ids: np.ndarray,
    layers: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Vectorized rounded-obround continuous filament tube builder (PrusaSlicer-style):
      - 8-sided rounded capsule cross-section with smooth per-vertex outward normals
      - Detects continuous extrusion chains (no travel/retract/type change) and joins
        consecutive segments with clean mitered cross-sections (including closed loops)
      - Emits rounded dome endcaps only at actual extrusion starts and stops

    Returns:
      (beads (N, 48, 9), caps (M, 24, 9), caps_seg_idx (M,))
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
    # Unit left lateral normal in XY: (-dy, dx)
    nx = -dy
    ny = dx

    hw = (0.5 * np.maximum(0.08, widths)).astype(np.float32)
    ht = np.maximum(0.05, heights).astype(np.float32)
    hz = (0.5 * ht).astype(np.float32)

    # Vertical centerline of each extruded bead sits at [z - ht, z]
    c0 = p0.astype(np.float32).copy()
    c1 = p1.astype(np.float32).copy()
    c0[:, 2] -= hz
    c1[:, 2] -= hz

    # Detect continuous chains: segment i seamlessly follows segment i - 1
    conn_prev = np.zeros(n, dtype=bool)
    if n > 1:
        gap = np.linalg.norm(p0[1:] - p1[:-1], axis=1)
        same = (
            (gap <= 0.025)
            & (type_ids[1:] == type_ids[:-1])
            & (np.abs(widths[1:] - widths[:-1]) <= 0.06)
            & (np.abs(heights[1:] - heights[:-1]) <= 0.06)
        )
        if layers is not None and len(layers) == n:
            same &= layers[1:] == layers[:-1]
        # Avoid spiky miters on hairpin reversals (> ~123° turn); cap sharp reversals cleanly
        dot_dir = dx[:-1] * dx[1:] + dy[:-1] * dy[1:]
        same &= dot_dir > -0.55
        conn_prev[1:] = same

    conn_next = np.zeros(n, dtype=bool)
    if n > 1:
        conn_next[:-1] = conn_prev[1:]

    # Per-endpoint lateral miter vectors (mx, my) & smooth unit normals (unx, uny)
    m0x = nx.copy()
    m0y = ny.copy()
    m1x = nx.copy()
    m1y = ny.copy()
    un0x = nx.copy()
    un0y = ny.copy()
    un1x = nx.copy()
    un1y = ny.copy()

    # Vectorized miter computation for all interior joints
    joint_i = np.flatnonzero(conn_prev)
    if len(joint_i) > 0:
        j_prev = joint_i - 1
        bx = nx[j_prev] + nx[joint_i]
        by = ny[j_prev] + ny[joint_i]
        blen = np.hypot(bx, by)
        ok_b = blen > 1e-4
        safe_b = np.where(ok_b, blen, 1.0)
        ubx = np.where(ok_b, bx / safe_b, nx[joint_i])
        uby = np.where(ok_b, by / safe_b, ny[joint_i])
        cos_half = np.maximum(ubx * nx[joint_i] + uby * ny[joint_i], 0.55)
        miter_scale = np.minimum(1.0 / cos_half, 1.65).astype(np.float32)

        mx_j = (ubx * miter_scale).astype(np.float32)
        my_j = (uby * miter_scale).astype(np.float32)
        m1x[j_prev] = mx_j
        m1y[j_prev] = my_j
        m0x[joint_i] = mx_j
        m0y[joint_i] = my_j
        un1x[j_prev] = ubx.astype(np.float32)
        un1y[j_prev] = uby.astype(np.float32)
        un0x[joint_i] = ubx.astype(np.float32)
        un0y[joint_i] = uby.astype(np.float32)

    # Also detect closed perimeter loops (chain end meets chain start) for seamless 360° miters
    starts = np.flatnonzero(~conn_prev)
    ends = np.flatnonzero(~conn_next)
    if len(starts) == len(ends) and len(starts) > 0:
        multi = ends > starts + 1
        if np.any(multi):
            s_idx = starts[multi]
            e_idx = ends[multi]
            loop_gap = np.linalg.norm(p1[e_idx] - p0[s_idx], axis=1)
            loop_dot = dx[e_idx] * dx[s_idx] + dy[e_idx] * dy[s_idx]
            closed = (loop_gap <= 0.025) & (loop_dot > -0.55)
            if np.any(closed):
                cs = s_idx[closed]
                ce = e_idx[closed]
                bx = nx[ce] + nx[cs]
                by = ny[ce] + ny[cs]
                blen = np.hypot(bx, by)
                ok_b = blen > 1e-4
                safe_b = np.where(ok_b, blen, 1.0)
                ubx = np.where(ok_b, bx / safe_b, nx[cs])
                uby = np.where(ok_b, by / safe_b, ny[cs])
                cos_half = np.maximum(ubx * nx[cs] + uby * ny[cs], 0.55)
                ms = np.minimum(1.0 / cos_half, 1.65).astype(np.float32)
                m1x[ce] = (ubx * ms).astype(np.float32)
                m1y[ce] = (uby * ms).astype(np.float32)
                m0x[cs] = m1x[ce]
                m0y[cs] = m1y[ce]
                un1x[ce] = ubx.astype(np.float32)
                un1y[ce] = uby.astype(np.float32)
                un0x[cs] = un1x[ce]
                un0y[cs] = un1y[ce]
                conn_prev[cs] = True
                conn_next[ce] = True

    # Build 8-vertex rings at start (r0) and end (r1): shape (N, 8, 3)
    u_hw = hw[:, None] * _RING_U[None, :]  # (N, 8)
    v_hz = hz[:, None] * _RING_V[None, :]  # (N, 8)

    r0 = np.empty((n, 8, 3), dtype=np.float32)
    r0[:, :, 0] = c0[:, 0:1] + m0x[:, None] * u_hw
    r0[:, :, 1] = c0[:, 1:2] + m0y[:, None] * u_hw
    r0[:, :, 2] = c0[:, 2:3] + v_hz

    r1 = np.empty((n, 8, 3), dtype=np.float32)
    r1[:, :, 0] = c1[:, 0:1] + m1x[:, None] * u_hw
    r1[:, :, 1] = c1[:, 1:2] + m1y[:, None] * u_hw
    r1[:, :, 2] = c1[:, 2:3] + v_hz

    # Smooth outward per-vertex normals at r0 and r1: shape (N, 8, 3)
    n0 = np.empty((n, 8, 3), dtype=np.float32)
    n0[:, :, 0] = un0x[:, None] * _RING_NU[None, :]
    n0[:, :, 1] = un0y[:, None] * _RING_NU[None, :]
    n0[:, :, 2] = _RING_NV[None, :]

    n1 = np.empty((n, 8, 3), dtype=np.float32)
    n1[:, :, 0] = un1x[:, None] * _RING_NU[None, :]
    n1[:, :, 1] = un1y[:, None] * _RING_NU[None, :]
    n1[:, :, 2] = _RING_NV[None, :]

    # Assemble 8 quads = 16 triangles = 48 vertices per segment -> (N, 8, 6, 9) -> (N, 48, 9)
    out = np.empty((n, 8, 6, 9), dtype=np.float32)
    # Tri 1: (r0[k], r1[k], r1[kn])
    out[:, :, 0, 0:3] = r0[:, _RING_K, :]
    out[:, :, 0, 3:6] = n0[:, _RING_K, :]
    out[:, :, 1, 0:3] = r1[:, _RING_K, :]
    out[:, :, 1, 3:6] = n1[:, _RING_K, :]
    out[:, :, 2, 0:3] = r1[:, _RING_KN, :]
    out[:, :, 2, 3:6] = n1[:, _RING_KN, :]
    # Tri 2: (r0[k], r1[kn], r0[kn])
    out[:, :, 3, 0:3] = r0[:, _RING_K, :]
    out[:, :, 3, 3:6] = n0[:, _RING_K, :]
    out[:, :, 4, 0:3] = r1[:, _RING_KN, :]
    out[:, :, 4, 3:6] = n1[:, _RING_KN, :]
    out[:, :, 5, 0:3] = r0[:, _RING_KN, :]
    out[:, :, 5, 3:6] = n0[:, _RING_KN, :]
    out[:, :, :, 6:9] = colors[:, None, None, :]
    beads = out.reshape(n, 48, 9)

    # Rounded dome endcaps ONLY at actual open starts (~conn_prev) and open ends (~conn_next)
    cap_chunks: List[np.ndarray] = []
    cap_idx_chunks: List[np.ndarray] = []

    open_s = np.flatnonzero(~conn_prev)
    if len(open_s) > 0:
        ns = len(open_s)
        ext_s = (0.42 * hw[open_s])[:, None]
        dir_s = np.stack([dx[open_s], dy[open_s], np.zeros(ns, dtype=np.float32)], axis=1)
        apex_s = c0[open_s] - dir_s * ext_s
        norm_s = -dir_s
        c_buf = np.empty((ns, 8, 3, 9), dtype=np.float32)
        c_buf[:, :, 0, 0:3] = apex_s[:, None, :]
        c_buf[:, :, 0, 3:6] = norm_s[:, None, :]
        c_buf[:, :, 1, 0:3] = r0[open_s][:, _RING_K, :]
        c_buf[:, :, 1, 3:6] = n0[open_s][:, _RING_K, :]
        c_buf[:, :, 2, 0:3] = r0[open_s][:, _RING_KN, :]
        c_buf[:, :, 2, 3:6] = n0[open_s][:, _RING_KN, :]
        c_buf[:, :, :, 6:9] = colors[open_s][:, None, None, :]
        cap_chunks.append(c_buf.reshape(ns, 24, 9))
        cap_idx_chunks.append(open_s.astype(np.int32))

    open_e = np.flatnonzero(~conn_next)
    if len(open_e) > 0:
        ne = len(open_e)
        ext_e = (0.42 * hw[open_e])[:, None]
        dir_e = np.stack([dx[open_e], dy[open_e], np.zeros(ne, dtype=np.float32)], axis=1)
        apex_e = c1[open_e] + dir_e * ext_e
        norm_e = dir_e
        c_buf = np.empty((ne, 8, 3, 9), dtype=np.float32)
        c_buf[:, :, 0, 0:3] = apex_e[:, None, :]
        c_buf[:, :, 0, 3:6] = norm_e[:, None, :]
        c_buf[:, :, 1, 0:3] = r1[open_e][:, _RING_KN, :]
        c_buf[:, :, 1, 3:6] = n1[open_e][:, _RING_KN, :]
        c_buf[:, :, 2, 0:3] = r1[open_e][:, _RING_K, :]
        c_buf[:, :, 2, 3:6] = n1[open_e][:, _RING_K, :]
        c_buf[:, :, :, 6:9] = colors[open_e][:, None, None, :]
        cap_chunks.append(c_buf.reshape(ne, 24, 9))
        cap_idx_chunks.append(open_e.astype(np.int32))

    if cap_chunks:
        caps = np.concatenate(cap_chunks, axis=0)
        caps_seg_idx = np.concatenate(cap_idx_chunks, axis=0)
    else:
        caps = np.zeros((0, 24, 9), dtype=np.float32)
        caps_seg_idx = np.zeros((0,), dtype=np.int32)

    return beads, caps, caps_seg_idx


def _triangulate_resin_layer_slab(
    geom,
    z_bot: float,
    z_top: float,
    color: Tuple[float, float, float],
    include_top: bool = True,
    include_bottom: bool = True,
) -> np.ndarray:
    """Build lit (V, 9) float32 triangles for a layer's photopolymer resin cavity polygon(s)."""
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

    # 1. Top and bottom triangulated horizontal caps via GEOS Constrained Delaunay
    if include_top or include_bottom:
        try:
            cdt = shapely.constrained_delaunay_triangles(clean)
            if not cdt.is_empty:
                raw_coords = shapely.get_coordinates(cdt)
                if len(raw_coords) >= 4 and len(raw_coords) % 4 == 0:
                    tri2d = raw_coords.reshape(-1, 4, 2)[:, :3, :].astype(np.float32)
                    # Enforce CCW orientation in XY for upward normal (+Z)
                    e1 = tri2d[:, 1] - tri2d[:, 0]
                    e2 = tri2d[:, 2] - tri2d[:, 0]
                    cross_z = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
                    cw = cross_z < 0.0
                    if np.any(cw):
                        tri2d[cw, 1], tri2d[cw, 2] = tri2d[cw, 2].copy(), tri2d[cw, 1].copy()

                    nt = len(tri2d)
                    if include_top:
                        tb = np.empty((nt, 3, 9), dtype=np.float32)
                        tb[:, :, 0:2] = tri2d
                        tb[:, :, 2] = float(z_top)
                        tb[:, :, 3:6] = np.array([0.0, 0.0, 1.0], dtype=np.float32)
                        tb[:, :, 6:9] = col_arr
                        chunks.append(tb.reshape(-1, 9))
                    if include_bottom:
                        bb = np.empty((nt, 3, 9), dtype=np.float32)
                        bb[:, 0, 0:2] = tri2d[:, 0]
                        bb[:, 1, 0:2] = tri2d[:, 2]
                        bb[:, 2, 0:2] = tri2d[:, 1]
                        bb[:, :, 2] = float(z_bot)
                        bb[:, :, 3:6] = np.array([0.0, 0.0, -1.0], dtype=np.float32)
                        bb[:, :, 6:9] = col_arr
                        chunks.append(bb.reshape(-1, 9))
        except Exception:
            pass

    # 2. Vertical side walls around exterior and hole rings
    polys = iter_polygons(clean)
    side_list: List[np.ndarray] = []
    for poly in polys:
        if poly.area < 0.04:
            continue
        rings = [poly.exterior] + list(poly.interiors)
        for r_idx, ring in enumerate(rings):
            pts = np.asarray(ring.coords, dtype=np.float32)
            if len(pts) < 3:
                continue
            a = pts[:-1]
            b = pts[1:]
            seg = b - a
            slen = np.hypot(seg[:, 0], seg[:, 1])
            keep = slen > 1e-4
            if not np.any(keep):
                continue
            a = a[keep]
            b = b[keep]
            seg = seg[keep]
            slen = slen[keep]
            # Outward normal: right-hand of CCW exterior, left-hand of CW interior
            sign = 1.0 if (r_idx == 0 and ring.is_ccw) or (r_idx > 0 and not ring.is_ccw) else -1.0
            nx = (sign * seg[:, 1] / slen).astype(np.float32)
            ny = (-sign * seg[:, 0] / slen).astype(np.float32)
            ns = len(a)
            sb = np.empty((ns, 6, 9), dtype=np.float32)
            sb[:, 0, 0:2] = a
            sb[:, 0, 2] = float(z_bot)
            sb[:, 1, 0:2] = b
            sb[:, 1, 2] = float(z_bot)
            sb[:, 2, 0:2] = b
            sb[:, 2, 2] = float(z_top)
            sb[:, 3, 0:2] = a
            sb[:, 3, 2] = float(z_bot)
            sb[:, 4, 0:2] = b
            sb[:, 4, 2] = float(z_top)
            sb[:, 5, 0:2] = a
            sb[:, 5, 2] = float(z_top)
            sb[:, :, 3] = nx[:, None]
            sb[:, :, 4] = ny[:, None]
            sb[:, :, 5] = 0.0
            sb[:, :, 6:9] = col_arr
            side_list.append(sb.reshape(-1, 9))

    if side_list:
        chunks.append(np.vstack(side_list))
    return np.vstack(chunks) if chunks else np.zeros((0, 9), dtype=np.float32)


def _build_filled_resin_cycles(
    slice_result: Optional[SliceResult],
) -> List[Tuple[int, List[Tuple[int, np.ndarray]]]]:
    """
    Build per-CureCycle triangulated photopolymer resin volumes from SliceResult.
    Returns list of (cycle_end_layer, [(layer_idx, verts_Nx9), ...]).
    Each cycle only appears in G-code Preview once its dosing layer (cycle_end_layer) is reached!
    """
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
            # Slightly recess the cycle meniscus surface so top FFF cell walls remain crisp
            z_top = float(ld.z) - (0.045 if k == cyc.end_layer else 0.005)
            if z_top <= z_bot:
                z_top = z_bot + 0.02
            verts = _triangulate_resin_layer_slab(
                ld.resin_void_geom,
                z_bot=z_bot,
                z_top=z_top,
                color=resin_col,
                include_top=(k == cyc.end_layer),
                include_bottom=(k == cyc.start_layer),
            )
            if len(verts) > 0:
                layer_meshes.append((k, verts))
        if layer_meshes:
            cycles_out.append((int(cyc.end_layer), layer_meshes))
    return cycles_out


class AlveoViewport3D(QOpenGLWidget):
    """
    PrusaSlicer-style OpenGL 3D Editor & G-code Previewer:
      - Multi-instance selection, Move/Rotate/Scale 3D axis gizmos, Place-on-Face
      - Interactive 3D Plane Cutter (moveable & rotatable translucent yellow cutting plane)
      - Interactive 3D Paint-on Supports (Enforcer green / Blocker red brush painting + 3D reticle)
      - Print build plate automatically becomes invisible when looking from bottom up (eye_z < 0)
      - Full Dark / Light theme awareness and Rectangular / Circular build plates
    """

    instance_selected = Signal(str)
    selection_cleared = Signal()
    plate_selected = Signal(int)                                # clicked inactive plate index in 3D space
    instance_moved = Signal(str, float, float)                   # instance_id, pos_x, pos_y
    instance_moved_z = Signal(str, float)                       # instance_id, pos_z
    instance_rotated = Signal(str, float, float, float)         # instance_id, rot_x, rot_y, rot_z
    instance_scaled = Signal(str, float)                        # instance_id, uniform_scale_pct
    face_picked = Signal(object)                                # world outward normal np.ndarray (3,)
    cut_plane_changed = Signal(float, float, float, float, float, float)  # ox, oy, oz, rx, ry, rz
    support_painted = Signal(str, float, float, float, float, int)        # inst_id, hx, hy, hz, radius, val

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 340)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._palette: ThemePalette = get_palette("dark")

        # View mode: "editor" or "preview"
        self.mode: str = "editor"
        # Interactive tool: "select" | "move" | "rotate" | "scale" | "place_on_face" | "cut" | "paint_supports"
        self.edit_tool: str = "select"

        # Bed dimensions, shape & multi-plate 3D grid state
        self.bed_x: float = 220.0
        self.bed_y: float = 220.0
        self.bed_z: float = 220.0
        self.bed_shape: str = "rectangular"
        self._num_plates: int = 1
        self._active_plate_idx: int = 0
        self._plate_offsets: List[Tuple[float, float]] = [(0.0, 0.0)]
        self._active_plate_offset = np.array([0.0, 0.0, 0.0], dtype=np.float64)

        # Orbit camera state
        self.cam_target = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self.cam_yaw: float = -42.0
        self.cam_pitch: float = 34.0
        self.cam_dist: float = 260.0
        self._last_eye = np.array([110.0, -90.0, 140.0], dtype=np.float64)
        self._last_mouse: Optional[QPoint] = None
        self._press_mouse: Optional[QPoint] = None
        self._drag_mode: str = ""
        self._drag_axis: str = ""  # "x" | "y" | "z" | ""
        self._pressed_on_empty: bool = False

        # Scene instances & picking caches
        self._instances_by_id: Dict[str, ModelInstance] = {}
        self._selected_id: Optional[str] = None
        self._pick_instances: List[Tuple[str, np.ndarray, np.ndarray, np.ndarray]] = []
        self._inactive_pick_instances: List[Tuple[int, str, np.ndarray, np.ndarray, np.ndarray]] = []
        self._placeable_faces: List[SimplifiedFace] = []
        self._hovered_face_idx: int = -1

        # 3D Transform gizmo anchor
        self._gizmo_center = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self._gizmo_size: float = 26.0
        self._gizmo_axes: List[Tuple[str, np.ndarray, np.ndarray]] = []

        # Interactive 3D Cut Plane state
        self.cut_origin = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self.cut_rot_x: float = 0.0
        self.cut_rot_y: float = 0.0
        self.cut_rot_z: float = 0.0
        self._cut_plane_half_size: float = 35.0
        self._drag_init_cut_origin = self.cut_origin.copy()
        self._drag_init_cut_rot: Tuple[float, float, float] = (0.0, 0.0, 0.0)

        # Interactive 3D Paint-on Supports state
        self.paint_brush_radius: float = 4.0
        self.paint_brush_mode: int = 1  # +1 enforcer, -1 blocker, 0 erase
        self._brush_hover_pt: Optional[np.ndarray] = None
        self._brush_hover_norm: Optional[np.ndarray] = None

        # Drag manipulation snapshot
        self._drag_inst_id: Optional[str] = None
        self._drag_move_offset: Tuple[float, float] = (0.0, 0.0)
        self._drag_init_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._drag_init_rot: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._drag_init_scale: float = 100.0

        # Scene display flags
        self.show_mesh: bool = True
        self.show_infill_wireframe: bool = True
        self.show_travels: bool = False
        self.show_dosing: bool = True
        self.visible_types: Dict[int, bool] = {i: True for i in range(len(FEATURE_TYPES))}

        # Preview scrub state
        self.preview_data: Optional[GCodePreviewData] = None
        self.layer_min: int = 1
        self.layer_max: int = 1
        self.top_move_limit: int = 10**9

        # CPU geometry buffers
        self._bed_plate_cpu = np.zeros((0, 9), dtype=np.float32)
        self._bed_grid_cpu = np.zeros((0, 6), dtype=np.float32)
        self._bed_grid_inactive_cpu = np.zeros((0, 6), dtype=np.float32)
        self._mesh_cpu = np.zeros((0, 9), dtype=np.float32)
        self._inactive_mesh_cpu = np.zeros((0, 9), dtype=np.float32)
        self._selection_box_cpu = np.zeros((0, 6), dtype=np.float32)
        self._wipe_tower_mesh_cpu = np.zeros((0, 9), dtype=np.float32)
        self._wipe_tower_lines_cpu = np.zeros((0, 6), dtype=np.float32)
        self._place_faces_cpu = np.zeros((0, 9), dtype=np.float32)
        self._place_hover_cpu = np.zeros((0, 9), dtype=np.float32)
        self._wireframe_cpu = np.zeros((0, 6), dtype=np.float32)
        self._gizmo_lit_cpu = np.zeros((0, 9), dtype=np.float32)
        self._gizmo_lines_cpu = np.zeros((0, 6), dtype=np.float32)
        self._cut_plane_lit_cpu = np.zeros((0, 9), dtype=np.float32)
        self._cut_plane_lines_cpu = np.zeros((0, 6), dtype=np.float32)
        self._brush_reticle_cpu = np.zeros((0, 6), dtype=np.float32)

        self._all_beads_cpu = np.zeros((0, 48, 9), dtype=np.float32)
        self._all_caps_cpu = np.zeros((0, 24, 9), dtype=np.float32)
        self._all_caps_seg_idx = np.zeros((0,), dtype=np.int32)
        self._resin_cycles: List[Tuple[int, List[Tuple[int, np.ndarray]]]] = []
        self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
        self._active_resin_cpu = np.zeros((0, 9), dtype=np.float32)
        self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
        self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
        self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)

        # Dirty flags for VBO uploads
        self._gl_ready = False
        self._dirty_bed = True
        self._dirty_mesh = True
        self._dirty_wipe = True
        self._dirty_place = True
        self._dirty_wire = True
        self._dirty_gizmo = True
        self._dirty_cut = True
        self._dirty_brush = True
        self._dirty_preview = True

        # GL resources
        self._gl: Optional[QOpenGLFunctions] = None
        self._lit_prog: Optional[QOpenGLShaderProgram] = None
        self._line_prog: Optional[QOpenGLShaderProgram] = None
        self._vbos: Dict[str, Tuple[QOpenGLBuffer, int]] = {}

        self._rebuild_bed_geometry()

    # -------------------------------------------------------------------------
    # Public Scene, Theme, Cut Plane & Paint API
    # -------------------------------------------------------------------------

    @property
    def is_looking_from_bottom(self) -> bool:
        """True whenever the camera eye sits underneath the build plate (Z < 0)."""
        return float(self._last_eye[2]) < 0.0

    def set_theme_mode(self, mode: str) -> None:
        self._palette = get_palette(mode)
        self._rebuild_bed_geometry()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode != "editor":
            self._hovered_face_idx = -1
            self._brush_hover_pt = None
            self._rebuild_brush_reticle()
        self.update()

    def set_edit_tool(self, tool: str) -> None:
        self.edit_tool = tool
        self.setMouseTracking(tool in ("place_on_face", "paint_supports"))
        self._brush_hover_pt = None
        self._rebuild_brush_reticle()

        if tool == "place_on_face":
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._rebuild_placeable_faces()
        elif tool == "paint_supports":
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._clear_placeable_faces()
        elif tool == "cut":
            self.setCursor(Qt.CursorShape.SplitVCursor)
            self._clear_placeable_faces()
            self._reset_cut_plane_to_selected()
        elif tool == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self._clear_placeable_faces()
        elif tool in ("rotate", "scale"):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._clear_placeable_faces()
        else:
            self.unsetCursor()
            self._clear_placeable_faces()

        self._rebuild_gizmo_geometry()
        self._rebuild_cut_plane_geometry()
        self.update()

    def _refresh_plate_offsets(self) -> None:
        n = max(1, self._num_plates)
        self._plate_offsets = [
            _plate_grid_offset(i, n, self.bed_x, self.bed_y) for i in range(n)
        ]
        a_idx = max(0, min(n - 1, self._active_plate_idx))
        ox, oy = self._plate_offsets[a_idx]
        self._active_plate_offset = np.array([ox, oy, 0.0], dtype=np.float64)

    def set_bed_size(self, bx: float, by: float, bz: float, bed_shape: Optional[str] = None) -> None:
        shape = bed_shape if bed_shape is not None else self.bed_shape
        if (
            abs(bx - self.bed_x) < 1e-3
            and abs(by - self.bed_y) < 1e-3
            and abs(bz - self.bed_z) < 1e-3
            and shape == self.bed_shape
        ):
            return
        self.bed_x, self.bed_y, self.bed_z = float(bx), float(by), float(bz)
        self.bed_shape = shape
        self._refresh_plate_offsets()
        self._rebuild_bed_geometry()
        self.update()

    def set_cut_plane(
        self,
        ox: float,
        oy: float,
        oz: float,
        rot_x: float,
        rot_y: float,
        rot_z: float,
        emit_signal: bool = True,
    ) -> None:
        self.cut_origin = np.array([float(ox), float(oy), float(oz)], dtype=np.float64)
        self.cut_rot_x = float(rot_x)
        self.cut_rot_y = float(rot_y)
        self.cut_rot_z = float(rot_z)
        self._rebuild_cut_plane_geometry()
        if emit_signal:
            self.cut_plane_changed.emit(
                float(self.cut_origin[0]),
                float(self.cut_origin[1]),
                float(self.cut_origin[2]),
                self.cut_rot_x,
                self.cut_rot_y,
                self.cut_rot_z,
            )
        self.update()

    def cut_plane_normal(self) -> np.ndarray:
        rot = _euler_matrix_xyz(self.cut_rot_x, self.cut_rot_y, self.cut_rot_z)
        n = rot @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
        nl = float(np.linalg.norm(n))
        return n / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

    def _reset_cut_plane_to_selected(self) -> None:
        inst = self._instances_by_id.get(self._selected_id) if self._selected_id else None
        if inst is not None:
            mesh = inst.transformed_mesh()
            b0, b1 = mesh.bounds[0], mesh.bounds[1]
            c = 0.5 * (b0 + b1)
            self.cut_origin = np.array([float(c[0]), float(c[1]), float(c[2])], dtype=np.float64)
            span = max(float(np.max(mesh.extents)), 20.0)
            self._cut_plane_half_size = max(18.0, span * 0.72)
        else:
            self.cut_origin = np.array([self.bed_x * 0.5, self.bed_y * 0.5, 15.0], dtype=np.float64)
            self._cut_plane_half_size = 35.0
        self.cut_rot_x = 0.0
        self.cut_rot_y = 0.0
        self.cut_rot_z = 0.0
        self._rebuild_cut_plane_geometry()
        self.cut_plane_changed.emit(
            float(self.cut_origin[0]),
            float(self.cut_origin[1]),
            float(self.cut_origin[2]),
            self.cut_rot_x,
            self.cut_rot_y,
            self.cut_rot_z,
        )

    def set_paint_brush(self, radius_mm: float, mode: int) -> None:
        self.paint_brush_radius = max(0.8, min(35.0, float(radius_mm)))
        self.paint_brush_mode = int(mode)
        self._rebuild_brush_reticle()
        self.update()

    def set_wipe_tower_preview(
        self, enabled: bool, x: float, y: float, w: float, d: float, h: float = 20.0
    ) -> None:
        if not enabled or w <= 0.0 or d <= 0.0:
            self._wipe_tower_mesh_cpu = np.zeros((0, 9), dtype=np.float32)
            self._wipe_tower_lines_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_wipe = True
            self.update()
            return

        h_box = max(4.0, float(h))
        box = trimesh.creation.box(extents=[float(w), float(d), h_box])
        box.apply_translation([float(x) + 0.5 * float(w), float(y) + 0.5 * float(d), 0.5 * h_box])
        self._wipe_tower_mesh_cpu = _mesh_to_lit_buffer(box, (0.28, 0.72, 0.88))

        x0, y0, x1, y1, z1 = float(x), float(y), float(x + w), float(y + d), h_box
        c = (0.38, 0.84, 0.98)
        edges = [
            ((x0, y0, 0.08), (x1, y0, 0.08)),
            ((x1, y0, 0.08), (x1, y1, 0.08)),
            ((x1, y1, 0.08), (x0, y1, 0.08)),
            ((x0, y1, 0.08), (x0, y0, 0.08)),
            ((x0, y0, z1), (x1, y0, z1)),
            ((x1, y0, z1), (x1, y1, z1)),
            ((x1, y1, z1), (x0, y1, z1)),
            ((x0, y1, z1), (x0, y0, z1)),
            ((x0, y0, 0.08), (x0, y0, z1)),
            ((x1, y0, 0.08), (x1, y0, z1)),
            ((x1, y1, 0.08), (x1, y1, z1)),
            ((x0, y1, 0.08), (x0, y1, z1)),
        ]
        line_pts = []
        for a, b in edges:
            line_pts.append([a[0], a[1], a[2], *c])
            line_pts.append([b[0], b[1], b[2], *c])
        self._wipe_tower_lines_cpu = np.asarray(line_pts, dtype=np.float32)
        self._dirty_wipe = True
        self.update()

    def set_workspace_plates(
        self,
        plates: List[BuildPlate],
        active_plate_idx: int,
        selected_id: Optional[str],
        capsules: Optional[List[Capsule]] = None,
    ) -> None:
        """Render all build plates side-by-side / in a 3D grid with inactive plates dimmed."""
        n = max(1, len(plates))
        a_idx = max(0, min(n - 1, int(active_plate_idx)))
        prev_offset = self._active_plate_offset.copy()
        prev_active = self._active_plate_idx

        self._num_plates = n
        self._active_plate_idx = a_idx
        self._refresh_plate_offsets()

        # Shift camera target smoothly along with the newly active plate if active plate changed
        if a_idx != prev_active and np.any(np.abs(self._active_plate_offset - prev_offset) > 1e-4):
            self.cam_target = self.cam_target + (self._active_plate_offset - prev_offset)
            self._refresh_camera_eye()

        # Build dimmed geometry + picking caches for all inactive plates in world space
        inactive_chunks: List[np.ndarray] = []
        self._inactive_pick_instances = []
        muted_col = (0.52, 0.54, 0.58)

        for p_idx, plate in enumerate(plates):
            if p_idx == a_idx:
                continue
            ox, oy = self._plate_offsets[p_idx]
            offset_vec = np.array([ox, oy, 0.0], dtype=np.float64)
            offset_f32 = np.array([ox, oy, 0.0], dtype=np.float32)
            for inst in plate.instances:
                mesh = inst.transformed_mesh()
                if mesh is None or len(mesh.faces) == 0:
                    continue
                buf = _mesh_to_lit_buffer(mesh, muted_col)
                if len(buf) > 0:
                    buf[:, 0:3] += offset_f32[None, :]
                    inactive_chunks.append(buf)
                tris_f64 = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64) + offset_vec[None, None, :]
                bmin = np.asarray(mesh.bounds[0], dtype=np.float64) + offset_vec
                bmax = np.asarray(mesh.bounds[1], dtype=np.float64) + offset_vec
                self._inactive_pick_instances.append((p_idx, inst.instance_id, bmin, bmax, tris_f64))

        self._inactive_mesh_cpu = (
            np.vstack(inactive_chunks) if inactive_chunks else np.zeros((0, 9), dtype=np.float32)
        )

        # Populate active plate instances & overlays (in active-plate-local coordinates)
        active_instances = plates[a_idx].instances if plates else []
        self.set_scene_instances(active_instances, selected_id, capsules=capsules)

    def set_scene_instances(
        self,
        instances: List[ModelInstance],
        selected_id: Optional[str],
        capsules: Optional[List[Capsule]] = None,
    ) -> None:
        self._instances_by_id = {inst.instance_id: inst for inst in instances}
        self._selected_id = selected_id
        self._pick_instances = []

        chunks: List[np.ndarray] = []
        sel_box_lines: List[List[float]] = []

        for inst in instances:
            mesh = inst.transformed_mesh()
            if mesh is None or len(mesh.faces) == 0:
                continue
            is_sel = inst.instance_id == selected_id
            col = (0.95, 0.77, 0.13) if is_sel else (0.64, 0.56, 0.28)
            paint_arr = inst.paint_support if (inst.paint_support is not None and len(inst.paint_support) == len(mesh.faces)) else None
            chunks.append(_mesh_to_lit_buffer(mesh, col, paint_support=paint_arr))

            tris_f64 = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64)
            bmin = np.asarray(mesh.bounds[0], dtype=np.float64)
            bmax = np.asarray(mesh.bounds[1], dtype=np.float64)
            self._pick_instances.append((inst.instance_id, bmin, bmax, tris_f64))

            if is_sel:
                self._gizmo_center = 0.5 * (bmin + bmax)
                span = max(float(np.max(bmax - bmin)), 16.0)
                self._gizmo_size = max(14.0, min(32.0, span * 0.45))
                self._cut_plane_half_size = max(18.0, span * 0.72)

                pad = 1.2
                x0, y0, z0 = float(bmin[0] - pad), float(bmin[1] - pad), max(0.06, float(bmin[2]))
                x1, y1, z1 = float(bmax[0] + pad), float(bmax[1] + pad), float(bmax[2] + pad)
                yc = (0.98, 0.82, 0.14)
                tick = min(6.0, 0.22 * (z1 - z0))
                segs = [
                    ((x0, y0, 0.08), (x1, y0, 0.08)),
                    ((x1, y0, 0.08), (x1, y1, 0.08)),
                    ((x1, y1, 0.08), (x0, y1, 0.08)),
                    ((x0, y1, 0.08), (x0, y0, 0.08)),
                    ((x0, y0, 0.08), (x0, y0, 0.08 + tick)),
                    ((x1, y0, 0.08), (x1, y0, 0.08 + tick)),
                    ((x1, y1, 0.08), (x1, y1, 0.08 + tick)),
                    ((x0, y1, 0.08), (x0, y1, 0.08 + tick)),
                ]
                for a, b in segs:
                    sel_box_lines.append([a[0], a[1], a[2], *yc])
                    sel_box_lines.append([b[0], b[1], b[2], *yc])

        self._mesh_cpu = np.vstack(chunks) if chunks else np.zeros((0, 9), dtype=np.float32)
        self._selection_box_cpu = (
            np.asarray(sel_box_lines, dtype=np.float32)
            if sel_box_lines
            else np.zeros((0, 6), dtype=np.float32)
        )
        self.set_wireframe(capsules)
        self._dirty_mesh = True

        if self.edit_tool == "place_on_face":
            self._rebuild_placeable_faces()
        else:
            self._clear_placeable_faces()
        self._rebuild_gizmo_geometry()
        self._rebuild_cut_plane_geometry()
        self.update()

    def set_mesh(self, mesh: Optional[trimesh.Trimesh], capsules: Optional[List[Capsule]] = None) -> None:
        self._instances_by_id = {}
        self._selected_id = None
        self._pick_instances = []
        self._selection_box_cpu = np.zeros((0, 6), dtype=np.float32)
        self._mesh_cpu = (
            _mesh_to_lit_buffer(mesh, (0.93, 0.76, 0.14))
            if (mesh is not None and len(mesh.faces) > 0)
            else np.zeros((0, 9), dtype=np.float32)
        )
        self.set_wireframe(capsules)
        self._dirty_mesh = True
        self.update()

    def set_wireframe(self, capsules: Optional[List[Capsule]]) -> None:
        if not capsules:
            self._wireframe_cpu = np.zeros((0, 6), dtype=np.float32)
        else:
            step = max(1, len(capsules) // 6000)
            sub = capsules[::step]
            pts = np.empty((len(sub) * 2, 6), dtype=np.float32)
            for idx, cap in enumerate(sub):
                pts[2 * idx, 0:3] = cap.a
                pts[2 * idx + 1, 0:3] = cap.b
            pts[:, 3:6] = np.array([0.05, 0.82, 0.95], dtype=np.float32)
            self._wireframe_cpu = pts
        self._dirty_wire = True
        self.update()

    def set_gcode_preview(
        self,
        data: Optional[GCodePreviewData],
        slice_result: Optional[SliceResult] = None,
    ) -> None:
        self.preview_data = data
        if data is None or data.num_layers == 0:
            self._all_beads_cpu = np.zeros((0, 48, 9), dtype=np.float32)
            self._all_caps_cpu = np.zeros((0, 24, 9), dtype=np.float32)
            self._all_caps_seg_idx = np.zeros((0,), dtype=np.int32)
            self._resin_cycles = []
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_resin_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)
            self._dirty_preview = True
            self.update()
            return

        self._all_beads_cpu, self._all_caps_cpu, self._all_caps_seg_idx = _build_extrusion_beads(
            data.ext_p0,
            data.ext_p1,
            data.ext_width,
            data.ext_height,
            data.ext_type,
            layers=data.ext_layer,
        )
        self._resin_cycles = _build_filled_resin_cycles(slice_result)
        self.layer_min = 1
        self.layer_max = data.num_layers
        self.top_move_limit = data.layer_move_counts[-1] if data.layer_move_counts else 10**9
        self._rebuild_preview_slice()

    def set_preview_filter(
        self,
        layer_min: Optional[int] = None,
        layer_max: Optional[int] = None,
        top_move_limit: Optional[int] = None,
        visible_types: Optional[Dict[int, bool]] = None,
        show_travels: Optional[bool] = None,
        show_dosing: Optional[bool] = None,
    ) -> None:
        if layer_min is not None:
            self.layer_min = max(1, layer_min)
        if layer_max is not None:
            self.layer_max = max(self.layer_min, layer_max)
        if top_move_limit is not None:
            self.top_move_limit = top_move_limit
        if visible_types is not None:
            self.visible_types = dict(visible_types)
        if show_travels is not None:
            self.show_travels = show_travels
        if show_dosing is not None:
            self.show_dosing = show_dosing
        self._rebuild_preview_slice()

    def _refresh_camera_eye(self) -> np.ndarray:
        yaw = math.radians(self.cam_yaw)
        pitch = math.radians(self.cam_pitch)
        cp = math.cos(pitch)
        self._last_eye = self.cam_target + self.cam_dist * np.array(
            [cp * math.sin(yaw), -cp * math.cos(yaw), math.sin(pitch)], dtype=np.float64
        )
        return self._last_eye

    def fit_to_mesh(self, mesh: Optional[trimesh.Trimesh]) -> None:
        if mesh is None or len(mesh.vertices) == 0:
            self.fit_to_bed()
            return
        bounds = mesh.bounds
        center = 0.5 * (bounds[0] + bounds[1])
        span = max(float(np.max(mesh.extents)), 15.0)
        self.cam_target = center.astype(np.float64) + self._active_plate_offset
        self.cam_dist = max(45.0, span * 2.2)
        self._refresh_camera_eye()
        self.update()

    def fit_to_bed(self) -> None:
        self.cam_target = (
            np.array([self.bed_x * 0.5, self.bed_y * 0.5, 12.0], dtype=np.float64)
            + self._active_plate_offset
        )
        self.cam_dist = max(self.bed_x, self.bed_y) * 1.35
        self._refresh_camera_eye()
        self.update()

    def set_camera_preset(self, preset: str) -> None:
        if preset == "iso":
            self.cam_yaw, self.cam_pitch = -42.0, 34.0
        elif preset == "top":
            self.cam_yaw, self.cam_pitch = 0.0, 88.5
        elif preset == "bottom":
            self.cam_yaw, self.cam_pitch = -35.0, -58.0
        elif preset == "front":
            self.cam_yaw, self.cam_pitch = 0.0, 6.0
        elif preset == "right":
            self.cam_yaw, self.cam_pitch = -90.0, 10.0
        self._refresh_camera_eye()
        self.update()

    # -------------------------------------------------------------------------
    # 3D Gizmos, Cut Plane & Paint Reticle Builders
    # -------------------------------------------------------------------------

    def _rebuild_gizmo_geometry(self) -> None:
        self._gizmo_axes = []
        if self.edit_tool not in ("move", "rotate", "scale") or not self._selected_id or self._selected_id not in self._instances_by_id:
            self._gizmo_lit_cpu = np.zeros((0, 9), dtype=np.float32)
            self._gizmo_lines_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_gizmo = True
            return

        c = self._gizmo_center
        sz = self._gizmo_size
        axis_specs = [
            ("x", np.array([1.0, 0.0, 0.0]), (0.95, 0.25, 0.25)),
            ("y", np.array([0.0, 1.0, 0.0]), (0.20, 0.86, 0.38)),
            ("z", np.array([0.0, 0.0, 1.0]), (0.24, 0.58, 0.98)),
        ]

        lines: List[List[float]] = []
        lit_chunks: List[np.ndarray] = []

        if self.edit_tool in ("move", "scale"):
            for name, axis, col in axis_specs:
                tip = c + axis * sz
                self._gizmo_axes.append((name, c.copy(), tip.copy()))
                lines.append([c[0], c[1], c[2], *col])
                lines.append([tip[0], tip[1], tip[2], *col])

                if self.edit_tool == "move":
                    cone = trimesh.creation.cone(radius=sz * 0.085, height=sz * 0.24, sections=10)
                    # Cone points along +Z by default; align to axis
                    if name == "x":
                        cone.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2.0, [0, 1, 0]))
                    elif name == "y":
                        cone.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2.0, [1, 0, 0]))
                    cone.apply_translation(tip)
                    lit_chunks.append(_mesh_to_lit_buffer(cone, col))
                else:
                    cube = trimesh.creation.box(extents=[sz * 0.14] * 3)
                    cube.apply_translation(tip)
                    lit_chunks.append(_mesh_to_lit_buffer(cube, col))

        elif self.edit_tool == "rotate":
            n_seg = 48
            rad = sz * 0.88
            for name, axis, col in axis_specs:
                u = np.array([0.0, 1.0, 0.0]) if abs(axis[0]) > 0.5 else np.array([1.0, 0.0, 0.0])
                u = u - np.dot(u, axis) * axis
                u /= max(float(np.linalg.norm(u)), 1e-9)
                v = np.cross(axis, u)
                self._gizmo_axes.append((name, c.copy(), c + axis * rad))
                for i in range(n_seg):
                    a0 = 2.0 * math.pi * i / n_seg
                    a1 = 2.0 * math.pi * (i + 1) / n_seg
                    p0 = c + rad * (math.cos(a0) * u + math.sin(a0) * v)
                    p1 = c + rad * (math.cos(a1) * u + math.sin(a1) * v)
                    lines.append([p0[0], p0[1], p0[2], *col])
                    lines.append([p1[0], p1[1], p1[2], *col])

        self._gizmo_lines_cpu = np.asarray(lines, dtype=np.float32) if lines else np.zeros((0, 6), dtype=np.float32)
        self._gizmo_lit_cpu = np.vstack(lit_chunks) if lit_chunks else np.zeros((0, 9), dtype=np.float32)
        self._dirty_gizmo = True

    def _rebuild_cut_plane_geometry(self) -> None:
        if self.edit_tool != "cut":
            self._cut_plane_lit_cpu = np.zeros((0, 9), dtype=np.float32)
            self._cut_plane_lines_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_cut = True
            return

        rot = _euler_matrix_xyz(self.cut_rot_x, self.cut_rot_y, self.cut_rot_z)
        u = rot @ np.array([1.0, 0.0, 0.0], dtype=np.float64)
        v = rot @ np.array([0.0, 1.0, 0.0], dtype=np.float64)
        n = rot @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
        hs = self._cut_plane_half_size
        o = self.cut_origin

        p00 = o - hs * u - hs * v
        p10 = o + hs * u - hs * v
        p11 = o + hs * u + hs * v
        p01 = o - hs * u + hs * v

        col_q = (0.98, 0.82, 0.14)
        nf = n.astype(np.float32)
        quad = np.array(
            [
                [*p00, *nf, *col_q],
                [*p10, *nf, *col_q],
                [*p11, *nf, *col_q],
                [*p00, *nf, *col_q],
                [*p11, *nf, *col_q],
                [*p01, *nf, *col_q],
                # Backface so visible from below too
                [*p00, *(-nf), *col_q],
                [*p11, *(-nf), *col_q],
                [*p10, *(-nf), *col_q],
                [*p00, *(-nf), *col_q],
                [*p01, *(-nf), *col_q],
                [*p11, *(-nf), *col_q],
            ],
            dtype=np.float32,
        )

        # Glowing handle sphere at plane center + normal cone arrow
        handle = trimesh.creation.icosphere(subdivisions=1, radius=max(2.0, hs * 0.065))
        handle.apply_translation(o)
        handle_buf = _mesh_to_lit_buffer(handle, (1.0, 0.90, 0.25))
        self._cut_plane_lit_cpu = np.vstack([quad, handle_buf])

        # Frame + crosshairs + normal shaft
        frame_c = (1.0, 0.88, 0.18)
        norm_c = (0.12, 0.88, 1.0)
        lines = [
            [*p00, *frame_c], [*p10, *frame_c],
            [*p10, *frame_c], [*p11, *frame_c],
            [*p11, *frame_c], [*p01, *frame_c],
            [*p01, *frame_c], [*p00, *frame_c],
            [*(o - hs * u), *frame_c], [*(o + hs * u), *frame_c],
            [*(o - hs * v), *frame_c], [*(o + hs * v), *frame_c],
            [*o, *norm_c], [*(o + n * (hs * 0.55)), *norm_c],
        ]
        self._cut_plane_lines_cpu = np.asarray(lines, dtype=np.float32)
        self._dirty_cut = True

    def _rebuild_brush_reticle(self) -> None:
        if self.edit_tool != "paint_supports" or self._brush_hover_pt is None or self._brush_hover_norm is None:
            self._brush_reticle_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_brush = True
            return

        pt = self._brush_hover_pt + self._brush_hover_norm * 0.20
        n = self._brush_hover_norm
        u = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
        u = u - np.dot(u, n) * n
        u /= max(float(np.linalg.norm(u)), 1e-9)
        v = np.cross(n, u)

        r = self.paint_brush_radius
        if self.paint_brush_mode > 0:
            col = (0.18, 0.96, 0.50)
        elif self.paint_brush_mode < 0:
            col = (0.98, 0.28, 0.28)
        else:
            col = (0.96, 0.96, 0.96)

        n_seg = 28
        lines: List[List[float]] = []
        for i in range(n_seg):
            a0 = 2.0 * math.pi * i / n_seg
            a1 = 2.0 * math.pi * (i + 1) / n_seg
            q0 = pt + r * (math.cos(a0) * u + math.sin(a0) * v)
            q1 = pt + r * (math.cos(a1) * u + math.sin(a1) * v)
            lines.append([q0[0], q0[1], q0[2], *col])
            lines.append([q1[0], q1[1], q1[2], *col])
        self._brush_reticle_cpu = np.asarray(lines, dtype=np.float32)
        self._dirty_brush = True

    # -------------------------------------------------------------------------
    # Place-on-Face Overlay Buffers
    # -------------------------------------------------------------------------

    def _clear_placeable_faces(self) -> None:
        self._placeable_faces = []
        self._hovered_face_idx = -1
        self._place_faces_cpu = np.zeros((0, 9), dtype=np.float32)
        self._place_hover_cpu = np.zeros((0, 9), dtype=np.float32)
        self._dirty_place = True

    def _rebuild_placeable_faces(self) -> None:
        inst = self._instances_by_id.get(self._selected_id) if self._selected_id else None
        if inst is None:
            self._clear_placeable_faces()
            return
        self._placeable_faces = inst.placeable_faces()
        if self._hovered_face_idx >= len(self._placeable_faces):
            self._hovered_face_idx = -1
        self._rebuild_placeable_face_buffers()

    def _rebuild_placeable_face_buffers(self) -> None:
        if not self._placeable_faces:
            self._place_faces_cpu = np.zeros((0, 9), dtype=np.float32)
            self._place_hover_cpu = np.zeros((0, 9), dtype=np.float32)
            self._dirty_place = True
            return

        base_chunks: List[np.ndarray] = []
        hover_chunks: List[np.ndarray] = []

        for idx, face in enumerate(self._placeable_faces):
            tris = face.triangles
            if len(tris) == 0:
                continue
            buf = np.empty((len(tris), 3, 9), dtype=np.float32)
            buf[:, :, 0:3] = tris
            buf[:, :, 3:6] = face.normal.astype(np.float32)[None, None, :]
            if idx == self._hovered_face_idx:
                buf[:, :, 6:9] = np.array([1.0, 0.94, 0.22], dtype=np.float32)
                hover_chunks.append(buf.reshape(-1, 9))
            else:
                buf[:, :, 6:9] = np.array([0.10, 0.80, 0.98], dtype=np.float32)
                base_chunks.append(buf.reshape(-1, 9))

        self._place_faces_cpu = np.vstack(base_chunks) if base_chunks else np.zeros((0, 9), dtype=np.float32)
        self._place_hover_cpu = np.vstack(hover_chunks) if hover_chunks else np.zeros((0, 9), dtype=np.float32)
        self._dirty_place = True

    # -------------------------------------------------------------------------
    # Build Plate & Grid Geometry (Theme-Aware, Rectangular or Circular)
    # -------------------------------------------------------------------------

    def _rebuild_bed_geometry(self) -> None:
        bx, by, bz = self.bed_x, self.bed_y, self.bed_z
        pal = self._palette
        z0 = -0.08
        # Active bed plate: rich, crisp, high-visibility opaque surface
        col = tuple(min(1.0, c * 1.18 + 0.03) for c in pal.gl_bed_plate)
        n = (0.0, 0.0, 1.0)
        lines_act: List[List[float]] = []
        lines_inact: List[List[float]] = []

        def _seg(
            target: List[List[float]],
            a: Tuple[float, float, float],
            b: Tuple[float, float, float],
            c: Tuple[float, float, float],
        ) -> None:
            target.append([a[0], a[1], a[2], c[0], c[1], c[2]])
            target.append([b[0], b[1], b[2], c[0], c[1], c[2]])

        minor_c = pal.gl_grid_minor
        major_c = pal.gl_grid_major
        border_act = (0.96, 0.78, 0.14)  # AlveoSlicer yellow active plate frame
        border_inact = pal.gl_grid_border
        cage_c = pal.gl_cage
        zg = -0.02

        if self.bed_shape == "circular":
            rad = 0.5 * min(bx, by)
            cx, cy = 0.5 * bx, 0.5 * by
            n_seg = 64
            plate_tris: List[List[float]] = []
            for i in range(n_seg):
                a0 = 2.0 * math.pi * i / n_seg
                a1 = 2.0 * math.pi * (i + 1) / n_seg
                p0 = (cx + rad * math.cos(a0), cy + rad * math.sin(a0))
                p1 = (cx + rad * math.cos(a1), cy + rad * math.sin(a1))
                plate_tris.append([cx, cy, z0, *n, *col])
                plate_tris.append([p0[0], p0[1], z0, *n, *col])
                plate_tris.append([p1[0], p1[1], z0, *n, *col])
                _seg(lines_act, (p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), border_act)
                _seg(lines_inact, (p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), border_inact)
            self._bed_plate_cpu = np.asarray(plate_tris, dtype=np.float32)

            # Clipped grid inside circle
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
            self._bed_plate_cpu = np.array(
                [
                    [0.0, 0.0, z0, *n, *col],
                    [bx, 0.0, z0, *n, *col],
                    [bx, by, z0, *n, *col],
                    [0.0, 0.0, z0, *n, *col],
                    [bx, by, z0, *n, *col],
                    [0.0, by, z0, *n, *col],
                ],
                dtype=np.float32,
            )

            x = 10.0
            while x < bx - 1e-3:
                c = major_c if (int(round(x)) % 50 == 0) else minor_c
                _seg(lines_act, (x, 0.0, zg), (x, by, zg), c)
                _seg(lines_inact, (x, 0.0, zg), (x, by, zg), c)
                x += 10.0

            y = 10.0
            while y < by - 1e-3:
                c = major_c if (int(round(y)) % 50 == 0) else minor_c
                _seg(lines_act, (0.0, y, zg), (bx, y, zg), c)
                _seg(lines_inact, (0.0, y, zg), (bx, y, zg), c)
                y += 10.0

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

        self._bed_grid_cpu = np.asarray(lines_act, dtype=np.float32)
        self._bed_grid_inactive_cpu = np.asarray(lines_inact, dtype=np.float32)
        self._dirty_bed = True

    def _rebuild_preview_slice(self) -> None:
        data = self.preview_data
        if data is None or len(self._all_beads_cpu) == 0:
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_resin_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)
            self._dirty_preview = True
            self.update()
            return

        in_range = (data.ext_layer >= self.layer_min) & (data.ext_layer <= self.layer_max)
        within_top_moves = (data.ext_layer < self.layer_max) | (data.ext_move_idx <= self.top_move_limit)

        type_allowed = np.array(
            [self.visible_types.get(i, True) for i in range(len(FEATURE_TYPES))], dtype=bool
        )
        type_ok = type_allowed[np.clip(data.ext_type, 0, len(type_allowed) - 1)]

        mask = in_range & within_top_moves & type_ok
        if np.any(mask):
            bead_verts = self._all_beads_cpu[mask].reshape(-1, 9)
            if len(self._all_caps_seg_idx) > 0:
                cap_mask = mask[self._all_caps_seg_idx]
                if np.any(cap_mask):
                    cap_verts = self._all_caps_cpu[cap_mask].reshape(-1, 9)
                    self._active_beads_cpu = np.vstack([bead_verts, cap_verts])
                else:
                    self._active_beads_cpu = bead_verts
            else:
                self._active_beads_cpu = bead_verts
        else:
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)

        # Filled resin: appears ONLY once a cure cycle's dosing layer has been reached/dosed!
        resin_type_id = FEATURE_TYPE_TO_ID.get("Filled resin", -1)
        show_resin = self.visible_types.get(resin_type_id, True) if resin_type_id >= 0 else False
        if show_resin and self._resin_cycles:
            resin_chunks: List[np.ndarray] = []
            for cyc_end_layer, layer_meshes in self._resin_cycles:
                # Has the pump dosed this cycle yet at (layer_max, top_move_limit)?
                if self.layer_max < cyc_end_layer:
                    continue
                if self.layer_max == cyc_end_layer and data.layer_move_counts:
                    idx0 = max(0, min(len(data.layer_move_counts) - 1, cyc_end_layer - 1))
                    if self.top_move_limit < data.layer_move_counts[idx0]:
                        continue
                # Cycle has been dosed! Display all layers of this cycle within [layer_min, layer_max]
                for l_idx, verts in layer_meshes:
                    if self.layer_min <= l_idx <= self.layer_max:
                        resin_chunks.append(verts)
            self._active_resin_cpu = (
                np.vstack(resin_chunks) if resin_chunks else np.zeros((0, 9), dtype=np.float32)
            )
        else:
            self._active_resin_cpu = np.zeros((0, 9), dtype=np.float32)

        top_mask = (data.ext_layer == self.layer_max) & (data.ext_move_idx <= self.top_move_limit)
        top_idxs = np.flatnonzero(top_mask)
        if len(top_idxs) > 0:
            tip = data.ext_p1[top_idxs[-1]]
            self._nozzle_cpu = self._build_nozzle_cone(float(tip[0]), float(tip[1]), float(tip[2]))
        else:
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)

        if self.show_travels and len(data.trv_p0) > 0:
            t_mask = (data.trv_layer >= self.layer_min) & (data.trv_layer <= self.layer_max)
            t_mask &= (data.trv_layer < self.layer_max) | (data.trv_move_idx <= self.top_move_limit)
            if np.any(t_mask):
                p0 = data.trv_p0[t_mask]
                p1 = data.trv_p1[t_mask]
                buf = np.empty((len(p0) * 2, 6), dtype=np.float32)
                buf[0::2, 0:3] = p0
                buf[1::2, 0:3] = p1
                buf[:, 3:6] = np.array(TRAVEL_COLOR, dtype=np.float32)
                self._active_travels_cpu = buf
            else:
                self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
        else:
            self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)

        if self.show_dosing and data.dosing_markers:
            active_markers = [
                m for m in data.dosing_markers if self.layer_min <= m.layer_idx <= self.layer_max
            ]
            d_lines: List[List[float]] = []
            c_pin = DOSING_COLOR
            c_top = (1.0, 0.92, 0.35)
            prev_by_layer: Dict[int, Tuple[float, float, float]] = {}

            for m in active_markers:
                is_top = m.layer_idx == self.layer_max
                col = c_pin if is_top else (0.0, 0.55, 0.72)
                pin_h = 4.2 if is_top else 1.8
                r = 1.0 if is_top else 0.55
                d_lines.append([m.x, m.y, m.z, *col])
                d_lines.append([m.x, m.y, m.z + pin_h, *col])
                d_lines.append([m.x - r, m.y, m.z + 0.25, *col])
                d_lines.append([m.x + r, m.y, m.z + 0.25, *col])
                d_lines.append([m.x, m.y - r, m.z + 0.25, *col])
                d_lines.append([m.x, m.y + r, m.z + 0.25, *col])
                if is_top and m.layer_idx in prev_by_layer:
                    px, py, pz = prev_by_layer[m.layer_idx]
                    d_lines.append([px, py, pz + pin_h * 0.7, *c_top])
                    d_lines.append([m.x, m.y, m.z + pin_h * 0.7, *c_top])
                prev_by_layer[m.layer_idx] = (m.x, m.y, m.z)

            self._active_dosing_cpu = (
                np.asarray(d_lines, dtype=np.float32) if d_lines else np.zeros((0, 6), dtype=np.float32)
            )
        else:
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)

        self._dirty_preview = True
        self.update()

    @staticmethod
    def _build_nozzle_cone(x: float, y: float, z: float) -> np.ndarray:
        sides = 10
        r = 1.6
        h = 5.0
        col = np.array([0.98, 0.82, 0.16], dtype=np.float32)
        tip = np.array([x, y, z + 0.1], dtype=np.float32)
        tris: List[List[float]] = []
        for i in range(sides):
            a0 = 2.0 * math.pi * i / sides
            a1 = 2.0 * math.pi * (i + 1) / sides
            p0 = np.array([x + r * math.cos(a0), y + r * math.sin(a0), z + h], dtype=np.float32)
            p1 = np.array([x + r * math.cos(a1), y + r * math.sin(a1), z + h], dtype=np.float32)
            norm = np.cross(p1 - tip, p0 - tip)
            nl = float(np.linalg.norm(norm))
            if nl > 1e-6:
                norm = norm / nl
            for v in (tip, p1, p0):
                tris.append([v[0], v[1], v[2], norm[0], norm[1], norm[2], col[0], col[1], col[2]])
        return np.asarray(tris, dtype=np.float32)

    # -------------------------------------------------------------------------
    # OpenGL Lifecycle
    # -------------------------------------------------------------------------

    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()

        self._lit_prog = QOpenGLShaderProgram(self)
        self._lit_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _LIT_VERT_SRC)
        self._lit_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _LIT_FRAG_SRC)
        self._lit_prog.link()

        self._line_prog = QOpenGLShaderProgram(self)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _LINE_VERT_SRC)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _LINE_FRAG_SRC)
        self._line_prog.link()

        self._gl_ready = True
        self._dirty_bed = True
        self._dirty_mesh = True
        self._dirty_wipe = True
        self._dirty_place = True
        self._dirty_wire = True
        self._dirty_gizmo = True
        self._dirty_cut = True
        self._dirty_brush = True
        self._dirty_preview = True

    def _upload_vbo(self, key: str, arr: np.ndarray) -> None:
        if key in self._vbos:
            vbo, _ = self._vbos[key]
        else:
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo.create()
        count = int(arr.shape[0])
        if count > 0:
            vbo.bind()
            raw = np.ascontiguousarray(arr, dtype=np.float32)
            vbo.allocate(raw.tobytes(), raw.nbytes)
            vbo.release()
        self._vbos[key] = (vbo, count)

    def _sync_vbos(self) -> None:
        if self._dirty_bed:
            self._upload_vbo("bed_plate", self._bed_plate_cpu)
            self._upload_vbo("bed_grid", self._bed_grid_cpu)
            self._upload_vbo("bed_grid_inactive", self._bed_grid_inactive_cpu)
            self._dirty_bed = False
        if self._dirty_mesh:
            self._upload_vbo("mesh", self._mesh_cpu)
            self._upload_vbo("inactive_mesh", self._inactive_mesh_cpu)
            self._upload_vbo("sel_box", self._selection_box_cpu)
            self._dirty_mesh = False
        if self._dirty_wipe:
            self._upload_vbo("wipe_mesh", self._wipe_tower_mesh_cpu)
            self._upload_vbo("wipe_lines", self._wipe_tower_lines_cpu)
            self._dirty_wipe = False
        if self._dirty_place:
            self._upload_vbo("place_faces", self._place_faces_cpu)
            self._upload_vbo("place_hover", self._place_hover_cpu)
            self._dirty_place = False
        if self._dirty_wire:
            self._upload_vbo("wireframe", self._wireframe_cpu)
            self._dirty_wire = False
        if self._dirty_gizmo:
            self._upload_vbo("gizmo_lit", self._gizmo_lit_cpu)
            self._upload_vbo("gizmo_lines", self._gizmo_lines_cpu)
            self._dirty_gizmo = False
        if self._dirty_cut:
            self._upload_vbo("cut_lit", self._cut_plane_lit_cpu)
            self._upload_vbo("cut_lines", self._cut_plane_lines_cpu)
            self._dirty_cut = False
        if self._dirty_brush:
            self._upload_vbo("brush_reticle", self._brush_reticle_cpu)
            self._dirty_brush = False
        if self._dirty_preview:
            self._upload_vbo("beads", self._active_beads_cpu)
            self._upload_vbo("resin", self._active_resin_cpu)
            self._upload_vbo("travels", self._active_travels_cpu)
            self._upload_vbo("dosing", self._active_dosing_cpu)
            self._upload_vbo("nozzle", self._nozzle_cpu)
            self._dirty_preview = False

    def _compute_mvp_and_eye(self) -> Tuple[QMatrix4x4, QVector3D]:
        w = max(1, self.width())
        h = max(1, self.height())
        aspect = w / float(h)

        yaw = math.radians(self.cam_yaw)
        pitch = math.radians(self.cam_pitch)
        cp = math.cos(pitch)
        eye = self.cam_target + self.cam_dist * np.array(
            [cp * math.sin(yaw), -cp * math.cos(yaw), math.sin(pitch)], dtype=np.float64
        )
        self._last_eye = eye
        view_dir = self.cam_target - eye

        proj = QMatrix4x4()
        proj.perspective(42.0, aspect, max(0.5, self.cam_dist * 0.004), max(2500.0, self.cam_dist * 14.0))

        view = QMatrix4x4()
        view.lookAt(
            QVector3D(float(eye[0]), float(eye[1]), float(eye[2])),
            QVector3D(float(self.cam_target[0]), float(self.cam_target[1]), float(self.cam_target[2])),
            QVector3D(0.0, 0.0, 1.0),
        )
        return proj * view, QVector3D(float(-view_dir[0]), float(-view_dir[1]), float(-view_dir[2]))

    @staticmethod
    def _translated_mvp(mvp: QMatrix4x4, ox: float, oy: float) -> QMatrix4x4:
        if abs(ox) < 1e-6 and abs(oy) < 1e-6:
            return mvp
        m = QMatrix4x4(mvp)
        m.translate(float(ox), float(oy), 0.0)
        return m

    def _draw_lit(self, key: str, mvp: QMatrix4x4, view_dir: QVector3D, alpha: float = 1.0) -> None:
        if key not in self._vbos or self._lit_prog is None or self._gl is None:
            return
        vbo, count = self._vbos[key]
        if count <= 0:
            return
        prog = self._lit_prog
        prog.bind()
        prog.setUniformValue(prog.uniformLocation("u_mvp"), mvp)
        prog.setUniformValue(prog.uniformLocation("u_alpha_v"), QVector2D(float(alpha), 0.0))
        prog.setUniformValue(prog.uniformLocation("u_view_dir"), view_dir)
        vbo.bind()
        loc_p = prog.attributeLocation("a_pos")
        loc_n = prog.attributeLocation("a_norm")
        loc_c = prog.attributeLocation("a_color")
        stride = 9 * 4
        prog.enableAttributeArray(loc_p)
        prog.enableAttributeArray(loc_n)
        prog.enableAttributeArray(loc_c)
        prog.setAttributeBuffer(loc_p, GL_FLOAT, 0, 3, stride)
        prog.setAttributeBuffer(loc_n, GL_FLOAT, 3 * 4, 3, stride)
        prog.setAttributeBuffer(loc_c, GL_FLOAT, 6 * 4, 3, stride)
        self._gl.glDrawArrays(GL_TRIANGLES, 0, count)
        prog.disableAttributeArray(loc_p)
        prog.disableAttributeArray(loc_n)
        prog.disableAttributeArray(loc_c)
        vbo.release()
        prog.release()

    def _draw_lines(self, key: str, mvp: QMatrix4x4, alpha: float = 1.0, width: float = 1.2) -> None:
        if key not in self._vbos or self._line_prog is None or self._gl is None:
            return
        vbo, count = self._vbos[key]
        if count <= 0:
            return
        prog = self._line_prog
        prog.bind()
        prog.setUniformValue(prog.uniformLocation("u_mvp"), mvp)
        prog.setUniformValue(prog.uniformLocation("u_alpha_v"), QVector2D(float(alpha), 0.0))
        vbo.bind()
        loc_p = prog.attributeLocation("a_pos")
        loc_c = prog.attributeLocation("a_color")
        stride = 6 * 4
        prog.enableAttributeArray(loc_p)
        prog.enableAttributeArray(loc_c)
        prog.setAttributeBuffer(loc_p, GL_FLOAT, 0, 3, stride)
        prog.setAttributeBuffer(loc_c, GL_FLOAT, 3 * 4, 3, stride)
        self._gl.glLineWidth(width)
        self._gl.glDrawArrays(GL_LINES, 0, count)
        prog.disableAttributeArray(loc_p)
        prog.disableAttributeArray(loc_c)
        vbo.release()
        prog.release()

    def paintGL(self) -> None:
        if not self._gl_ready or self._gl is None:
            return
        self._sync_vbos()

        gl = self._gl
        bg = self._palette.gl_bg
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glClearColor(bg[0], bg[1], bg[2], 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        mvp, view_dir = self._compute_mvp_and_eye()
        ax, ay = float(self._active_plate_offset[0]), float(self._active_plate_offset[1])
        mvp_active = self._translated_mvp(mvp, ax, ay)

        # 1. Build plates & grids — ALL invisible when looking from bottom up (eye_z < 0)!
        if not self.is_looking_from_bottom:
            # Active build plate: crisp, opaque (high visibility)
            self._draw_lit("bed_plate", mvp_active, view_dir, alpha=1.0)
            self._draw_lines("bed_grid", mvp_active, alpha=0.98, width=1.25)

            # Inactive build plates in side-by-side / grid layout (dimmed & translucent)
            if len(self._plate_offsets) > 1:
                plate_a = 0.24 if self.mode == "editor" else 0.14
                grid_a = 0.28 if self.mode == "editor" else 0.16
                gl.glDepthMask(False)
                for p_idx, (ox, oy) in enumerate(self._plate_offsets):
                    if p_idx == self._active_plate_idx:
                        continue
                    mvp_p = self._translated_mvp(mvp, ox, oy)
                    self._draw_lit("bed_plate", mvp_p, view_dir, alpha=plate_a)
                    self._draw_lines("bed_grid_inactive", mvp_p, alpha=grid_a, width=1.0)
                gl.glDepthMask(True)

        if self.mode == "editor":
            # Optional Wipe Tower footprint box (on active plate)
            if self._wipe_tower_lines_cpu.shape[0] > 0:
                self._draw_lines("wipe_lines", mvp_active, alpha=0.88, width=1.4)
                gl.glDepthMask(False)
                self._draw_lit("wipe_mesh", mvp_active, view_dir, alpha=0.22)
                gl.glDepthMask(True)

            # Selection bed footprint box (on active plate)
            if self._selection_box_cpu.shape[0] > 0:
                self._draw_lines("sel_box", mvp_active, alpha=0.95, width=1.8)

            has_wire = (
                self.show_infill_wireframe
                and self._wireframe_cpu.shape[0] > 0
                and self.edit_tool not in ("paint_supports", "cut")
            )
            if has_wire:
                self._draw_lines("wireframe", mvp_active, alpha=0.95, width=1.5)

            if self.show_mesh:
                if has_wire:
                    gl.glDepthMask(False)
                    self._draw_lit("mesh", mvp_active, view_dir, alpha=0.42)
                    gl.glDepthMask(True)
                else:
                    self._draw_lit("mesh", mvp_active, view_dir, alpha=1.0)

                # Ghosted models on inactive build plates (dimmed & translucent)
                if self._inactive_mesh_cpu.shape[0] > 0:
                    gl.glDepthMask(False)
                    self._draw_lit("inactive_mesh", mvp, view_dir, alpha=0.34)
                    gl.glDepthMask(True)

            # Place-on-Face clickable translucent face patches
            if self.edit_tool == "place_on_face" and (
                self._place_faces_cpu.shape[0] > 0 or self._place_hover_cpu.shape[0] > 0
            ):
                gl.glDepthMask(False)
                self._draw_lit("place_faces", mvp_active, view_dir, alpha=0.45)
                self._draw_lit("place_hover", mvp_active, view_dir, alpha=0.82)
                gl.glDepthMask(True)

            # 3D Transform Gizmos (Move / Rotate / Scale) drawn cleanly on top
            if self.edit_tool in ("move", "rotate", "scale") and (
                self._gizmo_lines_cpu.shape[0] > 0 or self._gizmo_lit_cpu.shape[0] > 0
            ):
                gl.glDisable(GL_DEPTH_TEST)
                self._draw_lines("gizmo_lines", mvp_active, alpha=0.96, width=2.0)
                self._draw_lit("gizmo_lit", mvp_active, view_dir, alpha=0.96)
                gl.glEnable(GL_DEPTH_TEST)

            # 3D Interactive Cut Plane
            if self.edit_tool == "cut" and self._cut_plane_lit_cpu.shape[0] > 0:
                self._draw_lines("cut_lines", mvp_active, alpha=0.96, width=1.8)
                gl.glDepthMask(False)
                self._draw_lit("cut_lit", mvp_active, view_dir, alpha=0.30)
                gl.glDepthMask(True)

            # 3D Paint-on Support Brush Reticle
            if self.edit_tool == "paint_supports" and self._brush_reticle_cpu.shape[0] > 0:
                gl.glDisable(GL_DEPTH_TEST)
                self._draw_lines("brush_reticle", mvp_active, alpha=0.98, width=2.0)
                gl.glEnable(GL_DEPTH_TEST)
        else:
            # PrusaSlicer-style volumetric G-code preview (rounded continuous filament beads + Filled Resin)
            self._draw_lit("beads", mvp_active, view_dir, alpha=1.0)
            if self._active_resin_cpu.shape[0] > 0:
                gl.glDepthMask(False)
                self._draw_lit("resin", mvp_active, view_dir, alpha=0.68)
                gl.glDepthMask(True)
            self._draw_lit("nozzle", mvp_active, view_dir, alpha=0.95)
            if self.show_travels:
                self._draw_lines("travels", mvp_active, alpha=0.55, width=1.0)
            if self.show_dosing:
                self._draw_lines("dosing", mvp_active, alpha=0.95, width=1.8)

    # -------------------------------------------------------------------------
    # Ray Casting & Interactive Picking
    # -------------------------------------------------------------------------

    def unproject_ray_world(self, sx: float, sy: float) -> Tuple[np.ndarray, np.ndarray]:
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        ndc_x = (2.0 * sx / w) - 1.0
        ndc_y = 1.0 - (2.0 * sy / h)

        mvp, _ = self._compute_mvp_and_eye()
        inv_mvp, ok = mvp.inverted()
        if not ok:
            return self.cam_target.copy(), np.array([0.0, 0.0, -1.0], dtype=np.float64)

        p_near = inv_mvp.map(QVector4D(ndc_x, ndc_y, -1.0, 1.0))
        p_far = inv_mvp.map(QVector4D(ndc_x, ndc_y, 1.0, 1.0))
        nw = p_near.w() if abs(p_near.w()) > 1e-8 else 1.0
        fw = p_far.w() if abs(p_far.w()) > 1e-8 else 1.0
        o = np.array([p_near.x() / nw, p_near.y() / nw, p_near.z() / nw], dtype=np.float64)
        f = np.array([p_far.x() / fw, p_far.y() / fw, p_far.z() / fw], dtype=np.float64)
        d = f - o
        dl = float(np.linalg.norm(d))
        if dl > 1e-9:
            d /= dl
        return o, d

    def unproject_ray(self, sx: float, sy: float) -> Tuple[np.ndarray, np.ndarray]:
        """Unproject ray into active-build-plate local coordinates."""
        o_w, d = self.unproject_ray_world(sx, sy)
        return o_w - self._active_plate_offset, d

    def _pick_inactive_instance_plate(self, sx: float, sy: float) -> Optional[int]:
        """Return plate index if clicking on a model resting on an inactive build plate."""
        if not self._inactive_pick_instances:
            return None
        orig, direction = self.unproject_ray_world(sx, sy)
        inv_dir = np.where(np.abs(direction) > 1e-9, 1.0 / direction, 1e12)
        best_plate: Optional[int] = None
        best_t = float("inf")
        for p_idx, _inst_id, bmin, bmax, tris in self._inactive_pick_instances:
            if not _ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                continue
            t = _ray_triangles_hit(orig, direction, tris)
            if t is not None and t < best_t:
                best_t = t
                best_plate = p_idx
        return best_plate

    def _pick_inactive_plate(self, sx: float, sy: float) -> Optional[int]:
        """Return plate index if clicking on the Z=0 build plate surface of an inactive bed."""
        if len(self._plate_offsets) <= 1:
            return None
        orig, direction = self.unproject_ray_world(sx, sy)
        if abs(direction[2]) < 1e-6:
            return None
        t = (0.0 - orig[2]) / direction[2]
        if t <= 0.0:
            return None
        hit = orig + t * direction
        hx, hy = float(hit[0]), float(hit[1])
        for p_idx, (ox, oy) in enumerate(self._plate_offsets):
            if p_idx == self._active_plate_idx:
                continue
            lx, ly = hx - ox, hy - oy
            if self.bed_shape == "circular":
                rad = 0.5 * min(self.bed_x, self.bed_y)
                cx, cy = 0.5 * self.bed_x, 0.5 * self.bed_y
                if (lx - cx) ** 2 + (ly - cy) ** 2 <= rad * rad:
                    return p_idx
            else:
                if 0.0 <= lx <= self.bed_x and 0.0 <= ly <= self.bed_y:
                    return p_idx
        return None

    def _ray_bed_plane_hit(self, sx: float, sy: float, z_plane: float = 0.0) -> Optional[Tuple[float, float]]:
        orig, direction = self.unproject_ray(sx, sy)
        if abs(direction[2]) < 1e-6:
            return None
        t = (z_plane - orig[2]) / direction[2]
        if t <= 0.0:
            return None
        hit = orig + t * direction
        return float(hit[0]), float(hit[1])

    def _pick_instance(self, sx: float, sy: float) -> Optional[str]:
        if not self._pick_instances:
            return None
        orig, direction = self.unproject_ray(sx, sy)
        inv_dir = np.where(np.abs(direction) > 1e-9, 1.0 / direction, 1e12)
        best_id: Optional[str] = None
        best_t = float("inf")
        for inst_id, bmin, bmax, tris in self._pick_instances:
            if not _ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                continue
            t = _ray_triangles_hit(orig, direction, tris)
            if t is not None and t < best_t:
                best_t = t
                best_id = inst_id
        return best_id

    def _pick_selected_surface(self, sx: float, sy: float) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if not self._selected_id:
            return None
        orig, direction = self.unproject_ray(sx, sy)
        inv_dir = np.where(np.abs(direction) > 1e-9, 1.0 / direction, 1e12)
        for inst_id, bmin, bmax, tris in self._pick_instances:
            if inst_id != self._selected_id:
                continue
            if not _ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                return None
            hit = _ray_triangles_hit_full(orig, direction, tris)
            if hit is not None:
                return hit[1], hit[2]
        return None

    def _pick_gizmo_axis(self, sx: float, sy: float) -> str:
        if not self._gizmo_axes or self.edit_tool not in ("move", "scale"):
            return ""
        orig, direction = self.unproject_ray(sx, sy)
        tol = max(2.2, self._gizmo_size * 0.14)
        best_axis = ""
        best_d = tol
        for axis_name, a, b in self._gizmo_axes:
            d, _ = _ray_segment_dist(orig, direction, a, b)
            if d < best_d:
                best_d = d
                best_axis = axis_name
        return best_axis

    def _hit_cut_plane(self, sx: float, sy: float) -> bool:
        if self.edit_tool != "cut":
            return False
        orig, direction = self.unproject_ray(sx, sy)
        n = self.cut_plane_normal()
        denom = float(np.dot(direction, n))
        if abs(denom) < 1e-5:
            return False
        t = float(np.dot(self.cut_origin - orig, n)) / denom
        if t <= 0.0:
            return False
        hit = orig + t * direction
        return float(np.linalg.norm(hit - self.cut_origin)) <= self._cut_plane_half_size * 1.45

    def _pick_placeable_face(self, sx: float, sy: float) -> int:
        if not self._placeable_faces:
            return -1
        orig, direction = self.unproject_ray(sx, sy)
        best_idx = -1
        best_t = float("inf")
        for idx, face in enumerate(self._placeable_faces):
            t = _ray_triangles_hit(orig, direction, face.triangles.astype(np.float64))
            if t is not None and t < best_t:
                best_t = t
                best_idx = idx
        return best_idx

    # -------------------------------------------------------------------------
    # Interactive Mouse Controls
    # -------------------------------------------------------------------------

    def _emit_paint_at(self, sx: float, sy: float, paint_val: int) -> bool:
        if not self._selected_id:
            return False
        hit = self._pick_selected_surface(sx, sy)
        if hit is None:
            return False
        pt, norm = hit
        self._brush_hover_pt = pt
        self._brush_hover_norm = norm
        self._rebuild_brush_reticle()
        self.support_painted.emit(
            self._selected_id,
            float(pt[0]),
            float(pt[1]),
            float(pt[2]),
            float(self.paint_brush_radius),
            int(paint_val),
        )
        return True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        self._last_mouse = pos
        self._press_mouse = pos
        btn = event.button()
        mods = event.modifiers()
        self._drag_axis = ""
        self._pressed_on_empty = False

        # Paint-on Supports: Left paints active mode (Shift=Erase), Right paints Blocker (-1)
        if self.mode == "editor" and self.edit_tool == "paint_supports":
            if btn == Qt.MouseButton.MiddleButton:
                self._drag_mode = "pan"
                return
            if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
                if mods & Qt.KeyboardModifier.AltModifier:
                    self._drag_mode = "orbit"
                    return
                val = 0 if (mods & Qt.KeyboardModifier.ShiftModifier) else (
                    -1 if btn == Qt.MouseButton.RightButton else self.paint_brush_mode
                )
                if self._emit_paint_at(pos.x(), pos.y(), val):
                    self._drag_mode = f"paint:{val}"
                    return
                self._drag_mode = "pan" if btn == Qt.MouseButton.RightButton else "orbit"
                return

        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._drag_mode = "pan"
            return

        if btn == Qt.MouseButton.LeftButton:
            # 1. Cut Plane interactive drag (vertical drag moves Z/normal, Shift+drag tilts plane)
            if self.mode == "editor" and self.edit_tool == "cut":
                if self._hit_cut_plane(pos.x(), pos.y()):
                    self._drag_mode = "cut_tilt" if (mods & Qt.KeyboardModifier.ShiftModifier) else "cut_move"
                    self._drag_init_cut_origin = self.cut_origin.copy()
                    self._drag_init_cut_rot = (self.cut_rot_x, self.cut_rot_y, self.cut_rot_z)
                    return

            # 2. Place-on-Face click
            if self.mode == "editor" and self.edit_tool == "place_on_face":
                face_idx = self._pick_placeable_face(pos.x(), pos.y())
                if face_idx >= 0:
                    face = self._placeable_faces[face_idx]
                    self._drag_mode = ""
                    self.face_picked.emit(face.normal.copy())
                    return

            # 3. 3D Gizmo Axis Pick (Move / Scale)
            if self.mode == "editor" and self._selected_id and self.edit_tool in ("move", "scale"):
                axis_hit = self._pick_gizmo_axis(pos.x(), pos.y())
                inst = self._instances_by_id.get(self._selected_id)
                if axis_hit and inst is not None:
                    self._drag_mode = f"gizmo_{self.edit_tool}"
                    self._drag_axis = axis_hit
                    self._drag_inst_id = self._selected_id
                    self._drag_init_pos = (inst.pos_x, inst.pos_y, inst.pos_z)
                    self._drag_init_scale = inst.scale_x
                    return

            # 4. Object Pick & Direct Manipulation (active plate)
            hit_active_model = False
            if self.mode == "editor" and not (mods & Qt.KeyboardModifier.ShiftModifier):
                hit_id = self._pick_instance(pos.x(), pos.y())
                if hit_id is not None:
                    hit_active_model = True
                    if hit_id != self._selected_id:
                        self._selected_id = hit_id
                        self.instance_selected.emit(hit_id)

                    inst = self._instances_by_id.get(hit_id)
                    if inst is not None and self.edit_tool in ("move", "rotate", "scale"):
                        self._drag_mode = self.edit_tool
                        self._drag_inst_id = hit_id
                        if self.edit_tool == "move":
                            hit_xy = self._ray_bed_plane_hit(pos.x(), pos.y(), z_plane=inst.pos_z)
                            if hit_xy is not None:
                                self._drag_move_offset = (inst.pos_x - hit_xy[0], inst.pos_y - hit_xy[1])
                            else:
                                self._drag_move_offset = (0.0, 0.0)
                        elif self.edit_tool == "rotate":
                            self._drag_init_rot = (inst.rot_x, inst.rot_y, inst.rot_z)
                        elif self.edit_tool == "scale":
                            self._drag_init_scale = inst.scale_x
                        return

            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._drag_mode = "pan"
            else:
                self._drag_mode = "orbit"
                if self.mode == "editor" and not hit_active_model:
                    self._pressed_on_empty = True
        else:
            self._drag_mode = ""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()

        # Hover tracking (no drag)
        if not self._drag_mode:
            if self.mode == "editor" and self.edit_tool == "place_on_face":
                idx = self._pick_placeable_face(pos.x(), pos.y())
                if idx != self._hovered_face_idx:
                    self._hovered_face_idx = idx
                    self._rebuild_placeable_face_buffers()
                    self.update()
            elif self.mode == "editor" and self.edit_tool == "paint_supports":
                hit = self._pick_selected_surface(pos.x(), pos.y())
                if hit is not None:
                    self._brush_hover_pt, self._brush_hover_norm = hit
                else:
                    self._brush_hover_pt = None
                    self._brush_hover_norm = None
                self._rebuild_brush_reticle()
                self.update()
            return

        if self._last_mouse is None:
            return
        dx = pos.x() - self._last_mouse.x()
        dy = pos.y() - self._last_mouse.y()
        self._last_mouse = pos

        # Continuous Paint-on Supports stroke
        if self._drag_mode.startswith("paint:"):
            paint_val = int(self._drag_mode.split(":")[1])
            self._emit_paint_at(pos.x(), pos.y(), paint_val)
            return

        # Cut plane vertical translation or tilt
        if self._drag_mode == "cut_move" and self._press_mouse is not None:
            tot_dy = self._press_mouse.y() - pos.y()
            delta = tot_dy * (self.cam_dist * 0.0022)
            n = self.cut_plane_normal()
            self.cut_origin = self._drag_init_cut_origin + n * delta
            self.cut_origin[2] = max(0.2, min(self.bed_z, float(self.cut_origin[2])))
            self._rebuild_cut_plane_geometry()
            self.cut_plane_changed.emit(
                float(self.cut_origin[0]),
                float(self.cut_origin[1]),
                float(self.cut_origin[2]),
                self.cut_rot_x,
                self.cut_rot_y,
                self.cut_rot_z,
            )
            self.update()
            return

        if self._drag_mode == "cut_tilt" and self._press_mouse is not None:
            tot_dx = pos.x() - self._press_mouse.x()
            tot_dy = pos.y() - self._press_mouse.y()
            rx0, ry0, rz0 = self._drag_init_cut_rot
            self.cut_rot_x = max(-89.0, min(89.0, round(rx0 + tot_dy * 0.45, 1)))
            self.cut_rot_y = max(-89.0, min(89.0, round(ry0 + tot_dx * 0.45, 1)))
            self.cut_rot_z = rz0
            self._rebuild_cut_plane_geometry()
            self.cut_plane_changed.emit(
                float(self.cut_origin[0]),
                float(self.cut_origin[1]),
                float(self.cut_origin[2]),
                self.cut_rot_x,
                self.cut_rot_y,
                self.cut_rot_z,
            )
            self.update()
            return

        # Gizmo axis-constrained move
        if self._drag_mode == "gizmo_move" and self._drag_inst_id is not None and self._press_mouse is not None:
            tot_dx = pos.x() - self._press_mouse.x()
            tot_dy = self._press_mouse.y() - pos.y()
            scale_mm = self.cam_dist * 0.0025
            px0, py0, pz0 = self._drag_init_pos
            if self._drag_axis == "x":
                nx = max(0.0, min(self.bed_x, px0 + tot_dx * scale_mm))
                self.instance_moved.emit(self._drag_inst_id, round(nx, 2), py0)
            elif self._drag_axis == "y":
                ny = max(0.0, min(self.bed_y, py0 + (tot_dy + tot_dx * 0.35) * scale_mm))
                self.instance_moved.emit(self._drag_inst_id, px0, round(ny, 2))
            elif self._drag_axis == "z":
                nz = max(0.0, min(self.bed_z, pz0 + tot_dy * scale_mm))
                self.instance_moved_z.emit(self._drag_inst_id, round(nz, 2))
            return

        if self._drag_mode == "gizmo_scale" and self._drag_inst_id is not None and self._press_mouse is not None:
            tot_dx = pos.x() - self._press_mouse.x()
            tot_dy = self._press_mouse.y() - pos.y()
            factor = math.exp((tot_dx + tot_dy) * 0.0045)
            new_scale = max(5.0, min(2000.0, round(self._drag_init_scale * factor, 1)))
            self.instance_scaled.emit(self._drag_inst_id, new_scale)
            return

        if self._drag_mode == "move" and self._drag_inst_id is not None:
            inst = self._instances_by_id.get(self._drag_inst_id)
            if inst is not None:
                hit_xy = self._ray_bed_plane_hit(pos.x(), pos.y(), z_plane=inst.pos_z)
                if hit_xy is not None:
                    nx = max(0.0, min(self.bed_x, hit_xy[0] + self._drag_move_offset[0]))
                    ny = max(0.0, min(self.bed_y, hit_xy[1] + self._drag_move_offset[1]))
                    self.instance_moved.emit(self._drag_inst_id, nx, ny)
            return

        if self._drag_mode == "rotate" and self._drag_inst_id is not None and self._press_mouse is not None:
            tot_dx = pos.x() - self._press_mouse.x()
            tot_dy = pos.y() - self._press_mouse.y()
            rx0, ry0, rz0 = self._drag_init_rot
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                rx = ((rx0 + tot_dy * 0.6 + 180.0) % 360.0) - 180.0
                ry = ((ry0 + tot_dx * 0.6 + 180.0) % 360.0) - 180.0
                self.instance_rotated.emit(self._drag_inst_id, round(rx, 1), round(ry, 1), rz0)
            else:
                rz = ((rz0 + tot_dx * 0.6 + 180.0) % 360.0) - 180.0
                self.instance_rotated.emit(self._drag_inst_id, rx0, ry0, round(rz, 1))
            return

        if self._drag_mode == "scale" and self._drag_inst_id is not None and self._press_mouse is not None:
            tot_dx = pos.x() - self._press_mouse.x()
            tot_dy = pos.y() - self._press_mouse.y()
            factor = math.exp((tot_dx - tot_dy) * 0.0045)
            new_scale = max(5.0, min(2000.0, round(self._drag_init_scale * factor, 1)))
            self.instance_scaled.emit(self._drag_inst_id, new_scale)
            return

        if self._drag_mode == "orbit":
            self.cam_yaw -= dx * 0.45
            self.cam_pitch = max(-85.0, min(88.5, self.cam_pitch + dy * 0.45))
        elif self._drag_mode == "pan":
            yaw = math.radians(self.cam_yaw)
            pitch = math.radians(self.cam_pitch)
            right = np.array([math.cos(yaw), math.sin(yaw), 0.0], dtype=np.float64)
            up = np.array(
                [-math.sin(yaw) * math.sin(pitch), math.cos(yaw) * math.sin(pitch), math.cos(pitch)],
                dtype=np.float64,
            )
            scale = self.cam_dist * 0.0018
            self.cam_target = self.cam_target - right * (dx * scale) + up * (dy * scale)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        was_empty_click = (
            event.button() == Qt.MouseButton.LeftButton
            and self._pressed_on_empty
            and self._press_mouse is not None
            and math.hypot(pos.x() - self._press_mouse.x(), pos.y() - self._press_mouse.y()) <= 5.0
            and self.mode == "editor"
        )
        self._last_mouse = None
        self._press_mouse = None
        self._drag_mode = ""
        self._drag_axis = ""
        self._drag_inst_id = None
        self._pressed_on_empty = False

        if was_empty_click:
            # 1. Check if user clicked on a model on another (inactive) build plate
            hit_plate = self._pick_inactive_instance_plate(pos.x(), pos.y())
            if hit_plate is None:
                # 2. Check if user clicked on the surface of another (inactive) build plate
                hit_plate = self._pick_inactive_plate(pos.x(), pos.y())
            if hit_plate is not None and hit_plate != self._active_plate_idx:
                self.plate_selected.emit(int(hit_plate))
                return
            # 3. Clicked empty space -> deselect any currently selected model
            if self._selected_id is not None:
                self._selected_id = None
                self._selection_box_cpu = np.zeros((0, 6), dtype=np.float32)
                self._dirty_mesh = True
                self._rebuild_gizmo_geometry()
                self._clear_placeable_faces()
                self.selection_cleared.emit()
                self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 0.88 if delta > 0 else 1.14
        self.cam_dist = max(12.0, min(1600.0, self.cam_dist * factor))
        self.update()
