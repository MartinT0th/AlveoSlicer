"""
Headless Multi-Bed Workspace Scene Model.

Manages independent `BuildPlate` workspaces, non-destructive `ModelInstance`
transforms (Move/Rotate/Scale), per-face Paint-on-Supports labels, automatic
2D shelf packing (`auto_arrange`), and interactive plane cutting.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import numpy as np
import trimesh

from .mesh_ops import (
    SimplifiedFace,
    compute_placeable_faces,
    cut_mesh_by_plane,
    normalize_base_mesh,
    rotation_matrix_aligning_vectors,
)


@dataclass
class ModelInstance:
    """Single placed 3D model instance on a BuildPlate with non-destructive transforms & painted supports."""

    instance_id: str
    name: str
    source_name: str
    base_mesh: trimesh.Trimesh
    pos_x: float = 110.0
    pos_y: float = 110.0
    pos_z: float = 0.0
    rot_x: float = 0.0          # Euler X rotation (deg)
    rot_y: float = 0.0          # Euler Y rotation (deg)
    rot_z: float = 0.0          # Euler Z rotation (deg)
    scale_x: float = 100.0      # Scale percentage (%)
    scale_y: float = 100.0
    scale_z: float = 100.0
    uniform_scale: bool = True
    # Per-face support paint labels: 0 = neutral, +1 = enforcer (green), -1 = blocker (red)
    paint_support: Optional[np.ndarray] = None

    _cached_mesh: Optional[trimesh.Trimesh] = field(default=None, init=False, repr=False)
    _cached_faces: Optional[List[SimplifiedFace]] = field(default=None, init=False, repr=False)

    # --- Paint-on-Supports Helpers ---

    def ensure_paint_array(self) -> np.ndarray:
        """Return a valid per-face int8 paint array matching ``base_mesh.faces``."""
        n_faces = len(self.base_mesh.faces)
        if self.paint_support is None or len(self.paint_support) != n_faces:
            self.paint_support = np.zeros(n_faces, dtype=np.int8)
        return self.paint_support

    def paint_counts(self) -> Tuple[int, int]:
        """Return ``(enforcer_face_count, blocker_face_count)``."""
        if self.paint_support is None or len(self.paint_support) != len(self.base_mesh.faces):
            return 0, 0
        return int(np.count_nonzero(self.paint_support == 1)), int(np.count_nonzero(self.paint_support == -1))

    def paint_faces_in_sphere(self, world_point: np.ndarray, radius_mm: float, value: int) -> int:
        """Paint triangles within ``radius_mm`` of ``world_point`` with label ``value`` in {-1, 0, +1}."""
        mesh = self.transformed_mesh()
        if len(mesh.faces) == 0:
            return 0
        arr = self.ensure_paint_array()
        pt = np.asarray(world_point, dtype=np.float64)
        tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64)
        r_sq = float(radius_mm) * float(radius_mm)
        d_c = np.einsum("ij,ij->i", tris.mean(axis=1) - pt, tris.mean(axis=1) - pt)
        d_v = np.min(np.einsum("ijk,ijk->ij", tris - pt[None, None, :], tris - pt[None, None, :]), axis=1)
        hit = (d_c <= r_sq) | (d_v <= r_sq * 0.72)
        val = int(np.clip(value, -1, 1))
        changed = int(np.count_nonzero(hit & (arr != val)))
        if changed > 0:
            arr[hit] = val
            if self._cached_mesh is not None:
                self._cached_mesh.metadata["paint_support"] = arr.copy()
        return changed

    def smart_paint_overhangs(self, overhang_angle_deg: float = 45.0, value: int = 1) -> int:
        """Automatically label downward-facing overhang triangles above Z = 0.35 mm."""
        mesh = self.transformed_mesh()
        if len(mesh.faces) == 0:
            return 0
        arr = self.ensure_paint_array()
        normals = np.asarray(mesh.face_normals, dtype=np.float64)
        centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
        cos_thresh = math.cos(math.radians(max(5.0, min(85.0, overhang_angle_deg))))
        hit = (normals[:, 2] < -cos_thresh) & (centroids[:, 2] > 0.35)
        val = int(np.clip(value, -1, 1))
        changed = int(np.count_nonzero(hit & (arr != val)))
        arr[hit] = val
        if self._cached_mesh is not None:
            self._cached_mesh.metadata["paint_support"] = arr.copy()
        return changed

    def clear_paint(self) -> None:
        """Reset all painted support enforcers and blockers on this instance."""
        self.paint_support = np.zeros(len(self.base_mesh.faces), dtype=np.int8)
        if self._cached_mesh is not None:
            self._cached_mesh.metadata["paint_support"] = self.paint_support.copy()

    # --- Transform & Geometry Accessors ---

    def invalidate(self) -> None:
        """Clear cached transformed mesh and simplified landing faces after any transform edit."""
        self._cached_mesh = None
        self._cached_faces = None

    def transformed_mesh(self) -> trimesh.Trimesh:
        """Return the world-space transformed copy of ``base_mesh`` (cached until invalidated)."""
        if self._cached_mesh is not None:
            return self._cached_mesh

        m = self.base_mesh.copy()
        sx, sy, sz = (max(0.02, s / 100.0) for s in (self.scale_x, self.scale_y, self.scale_z))
        if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6 or abs(sz - 1.0) > 1e-6:
            m.apply_transform(np.diag([sx, sy, sz, 1.0]))

        if abs(self.rot_x) > 1e-6 or abs(self.rot_y) > 1e-6 or abs(self.rot_z) > 1e-6:
            m.apply_transform(
                trimesh.transformations.euler_matrix(
                    math.radians(self.rot_x),
                    math.radians(self.rot_y),
                    math.radians(self.rot_z),
                    axes="sxyz",
                )
            )

        bounds = m.bounds
        cx, cy = 0.5 * (bounds[0, 0] + bounds[1, 0]), 0.5 * (bounds[0, 1] + bounds[1, 1])
        m.apply_translation([self.pos_x - cx, self.pos_y - cy, self.pos_z - bounds[0, 2]])
        if self.paint_support is not None and len(self.paint_support) == len(m.faces):
            m.metadata["paint_support"] = self.paint_support.copy()
        self._cached_mesh = m
        return m

    def placeable_faces(self) -> List[SimplifiedFace]:
        """Return cached simplified convex-hull resting planes for Place-on-Face mode."""
        if self._cached_faces is None:
            self._cached_faces = compute_placeable_faces(self.transformed_mesh())
        return self._cached_faces

    def align_normal_to_bed(self, world_normal: np.ndarray) -> None:
        """Rotate this instance so ``world_normal`` points straight down (0, 0, -1) onto Z = 0."""
        r_cur = trimesh.transformations.euler_matrix(
            math.radians(self.rot_x), math.radians(self.rot_y), math.radians(self.rot_z), axes="sxyz"
        )
        r_new = rotation_matrix_aligning_vectors(world_normal, np.array([0.0, 0.0, -1.0])) @ r_cur
        rx, ry, rz = trimesh.transformations.euler_from_matrix(r_new, axes="sxyz")
        self.rot_x, self.rot_y, self.rot_z = round(math.degrees(rx), 2), round(math.degrees(ry), 2), round(math.degrees(rz), 2)
        self.pos_z = 0.0
        self.invalidate()

    def reset_transform(self, bed_cx: float, bed_cy: float) -> None:
        """Reset position to bed center, rotation to 0 deg, and scale to 100%."""
        self.pos_x, self.pos_y, self.pos_z = bed_cx, bed_cy, 0.0
        self.rot_x = self.rot_y = self.rot_z = 0.0
        self.scale_x = self.scale_y = self.scale_z = 100.0
        self.invalidate()

    def clone_instance(self, new_name: str, offset_x: float = 15.0, offset_y: float = 0.0) -> "ModelInstance":
        """Create an independent duplicate sharing ``base_mesh`` with an XY placement offset."""
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
    """Independent build plate holding placed `ModelInstance` items plus its cached slice output."""

    plate_id: str
    name: str
    instances: List[ModelInstance] = field(default_factory=list)
    slice_result: Any = None
    preview_data: Any = None
    gcode_text: str = ""

    @property
    def is_sliced(self) -> bool:
        """Return True when this plate has up-to-date G-code and preview data."""
        return bool(self.gcode_text) and (self.preview_data is not None)

    def clear_slice(self) -> None:
        """Invalidate cached slice results after any geometry or transform mutation."""
        self.slice_result = None
        self.preview_data = None
        self.gcode_text = ""

    def instance_meshes(self) -> List[trimesh.Trimesh]:
        """Return independent world-space copies of every instance mesh on this plate, preserving paint."""
        out: List[trimesh.Trimesh] = []
        for inst in self.instances:
            m = inst.transformed_mesh().copy()
            ps = inst.paint_support
            if ps is not None and len(ps) == len(m.faces):
                m.metadata["paint_support"] = ps.copy()
            out.append(m)
        return out

    def combined_mesh(self) -> Optional[trimesh.Trimesh]:
        """Concatenate all instances on this plate into one mesh, preserving per-face support paint."""
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
                any_paint = any_paint or bool(np.any(inst.paint_support != 0))
            else:
                paint_chunks.append(np.zeros(len(m.faces), dtype=np.int8))
        combined = trimesh.util.concatenate(meshes)
        if any_paint:
            combined.metadata["paint_support"] = np.concatenate(paint_chunks)
        return combined

    def get_instance(self, instance_id: Optional[str]) -> Optional[ModelInstance]:
        """Look up an instance on this plate by its unique ``instance_id``."""
        if not instance_id:
            return None
        return next((inst for inst in self.instances if inst.instance_id == instance_id), None)

    def auto_arrange(self, bed_x: float, bed_y: float, spacing: float = 8.0) -> None:
        """Pack all instances on the plate with non-overlapping shelf packing centered on the bed."""
        if not self.instances:
            return
        if len(self.instances) == 1:
            inst = self.instances[0]
            inst.pos_x, inst.pos_y, inst.pos_z = bed_x * 0.5, bed_y * 0.5, 0.0
            inst.invalidate()
            self.clear_slice()
            return

        items = sorted(
            ((inst, float(inst.transformed_mesh().extents[0]), float(inst.transformed_mesh().extents[1])) for inst in self.instances),
            key=lambda t: t[2],
            reverse=True,
        )
        max_row_w = max(40.0, bed_x - 2.0 * spacing)
        cur_x = cur_y = row_h = 0.0
        placed: List[Tuple[ModelInstance, float, float, float, float]] = []

        for inst, w, d in items:
            if cur_x > 0.0 and cur_x + w > max_row_w:
                cur_x, cur_y, row_h = 0.0, cur_y + row_h + spacing, 0.0
            placed.append((inst, cur_x + 0.5 * w, cur_y + 0.5 * d, w, d))
            cur_x += w + spacing
            row_h = max(row_h, d)

        min_x = min(cx - 0.5 * w for _, cx, _, w, _ in placed)
        max_x = max(cx + 0.5 * w for _, cx, _, w, _ in placed)
        min_y = min(cy - 0.5 * d for _, _, cy, _, d in placed)
        max_y = max(cy + 0.5 * d for _, _, cy, _, d in placed)
        shift_x = bed_x * 0.5 - 0.5 * (min_x + max_x)
        shift_y = bed_y * 0.5 - 0.5 * (min_y + max_y)

        for inst, cx, cy, _w, _d in placed:
            inst.pos_x, inst.pos_y, inst.pos_z = cx + shift_x, cy + shift_y, 0.0
            inst.invalidate()
        self.clear_slice()


class WorkspaceScene:
    """Multi-bed scene controller managing independent BuildPlates and active selection."""

    def __init__(self) -> None:
        """Initialize a workspace containing a single empty build plate ('Plate 1')."""
        self.plates: List[BuildPlate] = [BuildPlate(plate_id=uuid.uuid4().hex[:8], name="Plate 1")]
        self.active_plate_idx: int = 0
        self.selected_instance_id: Optional[str] = None
        self._plate_counter: int = 1

    @property
    def active_plate(self) -> BuildPlate:
        """Return the currently active `BuildPlate`."""
        return self.plates[self.active_plate_idx]

    @property
    def selected_instance(self) -> Optional[ModelInstance]:
        """Return the currently selected `ModelInstance` on the active plate, if any."""
        return self.active_plate.get_instance(self.selected_instance_id)

    def add_plate(self, name: Optional[str] = None) -> BuildPlate:
        """Create, append, and activate a new empty `BuildPlate`."""
        self._plate_counter += 1
        plate = BuildPlate(plate_id=uuid.uuid4().hex[:8], name=name or f"Plate {self._plate_counter}")
        self.plates.append(plate)
        self.active_plate_idx = len(self.plates) - 1
        self.selected_instance_id = None
        return plate

    def remove_plate(self, idx: int) -> bool:
        """Remove build plate ``idx`` (always retaining at least one plate)."""
        if len(self.plates) <= 1 or not (0 <= idx < len(self.plates)):
            return False
        self.plates.pop(idx)
        self.active_plate_idx = min(self.active_plate_idx, len(self.plates) - 1)
        self._sync_selection_to_active_plate()
        return True

    def set_active_plate(self, idx: int) -> None:
        """Switch active plate to ``idx`` and keep selection valid for that plate."""
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
        """Normalize and add ``mesh`` onto the active build plate, auto-arranging if requested."""
        plate = self.active_plate
        inst = ModelInstance(
            instance_id=uuid.uuid4().hex[:10],
            name=self._next_instance_name(plate, source_name),
            source_name=source_name,
            base_mesh=normalize_base_mesh(mesh),
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
        """Duplicate the selected model instance onto the active plate."""
        inst = self.selected_instance
        if inst is None:
            return None
        plate = self.active_plate
        ext = inst.transformed_mesh().extents
        clone = inst.clone_instance(
            self._next_instance_name(plate, inst.source_name), offset_x=float(ext[0]) + 8.0
        )
        plate.instances.append(clone)
        if clone.pos_x + 0.5 * float(ext[0]) > bed_x - 4.0:
            plate.auto_arrange(bed_x, bed_y)
        else:
            plate.clear_slice()
        self.selected_instance_id = clone.instance_id
        return clone

    def remove_selected(self) -> bool:
        """Delete the currently selected instance from the active plate."""
        plate = self.active_plate
        inst = self.selected_instance
        if inst is None:
            return False
        plate.instances.remove(inst)
        plate.clear_slice()
        self._sync_selection_to_active_plate()
        return True

    def move_selected_to_plate(self, target_plate_idx: int, bed_x: float, bed_y: float) -> bool:
        """Transfer the currently selected instance to ``target_plate_idx``."""
        if not (0 <= target_plate_idx < len(self.plates)) or target_plate_idx == self.active_plate_idx:
            return False
        inst = self.selected_instance
        if inst is None:
            return False
        src, dst = self.active_plate, self.plates[target_plate_idx]
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
        """Cut the selected `ModelInstance` along a 3D plane and replace it with watertight halves."""
        inst = self.selected_instance
        if inst is None or (not keep_upper and not keep_lower):
            return []

        world_mesh = inst.transformed_mesh()
        normal = np.asarray(plane_normal, dtype=np.float64)
        nlen = float(np.linalg.norm(normal))
        normal = normal / nlen if nlen > 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

        upper_m, lower_m = cut_mesh_by_plane(
            world_mesh, plane_origin, normal, keep_upper=keep_upper, keep_lower=keep_lower
        )
        if upper_m is None and lower_m is None:
            return []

        plate = self.active_plate
        orig_name, orig_x, orig_y = inst.source_name, inst.pos_x, inst.pos_y
        plate.instances.remove(inst)
        created: List[ModelInstance] = []

        def _spawn(mesh_part: trimesh.Trimesh, suffix: str, cut_normal: np.ndarray, lay_flat: bool, dx: float) -> ModelInstance:
            m = mesh_part.copy()
            if lay_flat:
                m.apply_transform(rotation_matrix_aligning_vectors(cut_normal, np.array([0.0, 0.0, -1.0])))
            bounds = m.bounds
            cx, cy = 0.5 * (bounds[0, 0] + bounds[1, 0]), 0.5 * (bounds[0, 1] + bounds[1, 1])
            new_inst = ModelInstance(
                instance_id=uuid.uuid4().hex[:10],
                name=self._next_instance_name(plate, f"{orig_name} ({suffix})"),
                source_name=f"{orig_name} ({suffix})",
                base_mesh=normalize_base_mesh(m),
                pos_x=max(10.0, min(bed_x - 10.0, (cx if not lay_flat else orig_x) + dx)),
                pos_y=max(10.0, min(bed_y - 10.0, cy if not lay_flat else orig_y)),
                pos_z=0.0,
            )
            plate.instances.append(new_inst)
            return new_inst

        both = (upper_m is not None) and (lower_m is not None)
        sep = 0.5 * float(world_mesh.extents[0]) + 8.0 if both else 0.0
        if lower_m is not None:
            created.append(_spawn(lower_m, "Lower", normal, place_lower_on_bed, -0.5 * sep if both else 0.0))
        if upper_m is not None:
            created.append(_spawn(upper_m, "Upper", -normal, place_upper_on_bed, 0.5 * sep if both else 0.0))

        plate.clear_slice()
        if created:
            self.selected_instance_id = created[-1].instance_id
        else:
            self._sync_selection_to_active_plate()
        return created
