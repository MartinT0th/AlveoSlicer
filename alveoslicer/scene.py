"""Headless multi-bed, multi-instance 3D scene manager and PrusaSlicer-style Place-on-Face engine."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import numpy as np
import trimesh


@dataclass
class SimplifiedFace:
    """Clickable simplified resting plane on a 3D mesh for Place-on-Face alignment."""

    face_id: int
    normal: np.ndarray         # (3,) float64 outward unit normal in world coordinates
    centroid: np.ndarray       # (3,) float64
    area: float
    triangles: np.ndarray      # (M, 3, 3) float32 triangles forming this face patch


def compute_placeable_faces(
    mesh: trimesh.Trimesh,
    angle_tol_deg: float = 10.0,
    max_faces: int = 24,
) -> List[SimplifiedFace]:
    """
    Compute simplified clickable landing faces on ``mesh`` (PrusaSlicer Place-on-Face style):
      1. Extracts the convex hull so every landing face is physically stable on the bed.
      2. Merges near-coplanar triangles within ``angle_tol_deg`` and plane-offset tolerance.
      3. Returns the top ``max_faces`` patches sorted by area descending.
    """
    if mesh is None or len(mesh.faces) == 0:
        return []

    try:
        hull = mesh.convex_hull
        if hull is None or len(hull.faces) == 0:
            hull = mesh
    except Exception:
        hull = mesh

    tris = np.asarray(hull.triangles, dtype=np.float64)       # (F, 3, 3)
    normals = np.asarray(hull.face_normals, dtype=np.float64)  # (F, 3)
    areas = np.asarray(hull.area_faces, dtype=np.float64)      # (F,)
    if len(tris) == 0:
        return []

    centroids = tris.mean(axis=1)
    diag = max(1.0, float(np.linalg.norm(hull.extents)))
    cos_tol = math.cos(math.radians(angle_tol_deg))
    plane_tol = max(0.25, 0.015 * diag)

    # Greedy coplanar clustering ordered from largest triangle downward
    order = np.argsort(-areas)
    clusters: List[dict] = []

    for f_idx in order:
        a = float(areas[f_idx])
        if a < 1e-5:
            continue
        n = normals[f_idx]
        nl = float(np.linalg.norm(n))
        if nl < 1e-6:
            continue
        n = n / nl
        c = centroids[f_idx]
        d_plane = float(np.dot(n, c))

        matched = False
        for cl in clusters:
            cn = cl["normal"]
            if float(np.dot(cn, n)) >= cos_tol and abs(float(np.dot(cn, c)) - cl["d"]) <= plane_tol:
                cl["indices"].append(int(f_idx))
                cl["area"] += a
                cl["n_sum"] = cl["n_sum"] + n * a
                cl["c_sum"] = cl["c_sum"] + c * a
                nn = cl["n_sum"]
                nn_l = float(np.linalg.norm(nn))
                if nn_l > 1e-6:
                    cl["normal"] = nn / nn_l
                cl["d"] = float(np.dot(cl["normal"], cl["c_sum"] / cl["area"]))
                matched = True
                break

        if not matched:
            clusters.append(
                {
                    "indices": [int(f_idx)],
                    "area": a,
                    "normal": n.copy(),
                    "n_sum": n * a,
                    "c_sum": c * a,
                    "d": d_plane,
                }
            )

    if not clusters:
        return []

    clusters.sort(key=lambda cl: cl["area"], reverse=True)
    min_keep_area = clusters[0]["area"] * 0.008
    faces_out: List[SimplifiedFace] = []
    for cl in clusters[:max_faces]:
        if cl["area"] < min_keep_area:
            continue
        idx_arr = np.asarray(cl["indices"], dtype=np.int64)
        # Offset triangles slightly outward (0.04 mm) to prevent Z-fighting when rendered over mesh
        n_unit = cl["normal"]
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


def _rotation_matrix_aligning_vectors(v_from: np.ndarray, v_to: np.ndarray) -> np.ndarray:
    """Compute a 4x4 homogeneous rotation matrix rotating unit vector ``v_from`` onto ``v_to``."""
    a = np.asarray(v_from, dtype=np.float64)
    b = np.asarray(v_to, dtype=np.float64)
    a = a / max(float(np.linalg.norm(a)), 1e-12)
    b = b / max(float(np.linalg.norm(b)), 1e-12)
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))

    mat = np.eye(4, dtype=np.float64)
    if dot > 1.0 - 1e-7:
        return mat
    if dot < -1.0 + 1e-7:
        # 180-degree rotation around any axis perpendicular to a
        perp = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, perp)
        axis = axis / max(float(np.linalg.norm(axis)), 1e-12)
        return trimesh.transformations.rotation_matrix(math.pi, axis)

    axis = np.cross(a, b)
    angle = math.acos(dot)
    return trimesh.transformations.rotation_matrix(angle, axis)


def normalize_base_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Return a copy of ``mesh`` centered at XY=(0, 0) with lowest Z at 0."""
    m = mesh.copy()
    bounds = m.bounds
    cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
    cy = 0.5 * (bounds[0, 1] + bounds[1, 1])
    m.apply_translation([-cx, -cy, -bounds[0, 2]])
    return m


def _cap_open_cut_half(half: trimesh.Trimesh, is_upper: bool) -> trimesh.Trimesh:
    """
    Close the Z=0 planar cut boundary of an open half-mesh using its own exact
    boundary vertex indices and Shapely constrained Delaunay triangulation (handles
    multiple islands and nested holes with zero extra dependencies).
    """
    from collections import defaultdict
    import shapely
    from shapely.geometry import Polygon

    half = half.copy()
    half.merge_vertices(digits_vertex=5)
    half.remove_unreferenced_vertices()
    faces = np.asarray(half.faces, dtype=np.int64)
    verts = np.asarray(half.vertices, dtype=np.float64)
    if len(faces) == 0 or len(verts) == 0:
        return half

    # Collect directed boundary half-edges (u, v) whose opposite (v, u) is absent
    edge_set = set()
    for a, b, c in faces:
        ia, ib, ic = int(a), int(b), int(c)
        edge_set.add((ia, ib))
        edge_set.add((ib, ic))
        edge_set.add((ic, ia))

    adj: dict = defaultdict(list)
    for u, v in edge_set:
        if (v, u) not in edge_set:
            adj[u].append(v)

    if not adj:
        return half

    # Trace closed boundary loops along the planar cut
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

    # Build 2D polygons and classify nesting depth (even = outer ring, odd = hole)
    polys: List[Tuple[Polygon, List[int]]] = []
    for lp in loops:
        pts = [(float(verts[i, 0]), float(verts[i, 1])) for i in lp]
        poly_2d = Polygon(pts)
        if not poly_2d.is_empty and poly_2d.area > 1e-8:
            polys.append((poly_2d, lp))

    if not polys:
        return half

    depths = [0] * len(polys)
    parents = [-1] * len(polys)
    for i, (pi, _) in enumerate(polys):
        pt = pi.representative_point()
        best_area = float("inf")
        for j, (pj, _) in enumerate(polys):
            if i != j and pj.area > pi.area and pj.contains(pt):
                depths[i] += 1
                if pj.area < best_area:
                    best_area = pj.area
                    parents[i] = j

    # Exact lookup from 2D CDT coordinates back to the original vertex indices in ``verts``
    b_indices = np.array(sorted({idx for lp in loops for idx in lp}), dtype=np.int64)
    b_xy = verts[b_indices, :2]

    def _snap_vertex(x: float, y: float) -> int:
        d2 = (b_xy[:, 0] - x) ** 2 + (b_xy[:, 1] - y) ** 2
        return int(b_indices[int(np.argmin(d2))])

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
            i0 = _snap_vertex(p0[0], p0[1])
            i1 = _snap_vertex(p1[0], p1[1])
            i2 = _snap_vertex(p2[0], p2[1])
            if i0 == i1 or i1 == i2 or i2 == i0:
                continue
            if is_upper:
                # Upper cap faces -Z (CW in XY, cross < 0)
                cap_faces.append((i0, i2, i1) if cross > 0.0 else (i0, i1, i2))
            else:
                # Lower cap faces +Z (CCW in XY, cross > 0)
                cap_faces.append((i0, i1, i2) if cross > 0.0 else (i0, i2, i1))

    if not cap_faces:
        return half

    all_faces = np.vstack([faces, np.asarray(cap_faces, dtype=np.int64)])
    capped = trimesh.Trimesh(vertices=verts, faces=all_faces, process=True)
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
    """
    Slice ``mesh`` cleanly along an arbitrary 3D plane into watertight capped upper & lower meshes.
    ``plane_normal`` points toward the upper half.
    """
    origin = np.asarray(plane_origin, dtype=np.float64)
    normal = np.asarray(plane_normal, dtype=np.float64)
    nlen = float(np.linalg.norm(normal))
    if nlen < 1e-9:
        normal = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        normal = normal / nlen

    # Transform mesh into plane-local space where plane is Z = 0, normal = +Z
    r_to_z = _rotation_matrix_aligning_vectors(normal, np.array([0.0, 0.0, 1.0]))
    t_to_origin = trimesh.transformations.translation_matrix(-origin)
    to_local = r_to_z @ t_to_origin
    to_world = np.linalg.inv(to_local)

    m_loc = mesh.copy()
    m_loc.apply_transform(to_local)

    # Check if plane actually intersects the mesh
    zmin, zmax = float(m_loc.bounds[0, 2]), float(m_loc.bounds[1, 2])
    if zmax <= 1e-4:
        return (None, mesh.copy() if keep_lower else None)
    if zmin >= -1e-4:
        return (mesh.copy() if keep_upper else None, None)

    def _build_half(is_upper: bool) -> Optional[trimesh.Trimesh]:
        pn = [0.0, 0.0, 1.0] if is_upper else [0.0, 0.0, -1.0]
        half = trimesh.intersections.slice_mesh_plane(
            m_loc, plane_normal=pn, plane_origin=[0.0, 0.0, 0.0], cap=False
        )
        if half is None or len(half.faces) == 0:
            return None
        combined = _cap_open_cut_half(half, is_upper=is_upper)
        combined.apply_transform(to_world)
        return combined if len(combined.faces) > 0 else None

    upper = _build_half(True) if keep_upper else None
    lower = _build_half(False) if keep_lower else None
    return upper, lower


@dataclass
class ModelInstance:
    """A single placed instance of a 3D model on a BuildPlate with Move/Rotate/Scale & Paint-on Supports."""

    instance_id: str
    name: str
    source_name: str
    base_mesh: trimesh.Trimesh
    pos_x: float = 110.0
    pos_y: float = 110.0
    pos_z: float = 0.0
    rot_x: float = 0.0          # degrees
    rot_y: float = 0.0          # degrees
    rot_z: float = 0.0          # degrees
    scale_x: float = 100.0      # percent
    scale_y: float = 100.0      # percent
    scale_z: float = 100.0      # percent
    uniform_scale: bool = True
    # Per-face support paint labels: 0 = neutral, +1 = enforcer (green), -1 = blocker (red)
    paint_support: Optional[np.ndarray] = None

    _cached_mesh: Optional[trimesh.Trimesh] = field(default=None, init=False, repr=False)
    _cached_faces: Optional[List[SimplifiedFace]] = field(default=None, init=False, repr=False)

    def ensure_paint_array(self) -> np.ndarray:
        n_faces = len(self.base_mesh.faces)
        if self.paint_support is None or len(self.paint_support) != n_faces:
            self.paint_support = np.zeros(n_faces, dtype=np.int8)
        return self.paint_support

    def paint_counts(self) -> Tuple[int, int]:
        """Return (enforcer_face_count, blocker_face_count)."""
        if self.paint_support is None or len(self.paint_support) != len(self.base_mesh.faces):
            return 0, 0
        enf = int(np.count_nonzero(self.paint_support == 1))
        blk = int(np.count_nonzero(self.paint_support == -1))
        return enf, blk

    def paint_faces_in_sphere(self, world_point: np.ndarray, radius_mm: float, value: int) -> int:
        """Paint triangles whose centroids (or vertices) fall within ``radius_mm`` of ``world_point``."""
        mesh = self.transformed_mesh()
        if len(mesh.faces) == 0:
            return 0
        arr = self.ensure_paint_array()
        pt = np.asarray(world_point, dtype=np.float64)
        tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64)
        centroids = tris.mean(axis=1)
        r_sq = float(radius_mm) * float(radius_mm)
        d_c = np.einsum("ij,ij->i", centroids - pt, centroids - pt)
        # Also catch large triangles whose nearest vertex or face center is within radius
        d_v0 = np.einsum("ij,ij->i", tris[:, 0] - pt, tris[:, 0] - pt)
        d_v1 = np.einsum("ij,ij->i", tris[:, 1] - pt, tris[:, 1] - pt)
        d_v2 = np.einsum("ij,ij->i", tris[:, 2] - pt, tris[:, 2] - pt)
        hit = (d_c <= r_sq) | (np.minimum(np.minimum(d_v0, d_v1), d_v2) <= r_sq * 0.72)
        val = int(np.clip(value, -1, 1))
        changed = int(np.count_nonzero(hit & (arr != val)))
        if changed > 0:
            arr[hit] = val
            if self._cached_mesh is not None:
                self._cached_mesh.metadata["paint_support"] = arr.copy()
        return changed

    def smart_paint_overhangs(self, overhang_angle_deg: float = 45.0, value: int = 1) -> int:
        """Automatically paint downward-facing overhang triangles above Z = 0.3 mm."""
        mesh = self.transformed_mesh()
        if len(mesh.faces) == 0:
            return 0
        arr = self.ensure_paint_array()
        normals = np.asarray(mesh.face_normals, dtype=np.float64)
        centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
        # Overhang angle from horizontal: face normal Z < -cos(angle)
        cos_thresh = math.cos(math.radians(max(5.0, min(85.0, overhang_angle_deg))))
        hit = (normals[:, 2] < -cos_thresh) & (centroids[:, 2] > 0.35)
        val = int(np.clip(value, -1, 1))
        changed = int(np.count_nonzero(hit & (arr != val)))
        arr[hit] = val
        if self._cached_mesh is not None:
            self._cached_mesh.metadata["paint_support"] = arr.copy()
        return changed

    def clear_paint(self) -> None:
        self.paint_support = np.zeros(len(self.base_mesh.faces), dtype=np.int8)
        if self._cached_mesh is not None:
            self._cached_mesh.metadata["paint_support"] = self.paint_support.copy()

    def invalidate(self) -> None:
        self._cached_mesh = None
        self._cached_faces = None

    def transformed_mesh(self) -> trimesh.Trimesh:
        if self._cached_mesh is not None:
            return self._cached_mesh

        m = self.base_mesh.copy()
        sx = max(0.02, self.scale_x / 100.0)
        sy = max(0.02, self.scale_y / 100.0)
        sz = max(0.02, self.scale_z / 100.0)
        if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6 or abs(sz - 1.0) > 1e-6:
            scale_m = np.diag([sx, sy, sz, 1.0])
            m.apply_transform(scale_m)

        if abs(self.rot_x) > 1e-6 or abs(self.rot_y) > 1e-6 or abs(self.rot_z) > 1e-6:
            rot_m = trimesh.transformations.euler_matrix(
                math.radians(self.rot_x),
                math.radians(self.rot_y),
                math.radians(self.rot_z),
                axes="sxyz",
            )
            m.apply_transform(rot_m)

        bounds = m.bounds
        cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
        cy = 0.5 * (bounds[0, 1] + bounds[1, 1])
        m.apply_translation([self.pos_x - cx, self.pos_y - cy, self.pos_z - bounds[0, 2]])
        if self.paint_support is not None and len(self.paint_support) == len(m.faces):
            m.metadata["paint_support"] = self.paint_support.copy()
        self._cached_mesh = m
        return m

    def placeable_faces(self) -> List[SimplifiedFace]:
        if self._cached_faces is None:
            self._cached_faces = compute_placeable_faces(self.transformed_mesh())
        return self._cached_faces

    def align_normal_to_bed(self, world_normal: np.ndarray) -> None:
        """Rotate this instance so ``world_normal`` points straight down (0, 0, -1) onto Z = 0."""
        r_cur = trimesh.transformations.euler_matrix(
            math.radians(self.rot_x),
            math.radians(self.rot_y),
            math.radians(self.rot_z),
            axes="sxyz",
        )
        r_align = _rotation_matrix_aligning_vectors(world_normal, np.array([0.0, 0.0, -1.0]))
        r_new = r_align @ r_cur
        rx, ry, rz = trimesh.transformations.euler_from_matrix(r_new, axes="sxyz")
        self.rot_x = round(math.degrees(rx), 2)
        self.rot_y = round(math.degrees(ry), 2)
        self.rot_z = round(math.degrees(rz), 2)
        self.pos_z = 0.0
        self.invalidate()

    def reset_transform(self, bed_cx: float, bed_cy: float) -> None:
        self.pos_x = bed_cx
        self.pos_y = bed_cy
        self.pos_z = 0.0
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.rot_z = 0.0
        self.scale_x = 100.0
        self.scale_y = 100.0
        self.scale_z = 100.0
        self.invalidate()

    def clone_instance(
        self, new_name: str, offset_x: float = 15.0, offset_y: float = 0.0
    ) -> "ModelInstance":
        return ModelInstance(
            instance_id=uuid.uuid4().hex[:10],
            name=new_name,
            source_name=self.source_name,
            base_mesh=self.base_mesh,
            pos_x=self.pos_x + offset_x,
            pos_y=self.pos_y + offset_y,
            pos_z=self.pos_z,
            rot_x=self.rot_x,
            rot_y=self.rot_y,
            rot_z=self.rot_z,
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            scale_z=self.scale_z,
            uniform_scale=self.uniform_scale,
            paint_support=self.paint_support.copy() if self.paint_support is not None else None,
        )


@dataclass
class BuildPlate:
    """Independent build plate holding one or more ModelInstances plus its own slice result."""

    plate_id: str
    name: str
    instances: List[ModelInstance] = field(default_factory=list)
    slice_result: Any = None
    preview_data: Any = None
    gcode_text: str = ""

    @property
    def is_sliced(self) -> bool:
        return bool(self.gcode_text) and (self.preview_data is not None)

    def clear_slice(self) -> None:
        self.slice_result = None
        self.preview_data = None
        self.gcode_text = ""

    def combined_mesh(self) -> Optional[trimesh.Trimesh]:
        if not self.instances:
            return None
        if len(self.instances) == 1:
            m = self.instances[0].transformed_mesh().copy()
            ps = self.instances[0].paint_support
            if ps is not None and len(ps) == len(m.faces):
                m.metadata["paint_support"] = ps.copy()
            return m
        meshes = [inst.transformed_mesh() for inst in self.instances]
        paint_chunks: List[np.ndarray] = []
        any_paint = False
        for inst, m in zip(self.instances, meshes):
            if inst.paint_support is not None and len(inst.paint_support) == len(m.faces):
                paint_chunks.append(inst.paint_support.copy())
                if np.any(inst.paint_support != 0):
                    any_paint = True
            else:
                paint_chunks.append(np.zeros(len(m.faces), dtype=np.int8))
        combined = trimesh.util.concatenate(meshes)
        if any_paint:
            combined.metadata["paint_support"] = np.concatenate(paint_chunks)
        return combined

    def get_instance(self, instance_id: Optional[str]) -> Optional[ModelInstance]:
        if not instance_id:
            return None
        for inst in self.instances:
            if inst.instance_id == instance_id:
                return inst
        return None

    def auto_arrange(self, bed_x: float, bed_y: float, spacing: float = 8.0) -> None:
        """Pack all instances neatly on the build plate without XY overlap and center the group."""
        if not self.instances:
            return
        if len(self.instances) == 1:
            inst = self.instances[0]
            inst.pos_x = bed_x * 0.5
            inst.pos_y = bed_y * 0.5
            inst.pos_z = 0.0
            inst.invalidate()
            self.clear_slice()
            return

        # Sort tallest-Y footprints first for compact shelf packing
        items: List[Tuple[ModelInstance, float, float]] = []
        for inst in self.instances:
            ext = inst.transformed_mesh().extents
            items.append((inst, float(ext[0]), float(ext[1])))
        items.sort(key=lambda t: t[2], reverse=True)

        max_row_w = max(40.0, bed_x - 2.0 * spacing)
        cur_x = 0.0
        cur_y = 0.0
        row_h = 0.0
        placed: List[Tuple[ModelInstance, float, float, float, float]] = []

        for inst, w, d in items:
            if cur_x > 0.0 and cur_x + w > max_row_w:
                cur_x = 0.0
                cur_y += row_h + spacing
                row_h = 0.0
            placed.append((inst, cur_x + 0.5 * w, cur_y + 0.5 * d, w, d))
            cur_x += w + spacing
            row_h = max(row_h, d)

        # Compute packed group bounding box and shift cleanly to bed center
        min_x = min(cx - 0.5 * w for _, cx, _, w, _ in placed)
        max_x = max(cx + 0.5 * w for _, cx, _, w, _ in placed)
        min_y = min(cy - 0.5 * d for _, _, cy, _, d in placed)
        max_y = max(cy + 0.5 * d for _, _, cy, _, d in placed)
        shift_x = bed_x * 0.5 - 0.5 * (min_x + max_x)
        shift_y = bed_y * 0.5 - 0.5 * (min_y + max_y)

        for inst, cx, cy, _w, _d in placed:
            inst.pos_x = cx + shift_x
            inst.pos_y = cy + shift_y
            inst.pos_z = 0.0
            inst.invalidate()
        self.clear_slice()


class WorkspaceScene:
    """Multi-bed workspace managing independent BuildPlates and selected ModelInstance state."""

    def __init__(self) -> None:
        self.plates: List[BuildPlate] = [
            BuildPlate(plate_id=uuid.uuid4().hex[:8], name="Plate 1")
        ]
        self.active_plate_idx: int = 0
        self.selected_instance_id: Optional[str] = None
        self._plate_counter: int = 1

    @property
    def active_plate(self) -> BuildPlate:
        return self.plates[self.active_plate_idx]

    @property
    def selected_instance(self) -> Optional[ModelInstance]:
        return self.active_plate.get_instance(self.selected_instance_id)

    def add_plate(self, name: Optional[str] = None) -> BuildPlate:
        self._plate_counter += 1
        plate = BuildPlate(
            plate_id=uuid.uuid4().hex[:8],
            name=name or f"Plate {self._plate_counter}",
        )
        self.plates.append(plate)
        self.active_plate_idx = len(self.plates) - 1
        self.selected_instance_id = None
        return plate

    def remove_plate(self, idx: int) -> bool:
        if len(self.plates) <= 1 or not (0 <= idx < len(self.plates)):
            return False
        self.plates.pop(idx)
        self.active_plate_idx = min(self.active_plate_idx, len(self.plates) - 1)
        self._sync_selection_to_active_plate()
        return True

    def set_active_plate(self, idx: int) -> None:
        if 0 <= idx < len(self.plates):
            self.active_plate_idx = idx
            self._sync_selection_to_active_plate()

    def _sync_selection_to_active_plate(self) -> None:
        plate = self.active_plate
        if plate.get_instance(self.selected_instance_id) is None:
            self.selected_instance_id = plate.instances[0].instance_id if plate.instances else None

    def _next_instance_name(self, plate: BuildPlate, base_name: str) -> str:
        existing = {inst.name for inst in plate.instances}
        if base_name not in existing:
            return base_name
        i = 2
        while f"{base_name} #{i}" in existing:
            i += 1
        return f"{base_name} #{i}"

    def add_mesh(
        self,
        mesh: trimesh.Trimesh,
        source_name: str,
        bed_x: float,
        bed_y: float,
        auto_place: bool = True,
    ) -> ModelInstance:
        plate = self.active_plate
        base = normalize_base_mesh(mesh)
        inst = ModelInstance(
            instance_id=uuid.uuid4().hex[:10],
            name=self._next_instance_name(plate, source_name),
            source_name=source_name,
            base_mesh=base,
            pos_x=bed_x * 0.5,
            pos_y=bed_y * 0.5,
            pos_z=0.0,
        )
        plate.instances.append(inst)
        if auto_place and len(plate.instances) > 1:
            plate.auto_arrange(bed_x, bed_y)
        else:
            plate.clear_slice()
        self.selected_instance_id = inst.instance_id
        return inst

    def duplicate_selected(self, bed_x: float, bed_y: float) -> Optional[ModelInstance]:
        inst = self.selected_instance
        if inst is None:
            return None
        plate = self.active_plate
        ext = inst.transformed_mesh().extents
        new_name = self._next_instance_name(plate, inst.source_name)
        clone = inst.clone_instance(new_name, offset_x=float(ext[0]) + 8.0, offset_y=0.0)
        # Keep inside bed bounds or auto-arrange if it would overflow
        if clone.pos_x + 0.5 * float(ext[0]) > bed_x - 4.0:
            plate.instances.append(clone)
            plate.auto_arrange(bed_x, bed_y)
        else:
            plate.instances.append(clone)
            plate.clear_slice()
        self.selected_instance_id = clone.instance_id
        return clone

    def remove_selected(self) -> bool:
        plate = self.active_plate
        if not self.selected_instance_id:
            return False
        for i, inst in enumerate(plate.instances):
            if inst.instance_id == self.selected_instance_id:
                plate.instances.pop(i)
                plate.clear_slice()
                self._sync_selection_to_active_plate()
                return True
        return False

    def move_selected_to_plate(self, target_plate_idx: int, bed_x: float, bed_y: float) -> bool:
        if not (0 <= target_plate_idx < len(self.plates)) or target_plate_idx == self.active_plate_idx:
            return False
        inst = self.selected_instance
        if inst is None:
            return False
        src = self.active_plate
        dst = self.plates[target_plate_idx]
        src.instances.remove(inst)
        src.clear_slice()
        inst.name = self._next_instance_name(dst, inst.source_name)
        dst.instances.append(inst)
        if len(dst.instances) > 1:
            dst.auto_arrange(bed_x, bed_y)
        else:
            dst.clear_slice()
        self._sync_selection_to_active_plate()
        return True

    def cut_selected_instance(
        self,
        plane_origin: np.ndarray,
        plane_normal: np.ndarray,
        bed_x: float,
        bed_y: float,
        keep_upper: bool = True,
        keep_lower: bool = True,
        place_upper_on_bed: bool = True,
        place_lower_on_bed: bool = False,
    ) -> List[ModelInstance]:
        """Cut the selected ModelInstance along a 3D plane and replace it with watertight cut halves."""
        inst = self.selected_instance
        if inst is None or (not keep_upper and not keep_lower):
            return []

        world_mesh = inst.transformed_mesh()
        normal = np.asarray(plane_normal, dtype=np.float64)
        nlen = float(np.linalg.norm(normal))
        normal = normal / nlen if nlen > 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

        upper_m, lower_m = cut_mesh_by_plane(
            world_mesh,
            plane_origin=plane_origin,
            plane_normal=normal,
            keep_upper=keep_upper,
            keep_lower=keep_lower,
        )
        if upper_m is None and lower_m is None:
            return []

        plate = self.active_plate
        orig_name = inst.source_name
        orig_x, orig_y = inst.pos_x, inst.pos_y
        plate.instances.remove(inst)
        created: List[ModelInstance] = []

        def _spawn_half(
            mesh_part: trimesh.Trimesh,
            suffix: str,
            cut_face_normal: np.ndarray,
            lay_flat: bool,
            dx: float,
        ) -> ModelInstance:
            m = mesh_part.copy()
            if lay_flat:
                r_lay = _rotation_matrix_aligning_vectors(cut_face_normal, np.array([0.0, 0.0, -1.0]))
                m.apply_transform(r_lay)
            bounds = m.bounds
            cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
            cy = 0.5 * (bounds[0, 1] + bounds[1, 1])
            base = normalize_base_mesh(m)
            new_inst = ModelInstance(
                instance_id=uuid.uuid4().hex[:10],
                name=self._next_instance_name(plate, f"{orig_name} ({suffix})"),
                source_name=f"{orig_name} ({suffix})",
                base_mesh=base,
                pos_x=max(10.0, min(bed_x - 10.0, (cx if not lay_flat else orig_x) + dx)),
                pos_y=max(10.0, min(bed_y - 10.0, cy if not lay_flat else orig_y)),
                pos_z=0.0,
            )
            plate.instances.append(new_inst)
            return new_inst

        both = (upper_m is not None) and (lower_m is not None)
        sep = 0.5 * float(world_mesh.extents[0]) + 8.0 if both else 0.0

        if lower_m is not None:
            created.append(_spawn_half(lower_m, "Lower", normal, place_lower_on_bed, -0.5 * sep if both else 0.0))
        if upper_m is not None:
            created.append(_spawn_half(upper_m, "Upper", -normal, place_upper_on_bed, 0.5 * sep if both else 0.0))

        plate.clear_slice()
        if created:
            self.selected_instance_id = created[-1].instance_id
        else:
            self._sync_selection_to_active_plate()
        return created
