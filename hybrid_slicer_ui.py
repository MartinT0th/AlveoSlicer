#!/usr/bin/env python3
"""
Desktop GUI for the Hybrid FFF + In-Situ UV Photopolymer Slicer.
Place in the same directory as `hybrid_slicer.py` and run:
    python hybrid_slicer_ui.py
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("QtAgg")

import numpy as np
import trimesh
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import hybrid_slicer as hs


# =============================================================================
# Dark Theme Stylesheet (High-Contrast CAD / Slicer Styling)
# =============================================================================

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #16171a;
    color: #e4e6eb;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}
QTabWidget::pane {
    border: 1px solid #2c2f36;
    background-color: #1c1e22;
    border-radius: 4px;
}
QTabBar::tab {
    background-color: #16171a;
    color: #9aa0aa;
    padding: 7px 12px;
    border: 1px solid #2c2f36;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1c1e22;
    color: #ffffff;
    border-top: 2px solid #ff5722;
}
QGroupBox {
    border: 1px solid #2c2f36;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
    color: #ff7043;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
    background-color: #111215;
    border: 1px solid #32363e;
    border-radius: 4px;
    padding: 4px 6px;
    color: #f0f2f5;
}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {
    border: 1px solid #ff5722;
}
QPushButton {
    background-color: #272a31;
    border: 1px solid #3a3f4a;
    border-radius: 4px;
    padding: 6px 12px;
    color: #eef0f4;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #323640;
    border-color: #ff7043;
}
QPushButton:disabled {
    background-color: #1c1e22;
    color: #5c626e;
    border-color: #262930;
}
QPushButton#primaryBtn {
    background-color: #e64a19;
    border: 1px solid #ff7043;
    color: #ffffff;
    padding: 8px 14px;
    font-size: 13px;
}
QPushButton#primaryBtn:hover {
    background-color: #ff5722;
}
QProgressBar {
    border: 1px solid #2c2f36;
    border-radius: 4px;
    background-color: #111215;
    text-align: center;
    color: #ffffff;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #ff5722;
    border-radius: 3px;
}
QTableWidget {
    background-color: #111215;
    alternate-background-color: #181a1f;
    gridline-color: #262930;
    border: 1px solid #2c2f36;
}
QHeaderView::section {
    background-color: #22252b;
    color: #d8dce3;
    padding: 5px;
    border: 1px solid #2c2f36;
    font-weight: 600;
}
QPlainTextEdit {
    background-color: #111215;
    border: 1px solid #2c2f36;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 11px;
    color: #cfd5df;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2c2f36;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ff5722;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
"""


# =============================================================================
# Background Slicing Worker (QThread)
# =============================================================================

@dataclass
class SliceResult:
    mesh: trimesh.Trimesh
    capsules: List[hs.Capsule]
    layers: List[hs.SlicedLayer]
    xs: np.ndarray
    ys: np.ndarray
    schedule: List[hs.CureCycle]
    cycle_by_layer: Dict[int, hs.CureCycle]
    dosing_by_layer: Dict[int, List[hs.DosingPoint]]
    gcode_text: str
    total_resin_ml: float
    gcode_line_count: int


class SliceWorker(QThread):
    progress = Signal(int, str)
    log_msg = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, mesh: trimesh.Trimesh, cfg: hs.SlicerConfig):
        super().__init__()
        self.mesh = mesh.copy()
        self.cfg = cfg

    def run(self) -> None:
        try:
            self.progress.emit(5, "Stage 1: Building 3D SDF & Medial-Axis Field...")
            self.log_msg.emit(
                f"[Stage 1] Building 3D SDF (voxel_pitch={self.cfg.voxel_pitch_mm} mm)..."
            )
            field = hs.VolumetricField(self.mesh, pitch=self.cfg.voxel_pitch_mm)

            self.progress.emit(22, "Stage 2-3: Synthesizing 3D Surface-Normal Dendritic Infill...")
            self.log_msg.emit(
                f"[Stage 2-3] Generating 3D normal infill "
                f"(pattern={self.cfg.infill.pattern}, mode={self.cfg.infill.mode})..."
            )
            capsules = hs.generate_normal_dendritic_infill(field, self.cfg)
            self.log_msg.emit(f"            Synthesized {len(capsules)} 3D normal/cross-tie capsules.")

            self.progress.emit(42, "Stage 3: Planar XY Slicing & Mask Classification...")
            self.log_msg.emit(
                f"[Stage 3] Slicing planar XY layers (h={self.cfg.fff.layer_height} mm, "
                f"raster={self.cfg.slice_raster_pitch_mm} mm)..."
            )
            layers, xs, ys = hs.slice_model_to_layers(self.mesh, field, capsules, self.cfg)
            self.log_msg.emit(f"          Generated {len(layers)} slices.")

            self.progress.emit(65, "Stage 4: Solving Global Dynamic Programming Cure Schedule...")
            self.log_msg.emit(
                f"[Stage 4] Solving DP cure schedule "
                f"([{self.cfg.resin.x_min_layers}, {self.cfg.resin.x_max_layers}] layers)..."
            )
            schedule = hs.compute_optimal_cure_schedule(layers, self.cfg)
            cycle_by_layer = {c.end_layer: c for c in schedule}
            self.log_msg.emit(f"          Scheduled {len(schedule)} inject + UV cure cycles.")

            self.progress.emit(75, "Stage 5-6: Generating FFF Toolpaths, Fluid Basins & G-Code...")
            hal: hs.GCodeHAL = (
                hs.CoreXYToolchangerHAL(self.cfg)
                if self.cfg.printer.printer_type == "corexy_toolchanger"
                else hs.BedslingerPrototypeHAL(self.cfg)
            )
            hal.start_gcode()

            dosing_by_layer: Dict[int, List[hs.DosingPoint]] = {}
            total_resin_mm3 = 0.0
            n_layers = max(1, len(layers))

            for idx, ly in enumerate(layers):
                fff_paths = hs.generate_fff_paths_for_layer(ly, xs, ys, self.cfg)
                hal.emit_fff_layer(ly.layer_idx, ly.z, fff_paths)

                if ly.layer_idx in cycle_by_layer:
                    cycle = cycle_by_layer[ly.layer_idx]
                    dosing_pts, bbox_xy = hs.plan_cycle_dosing(layers, cycle, xs, ys, self.cfg)
                    dosing_by_layer[ly.layer_idx] = dosing_pts
                    if dosing_pts:
                        vol = sum(d.volume_mm3 for d in dosing_pts)
                        total_resin_mm3 += vol
                        hal.emit_resin_and_uv_cycle(cycle, ly.z, dosing_pts, bbox_xy)

                if idx % max(1, n_layers // 15) == 0:
                    pct = 75 + int(23 * (idx + 1) / n_layers)
                    self.progress.emit(pct, f"Stage 5-6: Layer {ly.layer_idx}/{n_layers}...")

            hal.end_gcode()
            gcode_text = "\n".join(hal.lines) + "\n"
            total_ml = total_resin_mm3 / 1000.0

            self.log_msg.emit(
                f"[Done] Generated {len(hal.lines)} G-code lines | "
                f"Total photopolymer: {total_ml:.2f} mL across {len(schedule)} cure cycles."
            )
            self.progress.emit(100, "Slicing complete.")

            res = SliceResult(
                mesh=self.mesh,
                capsules=capsules,
                layers=layers,
                xs=xs,
                ys=ys,
                schedule=schedule,
                cycle_by_layer=cycle_by_layer,
                dosing_by_layer=dosing_by_layer,
                gcode_text=gcode_text,
                total_resin_ml=total_ml,
                gcode_line_count=len(hal.lines),
            )
            self.finished_ok.emit(res)
        except Exception:
            self.failed.emit(traceback.format_exc())


# =============================================================================
# Helper Widget Builders
# =============================================================================

def _make_dspin(
    val: float, lo: float, hi: float, step: float = 0.1, decimals: int = 2, suffix: str = ""
) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setSingleStep(step)
    sb.setDecimals(decimals)
    sb.setValue(val)
    if suffix:
        sb.setSuffix(f" {suffix}")
    return sb


def _make_spin(val: int, lo: int, hi: int, step: int = 1, suffix: str = "") -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(lo, hi)
    sb.setSingleStep(step)
    sb.setValue(val)
    if suffix:
        sb.setSuffix(f" {suffix}")
    return sb


def _wrap_scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(widget)
    return area


# =============================================================================
# Main Window
# =============================================================================

class HybridSlicerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hybrid FFF + In-Situ UV Photopolymer Slicer")
        self.resize(1380, 860)

        self.cfg = hs.SlicerConfig()
        self.current_mesh: Optional[trimesh.Trimesh] = None
        self.current_model_name: str = "None"
        self.slice_result: Optional[SliceResult] = None
        self.worker: Optional[SliceWorker] = None

        self._build_ui()
        self._load_config_into_widgets(self.cfg)
        self._on_mode_or_printer_changed()

    # -------------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left Panel: Model + Settings + Actions
        left_panel = QWidget()
        left_panel.setMinimumWidth(370)
        left_panel.setMaximumWidth(470)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        left_layout.addWidget(self._build_model_box())

        self.settings_tabs = QTabWidget()
        self.settings_tabs.addTab(self._build_fff_tab(), "FFF")
        self.settings_tabs.addTab(self._build_infill_tab(), "3D Infill")
        self.settings_tabs.addTab(self._build_resin_tab(), "Resin & DP")
        self.settings_tabs.addTab(self._build_uv_printer_tab(), "UV & Printer")
        self.settings_tabs.addTab(self._build_engine_tab(), "Engine")
        left_layout.addWidget(self.settings_tabs, stretch=1)

        left_layout.addWidget(self._build_action_box())
        splitter.addWidget(left_panel)

        # Right Panel: Viewports + Schedule + Logs
        self.view_tabs = QTabWidget()
        self.view_tabs.addTab(self._build_2d_inspector_tab(), "2D Layer & Basins")
        self.view_tabs.addTab(self._build_3d_preview_tab(), "3D Model & Normal Infill")
        self.view_tabs.addTab(self._build_schedule_tab(), "DP Cure Schedule")
        self.view_tabs.addTab(self._build_console_tab(), "Log & G-Code")
        splitter.addWidget(self.view_tabs)

        splitter.setSizes([410, 950])

    def _build_model_box(self) -> QGroupBox:
        box = QGroupBox("Model & Profile")
        vbox = QVBoxLayout(box)

        self.lbl_model = QLabel("Model: None loaded")
        self.lbl_model.setWordWrap(True)
        vbox.addWidget(self.lbl_model)

        row1 = QHBoxLayout()
        btn_open = QPushButton("Open Model...")
        btn_open.clicked.connect(self._open_model_dialog)
        btn_sphere = QPushButton("Test Sphere (30 mm)")
        btn_sphere.clicked.connect(self._load_test_sphere)
        row1.addWidget(btn_open)
        row1.addWidget(btn_sphere)
        vbox.addLayout(row1)

        row2 = QHBoxLayout()
        btn_load_cfg = QPushButton("Load Config JSON")
        btn_load_cfg.clicked.connect(self._load_config_dialog)
        btn_save_cfg = QPushButton("Save Config JSON")
        btn_save_cfg.clicked.connect(self._save_config_dialog)
        row2.addWidget(btn_load_cfg)
        row2.addWidget(btn_save_cfg)
        vbox.addLayout(row2)
        return box

    def _build_fff_tab(self) -> QScrollArea:
        w = QWidget()
        form = QFormLayout(w)

        self.sp_layer_h = _make_dspin(0.25, 0.05, 1.0, 0.05, 2, "mm")
        self.sp_nozzle = _make_dspin(0.40, 0.15, 1.5, 0.05, 2, "mm")
        self.sp_line_w = _make_dspin(0.45, 0.20, 2.0, 0.05, 2, "mm")
        self.sp_fil_dia = _make_dspin(1.75, 1.0, 3.0, 0.05, 2, "mm")
        self.sp_flow = _make_dspin(1.0, 0.5, 1.5, 0.02, 2)
        self.sp_perims = _make_spin(3, 1, 10)
        self.sp_bot_layers = _make_spin(3, 1, 20)
        self.sp_top_layers = _make_spin(3, 1, 20)
        self.sp_print_spd = _make_dspin(45.0, 5.0, 300.0, 5.0, 1, "mm/s")
        self.sp_travel_spd = _make_dspin(150.0, 20.0, 500.0, 10.0, 1, "mm/s")
        self.sp_zhop = _make_dspin(0.6, 0.0, 5.0, 0.1, 2, "mm")
        self.sp_retract = _make_dspin(1.2, 0.0, 10.0, 0.2, 2, "mm")
        self.sp_retract_spd = _make_dspin(35.0, 5.0, 120.0, 5.0, 1, "mm/s")
        self.sp_nozzle_t = _make_spin(210, 0, 350, 5, "C")
        self.sp_bed_t = _make_spin(60, 0, 130, 5, "C")
        self.sp_fan = _make_spin(100, 0, 100, 5, "%")
        self.sp_dry_sup = _make_dspin(4.0, 1.5, 20.0, 0.5, 1, "mm")

        form.addRow("Layer height:", self.sp_layer_h)
        form.addRow("Nozzle diameter:", self.sp_nozzle)
        form.addRow("Line width:", self.sp_line_w)
        form.addRow("Filament diameter:", self.sp_fil_dia)
        form.addRow("Flow multiplier:", self.sp_flow)
        form.addRow("Watertight perimeters:", self.sp_perims)
        form.addRow("Bottom solid layers:", self.sp_bot_layers)
        form.addRow("Top solid layers:", self.sp_top_layers)
        form.addRow("Print speed:", self.sp_print_spd)
        form.addRow("Travel speed:", self.sp_travel_spd)
        form.addRow("Z-hop:", self.sp_zhop)
        form.addRow("Retraction length:", self.sp_retract)
        form.addRow("Retraction speed:", self.sp_retract_spd)
        form.addRow("Nozzle temp:", self.sp_nozzle_t)
        form.addRow("Bed temp:", self.sp_bed_t)
        form.addRow("Part cooling fan:", self.sp_fan)
        form.addRow("Dry core support spacing:", self.sp_dry_sup)
        return _wrap_scroll(w)

    def _build_infill_tab(self) -> QScrollArea:
        w = QWidget()
        form = QFormLayout(w)

        self.cb_pattern = QComboBox()
        self.cb_pattern.addItems(["voronoi", "hexagonal"])

        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["hollow_shell", "full_to_medial"])
        self.cb_mode.currentTextChanged.connect(self._on_mode_or_printer_changed)

        self.sp_hollow_wall = _make_dspin(6.0, 1.5, 30.0, 0.5, 2, "mm")
        self.sp_inner_skin = _make_dspin(0.9, 0.4, 5.0, 0.1, 2, "mm")
        self.sp_max_bridge = _make_dspin(8.0, 2.0, 30.0, 0.5, 2, "mm")

        self.chk_max_norm_depth = QCheckBox("Cap normal depth (isotropic core)")
        self.chk_max_norm_depth.toggled.connect(self._on_mode_or_printer_changed)
        self.sp_max_norm_depth = _make_dspin(10.0, 2.0, 80.0, 1.0, 1, "mm")

        self.sp_cell_pitch = _make_dspin(5.0, 2.0, 25.0, 0.5, 2, "mm")
        self.sp_strut_r = _make_dspin(0.45, 0.2, 3.0, 0.05, 2, "mm")
        self.sp_chan_clear = _make_dspin(1.2, 0.4, 10.0, 0.1, 2, "mm")
        self.sp_flatten_angle = _make_dspin(30.0, 0.0, 80.0, 1.0, 1, "deg")
        self.sp_div_ratio = _make_dspin(1.6, 1.2, 3.0, 0.1, 2)
        self.sp_smin_r = _make_dspin(0.30, 0.05, 2.0, 0.05, 2, "mm")
        self.sp_medial_ratio = _make_dspin(0.90, 0.50, 0.99, 0.02, 2)
        self.sp_shock_cos = _make_dspin(0.45, -0.5, 0.95, 0.05, 2)

        form.addRow("Normal pattern:", self.cb_pattern)
        form.addRow("Interior mode:", self.cb_mode)
        form.addRow("Hollow wall thickness:", self.sp_hollow_wall)
        form.addRow("Hollow inner skin:", self.sp_inner_skin)
        form.addRow("Max bridgeable span:", self.sp_max_bridge)
        form.addRow(self.chk_max_norm_depth)
        form.addRow("Core transition depth:", self.sp_max_norm_depth)
        form.addRow("Surface cell pitch:", self.sp_cell_pitch)
        form.addRow("Strut radius (w/2):", self.sp_strut_r)
        form.addRow("Min fluid channel gap:", self.sp_chan_clear)
        form.addRow("Flatten face angle (<):", self.sp_flatten_angle)
        form.addRow("Divergence split ratio:", self.sp_div_ratio)
        form.addRow("Y-junction blend radius:", self.sp_smin_r)
        form.addRow("Medial weld ratio:", self.sp_medial_ratio)
        form.addRow("Shock cos threshold:", self.sp_shock_cos)
        return _wrap_scroll(w)

    def _build_resin_tab(self) -> QScrollArea:
        w = QWidget()
        form = QFormLayout(w)

        self.sp_xmin = _make_spin(3, 1, 20, 1, "layers")
        self.sp_xmax = _make_spin(6, 1, 30, 1, "layers")
        self.sp_cure_depth = _make_dspin(1.40, 0.2, 10.0, 0.1, 2, "mm")
        self.sp_cure_eta = _make_dspin(0.85, 0.5, 1.0, 0.05, 2)
        self.sp_headspace = _make_dspin(0.15, 0.0, 1.0, 0.05, 2, "mm")
        self.sp_shrink = _make_dspin(1.04, 0.90, 1.25, 0.01, 3)
        self.sp_needle_r = _make_dspin(0.45, 0.1, 3.0, 0.05, 2, "mm")
        self.sp_needle_clr = _make_dspin(0.25, 0.0, 2.0, 0.05, 2, "mm")
        self.sp_pump_cal = _make_dspin(1.50, 0.05, 50.0, 0.1, 3, "mm3/E")
        self.sp_pump_spd = _make_dspin(4.0, 0.5, 50.0, 0.5, 1, "mm/s")
        self.sp_suckback = _make_dspin(0.40, 0.0, 10.0, 0.1, 2, "mm")
        self.sp_suck_spd = _make_dspin(12.0, 1.0, 80.0, 1.0, 1, "mm/s")
        self.sp_flow_rad = _make_dspin(8.0, 2.0, 50.0, 1.0, 1, "mm")
        self.sp_dwell = _make_spin(2500, 0, 30000, 250, "ms")
        self.sp_bleed = _make_dspin(0.25, 0.0, 2.0, 0.05, 2, "mm")
        self.sp_max_shadow = _make_dspin(0.03, 0.0, 0.50, 0.01, 3)

        form.addRow("Cure interval min (x_min):", self.sp_xmin)
        form.addRow("Cure interval max (x_max):", self.sp_xmax)
        form.addRow("Beer-Lambert cure depth:", self.sp_cure_depth)
        form.addRow("Cure depth safety factor:", self.sp_cure_eta)
        form.addRow("Meniscus headspace:", self.sp_headspace)
        form.addRow("Shrinkage compensation:", self.sp_shrink)
        form.addRow("Needle outer radius:", self.sp_needle_r)
        form.addRow("Needle wall clearance:", self.sp_needle_clr)
        form.addRow("Pump calibration:", self.sp_pump_cal)
        form.addRow("Dispense speed:", self.sp_pump_spd)
        form.addRow("Suck-back retraction:", self.sp_suckback)
        form.addRow("Suck-back speed:", self.sp_suck_spd)
        form.addRow("Max basin flow radius:", self.sp_flow_rad)
        form.addRow("Leveling dwell:", self.sp_dwell)
        form.addRow("UV shadow bleed radius:", self.sp_bleed)
        form.addRow("Max shadowed void ratio:", self.sp_max_shadow)
        return _wrap_scroll(w)

    def _build_uv_printer_tab(self) -> QScrollArea:
        w = QWidget()
        vbox = QVBoxLayout(w)

        # Printer profile group
        g_pr = QGroupBox("Printer Kinematics & Offsets")
        f_pr = QFormLayout(g_pr)
        self.cb_printer = QComboBox()
        self.cb_printer.addItems(["bedslinger_prototype", "corexy_toolchanger"])
        self.cb_printer.currentTextChanged.connect(self._on_mode_or_printer_changed)

        self.sp_bed_x = _make_dspin(220.0, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_bed_y = _make_dspin(220.0, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_bed_z = _make_dspin(220.0, 50.0, 600.0, 10.0, 1, "mm")
        self.sp_noff_x = _make_dspin(28.0, -150.0, 150.0, 1.0, 2, "mm")
        self.sp_noff_y = _make_dspin(0.0, -150.0, 150.0, 1.0, 2, "mm")
        self.sp_noff_z = _make_dspin(1.0, -10.0, 20.0, 0.2, 2, "mm")
        self.sp_x_park = _make_dspin(0.0, 0.0, 600.0, 5.0, 1, "mm")
        self.sp_uv_yoff = _make_dspin(110.0, 0.0, 600.0, 5.0, 1, "mm")
        self.ed_t_fff = QLineEdit("T0")
        self.ed_t_resin = QLineEdit("T1")
        self.ed_t_uv = QLineEdit("T2")
        self.ed_resin_axis = QLineEdit("E")

        f_pr.addRow("Printer HAL profile:", self.cb_printer)
        f_pr.addRow("Bed size X:", self.sp_bed_x)
        f_pr.addRow("Bed size Y:", self.sp_bed_y)
        f_pr.addRow("Bed size Z:", self.sp_bed_z)
        f_pr.addRow("Needle offset X:", self.sp_noff_x)
        f_pr.addRow("Needle offset Y:", self.sp_noff_y)
        f_pr.addRow("Needle dispense Z:", self.sp_noff_z)
        f_pr.addRow("X UV-shield park:", self.sp_x_park)
        f_pr.addRow("UV strip Y position:", self.sp_uv_yoff)
        f_pr.addRow("FFF tool command:", self.ed_t_fff)
        f_pr.addRow("Resin tool command:", self.ed_t_resin)
        f_pr.addRow("UV tool command:", self.ed_t_uv)
        f_pr.addRow("Resin pump axis:", self.ed_resin_axis)
        vbox.addWidget(g_pr)

        # UV settings group
        g_uv = QGroupBox("UV Hardening System")
        f_uv = QFormLayout(g_uv)
        self.sp_uv_irr = _make_dspin(25.0, 1.0, 500.0, 1.0, 1, "mW/cm2")
        self.sp_uv_beam = _make_dspin(1.5, 0.1, 20.0, 0.1, 2, "cm")
        self.sp_uv_dose = _make_dspin(80.0, 5.0, 1000.0, 5.0, 1, "mJ/cm2/mm")
        self.sp_uv_passes = _make_spin(2, 1, 12)
        self.sp_uv_cool = _make_spin(1000, 0, 20000, 250, "ms")
        self.sp_uv_zfocal = _make_dspin(15.0, 2.0, 150.0, 1.0, 1, "mm")
        self.ed_uv_on = QLineEdit("M106 P2 S255")
        self.ed_uv_off = QLineEdit("M106 P2 S0")

        f_uv.addRow("UV irradiance:", self.sp_uv_irr)
        f_uv.addRow("Beam width:", self.sp_uv_beam)
        f_uv.addRow("Target dose / mm:", self.sp_uv_dose)
        f_uv.addRow("Min thermal passes:", self.sp_uv_passes)
        f_uv.addRow("Inter-pass cooldown:", self.sp_uv_cool)
        f_uv.addRow("Focal Z clearance:", self.sp_uv_zfocal)
        f_uv.addRow("UV ON G-code:", self.ed_uv_on)
        f_uv.addRow("UV OFF G-code:", self.ed_uv_off)
        vbox.addWidget(g_uv)
        vbox.addStretch(1)
        return _wrap_scroll(w)

    def _build_engine_tab(self) -> QScrollArea:
        w = QWidget()
        form = QFormLayout(w)
        self.sp_vox_pitch = _make_dspin(0.45, 0.15, 2.0, 0.05, 2, "mm")
        self.sp_ras_pitch = _make_dspin(0.25, 0.10, 1.0, 0.05, 2, "mm")
        self.sp_step_lin = _make_dspin(0.02, 0.005, 0.5, 0.01, 3, "mm")
        self.sp_step_ang = _make_dspin(0.087, 0.01, 0.5, 0.01, 3, "rad")

        form.addRow("3D SDF voxel pitch:", self.sp_vox_pitch)
        form.addRow("2D slice raster pitch:", self.sp_ras_pitch)
        form.addRow("STEP linear tol:", self.sp_step_lin)
        form.addRow("STEP angular tol:", self.sp_step_ang)

        note = QLabel(
            "Tip: Keep SDF pitch around 0.4-0.6 mm and raster pitch 0.25-0.35 mm "
            "for fast interactive previews; lower them for final production slices."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa0aa; margin-top: 8px;")
        form.addRow(note)
        return _wrap_scroll(w)

    def _build_action_box(self) -> QGroupBox:
        box = QGroupBox("Run & Export")
        vbox = QVBoxLayout(box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        vbox.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Load a model or Test Sphere to begin.")
        self.lbl_status.setStyleSheet("color: #aeb4be;")
        vbox.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        self.btn_slice = QPushButton("Slice Model")
        self.btn_slice.setObjectName("primaryBtn")
        self.btn_slice.clicked.connect(self._start_slicing)
        self.btn_export = QPushButton("Export G-Code...")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_gcode_dialog)
        btn_row.addWidget(self.btn_slice)
        btn_row.addWidget(self.btn_export)
        vbox.addLayout(btn_row)
        return box

    def _build_2d_inspector_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        self.lbl_layer_info = QLabel("Slice a model to inspect 2D layers, resin voids, and dosing targets.")
        self.lbl_layer_info.setStyleSheet(
            "background-color: #111215; padding: 6px 10px; border-radius: 4px; font-weight: 600;"
        )
        vbox.addWidget(self.lbl_layer_info)

        self.fig_2d = Figure(facecolor="#16171a")
        self.ax_2d = self.fig_2d.add_subplot(111)
        self.canvas_2d = FigureCanvasQTAgg(self.fig_2d)
        self.toolbar_2d = NavigationToolbar2QT(self.canvas_2d, w)
        vbox.addWidget(self.toolbar_2d)
        vbox.addWidget(self.canvas_2d, stretch=1)

        ctrl = QHBoxLayout()
        self.btn_prev_ly = QPushButton("<")
        self.btn_prev_ly.setFixedWidth(34)
        self.btn_prev_ly.clicked.connect(lambda: self._step_layer(-1))

        self.slider_layer = QSlider(Qt.Orientation.Horizontal)
        self.slider_layer.setEnabled(False)
        self.slider_layer.valueChanged.connect(self._on_layer_changed)

        self.sp_layer_idx = QSpinBox()
        self.sp_layer_idx.setEnabled(False)
        self.sp_layer_idx.valueChanged.connect(self._on_layer_spin_changed)

        self.btn_next_ly = QPushButton(">")
        self.btn_next_ly.setFixedWidth(34)
        self.btn_next_ly.clicked.connect(lambda: self._step_layer(1))

        self.btn_next_cure = QPushButton("Next Cure Layer")
        self.btn_next_cure.setEnabled(False)
        self.btn_next_cure.clicked.connect(self._jump_next_cure)

        ctrl.addWidget(QLabel("Layer:"))
        ctrl.addWidget(self.btn_prev_ly)
        ctrl.addWidget(self.slider_layer, stretch=1)
        ctrl.addWidget(self.sp_layer_idx)
        ctrl.addWidget(self.btn_next_ly)
        ctrl.addWidget(self.btn_next_cure)
        vbox.addLayout(ctrl)

        self._style_2d_axes()
        return w

    def _build_3d_preview_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        top = QHBoxLayout()
        self.chk_show_mesh = QCheckBox("Show Outer Mesh")
        self.chk_show_mesh.setChecked(True)
        self.chk_show_mesh.toggled.connect(self._render_3d_view)
        self.chk_show_infill = QCheckBox("Show 3D Normal Infill Struts")
        self.chk_show_infill.setChecked(True)
        self.chk_show_infill.toggled.connect(self._render_3d_view)
        self.chk_show_bed = QCheckBox("Show Build Plate")
        self.chk_show_bed.setChecked(True)
        self.chk_show_bed.toggled.connect(self._render_3d_view)
        top.addWidget(self.chk_show_mesh)
        top.addWidget(self.chk_show_infill)
        top.addWidget(self.chk_show_bed)
        top.addStretch(1)
        vbox.addLayout(top)

        self.fig_3d = Figure(facecolor="#16171a")
        self.ax_3d = self.fig_3d.add_subplot(111, projection="3d")
        self.canvas_3d = FigureCanvasQTAgg(self.fig_3d)
        self.toolbar_3d = NavigationToolbar2QT(self.canvas_3d, w)
        vbox.addWidget(self.toolbar_3d)
        vbox.addWidget(self.canvas_3d, stretch=1)
        self._style_3d_axes()
        return w

    def _build_schedule_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        self.lbl_summary = QLabel("No slice summary yet.")
        self.lbl_summary.setStyleSheet(
            "background-color: #111215; padding: 8px 10px; border-radius: 4px; font-weight: 600;"
        )
        vbox.addWidget(self.lbl_summary)

        self.tbl_schedule = QTableWidget(0, 8)
        self.tbl_schedule.setHorizontalHeaderLabels([
            "Cycle #", "Layers", "Span", "Slab (mm)",
            "Trigger", "Shadowed (mm3)", "Dosing Pts", "Resin (mL)"
        ])
        self.tbl_schedule.setAlternatingRowColors(True)
        self.tbl_schedule.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_schedule.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_schedule.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_schedule.cellClicked.connect(self._on_schedule_row_clicked)
        vbox.addWidget(self.tbl_schedule, stretch=1)

        hint = QLabel("Click any row to jump the 2D Layer Inspector directly to that cure layer.")
        hint.setStyleSheet("color: #9aa0aa;")
        vbox.addWidget(hint)
        return w

    def _build_console_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        vbox.addWidget(QLabel("Slicer Pipeline Log:"))
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        vbox.addWidget(self.txt_log, stretch=1)

        vbox.addWidget(QLabel("G-Code Preview (first 250 lines):"))
        self.txt_gcode = QPlainTextEdit()
        self.txt_gcode.setReadOnly(True)
        vbox.addWidget(self.txt_gcode, stretch=1)
        return w

    # -------------------------------------------------------------------------
    # Config <-> Widget Synchronization
    # -------------------------------------------------------------------------

    def _on_mode_or_printer_changed(self) -> None:
        is_hollow = self.cb_mode.currentText() == "hollow_shell"
        self.sp_hollow_wall.setEnabled(is_hollow)
        self.sp_inner_skin.setEnabled(is_hollow)
        self.chk_max_norm_depth.setEnabled(not is_hollow)
        self.sp_max_norm_depth.setEnabled((not is_hollow) and self.chk_max_norm_depth.isChecked())

        is_bed = self.cb_printer.currentText() == "bedslinger_prototype"
        self.sp_noff_x.setEnabled(is_bed)
        self.sp_noff_y.setEnabled(is_bed)
        self.sp_x_park.setEnabled(is_bed)
        self.sp_uv_yoff.setEnabled(is_bed)
        self.ed_t_uv.setEnabled(not is_bed)

    def _load_config_into_widgets(self, cfg: hs.SlicerConfig) -> None:
        f, i, r, u, p = cfg.fff, cfg.infill, cfg.resin, cfg.uv, cfg.printer
        self.sp_layer_h.setValue(f.layer_height)
        self.sp_nozzle.setValue(f.nozzle_diameter)
        self.sp_line_w.setValue(f.line_width)
        self.sp_fil_dia.setValue(f.filament_diameter)
        self.sp_flow.setValue(f.flow_multiplier)
        self.sp_perims.setValue(f.perimeters)
        self.sp_bot_layers.setValue(f.bottom_solid_layers)
        self.sp_top_layers.setValue(f.top_solid_layers)
        self.sp_print_spd.setValue(f.print_speed_mm_s)
        self.sp_travel_spd.setValue(f.travel_speed_mm_s)
        self.sp_zhop.setValue(f.z_hop_mm)
        self.sp_retract.setValue(f.retract_length_mm)
        self.sp_retract_spd.setValue(f.retract_speed_mm_s)
        self.sp_nozzle_t.setValue(f.nozzle_temp_c)
        self.sp_bed_t.setValue(f.bed_temp_c)
        self.sp_fan.setValue(f.part_cooling_fan_pct)
        self.sp_dry_sup.setValue(f.dry_support_spacing_mm)

        self.cb_pattern.setCurrentText(i.pattern)
        self.cb_mode.setCurrentText(i.mode)
        self.sp_hollow_wall.setValue(i.hollow_wall_thickness)
        self.sp_inner_skin.setValue(i.inner_skin_thickness)
        self.sp_max_bridge.setValue(i.max_bridge_span)
        self.chk_max_norm_depth.setChecked(i.max_normal_depth is not None)
        if i.max_normal_depth is not None:
            self.sp_max_norm_depth.setValue(i.max_normal_depth)
        self.sp_cell_pitch.setValue(i.target_cell_pitch)
        self.sp_strut_r.setValue(i.strut_radius)
        self.sp_chan_clear.setValue(i.min_channel_clearance)
        self.sp_flatten_angle.setValue(i.flatten_angle_deg)
        self.sp_div_ratio.setValue(i.max_divergence_ratio)
        self.sp_smin_r.setValue(i.smin_blend_radius)
        self.sp_medial_ratio.setValue(i.medial_buffer_ratio)
        self.sp_shock_cos.setValue(i.shock_cos_threshold)

        self.sp_xmin.setValue(r.x_min_layers)
        self.sp_xmax.setValue(r.x_max_layers)
        self.sp_cure_depth.setValue(r.max_cure_depth_mm)
        self.sp_cure_eta.setValue(r.cure_depth_safety_eta)
        self.sp_headspace.setValue(r.headspace_margin_mm)
        self.sp_shrink.setValue(r.shrinkage_factor)
        self.sp_needle_r.setValue(r.needle_outer_radius_mm)
        self.sp_needle_clr.setValue(r.needle_safety_clear_mm)
        self.sp_pump_cal.setValue(r.pump_mm3_per_e_mm)
        self.sp_pump_spd.setValue(r.dispense_speed_e_mm_s)
        self.sp_suckback.setValue(r.suckback_e_mm)
        self.sp_suck_spd.setValue(r.suckback_speed_e_mm_s)
        self.sp_flow_rad.setValue(r.max_flow_radius_mm)
        self.sp_dwell.setValue(r.settle_dwell_ms)
        self.sp_bleed.setValue(r.uv_shadow_bleed_mm)
        self.sp_max_shadow.setValue(r.max_shadow_vol_ratio)

        self.cb_printer.setCurrentText(p.printer_type)
        self.sp_bed_x.setValue(p.bed_size_x)
        self.sp_bed_y.setValue(p.bed_size_y)
        self.sp_bed_z.setValue(p.bed_size_z)
        self.sp_noff_x.setValue(p.needle_offset_x)
        self.sp_noff_y.setValue(p.needle_offset_y)
        self.sp_noff_z.setValue(p.needle_offset_z)
        self.sp_x_park.setValue(p.x_uv_shield_park)
        self.sp_uv_yoff.setValue(p.uv_strip_y_offset)
        self.ed_t_fff.setText(p.tool_fff)
        self.ed_t_resin.setText(p.tool_resin)
        self.ed_t_uv.setText(p.tool_uv)
        self.ed_resin_axis.setText(p.resin_extruder_axis)

        self.sp_uv_irr.setValue(u.irradiance_mw_cm2)
        self.sp_uv_beam.setValue(u.beam_width_cm)
        self.sp_uv_dose.setValue(u.target_dose_mj_cm2_per_mm)
        self.sp_uv_passes.setValue(u.min_passes)
        self.sp_uv_cool.setValue(u.inter_pass_cooldown_ms)
        self.sp_uv_zfocal.setValue(u.focal_z_clearance_mm)
        self.ed_uv_on.setText(u.uv_on_gcode)
        self.ed_uv_off.setText(u.uv_off_gcode)

        self.sp_vox_pitch.setValue(cfg.voxel_pitch_mm)
        self.sp_ras_pitch.setValue(cfg.slice_raster_pitch_mm)
        self.sp_step_lin.setValue(cfg.step_tol_linear)
        self.sp_step_ang.setValue(cfg.step_tol_angular)
        self._on_mode_or_printer_changed()

    def _read_config_from_widgets(self) -> hs.SlicerConfig:
        x_min = self.sp_xmin.value()
        x_max = max(x_min, self.sp_xmax.value())
        if x_max != self.sp_xmax.value():
            self.sp_xmax.setValue(x_max)

        return hs.SlicerConfig(
            voxel_pitch_mm=self.sp_vox_pitch.value(),
            slice_raster_pitch_mm=self.sp_ras_pitch.value(),
            step_tol_linear=self.sp_step_lin.value(),
            step_tol_angular=self.sp_step_ang.value(),
            fff=hs.FFFSettings(
                layer_height=self.sp_layer_h.value(),
                nozzle_diameter=self.sp_nozzle.value(),
                line_width=self.sp_line_w.value(),
                filament_diameter=self.sp_fil_dia.value(),
                flow_multiplier=self.sp_flow.value(),
                perimeters=self.sp_perims.value(),
                bottom_solid_layers=self.sp_bot_layers.value(),
                top_solid_layers=self.sp_top_layers.value(),
                print_speed_mm_s=self.sp_print_spd.value(),
                travel_speed_mm_s=self.sp_travel_spd.value(),
                z_hop_mm=self.sp_zhop.value(),
                retract_length_mm=self.sp_retract.value(),
                retract_speed_mm_s=self.sp_retract_spd.value(),
                nozzle_temp_c=self.sp_nozzle_t.value(),
                bed_temp_c=self.sp_bed_t.value(),
                part_cooling_fan_pct=self.sp_fan.value(),
                dry_support_spacing_mm=self.sp_dry_sup.value(),
            ),
            infill=hs.InfillSettings(
                pattern=self.cb_pattern.currentText(),
                mode=self.cb_mode.currentText(),
                hollow_wall_thickness=self.sp_hollow_wall.value(),
                inner_skin_thickness=self.sp_inner_skin.value(),
                max_bridge_span=self.sp_max_bridge.value(),
                max_normal_depth=(
                    self.sp_max_norm_depth.value() if self.chk_max_norm_depth.isChecked() else None
                ),
                target_cell_pitch=self.sp_cell_pitch.value(),
                strut_radius=self.sp_strut_r.value(),
                min_channel_clearance=self.sp_chan_clear.value(),
                flatten_angle_deg=self.sp_flatten_angle.value(),
                max_divergence_ratio=self.sp_div_ratio.value(),
                smin_blend_radius=self.sp_smin_r.value(),
                medial_buffer_ratio=self.sp_medial_ratio.value(),
                shock_cos_threshold=self.sp_shock_cos.value(),
            ),
            resin=hs.ResinSettings(
                x_min_layers=x_min,
                x_max_layers=x_max,
                max_cure_depth_mm=self.sp_cure_depth.value(),
                cure_depth_safety_eta=self.sp_cure_eta.value(),
                headspace_margin_mm=self.sp_headspace.value(),
                shrinkage_factor=self.sp_shrink.value(),
                needle_outer_radius_mm=self.sp_needle_r.value(),
                needle_safety_clear_mm=self.sp_needle_clr.value(),
                pump_mm3_per_e_mm=self.sp_pump_cal.value(),
                dispense_speed_e_mm_s=self.sp_pump_spd.value(),
                suckback_e_mm=self.sp_suckback.value(),
                suckback_speed_e_mm_s=self.sp_suck_spd.value(),
                max_flow_radius_mm=self.sp_flow_rad.value(),
                settle_dwell_ms=self.sp_dwell.value(),
                uv_shadow_bleed_mm=self.sp_bleed.value(),
                max_shadow_vol_ratio=self.sp_max_shadow.value(),
            ),
            uv=hs.UVSettings(
                irradiance_mw_cm2=self.sp_uv_irr.value(),
                beam_width_cm=self.sp_uv_beam.value(),
                target_dose_mj_cm2_per_mm=self.sp_uv_dose.value(),
                min_passes=self.sp_uv_passes.value(),
                inter_pass_cooldown_ms=self.sp_uv_cool.value(),
                focal_z_clearance_mm=self.sp_uv_zfocal.value(),
                uv_on_gcode=self.ed_uv_on.text().strip() or "M106 P2 S255",
                uv_off_gcode=self.ed_uv_off.text().strip() or "M106 P2 S0",
            ),
            printer=hs.PrinterProfile(
                printer_type=self.cb_printer.currentText(),
                bed_size_x=self.sp_bed_x.value(),
                bed_size_y=self.sp_bed_y.value(),
                bed_size_z=self.sp_bed_z.value(),
                needle_offset_x=self.sp_noff_x.value(),
                needle_offset_y=self.sp_noff_y.value(),
                needle_offset_z=self.sp_noff_z.value(),
                x_uv_shield_park=self.sp_x_park.value(),
                uv_strip_y_offset=self.sp_uv_yoff.value(),
                tool_fff=self.ed_t_fff.text().strip() or "T0",
                tool_resin=self.ed_t_resin.text().strip() or "T1",
                tool_uv=self.ed_t_uv.text().strip() or "T2",
                resin_extruder_axis=self.ed_resin_axis.text().strip() or "E",
            ),
        )

    # -------------------------------------------------------------------------
    # File Actions
    # -------------------------------------------------------------------------

    def _open_model_dialog(self) -> None:
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Open 3D Model",
            "",
            "3D Models (*.stl *.3mf *.obj *.step *.stp);;All Files (*)",
        )
        if not fname:
            return
        try:
            cfg = self._read_config_from_widgets()
            mesh = hs.load_and_center_mesh(Path(fname), cfg)
            self._set_loaded_mesh(mesh, Path(fname).name)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load model:\n{exc}")

    def _load_test_sphere(self) -> None:
        cfg = self._read_config_from_widgets()
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=15.0)
        mesh.apply_translation([cfg.printer.bed_size_x * 0.5, cfg.printer.bed_size_y * 0.5, 15.0])
        self._set_loaded_mesh(mesh, "Built-in Test Sphere (30 mm)")

    def _set_loaded_mesh(self, mesh: trimesh.Trimesh, name: str) -> None:
        self.current_mesh = mesh
        self.current_model_name = name
        self.slice_result = None
        self.btn_export.setEnabled(False)
        ext = mesh.extents
        self.lbl_model.setText(
            f"Model: {name}\n"
            f"Size: {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm | "
            f"Faces: {len(mesh.faces):,}"
        )
        self.lbl_status.setText("Model loaded. Adjust settings and click Slice Model.")
        self._append_log(f"[Import] Loaded '{name}' ({ext[0]:.1f}x{ext[1]:.1f}x{ext[2]:.1f} mm).")
        self.view_tabs.setCurrentIndex(1)
        self._render_3d_view()

    def _load_config_dialog(self) -> None:
        fname, _ = QFileDialog.getOpenFileName(self, "Load Config JSON", "", "JSON (*.json)")
        if not fname:
            return
        try:
            self.cfg = hs.SlicerConfig.from_json(Path(fname))
            self._load_config_into_widgets(self.cfg)
            self._append_log(f"[Config] Loaded '{fname}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Config Error", f"Could not read config JSON:\n{exc}")

    def _save_config_dialog(self) -> None:
        fname, _ = QFileDialog.getSaveFileName(self, "Save Config JSON", "printer_profile.json", "JSON (*.json)")
        if not fname:
            return
        try:
            cfg = self._read_config_from_widgets()
            cfg.to_json(Path(fname))
            self._append_log(f"[Config] Saved '{fname}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Config Error", f"Could not write config JSON:\n{exc}")

    def _export_gcode_dialog(self) -> None:
        if not self.slice_result:
            return
        default_name = Path(self.current_model_name).stem + ".gcode"
        fname, _ = QFileDialog.getSaveFileName(self, "Export G-Code", default_name, "G-Code (*.gcode)")
        if not fname:
            return
        Path(fname).write_text(self.slice_result.gcode_text, encoding="utf-8")
        self._append_log(f"[Export] Wrote G-code to '{fname}'.")
        self.lbl_status.setText(f"Exported G-code to {Path(fname).name}")

    # -------------------------------------------------------------------------
    # Slicing Lifecycle
    # -------------------------------------------------------------------------

    def _start_slicing(self) -> None:
        if self.current_mesh is None:
            QMessageBox.information(self, "No Model", "Load a 3D model or click 'Test Sphere' first.")
            return
        if self.worker is not None and self.worker.isRunning():
            return

        self.cfg = self._read_config_from_widgets()
        self.btn_slice.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress_bar.setValue(0)
        self.txt_log.clear()

        self.worker = SliceWorker(self.current_mesh, self.cfg)
        self.worker.progress.connect(self._on_progress)
        self.worker.log_msg.connect(self._append_log)
        self.worker.finished_ok.connect(self._on_slice_finished)
        self.worker.failed.connect(self._on_slice_failed)
        self.worker.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _append_log(self, msg: str) -> None:
        self.txt_log.appendPlainText(msg)

    def _on_slice_finished(self, res: SliceResult) -> None:
        self.slice_result = res
        self.btn_slice.setEnabled(True)
        self.btn_export.setEnabled(True)

        n = len(res.layers)
        self.slider_layer.blockSignals(True)
        self.sp_layer_idx.blockSignals(True)
        self.slider_layer.setRange(1, n)
        self.sp_layer_idx.setRange(1, n)
        self.slider_layer.setEnabled(True)
        self.sp_layer_idx.setEnabled(True)
        self.btn_next_cure.setEnabled(len(res.schedule) > 0)

        # Start preview on first cure layer (or midpoint) for immediate visual feedback
        initial_layer = res.schedule[0].end_layer if res.schedule else max(1, n // 2)
        self.slider_layer.setValue(initial_layer)
        self.sp_layer_idx.setValue(initial_layer)
        self.slider_layer.blockSignals(False)
        self.sp_layer_idx.blockSignals(False)

        self._populate_schedule_table(res)
        preview_lines = "\n".join(res.gcode_text.splitlines()[:250])
        self.txt_gcode.setPlainText(preview_lines)

        self._render_2d_layer(initial_layer)
        self._render_3d_view()
        self.view_tabs.setCurrentIndex(0)

    def _on_slice_failed(self, tb: str) -> None:
        self.btn_slice.setEnabled(True)
        self.lbl_status.setText("Slicing failed (see Log tab).")
        self._append_log("[ERROR]\n" + tb)
        QMessageBox.critical(self, "Slicing Error", "Slicing encountered an error.\nCheck the Log tab for details.")

    # -------------------------------------------------------------------------
    # Visualization: 2D Layer & Fluid Basin Inspector
    # -------------------------------------------------------------------------

    def _style_2d_axes(self) -> None:
        self.ax_2d.set_facecolor("#111215")
        self.ax_2d.tick_params(colors="#b0b6c2")
        for spine in self.ax_2d.spines.values():
            spine.set_color("#32363e")
        self.ax_2d.set_xlabel("X (mm)", color="#cfd5df")
        self.ax_2d.set_ylabel("Y (mm)", color="#cfd5df")

    def _step_layer(self, delta: int) -> None:
        if not self.slice_result:
            return
        cur = self.slider_layer.value()
        nxt = max(1, min(len(self.slice_result.layers), cur + delta))
        self.slider_layer.setValue(nxt)

    def _on_layer_changed(self, layer_num: int) -> None:
        self.sp_layer_idx.blockSignals(True)
        self.sp_layer_idx.setValue(layer_num)
        self.sp_layer_idx.blockSignals(False)
        self._render_2d_layer(layer_num)

    def _on_layer_spin_changed(self, layer_num: int) -> None:
        self.slider_layer.blockSignals(True)
        self.slider_layer.setValue(layer_num)
        self.slider_layer.blockSignals(False)
        self._render_2d_layer(layer_num)

    def _jump_next_cure(self) -> None:
        if not self.slice_result or not self.slice_result.schedule:
            return
        cur = self.slider_layer.value()
        cure_layers = [c.end_layer for c in self.slice_result.schedule]
        for cl in cure_layers:
            if cl > cur:
                self.slider_layer.setValue(cl)
                return
        self.slider_layer.setValue(cure_layers[0])

    def _render_2d_layer(self, layer_num: int) -> None:
        res = self.slice_result
        if res is None:
            return
        ly = res.layers[layer_num - 1]
        xs, ys = res.xs, res.ys

        # Compose RGBA image transposed so rows=Y and cols=X
        nx, ny = ly.shell_mask.shape
        img = np.zeros((ny, nx, 4), dtype=np.float32)
        img[:, :, 3] = 1.0
        img[:, :, :3] = (0.07, 0.075, 0.085)  # Background

        # Void resin cavity (cyan-blue)
        img[ly.void_mask.T] = (0.05, 0.55, 0.78, 1.0)
        # Dry hollow support (muted yellow-amber)
        img[ly.dry_support_mask.T] = (0.72, 0.58, 0.16, 1.0)
        # 3D Normal-aligned infill (vibrant orange)
        img[ly.infill_mask.T] = (1.0, 0.42, 0.14, 1.0)
        # Watertight FFF shells & solid skins (crisp light silver)
        img[ly.shell_mask.T] = (0.86, 0.88, 0.92, 1.0)

        self.ax_2d.clear()
        self._style_2d_axes()
        self.ax_2d.imshow(
            img,
            origin="lower",
            extent=[float(xs[0]), float(xs[-1]), float(ys[0]), float(ys[-1])],
            interpolation="nearest",
        )
        self.ax_2d.set_aspect("equal", adjustable="box")

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
                "background-color: #2c1810; color: #ffab91; border: 1px solid #ff5722; "
                "padding: 6px 10px; border-radius: 4px; font-weight: 600;"
            )
            if dosing_pts:
                dx = [p.x for p in dosing_pts]
                dy = [p.y for p in dosing_pts]
                self.ax_2d.plot(dx, dy, color="#ff5252", linewidth=0.9, linestyle="--", alpha=0.8)
                self.ax_2d.scatter(dx, dy, color="#ff1744", edgecolors="#ffffff", s=45, zorder=5)
                for idx_p, pt in enumerate(dosing_pts[:25]):
                    self.ax_2d.annotate(
                        f"#{idx_p + 1}\n{pt.volume_mm3:.1f}",
                        (pt.x, pt.y),
                        textcoords="offset points",
                        xytext=(5, 5),
                        color="#ffffff",
                        fontsize=7,
                        bbox=dict(boxstyle="round,pad=0.15", fc="#16171a", ec="#ff5252", alpha=0.8),
                    )
        else:
            self.lbl_layer_info.setText(
                f"Layer {layer_num}/{len(res.layers)} (Z={ly.z:.2f} mm)  |  "
                f"FFF Print Layer (No resin injection on this layer)"
            )
            self.lbl_layer_info.setStyleSheet(
                "background-color: #111215; color: #e4e6eb; border: 1px solid #2c2f36; "
                "padding: 6px 10px; border-radius: 4px; font-weight: 600;"
            )

        legend_items = [
            Patch(facecolor=(0.86, 0.88, 0.92), label="Watertight Shell"),
            Patch(facecolor=(1.0, 0.42, 0.14), label="3D Normal Infill"),
            Patch(facecolor=(0.05, 0.55, 0.78), label="Photopolymer Void"),
            Patch(facecolor=(0.72, 0.58, 0.16), label="Dry Core Support"),
        ]
        self.ax_2d.legend(
            handles=legend_items,
            loc="upper right",
            facecolor="#16171a",
            edgecolor="#32363e",
            labelcolor="#e4e6eb",
            fontsize=8,
        )
        self.canvas_2d.draw_idle()

    # -------------------------------------------------------------------------
    # Visualization: 3D Model & Surface-Normal Infill Struts
    # -------------------------------------------------------------------------

    def _style_3d_axes(self) -> None:
        self.ax_3d.set_facecolor("#111215")
        self.ax_3d.tick_params(colors="#aeb4be", labelsize=8)
        self.ax_3d.set_xlabel("X (mm)", color="#cfd5df")
        self.ax_3d.set_ylabel("Y (mm)", color="#cfd5df")
        self.ax_3d.set_zlabel("Z (mm)", color="#cfd5df")
        for axis in (self.ax_3d.xaxis, self.ax_3d.yaxis, self.ax_3d.zaxis):
            axis.pane.set_facecolor((0.08, 0.09, 0.11, 0.85))
            axis.pane.set_edgecolor("#2c2f36")

    def _render_3d_view(self) -> None:
        self.ax_3d.clear()
        self._style_3d_axes()
        if self.current_mesh is None:
            self.canvas_3d.draw_idle()
            return

        mesh = self.current_mesh
        bounds = mesh.bounds

        # 1. Translucent outer mesh (subsampled if very high poly for responsiveness)
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
            self.ax_3d.add_collection3d(poly)

        # 2. 3D Surface-normal dendritic capsules
        if self.chk_show_infill.isChecked() and self.slice_result is not None:
            caps = self.slice_result.capsules
            if caps:
                step_c = max(1, len(caps) // 4500)
                segs = [[c.a, c.b] for c in caps[::step_c]]
                lc = Line3DCollection(segs, colors="#ff5722", linewidths=1.1, alpha=0.88)
                self.ax_3d.add_collection3d(lc)

        # 3. Build plate footprint
        if self.chk_show_bed.isChecked():
            bx = self.sp_bed_x.value()
            by = self.sp_bed_y.value()
            rect = [[
                [0, 0, 0], [bx, 0, 0], [bx, by, 0], [0, by, 0], [0, 0, 0]
            ]]
            self.ax_3d.add_collection3d(Line3DCollection(rect, colors="#3d4350", linewidths=1.0))

        # Equal-aspect bounding cube around model
        center = 0.5 * (bounds[0] + bounds[1])
        half_span = max(float(np.max(mesh.extents)) * 0.65, 10.0)
        self.ax_3d.set_xlim(center[0] - half_span, center[0] + half_span)
        self.ax_3d.set_ylim(center[1] - half_span, center[1] + half_span)
        self.ax_3d.set_zlim(0.0, max(bounds[1, 2] * 1.15, half_span * 1.5))
        self.canvas_3d.draw_idle()

    # -------------------------------------------------------------------------
    # Cure Schedule Table
    # -------------------------------------------------------------------------

    def _populate_schedule_table(self, res: SliceResult) -> None:
        self.lbl_summary.setText(
            f"Layers: {len(res.layers)}  |  3D Normal Capsules: {len(res.capsules):,}  |  "
            f"Cure Cycles: {len(res.schedule)}  |  Total Resin: {res.total_resin_ml:.2f} mL  |  "
            f"G-Code Lines: {res.gcode_line_count:,}"
        )
        self.tbl_schedule.setRowCount(len(res.schedule))
        for row, cyc in enumerate(res.schedule):
            pts = res.dosing_by_layer.get(cyc.end_layer, [])
            vol_ml = sum(p.volume_mm3 for p in pts) / 1000.0
            span = cyc.end_layer - cyc.start_layer + 1
            vals = [
                str(row + 1),
                f"{cyc.start_layer} - {cyc.end_layer}",
                f"{span}",
                f"{cyc.slab_thickness_mm:.2f}",
                cyc.trigger,
                f"{cyc.shadowed_vol_mm3:.2f}",
                str(len(pts)),
                f"{vol_ml:.3f}",
            ]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tbl_schedule.setItem(row, col, item)

    def _on_schedule_row_clicked(self, row: int, _col: int) -> None:
        if not self.slice_result or row >= len(self.slice_result.schedule):
            return
        target_layer = self.slice_result.schedule[row].end_layer
        self.slider_layer.setValue(target_layer)
        self.view_tabs.setCurrentIndex(0)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    win = HybridSlicerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()