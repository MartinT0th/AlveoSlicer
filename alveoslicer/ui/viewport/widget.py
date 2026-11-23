"""OpenGL 3D Editor & G-code Previewer widget (``AlveoViewport3D``).

Handles camera orbit/pan/zoom, multi-plate grid rendering, 3D object/plate
picking, Move/Rotate/Scale gizmo manipulation, Place-on-Face picking,
interactive Cut Plane dragging, Paint-on-Supports strokes, and volumetric
rounded-bead G-code + post-dosing Filled Resin previewing.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
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

from ...core.mesh_ops import SimplifiedFace
from ...core.scene import BuildPlate, ModelInstance
from ...gcode.parser import (
    DOSING_COLOR,
    FEATURE_TYPE_TO_ID,
    FEATURE_TYPES,
    TRAVEL_COLOR,
    GCodePreviewData,
)
from ...infill.lattice import Capsule
from ...pipeline import SliceResult
from ..styling.theme import ThemePalette, get_palette
from .builders import (
    build_bed_geometry,
    build_brush_reticle,
    build_cut_plane_geometry,
    build_extrusion_beads,
    build_filled_resin_cycles,
    build_gizmo_geometry,
    build_nozzle_cone,
    mesh_to_lit_buffer,
)
from .math_utils import (
    euler_matrix_xyz,
    plate_grid_offset,
    ray_intersects_aabb,
    ray_segment_dist,
    ray_triangles_hit,
    ray_triangles_hit_full,
)
from .shaders import (
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST,
    GL_FLOAT,
    GL_LINES,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    GL_TRIANGLES,
    LINE_FRAG_SRC,
    LINE_VERT_SRC,
    LIT_FRAG_SRC,
    LIT_VERT_SRC,
)


class AlveoViewport3D(QOpenGLWidget):
    """PrusaSlicer-style OpenGL 3D Editor & Volumetric G-code Previewer."""

    instance_selected = Signal(str)
    selection_cleared = Signal()
    plate_selected = Signal(int)                                         # clicked inactive plate index
    instance_moved = Signal(str, float, float)                           # instance_id, pos_x, pos_y
    instance_moved_z = Signal(str, float)                                # instance_id, pos_z
    instance_rotated = Signal(str, float, float, float)                  # instance_id, rot_x, rot_y, rot_z
    instance_scaled = Signal(str, float)                                 # instance_id, uniform_scale_pct
    face_picked = Signal(object)                                         # world outward normal np.ndarray (3,)
    cut_plane_changed = Signal(float, float, float, float, float, float) # ox, oy, oz, rx, ry, rz
    support_painted = Signal(str, float, float, float, float, int)       # inst_id, hx, hy, hz, radius, val

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize camera, scene caches, tool state, and CPU geometry buffers."""
        super().__init__(parent)
        self.setMinimumSize(420, 340)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._palette: ThemePalette = get_palette("dark")
        self.mode: str = "editor"        # "editor" | "preview"
        self.edit_tool: str = "select"   # "select" | "move" | "rotate" | "scale" | "place_on_face" | "cut" | "paint_supports"

        # Build plate dimensions & multi-plate 3D grid state
        self.bed_x: float = 220.0
        self.bed_y: float = 220.0
        self.bed_z: float = 220.0
        self.bed_shape: str = "rectangular"
        self._num_plates: int = 1
        self._active_plate_idx: int = 0
        self._plate_offsets: List[Tuple[float, float]] = [(0.0, 0.0)]
        self._active_plate_offset = np.array([0.0, 0.0, 0.0], dtype=np.float64)

        # Orbit camera & pointer drag state
        self.cam_target = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self.cam_yaw: float = -42.0
        self.cam_pitch: float = 34.0
        self.cam_dist: float = 260.0
        self._last_eye = np.array([110.0, -90.0, 140.0], dtype=np.float64)
        self._last_mouse: Optional[QPoint] = None
        self._press_mouse: Optional[QPoint] = None
        self._drag_mode: str = ""
        self._drag_axis: str = ""
        self._pressed_on_empty: bool = False

        # Picking & tool state
        self._instances_by_id: Dict[str, ModelInstance] = {}
        self._selected_id: Optional[str] = None
        self._pick_instances: List[Tuple[str, np.ndarray, np.ndarray, np.ndarray]] = []
        self._inactive_pick_instances: List[Tuple[int, str, np.ndarray, np.ndarray, np.ndarray]] = []
        self._placeable_faces: List[SimplifiedFace] = []
        self._hovered_face_idx: int = -1

        self._gizmo_center = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self._gizmo_size: float = 26.0
        self._gizmo_axes: List[Tuple[str, np.ndarray, np.ndarray]] = []

        self.cut_origin = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self.cut_rot_x: float = 0.0
        self.cut_rot_y: float = 0.0
        self.cut_rot_z: float = 0.0
        self._cut_plane_half_size: float = 35.0
        self._drag_init_cut_origin = self.cut_origin.copy()
        self._drag_init_cut_rot: Tuple[float, float, float] = (0.0, 0.0, 0.0)

        self.paint_brush_radius: float = 4.0
        self.paint_brush_mode: int = 1
        self._brush_hover_pt: Optional[np.ndarray] = None
        self._brush_hover_norm: Optional[np.ndarray] = None

        self._drag_inst_id: Optional[str] = None
        self._drag_move_offset: Tuple[float, float] = (0.0, 0.0)
        self._drag_init_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._drag_init_rot: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._drag_init_scale: float = 100.0

        # Visibility & preview scrubber state
        self.show_mesh: bool = True
        self.show_infill_wireframe: bool = True
        self.show_travels: bool = False
        self.show_dosing: bool = True
        self.visible_types: Dict[int, bool] = {i: True for i in range(len(FEATURE_TYPES))}
        self.preview_data: Optional[GCodePreviewData] = None
        self.layer_min: int = 1
        self.layer_max: int = 1
        self.top_move_limit: int = 10**9

        # CPU buffers
        z9 = lambda: np.zeros((0, 9), dtype=np.float32)
        z6 = lambda: np.zeros((0, 6), dtype=np.float32)
        self._bed_plate_cpu, self._bed_grid_cpu, self._bed_grid_inactive_cpu = z9(), z6(), z6()
        self._mesh_cpu, self._inactive_mesh_cpu, self._selection_box_cpu = z9(), z9(), z6()
        self._wipe_tower_mesh_cpu, self._wipe_tower_lines_cpu = z9(), z6()
        self._place_faces_cpu, self._place_hover_cpu = z9(), z9()
        self._wireframe_cpu = z6()
        self._gizmo_lit_cpu, self._gizmo_lines_cpu = z9(), z6()
        self._cut_plane_lit_cpu, self._cut_plane_lines_cpu = z9(), z6()
        self._brush_reticle_cpu = z6()
        # Static preview GPU datasets (uploaded ONCE in set_gcode_preview; never copied on scrub)
        self._bead_type_cpu: Dict[int, np.ndarray] = {}
        self._bead_type_layers: Dict[int, np.ndarray] = {}
        self._bead_type_moves: Dict[int, np.ndarray] = {}
        self._cap_type_cpu: Dict[int, np.ndarray] = {}
        self._cap_type_layers: Dict[int, np.ndarray] = {}
        self._cap_type_moves: Dict[int, np.ndarray] = {}
        self._resin_all_cpu = z9()
        self._resin_cycle_ranges: List[Tuple[int, List[Tuple[int, int, int]]]] = []
        self._travels_all_cpu = z6()
        # Lightweight per-scrub draw ranges (O(log N) searchsorted; zero GPU re-upload)
        self._active_bead_ranges: Dict[int, Tuple[int, int]] = {}
        self._active_cap_ranges: Dict[int, Tuple[int, int]] = {}
        self._active_resin_ranges: List[Tuple[int, int]] = []
        self._active_travel_range: Tuple[int, int] = (0, 0)
        self._active_dosing_cpu, self._nozzle_cpu = z6(), z9()

        # VBO dirty flags & GL handles
        self._gl_ready = False
        self._dirty_bed = self._dirty_mesh = self._dirty_wipe = self._dirty_place = True
        self._dirty_wire = self._dirty_gizmo = self._dirty_cut = self._dirty_brush = True
        self._dirty_preview_static = self._dirty_preview_dynamic = True
        self._gl: Optional[QOpenGLFunctions] = None
        self._lit_prog: Optional[QOpenGLShaderProgram] = None
        self._line_prog: Optional[QOpenGLShaderProgram] = None
        self._lit_locs: Tuple[int, int, int, int, int, int] = (-1, -1, -1, -1, -1, -1)
        self._line_locs: Tuple[int, int, int, int] = (-1, -1, -1, -1)
        self._vbos: Dict[str, Tuple[QOpenGLBuffer, int]] = {}

        self._rebuild_bed_geometry()

    # ------------------------------------------------------------------
    # Public Scene, Theme, Cut Plane & Paint API
    # ------------------------------------------------------------------

    @property
    def is_looking_from_bottom(self) -> bool:
        """True whenever the camera eye sits underneath the build plate (``Z < 0``)."""
        return float(self._last_eye[2]) < 0.0

    def set_theme_mode(self, mode: str) -> None:
        """Apply Dark/Light theme colors to the viewport background, build plate, and active meshes."""
        self._palette = get_palette(mode)
        self._rebuild_bed_geometry()
        if self._instances_by_id:
            self.set_scene_instances(
                list(self._instances_by_id.values()),
                self._selected_id,
                capsules=getattr(self, "_last_capsules", None),
            )
        self.update()

    def set_mode(self, mode: str) -> None:
        """Switch between ``'editor'`` and ``'preview'`` rendering modes."""
        self.mode = mode
        if mode != "editor":
            self._hovered_face_idx = -1
            self._brush_hover_pt = None
            self._rebuild_brush_reticle()
        self.update()

    def set_edit_tool(self, tool: str) -> None:
        """Activate a 3D editing tool and configure cursors, overlays, and gizmos."""
        self.edit_tool = tool
        self.setMouseTracking(tool in ("place_on_face", "paint_supports"))
        self._brush_hover_pt = None
        self._rebuild_brush_reticle()

        cursor_map = {
            "place_on_face": Qt.CursorShape.CrossCursor,
            "paint_supports": Qt.CursorShape.CrossCursor,
            "cut": Qt.CursorShape.SplitVCursor,
            "move": Qt.CursorShape.SizeAllCursor,
            "rotate": Qt.CursorShape.OpenHandCursor,
            "scale": Qt.CursorShape.OpenHandCursor,
        }
        if tool in cursor_map:
            self.setCursor(cursor_map[tool])
        else:
            self.unsetCursor()

        if tool == "place_on_face":
            self._rebuild_placeable_faces()
        else:
            self._clear_placeable_faces()
        if tool == "cut":
            self._reset_cut_plane_to_selected()

        self._rebuild_gizmo_geometry()
        self._rebuild_cut_plane_geometry()
        self.update()

    def _refresh_plate_offsets(self) -> None:
        """Recompute world-space offsets for every plate in the multi-bed grid."""
        n = max(1, self._num_plates)
        self._plate_offsets = [plate_grid_offset(i, n, self.bed_x, self.bed_y) for i in range(n)]
        ox, oy = self._plate_offsets[max(0, min(n - 1, self._active_plate_idx))]
        self._active_plate_offset = np.array([ox, oy, 0.0], dtype=np.float64)

    def set_bed_size(self, bx: float, by: float, bz: float, bed_shape: Optional[str] = None) -> None:
        """Update build volume dimensions and shape, rebuilding plate geometry if changed."""
        shape = bed_shape if bed_shape is not None else self.bed_shape
        if (
            abs(bx - self.bed_x) < 1e-3
            and abs(by - self.bed_y) < 1e-3
            and abs(bz - self.bed_z) < 1e-3
            and shape == self.bed_shape
        ):
            return
        self.bed_x, self.bed_y, self.bed_z, self.bed_shape = float(bx), float(by), float(bz), shape
        self._refresh_plate_offsets()
        self._rebuild_bed_geometry()
        self.update()

    def set_cut_plane(
        self, ox: float, oy: float, oz: float, rot_x: float, rot_y: float, rot_z: float, emit_signal: bool = True
    ) -> None:
        """Set the 3D cutting plane origin and Euler tilt angles (degrees)."""
        self.cut_origin = np.array([float(ox), float(oy), float(oz)], dtype=np.float64)
        self.cut_rot_x, self.cut_rot_y, self.cut_rot_z = float(rot_x), float(rot_y), float(rot_z)
        self._rebuild_cut_plane_geometry()
        if emit_signal:
            self._emit_cut_plane()
        self.update()

    def _emit_cut_plane(self) -> None:
        """Emit ``cut_plane_changed`` with current origin and rotation."""
        self.cut_plane_changed.emit(
            float(self.cut_origin[0]), float(self.cut_origin[1]), float(self.cut_origin[2]),
            self.cut_rot_x, self.cut_rot_y, self.cut_rot_z,
        )

    def cut_plane_normal(self) -> np.ndarray:
        """Return the unit normal vector of the active 3D cutting plane."""
        n = euler_matrix_xyz(self.cut_rot_x, self.cut_rot_y, self.cut_rot_z) @ np.array([0.0, 0.0, 1.0])
        nl = float(np.linalg.norm(n))
        return n / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

    def _reset_cut_plane_to_selected(self) -> None:
        """Center the cutting plane horizontally through the currently selected model."""
        inst = self._instances_by_id.get(self._selected_id) if self._selected_id else None
        if inst is not None:
            mesh = inst.transformed_mesh()
            c = 0.5 * (mesh.bounds[0] + mesh.bounds[1])
            self.cut_origin = np.array([float(c[0]), float(c[1]), float(c[2])], dtype=np.float64)
            self._cut_plane_half_size = max(18.0, max(float(np.max(mesh.extents)), 20.0) * 0.72)
        else:
            self.cut_origin = np.array([self.bed_x * 0.5, self.bed_y * 0.5, 15.0], dtype=np.float64)
            self._cut_plane_half_size = 35.0
        self.cut_rot_x = self.cut_rot_y = self.cut_rot_z = 0.0
        self._rebuild_cut_plane_geometry()
        self._emit_cut_plane()

    def set_paint_brush(self, radius_mm: float, mode: int) -> None:
        """Configure the Paint-on-Supports brush radius (mm) and mode (+1/-1/0)."""
        self.paint_brush_radius = max(0.8, min(35.0, float(radius_mm)))
        self.paint_brush_mode = int(mode)
        self._rebuild_brush_reticle()
        self.update()

    def set_wipe_tower_preview(
        self, enabled: bool, x: float, y: float, w: float, d: float, h: float = 20.0
    ) -> None:
        """Update the translucent wipe-tower footprint preview box on the active plate."""
        if not enabled or w <= 0.0 or d <= 0.0:
            self._wipe_tower_mesh_cpu = np.zeros((0, 9), dtype=np.float32)
            self._wipe_tower_lines_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_wipe = True
            self.update()
            return

        h_box = max(4.0, float(h))
        box = trimesh.creation.box(extents=[float(w), float(d), h_box])
        box.apply_translation([float(x) + 0.5 * float(w), float(y) + 0.5 * float(d), 0.5 * h_box])
        self._wipe_tower_mesh_cpu = mesh_to_lit_buffer(box, (0.28, 0.72, 0.88))

        x0, y0, x1, y1, z1 = float(x), float(y), float(x + w), float(y + d), h_box
        c = (0.38, 0.84, 0.98)
        edges = [
            ((x0, y0, 0.08), (x1, y0, 0.08)), ((x1, y0, 0.08), (x1, y1, 0.08)),
            ((x1, y1, 0.08), (x0, y1, 0.08)), ((x0, y1, 0.08), (x0, y0, 0.08)),
            ((x0, y0, z1), (x1, y0, z1)), ((x1, y0, z1), (x1, y1, z1)),
            ((x1, y1, z1), (x0, y1, z1)), ((x0, y1, z1), (x0, y0, z1)),
            ((x0, y0, 0.08), (x0, y0, z1)), ((x1, y0, 0.08), (x1, y0, z1)),
            ((x1, y1, 0.08), (x1, y1, z1)), ((x0, y1, 0.08), (x0, y1, z1)),
        ]
        self._wipe_tower_lines_cpu = np.asarray(
            [[*pt, *c] for seg in edges for pt in seg], dtype=np.float32
        )
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

        self._num_plates, self._active_plate_idx = n, a_idx
        self._refresh_plate_offsets()

        if a_idx != prev_active and np.any(np.abs(self._active_plate_offset - prev_offset) > 1e-4):
            self.cam_target = self.cam_target + (self._active_plate_offset - prev_offset)
            self._refresh_camera_eye()

        inactive_chunks: List[np.ndarray] = []
        self._inactive_pick_instances = []
        muted_col = (0.52, 0.54, 0.58)
        for p_idx, plate in enumerate(plates):
            if p_idx == a_idx:
                continue
            ox, oy = self._plate_offsets[p_idx]
            off64 = np.array([ox, oy, 0.0], dtype=np.float64)
            off32 = np.array([ox, oy, 0.0], dtype=np.float32)
            for inst in plate.instances:
                mesh = inst.transformed_mesh()
                if mesh is None or len(mesh.faces) == 0:
                    continue
                buf = mesh_to_lit_buffer(mesh, muted_col)
                if len(buf) > 0:
                    buf[:, 0:3] += off32[None, :]
                    inactive_chunks.append(buf)
                tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64) + off64[None, None, :]
                self._inactive_pick_instances.append((
                    p_idx, inst.instance_id,
                    np.asarray(mesh.bounds[0], dtype=np.float64) + off64,
                    np.asarray(mesh.bounds[1], dtype=np.float64) + off64,
                    tris,
                ))

        self._inactive_mesh_cpu = np.vstack(inactive_chunks) if inactive_chunks else np.zeros((0, 9), dtype=np.float32)
        self.set_scene_instances(plates[a_idx].instances if plates else [], selected_id, capsules=capsules)

    def set_scene_instances(
        self,
        instances: List[ModelInstance],
        selected_id: Optional[str],
        capsules: Optional[List[Capsule]] = None,
    ) -> None:
        """Upload active build-plate model meshes, selection frame, gizmo anchor, and wireframe."""
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
            col = self._palette.gl_model_selected if is_sel else self._palette.gl_model_default
            paint = inst.paint_support if (inst.paint_support is not None and len(inst.paint_support) == len(mesh.faces)) else None
            chunks.append(mesh_to_lit_buffer(mesh, col, paint_support=paint))

            tris_f64 = np.asarray(mesh.vertices[mesh.faces], dtype=np.float64)
            bmin, bmax = np.asarray(mesh.bounds[0], dtype=np.float64), np.asarray(mesh.bounds[1], dtype=np.float64)
            self._pick_instances.append((inst.instance_id, bmin, bmax, tris_f64))

            if is_sel:
                self._gizmo_center = 0.5 * (bmin + bmax)
                span = max(float(np.max(bmax - bmin)), 16.0)
                self._gizmo_size = max(14.0, min(32.0, span * 0.45))
                self._cut_plane_half_size = max(18.0, span * 0.72)

                pad = 1.2
                x0, y0, z0 = float(bmin[0] - pad), float(bmin[1] - pad), max(0.06, float(bmin[2]))
                x1, y1, z1 = float(bmax[0] + pad), float(bmax[1] + pad), float(bmax[2] + pad)
                yc, tick = self._palette.gl_accent, min(6.0, 0.22 * (z1 - z0))
                segs = [
                    ((x0, y0, 0.08), (x1, y0, 0.08)), ((x1, y0, 0.08), (x1, y1, 0.08)),
                    ((x1, y1, 0.08), (x0, y1, 0.08)), ((x0, y1, 0.08), (x0, y0, 0.08)),
                    ((x0, y0, 0.08), (x0, y0, 0.08 + tick)), ((x1, y0, 0.08), (x1, y0, 0.08 + tick)),
                    ((x1, y1, 0.08), (x1, y1, 0.08 + tick)), ((x0, y1, 0.08), (x0, y1, 0.08 + tick)),
                ]
                for a, b in segs:
                    sel_box_lines.extend([[*a, *yc], [*b, *yc]])

        self._mesh_cpu = np.vstack(chunks) if chunks else np.zeros((0, 9), dtype=np.float32)
        self._selection_box_cpu = np.asarray(sel_box_lines, dtype=np.float32) if sel_box_lines else np.zeros((0, 6), dtype=np.float32)
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
        """Display a standalone ``Trimesh`` (used by tests or single-mesh previews)."""
        self._instances_by_id, self._selected_id, self._pick_instances = {}, None, []
        self._selection_box_cpu = np.zeros((0, 6), dtype=np.float32)
        self._mesh_cpu = (
            mesh_to_lit_buffer(mesh, self._palette.gl_model_selected)
            if (mesh is not None and len(mesh.faces) > 0)
            else np.zeros((0, 9), dtype=np.float32)
        )
        self.set_wireframe(capsules)
        self._dirty_mesh = True
        self.update()

    def set_wireframe(self, capsules: Optional[List[Capsule]]) -> None:
        """Upload subsampled 3D cellular lattice struts for translucent editor overlay."""
        self._last_capsules = capsules
        if not capsules:
            self._wireframe_cpu = np.zeros((0, 6), dtype=np.float32)
        else:
            sub = capsules[::max(1, len(capsules) // 6000)]
            pts = np.empty((len(sub) * 2, 6), dtype=np.float32)
            for idx, cap in enumerate(sub):
                pts[2 * idx, 0:3] = cap.a
                pts[2 * idx + 1, 0:3] = cap.b
            pts[:, 3:6] = np.array(self._palette.gl_uv, dtype=np.float32)
            self._wireframe_cpu = pts
        self._dirty_wire = True
        self.update()

    def set_gcode_preview(
        self, data: Optional[GCodePreviewData], slice_result: Optional[SliceResult] = None
    ) -> None:
        """Compile static per-feature rounded filament VBOs and Filled Resin slabs once per slice."""
        self.preview_data = data
        self._bead_type_cpu = {}
        self._bead_type_layers = {}
        self._bead_type_moves = {}
        self._cap_type_cpu = {}
        self._cap_type_layers = {}
        self._cap_type_moves = {}
        self._resin_all_cpu = np.zeros((0, 9), dtype=np.float32)
        self._resin_cycle_ranges = []
        self._travels_all_cpu = np.zeros((0, 6), dtype=np.float32)

        if data is None or data.num_layers == 0:
            self._dirty_preview_static = True
            self._rebuild_preview_slice()
            return

        beads, caps, caps_seg_idx = build_extrusion_beads(
            data.ext_p0, data.ext_p1, data.ext_width, data.ext_height, data.ext_type, layers=data.ext_layer
        )
        n_types = len(FEATURE_TYPES)
        ext_type = np.clip(data.ext_type, 0, n_types - 1)
        cap_type = ext_type[caps_seg_idx] if len(caps_seg_idx) > 0 else np.zeros((0,), dtype=np.int32)
        cap_layers = data.ext_layer[caps_seg_idx] if len(caps_seg_idx) > 0 else np.zeros((0,), dtype=np.int32)
        cap_moves = data.ext_move_idx[caps_seg_idx] if len(caps_seg_idx) > 0 else np.zeros((0,), dtype=np.int32)

        # Partition static geometry per feature type preserving monotonic (layer, move_idx) order
        for tid in range(n_types):
            idx = np.flatnonzero(ext_type == tid)
            if len(idx) > 0:
                self._bead_type_cpu[tid] = np.ascontiguousarray(beads[idx].reshape(-1, 9), dtype=np.float32)
                self._bead_type_layers[tid] = data.ext_layer[idx]
                self._bead_type_moves[tid] = data.ext_move_idx[idx]
            if len(caps_seg_idx) > 0:
                c_idx = np.flatnonzero(cap_type == tid)
                if len(c_idx) > 0:
                    # Ensure monotonic (layer, move_idx) ordering across start/end caps
                    order = np.lexsort((cap_moves[c_idx], cap_layers[c_idx]))
                    c_ord = c_idx[order]
                    self._cap_type_cpu[tid] = np.ascontiguousarray(caps[c_ord].reshape(-1, 9), dtype=np.float32)
                    self._cap_type_layers[tid] = cap_layers[c_ord]
                    self._cap_type_moves[tid] = cap_moves[c_ord]

        # Pack all filled-resin cycle slabs into one static VBO with per-layer vertex spans
        resin_cycles = build_filled_resin_cycles(slice_result)
        if resin_cycles:
            resin_chunks: List[np.ndarray] = []
            offset = 0
            for cyc_end, layer_meshes in resin_cycles:
                spans: List[Tuple[int, int, int]] = []
                for l_idx, verts in layer_meshes:
                    cnt = len(verts)
                    if cnt > 0:
                        resin_chunks.append(verts)
                        spans.append((l_idx, offset, cnt))
                        offset += cnt
                if spans:
                    self._resin_cycle_ranges.append((cyc_end, spans))
            if resin_chunks:
                self._resin_all_cpu = np.ascontiguousarray(np.vstack(resin_chunks), dtype=np.float32)

        # Pack all travel segments once in monotonic (layer, move_idx) order
        if len(data.trv_p0) > 0:
            tbuf = np.empty((len(data.trv_p0) * 2, 6), dtype=np.float32)
            tbuf[0::2, 0:3] = data.trv_p0
            tbuf[1::2, 0:3] = data.trv_p1
            tbuf[:, 3:6] = np.array(TRAVEL_COLOR, dtype=np.float32)
            self._travels_all_cpu = tbuf

        self._dirty_preview_static = True
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
        """Update active layer range, move scrubber, feature visibility, and refresh preview VBOs."""
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

    # ------------------------------------------------------------------
    # Camera Controls
    # ------------------------------------------------------------------

    def _refresh_camera_eye(self) -> np.ndarray:
        """Recompute world-space camera eye coordinates from yaw/pitch/distance."""
        yaw, pitch = math.radians(self.cam_yaw), math.radians(self.cam_pitch)
        cp = math.cos(pitch)
        self._last_eye = self.cam_target + self.cam_dist * np.array(
            [cp * math.sin(yaw), -cp * math.cos(yaw), math.sin(pitch)], dtype=np.float64
        )
        return self._last_eye

    def fit_to_mesh(self, mesh: Optional[trimesh.Trimesh]) -> None:
        """Frame the orbit camera tightly around ``mesh`` on the active plate."""
        if mesh is None or len(mesh.vertices) == 0:
            self.fit_to_bed()
            return
        center = 0.5 * (mesh.bounds[0] + mesh.bounds[1])
        self.cam_target = center.astype(np.float64) + self._active_plate_offset
        self.cam_dist = max(45.0, max(float(np.max(mesh.extents)), 15.0) * 2.2)
        self._refresh_camera_eye()
        self.update()

    def fit_to_bed(self) -> None:
        """Center the orbit camera on the active build plate."""
        self.cam_target = np.array([self.bed_x * 0.5, self.bed_y * 0.5, 12.0], dtype=np.float64) + self._active_plate_offset
        self.cam_dist = max(self.bed_x, self.bed_y) * 1.35
        self._refresh_camera_eye()
        self.update()

    def set_camera_preset(self, preset: str) -> None:
        """Snap camera orientation to a standard CAD preset (iso/top/bottom/front/right)."""
        presets = {"iso": (-42.0, 34.0), "top": (0.0, 88.5), "bottom": (-35.0, -58.0), "front": (0.0, 6.0), "right": (-90.0, 10.0)}
        if preset in presets:
            self.cam_yaw, self.cam_pitch = presets[preset]
        self._refresh_camera_eye()
        self.update()

    # ------------------------------------------------------------------
    # Overlay & Preview Buffer Rebuilders
    # ------------------------------------------------------------------

    def _rebuild_gizmo_geometry(self) -> None:
        if self.edit_tool not in ("move", "rotate", "scale") or not self._selected_id or self._selected_id not in self._instances_by_id:
            self._gizmo_axes, self._gizmo_lit_cpu, self._gizmo_lines_cpu = [], np.zeros((0, 9), dtype=np.float32), np.zeros((0, 6), dtype=np.float32)
        else:
            self._gizmo_axes, self._gizmo_lines_cpu, self._gizmo_lit_cpu = build_gizmo_geometry(
                self.edit_tool, self._gizmo_center, self._gizmo_size
            )
        self._dirty_gizmo = True

    def _rebuild_cut_plane_geometry(self) -> None:
        if self.edit_tool != "cut":
            self._cut_plane_lit_cpu = np.zeros((0, 9), dtype=np.float32)
            self._cut_plane_lines_cpu = np.zeros((0, 6), dtype=np.float32)
        else:
            self._cut_plane_lit_cpu, self._cut_plane_lines_cpu = build_cut_plane_geometry(
                self.cut_origin, self.cut_rot_x, self.cut_rot_y, self.cut_rot_z, self._cut_plane_half_size
            )
        self._dirty_cut = True

    def _rebuild_brush_reticle(self) -> None:
        if self.edit_tool != "paint_supports" or self._brush_hover_pt is None or self._brush_hover_norm is None:
            self._brush_reticle_cpu = np.zeros((0, 6), dtype=np.float32)
        else:
            self._brush_reticle_cpu = build_brush_reticle(
                self._brush_hover_pt, self._brush_hover_norm, self.paint_brush_radius, self.paint_brush_mode
            )
        self._dirty_brush = True

    def _clear_placeable_faces(self) -> None:
        self._placeable_faces, self._hovered_face_idx = [], -1
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
        base_chunks, hover_chunks = [], []
        for idx, face in enumerate(self._placeable_faces):
            if len(face.triangles) == 0:
                continue
            buf = np.empty((len(face.triangles), 3, 9), dtype=np.float32)
            buf[:, :, 0:3] = face.triangles
            buf[:, :, 3:6] = face.normal.astype(np.float32)[None, None, :]
            if idx == self._hovered_face_idx:
                buf[:, :, 6:9] = (1.0, 0.94, 0.22)
                hover_chunks.append(buf.reshape(-1, 9))
            else:
                buf[:, :, 6:9] = (0.10, 0.80, 0.98)
                base_chunks.append(buf.reshape(-1, 9))
        self._place_faces_cpu = np.vstack(base_chunks) if base_chunks else np.zeros((0, 9), dtype=np.float32)
        self._place_hover_cpu = np.vstack(hover_chunks) if hover_chunks else np.zeros((0, 9), dtype=np.float32)
        self._dirty_place = True

    def _rebuild_bed_geometry(self) -> None:
        self._bed_plate_cpu, self._bed_grid_cpu, self._bed_grid_inactive_cpu = build_bed_geometry(
            self.bed_x, self.bed_y, self.bed_z, self.bed_shape, self._palette
        )
        self._dirty_bed = True

    @property
    def _active_resin_cpu(self) -> np.ndarray:
        """Return active Filled Resin vertices on demand (tests/inspection); rendering uses static GPU VBO ranges."""
        if not self._active_resin_ranges or len(self._resin_all_cpu) == 0:
            return np.zeros((0, 9), dtype=np.float32)
        if len(self._active_resin_ranges) == 1:
            s, c = self._active_resin_ranges[0]
            return self._resin_all_cpu[s : s + c]
        return np.vstack([self._resin_all_cpu[s : s + c] for s, c in self._active_resin_ranges])

    @staticmethod
    def _range_for_layer_move(
        layers_arr: np.ndarray,
        moves_arr: np.ndarray,
        layer_min: int,
        layer_max: int,
        top_move_limit: int,
    ) -> Tuple[int, int]:
        """O(log N) half-open slice ``(start, count)`` for monotonic ``(layer, move_idx)`` arrays."""
        s = int(np.searchsorted(layers_arr, layer_min, side="left"))
        e_lo = int(np.searchsorted(layers_arr, layer_max, side="left"))
        e_hi = int(np.searchsorted(layers_arr, layer_max, side="right"))
        if e_lo < e_hi:
            e = e_lo + int(np.searchsorted(moves_arr[e_lo:e_hi], top_move_limit, side="right"))
        else:
            e = e_hi
        return (s, e - s) if e > s else (0, 0)

    def _rebuild_preview_slice(self) -> None:
        """Update O(log N) GPU draw ranges for the current layer/move/feature filters without re-uploading VBOs."""
        data = self.preview_data
        self._active_bead_ranges = {}
        self._active_cap_ranges = {}
        self._active_resin_ranges = []
        self._active_travel_range = (0, 0)

        if data is None or not self._bead_type_layers:
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
            self._dirty_preview_dynamic = True
            self.update()
            return

        lmin, lmax, mlim = self.layer_min, self.layer_max, self.top_move_limit
        for tid, l_arr in self._bead_type_layers.items():
            if not self.visible_types.get(tid, True):
                continue
            s, cnt = self._range_for_layer_move(l_arr, self._bead_type_moves[tid], lmin, lmax, mlim)
            if cnt > 0:
                self._active_bead_ranges[tid] = (s * 48, cnt * 48)
            c_arr = self._cap_type_layers.get(tid)
            if c_arr is not None:
                cs, ccnt = self._range_for_layer_move(c_arr, self._cap_type_moves[tid], lmin, lmax, mlim)
                if ccnt > 0:
                    self._active_cap_ranges[tid] = (cs * 24, ccnt * 24)

        # Filled resin appears strictly AFTER its cure cycle has been dosed
        resin_id = FEATURE_TYPE_TO_ID.get("Filled resin", -1)
        if resin_id >= 0 and self.visible_types.get(resin_id, True) and self._resin_cycle_ranges:
            merged: List[Tuple[int, int]] = []
            for cyc_end, spans in self._resin_cycle_ranges:
                if lmax < cyc_end:
                    continue
                if lmax == cyc_end and data.layer_move_counts:
                    idx0 = max(0, min(len(data.layer_move_counts) - 1, cyc_end - 1))
                    if mlim < data.layer_move_counts[idx0]:
                        continue
                for l_idx, start_v, cnt_v in spans:
                    if lmin <= l_idx <= lmax:
                        if merged and merged[-1][0] + merged[-1][1] == start_v:
                            merged[-1] = (merged[-1][0], merged[-1][1] + cnt_v)
                        else:
                            merged.append((start_v, cnt_v))
            self._active_resin_ranges = merged

        # Locate active nozzle endpoint in O(log N) via searchsorted
        e_lo = int(np.searchsorted(data.ext_layer, lmax, side="left"))
        e_hi = int(np.searchsorted(data.ext_layer, lmax, side="right"))
        if e_lo < e_hi:
            top_pos = e_lo + int(np.searchsorted(data.ext_move_idx[e_lo:e_hi], mlim, side="right")) - 1
            if top_pos >= e_lo:
                self._nozzle_cpu = build_nozzle_cone(*map(float, data.ext_p1[top_pos]))
            else:
                self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)
        else:
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)

        if self.show_travels and len(data.trv_layer) > 0:
            ts, tcnt = self._range_for_layer_move(data.trv_layer, data.trv_move_idx, lmin, lmax, mlim)
            self._active_travel_range = (ts * 2, tcnt * 2)

        if self.show_dosing and data.dosing_markers:
            d_lines: List[List[float]] = []
            prev_by_layer: Dict[int, Tuple[float, float, float]] = {}
            for m in data.dosing_markers:
                if not (lmin <= m.layer_idx <= lmax):
                    continue
                is_top = m.layer_idx == lmax
                col = DOSING_COLOR if is_top else (0.0, 0.55, 0.72)
                pin_h, r = (4.2, 1.0) if is_top else (1.8, 0.55)
                d_lines.extend([
                    [m.x, m.y, m.z, *col], [m.x, m.y, m.z + pin_h, *col],
                    [m.x - r, m.y, m.z + 0.25, *col], [m.x + r, m.y, m.z + 0.25, *col],
                    [m.x, m.y - r, m.z + 0.25, *col], [m.x, m.y + r, m.z + 0.25, *col],
                ])
                if is_top and m.layer_idx in prev_by_layer:
                    px, py, pz = prev_by_layer[m.layer_idx]
                    d_lines.extend([[px, py, pz + pin_h * 0.7, 1.0, 0.92, 0.35], [m.x, m.y, m.z + pin_h * 0.7, 1.0, 0.92, 0.35]])
                prev_by_layer[m.layer_idx] = (m.x, m.y, m.z)
            self._active_dosing_cpu = np.asarray(d_lines, dtype=np.float32) if d_lines else np.zeros((0, 6), dtype=np.float32)
        else:
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)

        self._dirty_preview_dynamic = True
        self.update()

    # ------------------------------------------------------------------
    # OpenGL Lifecycle & Rendering
    # ------------------------------------------------------------------

    def initializeGL(self) -> None:
        """Compile lit and line shader programs and mark all VBOs dirty for first upload."""
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()

        self._lit_prog = QOpenGLShaderProgram(self)
        self._lit_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, LIT_VERT_SRC)
        self._lit_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, LIT_FRAG_SRC)
        self._lit_prog.link()
        self._lit_locs = (
            self._lit_prog.uniformLocation("u_mvp"),
            self._lit_prog.uniformLocation("u_alpha_v"),
            self._lit_prog.uniformLocation("u_view_dir"),
            self._lit_prog.attributeLocation("a_pos"),
            self._lit_prog.attributeLocation("a_norm"),
            self._lit_prog.attributeLocation("a_color"),
        )

        self._line_prog = QOpenGLShaderProgram(self)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, LINE_VERT_SRC)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, LINE_FRAG_SRC)
        self._line_prog.link()
        self._line_locs = (
            self._line_prog.uniformLocation("u_mvp"),
            self._line_prog.uniformLocation("u_alpha_v"),
            self._line_prog.attributeLocation("a_pos"),
            self._line_prog.attributeLocation("a_color"),
        )

        self._gl_ready = True
        self._dirty_bed = self._dirty_mesh = self._dirty_wipe = self._dirty_place = True
        self._dirty_wire = self._dirty_gizmo = self._dirty_cut = self._dirty_brush = True
        self._dirty_preview_static = self._dirty_preview_dynamic = True

    def _upload_vbo(self, key: str, arr: np.ndarray) -> None:
        """Allocate or update GPU vertex buffer ``key`` from numpy array ``arr``."""
        if key in self._vbos:
            vbo, _ = self._vbos[key]
        else:
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo.create()
        count = int(arr.shape[0])
        if count > 0:
            vbo.bind()
            raw = np.ascontiguousarray(arr, dtype=np.float32)
            vbo.allocate(raw.data, raw.nbytes)
            vbo.release()
        self._vbos[key] = (vbo, count)

    def _sync_vbos(self) -> None:
        """Upload any modified CPU buffers to GPU VBOs prior to drawing."""
        groups = [
            ("_dirty_bed", [("bed_plate", self._bed_plate_cpu), ("bed_grid", self._bed_grid_cpu), ("bed_grid_inactive", self._bed_grid_inactive_cpu)]),
            ("_dirty_mesh", [("mesh", self._mesh_cpu), ("inactive_mesh", self._inactive_mesh_cpu), ("sel_box", self._selection_box_cpu)]),
            ("_dirty_wipe", [("wipe_mesh", self._wipe_tower_mesh_cpu), ("wipe_lines", self._wipe_tower_lines_cpu)]),
            ("_dirty_place", [("place_faces", self._place_faces_cpu), ("place_hover", self._place_hover_cpu)]),
            ("_dirty_wire", [("wireframe", self._wireframe_cpu)]),
            ("_dirty_gizmo", [("gizmo_lit", self._gizmo_lit_cpu), ("gizmo_lines", self._gizmo_lines_cpu)]),
            ("_dirty_cut", [("cut_lit", self._cut_plane_lit_cpu), ("cut_lines", self._cut_plane_lines_cpu)]),
            ("_dirty_brush", [("brush_reticle", self._brush_reticle_cpu)]),
            ("_dirty_preview_dynamic", [("dosing", self._active_dosing_cpu), ("nozzle", self._nozzle_cpu)]),
        ]
        for flag, items in groups:
            if getattr(self, flag):
                for key, arr in items:
                    self._upload_vbo(key, arr)
                setattr(self, flag, False)

        if self._dirty_preview_static:
            empty9 = np.zeros((0, 9), dtype=np.float32)
            for tid in range(len(FEATURE_TYPES)):
                self._upload_vbo(f"beads_t{tid}", self._bead_type_cpu.get(tid, empty9))
                self._upload_vbo(f"caps_t{tid}", self._cap_type_cpu.get(tid, empty9))
            self._upload_vbo("resin_all", self._resin_all_cpu)
            self._upload_vbo("travels_all", self._travels_all_cpu)
            self._dirty_preview_static = False

    def _compute_mvp_and_eye(self) -> Tuple[QMatrix4x4, QVector3D]:
        """Compute perspective-view matrix and camera view direction."""
        aspect = max(1, self.width()) / float(max(1, self.height()))
        eye = self._refresh_camera_eye()
        vd = self.cam_target - eye
        proj = QMatrix4x4()
        proj.perspective(42.0, aspect, max(0.5, self.cam_dist * 0.004), max(2500.0, self.cam_dist * 14.0))
        view = QMatrix4x4()
        view.lookAt(
            QVector3D(float(eye[0]), float(eye[1]), float(eye[2])),
            QVector3D(float(self.cam_target[0]), float(self.cam_target[1]), float(self.cam_target[2])),
            QVector3D(0.0, 0.0, 1.0),
        )
        return proj * view, QVector3D(float(-vd[0]), float(-vd[1]), float(-vd[2]))

    @staticmethod
    def _translated_mvp(mvp: QMatrix4x4, ox: float, oy: float) -> QMatrix4x4:
        """Return a copy of ``mvp`` translated by ``(ox, oy, 0)``."""
        if abs(ox) < 1e-6 and abs(oy) < 1e-6:
            return mvp
        m = QMatrix4x4(mvp)
        m.translate(float(ox), float(oy), 0.0)
        return m

    def _draw_lit(
        self,
        key: str,
        mvp: QMatrix4x4,
        view_dir: QVector3D,
        alpha: float = 1.0,
        first: int = 0,
        count_override: Optional[int] = None,
    ) -> None:
        """Render a shaded triangle VBO (or sub-range) with two-light CAD hemisphere lighting."""
        if key not in self._vbos or self._lit_prog is None or self._gl is None:
            return
        vbo, count = self._vbos[key]
        draw_count = count if count_override is None else min(count_override, max(0, count - first))
        if draw_count <= 0:
            return
        u_mvp, u_alpha, u_view, lp, ln, lc = self._lit_locs
        prog = self._lit_prog
        prog.bind()
        prog.setUniformValue(u_mvp, mvp)
        prog.setUniformValue(u_alpha, QVector2D(float(alpha), 0.0))
        prog.setUniformValue(u_view, view_dir)
        vbo.bind()
        prog.enableAttributeArray(lp)
        prog.setAttributeBuffer(lp, GL_FLOAT, 0, 3, 36)
        prog.enableAttributeArray(ln)
        prog.setAttributeBuffer(ln, GL_FLOAT, 12, 3, 36)
        prog.enableAttributeArray(lc)
        prog.setAttributeBuffer(lc, GL_FLOAT, 24, 3, 36)
        self._gl.glDrawArrays(GL_TRIANGLES, first, draw_count)
        prog.disableAttributeArray(lp)
        prog.disableAttributeArray(ln)
        prog.disableAttributeArray(lc)
        vbo.release()
        prog.release()

    def _draw_lit_ranges(
        self,
        key: str,
        ranges: List[Tuple[int, int]],
        mvp: QMatrix4x4,
        view_dir: QVector3D,
        alpha: float = 1.0,
    ) -> None:
        """Render multiple contiguous sub-ranges of one static shaded VBO with a single bind."""
        if not ranges or key not in self._vbos or self._lit_prog is None or self._gl is None:
            return
        vbo, total = self._vbos[key]
        if total <= 0:
            return
        u_mvp, u_alpha, u_view, lp, ln, lc = self._lit_locs
        prog = self._lit_prog
        prog.bind()
        prog.setUniformValue(u_mvp, mvp)
        prog.setUniformValue(u_alpha, QVector2D(float(alpha), 0.0))
        prog.setUniformValue(u_view, view_dir)
        vbo.bind()
        prog.enableAttributeArray(lp)
        prog.setAttributeBuffer(lp, GL_FLOAT, 0, 3, 36)
        prog.enableAttributeArray(ln)
        prog.setAttributeBuffer(ln, GL_FLOAT, 12, 3, 36)
        prog.enableAttributeArray(lc)
        prog.setAttributeBuffer(lc, GL_FLOAT, 24, 3, 36)
        gl_draw = self._gl.glDrawArrays
        for first, cnt in ranges:
            if cnt > 0 and first < total:
                gl_draw(GL_TRIANGLES, first, min(cnt, total - first))
        prog.disableAttributeArray(lp)
        prog.disableAttributeArray(ln)
        prog.disableAttributeArray(lc)
        vbo.release()
        prog.release()

    def _draw_lines(
        self,
        key: str,
        mvp: QMatrix4x4,
        alpha: float = 1.0,
        width: float = 1.2,
        first: int = 0,
        count_override: Optional[int] = None,
    ) -> None:
        """Render an unlit colored line-segment VBO (or sub-range)."""
        if key not in self._vbos or self._line_prog is None or self._gl is None:
            return
        vbo, count = self._vbos[key]
        draw_count = count if count_override is None else min(count_override, max(0, count - first))
        if draw_count <= 0:
            return
        u_mvp, u_alpha, lp, lc = self._line_locs
        prog = self._line_prog
        prog.bind()
        prog.setUniformValue(u_mvp, mvp)
        prog.setUniformValue(u_alpha, QVector2D(float(alpha), 0.0))
        vbo.bind()
        prog.enableAttributeArray(lp)
        prog.setAttributeBuffer(lp, GL_FLOAT, 0, 3, 24)
        prog.enableAttributeArray(lc)
        prog.setAttributeBuffer(lc, GL_FLOAT, 12, 3, 24)
        self._gl.glLineWidth(width)
        self._gl.glDrawArrays(GL_LINES, first, draw_count)
        prog.disableAttributeArray(lp)
        prog.disableAttributeArray(lc)
        vbo.release()
        prog.release()

    def paintGL(self) -> None:
        """Render the complete 3D build-plate workspace, tools, or volumetric G-code preview."""
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

        # Build plates automatically hide when viewing from underneath (eye_z < 0)
        if not self.is_looking_from_bottom:
            self._draw_lit("bed_plate", mvp_active, view_dir, alpha=1.0)
            self._draw_lines("bed_grid", mvp_active, alpha=0.98, width=1.25)
            if len(self._plate_offsets) > 1:
                plate_a = 0.24 if self.mode == "editor" else 0.14
                grid_a = 0.28 if self.mode == "editor" else 0.16
                gl.glDepthMask(False)
                for p_idx, (ox, oy) in enumerate(self._plate_offsets):
                    if p_idx != self._active_plate_idx:
                        mvp_p = self._translated_mvp(mvp, ox, oy)
                        self._draw_lit("bed_plate", mvp_p, view_dir, alpha=plate_a)
                        self._draw_lines("bed_grid_inactive", mvp_p, alpha=grid_a, width=1.0)
                gl.glDepthMask(True)

        if self.mode == "editor":
            if self._wipe_tower_lines_cpu.shape[0] > 0:
                self._draw_lines("wipe_lines", mvp_active, alpha=0.88, width=1.4)
                gl.glDepthMask(False)
                self._draw_lit("wipe_mesh", mvp_active, view_dir, alpha=0.22)
                gl.glDepthMask(True)
            if self._selection_box_cpu.shape[0] > 0:
                self._draw_lines("sel_box", mvp_active, alpha=0.95, width=1.8)

            has_wire = self.show_infill_wireframe and self._wireframe_cpu.shape[0] > 0 and self.edit_tool not in ("paint_supports", "cut")
            if has_wire:
                self._draw_lines("wireframe", mvp_active, alpha=0.95, width=1.5)
            if self.show_mesh:
                if has_wire:
                    gl.glDepthMask(False)
                    self._draw_lit("mesh", mvp_active, view_dir, alpha=0.42)
                    gl.glDepthMask(True)
                else:
                    self._draw_lit("mesh", mvp_active, view_dir, alpha=1.0)
                if self._inactive_mesh_cpu.shape[0] > 0:
                    gl.glDepthMask(False)
                    self._draw_lit("inactive_mesh", mvp, view_dir, alpha=0.34)
                    gl.glDepthMask(True)

            if self.edit_tool == "place_on_face" and (self._place_faces_cpu.shape[0] > 0 or self._place_hover_cpu.shape[0] > 0):
                gl.glDepthMask(False)
                self._draw_lit("place_faces", mvp_active, view_dir, alpha=0.45)
                self._draw_lit("place_hover", mvp_active, view_dir, alpha=0.82)
                gl.glDepthMask(True)
            if self.edit_tool in ("move", "rotate", "scale") and (self._gizmo_lines_cpu.shape[0] > 0 or self._gizmo_lit_cpu.shape[0] > 0):
                gl.glDisable(GL_DEPTH_TEST)
                self._draw_lines("gizmo_lines", mvp_active, alpha=0.96, width=2.0)
                self._draw_lit("gizmo_lit", mvp_active, view_dir, alpha=0.96)
                gl.glEnable(GL_DEPTH_TEST)
            if self.edit_tool == "cut" and self._cut_plane_lit_cpu.shape[0] > 0:
                self._draw_lines("cut_lines", mvp_active, alpha=0.96, width=1.8)
                gl.glDepthMask(False)
                self._draw_lit("cut_lit", mvp_active, view_dir, alpha=0.30)
                gl.glDepthMask(True)
            if self.edit_tool == "paint_supports" and self._brush_reticle_cpu.shape[0] > 0:
                gl.glDisable(GL_DEPTH_TEST)
                self._draw_lines("brush_reticle", mvp_active, alpha=0.98, width=2.0)
                gl.glEnable(GL_DEPTH_TEST)
        else:
            for tid, (first, cnt) in self._active_bead_ranges.items():
                self._draw_lit(f"beads_t{tid}", mvp_active, view_dir, alpha=1.0, first=first, count_override=cnt)
            for tid, (first, cnt) in self._active_cap_ranges.items():
                self._draw_lit(f"caps_t{tid}", mvp_active, view_dir, alpha=1.0, first=first, count_override=cnt)
            if self._active_resin_ranges:
                gl.glDepthMask(False)
                self._draw_lit_ranges("resin_all", self._active_resin_ranges, mvp_active, view_dir, alpha=0.68)
                gl.glDepthMask(True)
            self._draw_lit("nozzle", mvp_active, view_dir, alpha=0.95)
            if self.show_travels and self._active_travel_range[1] > 0:
                self._draw_lines(
                    "travels_all",
                    mvp_active,
                    alpha=0.55,
                    width=1.0,
                    first=self._active_travel_range[0],
                    count_override=self._active_travel_range[1],
                )
            if self.show_dosing:
                self._draw_lines("dosing", mvp_active, alpha=0.95, width=1.8)

    # ------------------------------------------------------------------
    # Ray Casting & Picking
    # ------------------------------------------------------------------

    def unproject_ray_world(self, sx: float, sy: float) -> Tuple[np.ndarray, np.ndarray]:
        """Unproject screen pixel ``(sx, sy)`` into a world-space ``(origin, unit_dir)`` ray."""
        ndc_x = (2.0 * sx / max(1.0, float(self.width()))) - 1.0
        ndc_y = 1.0 - (2.0 * sy / max(1.0, float(self.height())))
        mvp, _ = self._compute_mvp_and_eye()
        inv_mvp, ok = mvp.inverted()
        if not ok:
            return self.cam_target.copy(), np.array([0.0, 0.0, -1.0], dtype=np.float64)

        p_near = inv_mvp.map(QVector4D(ndc_x, ndc_y, -1.0, 1.0))
        p_far = inv_mvp.map(QVector4D(ndc_x, ndc_y, 1.0, 1.0))
        nw = p_near.w() if abs(p_near.w()) > 1e-8 else 1.0
        fw = p_far.w() if abs(p_far.w()) > 1e-8 else 1.0
        o = np.array([p_near.x() / nw, p_near.y() / nw, p_near.z() / nw], dtype=np.float64)
        d = np.array([p_far.x() / fw, p_far.y() / fw, p_far.z() / fw], dtype=np.float64) - o
        dl = float(np.linalg.norm(d))
        return o, (d / dl if dl > 1e-9 else d)

    def unproject_ray(self, sx: float, sy: float) -> Tuple[np.ndarray, np.ndarray]:
        """Unproject screen pixel ``(sx, sy)`` into active-build-plate local coordinates."""
        o_w, d = self.unproject_ray_world(sx, sy)
        return o_w - self._active_plate_offset, d

    def _pick_inactive_instance_plate(self, sx: float, sy: float) -> Optional[int]:
        if not self._inactive_pick_instances:
            return None
        orig, direction = self.unproject_ray_world(sx, sy)
        inv_dir = np.where(np.abs(direction) > 1e-9, 1.0 / direction, 1e12)
        best_plate, best_t = None, float("inf")
        for p_idx, _iid, bmin, bmax, tris in self._inactive_pick_instances:
            if ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                t = ray_triangles_hit(orig, direction, tris)
                if t is not None and t < best_t:
                    best_t, best_plate = t, p_idx
        return best_plate

    def _pick_inactive_plate(self, sx: float, sy: float) -> Optional[int]:
        if len(self._plate_offsets) <= 1:
            return None
        orig, direction = self.unproject_ray_world(sx, sy)
        if abs(direction[2]) < 1e-6:
            return None
        t = -orig[2] / direction[2]
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
                if (lx - 0.5 * self.bed_x) ** 2 + (ly - 0.5 * self.bed_y) ** 2 <= rad * rad:
                    return p_idx
            elif 0.0 <= lx <= self.bed_x and 0.0 <= ly <= self.bed_y:
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
        best_id, best_t = None, float("inf")
        for inst_id, bmin, bmax, tris in self._pick_instances:
            if ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                t = ray_triangles_hit(orig, direction, tris)
                if t is not None and t < best_t:
                    best_t, best_id = t, inst_id
        return best_id

    def _pick_selected_surface(self, sx: float, sy: float) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if not self._selected_id:
            return None
        orig, direction = self.unproject_ray(sx, sy)
        inv_dir = np.where(np.abs(direction) > 1e-9, 1.0 / direction, 1e12)
        for inst_id, bmin, bmax, tris in self._pick_instances:
            if inst_id == self._selected_id:
                if not ray_intersects_aabb(orig, inv_dir, bmin, bmax):
                    return None
                hit = ray_triangles_hit_full(orig, direction, tris)
                return (hit[1], hit[2]) if hit is not None else None
        return None

    def _pick_gizmo_axis(self, sx: float, sy: float) -> str:
        if not self._gizmo_axes or self.edit_tool not in ("move", "scale"):
            return ""
        orig, direction = self.unproject_ray(sx, sy)
        best_axis, best_d = "", max(2.2, self._gizmo_size * 0.14)
        for axis_name, a, b in self._gizmo_axes:
            d, _ = ray_segment_dist(orig, direction, a, b)
            if d < best_d:
                best_d, best_axis = d, axis_name
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
        return t > 0.0 and float(np.linalg.norm((orig + t * direction) - self.cut_origin)) <= self._cut_plane_half_size * 1.45

    def _pick_placeable_face(self, sx: float, sy: float) -> int:
        if not self._placeable_faces:
            return -1
        orig, direction = self.unproject_ray(sx, sy)
        best_idx, best_t = -1, float("inf")
        for idx, face in enumerate(self._placeable_faces):
            t = ray_triangles_hit(orig, direction, face.triangles.astype(np.float64))
            if t is not None and t < best_t:
                best_t, best_idx = t, idx
        return best_idx

    # ------------------------------------------------------------------
    # Mouse Event Handlers
    # ------------------------------------------------------------------

    def _emit_paint_at(self, sx: float, sy: float, paint_val: int) -> bool:
        if not self._selected_id:
            return False
        hit = self._pick_selected_surface(sx, sy)
        if hit is None:
            return False
        pt, norm = hit
        self._brush_hover_pt, self._brush_hover_norm = pt, norm
        self._rebuild_brush_reticle()
        self.support_painted.emit(
            self._selected_id, float(pt[0]), float(pt[1]), float(pt[2]), float(self.paint_brush_radius), int(paint_val)
        )
        return True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press for painting, cutting, gizmo drags, picking, orbiting, and panning."""
        pos = event.position().toPoint()
        self._last_mouse = self._press_mouse = pos
        btn, mods = event.button(), event.modifiers()
        self._drag_axis, self._pressed_on_empty = "", False

        if self.mode == "editor" and self.edit_tool == "paint_supports":
            if btn == Qt.MouseButton.MiddleButton:
                self._drag_mode = "pan"
                return
            if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
                if mods & Qt.KeyboardModifier.AltModifier:
                    self._drag_mode = "orbit"
                    return
                val = 0 if (mods & Qt.KeyboardModifier.ShiftModifier) else (-1 if btn == Qt.MouseButton.RightButton else self.paint_brush_mode)
                if self._emit_paint_at(pos.x(), pos.y(), val):
                    self._drag_mode = f"paint:{val}"
                    return
                self._drag_mode = "pan" if btn == Qt.MouseButton.RightButton else "orbit"
                return

        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._drag_mode = "pan"
            return

        if btn == Qt.MouseButton.LeftButton:
            if self.mode == "editor" and self.edit_tool == "cut" and self._hit_cut_plane(pos.x(), pos.y()):
                self._drag_mode = "cut_tilt" if (mods & Qt.KeyboardModifier.ShiftModifier) else "cut_move"
                self._drag_init_cut_origin = self.cut_origin.copy()
                self._drag_init_cut_rot = (self.cut_rot_x, self.cut_rot_y, self.cut_rot_z)
                return

            if self.mode == "editor" and self.edit_tool == "place_on_face":
                face_idx = self._pick_placeable_face(pos.x(), pos.y())
                if face_idx >= 0:
                    self._drag_mode = ""
                    self.face_picked.emit(self._placeable_faces[face_idx].normal.copy())
                    return

            if self.mode == "editor" and self._selected_id and self.edit_tool in ("move", "scale"):
                axis_hit = self._pick_gizmo_axis(pos.x(), pos.y())
                inst = self._instances_by_id.get(self._selected_id)
                if axis_hit and inst is not None:
                    self._drag_mode, self._drag_axis, self._drag_inst_id = f"gizmo_{self.edit_tool}", axis_hit, self._selected_id
                    self._drag_init_pos = (inst.pos_x, inst.pos_y, inst.pos_z)
                    self._drag_init_scale = inst.scale_x
                    return

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
                        self._drag_mode, self._drag_inst_id = self.edit_tool, hit_id
                        if self.edit_tool == "move":
                            hit_xy = self._ray_bed_plane_hit(pos.x(), pos.y(), z_plane=inst.pos_z)
                            self._drag_move_offset = (inst.pos_x - hit_xy[0], inst.pos_y - hit_xy[1]) if hit_xy else (0.0, 0.0)
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
        """Handle hover reticles, tool drags, gizmo transforms, and orbit/pan camera moves."""
        pos = event.position().toPoint()

        if not self._drag_mode:
            if self.mode == "editor" and self.edit_tool == "place_on_face":
                idx = self._pick_placeable_face(pos.x(), pos.y())
                if idx != self._hovered_face_idx:
                    self._hovered_face_idx = idx
                    self._rebuild_placeable_face_buffers()
                    self.update()
            elif self.mode == "editor" and self.edit_tool == "paint_supports":
                hit = self._pick_selected_surface(pos.x(), pos.y())
                self._brush_hover_pt, self._brush_hover_norm = hit if hit is not None else (None, None)
                self._rebuild_brush_reticle()
                self.update()
            return

        if self._last_mouse is None:
            return
        dx, dy = pos.x() - self._last_mouse.x(), pos.y() - self._last_mouse.y()
        self._last_mouse = pos

        if self._drag_mode.startswith("paint:"):
            self._emit_paint_at(pos.x(), pos.y(), int(self._drag_mode.split(":")[1]))
            return

        if self._drag_mode == "cut_move" and self._press_mouse is not None:
            delta = (self._press_mouse.y() - pos.y()) * (self.cam_dist * 0.0022)
            self.cut_origin = self._drag_init_cut_origin + self.cut_plane_normal() * delta
            self.cut_origin[2] = max(0.2, min(self.bed_z, float(self.cut_origin[2])))
            self._rebuild_cut_plane_geometry()
            self._emit_cut_plane()
            self.update()
            return

        if self._drag_mode == "cut_tilt" and self._press_mouse is not None:
            rx0, ry0, rz0 = self._drag_init_cut_rot
            self.cut_rot_x = max(-89.0, min(89.0, round(rx0 + (pos.y() - self._press_mouse.y()) * 0.45, 1)))
            self.cut_rot_y = max(-89.0, min(89.0, round(ry0 + (pos.x() - self._press_mouse.x()) * 0.45, 1)))
            self.cut_rot_z = rz0
            self._rebuild_cut_plane_geometry()
            self._emit_cut_plane()
            self.update()
            return

        if self._drag_mode == "gizmo_move" and self._drag_inst_id and self._press_mouse is not None:
            tot_dx, tot_dy = pos.x() - self._press_mouse.x(), self._press_mouse.y() - pos.y()
            scale_mm = self.cam_dist * 0.0025
            px0, py0, pz0 = self._drag_init_pos
            if self._drag_axis == "x":
                self.instance_moved.emit(self._drag_inst_id, round(max(0.0, min(self.bed_x, px0 + tot_dx * scale_mm)), 2), py0)
            elif self._drag_axis == "y":
                self.instance_moved.emit(self._drag_inst_id, px0, round(max(0.0, min(self.bed_y, py0 + (tot_dy + tot_dx * 0.35) * scale_mm)), 2))
            elif self._drag_axis == "z":
                self.instance_moved_z.emit(self._drag_inst_id, round(max(0.0, min(self.bed_z, pz0 + tot_dy * scale_mm)), 2))
            return

        if self._drag_mode == "gizmo_scale" and self._drag_inst_id and self._press_mouse is not None:
            factor = math.exp(((pos.x() - self._press_mouse.x()) + (self._press_mouse.y() - pos.y())) * 0.0045)
            self.instance_scaled.emit(self._drag_inst_id, max(5.0, min(2000.0, round(self._drag_init_scale * factor, 1))))
            return

        if self._drag_mode == "move" and self._drag_inst_id:
            inst = self._instances_by_id.get(self._drag_inst_id)
            if inst is not None:
                hit_xy = self._ray_bed_plane_hit(pos.x(), pos.y(), z_plane=inst.pos_z)
                if hit_xy is not None:
                    self.instance_moved.emit(
                        self._drag_inst_id,
                        max(0.0, min(self.bed_x, hit_xy[0] + self._drag_move_offset[0])),
                        max(0.0, min(self.bed_y, hit_xy[1] + self._drag_move_offset[1])),
                    )
            return

        if self._drag_mode == "rotate" and self._drag_inst_id and self._press_mouse is not None:
            tot_dx, tot_dy = pos.x() - self._press_mouse.x(), pos.y() - self._press_mouse.y()
            rx0, ry0, rz0 = self._drag_init_rot
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                rx = ((rx0 + tot_dy * 0.6 + 180.0) % 360.0) - 180.0
                ry = ((ry0 + tot_dx * 0.6 + 180.0) % 360.0) - 180.0
                self.instance_rotated.emit(self._drag_inst_id, round(rx, 1), round(ry, 1), rz0)
            else:
                rz = ((rz0 + tot_dx * 0.6 + 180.0) % 360.0) - 180.0
                self.instance_rotated.emit(self._drag_inst_id, rx0, ry0, round(rz, 1))
            return

        if self._drag_mode == "scale" and self._drag_inst_id and self._press_mouse is not None:
            factor = math.exp(((pos.x() - self._press_mouse.x()) - (pos.y() - self._press_mouse.y())) * 0.0045)
            self.instance_scaled.emit(self._drag_inst_id, max(5.0, min(2000.0, round(self._drag_init_scale * factor, 1))))
            return

        if self._drag_mode == "orbit":
            self.cam_yaw -= dx * 0.45
            self.cam_pitch = max(-85.0, min(88.5, self.cam_pitch + dy * 0.45))
        elif self._drag_mode == "pan":
            yaw, pitch = math.radians(self.cam_yaw), math.radians(self.cam_pitch)
            right = np.array([math.cos(yaw), math.sin(yaw), 0.0], dtype=np.float64)
            up = np.array([-math.sin(yaw) * math.sin(pitch), math.cos(yaw) * math.sin(pitch), math.cos(pitch)], dtype=np.float64)
            self.cam_target = self.cam_target - right * (dx * self.cam_dist * 0.0018) + up * (dy * self.cam_dist * 0.0018)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Complete drag or process empty-space click (switch inactive plate or deselect)."""
        pos = event.position().toPoint()
        was_empty_click = (
            event.button() == Qt.MouseButton.LeftButton
            and self._pressed_on_empty
            and self._press_mouse is not None
            and math.hypot(pos.x() - self._press_mouse.x(), pos.y() - self._press_mouse.y()) <= 5.0
            and self.mode == "editor"
        )
        self._last_mouse = self._press_mouse = self._drag_inst_id = None
        self._drag_mode = self._drag_axis = ""
        self._pressed_on_empty = False

        if was_empty_click:
            hit_plate = self._pick_inactive_instance_plate(pos.x(), pos.y())
            if hit_plate is None:
                hit_plate = self._pick_inactive_plate(pos.x(), pos.y())
            if hit_plate is not None and hit_plate != self._active_plate_idx:
                self.plate_selected.emit(int(hit_plate))
                return
            if self._selected_id is not None:
                self._selected_id = None
                self._selection_box_cpu = np.zeros((0, 6), dtype=np.float32)
                self._dirty_mesh = True
                self._rebuild_gizmo_geometry()
                self._clear_placeable_faces()
                self.selection_cleared.emit()
                self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom orbit camera smoothly on scroll wheel."""
        factor = 0.88 if event.angleDelta().y() > 0 else 1.14
        self.cam_dist = max(12.0, min(1600.0, self.cam_dist * factor))
        self.update()
