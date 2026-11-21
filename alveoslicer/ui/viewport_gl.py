"""GPU-accelerated PySide6 OpenGL 3D viewport for PrusaSlicer-style model viewing & G-code previewing."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import trimesh
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMatrix4x4, QMouseEvent, QOpenGLFunctions, QVector2D, QVector3D, QWheelEvent
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from ..gcode_parser import (
    DOSING_COLOR,
    FEATURE_COLORS,
    FEATURE_TYPES,
    TRAVEL_COLOR,
    GCodePreviewData,
)
from ..infill import Capsule

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
    float spec = pow(max(dot(n, half_v), 0.0), 28.0) * 0.22;
    float shade = 0.30 + 0.16 * hemi + 0.52 * d1 + 0.22 * d2;
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


def _build_extrusion_beads(
    p0: np.ndarray,
    p1: np.ndarray,
    widths: np.ndarray,
    heights: np.ndarray,
    type_ids: np.ndarray,
) -> np.ndarray:
    """
    Vectorized 3D volumetric prism builder for G-code extrusion paths.
    Produces shape (N, 18, 9) float32: 6 triangles (top + left + right walls) with (x,y,z, nx,ny,nz, r,g,b).
    """
    n = len(p0)
    if n == 0:
        return np.zeros((0, 18, 9), dtype=np.float32)

    palette = np.array([FEATURE_COLORS[t] for t in FEATURE_TYPES], dtype=np.float32)
    colors = palette[np.clip(type_ids, 0, len(palette) - 1)]  # (N, 3)

    d = p1 - p0
    len_xy = np.hypot(d[:, 0], d[:, 1])
    safe_len = np.where(len_xy > 1e-5, len_xy, 1.0)
    nx = np.where(len_xy > 1e-5, -d[:, 1] / safe_len, 1.0).astype(np.float32)
    ny = np.where(len_xy > 1e-5, d[:, 0] / safe_len, 0.0).astype(np.float32)

    hw = (0.5 * widths).astype(np.float32)
    ht = np.maximum(0.05, heights).astype(np.float32)

    off = np.stack([nx * hw, ny * hw, np.zeros(n, dtype=np.float32)], axis=1)
    dz = np.stack([np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32), ht], axis=1)

    # Top quad corners
    t0l = p0 + off
    t0r = p0 - off
    t1l = p1 + off
    t1r = p1 - off
    # Bottom quad corners
    b0l = t0l - dz
    b0r = t0r - dz
    b1l = t1l - dz
    b1r = t1r - dz

    n_top = np.broadcast_to(np.array([[0.0, 0.0, 1.0]], dtype=np.float32), (n, 3))
    n_left = np.stack([nx, ny, np.full(n, 0.25, dtype=np.float32)], axis=1)
    n_right = np.stack([-nx, -ny, np.full(n, 0.25, dtype=np.float32)], axis=1)

    out = np.empty((n, 18, 9), dtype=np.float32)

    def _put_tri(slot: int, a: np.ndarray, b: np.ndarray, c: np.ndarray, norm: np.ndarray) -> None:
        out[:, slot, 0:3] = a
        out[:, slot + 1, 0:3] = b
        out[:, slot + 2, 0:3] = c
        out[:, slot : slot + 3, 3:6] = norm[:, None, :]
        out[:, slot : slot + 3, 6:9] = colors[:, None, :]

    # Top face
    _put_tri(0, t0l, t0r, t1r, n_top)
    _put_tri(3, t0l, t1r, t1l, n_top)
    # Left wall
    _put_tri(6, b0l, t0l, t1l, n_left)
    _put_tri(9, b0l, t1l, b1l, n_left)
    # Right wall
    _put_tri(12, t0r, b0r, b1r, n_right)
    _put_tri(15, t0r, b1r, t1r, n_right)

    return out


class AlveoViewport3D(QOpenGLWidget):
    """PrusaSlicer-style OpenGL 3D Editor and volumetric G-code Toolpath Previewer."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # View mode: "editor" or "preview"
        self.mode: str = "editor"

        # Bed dimensions
        self.bed_x: float = 220.0
        self.bed_y: float = 220.0
        self.bed_z: float = 220.0

        # Orbit camera state
        self.cam_target = np.array([110.0, 110.0, 15.0], dtype=np.float64)
        self.cam_yaw: float = -42.0       # degrees
        self.cam_pitch: float = 34.0      # degrees
        self.cam_dist: float = 260.0      # mm
        self._last_mouse: Optional[QPoint] = None
        self._drag_mode: str = ""

        # Scene options
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

        # CPU geometry caches (pending upload to GPU)
        self._bed_plate_cpu = np.zeros((0, 9), dtype=np.float32)
        self._bed_grid_cpu = np.zeros((0, 6), dtype=np.float32)
        self._mesh_cpu = np.zeros((0, 9), dtype=np.float32)
        self._wireframe_cpu = np.zeros((0, 6), dtype=np.float32)

        self._all_beads_cpu = np.zeros((0, 18, 9), dtype=np.float32)
        self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
        self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
        self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
        self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)

        # Dirty flags for VBO uploads
        self._gl_ready = False
        self._dirty_bed = True
        self._dirty_mesh = True
        self._dirty_wire = True
        self._dirty_preview = True

        # GL resources (created in initializeGL)
        self._gl: Optional[QOpenGLFunctions] = None
        self._lit_prog: Optional[QOpenGLShaderProgram] = None
        self._line_prog: Optional[QOpenGLShaderProgram] = None
        self._vbos: Dict[str, Tuple[QOpenGLBuffer, int]] = {}

        self._rebuild_bed_geometry()

    # -------------------------------------------------------------------------
    # Public Scene API
    # -------------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def set_bed_size(self, bx: float, by: float, bz: float) -> None:
        if abs(bx - self.bed_x) < 1e-3 and abs(by - self.bed_y) < 1e-3 and abs(bz - self.bed_z) < 1e-3:
            return
        self.bed_x, self.bed_y, self.bed_z = float(bx), float(by), float(bz)
        self._rebuild_bed_geometry()
        self.update()

    def set_mesh(self, mesh: Optional[trimesh.Trimesh], capsules: Optional[List[Capsule]] = None) -> None:
        if mesh is None or len(mesh.faces) == 0:
            self._mesh_cpu = np.zeros((0, 9), dtype=np.float32)
        else:
            tris = np.asarray(mesh.vertices[mesh.faces], dtype=np.float32)  # (F, 3, 3)
            # Crisp per-face normals so CAD edges pop cleanly like PrusaSlicer
            e1 = tris[:, 1] - tris[:, 0]
            e2 = tris[:, 2] - tris[:, 0]
            fn = np.cross(e1, e2)
            fl = np.linalg.norm(fn, axis=1, keepdims=True)
            fn = fn / np.maximum(fl, 1e-6)

            n_faces = len(tris)
            buf = np.empty((n_faces, 3, 9), dtype=np.float32)
            buf[:, :, 0:3] = tris
            buf[:, :, 3:6] = fn[:, None, :]
            # Signature AlveoSlicer yellow-gold model material
            buf[:, :, 6:9] = np.array([0.93, 0.76, 0.14], dtype=np.float32)
            self._mesh_cpu = buf.reshape(-1, 9)

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
            # Vivid cyan-teal wireframe contrasting cleanly with yellow mesh
            pts[:, 3:6] = np.array([0.05, 0.85, 0.96], dtype=np.float32)
            self._wireframe_cpu = pts
        self._dirty_wire = True
        self.update()

    def set_gcode_preview(self, data: Optional[GCodePreviewData]) -> None:
        self.preview_data = data
        if data is None or data.num_layers == 0:
            self._all_beads_cpu = np.zeros((0, 18, 9), dtype=np.float32)
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
            self._active_travels_cpu = np.zeros((0, 6), dtype=np.float32)
            self._active_dosing_cpu = np.zeros((0, 6), dtype=np.float32)
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)
            self._dirty_preview = True
            self.update()
            return

        self._all_beads_cpu = _build_extrusion_beads(
            data.ext_p0, data.ext_p1, data.ext_width, data.ext_height, data.ext_type
        )
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

    def fit_to_mesh(self, mesh: Optional[trimesh.Trimesh]) -> None:
        if mesh is None or len(mesh.vertices) == 0:
            self.fit_to_bed()
            return
        bounds = mesh.bounds
        center = 0.5 * (bounds[0] + bounds[1])
        span = max(float(np.max(mesh.extents)), 15.0)
        self.cam_target = center.astype(np.float64)
        self.cam_dist = max(45.0, span * 2.2)
        self.update()

    def fit_to_bed(self) -> None:
        self.cam_target = np.array([self.bed_x * 0.5, self.bed_y * 0.5, 12.0], dtype=np.float64)
        self.cam_dist = max(self.bed_x, self.bed_y) * 1.35
        self.update()

    def set_camera_preset(self, preset: str) -> None:
        if preset == "iso":
            self.cam_yaw, self.cam_pitch = -42.0, 34.0
        elif preset == "top":
            self.cam_yaw, self.cam_pitch = 0.0, 88.5
        elif preset == "front":
            self.cam_yaw, self.cam_pitch = 0.0, 6.0
        elif preset == "right":
            self.cam_yaw, self.cam_pitch = -90.0, 10.0
        self.update()

    # -------------------------------------------------------------------------
    # CPU Buffer Builders
    # -------------------------------------------------------------------------

    def _rebuild_bed_geometry(self) -> None:
        bx, by, bz = self.bed_x, self.bed_y, self.bed_z
        # Shaded dark matte build plate quad at Z = -0.08 mm
        z0 = -0.08
        col = (0.145, 0.155, 0.185)
        n = (0.0, 0.0, 1.0)
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

        lines: List[List[float]] = []

        def _seg(a: Tuple[float, float, float], b: Tuple[float, float, float], c: Tuple[float, float, float]) -> None:
            lines.append([a[0], a[1], a[2], c[0], c[1], c[2]])
            lines.append([b[0], b[1], b[2], c[0], c[1], c[2]])

        minor_c = (0.20, 0.22, 0.27)
        major_c = (0.30, 0.33, 0.40)
        border_c = (0.76, 0.62, 0.12)  # Subtle yellow bed border
        cage_c = (0.23, 0.25, 0.31)
        zg = -0.02

        x = 10.0
        while x < bx - 1e-3:
            c = major_c if (int(round(x)) % 50 == 0) else minor_c
            _seg((x, 0.0, zg), (x, by, zg), c)
            x += 10.0

        y = 10.0
        while y < by - 1e-3:
            c = major_c if (int(round(y)) % 50 == 0) else minor_c
            _seg((0.0, y, zg), (bx, y, zg), c)
            y += 10.0

        # Bed perimeter in subtle yellow
        _seg((0.0, 0.0, 0.0), (bx, 0.0, 0.0), border_c)
        _seg((bx, 0.0, 0.0), (bx, by, 0.0), border_c)
        _seg((bx, by, 0.0), (0.0, by, 0.0), border_c)
        _seg((0.0, by, 0.0), (0.0, 0.0, 0.0), border_c)

        # Subtle build volume vertical corners (40mm guide pillars)
        guide_z = min(bz, 40.0)
        for cx, cy in ((0.0, 0.0), (bx, 0.0), (bx, by), (0.0, by)):
            _seg((cx, cy, 0.0), (cx, cy, guide_z), cage_c)

        # Origin XYZ axes triad
        axis_len = 24.0
        _seg((0.0, 0.0, 0.05), (axis_len, 0.0, 0.05), (0.95, 0.28, 0.28))
        _seg((0.0, 0.0, 0.05), (0.0, axis_len, 0.05), (0.28, 0.88, 0.42))
        _seg((0.0, 0.0, 0.05), (0.0, 0.0, axis_len), (0.28, 0.58, 0.96))

        self._bed_grid_cpu = np.asarray(lines, dtype=np.float32)
        self._dirty_bed = True

    def _rebuild_preview_slice(self) -> None:
        data = self.preview_data
        if data is None or len(self._all_beads_cpu) == 0:
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)
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
            self._active_beads_cpu = self._all_beads_cpu[mask].reshape(-1, 9)
        else:
            self._active_beads_cpu = np.zeros((0, 9), dtype=np.float32)

        # Active nozzle marker at last extruded segment on layer_max
        top_mask = (data.ext_layer == self.layer_max) & (data.ext_move_idx <= self.top_move_limit)
        top_idxs = np.flatnonzero(top_mask)
        if len(top_idxs) > 0:
            tip = data.ext_p1[top_idxs[-1]]
            self._nozzle_cpu = self._build_nozzle_cone(float(tip[0]), float(tip[1]), float(tip[2]))
        else:
            self._nozzle_cpu = np.zeros((0, 9), dtype=np.float32)

        # Travel lines
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

        # Resin dosing targets (vertical needle pins + top crosshair + TSP path on visible cure layers)
        if self.show_dosing and data.dosing_markers:
            # Show markers on the active top layer if it's a cure layer, or all visible layers in range
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
                # Vertical needle shaft
                d_lines.append([m.x, m.y, m.z, *col])
                d_lines.append([m.x, m.y, m.z + pin_h, *col])
                # Crosshair at entry
                d_lines.append([m.x - r, m.y, m.z + 0.25, *col])
                d_lines.append([m.x + r, m.y, m.z + 0.25, *col])
                d_lines.append([m.x, m.y - r, m.z + 0.25, *col])
                d_lines.append([m.x, m.y + r, m.z + 0.25, *col])
                # Connect TSP route on top cure layer
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
        """Build a small brass/yellow inverted cone representing the active printhead nozzle."""
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
        self._dirty_wire = True
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
            self._dirty_bed = False
        if self._dirty_mesh:
            self._upload_vbo("mesh", self._mesh_cpu)
            self._dirty_mesh = False
        if self._dirty_wire:
            self._upload_vbo("wireframe", self._wireframe_cpu)
            self._dirty_wire = False
        if self._dirty_preview:
            self._upload_vbo("beads", self._active_beads_cpu)
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
        view_dir = self.cam_target - eye

        proj = QMatrix4x4()
        proj.perspective(42.0, aspect, max(0.5, self.cam_dist * 0.004), max(2000.0, self.cam_dist * 12.0))

        view = QMatrix4x4()
        view.lookAt(
            QVector3D(float(eye[0]), float(eye[1]), float(eye[2])),
            QVector3D(float(self.cam_target[0]), float(self.cam_target[1]), float(self.cam_target[2])),
            QVector3D(0.0, 0.0, 1.0),
        )
        return proj * view, QVector3D(float(-view_dir[0]), float(-view_dir[1]), float(-view_dir[2]))

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
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glClearColor(0.088, 0.094, 0.112, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        mvp, view_dir = self._compute_mvp_and_eye()

        # 1. Build plate & grid
        self._draw_lit("bed_plate", mvp, view_dir, alpha=1.0)
        self._draw_lines("bed_grid", mvp, alpha=0.95, width=1.1)

        if self.mode == "editor":
            has_wire = self.show_infill_wireframe and self._wireframe_cpu.shape[0] > 0
            if has_wire:
                self._draw_lines("wireframe", mvp, alpha=0.95, width=1.6)
            if self.show_mesh:
                if has_wire:
                    gl.glDepthMask(False)
                    self._draw_lit("mesh", mvp, view_dir, alpha=0.42)
                    gl.glDepthMask(True)
                else:
                    self._draw_lit("mesh", mvp, view_dir, alpha=1.0)
        else:
            # PrusaSlicer-style volumetric G-code preview
            self._draw_lit("beads", mvp, view_dir, alpha=1.0)
            self._draw_lit("nozzle", mvp, view_dir, alpha=0.95)
            if self.show_travels:
                self._draw_lines("travels", mvp, alpha=0.55, width=1.0)
            if self.show_dosing:
                self._draw_lines("dosing", mvp, alpha=0.95, width=1.8)

    # -------------------------------------------------------------------------
    # Interactive CAD Camera Mouse Controls
    # -------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._last_mouse = event.position().toPoint()
        btn = event.button()
        mods = event.modifiers()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or (
            btn == Qt.MouseButton.LeftButton and (mods & Qt.KeyboardModifier.ShiftModifier)
        ):
            self._drag_mode = "pan"
        elif btn == Qt.MouseButton.LeftButton:
            self._drag_mode = "orbit"
        else:
            self._drag_mode = ""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_mouse is None or not self._drag_mode:
            return
        pos = event.position().toPoint()
        dx = pos.x() - self._last_mouse.x()
        dy = pos.y() - self._last_mouse.y()
        self._last_mouse = pos

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
        del event
        self._last_mouse = None
        self._drag_mode = ""

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 0.88 if delta > 0 else 1.14
        self.cam_dist = max(12.0, min(1600.0, self.cam_dist * factor))
        self.update()
