"""2D Layer & Fluid Basin Inspector tab widget."""

from __future__ import annotations

from typing import Optional

import matplotlib

matplotlib.use("QtAgg")

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..pipeline import SliceResult


class LayerInspector2D(QWidget):
    """Interactive 2D slice viewer showing shells, inside perimeters, resin voids, dry supports, and TSP dosing."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._slice_result: Optional[SliceResult] = None

        vbox = QVBoxLayout(self)

        self.lbl_layer_info = QLabel("Slice a model to inspect 2D layers, resin voids, and dosing targets.")
        self.lbl_layer_info.setStyleSheet(
            "background-color: #111215; padding: 6px 10px; border-radius: 4px; font-weight: 600;"
        )
        vbox.addWidget(self.lbl_layer_info)

        self.fig = Figure(facecolor="#16171a")
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        vbox.addWidget(self.toolbar)
        vbox.addWidget(self.canvas, stretch=1)

        ctrl = QHBoxLayout()
        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedWidth(34)
        self.btn_prev.clicked.connect(lambda: self._step_layer(-1))

        self.slider_layer = QSlider(Qt.Orientation.Horizontal)
        self.slider_layer.setEnabled(False)
        self.slider_layer.valueChanged.connect(self._on_slider_changed)

        self.sp_layer_idx = QSpinBox()
        self.sp_layer_idx.setEnabled(False)
        self.sp_layer_idx.valueChanged.connect(self._on_spin_changed)

        self.btn_next = QPushButton(">")
        self.btn_next.setFixedWidth(34)
        self.btn_next.clicked.connect(lambda: self._step_layer(1))

        self.btn_next_cure = QPushButton("Next Cure Layer")
        self.btn_next_cure.setEnabled(False)
        self.btn_next_cure.clicked.connect(self._jump_next_cure)

        ctrl.addWidget(QLabel("Layer:"))
        ctrl.addWidget(self.btn_prev)
        ctrl.addWidget(self.slider_layer, stretch=1)
        ctrl.addWidget(self.sp_layer_idx)
        ctrl.addWidget(self.btn_next)
        ctrl.addWidget(self.btn_next_cure)
        vbox.addLayout(ctrl)

        self._style_axes()

    def set_result(self, res: SliceResult) -> None:
        self._slice_result = res
        n = len(res.layers)
        self.slider_layer.blockSignals(True)
        self.sp_layer_idx.blockSignals(True)
        self.slider_layer.setRange(1, n)
        self.sp_layer_idx.setRange(1, n)
        self.slider_layer.setEnabled(True)
        self.sp_layer_idx.setEnabled(True)
        self.btn_next_cure.setEnabled(len(res.schedule) > 0)

        initial_layer = res.schedule[0].end_layer if res.schedule else max(1, n // 2)
        self.slider_layer.setValue(initial_layer)
        self.sp_layer_idx.setValue(initial_layer)
        self.slider_layer.blockSignals(False)
        self.sp_layer_idx.blockSignals(False)
        self._render_layer(initial_layer)

    def select_layer(self, layer_num: int) -> None:
        if self._slice_result is None:
            return
        clamped = max(1, min(len(self._slice_result.layers), layer_num))
        self.slider_layer.setValue(clamped)

    def _style_axes(self) -> None:
        self.ax.set_facecolor("#111215")
        self.ax.tick_params(colors="#b0b6c2")
        for spine in self.ax.spines.values():
            spine.set_color("#32363e")
        self.ax.set_xlabel("X (mm)", color="#cfd5df")
        self.ax.set_ylabel("Y (mm)", color="#cfd5df")

    def _step_layer(self, delta: int) -> None:
        if self._slice_result is None:
            return
        self.select_layer(self.slider_layer.value() + delta)

    def _on_slider_changed(self, layer_num: int) -> None:
        self.sp_layer_idx.blockSignals(True)
        self.sp_layer_idx.setValue(layer_num)
        self.sp_layer_idx.blockSignals(False)
        self._render_layer(layer_num)

    def _on_spin_changed(self, layer_num: int) -> None:
        self.slider_layer.blockSignals(True)
        self.slider_layer.setValue(layer_num)
        self.slider_layer.blockSignals(False)
        self._render_layer(layer_num)

    def _jump_next_cure(self) -> None:
        if self._slice_result is None or not self._slice_result.schedule:
            return
        cur = self.slider_layer.value()
        cure_layers = [c.end_layer for c in self._slice_result.schedule]
        for cl in cure_layers:
            if cl > cur:
                self.slider_layer.setValue(cl)
                return
        self.slider_layer.setValue(cure_layers[0])

    def _render_layer(self, layer_num: int) -> None:
        res = self._slice_result
        if res is None:
            return
        ly = res.layers[layer_num - 1]
        xs, ys = res.xs, res.ys

        # Compose RGBA image transposed so rows=Y and cols=X
        nx, ny = ly.shell_mask.shape
        img = np.zeros((ny, nx, 4), dtype=np.float32)
        img[:, :, 3] = 1.0
        img[:, :, :3] = (0.07, 0.075, 0.085)  # Background / empty air cells

        img[ly.void_mask.T] = (0.05, 0.55, 0.78, 1.0)         # Continuous photopolymer cavity
        img[ly.dry_support_mask.T] = (0.26, 0.72, 0.40, 1.0)  # Dry hollow-core support
        img[ly.infill_mask.T] = (0.96, 0.77, 0.09, 1.0)       # Inside perimeters & cell bridge caps (yellow)
        img[ly.shell_mask.T] = (0.86, 0.88, 0.92, 1.0)        # Watertight FFF shells & solid skins

        self.ax.clear()
        self._style_axes()
        self.ax.imshow(
            img,
            origin="lower",
            extent=[float(xs[0]), float(xs[-1]), float(ys[0]), float(ys[-1])],
            interpolation="nearest",
        )
        self.ax.set_aspect("equal", adjustable="box")

        cycle = res.cycle_by_layer.get(layer_num)
        dosing_pts = res.dosing_by_layer.get(layer_num, [])

        if cycle is not None:
            tot_mm3 = sum(d.volume_mm3 for d in dosing_pts)
            self.lbl_layer_info.setText(
                f"Layer {layer_num}/{len(res.layers)} (Z={ly.z:.2f} mm)  |  "
                f"UV CURE CYCLE: Layers {cycle.start_layer}-{cycle.end_layer} "
                f"({cycle.slab_thickness_mm:.2f} mm, {cycle.trigger})  |  "
                f"{len(dosing_pts)} needle targets ({tot_mm3:.1f} mm3)"
            )
            self.lbl_layer_info.setStyleSheet(
                "background-color: rgba(0, 212, 255, 0.14); color: #7ee8ff; "
                "border: 1px solid rgba(0, 212, 255, 0.55); "
                "padding: 6px 10px; border-radius: 5px; font-weight: 600;"
            )
            if dosing_pts:
                dx = [p.x for p in dosing_pts]
                dy = [p.y for p in dosing_pts]
                self.ax.plot(dx, dy, color="#00d4ff", linewidth=1.0, linestyle="--", alpha=0.85)
                self.ax.scatter(dx, dy, color="#f5c518", edgecolors="#141518", s=48, zorder=5)
                for idx_p, pt in enumerate(dosing_pts[:25]):
                    self.ax.annotate(
                        f"#{idx_p + 1}\n{pt.volume_mm3:.1f}",
                        (pt.x, pt.y),
                        textcoords="offset points",
                        xytext=(5, 5),
                        color="#f5c518",
                        fontsize=7,
                        bbox=dict(boxstyle="round,pad=0.15", fc="#16171a", ec="#f5c518", alpha=0.85),
                    )
        else:
            self.lbl_layer_info.setText(
                f"Layer {layer_num}/{len(res.layers)} (Z={ly.z:.2f} mm)  |  "
                f"FFF Print Layer (No resin injection on this layer)"
            )
            self.lbl_layer_info.setStyleSheet(
                "background-color: #131418; color: #eceff4; border: 1px solid #313542; "
                "padding: 6px 10px; border-radius: 5px; font-weight: 600;"
            )

        legend_items = [
            Patch(facecolor=(0.86, 0.88, 0.92), label="Watertight Shell"),
            Patch(facecolor=(0.96, 0.77, 0.09), label="Inside Perimeters / Caps"),
            Patch(facecolor=(0.05, 0.55, 0.78), label="Photopolymer Channel"),
            Patch(facecolor=(0.26, 0.72, 0.40), label="Dry Core Support"),
        ]
        self.ax.legend(
            handles=legend_items,
            loc="upper right",
            facecolor="#17181c",
            edgecolor="#313542",
            labelcolor="#eceff4",
            fontsize=8,
        )
        self.canvas.draw_idle()
