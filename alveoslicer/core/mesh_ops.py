"""
3D Mesh Operations: Place-on-Face landing detection, normalization, and watertight plane cutting.

Isolated from scene state management so geometric mesh algorithms can be expanded
independently (e.g., hollowing, connectors/dowel pins, mesh repair, Boolean modifiers).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import shapely
import trimesh
from shapely.geometry import Polygon


@dataclass
class SimplifiedFace:
    """Clickable simplified resting plane on a 3D mesh for Place-on-Face alignment."""

    face_id: int
    normal: np.ndarray          # (3,) float64 outward unit normal in world coordinates
    centroid: np.ndarray        # (3,) float64 patch centroid
    area: float                 # Total patch area (mm^2)
    triangles: np.ndarray       # (M, 3, 3) float32 triangles forming this face patch


def rotation_matrix_aligning_vectors(v_from: np.ndarray, v_to: np.ndarray) -> np.ndarray:
    """Return a 4x4 homogeneous rotation matrix rotating unit vector ``v_from`` onto ``v_to``."""
    a = np.asarray(v_from, dtype=np.float64)
    b = np.asarray(v_to, dtype=np.float64)
    a = a / max(float(np.linalg.norm(a)), 1e-12)
    b = b / max(float(np.linalg.norm(b)), 1e-12)
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))

    if dot > 1.0 - 1e-7:
        return np.eye(4, dtype=np.float64)
    if dot < -1.0 + 1e-7:
        perp = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, perp)
        axis = axis / max(float(np.linalg.norm(axis)), 1e-12)
        return trimesh.transformations.rotation_matrix(math.pi, axis)

    return trimesh.transformations.rotation_matrix(math.acos(dot), np.cross(a, b))


def normalize_base_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Return a copy of ``mesh`` centered at XY=(0, 0) with lowest Z resting at 0."""
    m = mesh.copy()
    bounds = m.bounds
    cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
    cy = 0.5 * (bounds[0, 1] + bounds[1, 1])
    m.apply_translation([-cx, -cy, -bounds[0, 2]])
    return m


def compute_placeable_faces(
    mesh: trimesh.Trimesh,
    angle_tol_deg: float = 10.0,
    max_faces: int = 24,
) -> List[SimplifiedFace]:
    """
    Compute simplified clickable resting faces on ``mesh`` (PrusaSlicer Place-on-Face style):
      1. Extracts the convex hull so every candidate landing plane is physically stable.
      2. Merges near-coplanar hull triangles within ``angle_tol_deg`` and plane offset tolerance.
      3. Returns the top ``max_faces`` face patches sorted by area descending.
    """
    if mesh is None or len(mesh.faces) == 0:
        return []

    try:
        hull = mesh.convex_hull
        if hull is None or len(hull.faces) == 0:
            hull = mesh
    except Exception:
        hull = mesh

    tris = np.asarray(hull.triangles, dtype=np.float64)
    normals = np.asarray(hull.face_normals, dtype=np.float64)
    areas = np.asarray(hull.area_faces, dtype=np.float64)
    if len(tris) == 0:
        return []

    centroids = tris.mean(axis=1)
    diag = max(1.0, float(np.linalg.norm(hull.extents)))
    cos_tol = math.cos(math.radians(angle_tol_deg))
    plane_tol = max(0.25, 0.015 * diag)

    clusters: List[dict] = []
    for f_idx in np.argsort(-areas):
        a = float(areas[f_idx])
        if a < 1e-5:
            continue
        n = normals[f_idx]
        nl = float(np.linalg.norm(n))
        if nl < 1e-6:
            continue
        n = n / nl
        c = centroids[f_idx]

        matched = False
        for cl in clusters:
            cn = cl["normal"]
            if float(np.dot(cn, n)) >= cos_tol and abs(float(np.dot(cn, c)) - cl["d"]) <= plane_tol:
                cl["indices"].append(int(f_idx))
                cl["area"] += a
                cl["n_sum"] = cl["n_sum"] + n * a
                cl["c_sum"] = cl["c_sum"] + c * a
                nn_l = float(np.linalg.norm(cl["n_sum"]))
                if nn_l > 1e-6:
                    cl["normal"] = cl["n_sum"] / nn_l
                cl["d"] = float(np.dot(cl["normal"], cl["c_sum"] / cl["area"]))
                matched = True
                break

        if not matched:
            clusters.append({
                "indices": [int(f_idx)],
                "area": a,
                "normal": n.copy(),
                "n_sum": n * a,
                "c_sum": c * a,
                "d": float(np.dot(n, c)),
            })

    if not clusters:
        return []

    clusters.sort(key=lambda cl: cl["area"], reverse=True)
    min_keep_area = clusters[0]["area"] * 0.008
    faces_out: List[SimplifiedFace] = []
    for cl in clusters[:max_faces]:
        if cl["area"] < min_keep_area:
            continue
        idx_arr = np.asarray(cl["indices"], dtype=np.int64)
        n_unit = cl["normal"]
        # Offset 0.04 mm outward along normal to avoid Z-fighting over the underlying mesh
        tri_patch = (tris[idx_arr] + n_unit[None, None, :] * 0.04).astype(np.float32)
        faces_out.append(
            SimplifiedFace(
                face_id=len(faces_out),
                normal=n_unit.astype(np.float64),
                centroid=(cl["c_sum"] / cl["area"]).astype(np.float64),
                area=float(cl["area"]),
                triangles=tri_patch,
            )
        )
    return faces_out


def _cap_open_cut_half(half: trimesh.Trimesh, is_upper: bool) -> trimesh.Trimesh:
    """Close the planar Z = 0 cut boundary of an open half-mesh using GEOS Constrained Delaunay."""
    half = half.copy()
    half.merge_vertices(digits_vertex=5)
    half.remove_unreferenced_vertices()
    faces = np.asarray(half.faces, dtype=np.int64)
    verts = np.asarray(half.vertices, dtype=np.float64)
    if len(faces) == 0 or len(verts) == 0:
        return half

    # Identify directed boundary half-edges (u, v) whose opposite (v, u) is missing
    edge_set = set()
    for a, b, c in faces:
        ia, ib, ic = int(a), int(b), int(c)
        edge_set.update(((ia, ib), (ib, ic), (ic, ia)))

    adj: dict = defaultdict(list)
    for u, v in edge_set:
        if (v, u) not in edge_set:
            adj[u].append(v)
    if not adj:
        return half

    # Trace closed boundary vertex loops
    loops: List[List[int]] = []
    used = set()
    for start in list(adj.keys()):
        for nxt in adj[start]:
            if (start, nxt) in used:
                continue
            loop = [start]
            u, v = start, nxt
            while True:
                used.add((u, v))
                if v == start:
                    break
                loop.append(v)
                cands = [w for w in adj[v] if (v, w) not in used]
                if not cands:
                    break
                u, v = v, cands[0]
            if len(loop) >= 3:
                loops.append(loop)
    if not loops:
        return half

    # Classify loop nesting depth (even = outer ring, odd = interior hole)
    polys: List[Tuple[Polygon, List[int]]] = []
    for lp in loops:
        poly_2d = Polygon([(float(verts[i, 0]), float(verts[i, 1])) for i in lp])
        if not poly_2d.is_empty and poly_2d.area > 1e-8:
            polys.append((poly_2d, lp))
    if not polys:
        return half

    depths, parents = [0] * len(polys), [-1] * len(polys)
    for i, (pi, _) in enumerate(polys):
        pt = pi.representative_point()
        best_area = float("inf")
        for j, (pj, _) in enumerate(polys):
            if i != j and pj.area > pi.area and pj.contains(pt):
                depths[i] += 1
                if pj.area < best_area:
                    best_area, parents[i] = pj.area, j

    b_indices = np.array(sorted({idx for lp in loops for idx in lp}), dtype=np.int64)
    b_xy = verts[b_indices, :2]

    def _snap_vertex(x: float, y: float) -> int:
        return int(b_indices[int(np.argmin((b_xy[:, 0] - x) ** 2 + (b_xy[:, 1] - y) ** 2))])

    cap_faces: List[Tuple[int, int, int]] = []
    for i, (pi, _lp) in enumerate(polys):
        if depths[i] % 2 != 0:
            continue
        holes = [list(polys[j][0].exterior.coords) for j in range(len(polys)) if parents[j] == i]
        region = Polygon(list(pi.exterior.coords), holes)
        try:
            if not region.is_valid:
                region = shapely.make_valid(region)
            cdt = shapely.constrained_delaunay_triangles(region)
            tri_geoms = list(cdt.geoms) if hasattr(cdt, "geoms") else [cdt]
        except Exception:
            tri_geoms = []

        for tg in tri_geoms:
            if not isinstance(tg, Polygon) or tg.is_empty or tg.area < 1e-9:
                continue
            coords = list(tg.exterior.coords)
            if len(coords) < 4:
                continue
            p0, p1, p2 = coords[0], coords[1], coords[2]
            cross = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
            i0, i1, i2 = _snap_vertex(p0[0], p0[1]), _snap_vertex(p1[0], p1[1]), _snap_vertex(p2[0], p2[1])
            if i0 == i1 or i1 == i2 or i2 == i0:
                continue
            # Upper cap faces -Z (CW in XY); lower cap faces +Z (CCW in XY)
            flip = (cross > 0.0) if is_upper else (cross < 0.0)
            cap_faces.append((i0, i2, i1) if flip else (i0, i1, i2))

    if not cap_faces:
        return half

    capped = trimesh.Trimesh(vertices=verts, faces=np.vstack([faces, np.asarray(cap_faces, dtype=np.int64)]), process=True)
    try:
        trimesh.repair.fix_normals(capped)
    except Exception:
        pass
    return capped


def cut_mesh_by_plane(
    mesh: trimesh.Trimesh,
    plane_origin: np.ndarray,
    plane_normal: np.ndarray,
    keep_upper: bool = True,
    keep_lower: bool = True,
) -> Tuple[Optional[trimesh.Trimesh], Optional[trimesh.Trimesh]]:
    """Slice ``mesh`` along an arbitrary 3D plane into watertight capped upper and lower meshes."""
    origin = np.asarray(plane_origin, dtype=np.float64)
    normal = np.asarray(plane_normal, dtype=np.float64)
    nlen = float(np.linalg.norm(normal))
    normal = normal / nlen if nlen >= 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

    # Transform mesh into plane-local frame (cut plane at Z = 0, normal = +Z)
    to_local = rotation_matrix_aligning_vectors(normal, np.array([0.0, 0.0, 1.0])) @ trimesh.transformations.translation_matrix(-origin)
    to_world = np.linalg.inv(to_local)
    m_loc = mesh.copy()
    m_loc.apply_transform(to_local)

    zmin, zmax = float(m_loc.bounds[0, 2]), float(m_loc.bounds[1, 2])
    if zmax <= 1e-4:
        return (None, mesh.copy() if keep_lower else None)
    if zmin >= -1e-4:
        return (mesh.copy() if keep_upper else None, None)

    def _build_half(is_upper: bool) -> Optional[trimesh.Trimesh]:
        half = trimesh.intersections.slice_mesh_plane(
            m_loc,
            plane_normal=[0.0, 0.0, 1.0 if is_upper else -1.0],
            plane_origin=[0.0, 0.0, 0.0],
            cap=False,
        )
        if half is None or len(half.faces) == 0:
            return None
        combined = _cap_open_cut_half(half, is_upper=is_upper)
        combined.apply_transform(to_world)
        return combined if len(combined.faces) > 0 else None

    return (
        _build_half(True) if keep_upper else None,
        _build_half(False) if keep_lower else None,
    )
