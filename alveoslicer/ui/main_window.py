"""Main PrusaSlicer-style desktop window orchestrating multi-plate Plater, Settings, Presets, and Slicing.

:class:`AlveoSlicerWindow` connects the headless slicing engine and multi-plate
:class:`WorkspaceScene` to the 3D Plater tab, four categorized PrusaSlicer-style
settings tabs, Cure Schedule & Pipeline Log view, Dark/Light/System theme
switcher, and background :class:`SliceWorker`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import trimesh
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
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

from ..assets import get_app_icon_path
from ..core.config import SlicerConfig
from ..core.geometry import (
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    load_and_center_mesh,
)
from ..core.presets import CATEGORY_LABELS, PRESET_CATEGORIES, PresetManager
from ..core.scene import ModelInstance, WorkspaceScene
from ..gcode.parser import GCodePreviewData, parse_gcode
from ..pipeline import SliceResult
from .plater import PlaterView
from .settings import (
    CategorySettingsTab,
    build_filament_settings_tab,
    build_print_settings_tab,
    build_printer_settings_tab,
    build_resin_settings_tab,
)
from .styling import (
    EXPERIENCE_MODES,
    THEME_MODES,
    build_stylesheet,
    get_palette,
    load_experience_mode,
    load_theme_preference,
    make_icon,
    resolve_theme_mode,
    save_experience_mode,
    save_theme_preference,
)
from .views import ConsoleGCodeView, CureScheduleView
from .worker import SliceWorker


class AlveoSlicerWindow(QMainWindow):
    """Top-level desktop window for AlveoSlicer's hybrid FFF + UV photopolymer slicer."""

    def __init__(self, preset_manager: Optional[PresetManager] = None) -> None:
        """Initialize scene, config, presets, tabs, menus, theme, and default test wall."""
        super().__init__()
        self.setWindowTitle("AlveoSlicer — Hybrid FFF + Photopolymer Slicer")
        icon_path = get_app_icon_path()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1540, 950)

        self.preset_manager = preset_manager or PresetManager()
        self.cfg = SlicerConfig()
        self.scene = WorkspaceScene()

        self.theme_mode: str = load_theme_preference()
        self.resolved_theme: str = resolve_theme_mode(self.theme_mode)
        self.experience_mode: str = load_experience_mode()
        self._theme_buttons: Dict[str, QPushButton] = {}
        self._theme_actions: Dict[str, QAction] = {}
        self._mode_buttons: Dict[str, QPushButton] = {}
        self._mode_actions: Dict[str, QAction] = {}
        self._tab_icon_names: Dict[int, str] = {}

        # Slicing worker & multi-plate queue state
        self.worker: Optional[SliceWorker] = None
        self._slicing_plate_idx: int = 0
        self._slice_queue: List[int] = []
        self._syncing_mode: bool = False
        self._last_hybrid_state: Optional[bool] = None

        self._build_ui()
        self._build_menu_bar()
        self._init_presets_and_config()
        self.set_experience_mode(self.experience_mode, save=False)
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
    # Compatibility Properties (Active Plate Shortcuts)
    # -------------------------------------------------------------------------

    @property
    def current_mesh(self) -> Optional[trimesh.Trimesh]:
        """Return the combined world-space mesh on the currently active build plate."""
        return self.scene.active_plate.combined_mesh()

    @property
    def slice_result(self) -> Optional[SliceResult]:
        """Return the cached :class:`SliceResult` of the currently active build plate."""
        return self.scene.active_plate.slice_result

    @property
    def preview_data(self) -> Optional[GCodePreviewData]:
        """Return the cached :class:`GCodePreviewData` of the currently active build plate."""
        return self.scene.active_plate.preview_data

    # -------------------------------------------------------------------------
    # UI & Menu Construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the central tab widget (Plater, 5 Settings tabs, Schedule & Log) and theme bar."""
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
        self._connect_plater_signals()
        plater_idx = self.tabs.addTab(self.plater, "Plater")
        self._tab_icon_names[plater_idx] = "view_3d"

        # Tabs 1..4: Per-category Settings Tabs (Print Settings includes Hybrid & UV)
        builders = {
            "print": build_print_settings_tab,
            "filament": build_filament_settings_tab,
            "resin": build_resin_settings_tab,
            "printer": build_printer_settings_tab,
        }
        cat_icons = {
            "print": "layers",
            "filament": "filament",
            "resin": "resin",
            "printer": "printer",
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
        self._sched_tab_idx: int = sched_idx
        self._tab_icon_names[sched_idx] = "gcode"
        self.tabs.setIconSize(QSize(15, 15))

        # Top-Right Controls: Simple/Advanced Pill + Dark/Light/System Theme Pill
        corner_bar = QWidget()
        cb_row = QHBoxLayout(corner_bar)
        cb_row.setContentsMargins(6, 3, 10, 3)
        cb_row.setSpacing(8)

        mode_pill = QFrame()
        mode_pill.setObjectName("segmentedPill")
        mp_row = QHBoxLayout(mode_pill)
        mp_row.setContentsMargins(2, 2, 2, 2)
        mp_row.setSpacing(2)
        for m_key, m_label, m_tip in (
            ("simple", "Simple", "Show essential settings for quick day-to-day slicing"),
            ("advanced", "Advanced", "Show all calibration, hardware, and DP scheduler settings"),
        ):
            btn = QPushButton(m_label)
            btn.setObjectName("modeBtn")
            btn.setCheckable(True)
            btn.setToolTip(m_tip)
            btn.clicked.connect(lambda _c=False, m=m_key: self.set_experience_mode(m))
            mp_row.addWidget(btn)
            self._mode_buttons[m_key] = btn
        cb_row.addWidget(mode_pill)

        theme_pill = QFrame()
        theme_pill.setObjectName("segmentedPill")
        tb_row = QHBoxLayout(theme_pill)
        tb_row.setContentsMargins(2, 2, 2, 2)
        tb_row.setSpacing(2)
        for mode_key, label, tip in (
            ("dark", "Dark", "Switch to Dark Mode"),
            ("light", "Light", "Switch to Light Mode"),
            ("system", "System", "Follow operating system Light/Dark theme"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("themeBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setIconSize(QSize(13, 13))
            btn.clicked.connect(lambda _c=False, m=mode_key: self.set_theme_mode(m))
            tb_row.addWidget(btn)
            self._theme_buttons[mode_key] = btn
        cb_row.addWidget(theme_pill)

        self.tabs.setCornerWidget(corner_bar, Qt.Corner.TopRightCorner)
        root.addWidget(self.tabs)

    def _connect_plater_signals(self) -> None:
        """Wire all Plater and 3D Viewport signals to their main-window controllers."""
        p = self.plater
        vp = p.viewport
        p.plate_selected.connect(self._on_plate_selected)
        p.add_plate_requested.connect(self._add_plate)
        p.remove_plate_requested.connect(self._remove_active_plate)

        p.add_model_requested.connect(self._open_model_dialog)
        p.load_sphere_requested.connect(lambda: self._load_test_sphere(fit_camera=False))
        p.load_wall_requested.connect(lambda: self._load_test_wall(fit_camera=False))
        p.instance_selected.connect(self._on_instance_selected)
        p.duplicate_instance_requested.connect(self._duplicate_selected_instance)
        p.remove_instance_requested.connect(self._remove_selected_instance)
        p.move_to_plate_requested.connect(self._move_selected_to_plate)
        p.arrange_plate_requested.connect(self._arrange_active_plate)
        p.center_model_requested.connect(self._center_selected_instance)
        p.drop_model_requested.connect(self._drop_selected_instance)
        p.reset_transform_requested.connect(self._reset_selected_transform)
        p.instance_transform_changed.connect(self._on_plater_transform_changed)
        p.quick_rotate_requested.connect(self._quick_rotate_selected)

        vp.instance_selected.connect(self._on_instance_selected)
        vp.selection_cleared.connect(self._on_selection_cleared)
        vp.plate_selected.connect(self._on_plate_selected)
        vp.instance_moved.connect(self._on_viewport_instance_moved)
        vp.instance_moved_z.connect(self._on_viewport_instance_moved_z)
        vp.instance_rotated.connect(self._on_viewport_instance_rotated)
        vp.instance_scaled.connect(self._on_viewport_instance_scaled)
        vp.face_picked.connect(self._on_viewport_face_picked)
        vp.support_painted.connect(self._on_viewport_support_painted)

        p.cut_performed.connect(self._on_cut_performed)
        p.paint_smart_overhangs_requested.connect(self._on_paint_smart_overhangs)
        p.paint_clear_requested.connect(self._on_paint_clear)

        p.preset_selected.connect(self._on_preset_selected)
        p.open_settings_requested.connect(self._focus_settings_tab)
        p.slice_requested.connect(self._start_slicing_active_plate)
        p.slice_all_plates_requested.connect(self._start_slicing_all_plates)
        p.export_gcode_requested.connect(self._export_gcode)

    def _build_menu_bar(self) -> None:
        """Build File, Edit, Presets, and View menus with keyboard shortcuts."""
        mb = self.menuBar()

        def _add_act(menu, label: str, slot, shortcut=None) -> QAction:
            act = QAction(label, self)
            if shortcut is not None:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            menu.addAction(act)
            return act

        # ---- File Menu ----
        m_file = mb.addMenu("&File")
        _add_act(m_file, "Add 3D Model...", self._open_model_dialog, QKeySequence.StandardKey.Open)
        _add_act(m_file, "New Build Plate", self._add_plate, "Ctrl+N")
        _add_act(m_file, "Open External G-Code...", self._open_external_gcode, "Ctrl+G")
        _add_act(m_file, "Export Active Plate G-Code...", self._export_gcode, "Ctrl+E")
        m_file.addSeparator()
        _add_act(m_file, "Import Config JSON...", self._import_config_json)
        _add_act(m_file, "Export Config JSON...", self._export_config_json)
        m_file.addSeparator()
        _add_act(m_file, "Exit", self.close, QKeySequence.StandardKey.Quit)

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
            _add_act(m_edit, label, lambda _chk=False, t=tool_name: self.plater.set_edit_tool(t), shortcut)
        m_edit.addSeparator()
        _add_act(m_edit, "Duplicate Instance", self._duplicate_selected_instance, "Ctrl+D")
        _add_act(m_edit, "Auto-Arrange Plate", self._arrange_active_plate, "A")
        _add_act(m_edit, "Delete Selected Object", self._remove_selected_instance, QKeySequence.StandardKey.Delete)

        # ---- Presets Menu ----
        m_presets = mb.addMenu("&Presets")
        for cat in PRESET_CATEGORIES:
            _add_act(
                m_presets,
                f"Edit {CATEGORY_LABELS[cat]}...",
                lambda _checked=False, c=cat: self._focus_settings_tab(c),
            )

        # ---- View Menu ----
        m_view = mb.addMenu("&View")
        _add_act(m_view, "3D Editor", lambda: self._switch_plater_view("editor"), "Ctrl+1")
        _add_act(m_view, "G-Code Preview", lambda: self._switch_plater_view("preview"), "Ctrl+2")
        _add_act(m_view, "2D Basins", lambda: self._switch_plater_view("basins"), "Ctrl+3")
        m_view.addSeparator()

        m_theme = m_view.addMenu("Appearance")
        theme_grp = QActionGroup(self)
        theme_grp.setExclusive(True)
        for mode_key, label in (("dark", "Dark Mode"), ("light", "Light Mode"), ("system", "System Mode")):
            act = _add_act(m_theme, label, lambda _checked=False, m=mode_key: self.set_theme_mode(m))
            act.setCheckable(True)
            theme_grp.addAction(act)
            self._theme_actions[mode_key] = act

        m_exp = m_view.addMenu("Settings Mode")
        exp_grp = QActionGroup(self)
        exp_grp.setExclusive(True)
        for exp_key, label in (("simple", "Simple"), ("advanced", "Advanced")):
            act = _add_act(m_exp, label, lambda _checked=False, m=exp_key: self.set_experience_mode(m))
            act.setCheckable(True)
            exp_grp.addAction(act)
            self._mode_actions[exp_key] = act

        m_view.addSeparator()
        for label, key in (
            ("Camera: Isometric", "iso"),
            ("Camera: Top", "top"),
            ("Camera: Bottom (Underside)", "bottom"),
            ("Camera: Front", "front"),
            ("Camera: Right", "right"),
        ):
            _add_act(m_view, label, lambda _checked=False, k=key: self.plater.viewport.set_camera_preset(k))
        _add_act(m_view, "Camera: Fit Build Plate", self.plater.viewport.fit_to_bed)

    # -------------------------------------------------------------------------
    # Theme Switching (Dark / Light / System)
    # -------------------------------------------------------------------------

    def set_experience_mode(self, mode: str, save: bool = True) -> None:
        """Switch settings tabs between ``simple`` and ``advanced`` complexity levels."""
        m = mode.strip().lower() if mode and mode.strip().lower() in EXPERIENCE_MODES else "simple"
        self.experience_mode = m
        if save:
            save_experience_mode(m)
        for tab in self.settings_tabs.values():
            tab.set_experience_mode(m)
        for key, btn in self._mode_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == m)
            btn.blockSignals(False)
        for key, act in self._mode_actions.items():
            act.blockSignals(True)
            act.setChecked(key == m)
            act.blockSignals(False)

    def set_theme_mode(self, mode: str, save: bool = True) -> None:
        """Apply ``dark``, ``light``, or ``system`` UI theme across window, viewport, and icons."""
        m = mode.strip().lower() if mode and mode.strip().lower() in THEME_MODES else "dark"
        self.theme_mode = m
        self.resolved_theme = resolve_theme_mode(m)
        if save:
            save_theme_preference(m)

        self.setStyleSheet(build_stylesheet(self.resolved_theme))
        pal = get_palette(self.resolved_theme)

        self.plater.apply_theme(self.resolved_theme)
        if hasattr(self.schedule_view, "apply_theme"):
            self.schedule_view.apply_theme(self.resolved_theme)
        for tab in self.settings_tabs.values():
            tab.apply_theme(self.resolved_theme)

        for tab_idx, icon_name in self._tab_icon_names.items():
            self.tabs.setTabIcon(tab_idx, make_icon(icon_name, pal.icon_fg, pal.icon_accent, 15))

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
        """React to live OS dark/light theme changes when ``theme_mode == 'system'``."""
        if self.theme_mode == "system":
            self.set_theme_mode("system", save=False)

    # -------------------------------------------------------------------------
    # Preset & Config Synchronization
    # -------------------------------------------------------------------------

    def _init_presets_and_config(self) -> None:
        """Load the default built-in preset for each of the four settings categories."""
        for cat in PRESET_CATEGORIES:
            default_name = self.preset_manager.default_preset_name(cat)
            self.preset_manager.apply_preset(self.cfg, cat, default_name)
            tab = self.settings_tabs[cat]
            tab.refresh_preset_list(select_name=default_name)
            tab.load_from_config(self.cfg)
            self.plater.refresh_preset_dropdown(cat, default_name, is_modified=False)
        self._sync_bed_and_mode()

    @staticmethod
    def _is_fff_only_print_preset(preset: Any) -> bool:
        """Return ``True`` for user presets or built-in Standard FFF-only print presets."""
        if not getattr(preset, "builtin", False):
            return True
        vals = getattr(preset, "values", {})
        return (
            not bool(vals.get("hybrid.enable_resin_pumping", True))
            and not bool(vals.get("hybrid.enable_uv_curing", True))
            and str(vals.get("infill.infill_engine", "surface_normal_3d")) == "standard"
        )

    def _sync_bed_and_mode(self) -> None:
        """Sync bed dimensions, Hybrid vs FFF-only capability visibility, presets, and wipe tower."""
        if self._syncing_mode:
            return
        self._syncing_mode = True
        try:
            is_hybrid = self.cfg.is_hybrid_printer
            if self._last_hybrid_state != is_hybrid:
                self._last_hybrid_state = is_hybrid
                resin_idx = self._category_tab_indices.get("resin")
                if resin_idx is not None:
                    if (not is_hybrid) and self.tabs.currentIndex() == resin_idx:
                        self.tabs.setCurrentIndex(0)
                    self.tabs.setTabVisible(resin_idx, is_hybrid)

                self.schedule_view.setVisible(is_hybrid)
                self.tabs.setTabText(self._sched_tab_idx, "Schedule && Log" if is_hybrid else "Log && G-Code")
                for tab in self.settings_tabs.values():
                    tab.set_hybrid_enabled(is_hybrid)
                self.plater.set_hybrid_mode(is_hybrid)

                print_tab = self.settings_tabs["print"]
                if not is_hybrid:
                    pred = self._is_fff_only_print_preset
                    cur_pname = print_tab.active_preset_name
                    cur_preset = self.preset_manager.get_preset("print", cur_pname)
                    switched = False
                    if cur_preset is None or not pred(cur_preset):
                        fallback = "0.20mm STANDARD FFF (Gyroid 15%, No Resin/UV)"
                        if self.preset_manager.get_preset("print", fallback) is not None:
                            self.preset_manager.apply_preset(self.cfg, "print", fallback)
                            cur_pname = fallback
                            switched = True
                    print_tab.set_preset_filter(pred, select_name=cur_pname)
                    if switched:
                        print_tab.load_from_config(self.cfg)
                    self.plater.set_preset_filter("print", pred)
                    self.plater.refresh_preset_dropdown(
                        "print",
                        cur_pname,
                        is_modified=self.preset_manager.is_modified(self.cfg, "print", cur_pname),
                    )
                else:
                    cur_pname = print_tab.active_preset_name
                    print_tab.set_preset_filter(None, select_name=cur_pname)
                    self.plater.set_preset_filter("print", None)
                    self.plater.refresh_preset_dropdown(
                        "print",
                        cur_pname,
                        is_modified=self.preset_manager.is_modified(self.cfg, "print", cur_pname),
                    )

            p = self.cfg.printer
            self.plater.viewport.set_bed_size(p.bed_size_x, p.bed_size_y, p.bed_size_z, bed_shape=p.bed_shape)
            self.plater.update_mode_badge(self.cfg)
            self.plater.update_material_properties(self.cfg)
            self._sync_wipe_tower_preview()
        finally:
            self._syncing_mode = False

    def _sync_wipe_tower_preview(self) -> None:
        """Update the 3D wipe tower footprint/height box from active FFF settings and model Z."""
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
        """Apply a selected preset to ``self.cfg`` and refresh both settings tab and Plater."""
        if not self.preset_manager.apply_preset(self.cfg, category, name):
            return
        tab = self.settings_tabs[category]
        tab.select_preset_by_name(name, emit=False)
        tab.load_from_config(self.cfg)
        self.plater.refresh_preset_dropdown(category, name, is_modified=False)
        self._sync_bed_and_mode()

    def _on_tab_values_changed(self, category: str) -> None:
        """Write live widget edits to ``self.cfg`` and refresh modified indicators."""
        tab = self.settings_tabs[category]
        tab.write_to_config(self.cfg)
        tab.update_modified_indicator(self.cfg)
        modified = self.preset_manager.is_modified(self.cfg, category, tab.active_preset_name)
        self.plater.set_preset_modified(category, modified)
        self._sync_bed_and_mode()

    def _on_presets_mutated(self, category: str) -> None:
        """Refresh sidebar preset dropdown after user saves, deletes, or resets a preset."""
        tab = self.settings_tabs[category]
        tab.update_modified_indicator(self.cfg)
        modified = self.preset_manager.is_modified(self.cfg, category, tab.active_preset_name)
        self.plater.refresh_preset_dropdown(category, tab.active_preset_name, is_modified=modified)

    def _focus_settings_tab(self, category: str) -> None:
        """Switch main tab bar to the specified settings category tab."""
        idx = self._category_tab_indices.get(category)
        if idx is not None:
            self.tabs.setCurrentIndex(idx)

    def _switch_plater_view(self, mode: str) -> None:
        """Activate the Plater tab and switch its view mode (``editor``/``preview``/``basins``)."""
        if mode == "basins" and not self.cfg.is_hybrid_printer:
            return
        self.tabs.setCurrentIndex(0)
        self.plater.set_view_mode(mode)

    # -------------------------------------------------------------------------
    # Multi-Plate & Multi-Instance Scene Management
    # -------------------------------------------------------------------------

    def _sync_scene_ui(self, fit_camera: bool = False, refresh_slice_view: bool = True) -> None:
        """Synchronize plate tabs, object list, 3D viewport meshes, and cached slice previews."""
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
            self.plater.set_slice_result(plate.slice_result, plate.preview_data, switch_to_preview=False)
            if plate.slice_result is not None:
                self.schedule_view.populate(plate.slice_result)
            if plate.gcode_text:
                self.console_view.set_gcode_preview(plate.gcode_text)

    def _commit_viewport_drag(self, inst: ModelInstance) -> None:
        """Invalidate transformed cache and refresh viewport/inspector after interactive drag."""
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self.plater.viewport.set_scene_instances(
            self.scene.active_plate.instances, self.scene.selected_instance_id
        )
        self.plater.update_selected_instance_inspector(inst)

    def _on_plate_selected(self, idx: int) -> None:
        """Switch the active build plate and update 3D focus and slice preview state."""
        if idx == self.scene.active_plate_idx:
            return
        self.scene.set_active_plate(idx)
        plate = self.scene.active_plate
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        if not plate.is_sliced:
            self.plater.set_view_mode("editor")
        self.plater.lbl_status.setText(f"Switched to {plate.name} ({len(plate.instances)} object(s)).")

    def _add_plate(self) -> None:
        """Create and activate a new empty build plate."""
        plate = self.scene.add_plate()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.set_progress(0, f"Created {plate.name}. Add models to populate this print bed.")

    def _remove_active_plate(self) -> None:
        """Delete the active build plate when more than one plate exists."""
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
        """Add ``mesh`` to the active build plate, auto-placing it without overlapping."""
        p = self.cfg.printer
        inst = self.scene.add_mesh(mesh, name, p.bed_size_x, p.bed_size_y, auto_place=True)
        self._sync_scene_ui(fit_camera=fit_camera, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.set_progress(0, f"Added {inst.name} to {self.scene.active_plate.name}.")
        self.console_view.append_log(
            f"Added model '{inst.name}' to {self.scene.active_plate.name} ({len(inst.base_mesh.faces):,} faces)"
        )

    def _on_instance_selected(self, inst_id: str) -> None:
        """Select a model instance on the active build plate."""
        if self.scene.active_plate.get_instance(inst_id) is None:
            return
        self.scene.selected_instance_id = inst_id
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=False)

    def _on_selection_cleared(self) -> None:
        """Clear the active model selection when the user clicks empty canvas space."""
        if self.scene.selected_instance_id is None:
            return
        self.scene.selected_instance_id = None
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=False)
        self.plater.lbl_status.setText("Selection cleared.")

    def _duplicate_selected_instance(self) -> None:
        """Clone the currently selected model instance onto the active build plate."""
        p = self.cfg.printer
        clone = self.scene.duplicate_selected(p.bed_size_x, p.bed_size_y)
        if clone is None:
            return
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.set_view_mode("editor")
        self.plater.lbl_status.setText(f"Created instance '{clone.name}'.")

    def _remove_selected_instance(self) -> None:
        """Remove the currently selected model instance from the active build plate."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        removed = inst.name
        if self.scene.remove_selected():
            self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
            self.plater.set_view_mode("editor")
            self.plater.lbl_status.setText(f"Removed '{removed}' from {self.scene.active_plate.name}.")

    def _move_selected_to_plate(self) -> None:
        """Transfer the selected model instance to another build plate (creating one if needed)."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        p = self.cfg.printer
        if len(self.scene.plates) == 1:
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
        """Shelf-pack all instances on the active build plate with clearance margins."""
        p = self.cfg.printer
        self.scene.active_plate.auto_arrange(p.bed_size_x, p.bed_size_y)
        self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)
        self.plater.lbl_status.setText(f"Auto-arranged {self.scene.active_plate.name}.")

    def _center_selected_instance(self) -> None:
        """Center the selected instance in XY on the active build plate."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_x = self.cfg.printer.bed_size_x * 0.5
        inst.pos_y = self.cfg.printer.bed_size_y * 0.5
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _drop_selected_instance(self) -> None:
        """Drop the selected instance flush onto the build plate (Z = 0)."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_z = 0.0
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _reset_selected_transform(self) -> None:
        """Reset position, rotation, and scale of the selected instance to bed-center defaults."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.reset_transform(self.cfg.printer.bed_size_x * 0.5, self.cfg.printer.bed_size_y * 0.5)
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=True, refresh_slice_view=True)

    def _quick_rotate_selected(self, axis: str, delta_deg: float) -> None:
        """Rotate the selected instance by ``delta_deg`` around ``x``, ``y``, or ``z``."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        attr = f"rot_{axis}" if axis in ("x", "y") else "rot_z"
        setattr(inst, attr, ((getattr(inst, attr) + delta_deg + 180.0) % 360.0) - 180.0)
        inst.pos_z = 0.0
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _on_plater_transform_changed(
        self,
        px: float, py: float, pz: float,
        rx: float, ry: float, rz: float,
        sx: float, sy: float, sz: float,
        uniform: bool,
    ) -> None:
        """Apply numeric sidebar transform edits to the selected model instance."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.pos_x, inst.pos_y, inst.pos_z = px, py, pz
        inst.rot_x, inst.rot_y, inst.rot_z = rx, ry, rz
        inst.scale_x, inst.scale_y, inst.scale_z = sx, sy, sz
        inst.uniform_scale = uniform
        inst.invalidate()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)

    def _on_viewport_instance_moved(self, inst_id: str, nx: float, ny: float) -> None:
        """Update instance XY coordinates during an interactive 3D Move drag."""
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is not None:
            inst.pos_x, inst.pos_y = round(nx, 2), round(ny, 2)
            self._commit_viewport_drag(inst)

    def _on_viewport_instance_moved_z(self, inst_id: str, nz: float) -> None:
        """Update instance Z elevation during an interactive 3D Z-arrow drag."""
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is not None:
            inst.pos_z = round(max(0.0, nz), 2)
            self._commit_viewport_drag(inst)

    def _on_viewport_instance_rotated(self, inst_id: str, rx: float, ry: float, rz: float) -> None:
        """Update instance Euler angles during an interactive 3D Rotate drag."""
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is not None:
            inst.rot_x, inst.rot_y, inst.rot_z = rx, ry, rz
            self._commit_viewport_drag(inst)

    def _on_viewport_instance_scaled(self, inst_id: str, new_scale: float) -> None:
        """Update uniform scale percentage during an interactive 3D Scale drag."""
        inst = self.scene.active_plate.get_instance(inst_id)
        if inst is not None:
            inst.scale_x = inst.scale_y = inst.scale_z = new_scale
            inst.uniform_scale = True
            self._commit_viewport_drag(inst)

    def _on_viewport_face_picked(self, normal: np.ndarray) -> None:
        """Rotate the selected instance so the clicked face lies flat on Z = 0."""
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
        ox: float, oy: float, oz: float,
        nx: float, ny: float, nz: float,
        keep_upper: bool, keep_lower: bool, place_on_bed: bool,
    ) -> None:
        """Slice the selected instance along the user-positioned plane into watertight halves."""
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
        """Apply a spherical paint-on-support brush stroke to faces of ``inst_id``."""
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
        """Auto-paint support enforcers on downward-facing triangles steeper than ``angle_deg``."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        n_marked = inst.smart_paint_overhangs(overhang_angle_deg=angle_deg)
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.update_paint_counts(*inst.paint_counts())
        self.plater.lbl_status.setText(
            f"Smart-painted {n_marked} overhang faces (> {angle_deg:.0f}°) on '{inst.name}'."
        )

    def _on_paint_clear(self) -> None:
        """Erase all painted support enforcers and blockers on the selected instance."""
        inst = self.scene.selected_instance
        if inst is None:
            return
        inst.clear_paint()
        self.scene.active_plate.clear_slice()
        self._sync_scene_ui(fit_camera=False, refresh_slice_view=True)
        self.plater.update_paint_counts(0, 0)
        self.plater.lbl_status.setText(f"Cleared painted support regions on '{inst.name}'.")

    def _open_model_dialog(self) -> None:
        """Open a file chooser dialog and import an STL, OBJ, 3MF, PLY, or STEP mesh."""
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
        """Add the built-in 30 mm test sphere mesh to the active plate."""
        self._add_mesh_to_active_plate(
            create_test_sphere_mesh(self.cfg, radius=15.0), "Test Sphere (30 mm)", fit_camera=fit_camera
        )

    def _load_test_wall(self, fit_camera: bool = False) -> None:
        """Add the built-in curved test wall mesh to the active plate."""
        self._add_mesh_to_active_plate(
            create_test_curved_wall_mesh(self.cfg), "Test Curved Wall", fit_camera=fit_camera
        )

    # -------------------------------------------------------------------------
    # Slicing (Single Plate & Sequential Multi-Plate) & G-Code Preview / Export
    # -------------------------------------------------------------------------

    def _collect_full_config(self) -> SlicerConfig:
        """Flush all five settings tabs into ``self.cfg`` before slicing or JSON export."""
        for tab in self.settings_tabs.values():
            tab.write_to_config(self.cfg)
        return self.cfg

    def _start_slicing_active_plate(self) -> None:
        """Queue and start background slicing for the currently active build plate."""
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
        """Queue and sequentially slice every non-empty build plate in the workspace."""
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
        """Pop the next plate index from ``_slice_queue`` and start a :class:`SliceWorker`."""
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

        self.worker = SliceWorker(mesh, cfg, bodies=plate.instance_meshes())
        self.worker.progress.connect(
            lambda pct, msg, pname=plate.name: self.plater.set_progress(pct, f"[{pname}] {msg}")
        )
        self.worker.log_msg.connect(self.console_view.append_log)
        self.worker.finished_ok.connect(self._on_slice_done)
        self.worker.failed.connect(self._on_slice_failed)
        self.worker.start()

    def _on_slice_done(self, res: SliceResult) -> None:
        """Store completed slice artifacts on the plate and transition to G-Code Preview."""
        plate = self.scene.plates[self._slicing_plate_idx]
        plate.slice_result = res
        plate.gcode_text = res.gcode_text
        plate.preview_data = getattr(res, "_preview_data", None) or parse_gcode(res.gcode_text)

        mode_txt = "Standard FFF" if self.cfg.effective_is_fff_only else "Hybrid"
        self.console_view.append_log(
            f"--- {plate.name} COMPLETE ({mode_txt}) | {len(res.layers)} layers, "
            f"{len(res.schedule)} cure cycles, {res.total_resin_ml:.2f} mL resin, "
            f"{res.total_filament_mm / 1000.0:.2f} m filament ---"
        )
        self.plater.update_plates(self.scene.plates, self.scene.active_plate_idx)

        if self._slice_queue:
            self._launch_next_queued_plate()
            return

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
        """Abort remaining queued plates and display the worker traceback on error."""
        self._slice_queue.clear()
        self.plater.set_slicing_busy(False, "Slicing failed. See Schedule & Log tab.")
        self.console_view.append_log("ERROR:\n" + err_tb)
        QMessageBox.critical(
            self,
            "Slicing Error",
            f"The slicer pipeline encountered an error:\n\n{err_tb[-1000:]}",
        )

    def _open_external_gcode(self) -> None:
        """Parse and visualize any external ``.gcode`` file in the 3D G-Code Previewer."""
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
            self.plater.set_progress(100, f"Loaded G-code: {Path(path).name} ({preview.num_layers} layers)")
            self.tabs.setCurrentIndex(0)
        except Exception as exc:
            QMessageBox.critical(self, "Open G-Code Error", str(exc))

    def _export_gcode(self) -> None:
        """Save the active plate's generated G-code text to disk."""
        plate = self.scene.active_plate
        text = plate.gcode_text
        if not text:
            return
        base = plate.instances[0].source_name if plate.instances else plate.name
        stem = f"{Path(base).stem}_{plate.name}"
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem).strip("_") or "alveo_print"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export G-Code ({plate.name})", f"{safe_stem}.gcode", "G-Code Files (*.gcode);;All Files (*)"
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
        """Switch to the Plater tab and jump to ``layer_num`` when clicked in Cure Schedule."""
        self.tabs.setCurrentIndex(0)
        self.plater.jump_to_layer(layer_num)

    # -------------------------------------------------------------------------
    # Full Config JSON Import / Export
    # -------------------------------------------------------------------------

    def _export_config_json(self) -> None:
        """Serialize the complete :class:`SlicerConfig` across all 4 categories to JSON."""
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
        """Load a full :class:`SlicerConfig` from JSON and refresh every settings tab."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Full Config JSON", "", "JSON (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            self.cfg = SlicerConfig.from_json(path)
            for cat, tab in self.settings_tabs.items():
                tab.load_from_config(self.cfg)
                modified = self.preset_manager.is_modified(self.cfg, cat, tab.active_preset_name)
                self.plater.set_preset_modified(cat, modified)
            self._sync_bed_and_mode()
            self.console_view.append_log(f"Imported full config JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Import Config Error", str(exc))
