"""3D Model & Surface-Normal Cellular Infill preview tab widget."""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import matplotlib

matplotlib.use("QtAgg")

import numpy as np
import trimesh
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from ..infill import Capsule


class Preview3DWidget(QWidget):
    """Interactive 3D Matplotlib preview showing outer mesh, build plate, and cellular wireframe."""

    def __init__(
        self,
        bed_size_provider: Callable[[], Tuple[float, float]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._bed_size_provider = bed_size_provider
        self._mesh: Optional[trimesh.Trimesh] = None
        self._capsules: Optional[List[Capsule]] = None

        vbox = QVBoxLayout(self)

        top = QHBoxLayout()
        self.chk_show_mesh = QCheckBox("Show Outer Mesh")
        self.chk_show_mesh.setChecked(True)
        self.chk_show_mesh.toggled.connect(self.render)

        self.chk_show_infill = QCheckBox("Show 3D Cellular Wireframe")
        self.chk_show_infill.setChecked(True)
        self.chk_show_infill.toggled.connect(self.render)

        self.chk_show_bed = QCheckBox("Show Build Plate")
        self.chk_show_bed.setChecked(True)
        self.chk_show_bed.toggled.connect(self.render)

        top.addWidget(self.chk_show_mesh)
        top.addWidget(self.chk_show_infill)
        top.addWidget(self.chk_show_bed)
        top.addStretch(1)
        vbox.addLayout(top)

        self.fig = Figure(facecolor="#16171a")
        self.ax = self.fig.add_subplot(111, projection="3d")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        vbox.addWidget(self.toolbar)
        vbox.addWidget(self.canvas, stretch=1)
        self._style_axes()

    def set_scene(
        self,
        mesh: Optional[trimesh.Trimesh],
        capsules: Optional[List[Capsule]] = None,
    ) -> None:
        self._mesh = mesh
        self._capsules = capsules
        self.render()

    def _style_axes(self) -> None:
        self.ax.set_facecolor("#111215")
        self.ax.tick_params(colors="#aeb4be", labelsize=8)
        self.ax.set_xlabel("X (mm)", color="#cfd5df")
        self.ax.set_ylabel("Y (mm)", color="#cfd5df")
        self.ax.set_zlabel("Z (mm)", color="#cfd5df")
        for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
            axis.pane.set_facecolor((0.08, 0.09, 0.11, 0.85))
            axis.pane.set_edgecolor("#2c2f36")

    def render(self) -> None:
        self.ax.clear()
        self._style_axes()
        if self._mesh is None:
            self.canvas.draw_idle()
            return

        mesh = self._mesh
        bounds = mesh.bounds

        if self.chk_show_mesh.isChecked():
            faces = mesh.faces
            step = max(1, len(faces) // 2500)
            tris = mesh.vertices[faces[::step]]
            poly = Poly3DCollection(
                tris,
                facecolor=(0.45, 0.52, 0.62, 0.18),
                edgecolor=(0.55, 0.62, 0.72, 0.25),
                linewidths=0.35,
            )
            self.ax.add_collection3d(poly)

        if self.chk_show_infill.isChecked() and self._capsules:
            step_c = max(1, len(self._capsules) // 4500)
            segs = [[c.a, c.b] for c in self._capsules[::step_c]]
            lc = Line3DCollection(segs, colors="#ff5722", linewidths=1.1, alpha=0.88)
            self.ax.add_collection3d(lc)

        if self.chk_show_bed.isChecked():
            bx, by = self._bed_size_provider()
            rect = [[[0, 0, 0], [bx, 0, 0], [bx, by, 0], [0, by, 0], [0, 0, 0]]]
            self.ax.add_collection3d(Line3DCollection(rect, colors="#3d4350", linewidths=1.0))

        center = 0.5 * (bounds[0] + bounds[1])
        half_span = max(float(np.max(mesh.extents)) * 0.65, 10.0)
        self.ax.set_xlim(center[0] - half_span, center[0] + half_span)
        self.ax.set_ylim(center[1] - half_span, center[1] + half_span)
        self.ax.set_zlim(0.0, max(bounds[1, 2] * 1.15, half_span * 1.5))
        self.canvas.draw_idle()
