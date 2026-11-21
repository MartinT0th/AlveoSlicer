"""3D math, multi-plate grid layout, and vectorized raycasting utilities for the viewport."""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np


def euler_matrix_xyz(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """Build a 3x3 intrinsic XYZ Euler rotation matrix (``Rz @ Ry @ Rx``) from degrees."""
    rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return mz @ my @ mx


def plate_grid_offset(plate_idx: int, num_plates: int, bed_x: float, bed_y: float) -> Tuple[float, float]:
    """Compute world-space ``(ox, oy)`` grid offset for side-by-side multi-plate rendering."""
    if num_plates <= 1 or plate_idx <= 0:
        return 0.0, 0.0
    cols = num_plates if num_plates <= 3 else int(math.ceil(math.sqrt(num_plates)))
    gap = 38.0
    return float((plate_idx % cols) * (bed_x + gap)), float((plate_idx // cols) * (bed_y + gap))


def ray_intersects_aabb(
    orig: np.ndarray, inv_dir: np.ndarray, bmin: np.ndarray, bmax: np.ndarray
) -> bool:
    """Fast slab-method ray vs axis-aligned bounding-box intersection test."""
    t1 = (bmin - orig) * inv_dir
    t2 = (bmax - orig) * inv_dir
    return float(np.min(np.maximum(t1, t2))) >= max(0.0, float(np.max(np.minimum(t1, t2))))


def _moller_trumbore(
    orig: np.ndarray, direction: np.ndarray, tris: np.ndarray
) -> Optional[Tuple[int, float, np.ndarray, np.ndarray]]:
    """Shared vectorized Möller–Trumbore kernel returning ``(best_idx, best_t, e1, e2)`` or ``None``."""
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
    best_i = int(valid_idxs[int(np.argmin(t[valid_idxs]))])
    return best_i, float(t[best_i]), e1, e2


def ray_triangles_hit(
    orig: np.ndarray, direction: np.ndarray, tris: np.ndarray
) -> Optional[float]:
    """Vectorized ray-triangle intersection returning nearest positive ray distance ``t``, or ``None``."""
    res = _moller_trumbore(orig, direction, tris)
    return None if res is None else res[1]


def ray_triangles_hit_full(
    orig: np.ndarray, direction: np.ndarray, tris: np.ndarray
) -> Optional[Tuple[float, np.ndarray, np.ndarray]]:
    """Return ``(t, hit_point, unit_face_normal)`` for the nearest ray-triangle hit, or ``None``."""
    res = _moller_trumbore(orig, direction, tris)
    if res is None:
        return None
    best_i, best_t, e1, e2 = res
    hit_pt = orig + best_t * direction
    fn = np.cross(e1[best_i], e2[best_i])
    nl = float(np.linalg.norm(fn))
    fn = fn / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return best_t, hit_pt, fn


def ray_segment_dist(
    orig: np.ndarray, direction: np.ndarray, a: np.ndarray, b: np.ndarray
) -> Tuple[float, float]:
    """Compute shortest 3D distance between ray ``orig + t*direction`` (``t>=0``) and segment ``[a, b]``."""
    u, v, w = direction, b - a, orig - a
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
    return float(np.linalg.norm((orig + sc * u) - (a + tc * v))), sc
