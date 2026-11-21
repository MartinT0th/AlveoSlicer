"""Main PrusaSlicer-style desktop window orchestrating Plater, per-category Settings Tabs, Presets, and Slicing."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import trimesh
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import SlicerConfig
from ..gcode_parser import GCodePreviewData, parse_gcode
from ..geometry import (
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    load_and_center_mesh,
)
from ..pipeline import SliceResult
from ..presets import CATEGORY_LABELS, PRESET_CATEGORIES, PresetManager
from .console_view import ConsoleGCodeView
from .plater_view import PlaterView
from .schedule_view import CureScheduleView
from .settings_pages import (
    CategorySettingsTab,
    build_filament_settings_tab,
    build_hybrid_settings_tab,
    build_print_settings_tab,
    build_printer_settings_tab,
    build_resin_settings_tab,
)
from .theme import DARK_STYLESHEET
from .worker import SliceWorker


class AlveoSlicerWindow(QMainWindow):
    """PrusaSlicer-inspired main GUI for AlveoSlicer with per-category presets and 3D G-code viewer."""

    def __init__(self, preset_manager: Optional[PresetManager] = None) -> None:
        super().__init__()
        self.setWindowTitle("AlveoSlicer — Hybrid FFF + Photopolymer Slicer")
        self.resize(1520, 940)
        self.setStyleSheet(DARK_STYLESHEET)

        self.preset_manager = preset_manager or PresetManager()
        self.cfg = SlicerConfig()

        # Model & transform state
        self._base_mesh: Optional[trimesh.Trimesh] = None
        self.current_mesh: Optional[trimesh.Trimesh] = None
        self.current_model_name: str = ""
        self._scale_pct: float = 100.0
        self._rot_z_deg: float = 0.0
        self._pos_x: float = self.cfg.printer.bed_size_x * 0.5
        self._pos_y: float = self.cfg.printer.bed_size_y * 0.5

        # Slicing / G-code state
        self.slice_result: Optional[SliceResult] = None
        self.preview_data: Optional[GCodePreviewData] = None
        self._loaded_gcode_text: str = ""
        self.worker: Optional[SliceWorker] = None

        self._build_ui()
        self._build_menu_bar()
        self._init_presets_and_config()

        # Load built-in curved test wall on startup so the build plate is immediately populated
        self._load_test_wall(fit_camera=True)

    # -------------------------------------------------------------------------
    # UI & Menu Construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.setDocumentMode(True)

        # Tab 0: Plater (3D Editor + G-Code Preview + 2D Basins + Presets Sidebar)
        self.plater = PlaterView(self.preset_manager)
        self.plater.add_model_requested.connect(self._open_model_dialog)
        self.plater.load_sphere_requested.connect(lambda: self._load_test_sphere(fit_camera=True))
        self.plater.load_wall_requested.connect(lambda: self._load_test_wall(fit_camera=True))
        self.plater.center_model_requested.connect(self._center_model)
        self.plater.drop_model_requested.connect(self._drop_model_to_bed)
        self.plater.clear_model_requested.connect(self._clear_model)
        self.plater.transform_changed.connect(self._on_plater_transform_changed)
        self.plater.reset_transform_requested.connect(self._reset_transform)
        self.plater.preset_selected.connect(self._on_preset_selected)
        self.plater.open_settings_requested.connect(self._focus_settings_tab)
        self.plater.slice_requested.connect(self._start_slicing)
        self.plater.export_gcode_requested.connect(self._export_gcode)
        self.tabs.addTab(self.plater, "Plater")

        # Tabs 1..5: PrusaSlicer-style per-category Settings Tabs
        builders = {
            "print": build_print_settings_tab,
            "filament": build_filament_settings_tab,
            "resin": build_resin_settings_tab,
            "printer": build_printer_settings_tab,
            "hybrid": build_hybrid_settings_tab,
        }
        self.settings_tabs: Dict[str, CategorySettingsTab] = {}
        self._category_tab_indices: Dict[str, int] = {}

        for cat in PRESET_CATEGORIES:
            tab = builders[cat](self.preset_manager, self.cfg)
            tab.values_changed.connect(self._on_tab_values_changed)
            tab.preset_selected.connect(self._on_preset_selected)
            tab.presets_mutated.connect(self._on_presets_mutated)
            idx = self.tabs.addTab(tab, CATEGORY_LABELS[cat])
            self.settings_tabs[cat] = tab
            self._category_tab_indices[cat] = idx

        # Tab 6: Schedule & Pipeline Log
        sched_log = QSplitter(Qt.Orientation.Vertical)
        self.schedule_view = CureScheduleView()
        self.schedule_view.layer_selected.connect(self._jump_to_cure_layer)
        self.console_view = ConsoleGCodeView()
        sched_log.addWidget(self.schedule_view)
        sched_log.addWidget(self.console_view)
        sched_log.setSizes([380, 420])
        self.tabs.addTab(sched_log, "Schedule && Log")

        root.addWidget(self.tabs)

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()

        # ---- File Menu ----
        m_file = mb.addMenu("&File")

        act_open = QAction("Open 3D Model...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open_model_dialog)
        m_file.addAction(act_open)

        act_open_gcode = QAction("Open External G-Code...", self)
        act_open_gcode.setShortcut(QKeySequence("Ctrl+G"))
        act_open_gcode.triggered.connect(self._open_external_gcode)
        m_file.addAction(act_open_gcode)

        act_export_gcode = QAction("Export G-Code...", self)
        act_export_gcode.setShortcut(QKeySequence("Ctrl+E"))
        act_export_gcode.triggered.connect(self._export_gcode)
        m_file.addAction(act_export_gcode)

        m_file.addSeparator()

        act_import_cfg = QAction("Import Config JSON...", self)
        act_import_cfg.triggered.connect(self._import_config_json)
        m_file.addAction(act_import_cfg)

        act_export_cfg = QAction("Export Config JSON...", self)
        act_export_cfg.triggered.connect(self._export_config_json)
        m_file.addAction(act_export_cfg)

        m_file.addSeparator()

        act_exit = QAction("Exit", self)
        act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        act_exit.triggered.connect(self.close)
        m_file.addAction(act_exit)

        # ---- Presets Menu ----
        m_presets = mb.addMenu("&Presets")
        for cat in PRESET_CATEGORIES:
            act = QAction(f"Edit {CATEGORY_LABELS[cat]}...", self)
            act.triggered.connect(lambda _checked=False, c=cat: self._focus_settings_tab(c))
            m_presets.addAction(act)

        # ---- View Menu ----
        m_view = mb.addMenu("&View")

        act_v_ed = QAction("3D Editor", self)
        act_v_ed.setShortcut(QKeySequence("Ctrl+1"))
        act_v_ed.triggered.connect(lambda: self._switch_plater_view("editor"))
        m_view.addAction(act_v_ed)

        act_v_prev = QAction("G-Code Preview", self)
        act_v_prev.setShortcut(QKeySequence("Ctrl+2"))
        act_v_prev.triggered.connect(lambda: self._switch_plater_view("preview"))
        m_view.addAction(act_v_prev)

        act_v_2d = QAction("2D Basins", self)
        act_v_2d.setShortcut(QKeySequence("Ctrl+3"))
        act_v_2d.triggered.connect(lambda: self._switch_plater_view("basins"))
        m_view.addAction(act_v_2d)

        m_view.addSeparator()

        for label, key in (
            ("Camera: Isometric", "iso"),
            ("Camera: Top", "top"),
            ("Camera: Front", "front"),
            ("Camera: Right", "right"),
        ):
            act = QAction(label, self)
            act.triggered.connect(lambda _checked=False, k=key: self.plater.viewport.set_camera_preset(k))
            m_view.addAction(act)

        act_fit = QAction("Camera: Fit Build Plate", self)
        act_fit.triggered.connect(self.plater.viewport.fit_to_bed)
        m_view.addAction(act_fit)

    # -------------------------------------------------------------------------
    # Preset & Config Synchronization
    # -------------------------------------------------------------------------

    def _init_presets_and_config(self) -> None:
        for cat in PRESET_CATEGORIES:
            default_name = self.preset_manager.default_preset_name(cat)
            self.preset_manager.apply_preset(self.cfg, cat, default_name)
            tab = self.settings_tabs[cat]
            tab.refresh_preset_list(select_name=default_name)
            tab.load_from_config(self.cfg)
            self.plater.refresh_preset_dropdown(cat, default_name, is_modified=False)

        self._sync_bed_and_mode()

    def _sync_bed_and_mode(self) -> None:
        p = self.cfg.printer
        self.plater.viewport.set_bed_size(p.bed_size_x, p.bed_size_y, p.bed_size_z)
        self.plater.update_mode_badge(self.cfg)

    def _on_preset_selected(self, category: str, name: str) -> None:
        if not self.preset_manager.apply_preset(self.cfg, category, name):
            return
        tab = self.settings_tabs[category]
        tab.select_preset_by_name(name, emit=False)
        tab.load_from_config(self.cfg)
        self.plater.refresh_preset_dropdown(category, name, is_modified=False)
        self._sync_bed_and_mode()

    def _on_tab_values_changed(self, category: str) -> None:
        tab = self.settings_tabs[category]
        tab.write_to_config(self.cfg)
        tab.update_modified_indicator(self.cfg)
        modified = self.preset_manager.is_modified(self.cfg, category, tab.active_preset_name)
        self.plater.set_preset_modified(category, modified)
        self._sync_bed_and_mode()

    def _on_presets_mutated(self, category: str) -> None:
        tab = self.settings_tabs[category]
        tab.update_modified_indicator(self.cfg)
        modified = self.preset_manager.is_modified(self.cfg, category, tab.active_preset_name)
        self.plater.refresh_preset_dropdown(category, tab.active_preset_name, is_modified=modified)

    def _focus_settings_tab(self, category: str) -> None:
        idx = self._category_tab_indices.get(category)
        if idx is not None:
            self.tabs.setCurrentIndex(idx)

    def _switch_plater_view(self, mode: str) -> None:
        self.tabs.setCurrentIndex(0)
        self.plater.set_view_mode(mode)

    # -------------------------------------------------------------------------
    # Model Loading & Non-Destructive Transforms
    # -------------------------------------------------------------------------

    def _set_base_mesh(self, mesh: trimesh.Trimesh, name: str, fit_camera: bool = True) -> None:
        base = mesh.copy()
        bounds = base.bounds
        cx = 0.5 * (bounds[0][0] + bounds[1][0])
        cy = 0.5 * (bounds[0][1] + bounds[1][1])
        base.apply_translation([-cx, -cy, -bounds[0][2]])

        self._base_mesh = base
        self.current_model_name = name
        self._scale_pct = 100.0
        self._rot_z_deg = 0.0
        self._pos_x = self.cfg.printer.bed_size_x * 0.5
        self._pos_y = self.cfg.printer.bed_size_y * 0.5

        self.slice_result = None
        self.preview_data = None
        self._loaded_gcode_text = ""
        self.plater.set_slice_result(None, None, switch_to_preview=False)

        self._apply_mesh_transform(fit_camera=fit_camera)
        self.plater.set_view_mode("editor")
        self.plater.set_progress(0, f"Loaded {name}. Ready to slice.")
        self.console_view.append_log(f"Loaded model: {name} ({len(base.faces):,} faces)")

    def _apply_mesh_transform(self, fit_camera: bool = False) -> None:
        if self._base_mesh is None:
            self.current_mesh = None
            self.plater.update_model_display("", None)
            return

        m = self._base_mesh.copy()
        s = max(0.05, self._scale_pct / 100.0)
        if abs(s - 1.0) > 1e-6:
            m.apply_scale(s)
        if abs(self._rot_z_deg) > 1e-6:
            rot = trimesh.transformations.rotation_matrix(
                math.radians(self._rot_z_deg), [0.0, 0.0, 1.0]
            )
            m.apply_transform(rot)

        # Keep Z grounded at 0 and center XY at (_pos_x, _pos_y)
        bounds = m.bounds
        cx = 0.5 * (bounds[0][0] + bounds[1][0])
        cy = 0.5 * (bounds[0][1] + bounds[1][1])
        m.apply_translation([self._pos_x - cx, self._pos_y - cy, -bounds[0][2]])

        self.current_mesh = m
        self.plater.update_model_display(
            self.current_model_name,
            self.current_mesh,
            scale_pct=self._scale_pct,
            rot_z_deg=self._rot_z_deg,
            capsules=None,
            fit_camera=fit_camera,
        )

    def _on_plater_transform_changed(
        self, scale_pct: float, rot_z_deg: float, pos_x: float, pos_y: float
    ) -> None:
        self._scale_pct = scale_pct
        self._rot_z_deg = rot_z_deg
        self._pos_x = pos_x
        self._pos_y = pos_y
        self._apply_mesh_transform(fit_camera=False)

    def _reset_transform(self) -> None:
        if self._base_mesh is None:
            return
        self._scale_pct = 100.0
        self._rot_z_deg = 0.0
        self._pos_x = self.cfg.printer.bed_size_x * 0.5
        self._pos_y = self.cfg.printer.bed_size_y * 0.5
        self._apply_mesh_transform(fit_camera=True)

    def _center_model(self) -> None:
        if self._base_mesh is None:
            return
        self._pos_x = self.cfg.printer.bed_size_x * 0.5
        self._pos_y = self.cfg.printer.bed_size_y * 0.5
        self._apply_mesh_transform(fit_camera=False)

    def _drop_model_to_bed(self) -> None:
        if self.current_mesh is None:
            return
        bounds = self.current_mesh.bounds
        self.current_mesh.apply_translation([0.0, 0.0, -bounds[0][2]])
        self.plater.update_model_display(
            self.current_model_name,
            self.current_mesh,
            scale_pct=self._scale_pct,
            rot_z_deg=self._rot_z_deg,
            capsules=None,
            fit_camera=False,
        )

    def _clear_model(self) -> None:
        self._base_mesh = None
        self.current_mesh = None
        self.current_model_name = ""
        self.slice_result = None
        self.preview_data = None
        self._loaded_gcode_text = ""
        self.plater.update_model_display("", None)
        self.plater.set_slice_result(None, None, switch_to_preview=False)
        self.plater.set_progress(0, "Build plate cleared.")

    def _open_model_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open 3D Model",
            "",
            "3D Models (*.stl *.obj *.3mf *.ply *.step *.stp);;All Files (*)",
        )
        if not path:
            return
        try:
            mesh = load_and_center_mesh(path, self.cfg)
            self._set_base_mesh(mesh, Path(path).name, fit_camera=True)
        except Exception as exc:
            QMessageBox.critical(self, "Model Load Error", str(exc))

    def _load_test_sphere(self, fit_camera: bool = True) -> None:
        mesh = create_test_sphere_mesh(self.cfg, radius=15.0)
        self._set_base_mesh(mesh, "Test Sphere (30 mm)", fit_camera=fit_camera)

    def _load_test_wall(self, fit_camera: bool = True) -> None:
        mesh = create_test_curved_wall_mesh(self.cfg)
        self._set_base_mesh(mesh, "Test Curved Wall (10 mm)", fit_camera=fit_camera)

    # -------------------------------------------------------------------------
    # Slicing & G-Code Preview / Export
    # -------------------------------------------------------------------------

    def _collect_full_config(self) -> SlicerConfig:
        for tab in self.settings_tabs.values():
            tab.write_to_config(self.cfg)
        return self.cfg

    def _start_slicing(self) -> None:
        if self.current_mesh is None:
            QMessageBox.warning(self, "No Model", "Load a 3D model before slicing.")
            return
        if self.worker is not None and self.worker.isRunning():
            return

        cfg = self._collect_full_config()
        self.console_view.clear_log()
        self.plater.set_slicing_busy(True, "Starting AlveoSlicer pipeline...")
        self.tabs.setCurrentIndex(0)

        self.worker = SliceWorker(self.current_mesh, cfg)
        self.worker.progress.connect(self.plater.set_progress)
        self.worker.log_msg.connect(self.console_view.append_log)
        self.worker.finished_ok.connect(self._on_slice_done)
        self.worker.failed.connect(self._on_slice_failed)
        self.worker.start()

    def _on_slice_done(self, res: SliceResult) -> None:
        self.slice_result = res
        self._loaded_gcode_text = res.gcode_text
        self.preview_data = parse_gcode(res.gcode_text)

        self.plater.set_slicing_busy(False, "Slicing complete! Inspect toolpaths or export G-Code.")
        self.plater.progress_bar.setValue(100)
        self.plater.set_slice_result(res, self.preview_data, switch_to_preview=True)
        self.schedule_view.populate(res)
        self.console_view.set_gcode_preview(res.gcode_text)

        mode_txt = "Standard FFF" if self.cfg.hybrid.is_fff_only else "Hybrid"
        self.console_view.append_log(
            f"--- SLICING COMPLETE ({mode_txt}) | {len(res.layers)} layers, "
            f"{len(res.schedule)} cure cycles, {res.total_resin_ml:.2f} mL resin, "
            f"{res.total_filament_mm / 1000.0:.2f} m filament ---"
        )

    def _on_slice_failed(self, err_tb: str) -> None:
        self.plater.set_slicing_busy(False, "Slicing failed. See Schedule & Log tab.")
        self.console_view.append_log("ERROR:\n" + err_tb)
        QMessageBox.critical(
            self,
            "Slicing Error",
            f"The slicer pipeline encountered an error:\n\n{err_tb[-1000:]}",
        )

    def _open_external_gcode(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open G-Code File", "", "G-Code Files (*.gcode *.gco *.g);;All Files (*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            preview = parse_gcode(text)
            if preview.num_layers == 0:
                QMessageBox.warning(
                    self, "Empty G-Code", "No printable layers were detected in this G-code file."
                )
                return
            self.slice_result = None
            self._loaded_gcode_text = text
            self.preview_data = preview
            self.plater.set_slice_result(None, preview, switch_to_preview=True)
            self.console_view.set_gcode_preview(text)
            self.plater.set_progress(
                100, f"Loaded G-code: {Path(path).name} ({preview.num_layers} layers)"
            )
            self.tabs.setCurrentIndex(0)
        except Exception as exc:
            QMessageBox.critical(self, "Open G-Code Error", str(exc))

    def _export_gcode(self) -> None:
        text = self.slice_result.gcode_text if self.slice_result is not None else self._loaded_gcode_text
        if not text:
            return
        stem = Path(self.current_model_name).stem if self.current_model_name else "alveo_print"
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem).strip("_") or "alveo_print"
        default_name = f"{safe_stem}.gcode"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export G-Code", default_name, "G-Code Files (*.gcode);;All Files (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.plater.lbl_status.setText(f"Exported G-code to {Path(path).name}")
            self.console_view.append_log(f"Saved G-Code: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _jump_to_cure_layer(self, layer_num: int) -> None:
        self.tabs.setCurrentIndex(0)
        self.plater.jump_to_layer(layer_num)

    # -------------------------------------------------------------------------
    # Full Config JSON Import / Export
    # -------------------------------------------------------------------------

    def _export_config_json(self) -> None:
        cfg = self._collect_full_config()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Full Config JSON", "alveoslicer_config.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(cfg.to_json(), encoding="utf-8")
            self.console_view.append_log(f"Exported full config JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Config Error", str(exc))

    def _import_config_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Full Config JSON", "", "JSON (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            loaded = SlicerConfig.from_json(Path(path).read_text(encoding="utf-8"))
            self.cfg = loaded
            for cat, tab in self.settings_tabs.items():
                tab.load_from_config(self.cfg)
                modified = self.preset_manager.is_modified(self.cfg, cat, tab.active_preset_name)
                self.plater.set_preset_modified(cat, modified)
            self._sync_bed_and_mode()
            self.console_view.append_log(f"Imported full config JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Import Config Error", str(exc))
