"""Stage 1: Multi-format mesh ingestion, built-in test meshes, SDF field, and 2D geometry helpers."""

from __future__ import annotations

from pathlib import Path
from typing import List, Union

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from .config import SlicerConfig


def center_mesh_on_bed(mesh: trimesh.Trimesh, cfg: SlicerConfig) -> trimesh.Trimesh:
    """Center a mesh in XY on the build plate and rest its lowest vertex at Z = 0."""
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
    """Load an STL, OBJ, 3MF, or STEP/STP file, repair it if needed, and center it on the bed."""
    path = Path(filepath)
    ext = path.suffix.lower()
    kwargs = {}
    if ext in {".step", ".stp"}:
        kwargs = {
            "tol_linear": cfg.step_tol_linear,
            "tol_angular": cfg.step_tol_angular,
        }

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
    """Create a built-in 30 mm icosphere centered on the build plate."""
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    mesh.apply_translation([cfg.printer.bed_size_x * 0.5, cfg.printer.bed_size_y * 0.5, radius])
    return mesh


def create_test_curved_wall_mesh(cfg: SlicerConfig) -> trimesh.Trimesh:
    """Create a watertight wavy 60x25x5 mm curved shell without requiring external earcut engines."""
    nu = 64
    length, height, thick = 60.0, 25.0, 5.0
    amp = 4.5
    us = np.linspace(-0.5 * length, 0.5 * length, nu)
    yc = amp * np.sin(2.0 * np.pi * us / length)
    dydx = amp * (2.0 * np.pi / length) * np.cos(2.0 * np.pi * us / length)
    norm_len = np.sqrt(1.0 + dydx * dydx)
    nx = -dydx / norm_len
    ny = 1.0 / norm_len

    front_xy = np.column_stack([us + 0.5 * thick * nx, yc + 0.5 * thick * ny])
    back_xy = np.column_stack([us - 0.5 * thick * nx, yc - 0.5 * thick * ny])

    zeros = np.zeros((nu, 1), dtype=np.float64)
    tops = np.full((nu, 1), height, dtype=np.float64)

    # Vertex layout: [F0 (0..nu-1), B0 (nu..2nu-1), F1 (2nu..3nu-1), B1 (3nu..4nu-1)]
    vertices = np.vstack(
        [
            np.hstack([front_xy, zeros]),
            np.hstack([back_xy, zeros]),
            np.hstack([front_xy, tops]),
            np.hstack([back_xy, tops]),
        ]
    )

    f0 = np.arange(0, nu)
    b0 = np.arange(nu, 2 * nu)
    f1 = np.arange(2 * nu, 3 * nu)
    b1 = np.arange(3 * nu, 4 * nu)

    faces: List[List[int]] = []
    for i in range(nu - 1):
        # Bottom cap (-Z)
        faces.append([f0[i], f0[i + 1], b0[i + 1]])
        faces.append([f0[i], b0[i + 1], b0[i]])
        # Top cap (+Z)
        faces.append([f1[i], b1[i + 1], f1[i + 1]])
        faces.append([f1[i], b1[i], b1[i + 1]])
        # Front wall (+normal)
        faces.append([f0[i], f1[i], f1[i + 1]])
        faces.append([f0[i], f1[i + 1], f0[i + 1]])
        # Back wall (-normal)
        faces.append([b0[i], b0[i + 1], b1[i + 1]])
        faces.append([b0[i], b1[i + 1], b1[i]])

    # Left end cap (i = 0)
    faces.append([f0[0], b0[0], b1[0]])
    faces.append([f0[0], b1[0], f1[0]])
    # Right end cap (i = nu - 1)
    faces.append([f0[-1], b1[-1], b0[-1]])
    faces.append([f0[-1], f1[-1], b1[-1]])

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=True)
    trimesh.repair.fix_normals(mesh)
    return center_mesh_on_bed(mesh, cfg)


class VolumetricField:
    """Lightweight 3D distance field for outer-surface wireframe & deep-core seeding."""

    def __init__(self, mesh: trimesh.Trimesh, pitch: float):
        self.mesh = mesh
        self.pitch = pitch
        vg = mesh.voxelized(pitch=pitch).fill()
        mat = vg.matrix.astype(bool)

        pad = 3
        self.vol = np.pad(mat, pad_width=pad, mode="constant", constant_values=False)
        self.origin = vg.transform[:3, 3] - pad * pitch
        self.shape = self.vol.shape

        self.dist_in = ndimage.distance_transform_edt(self.vol) * pitch
        dist_out = ndimage.distance_transform_edt(~self.vol) * pitch
        self.sdf = dist_out - self.dist_in


def clean_geom(geom: shapely.Geometry) -> shapely.Geometry:
    """Repair invalid or self-touching Shapely geometries so GEOS boolean ops never fail."""
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
    if geom is None or geom.is_empty:
        return Polygon()
    return geom


def safe_buffer(geom: shapely.Geometry, dist: float, **kwargs) -> shapely.Geometry:
    """Topology-safe Shapely buffer with automatic make_valid + precision fallback."""
    if geom is None or geom.is_empty:
        return Polygon()
    try:
        res = geom.buffer(dist, **kwargs)
        return clean_geom(res)
    except Exception:
        g = clean_geom(geom)
        if g.is_empty:
            return Polygon()
        try:
            return clean_geom(g.buffer(dist, **kwargs))
        except Exception:
            try:
                g_p = shapely.set_precision(g, 1e-4)
                return clean_geom(g_p.buffer(dist, **kwargs))
            except Exception:
                return Polygon()


def safe_union(geoms: List[shapely.Geometry]) -> shapely.Geometry:
    """Topology-safe unary_union with automatic repair and precision-grid fallback."""
    valid_list = [g for g in geoms if g is not None and not g.is_empty]
    if not valid_list:
        return Polygon()
    if len(valid_list) == 1:
        return clean_geom(valid_list[0])
    try:
        return clean_geom(shapely.unary_union(valid_list))
    except Exception:
        repaired = [clean_geom(g) for g in valid_list]
        repaired = [g for g in repaired if not g.is_empty]
        if not repaired:
            return Polygon()
        try:
            return clean_geom(shapely.unary_union(repaired))
        except Exception:
            try:
                snapped = [shapely.set_precision(g, 1e-4) for g in repaired]
                return clean_geom(shapely.unary_union(snapped))
            except Exception:
                acc = repaired[0]
                for g in repaired[1:]:
                    try:
                        acc = clean_geom(acc.union(g))
                    except Exception:
                        continue
                return acc


def safe_intersection(a: shapely.Geometry, b: shapely.Geometry) -> shapely.Geometry:
    """Topology-safe Shapely intersection (never throws TopologyException)."""
    if a is None or b is None or a.is_empty or b.is_empty:
        return Polygon()
    try:
        return a.intersection(b)
    except Exception:
        ca, cb = clean_geom(a), clean_geom(b)
        if ca.is_empty or cb.is_empty:
            return Polygon()
        try:
            return ca.intersection(cb)
        except Exception:
            try:
                return shapely.set_precision(ca, 1e-4).intersection(shapely.set_precision(cb, 1e-4))
            except Exception:
                return Polygon()


def safe_difference(a: shapely.Geometry, b: shapely.Geometry) -> shapely.Geometry:
    """Topology-safe Shapely difference (never throws TopologyException)."""
    if a is None or a.is_empty:
        return Polygon()
    if b is None or b.is_empty:
        return clean_geom(a)
    try:
        return clean_geom(a.difference(b))
    except Exception:
        ca, cb = clean_geom(a), clean_geom(b)
        if ca.is_empty:
            return Polygon()
        if cb.is_empty:
            return ca
        try:
            return clean_geom(ca.difference(cb))
        except Exception:
            try:
                return clean_geom(
                    shapely.set_precision(ca, 1e-4).difference(shapely.set_precision(cb, 1e-4))
                )
            except Exception:
                return ca


def extract_polylines(geom: shapely.Geometry) -> List[np.ndarray]:
    """Extract all 2D polyline coordinate arrays from a Shapely geometry."""
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
    """Flatten a Shapely geometry into non-trivial Polygon objects."""
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
    """Create a 2D boolean circular structuring element with given pixel radius."""
    r = max(1, radius_px)
    yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= (r * r)
