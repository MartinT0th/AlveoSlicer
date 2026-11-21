"""
Stage 1 Geometry Utilities: Mesh ingestion, built-in test shapes, 3D SDF field, and safe 2D ops.

Provides:
  - Multi-format mesh loading (STL, OBJ, 3MF, STEP/STP) with watertight repair and bed centering.
  - Built-in procedural watertight test geometries (icosphere and wavy curved shell).
  - `VolumetricField`: lightweight 3D signed-distance-like field for surface-normal synthesis.
  - Topology-safe Shapely wrappers (`clean_geom`, `safe_buffer`, `safe_union`, `safe_intersection`,
    `safe_difference`) that guarantee slicing never crashes with GEOS `TopologyException`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Sequence, Union

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from .config import SlicerConfig


# -----------------------------------------------------------------------------
# 3D Mesh Loading & Built-In Test Shapes
# -----------------------------------------------------------------------------


def center_mesh_on_bed(mesh: trimesh.Trimesh, cfg: SlicerConfig) -> trimesh.Trimesh:
    """Center ``mesh`` in XY on the build plate and seat its lowest vertex at Z = 0."""
    bounds = mesh.bounds
    center_xy = 0.5 * (bounds[0, :2] + bounds[1, :2])
    shift = np.array(
        [
            cfg.printer.bed_size_x * 0.5 - center_xy[0],
            cfg.printer.bed_size_y * 0.5 - center_xy[1],
            -bounds[0, 2],
        ],
        dtype=np.float64,
    )
    mesh.apply_translation(shift)
    return mesh


def load_and_center_mesh(filepath: Union[str, Path], cfg: SlicerConfig) -> trimesh.Trimesh:
    """Load an STL, OBJ, 3MF, PLY, or STEP/STP mesh, repair holes/normals, and center on bed."""
    path = Path(filepath)
    ext = path.suffix.lower()
    kwargs = (
        {"tol_linear": cfg.step_tol_linear, "tol_angular": cfg.step_tol_angular}
        if ext in {".step", ".stp"}
        else {}
    )
    try:
        loaded = trimesh.load(str(path), **kwargs)
    except Exception as exc:
        if ext in {".step", ".stp"}:
            raise RuntimeError(
                f"Failed to load STEP file '{path}'. Install cascadio (`pip install cascadio`). "
                f"Original error: {exc}"
            ) from exc
        raise

    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError(f"No triangle meshes found in '{path}'.")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise TypeError(f"Unsupported geometry type: {type(loaded)}")

    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_normals(mesh)
    return center_mesh_on_bed(mesh, cfg)


def create_test_sphere_mesh(cfg: SlicerConfig, radius: float = 15.0, subdivisions: int = 3) -> trimesh.Trimesh:
    """Create a watertight 30 mm icosphere centered on the build plate."""
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    mesh.apply_translation([cfg.printer.bed_size_x * 0.5, cfg.printer.bed_size_y * 0.5, radius])
    return mesh


def create_test_curved_wall_mesh(cfg: SlicerConfig) -> trimesh.Trimesh:
    """Create a watertight wavy 60x25x5 mm curved shell without requiring external earcut engines."""
    nu, length, height, thick, amp = 64, 60.0, 25.0, 5.0, 4.5
    us = np.linspace(-0.5 * length, 0.5 * length, nu)
    yc = amp * np.sin(2.0 * np.pi * us / length)
    dydx = amp * (2.0 * np.pi / length) * np.cos(2.0 * np.pi * us / length)
    norm_len = np.sqrt(1.0 + dydx * dydx)
    nx, ny = -dydx / norm_len, 1.0 / norm_len

    front_xy = np.column_stack([us + 0.5 * thick * nx, yc + 0.5 * thick * ny])
    back_xy = np.column_stack([us - 0.5 * thick * nx, yc - 0.5 * thick * ny])
    zeros = np.zeros((nu, 1), dtype=np.float64)
    tops = np.full((nu, 1), height, dtype=np.float64)

    # Vertex layout: [F0 (0..nu-1), B0 (nu..2nu-1), F1 (2nu..3nu-1), B1 (3nu..4nu-1)]
    vertices = np.vstack([
        np.hstack([front_xy, zeros]),
        np.hstack([back_xy, zeros]),
        np.hstack([front_xy, tops]),
        np.hstack([back_xy, tops]),
    ])

    faces = []
    for i in range(nu - 1):
        f0a, f0b, b0a, b0b = i, i + 1, nu + i, nu + i + 1
        f1a, f1b, b1a, b1b = 2 * nu + i, 2 * nu + i + 1, 3 * nu + i, 3 * nu + i + 1
        faces.extend([
            (f0a, f0b, f1b), (f0a, f1b, f1a),  # front
            (b0a, b1b, b0b), (b0a, b1a, b1b),  # back
            (f1a, f1b, b1b), (f1a, b1b, b1a),  # top
            (f0a, b0b, f0b), (f0a, b0a, b0b),  # bottom
        ])
    # End caps (u = 0 and u = nu - 1)
    faces.extend([
        (0, 2 * nu, 3 * nu), (0, 3 * nu, nu),
        (nu - 1, 2 * nu - 1, 4 * nu - 1), (nu - 1, 4 * nu - 1, 3 * nu - 1),
    ])

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=True)
    trimesh.repair.fix_normals(mesh)
    return center_mesh_on_bed(mesh, cfg)


# -----------------------------------------------------------------------------
# 3D Volumetric Field (Used for 3D Cellular Seeding Only — Never for Toolpaths)
# -----------------------------------------------------------------------------


class VolumetricField:
    """Lightweight 3D voxel occupancy + signed distance field (SDF) for 3D cellular seeding."""

    def __init__(self, mesh: trimesh.Trimesh, pitch: float):
        """Voxelize ``mesh`` at ``pitch`` mm and compute interior/exterior Euclidean distance fields."""
        self.mesh = mesh
        self.pitch = float(pitch)
        vox = mesh.voxelized(pitch=self.pitch).fill()
        vol = np.pad(vox.matrix.astype(bool), pad_width=2, mode="constant", constant_values=False)
        self.vol: np.ndarray = vol
        self.occ: np.ndarray = vol
        self.origin: np.ndarray = vox.translation - 2.0 * self.pitch
        self.shape = vol.shape
        self.dist_in: np.ndarray = ndimage.distance_transform_edt(vol) * self.pitch
        self.dist_out: np.ndarray = ndimage.distance_transform_edt(~vol) * self.pitch
        self.sdf: np.ndarray = self.dist_out - self.dist_in
        self.edt_in: np.ndarray = self.dist_in


# -----------------------------------------------------------------------------
# Topology-Safe 2D Shapely Geometry Helpers
# -----------------------------------------------------------------------------


def clean_geom(geom: shapely.Geometry) -> shapely.Geometry:
    """Repair invalid, self-intersecting, or mixed-dimension 2D polygons safely."""
    if geom is None or geom.is_empty:
        return Polygon()
    try:
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
    except Exception:
        try:
            geom = geom.buffer(0)
        except Exception:
            return Polygon()
    if geom.is_empty:
        return Polygon()
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        return shapely.unary_union(polys) if polys else Polygon()
    return geom


def safe_buffer(geom: shapely.Geometry, dist: float, **kwargs) -> shapely.Geometry:
    """Topology-safe Shapely buffer with automatic repair and precision fallback."""
    if geom is None or geom.is_empty:
        return Polygon()
    try:
        return clean_geom(geom.buffer(dist, **kwargs))
    except Exception:
        c = clean_geom(geom)
        if c.is_empty:
            return Polygon()
        try:
            return clean_geom(c.buffer(dist, **kwargs))
        except Exception:
            try:
                return clean_geom(shapely.set_precision(c, 1e-4).buffer(dist, **kwargs))
            except Exception:
                return Polygon()


def safe_union(geoms: Sequence[shapely.Geometry]) -> shapely.Geometry:
    """Topology-safe unary union across arbitrary polygons (never raises TopologyException)."""
    valid = [g for g in geoms if g is not None and not g.is_empty]
    if not valid:
        return Polygon()
    if len(valid) == 1:
        return clean_geom(valid[0])
    try:
        return clean_geom(shapely.unary_union(valid))
    except Exception:
        cleaned = [clean_geom(g) for g in valid]
        cleaned = [g for g in cleaned if not g.is_empty]
        if not cleaned:
            return Polygon()
        try:
            return clean_geom(shapely.unary_union(cleaned))
        except Exception:
            try:
                return clean_geom(shapely.unary_union([shapely.set_precision(g, 1e-4) for g in cleaned]))
            except Exception:
                acc = cleaned[0]
                for g in cleaned[1:]:
                    try:
                        acc = clean_geom(acc.union(g))
                    except Exception:
                        pass
                return acc


def _safe_binary_op(
    a: shapely.Geometry,
    b: shapely.Geometry,
    op: Callable[[shapely.Geometry, shapely.Geometry], shapely.Geometry],
    fallback_on_fail: Callable[[shapely.Geometry], shapely.Geometry],
) -> shapely.Geometry:
    """Execute a binary Shapely op with clean_geom and 1e-4 precision-grid fallbacks."""
    try:
        return clean_geom(op(a, b))
    except Exception:
        ca, cb = clean_geom(a), clean_geom(b)
        if ca.is_empty:
            return Polygon()
        if cb.is_empty:
            return fallback_on_fail(ca)
        try:
            return clean_geom(op(ca, cb))
        except Exception:
            try:
                return clean_geom(op(shapely.set_precision(ca, 1e-4), shapely.set_precision(cb, 1e-4)))
            except Exception:
                return fallback_on_fail(ca)


def safe_intersection(a: shapely.Geometry, b: shapely.Geometry) -> shapely.Geometry:
    """Topology-safe Shapely intersection (never throws TopologyException)."""
    if a is None or b is None or a.is_empty or b.is_empty:
        return Polygon()
    return _safe_binary_op(a, b, lambda x, y: x.intersection(y), lambda _: Polygon())


def safe_difference(a: shapely.Geometry, b: shapely.Geometry) -> shapely.Geometry:
    """Topology-safe Shapely difference (never throws TopologyException)."""
    if a is None or a.is_empty:
        return Polygon()
    if b is None or b.is_empty:
        return clean_geom(a)
    return _safe_binary_op(a, b, lambda x, y: x.difference(y), lambda ca: ca)


def extract_polylines(geom: shapely.Geometry) -> List[np.ndarray]:
    """Extract all 2D polyline coordinate arrays from a Shapely LineString/MultiLineString/Collection."""
    lines: List[np.ndarray] = []
    if geom is None or geom.is_empty:
        return lines
    if isinstance(geom, LineString):
        coords = np.asarray(geom.coords)
        if len(coords) >= 2:
            lines.append(coords)
    elif isinstance(geom, MultiLineString):
        for g in geom.geoms:
            coords = np.asarray(g.coords)
            if len(coords) >= 2:
                lines.append(coords)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            lines.extend(extract_polylines(g))
    return lines


def iter_polygons(geom: shapely.Geometry, min_area: float = 1e-5) -> List[Polygon]:
    """Flatten a Shapely geometry into non-trivial Polygon objects above ``min_area``."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > min_area else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > min_area]
    if hasattr(geom, "geoms"):
        out: List[Polygon] = []
        for g in geom.geoms:
            out.extend(iter_polygons(g, min_area=min_area))
        return out
    return []


def rasterize_geom(geom: shapely.Geometry, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Rasterize a 2D Shapely geometry onto an (nx, ny) boolean grid (for DP/BFS/UI only)."""
    if geom is None or geom.is_empty:
        return np.zeros(gx.shape, dtype=bool)
    return shapely.contains_xy(geom, gx, gy)


def disk_kernel(radius_px: int) -> np.ndarray:
    """Create a 2D boolean circular structuring element with pixel radius ``radius_px``."""
    r = max(1, radius_px)
    yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= (r * r)


def split_mesh_bodies(mesh: trimesh.Trimesh) -> List[trimesh.Trimesh]:
    """Split a multi-body mesh into independent connected solid submeshes, preserving ``paint_support``."""
    n_faces = len(mesh.faces)
    if n_faces == 0:
        return []
    try:
        comps = trimesh.graph.connected_components(
            mesh.face_adjacency, nodes=np.arange(n_faces), min_len=4
        )
    except Exception:
        return [mesh]

    if len(comps) <= 1:
        return [mesh]

    paint = mesh.metadata.get("paint_support") if isinstance(mesh.metadata, dict) else None
    has_paint = paint is not None and len(paint) == n_faces
    paint_arr = np.asarray(paint, dtype=np.int8) if has_paint else None

    bodies: List[trimesh.Trimesh] = []
    for face_idx in comps:
        idx_arr = np.asarray(face_idx, dtype=np.int64)
        sub = mesh.submesh([idx_arr], append=True)
        if not isinstance(sub, trimesh.Trimesh) or len(sub.faces) < 4:
            continue
        if paint_arr is not None:
            sub.metadata["paint_support"] = paint_arr[idx_arr].copy()
        bodies.append(sub)

    if not bodies:
        return [mesh]
    bodies.sort(key=lambda m: (float(m.bounds[0, 0]), float(m.bounds[0, 1]), float(m.bounds[0, 2])))
    return bodies
