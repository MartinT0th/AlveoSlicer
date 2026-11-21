"""PrusaSlicer-style Plater workspace tab with floating 3D overlays and G-Code preview controls.

Coordinates the central 3D OpenGL viewport (:class:`AlveoViewport3D`), the 2D
Basin Inspector (:class:`LayerInspector2D`), floating in-canvas tool rail and
camera pill bar, contextual Cut Plane and Paint-on-Supports popovers, dual
vertical layer slider (:class:`LayerRangeBar`), move scrubber, and right sidebar.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.config import SlicerConfig
from ...core.presets import PresetManager
from ...core.scene import BuildPlate, ModelInstance
from ...gcode.parser import GCodePreviewData
from ...pipeline import SliceResult
from ..styling.icons import make_icon
from ..styling.theme import get_palette
from ..viewport import AlveoViewport3D
from ..views.inspector_2d import LayerInspector2D
from .layer_slider import LayerRangeBar
from .panels import (
    build_cut_popover,
    build_legend_card,
    build_paint_popover,
    build_right_sidebar,
    format_duration,
)

_TOOL_HINTS: Dict[str, str] = {
    "select": "Click model: Select  |  Click empty space: Deselect  |  Click another bed: Activate  |  Drag: Orbit",
    "move": "Move (M): Drag RGB arrows (X/Y/Z) or drag model across the build plate  |  Empty bed: Orbit",
    "rotate": "Rotate (R): Drag RGB rings or drag model to rotate (Shift+drag for X/Y)  |  Empty bed: Orbit",
    "scale": "Scale (S): Drag RGB handles or drag model up/right to scale uniformly  |  Empty bed: Orbit",
    "place_on_face": "Place on Face (F): Click any highlighted cyan face on the model to lay it flat on Z = 0",
    "cut": "Cut Plane (C): Drag yellow handle vertically (Shift+drag to tilt) or set exact coordinates, then click Perform Cut",
    "paint_supports": "Paint Supports (L): Left-drag = Enforcer (green)  |  Right-drag = Blocker (red)  |  Shift+drag = Erase",
}


class PlaterView(QWidget):
    """Main 3D Plater workspace combining multi-bed editing, slicing, and G-code inspection."""

    # Build plate signals
    plate_selected = Signal(int)
    add_plate_requested = Signal()
    remove_plate_requested = Signal()

    # Model & instance signals
    add_model_requested = Signal()
    load_sphere_requested = Signal()
    load_wall_requested = Signal()
    instance_selected = Signal(str)
    duplicate_instance_requested = Signal()
    remove_instance_requested = Signal()
    move_to_plate_requested = Signal()
    arrange_plate_requested = Signal()
    center_model_requested = Signal()
    drop_model_requested = Signal()
    reset_transform_requested = Signal()
    instance_transform_changed = Signal(
        float, float, float, float, float, float, float, float, float, bool
    )
    quick_rotate_requested = Signal(str, float)

    # Cut Plane & Paint-on Supports signals
    cut_performed = Signal(float, float, float, float, float, float, bool, bool, bool)
    paint_smart_overhangs_requested = Signal(float)
    paint_clear_requested = Signal()

    # Preset & navigation signals
    preset_selected = Signal(str, str)
    open_settings_requested = Signal(str)

    # Slicing & export signals
    slice_requested = Signal()
    slice_all_plates_requested = Signal()
    export_gcode_requested = Signal()

    def __init__(self, preset_manager: PresetManager, parent: Optional[QWidget] = None) -> None:
        """Assemble the split workspace, floating viewport overlays, and default tool state."""
        super().__init__(parent)
        self.preset_manager = preset_manager
        self._preview_data: Optional[GCodePreviewData] = None
        self._suppress_transform = False
        self._suppress_cut_ui = False
        self._suppress_presets = False
        self._suppress_obj_list = False
        self._current_view: str = "editor"
        self._current_tool: str = "select"
        self._resolved_theme: str = "dark"

        self._tool_buttons: Dict[str, QPushButton] = {}
        self._icon_registry: List[Tuple[QPushButton, str, int, bool]] = []
        self._top_edit_widgets: List[QWidget] = []
        self._paint_mode_btns: Dict[int, QPushButton] = {}
        self._preset_combos: Dict[str, QComboBox] = {}
        self._preset_mod_labels: Dict[str, QLabel] = {}
        self._feature_checks: Dict[int, QCheckBox] = {}

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._on_play_tick)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_center_workspace())
        self.splitter.addWidget(build_right_sidebar(self))
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1120, 360])
        root.addWidget(self.splitter)

        self._build_viewport_overlays()
        self.viewport.installEventFilter(self)
        self.viewport.cut_plane_changed.connect(self._on_viewport_cut_plane_changed)

        self.apply_theme("dark")
        self.set_view_mode("editor")
        self.set_edit_tool("select")

    # -------------------------------------------------------------------------
    # Event Filter & Floating Overlay Layout
    # -------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keep floating tool rail, top pill bar, and popovers aligned when viewport resizes."""
        if watched is self.viewport and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._reposition_viewport_overlays()
        return super().eventFilter(watched, event)

    def _reposition_viewport_overlays(self) -> None:
        """Compute anchored pixel coordinates for all viewport child overlays."""
        vw, vh = self.viewport.width(), self.viewport.height()
        if vw <= 10 or vh <= 10:
            return

        self._vp_tool_rail.adjustSize()
        rw, rh = self._vp_tool_rail.width(), self._vp_tool_rail.height()
        ry = max(62, min(vh - rh - 14, (vh - rh) // 2))
        self._vp_tool_rail.move(12, ry)

        self._vp_top_bar.adjustSize()
        tw = self._vp_top_bar.width()
        tx = max(12, (vw - tw) // 2)
        if tx + tw > vw - 12:
            tx = max(8, vw - tw - 8)
        self._vp_top_bar.move(tx, 12)

        pop_x = 12 + rw + 10
        pop_y = max(64, ry)
        for pop in (self._cut_popover, self._paint_popover):
            pop.adjustSize()
            py = max(64, min(vh - pop.height() - 12, pop_y))
            pop.move(pop_x, py)

    # -------------------------------------------------------------------------
    # Theme & Vector Icon Sync
    # -------------------------------------------------------------------------

    def _register_icon_btn(
        self,
        btn: QPushButton,
        icon_name: str,
        size: int = 18,
        accent_bg_on_check: bool = False,
    ) -> None:
        """Track a button for automatic high-DPI vector icon recoloring on theme switches."""
        btn.setIconSize(QSize(size, size))
        self._icon_registry.append((btn, icon_name, size, accent_bg_on_check))

    def _refresh_all_icons(self) -> None:
        """Re-render all registered vector icons using the active theme palette."""
        pal = get_palette(self._resolved_theme)
        for btn, icon_name, size, accent_bg_on_check in self._icon_registry:
            if (accent_bg_on_check and btn.isChecked()) or btn is self.btn_slice:
                fg = ac = pal.accent_fg
            else:
                fg, ac = pal.icon_fg, pal.icon_accent
            btn.setIcon(make_icon(icon_name, color=fg, accent=ac, size=size))

    def apply_theme(self, resolved_mode: str) -> None:
        """Apply dark or light styling across the 3D viewport, layer bar, icons, and dividers."""
        self._resolved_theme = resolved_mode
        self.viewport.set_theme_mode(resolved_mode)
        self.layer_bar.apply_theme(resolved_mode)
        self._refresh_all_icons()

        pal = get_palette(resolved_mode)
        div_qss = f"background-color: {pal.border};"
        for div in getattr(self, "_overlay_dividers", []):
            div.setStyleSheet(div_qss)

    # -------------------------------------------------------------------------
    # Floating In-Viewport Overlays & Center Workspace Construction
    # -------------------------------------------------------------------------

    def _build_viewport_overlays(self) -> None:
        """Create floating left tool rail, top-center action/camera bar, and tool popovers."""
        self._overlay_dividers: List[QFrame] = []

        # 1. Floating Left Vertical Tool Rail
        self._vp_tool_rail = QFrame(self.viewport)
        self._vp_tool_rail.setObjectName("viewportRail")
        rail_v = QVBoxLayout(self._vp_tool_rail)
        rail_v.setContentsMargins(5, 6, 5, 6)
        rail_v.setSpacing(4)

        tool_specs = [
            ("select", "select", "Select / Orbit (Esc)"),
            ("move", "move", "Move Tool (M) — 3D RGB Gizmo & Bed Drag"),
            ("rotate", "rotate", "Rotate Tool (R) — 3D Rings & Drag"),
            ("scale", "scale", "Scale Tool (S) — 3D Handles & Uniform Drag"),
            ("place_on_face", "place_on_face", "Place on Face (F) — Click any face to lay flat"),
            ("cut", "cut", "Cut Plane Tool (C) — Slice 3D model along movable & rotatable plane"),
            ("paint_supports", "paint_supports", "Paint-on Supports (L) — Brush Enforcers & Blockers"),
        ]
        for idx, (tool_key, icon_name, tip) in enumerate(tool_specs):
            if idx == 5:
                sep = QFrame()
                sep.setFixedHeight(1)
                self._overlay_dividers.append(sep)
                rail_v.addWidget(sep)
            btn = QPushButton()
            btn.setObjectName("viewportToolBtn")
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setFixedSize(36, 36)
            self._register_icon_btn(btn, icon_name, size=20, accent_bg_on_check=True)
            if tool_key == "select":
                btn.clicked.connect(lambda: self.set_edit_tool("select"))
            else:
                btn.clicked.connect(lambda _chk=False, t=tool_key: self._toggle_edit_tool(t))
            rail_v.addWidget(btn)
            self._tool_buttons[tool_key] = btn
        self._vp_tool_rail.show()

        # 2. Floating Top-Center Action & Camera Pill Bar
        self._vp_top_bar = QFrame(self.viewport)
        self._vp_top_bar.setObjectName("viewportTopBar")
        top_h = QHBoxLayout(self._vp_top_bar)
        top_h.setContentsMargins(7, 4, 7, 4)
        top_h.setSpacing(2)

        def _add_vdiv() -> None:
            d = QFrame()
            d.setFixedSize(1, 20)
            self._overlay_dividers.append(d)
            top_h.addWidget(d)
            self._top_edit_widgets.append(d)

        def _pill_btn(icon_name: str, text: str, tip: str, cb, edit_only: bool = True) -> None:
            b = QPushButton(text)
            b.setObjectName("viewportPillBtn")
            b.setToolTip(tip)
            self._register_icon_btn(b, icon_name, size=16, accent_bg_on_check=False)
            b.clicked.connect(cb)
            top_h.addWidget(b)
            if edit_only:
                self._top_edit_widgets.append(b)

        _pill_btn("add_model", "Add", "Add STL / OBJ / 3MF / STEP model to active plate", self.add_model_requested.emit)
        _pill_btn("sphere", "", "Add built-in 30 mm test sphere", self.load_sphere_requested.emit)
        _pill_btn("wall", "", "Add built-in curved test wall", self.load_wall_requested.emit)
        _add_vdiv()
        _pill_btn("clone", "", "Duplicate selected object (Ctrl+D)", self.duplicate_instance_requested.emit)
        _pill_btn("arrange", "", "Auto-arrange all models on active plate (A)", self.arrange_plate_requested.emit)
        _pill_btn("center", "", "Center selected model on build plate", self.center_model_requested.emit)
        _pill_btn("drop", "", "Drop selected model onto Z = 0", self.drop_model_requested.emit)
        _pill_btn("delete", "", "Delete selected object (Del)", self.remove_instance_requested.emit)
        _add_vdiv()

        for icon_name, tip, preset in (
            ("cam_iso", "Isometric 3D camera", "iso"),
            ("cam_top", "Top-down camera", "top"),
            ("cam_bottom", "Bottom-up camera (build plate automatically hides for clear underside inspection)", "bottom"),
            ("cam_front", "Front elevation camera", "front"),
            ("cam_right", "Right side camera", "right"),
        ):
            _pill_btn(
                icon_name,
                "",
                tip,
                lambda _c=False, p=preset: self.viewport.set_camera_preset(p),
                edit_only=False,
            )
        _pill_btn("cam_fit", "", "Fit camera to build plate", lambda: self.viewport.fit_to_bed(), edit_only=False)
        self._vp_top_bar.show()

        # 3 & 4. Floating Contextual Popovers (Cut Plane & Paint-on-Supports)
        self._cut_popover = build_cut_popover(self)
        self._paint_popover = build_paint_popover(self)

    def _build_plate_bar(self) -> QFrame:
        """Build the top multi-plate tab bar with Add Plate and Remove Plate buttons."""
        bar = QFrame()
        bar.setObjectName("card")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(6)

        lbl = QLabel("BUILD PLATES:")
        lbl.setObjectName("mutedLabel")
        row.addWidget(lbl)

        self._plate_btn_layout = QHBoxLayout()
        self._plate_btn_layout.setContentsMargins(0, 0, 0, 0)
        self._plate_btn_layout.setSpacing(5)
        row.addLayout(self._plate_btn_layout)
        row.addStretch(1)

        self.btn_add_plate = QPushButton("Add Plate")
        self.btn_add_plate.setObjectName("iconSmallBtn")
        self.btn_add_plate.setToolTip("Create a new independent print bed (Ctrl+N)")
        self._register_icon_btn(self.btn_add_plate, "plus", size=14)
        self.btn_add_plate.clicked.connect(self.add_plate_requested.emit)
        row.addWidget(self.btn_add_plate)

        self.btn_remove_plate = QPushButton("Remove Plate")
        self.btn_remove_plate.setObjectName("iconSmallBtn")
        self.btn_remove_plate.setToolTip("Delete the currently active build plate")
        self._register_icon_btn(self.btn_remove_plate, "minus", size=14)
        self.btn_remove_plate.clicked.connect(self.remove_plate_requested.emit)
        row.addWidget(self.btn_remove_plate)
        return bar

    def _build_center_workspace(self) -> QWidget:
        """Build the center column: plate bar, 3D/2D stacked view, and bottom view/scrubber bar."""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 4, 0)
        vbox.setSpacing(6)
        vbox.addWidget(self._build_plate_bar())

        self.view_stack = QStackedWidget()

        # Page 0: 3D OpenGL Viewport + Side Legend & Layer Slider
        gl_page = QWidget()
        gl_row = QHBoxLayout(gl_page)
        gl_row.setContentsMargins(0, 0, 0, 0)
        gl_row.setSpacing(6)

        self.legend_card = build_legend_card(self)
        self.legend_card.setVisible(False)
        gl_row.addWidget(self.legend_card)

        viewport_frame = QFrame()
        viewport_frame.setObjectName("card")
        vf_layout = QVBoxLayout(viewport_frame)
        vf_layout.setContentsMargins(2, 2, 2, 2)
        self.viewport = AlveoViewport3D()
        vf_layout.addWidget(self.viewport)
        gl_row.addWidget(viewport_frame, stretch=1)

        self.layer_bar = LayerRangeBar()
        self.layer_bar.setVisible(False)
        self.layer_bar.range_changed.connect(self._on_layer_range_changed)
        gl_row.addWidget(self.layer_bar)
        self.view_stack.addWidget(gl_page)

        # Page 1: 2D Layer & Resin Basin Inspector
        self.inspector_2d = LayerInspector2D()
        self.view_stack.addWidget(self.inspector_2d)
        vbox.addWidget(self.view_stack, stretch=1)

        # Bottom Mode Switcher + Contextual Controls
        bottom = QFrame()
        bottom.setObjectName("card")
        b_row = QHBoxLayout(bottom)
        b_row.setContentsMargins(8, 6, 8, 6)
        b_row.setSpacing(8)

        def _mode_btn(label: str, icon: str, mode: str, enabled: bool = True) -> QPushButton:
            btn = QPushButton(label)
            btn.setObjectName("viewToggle")
            btn.setCheckable(True)
            btn.setEnabled(enabled)
            self._register_icon_btn(btn, icon, size=15, accent_bg_on_check=True)
            btn.clicked.connect(lambda: self.set_view_mode(mode))
            b_row.addWidget(btn)
            return btn

        self.btn_mode_editor = _mode_btn("3D Editor", "view_3d", "editor", enabled=True)
        self.btn_mode_preview = _mode_btn("G-Code Preview", "view_gcode", "preview", enabled=False)
        self.btn_mode_basins = _mode_btn("2D Basins", "view_2d", "basins", enabled=False)

        # Contextual Editor Strip
        self.editor_controls = QWidget()
        ed_row = QHBoxLayout(self.editor_controls)
        ed_row.setContentsMargins(8, 0, 0, 0)
        ed_row.setSpacing(12)
        self.chk_wireframe = QCheckBox("3D Cellular Wireframe")
        self.chk_wireframe.setChecked(True)
        self.chk_wireframe.toggled.connect(self._on_wireframe_toggled)
        ed_row.addWidget(self.chk_wireframe)
        self.lbl_editor_hint = QLabel(_TOOL_HINTS["select"])
        self.lbl_editor_hint.setObjectName("mutedLabel")
        ed_row.addWidget(self.lbl_editor_hint, stretch=1)
        b_row.addWidget(self.editor_controls, stretch=1)

        # Contextual G-Code Move Scrubber Strip
        self.preview_controls = QWidget()
        pr_row = QHBoxLayout(self.preview_controls)
        pr_row.setContentsMargins(8, 0, 0, 0)
        pr_row.setSpacing(8)

        self.btn_toggle_legend = QPushButton("Legend")
        self.btn_toggle_legend.setObjectName("iconSmallBtn")
        self.btn_toggle_legend.setCheckable(True)
        self.btn_toggle_legend.setChecked(True)
        self._register_icon_btn(self.btn_toggle_legend, "legend", size=15)
        self.btn_toggle_legend.toggled.connect(self._on_legend_toggled)
        pr_row.addWidget(self.btn_toggle_legend)

        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("iconSmallBtn")
        self.btn_play.setMinimumWidth(68)
        self._register_icon_btn(self.btn_play, "play", size=14)
        self.btn_play.clicked.connect(self._toggle_playback)
        pr_row.addWidget(self.btn_play)

        lbl_mv = QLabel("Move:")
        lbl_mv.setObjectName("mutedLabel")
        pr_row.addWidget(lbl_mv)

        self.slider_move = QSlider(Qt.Orientation.Horizontal)
        self.slider_move.setRange(1, 1)
        self.slider_move.valueChanged.connect(self._on_move_slider_changed)
        pr_row.addWidget(self.slider_move, stretch=1)

        self.lbl_move_count = QLabel("0 / 0")
        self.lbl_move_count.setObjectName("accentValue")
        self.lbl_move_count.setMinimumWidth(78)
        self.lbl_move_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pr_row.addWidget(self.lbl_move_count)

        self.preview_controls.setVisible(False)
        b_row.addWidget(self.preview_controls, stretch=1)

        vbox.addWidget(bottom)
        return container

    # -------------------------------------------------------------------------
    # Public Sync & State API
    # -------------------------------------------------------------------------

    def set_edit_tool(self, tool: str) -> None:
        """Activate a 3D viewport manipulation tool and toggle its contextual popover."""
        if tool not in _TOOL_HINTS:
            tool = "select"
        self._current_tool = tool
        for key, btn in self._tool_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == tool)
            btn.blockSignals(False)
        self.btn_place_face_quick.blockSignals(True)
        self.btn_place_face_quick.setChecked(tool == "place_on_face")
        self.btn_place_face_quick.blockSignals(False)
        self.lbl_editor_hint.setText(_TOOL_HINTS[tool])
        self.viewport.set_edit_tool(tool)

        is_ed = self._current_view == "editor"
        self._cut_popover.setVisible(is_ed and tool == "cut")
        self._paint_popover.setVisible(is_ed and tool == "paint_supports")
        self._refresh_all_icons()
        self._reposition_viewport_overlays()

    def _toggle_edit_tool(self, tool: str) -> None:
        """Toggle a tool off back to ``select`` if already active, else activate it."""
        if self._current_tool == tool:
            self.set_edit_tool("select")
        else:
            if self._current_view != "editor":
                self.set_view_mode("editor")
            self.set_edit_tool(tool)

    def update_paint_counts(self, enforcers: int, blockers: int) -> None:
        """Refresh the enforcer/blocker face counter in the Paint-on-Supports popover."""
        self.lbl_paint_counts.setText(f"Enforcers: {enforcers}  |  Blockers: {blockers} faces")

    def update_plates(self, plates: List[BuildPlate], active_idx: int) -> None:
        """Rebuild the build plate switcher buttons to mirror ``WorkspaceScene.plates``."""
        while self._plate_btn_layout.count():
            item = self._plate_btn_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        for idx, plate in enumerate(plates):
            suffix = " ✓" if plate.is_sliced else ""
            btn = QPushButton(f"{plate.name} ({len(plate.instances)}){suffix}")
            btn.setObjectName("plateTab")
            btn.setCheckable(True)
            btn.setChecked(idx == active_idx)
            btn.clicked.connect(lambda _chk=False, i=idx: self.plate_selected.emit(i))
            self._plate_btn_layout.addWidget(btn)

        self.btn_remove_plate.setEnabled(len(plates) > 1)
        self.btn_slice_all.setEnabled(sum(1 for p in plates if p.instances) > 1)

    def update_plate_objects(
        self,
        instances: List[ModelInstance],
        selected_id: Optional[str],
    ) -> None:
        """Sync the Build Plate Objects list widget and selected-object transform fields."""
        self._suppress_obj_list = True
        try:
            self.list_objects.clear()
            sel_row = -1
            for row_idx, inst in enumerate(instances):
                ext = inst.transformed_mesh().extents
                item = QListWidgetItem(f"{inst.name}   ({ext[0]:.0f}×{ext[1]:.0f}×{ext[2]:.0f} mm)")
                item.setData(Qt.ItemDataRole.UserRole, inst.instance_id)
                self.list_objects.addItem(item)
                if inst.instance_id == selected_id:
                    sel_row = row_idx
            if sel_row >= 0:
                self.list_objects.setCurrentRow(sel_row)
            else:
                self.list_objects.clearSelection()
                self.list_objects.setCurrentRow(-1)
        finally:
            self._suppress_obj_list = False

        selected = next((i for i in instances if i.instance_id == selected_id), None)
        self.update_selected_instance_inspector(selected)

    def update_selected_instance_inspector(self, inst: Optional[ModelInstance]) -> None:
        """Populate or disable the transform, cut, and paint widgets for ``inst``."""
        has_inst = inst is not None
        for w in (
            self.btn_dup_inst, self.btn_move_plate, self.btn_del_inst,
            self.sp_pos_x, self.sp_pos_y, self.sp_pos_z,
            self.sp_rot_x, self.sp_rot_y, self.sp_rot_z,
            self.sp_scale_x, self.sp_scale_y, self.sp_scale_z,
            self.chk_uniform_scale,
            self.btn_rot_x90, self.btn_rot_y90, self.btn_rot_z90,
            self.btn_place_face_quick, self.btn_reset_xform,
            self.btn_perform_cut, self.btn_smart_paint, self.btn_clear_paint,
        ):
            w.setEnabled(has_inst)

        self._suppress_transform = True
        try:
            if inst is not None:
                mesh = inst.transformed_mesh()
                ext = mesh.extents
                self.lbl_model_name.setText(inst.name)
                self.lbl_model_dims.setText(
                    f"Size: {ext[0]:.1f} × {ext[1]:.1f} × {ext[2]:.1f} mm  |  {len(mesh.faces):,} faces"
                )
                self.sp_pos_x.setValue(inst.pos_x)
                self.sp_pos_y.setValue(inst.pos_y)
                self.sp_pos_z.setValue(inst.pos_z)
                self.sp_rot_x.setValue(inst.rot_x)
                self.sp_rot_y.setValue(inst.rot_y)
                self.sp_rot_z.setValue(inst.rot_z)
                self.sp_scale_x.setValue(inst.scale_x)
                self.sp_scale_y.setValue(inst.scale_y)
                self.sp_scale_z.setValue(inst.scale_z)
                self.chk_uniform_scale.setChecked(inst.uniform_scale)
                self.update_paint_counts(*inst.paint_counts())
            else:
                self.lbl_model_name.setText("No object selected")
                self.lbl_model_dims.setText("Size: —   |   Faces: —")
                self.update_paint_counts(0, 0)
        finally:
            self._suppress_transform = False

    def refresh_preset_dropdown(self, category: str, selected_name: str, is_modified: bool) -> None:
        """Repopulate one sidebar preset combo box and update its modified badge."""
        cb = self._preset_combos[category]
        self._suppress_presets = True
        try:
            cb.clear()
            presets = self.preset_manager.list_presets(category)
            sel_idx = 0
            for idx, p in enumerate(presets):
                cb.addItem(p.name if p.builtin else f"{p.name} (User)", userData=p.name)
                if p.name == selected_name:
                    sel_idx = idx
            if presets:
                cb.setCurrentIndex(sel_idx)
        finally:
            self._suppress_presets = False
        self._preset_mod_labels[category].setVisible(is_modified)

    def set_preset_modified(self, category: str, is_modified: bool) -> None:
        """Show or hide the ``* modified`` badge next to a sidebar preset dropdown."""
        self._preset_mod_labels[category].setVisible(is_modified)

    def update_mode_badge(self, cfg: SlicerConfig) -> None:
        """Update the Hybrid vs. Standard FFF banner in the Slice Summary card."""
        pump, uv = cfg.hybrid.enable_resin_pumping, cfg.hybrid.enable_uv_curing
        if pump and uv:
            text, obj = "HYBRID: FFF + RESIN + UV", "modeBannerHybrid"
        elif pump:
            text, obj = "RESIN DOSING ONLY (NO UV)", "modeBannerHybrid"
        elif uv:
            text, obj = "FFF + UV SWEEP (NO DOSING)", "modeBannerHybrid"
        else:
            text, obj = "STANDARD FFF ONLY", "modeBannerFFF"
        self.lbl_mode_badge.setText(text)
        self.lbl_mode_badge.setObjectName(obj)
        self.lbl_mode_badge.style().unpolish(self.lbl_mode_badge)
        self.lbl_mode_badge.style().polish(self.lbl_mode_badge)

    def set_slice_result(
        self,
        res: Optional[SliceResult],
        preview: Optional[GCodePreviewData],
        switch_to_preview: bool = True,
    ) -> None:
        """Load slicing results and/or parsed G-code into the 3D previewer, 2D inspector, and summary."""
        self._stop_playback()
        self._preview_data = preview

        if res is not None:
            self.viewport.set_wireframe(res.capsules)
            self.inspector_2d.set_result(res)
            self.btn_mode_basins.setEnabled(True)
            self.lbl_stat_layers.setText(str(len(res.layers)))
            self.lbl_stat_cycles.setText(str(len(res.schedule)))
            self.lbl_stat_filament.setText(f"{res.total_filament_mm / 1000.0:.2f} m")
            self.lbl_stat_resin.setText(f"{res.total_resin_ml:.2f} mL")
            self.lbl_stat_time.setText(format_duration(res.est_print_time_s))
            self.lbl_stat_lines.setText(f"{res.gcode_line_count:,}")
            self.btn_export.setEnabled(True)
        else:
            self.btn_mode_basins.setEnabled(False)
            for lbl in (
                self.lbl_stat_layers, self.lbl_stat_cycles, self.lbl_stat_filament,
                self.lbl_stat_resin, self.lbl_stat_time, self.lbl_stat_lines,
            ):
                lbl.setText("—")
            self.btn_export.setEnabled(False)

        if preview is not None and preview.num_layers > 0:
            self.viewport.set_gcode_preview(preview, slice_result=res)
            self.layer_bar.configure(preview.layer_zs, preview.cure_layers)
            self._configure_move_slider_for_layer(preview.num_layers, reset_to_max=True)
            self.btn_mode_preview.setEnabled(True)
            if res is None:
                self.lbl_stat_layers.setText(str(preview.num_layers))
                self.lbl_stat_cycles.setText(str(len(preview.cure_layers)))
                self.lbl_stat_filament.setText(f"{preview.total_filament_mm / 1000.0:.2f} m")
                self.lbl_stat_resin.setText(f"{preview.total_resin_ml:.2f} mL")
                self.lbl_stat_time.setText(preview.est_time_str or "—")
                self.lbl_stat_lines.setText("External")
                self.btn_export.setEnabled(True)
            if switch_to_preview:
                self.set_view_mode("preview")
        else:
            self.viewport.set_gcode_preview(None)
            self.btn_mode_preview.setEnabled(False)
            if self._current_view == "preview":
                self.set_view_mode("editor")

    def set_view_mode(self, mode: str) -> None:
        """Switch between ``editor`` (3D Plater), ``preview`` (G-Code Viewer), and ``basins`` (2D)."""
        if mode == "preview" and not self.btn_mode_preview.isEnabled():
            mode = "editor"
        if mode == "basins" and not self.btn_mode_basins.isEnabled():
            mode = "editor"

        self._current_view = mode
        self._stop_playback()

        self.btn_mode_editor.setChecked(mode == "editor")
        self.btn_mode_preview.setChecked(mode == "preview")
        self.btn_mode_basins.setChecked(mode == "basins")

        if mode == "basins":
            self.view_stack.setCurrentIndex(1)
            self.editor_controls.setVisible(False)
            self.preview_controls.setVisible(False)
        else:
            self.view_stack.setCurrentIndex(0)
            self.viewport.set_mode(mode)
            is_prev = mode == "preview"
            self.legend_card.setVisible(is_prev and self.btn_toggle_legend.isChecked())
            self.layer_bar.setVisible(is_prev)
            self.editor_controls.setVisible(not is_prev)
            self.preview_controls.setVisible(is_prev)

            self._vp_tool_rail.setVisible(not is_prev)
            for w in self._top_edit_widgets:
                w.setVisible(not is_prev)
            self._cut_popover.setVisible((not is_prev) and self._current_tool == "cut")
            self._paint_popover.setVisible((not is_prev) and self._current_tool == "paint_supports")
            self._reposition_viewport_overlays()

        self._refresh_all_icons()

    def jump_to_layer(self, layer_num: int) -> None:
        """Navigate both the 3D G-code previewer and 2D Basin Inspector to ``layer_num``."""
        if self._preview_data is not None and self.btn_mode_preview.isEnabled():
            self.set_view_mode("preview")
            self.layer_bar.set_top_layer(layer_num)
        if self.btn_mode_basins.isEnabled():
            self.inspector_2d.select_layer(layer_num)

    def set_slicing_busy(self, busy: bool, status_text: str = "") -> None:
        """Enable/disable slicing buttons and update the footer status message."""
        self.btn_slice.setEnabled(not busy)
        self.btn_slice_all.setEnabled(not busy)
        if busy:
            self.btn_export.setEnabled(False)
            self.progress_bar.setValue(0)
        if status_text:
            self.lbl_status.setText(status_text)

    def set_progress(self, pct: int, msg: str) -> None:
        """Update the footer progress bar percentage and status text."""
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    # -------------------------------------------------------------------------
    # Cut Plane & Paint-on-Supports Slots
    # -------------------------------------------------------------------------

    def _on_viewport_cut_plane_changed(
        self, ox: float, oy: float, oz: float, rx: float, ry: float, rz: float
    ) -> None:
        """Mirror interactive 3D cut-plane handle drags back into the popover spinboxes."""
        self._suppress_cut_ui = True
        try:
            self.sp_cut_ox.setValue(ox)
            self.sp_cut_oy.setValue(oy)
            self.sp_cut_oz.setValue(oz)
            self.sp_cut_rx.setValue(rx)
            self.sp_cut_ry.setValue(ry)
            self.sp_cut_rz.setValue(rz)
        finally:
            self._suppress_cut_ui = False

    def _on_cut_spin_changed(self) -> None:
        """Push numeric Cut Plane popover edits to the 3D viewport plane."""
        if self._suppress_cut_ui:
            return
        self.viewport.set_cut_plane(
            self.sp_cut_ox.value(), self.sp_cut_oy.value(), self.sp_cut_oz.value(),
            self.sp_cut_rx.value(), self.sp_cut_ry.value(), self.sp_cut_rz.value(),
            emit_signal=False,
        )

    def _set_cut_orientation(self, rx: float, ry: float) -> None:
        """Snap the cutting plane to a cardinal orientation preset (Horiz Z, Vert X, Vert Y)."""
        self._suppress_cut_ui = True
        try:
            self.sp_cut_rx.setValue(rx)
            self.sp_cut_ry.setValue(ry)
            self.sp_cut_rz.setValue(0.0)
        finally:
            self._suppress_cut_ui = False
        self._on_cut_spin_changed()

    def _reset_cut_plane(self) -> None:
        """Re-center the cut plane horizontally through the selected model's midpoint."""
        self.viewport._reset_cut_plane_to_selected()

    def _emit_perform_cut(self) -> None:
        """Emit :attr:`cut_performed` with current plane origin, normal, and keep flags."""
        keep_up, keep_lo = self.chk_cut_upper.isChecked(), self.chk_cut_lower.isChecked()
        if not keep_up and not keep_lo:
            return
        n = self.viewport.cut_plane_normal()
        self.cut_performed.emit(
            self.sp_cut_ox.value(), self.sp_cut_oy.value(), self.sp_cut_oz.value(),
            float(n[0]), float(n[1]), float(n[2]),
            keep_up, keep_lo, self.chk_cut_place.isChecked(),
        )

    def _set_paint_brush_mode(self, mode: int) -> None:
        """Select support brush mode (``+1`` enforcer, ``-1`` blocker, ``0`` erase)."""
        for mv, btn in self._paint_mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(mv == mode)
            btn.blockSignals(False)
        self.viewport.set_paint_brush(self.slider_brush.value() / 10.0, mode)

    def _on_brush_slider_changed(self, raw: int) -> None:
        """Update the 3D support brush radius from the slider (tenths of a millimeter)."""
        rad = raw / 10.0
        self.lbl_brush_radius.setText(f"{rad:.1f} mm")
        self.viewport.set_paint_brush(rad, self.viewport.paint_brush_mode)

    # -------------------------------------------------------------------------
    # Sidebar, Transform & G-Code Preview Slots
    # -------------------------------------------------------------------------

    def _on_sidebar_preset_changed(self, category: str, idx: int) -> None:
        """Emit :attr:`preset_selected` when the user picks a preset in the sidebar."""
        if self._suppress_presets or idx < 0:
            return
        name = self._preset_combos[category].itemData(idx)
        if name:
            self.preset_selected.emit(category, str(name))

    def _on_object_list_changed(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        """Emit :attr:`instance_selected` when the object list selection changes."""
        if self._suppress_obj_list or current is None:
            return
        inst_id = current.data(Qt.ItemDataRole.UserRole)
        if inst_id:
            self.instance_selected.emit(str(inst_id))

    def _on_uniform_toggled(self, checked: bool) -> None:
        """Sync Y/Z scale to X when uniform scaling is enabled."""
        if self._suppress_transform:
            return
        if checked:
            v = self.sp_scale_x.value()
            self._suppress_transform = True
            try:
                self.sp_scale_y.setValue(v)
                self.sp_scale_z.setValue(v)
            finally:
                self._suppress_transform = False
        self._emit_transform()

    def _on_scale_axis_edited(self, axis: str, val: float) -> None:
        """Propagate scale edits across all three axes when uniform scaling is locked."""
        if self._suppress_transform:
            return
        if self.chk_uniform_scale.isChecked():
            self._suppress_transform = True
            try:
                if axis != "x":
                    self.sp_scale_x.setValue(val)
                if axis != "y":
                    self.sp_scale_y.setValue(val)
                if axis != "z":
                    self.sp_scale_z.setValue(val)
            finally:
                self._suppress_transform = False
        self._emit_transform()

    def _on_transform_edited(self) -> None:
        """Forward position or rotation spinbox edits to the main window."""
        if not self._suppress_transform:
            self._emit_transform()

    def _emit_transform(self) -> None:
        """Emit :attr:`instance_transform_changed` with all 9 DOF values and uniform flag."""
        self.instance_transform_changed.emit(
            self.sp_pos_x.value(), self.sp_pos_y.value(), self.sp_pos_z.value(),
            self.sp_rot_x.value(), self.sp_rot_y.value(), self.sp_rot_z.value(),
            self.sp_scale_x.value(), self.sp_scale_y.value(), self.sp_scale_z.value(),
            self.chk_uniform_scale.isChecked(),
        )

    def _on_wireframe_toggled(self, checked: bool) -> None:
        """Toggle 3D cellular capsule wireframe rendering in Editor mode."""
        self.viewport.show_infill_wireframe = checked
        self.viewport.update()

    def _on_legend_toggled(self, checked: bool) -> None:
        """Show or hide the G-code feature legend card while in Preview mode."""
        if self._current_view == "preview":
            self.legend_card.setVisible(checked)

    def _set_all_features(self, enabled: bool) -> None:
        """Check or uncheck all feature-type checkboxes at once."""
        for chk in self._feature_checks.values():
            chk.blockSignals(True)
            chk.setChecked(enabled)
            chk.blockSignals(False)
        self._sync_preview_filters()

    def _sync_preview_filters(self) -> None:
        """Push feature-type, travel, and resin-dosing visibility flags to the viewport."""
        vis = {idx: chk.isChecked() for idx, chk in self._feature_checks.items()}
        self.viewport.set_preview_filter(
            visible_types=vis,
            show_travels=self.chk_travels.isChecked(),
            show_dosing=self.chk_dosing.isChecked(),
        )

    def _configure_move_slider_for_layer(self, layer_num: int, reset_to_max: bool = True) -> None:
        """Update the horizontal move scrubber range for the active top preview layer."""
        data = self._preview_data
        if data is None or not data.layer_move_counts:
            self.slider_move.setRange(1, 1)
            self.slider_move.setValue(1)
            self.lbl_move_count.setText("0 / 0")
            return
        idx = max(0, min(len(data.layer_move_counts) - 1, layer_num - 1))
        total_moves = max(1, data.layer_move_counts[idx])
        self.slider_move.blockSignals(True)
        self.slider_move.setRange(1, total_moves)
        val = total_moves if reset_to_max else min(self.slider_move.value(), total_moves)
        self.slider_move.setValue(val)
        self.slider_move.blockSignals(False)
        self.lbl_move_count.setText(f"{val} / {total_moves}")

    def _on_layer_range_changed(self, low: int, high: int) -> None:
        """Update visible layer window and reset top-layer move scrubber when slider moves."""
        self._stop_playback()
        self._configure_move_slider_for_layer(high, reset_to_max=True)
        self.viewport.set_preview_filter(
            layer_min=low, layer_max=high, top_move_limit=self.slider_move.value()
        )

    def _on_move_slider_changed(self, move_limit: int) -> None:
        """Update intra-layer extrusion move scrubbing in the 3D previewer."""
        self.lbl_move_count.setText(f"{move_limit} / {self.slider_move.maximum()}")
        self.viewport.set_preview_filter(top_move_limit=move_limit)

    def _toggle_playback(self) -> None:
        """Start or pause animated nozzle playback on the active top layer."""
        if self._play_timer.isActive():
            self._stop_playback()
            return
        if self._preview_data is None or self.slider_move.maximum() <= 1:
            return
        if self.slider_move.value() >= self.slider_move.maximum():
            self.slider_move.setValue(1)
        self.btn_play.setText("Pause")
        pal = get_palette(self._resolved_theme)
        self.btn_play.setIcon(make_icon("pause", color=pal.icon_fg, accent=pal.icon_accent, size=14))
        self._play_timer.start()

    def _stop_playback(self) -> None:
        """Stop animated nozzle playback and restore the Play button icon/label."""
        if self._play_timer.isActive():
            self._play_timer.stop()
        self.btn_play.setText("Play")
        pal = get_palette(self._resolved_theme)
        self.btn_play.setIcon(make_icon("play", color=pal.icon_fg, accent=pal.icon_accent, size=14))

    def _on_play_tick(self) -> None:
        """Advance the move slider during animated top-layer toolpath playback."""
        step = max(1, self.slider_move.maximum() // 90)
        nxt = self.slider_move.value() + step
        if nxt >= self.slider_move.maximum():
            self.slider_move.setValue(self.slider_move.maximum())
            self._stop_playback()
        else:
            self.slider_move.setValue(nxt)
