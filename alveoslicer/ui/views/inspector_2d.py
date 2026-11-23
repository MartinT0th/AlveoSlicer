"""2D Layer & Fluid Basin Inspector tab widget.

Renders the rasterized XY cross-section of any sliced layer showing watertight
FFF shells, inside perimeters / bridge caps, continuous photopolymer channels,
dry-core supports, and the TSP-ordered syringe dosing path on cure layers.
"""

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

from ...pipeline import SliceResult
from ..styling.theme import get_palette


class LayerInspector2D(QWidget):
    """Interactive 2D slice viewer showing shells, inside perimeters, resin voids, dry supports, and TSP dosing."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize the Matplotlib canvas, navigation toolbar, and layer scrubber controls."""
        super().__init__(parent)
        self._slice_result: Optional[SliceResult] = None
        self._pending_layer: Optional[int] = None
        self._resolved_theme: str = "dark"

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        self.lbl_layer_info = QLabel("Slice a model to inspect 2D layers, resin voids, and dosing targets.")
        self.lbl_layer_info.setObjectName("summaryBanner")
        vbox.addWidget(self.lbl_layer_info)

        pal = get_palette(self._resolved_theme)
        self.fig = Figure(facecolor=pal.bg_surface)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        vbox.addWidget(self.toolbar)
        vbox.addWidget(self.canvas, stretch=1)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        self.btn_prev = QPushButton("<")
        self.btn_prev.setObjectName("iconSmallBtn")
        self.btn_prev.setFixedWidth(34)
        self.btn_prev.clicked.connect(lambda: self._step_layer(-1))

        self.slider_layer = QSlider(Qt.Orientation.Horizontal)
        self.slider_layer.setEnabled(False)
        self.slider_layer.valueChanged.connect(self._on_slider_changed)

        self.sp_layer_idx = QSpinBox()
        self.sp_layer_idx.setEnabled(False)
        self.sp_layer_idx.setFixedWidth(78)
        self.sp_layer_idx.valueChanged.connect(self._on_spin_changed)

        self.btn_next = QPushButton(">")
        self.btn_next.setObjectName("iconSmallBtn")
        self.btn_next.setFixedWidth(34)
        self.btn_next.clicked.connect(lambda: self._step_layer(1))

        self.btn_next_cure = QPushButton("Next Cure Layer")
        self.btn_next_cure.setEnabled(False)
        self.btn_next_cure.clicked.connect(self._jump_next_cure)

        lbl_l = QLabel("Layer:")
        lbl_l.setObjectName("mutedLabel")
        ctrl.addWidget(lbl_l)
        ctrl.addWidget(self.btn_prev)
        ctrl.addWidget(self.slider_layer, stretch=1)
        ctrl.addWidget(self.sp_layer_idx)
        ctrl.addWidget(self.btn_next)
        ctrl.addWidget(self.btn_next_cure)
        vbox.addLayout(ctrl)

        self._style_axes()

    def apply_theme(self, resolved_mode: str) -> None:
        """Apply Dark or Light Optical Glass Lab styling to the Matplotlib canvas and banner."""
        self._resolved_theme = "light" if resolved_mode == "light" else "dark"
        pal = get_palette(self._resolved_theme)
        self.fig.set_facecolor(pal.bg_surface)
        self._style_axes()
        if self._slice_result is not None:
            self._render_layer(self.slider_layer.value())
        else:
            self.canvas.draw_idle()

    def set_result(self, res: SliceResult) -> None:
        """Bind a new ``SliceResult`` and render its first cure layer (or middle layer in FFF-only mode)."""
        self._slice_result = res
        n = len(res.layers)
        for w in (self.slider_layer, self.sp_layer_idx):
            w.blockSignals(True)
            w.setRange(1, n)
            w.setEnabled(True)
        self.btn_next_cure.setEnabled(len(res.schedule) > 0)

        initial_layer = res.schedule[0].end_layer if res.schedule else max(1, n // 2)
        self.slider_layer.setValue(initial_layer)
        self.sp_layer_idx.setValue(initial_layer)
        self.slider_layer.blockSignals(False)
        self.sp_layer_idx.blockSignals(False)
        self._render_layer(initial_layer)

    def select_layer(self, layer_num: int) -> None:
        """Jump the 2D inspector to 1-indexed ``layer_num``."""
        if self._slice_result is None:
            return
        self.slider_layer.setValue(max(1, min(len(self._slice_result.layers), layer_num)))

    def _style_axes(self) -> None:
        """Apply theme-aware CAD styling to the Matplotlib axes."""
        pal = get_palette(self._resolved_theme)
        self.fig.set_facecolor(pal.bg_surface)
        self.ax.set_facecolor(pal.bg_input)
        self.ax.tick_params(colors=pal.text_muted, labelsize=8.5)
        for spine in self.ax.spines.values():
            spine.set_color(pal.border_strong)
        self.ax.set_xlabel("X (mm)", color=pal.text_secondary, fontsize=9)
        self.ax.set_ylabel("Y (mm)", color=pal.text_secondary, fontsize=9)

    def _set_banner_role(self, object_name: str) -> None:
        if self.lbl_layer_info.objectName() != object_name:
            self.lbl_layer_info.setObjectName(object_name)
            self.lbl_layer_info.style().unpolish(self.lbl_layer_info)
            self.lbl_layer_info.style().polish(self.lbl_layer_info)

    def _step_layer(self, delta: int) -> None:
        """Advance or rewind the active layer by ``delta``."""
        if self._slice_result is not None:
            self.select_layer(self.slider_layer.value() + delta)

    def _on_slider_changed(self, layer_num: int) -> None:
        """Sync the spinbox and redraw when the slider moves."""
        self.sp_layer_idx.blockSignals(True)
        self.sp_layer_idx.setValue(layer_num)
        self.sp_layer_idx.blockSignals(False)
        self._render_layer(layer_num)

    def _on_spin_changed(self, layer_num: int) -> None:
        """Sync the slider and redraw when the spinbox changes."""
        self.slider_layer.blockSignals(True)
        self.slider_layer.setValue(layer_num)
        self.slider_layer.blockSignals(False)
        self._render_layer(layer_num)

    def _jump_next_cure(self) -> None:
        """Cycle forward to the next scheduled UV cure layer."""
        if self._slice_result is None or not self._slice_result.schedule:
            return
        cur = self.slider_layer.value()
        cure_layers = [c.end_layer for c in self._slice_result.schedule]
        self.slider_layer.setValue(next((cl for cl in cure_layers if cl > cur), cure_layers[0]))

    def showEvent(self, event) -> None:
        """Render any deferred layer snapshot once the 2D Basin Inspector becomes visible."""
        super().showEvent(event)
        if self._pending_layer is not None:
            layer_num = self._pending_layer
            self._pending_layer = None
            self._render_layer(layer_num)

    def _render_layer(self, layer_num: int) -> None:
        """Composite RGBA masks and dosing overlays for ``layer_num`` onto the canvas."""
        res = self._slice_result
        if res is None:
            return
        if not self.isVisible():
            self._pending_layer = layer_num
            return
        self._pending_layer = None
        ly = res.layers[layer_num - 1]
        xs, ys = res.xs, res.ys
        pal = get_palette(self._resolved_theme)
        is_light = pal.mode == "light"

        # Compose RGBA image transposed so rows=Y and cols=X
        nx, ny = ly.shell_mask.shape
        img = np.zeros((ny, nx, 4), dtype=np.float32)
        img[:, :, 3] = 1.0
        bg_rgb = (0.945, 0.960, 0.975) if is_light else (0.043, 0.059, 0.078)
        shell_rgb = (0.18, 0.24, 0.32) if is_light else (0.86, 0.90, 0.95)
        infill_rgb = (0.86, 0.62, 0.08) if is_light else (0.961, 0.722, 0.180)
        void_rgb = (0.31, 0.34, 0.88) if is_light else (0.42, 0.48, 0.96)
        sup_rgb = (0.16, 0.62, 0.38) if is_light else (0.24, 0.76, 0.48)

        img[:, :, :3] = bg_rgb
        img[ly.void_mask.T] = (*void_rgb, 1.0)            # Continuous photopolymer cavity
        img[ly.dry_support_mask.T] = (*sup_rgb, 1.0)      # Dry hollow-core support
        img[ly.infill_mask.T] = (*infill_rgb, 1.0)        # Inside perimeters & bridge caps
        img[ly.shell_mask.T] = (*shell_rgb, 1.0)          # Watertight FFF shells & skins

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
            self._set_banner_role("uvBanner")
            if dosing_pts:
                dx = [p.x for p in dosing_pts]
                dy = [p.y for p in dosing_pts]
                self.ax.plot(dx, dy, color=pal.uv_accent, linewidth=1.1, linestyle="--", alpha=0.9)
                self.ax.scatter(dx, dy, color=pal.accent, edgecolors=pal.bg_bar, s=48, zorder=5)
                for idx_p, pt in enumerate(dosing_pts[:25]):
                    self.ax.annotate(
                        f"#{idx_p + 1}\n{pt.volume_mm3:.1f}",
                        (pt.x, pt.y),
                        textcoords="offset points",
                        xytext=(5, 5),
                        color=pal.text_primary,
                        fontsize=7,
                        bbox=dict(
                            boxstyle="round,pad=0.18",
                            fc=pal.bg_surface,
                            ec=pal.accent,
                            alpha=0.92,
                        ),
                    )
        else:
            self.lbl_layer_info.setText(
                f"Layer {layer_num}/{len(res.layers)} (Z={ly.z:.2f} mm)  |  "
                f"FFF Print Layer (No resin injection on this layer)"
            )
            self._set_banner_role("summaryBanner")

        legend_items = [
            Patch(facecolor=shell_rgb, label="Watertight Shell"),
            Patch(facecolor=infill_rgb, label="Inside Perimeters / Caps"),
            Patch(facecolor=void_rgb, label="Photopolymer Channel"),
            Patch(facecolor=sup_rgb, label="Dry Core Support"),
        ]
        self.ax.legend(
            handles=legend_items,
            loc="upper right",
            facecolor=pal.bg_surface,
            edgecolor=pal.border_strong,
            labelcolor=pal.text_primary,
            fontsize=8,
        )
        self.canvas.draw_idle()
