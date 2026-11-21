"""Main desktop window composing the AlveoSlicer controls, viewports, and background worker."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import trimesh
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import SlicerConfig
from ..geometry import create_test_curved_wall_mesh, create_test_sphere_mesh, load_and_center_mesh
from ..pipeline import SliceResult
from .console_view import ConsoleGCodeView
from .inspector_2d import LayerInspector2D
from .preview_3d import Preview3DWidget
from .schedule_view import CureScheduleView
from .settings_panel import SettingsPanel
from .worker import SliceWorker


class AlveoSlicerWindow(QMainWindow):
    """Primary desktop window for AlveoSlicer."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AlveoSlicer - Hybrid FFF + In-Situ UV Photopolymer Slicer")
        self.resize(1380, 860)

        self.cfg = SlicerConfig()
        self.current_mesh: Optional[trimesh.Trimesh] = None
        self.current_model_name: str = "None"
        self.slice_result: Optional[SliceResult] = None
        self.worker: Optional[SliceWorker] = None

        self._build_ui()

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

        self.settings_panel = SettingsPanel(self.cfg)
        left_layout.addWidget(self.settings_panel, stretch=1)

        left_layout.addWidget(self._build_action_box())
        splitter.addWidget(left_panel)

        # Right Panel: Viewports + Schedule + Logs
        self.view_tabs = QTabWidget()
        self.inspector_2d = LayerInspector2D()
        self.preview_3d = Preview3DWidget(bed_size_provider=self.settings_panel.bed_size_xy)
        self.schedule_view = CureScheduleView()
        self.schedule_view.layer_selected.connect(self._jump_to_layer)
        self.console_view = ConsoleGCodeView()

        self.view_tabs.addTab(self.inspector_2d, "2D Layer & Basins")
        self.view_tabs.addTab(self.preview_3d, "3D Model & Normal Infill")
        self.view_tabs.addTab(self.schedule_view, "DP Cure Schedule")
        self.view_tabs.addTab(self.console_view, "Log & G-Code")
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
        btn_sphere = QPushButton("Test Sphere")
        btn_sphere.clicked.connect(self._load_test_sphere)
        btn_wall = QPushButton("Test Wall")
        btn_wall.clicked.connect(self._load_test_wall)
        row1.addWidget(btn_open)
        row1.addWidget(btn_sphere)
        row1.addWidget(btn_wall)
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

    def _build_action_box(self) -> QGroupBox:
        box = QGroupBox("Run & Export")
        vbox = QVBoxLayout(box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        vbox.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Load a model or built-in test shape to begin.")
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

    # -------------------------------------------------------------------------
    # Model & Config File Actions
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
            cfg = self.settings_panel.read_config()
            mesh = load_and_center_mesh(Path(fname), cfg)
            self._set_loaded_mesh(mesh, Path(fname).name)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load model:\n{exc}")

    def _load_test_sphere(self) -> None:
        cfg = self.settings_panel.read_config()
        mesh = create_test_sphere_mesh(cfg)
        self._set_loaded_mesh(mesh, "Built-in Test Sphere (30 mm)")

    def _load_test_wall(self) -> None:
        cfg = self.settings_panel.read_config()
        mesh = create_test_curved_wall_mesh(cfg)
        self._set_loaded_mesh(mesh, "Built-in Curved Wall (60x25x5 mm)")

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
        self.console_view.append_log(
            f"[Import] Loaded '{name}' ({ext[0]:.1f}x{ext[1]:.1f}x{ext[2]:.1f} mm)."
        )
        self.view_tabs.setCurrentIndex(1)
        self.preview_3d.set_scene(mesh, capsules=None)

    def _load_config_dialog(self) -> None:
        fname, _ = QFileDialog.getOpenFileName(self, "Load Config JSON", "", "JSON (*.json)")
        if not fname:
            return
        try:
            self.cfg = SlicerConfig.from_json(Path(fname))
            self.settings_panel.load_config(self.cfg)
            self.console_view.append_log(f"[Config] Loaded '{fname}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Config Error", f"Could not read config JSON:\n{exc}")

    def _save_config_dialog(self) -> None:
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save Config JSON", "printer_profile.json", "JSON (*.json)"
        )
        if not fname:
            return
        try:
            cfg = self.settings_panel.read_config()
            cfg.to_json(Path(fname))
            self.console_view.append_log(f"[Config] Saved '{fname}'.")
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
        self.console_view.append_log(f"[Export] Wrote G-code to '{fname}'.")
        self.lbl_status.setText(f"Exported G-code to {Path(fname).name}")

    # -------------------------------------------------------------------------
    # Slicing Lifecycle
    # -------------------------------------------------------------------------

    def _start_slicing(self) -> None:
        if self.current_mesh is None:
            QMessageBox.information(
                self, "No Model", "Load a 3D model or click 'Test Sphere' / 'Test Wall' first."
            )
            return
        if self.worker is not None and self.worker.isRunning():
            return

        self.cfg = self.settings_panel.read_config()
        self.btn_slice.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress_bar.setValue(0)
        self.console_view.clear_log()

        self.worker = SliceWorker(self.current_mesh, self.cfg)
        self.worker.progress.connect(self._on_progress)
        self.worker.log_msg.connect(self.console_view.append_log)
        self.worker.finished_ok.connect(self._on_slice_finished)
        self.worker.failed.connect(self._on_slice_failed)
        self.worker.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_slice_finished(self, res: SliceResult) -> None:
        self.slice_result = res
        self.btn_slice.setEnabled(True)
        self.btn_export.setEnabled(True)

        self.inspector_2d.set_result(res)
        self.preview_3d.set_scene(self.current_mesh, capsules=res.capsules)
        self.schedule_view.populate(res)
        self.console_view.set_gcode_preview(res.gcode_text)
        self.view_tabs.setCurrentIndex(0)

    def _on_slice_failed(self, tb: str) -> None:
        self.btn_slice.setEnabled(True)
        self.lbl_status.setText("Slicing failed (see Log tab).")
        self.console_view.append_log("[ERROR]\n" + tb)
        QMessageBox.critical(
            self, "Slicing Error", "Slicing encountered an error.\nCheck the Log tab for details."
        )

    def _jump_to_layer(self, layer_num: int) -> None:
        self.inspector_2d.select_layer(layer_num)
        self.view_tabs.setCurrentIndex(0)
