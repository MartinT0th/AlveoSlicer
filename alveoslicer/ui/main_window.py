"""Main PrusaSlicer-style desktop window orchestrating multi-plate Plater, Settings Tabs, Presets, and Slicing."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import trimesh
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
from ..scene import WorkspaceScene
from .console_view import ConsoleGCodeView
from .icons import make_icon
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
from .theme import (
    THEME_MODES,
    build_stylesheet,
    get_palette,
    load_theme_preference,
    resolve_theme_mode,
    save_theme_preference,
)
from .worker import SliceWorker


class AlveoSlicerWindow(QMainWindow):
    """PrusaSlicer-inspired main GUI for AlveoSlicer with multi-plate editing, presets, Cut Plane, Paint Supports, and Themes."""

    def __init__(self, preset_manager: Optional[PresetManager] = None) -> None:
        super().__init__()
        self.setWindowTitle("AlveoSlicer — Hybrid FFF + Photopolymer Slicer")
        self.resize(1540, 950)

        self.preset_manager = preset_manager or PresetManager()
        self.cfg = SlicerConfig()
        self.scene = WorkspaceScene()

        self.theme_mode: str = load_theme_preference()
        self.resolved_theme: str = resolve_theme_mode(self.theme_mode)
        self._theme_buttons: Dict[str, QPushButton] = {}
        self._theme_actions: Dict[str, QAction] = {}
        self._tab_icon_names: Dict[int, str] = {}

        # Slicing worker & multi-plate queue state
        self.worker: Optional[SliceWorker] = None
        self._slicing_plate_idx: int = 0
        self._slice_queue: List[int] = []

        self._build_ui()
        self._build_menu_bar()
        self._init_presets_and_config()
        self.set_theme_mode(self.theme_mode, save=False)

        # Follow OS live Dark/Light changes when set to System mode
        app = QGuiApplication.instance()
        if app is not None:
            try:
                app.styleHints().colorSchemeChanged.connect(self._on_system_color_scheme_changed)
            except Exception:
                pass

        # Populate Plate 1 with built-in curved test wall on startup
        self._load_test_wall(fit_camera=True)

    # -------------------------------------------------------------------------
    # Compatibility Properties (active plate shortcuts)
    # -------------------------------------------------------------------------

    @property
    def current_mesh(self) -> Optional[trimesh.Trimesh]:
        return self.scene.active_plate.combined_mesh()

    @property
    def slice_result(self) -> Optional[SliceResult]:
        return self.scene.active_plate.slice_result

    @property
    def preview_data(self) -> Optional[GCodePreviewData]:
        return self.scene.active_plate.preview_data

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

        # Tab 0: Plater
        self.plater = PlaterView(self.preset_manager)
        self.plater.plate_selected.connect(self._on_plate_selected)
        self.plater.add_plate_requested.connect(self._add_plate)
        self.plater.remove_plate_requested.connect(self._remove_active_plate)

        self.plater.add_model_requested.connect(self._open_model_dialog)
        self.plater.load_sphere_requested.connect(lambda: self._load_test_sphere(fit_camera=False))
        self.plater.load_wall_requested.connect(lambda: self._load_test_wall(fit_camera=False))
        self.plater.instance_selected.connect(self._on_instance_selected)
        self.plater.duplicate_instance_requested.connect(self._duplicate_selected_instance)
        self.plater.remove_instance_requested.connect(self._remove_selected_instance)
        self.plater.move_to_plate_requested.connect(self._move_selected_to_plate)
        self.plater.arrange_plate_requested.connect(self._arrange_active_plate)
        self.plater.center_model_requested.connect(self._center_selected_instance)
        self.plater.drop_model_requested.connect(self._drop_selected_instance)
        self.plater.reset_transform_requested.connect(self._reset_selected_transform)
        self.plater.instance_transform_changed.connect(self._on_plater_transform_changed)
        self.plater.quick_rotate_requested.connect(self._quick_rotate_selected)

        # Interactive 3D Viewport manipulation signals
        self.plater.viewport.instance_selected.connect(self._on_instance_selected)
        self.plater.viewport.selection_cleared.connect(self._on_selection_cleared)
        self.plater.viewport.plate_selected.connect(self._on_plate_selected)
        self.plater.viewport.instance_moved.connect(self._on_viewport_instance_moved)
        self.plater.viewport.instance_moved_z.connect(self._on_viewport_instance_moved_z)
        self.plater.viewport.instance_rotated.connect(self._on_viewport_instance_rotated)
        self.plater.viewport.instance_scaled.connect(self._on_viewport_instance_scaled)
        self.plater.viewport.face_picked.connect(self._on_viewport_face_picked)
        self.plater.viewport.support_painted.connect(self._on_viewport_support_painted)

        # Cut Plane & Paint-on Supports actions
        self.plater.cut_performed.connect(self._on_cut_performed)
        self.plater.paint_smart_overhangs_requested.connect(self._on_paint_smart_overhangs)
        self.plater.paint_clear_requested.connect(self._on_paint_clear)

        # Presets & Slicing signals
        self.plater.preset_selected.connect(self._on_preset_selected)
        self.plater.open_settings_requested.connect(self._focus_settings_tab)
        self.plater.slice_requested.connect(self._start_slicing_active_plate)
        self.plater.slice_all_plates_requested.connect(self._start_slicing_all_plates)
        self.plater.export_gcode_requested.connect(self._export_gcode)
        plater_idx = self.tabs.addTab(self.plater, "Plater")
        self._tab_icon_names[plater_idx] = "view_3d"

        # Tabs 1..5: Per-category Settings Tabs
        builders = {
            "print": build_print_settings_tab,
            "filament": build_filament_settings_tab,
            "resin": build_resin_settings_tab,
            "printer": build_printer_settings_tab,
            "hybrid": build_hybrid_settings_tab,
        }
        cat_icons = {
            "print": "layers",
            "filament": "filament",
            "resin": "resin",
            "printer": "printer",
            "hybrid": "uv",
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
            self._tab_icon_names[idx] = cat_icons[cat]

        # Tab 6: Schedule & Pipeline Log
        sched_log = QSplitter(Qt.Orientation.Vertical)
        self.schedule_view = CureScheduleView()
        self.schedule_view.layer_selected.connect(self._jump_to_cure_layer)
        self.console_view = ConsoleGCodeView()
        sched_log.addWidget(self.schedule_view)
        sched_log.addWidget(self.console_view)
        sched_log.setSizes([380, 420])
        sched_idx = self.tabs.addTab(sched_log, "Schedule && Log")
        self._tab_icon_names[sched_idx] = "gcode"

        self.tabs.setIconSize(QSize(15, 15))

        # Top-Right Theme Switcher (Dark / Light / System) in Tab Bar corner
        theme_bar = QWidget()
        tb_row = QHBoxLayout(theme_bar)
        tb_row.setContentsMargins(6, 4, 10, 4)
        tb_row.setSpacing(3)
        for mode_key, label, icon_name, tip in (
            ("dark", "Dark", "theme_dark", "Switch to Dark Mode"),
            ("light", "Light", "theme_light", "Switch to Light Mode"),
            ("system", "System", "theme_system", "Follow operating system Light/Dark theme"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("themeBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setIconSize(QSize(14, 14))
            btn.clicked.connect(lambda _c=False, m=mode_key: self.set_theme_mode(m))
            tb_row.addWidget(btn)
            self._theme_buttons[mode_key] = btn
        self.tabs.setCornerWidget(theme_bar, Qt.Corner.TopRightCorner)

        root.addWidget(self.tabs)

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()

        # ---- File Menu ----
        m_file = mb.addMenu("&File")

        act_open = QAction("Add 3D Model...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open_model_dialog)
        m_file.addAction(act_open)

        act_new_plate = QAction("New Build Plate", self)
        act_new_plate.setShortcut(QKeySequence("Ctrl+N"))
        act_new_plate.triggered.connect(self._add_plate)
        m_file.addAction(act_new_plate)

        act_open_gcode = QAction("Open External G-Code...", self)
        act_open_gcode.setShortcut(QKeySequence("Ctrl+G"))
        act_open_gcode.triggered.connect(self._open_external_gcode)
        m_file.addAction(act_open_gcode)

        act_export_gcode = QAction("Export Active Plate G-Code...", self)
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

        # ---- Edit / Tools Menu ----
        m_edit = mb.addMenu("&Edit")

        for label, shortcut, tool_name in (
            ("Select / Orbit Tool", "Escape", "select"),
            ("Move Tool", "M", "move"),
            ("Rotate Tool", "R", "rotate"),
            ("Scale Tool", "S", "scale"),
            ("Place on Face Tool", "F", "place_on_face"),
            ("Cut Plane Tool", "C", "cut"),
            ("Paint-on Supports Tool", "L", "paint_supports"),
        ):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(lambda _chk=False, t=tool_name: self.plater.set_edit_tool(t))
            m_edit.addAction(act)

        m_edit.addSeparator()

        act_dup = QAction("Duplicate Instance", self)
        act_dup.setShortcut(QKeySequence("Ctrl+D"))
        act_dup.triggered.connect(self._duplicate_selected_instance)
        m_edit.addAction(act_dup)

        act_arr = QAction("Auto-Arrange Plate", self)
        act_arr.setShortcut(QKeySequence("A"))
        act_arr.triggered.connect(self._arrange_active_plate)
        m_edit.addAction(act_arr)

        act_del = QAction("Delete Selected Object", self)
        act_del.setShortcut(QKeySequence.StandardKey.Delete)
        act_del.triggered.connect(self._remove_selected_instance)
        m_edit.addAction(act_del)

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

        # Appearance submenu (Dark / Light / System)
        m_theme = m_view.addMenu("Appearance")
        theme_grp = QActionGroup(self)
        theme_grp.setExclusive(True)
        for mode_key, label in (
            ("dark", "Dark Mode"),
            ("light", "Light Mode"),
            ("system", "System Mode"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, m=mode_key: self.set_theme_mode(m))
            theme_grp.addAction(act)
            m_theme.addAction(act)
            self._theme_actions[mode_key] = act

        m_view.addSeparator()

        for label, key in (
            ("Camera: Isometric", "iso"),
            ("Camera: Top", "top"),
            ("Camera: Bottom (Underside)", "bottom"),
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
    # Theme Switching (Dark / Light / System)
    # -------------------------------------------------------------------------

    def set_theme_mode(self, mode: str, save: bool = True) -> None:
        m = mode.strip().lower() if mode and mode.strip().lower() in THEME_MODES else "dark"
        self.theme_mode = m
        self.resolved_theme = resolve_theme_mode(m)
        if save:
            save_theme_preference(m)

        self.setStyleSheet(build_stylesheet(self.resolved_theme))
        pal = get_palette(self.resolved_theme)

        self.plater.apply_theme(self.resolved_theme)
        for tab in self.settings_tabs.values():
            tab.apply_theme(self.resolved_theme)

        # Update top-level tab icons
        for tab_idx, icon_name in self._tab_icon_names.items():
            self.tabs.setTabIcon(tab_idx, make_icon(icon_name, pal.icon_fg, pal.icon_accent, 15))

        # Update theme switcher buttons & menu checkmarks
        icon_map = {"dark": "theme_dark", "light": "theme_light", "system": "theme_system"}
        for key, btn in self._theme_buttons.items():
            checked = key == m
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)
            fg = pal.accent_fg if checked else pal.icon_fg
            ac = pal.accent_fg if checked else pal.icon_accent
            btn.setIcon(make_icon(icon_map[key], fg, ac, 14))
        for key, act in self._theme_actions.items():
            act.blockSignals(True)
            act.setChecked(key == m)
            act.blockSignals(False)

    def _on_system_color_scheme_changed(self, *_args) -> None:
        if self.theme_mode == "system":
            self.set_theme_mode("system", save=False)

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
        self.plater.viewport.set_bed_size(p.bed_size_x, p.bed_size_y, p.bed_size_z, bed_shape=p.bed_shape)
        self.plater.update_mode_badge(self.cfg)
        self._sync_wipe_tower_preview()

    def _sync_wipe_tower_preview(self) -> None:
        f = self.cfg.fff
        plate_mesh = self.scene.active_plate.combined_mesh()
        h = float(plate_mesh.bounds[1, 2]) if plate_mesh is not None else 20.0
        self.plater.viewport.set_wipe_tower_preview(
            enabled=f.wipe_tower_enable,
            x=f.wipe_tower_x,
            y=f.wipe_tower_y,
            w=f.wipe_tower_width_mm,
            d=f.wipe_tower_depth_mm,
            h=max(6.0, h),
        )

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
    # Multi-Plate & Multi-Instance Scene Management
    # -------------------------------------------------------------------------

    def _sync_scene_ui(self, fit_camera: bool = False, refresh_slice_view: bool = True) -> None:
        plate = self.scene.active_plate
        self.plater.update_plates(self.scene.plates, self.scene.active_plate_idx)
        self.plater.update_plate_objects(plate.instances, self.scene.selected_instance_id)

        capsules = plate.slice_result.capsules if plate.slice_result is not None else None
        self.plater.viewport.set_workspace_plates(
            self.scene.plates,
            self.scene.active_plate_idx,
            self.scene.selected_instance_id,
            capsules=capsules,
        )
        self._sync_wipe_tower_preview()

        if fit_camera:
            comb = plate.combined_mesh()
            if comb is not None:
                self.plater.viewport.fit_to_mesh(comb)
            else:
                self.plater.viewport.fit_to_bed()

        if refresh_slice_view:
            self.plater.set_slice_result(
                plate.slice_result, plate.preview_data, switch_to_preview=False
            )
            if plate.slice_result is not None:
                self.schedule_view.populate(plate.slice_result)
            if plate.gcode_text:
                self.console_view.set_gcode_preview(plate.gcode_text)

    def _on_plate_selected(self, idx: int) -> None:
        if idx == self.scene.active_plate_idx:
            return
        self.scene.set_active_plate(idx)
        plate = self.scene.active_plate
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        if not plate.is_sliced:
            self.plater.set_view_mode("editor")
        self.plater.lbl_status.setText(
            f"Switched to {plate.name} ({len(plate.instances)} object(s))."
        )

    def _add_plate(self) -> None:
        plate = self.scene.add_plate()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.set_progress(0, f"Created {plate.name}. Add models to populate this print bed.")

    def _remove_active_plate(self) -> None:
        if len(self.scene.plates) <= 1:
            return
        removed_name = self.scene.active_plate.name
        self.scene.remove_plate(self.scene.active_plate_idx)
        self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.lbl_status.setText(f"Removed {removed_name}.")

    def _add_mesh_to_active_plate(
        self, mesh: trimesh.Trimesh, name: str, fit_camera: bool = False
    ) -> None:
        p = self.cfg.printer
        inst = self.scene.add_mesh(mesh, name, p.bed_size_x, p.bed_size_y, auto_place=True)
        self._sync_scene_ui(fit_camera=fit_camera, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.set_progress(0, f"Added {inst.name} to {self.scene.active_plate.name}.")
        self.console_view.append_log(
            f"Added model '{inst.name}' to {self.scene.active_plate.name} ({len(inst.base_mesh.faces):,} faces)"
        )

    def _on_instance_selected(self, inst_id: str) -> None:
        if self.scene.active_plate.get_instance(inst_id) is None:
            return
        self.scene.selected_instance_id = inst_id
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=False)

    def _on_selection_cleared(self) -> None:
        if self.scene.selected_instance_id is None:
            return
        self.scene.selected_instance_id = None
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=False)
        self.plater.lbl_status.setText("Selection cleared.")

    def _duplicate_selected_instance(self) -> None:
        p = self.cfg.printer
        clone = self.scene.duplicate_selected(p.bed_size_x, p.bed_size_y)
        if clone is None:
            return
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.lbl_status.setText(f"Created instance '{clone.name}'.")

    def _remove_selected_instance(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        removed = inst.name
        if self.scene.remove_selected():
            self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
            self.plater.set_view_mode("editor")
            self.plater.lbl_status.setText(f"Removed '{removed}' from {self.scene.active_plate.name}.")

    def _move_selected_to_plate(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        p = self.cfg.printer
        if len(self.scene.plates) == 1:
            # Automatically create Plate 2 and move selected object there
            src_idx = self.scene.active_plate_idx
            new_plate = self.scene.add_plate()
            self.scene.set_active_plate(src_idx)
            self.scene.selected_instance_id = inst.instance_id
            target_idx = len(self.scene.plates) - 1
            self.scene.move_selected_to_plate(target_idx, p.bed_size_x, p.bed_size_y)
            self.scene.set_active_plate(target_idx)
            self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)
            self.plater.lbl_status.setText(f"Moved '{inst.name}' to {new_plate.name}.")
            return

        choices = [
            f"{i + 1}: {pl.name}"
            for i, pl in enumerate(self.scene.plates)
            if i != self.scene.active_plate_idx
        ]
        choice, ok = QInputDialog.getItem(
            self, "Move Object to Build Plate", "Select destination plate:", choices, 0, False
        )
        if not ok or not choice:
            return
        target_idx = int(choice.split(":", 1)[0]) - 1
        if self.scene.move_selected_to_plate(target_idx, p.bed_size_x, p.bed_size_y):
            self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
            self.plater.lbl_status.setText(
                f"Moved '{inst.name}' to {self.scene.plates[target_idx].name}."
            )

    def _arrange_active_plate(self) -> None:
        p = self.cfg.printer
        self.scene.active_plate.auto_arrange(p.bed_size_x, p.bed_size_y)
        self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)
        self.plater.lbl_status.setText(f"Auto-arranged {self.scene.active_plate.name}.")

    def _center_selected_instance(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_x = self.cfg.printer.bed_size_x * 0.5
        inst.pos_y = self.cfg.printer.bed_size_y * 0.5
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _drop_selected_instance(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_z = 0.0
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _reset_selected_transform(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.reset_transform(self.cfg.printer.bed_size_x * 0.5, self.cfg.printer.bed_size_y * 0.5)
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)

    def _quick_rotate_selected(self, axis: str, delta_deg: float) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        if axis == "x":
            inst.rot_x = ((inst.rot_x + delta_deg + 180.0) % 360.0) - 180.0
        elif axis == "y":
            inst.rot_y = ((inst.rot_y + delta_deg + 180.0) % 360.0) - 180.0
        else:
            inst.rot_z = ((inst.rot_z + delta_deg + 180.0) % 360.0) - 180.0
        inst.pos_z = 0.0
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _on_plater_transform_changed(
        self,
        px: float,
        py: float,
        pz: float,
        rx: float,
        ry: float,
        rz: float,
        sx: float,
        sy: float,
        sz: float,
        uniform: bool,
    ) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_x = px
        inst.pos_y = py
        inst.pos_z = pz
        inst.rot_x = rx
        inst.rot_y = ry
        inst.rot_z = rz
        inst.scale_x = sx
        inst.scale_y = sy
        inst.scale_z = sz
        inst.uniform_scale = uniform
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _on_viewport_instance_moved(self, inst_id: str, nx: float, ny: float) -> None:
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is None:
            return
        inst.pos_x = round(nx, 2)
        inst.pos_y = round(ny, 2)
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        self.plater.update_selected_instance_inspector(inst)

    def _on_viewport_instance_moved_z(self, inst_id: str, nz: float) -> None:
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is None:
            return
        inst.pos_z = round(max(0.0, nz), 2)
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        self.plater.update_selected_instance_inspector(inst)

    def _on_viewport_instance_rotated(self, inst_id: str, rx: float, ry: float, rz: float) -> None:
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is None:
            return
        inst.rot_x = rx
        inst.rot_y = ry
        inst.rot_z = rz
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        self.plater.update_selected_instance_inspector(inst)

    def _on_viewport_instance_scaled(self, inst_id: str, new_scale: float) -> None:
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is None:
            return
        inst.scale_x = new_scale
        inst.scale_y = new_scale
        inst.scale_z = new_scale
        inst.uniform_scale = True
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        self.plater.update_selected_instance_inspector(inst)

    def _on_viewport_face_picked(self, normal: np.ndarray) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.align_normal_to_bed(normal)
        self.scene.active_plate.clear_slice()
        self.plater.set_edit_tool("select")
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.lbl_status.setText(f"Placed '{inst.name}' flat on selected face (Z = 0).")

    def _on_cut_performed(
        self,
        ox: float,
        oy: float,
        oz: float,
        nx: float,
        ny: float,
        nz: float,
        keep_upper: bool,
        keep_lower: bool,
        place_on_bed: bool,
    ) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        orig_name = inst.name
        p = self.cfg.printer
        created = self.scene.cut_selected_instance(
            plane_origin=np.array([ox, oy, oz], dtype=np.float64),
            plane_normal=np.array([nx, ny, nz], dtype=np.float64),
            bed_x=p.bed_size_x,
            bed_y=p.bed_size_y,
            keep_upper=keep_upper,
            keep_lower=keep_lower,
            place_upper_on_bed=place_on_bed,
            place_lower_on_bed=place_on_bed,
        )
        if not created:
            QMessageBox.information(
                self,
                "Cut Plane",
                "The cutting plane does not intersect the selected model. Adjust plane position or angle.",
            )
            return
        self.plater.set_edit_tool("select")
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        names = ", ".join(c.name for c in created)
        self.plater.lbl_status.setText(f"Cut '{orig_name}' into {len(created)} watertight part(s): {names}.")
        self.console_view.append_log(f"Plane cut '{orig_name}' -> {names}")

    def _on_viewport_support_painted(
        self, inst_id: str, hx: float, hy: float, hz: float, radius: float, val: int
    ) -> None:
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is None:
            return
        changed = inst.paint_faces_in_sphere(np.array([hx, hy, hz], dtype=np.float64), radius, val)
        if changed <= 0:
            return
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        enf, blk = inst.paint_counts()
        self.plater.update_paint_counts(enf, blk)
        self.plater.lbl_status.setText(
            f"Painted '{inst.name}' — Enforcers: {enf} faces, Blockers: {blk} faces."
        )

    def _on_paint_smart_overhangs(self, angle_deg: float) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        n_marked = inst.smart_paint_overhangs(overhang_angle_deg=angle_deg)
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        enf, blk = inst.paint_counts()
        self.plater.update_paint_counts(enf, blk)
        self.plater.lbl_status.setText(
            f"Smart-painted {n_marked} overhang faces (> {angle_deg:.0f}°) on '{inst.name}'."
        )

    def _on_paint_clear(self) -> None:
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.clear_paint()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.update_paint_counts(0, 0)
        self.plater.lbl_status.setText(f"Cleared painted support regions on '{inst.name}'.")

    def _open_model_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add 3D Model",
            "",
            "3D Models (*.stl *.obj *.3mf *.ply *.step *.stp);;All Files (*)",
        )
        if not path:
            return
        try:
            mesh = load_and_center_mesh(path, self.cfg)
            self._add_mesh_to_active_plate(mesh, Path(path).name, fit_camera=True)
        except Exception as exc:
            QMessageBox.critical(self, "Model Load Error", str(exc))

    def _load_test_sphere(self, fit_camera: bool = False) -> None:
        mesh = create_test_sphere_mesh(self.cfg, radius=15.0)
        self._add_mesh_to_active_plate(mesh, "Test Sphere (30 mm)", fit_camera=fit_camera)

    def _load_test_wall(self, fit_camera: bool = False) -> None:
        mesh = create_test_curved_wall_mesh(self.cfg)
        self._add_mesh_to_active_plate(mesh, "Test Curved Wall", fit_camera=fit_camera)

    # -------------------------------------------------------------------------
    # Slicing (Single Plate & Sequential Multi-Plate) & G-Code Preview / Export
    # -------------------------------------------------------------------------

    def _collect_full_config(self) -> SlicerConfig:
        for tab in self.settings_tabs.values():
            tab.write_to_config(self.cfg)
        return self.cfg

    def _start_slicing_active_plate(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        plate = self.scene.active_plate
        if not plate.instances:
            QMessageBox.warning(self, "Empty Plate", f"{plate.name} has no models to slice.")
            return
        self._slice_queue = [self.scene.active_plate_idx]
        self.console_view.clear_log()
        self.tabs.setCurrentIndex(0)
        self._launch_next_queued_plate()

    def _start_slicing_all_plates(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        queue = [i for i, pl in enumerate(self.scene.plates) if pl.instances]
        if not queue:
            QMessageBox.warning(self, "No Models", "Add at least one 3D model before slicing.")
            return
        self._slice_queue = queue
        self.console_view.clear_log()
        self.tabs.setCurrentIndex(0)
        self._launch_next_queued_plate()

    def _launch_next_queued_plate(self) -> None:
        if not self._slice_queue:
            return
        idx = self._slice_queue.pop(0)
        self._slicing_plate_idx = idx
        plate = self.scene.plates[idx]
        mesh = plate.combined_mesh()
        if mesh is None:
            self._launch_next_queued_plate()
            return

        cfg = self._collect_full_config()
        self.plater.set_slicing_busy(True, f"Slicing {plate.name} ({len(plate.instances)} object(s))...")
        self.console_view.append_log(
            f"=== Slicing {plate.name}: {', '.join(i.name for i in plate.instances)} ==="
        )

        self.worker = SliceWorker(mesh, cfg)
        self.worker.progress.connect(
            lambda pct, msg, pname=plate.name: self.plater.set_progress(pct, f"[{pname}] {msg}")
        )
        self.worker.log_msg.connect(self.console_view.append_log)
        self.worker.finished_ok.connect(self._on_slice_done)
        self.worker.failed.connect(self._on_slice_failed)
        self.worker.start()

    def _on_slice_done(self, res: SliceResult) -> None:
        plate = self.scene.plates[self._slicing_plate_idx]
        plate.slice_result = res
        plate.gcode_text = res.gcode_text
        plate.preview_data = parse_gcode(res.gcode_text)

        mode_txt = "Standard FFF" if self.cfg.hybrid.is_fff_only else "Hybrid"
        self.console_view.append_log(
            f"--- {plate.name} COMPLETE ({mode_txt}) | {len(res.layers)} layers, "
            f"{len(res.schedule)} cure cycles, {res.total_resin_ml:.2f} mL resin, "
            f"{res.total_filament_mm / 1000.0:.2f} m filament ---"
        )

        self.plater.update_plates(self.scene.plates, self.scene.active_plate_idx)

        if self._slice_queue:
            self._launch_next_queued_plate()
            return

        # Finished active / final plate in queue
        active_plate = self.scene.active_plate
        self.plater.set_slicing_busy(
            False, f"{active_plate.name} sliced! Inspect toolpaths or export G-Code."
        )
        self.plater.progress_bar.setValue(100)
        self.plater.set_slice_result(
            active_plate.slice_result, active_plate.preview_data, switch_to_preview=True
        )
        if active_plate.slice_result is not None:
            self.schedule_view.populate(active_plate.slice_result)
        if active_plate.gcode_text:
            self.console_view.set_gcode_preview(active_plate.gcode_text)

    def _on_slice_failed(self, err_tb: str) -> None:
        self._slice_queue.clear()
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
            plate = self.scene.active_plate
            plate.slice_result = None
            plate.gcode_text = text
            plate.preview_data = preview
            self.plater.update_plates(self.scene.plates, self.scene.active_plate_idx)
            self.plater.set_slice_result(None, preview, switch_to_preview=True)
            self.console_view.set_gcode_preview(text)
            self.plater.set_progress(
                100, f"Loaded G-code: {Path(path).name} ({preview.num_layers} layers)"
            )
            self.tabs.setCurrentIndex(0)
        except Exception as exc:
            QMessageBox.critical(self, "Open G-Code Error", str(exc))

    def _export_gcode(self) -> None:
        plate = self.scene.active_plate
        text = plate.gcode_text
        if not text:
            return
        base = plate.instances[0].source_name if plate.instances else plate.name
        stem = f"{Path(base).stem}_{plate.name}"
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem).strip("_") or "alveo_print"
        default_name = f"{safe_stem}.gcode"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export G-Code ({plate.name})", default_name, "G-Code Files (*.gcode);;All Files (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.plater.lbl_status.setText(f"Exported {plate.name} G-code to {Path(path).name}")
            self.console_view.append_log(f"Saved G-Code ({plate.name}): {path}")
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
            cfg.to_json(path)
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
            loaded = SlicerConfig.from_json(path)
            self.cfg = loaded
            for cat, tab in self.settings_tabs.items():
                tab.load_from_config(self.cfg)
                modified = self.preset_manager.is_modified(self.cfg, cat, tab.active_preset_name)
                self.plater.set_preset_modified(cat, modified)
            self._sync_bed_and_mode()
            self.console_view.append_log(f"Imported full config JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Import Config Error", str(exc))
