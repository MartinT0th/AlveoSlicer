"""PrusaSlicer-style Plater tab with floating 3D viewport icon overlays, Cut Plane, Paint-on Supports, and Presets."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import SlicerConfig
from ..gcode_parser import (
    DOSING_COLOR,
    FEATURE_COLORS,
    FEATURE_TYPES,
    TRAVEL_COLOR,
    GCodePreviewData,
)
from ..pipeline import SliceResult
from ..presets import CATEGORY_LABELS, PRESET_CATEGORIES, PresetManager
from ..scene import BuildPlate, ModelInstance
from .icons import make_icon
from .inspector_2d import LayerInspector2D
from .layer_slider import LayerRangeBar
from .theme import get_palette
from .viewport_gl import AlveoViewport3D
from .widgets import make_double_spin, wrap_scroll


def _rgb_hex(rgb: Tuple[float, float, float]) -> str:
    r = max(0, min(255, int(round(rgb[0] * 255))))
    g = max(0, min(255, int(round(rgb[1] * 255))))
    b = max(0, min(255, int(round(rgb[2] * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _format_duration(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    hrs, rem = divmod(sec, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


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
    """
    Modern PrusaSlicer-inspired Plater workspace:
      - Maximized 3D OpenGL Viewport with floating in-canvas icon tool rail, floating top-center
        action & camera pill bar, and floating contextual Cut Plane & Paint-on Supports popovers
      - Resizable QSplitter separating the 3D workspace from the right Presets/Objects/Transform sidebar
      - Multi-plate top bar, volumetric G-Code Viewer with Legend + Dual Layer Slider + Move Scrubber
      - Full Dark / Light theme & high-DPI vector icon support
    """

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
    # (pos_x, pos_y, pos_z, rot_x, rot_y, rot_z, scale_x, scale_y, scale_z, uniform_scale)
    instance_transform_changed = Signal(
        float, float, float, float, float, float, float, float, float, bool
    )
    quick_rotate_requested = Signal(str, float)  # axis ("x"|"y"|"z"), delta_deg

    # Cut Plane & Paint-on Supports signals
    # (ox, oy, oz, nx, ny, nz, keep_upper, keep_lower, place_on_bed)
    cut_performed = Signal(float, float, float, float, float, float, bool, bool, bool)
    paint_smart_overhangs_requested = Signal(float)
    paint_clear_requested = Signal()

    # Preset & navigation signals
    preset_selected = Signal(str, str)       # (category, preset_name)
    open_settings_requested = Signal(str)    # category

    # Slicing & export signals
    slice_requested = Signal()
    slice_all_plates_requested = Signal()
    export_gcode_requested = Signal()

    def __init__(self, preset_manager: PresetManager, parent: Optional[QWidget] = None) -> None:
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
        self._icon_registry: List[Tuple[QPushButton, str, int, bool]] = []  # (btn, icon_name, px, is_accent_when_checked)
        self._top_edit_widgets: List[QWidget] = []
        self._paint_mode_btns: Dict[int, QPushButton] = {}

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._on_play_tick)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        # Resizable QSplitter: Center 3D Workspace | Right Sidebar
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_center_workspace())
        self.splitter.addWidget(self._build_right_sidebar())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1120, 360])
        root.addWidget(self.splitter)

        # Build floating in-viewport controls (parented to self.viewport)
        self._build_viewport_overlays()
        self.viewport.installEventFilter(self)
        self.viewport.cut_plane_changed.connect(self._on_viewport_cut_plane_changed)

        self.apply_theme("dark")
        self.set_view_mode("editor")
        self.set_edit_tool("select")

    # -------------------------------------------------------------------------
    # Event Filter for Floating Viewport Overlays
    # -------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._reposition_viewport_overlays()
        return super().eventFilter(watched, event)

    def _reposition_viewport_overlays(self) -> None:
        vw = self.viewport.width()
        vh = self.viewport.height()
        if vw <= 10 or vh <= 10:
            return

        # 1. Left vertical icon tool rail (vertically centered, left margin 12px)
        self._vp_tool_rail.adjustSize()
        rw = self._vp_tool_rail.width()
        rh = self._vp_tool_rail.height()
        ry = max(62, min(vh - rh - 14, (vh - rh) // 2))
        self._vp_tool_rail.move(12, ry)

        # 2. Top-center pill bar (top margin 12px, centered cleanly)
        self._vp_top_bar.adjustSize()
        tw = self._vp_top_bar.width()
        tx = max(12, (vw - tw) // 2)
        if tx + tw > vw - 12:
            tx = max(8, vw - tw - 8)
        self._vp_top_bar.move(tx, 12)

        # 3. Contextual popovers right beside the left tool rail
        pop_x = 12 + rw + 10
        pop_y = max(64, ry)
        for pop in (self._cut_popover, self._paint_popover):
            pop.adjustSize()
            ph = pop.height()
            py = max(64, min(vh - ph - 12, pop_y))
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
        btn.setIconSize(QSize(size, size))
        self._icon_registry.append((btn, icon_name, size, accent_bg_on_check))

    def _refresh_all_icons(self) -> None:
        pal = get_palette(self._resolved_theme)
        for btn, icon_name, size, accent_bg_on_check in self._icon_registry:
            if accent_bg_on_check and btn.isChecked():
                fg = pal.accent_fg
                ac = pal.accent_fg
            elif btn is self.btn_slice:
                fg = pal.accent_fg
                ac = pal.accent_fg
            else:
                fg = pal.icon_fg
                ac = pal.icon_accent
            btn.setIcon(make_icon(icon_name, color=fg, accent=ac, size=size))

    def apply_theme(self, resolved_mode: str) -> None:
        self._resolved_theme = resolved_mode
        self.viewport.set_theme_mode(resolved_mode)
        self.layer_bar.apply_theme(resolved_mode)
        self._refresh_all_icons()

        # Refresh swatch borders & dividers so light mode looks crisp
        pal = get_palette(resolved_mode)
        div_qss = f"background-color: {pal.border};"
        for d in getattr(self, "_overlay_dividers", []):
            d.setStyleSheet(div_qss)

    # -------------------------------------------------------------------------
    # Floating Overlays Inside 3D Viewport
    # -------------------------------------------------------------------------

    def _build_viewport_overlays(self) -> None:
        self._overlay_dividers: List[QFrame] = []

        # ---- 1. Floating Left Vertical Tool Rail ----
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

        # ---- 2. Floating Top-Center Action & Camera Pill Bar ----
        self._vp_top_bar = QFrame(self.viewport)
        self._vp_top_bar.setObjectName("viewportTopBar")
        top_h = QHBoxLayout(self._vp_top_bar)
        top_h.setContentsMargins(7, 4, 7, 4)
        top_h.setSpacing(2)

        def _vdiv() -> QFrame:
            d = QFrame()
            d.setFixedSize(1, 20)
            self._overlay_dividers.append(d)
            return d

        def _pill_btn(
            icon_name: str,
            text: str,
            tip: str,
            cb,
            edit_only: bool = True,
        ) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("viewportPillBtn")
            b.setToolTip(tip)
            self._register_icon_btn(b, icon_name, size=16, accent_bg_on_check=False)
            b.clicked.connect(cb)
            top_h.addWidget(b)
            if edit_only:
                self._top_edit_widgets.append(b)
            return b

        # Add models
        _pill_btn("add_model", "Add", "Add STL / OBJ / 3MF / STEP model to active plate", self.add_model_requested.emit)
        _pill_btn("sphere", "", "Add built-in 30 mm test sphere", self.load_sphere_requested.emit)
        _pill_btn("wall", "", "Add built-in curved test wall", self.load_wall_requested.emit)

        d1 = _vdiv()
        top_h.addWidget(d1)
        self._top_edit_widgets.append(d1)

        # Quick plate / object actions
        _pill_btn("clone", "", "Duplicate selected object (Ctrl+D)", self.duplicate_instance_requested.emit)
        _pill_btn("arrange", "", "Auto-arrange all models on active plate (A)", self.arrange_plate_requested.emit)
        _pill_btn("center", "", "Center selected model on build plate", self.center_model_requested.emit)
        _pill_btn("drop", "", "Drop selected model onto Z = 0", self.drop_model_requested.emit)
        _pill_btn("delete", "", "Delete selected object (Del)", self.remove_instance_requested.emit)

        d2 = _vdiv()
        top_h.addWidget(d2)
        self._top_edit_widgets.append(d2)

        # Camera presets (always visible in both 3D Editor and G-Code Preview!)
        _pill_btn("cam_iso", "", "Isometric 3D camera", lambda: self.viewport.set_camera_preset("iso"), edit_only=False)
        _pill_btn("cam_top", "", "Top-down camera", lambda: self.viewport.set_camera_preset("top"), edit_only=False)
        _pill_btn(
            "cam_bottom",
            "",
            "Bottom-up camera (build plate automatically hides for clear underside inspection)",
            lambda: self.viewport.set_camera_preset("bottom"),
            edit_only=False,
        )
        _pill_btn("cam_front", "", "Front elevation camera", lambda: self.viewport.set_camera_preset("front"), edit_only=False)
        _pill_btn("cam_right", "", "Right side camera", lambda: self.viewport.set_camera_preset("right"), edit_only=False)
        _pill_btn("cam_fit", "", "Fit camera to build plate", lambda: self.viewport.fit_to_bed(), edit_only=False)

        self._vp_top_bar.show()

        # ---- 3. Floating Contextual Cut Plane Popover ----
        self._cut_popover = QFrame(self.viewport)
        self._cut_popover.setObjectName("viewportPopover")
        self._cut_popover.setFixedWidth(258)
        cv = QVBoxLayout(self._cut_popover)
        cv.setContentsMargins(10, 9, 10, 10)
        cv.setSpacing(6)

        cut_hdr = QHBoxLayout()
        lbl_cut_t = QLabel("Cut Plane")
        lbl_cut_t.setObjectName("sectionTitle")
        self.btn_cut_reset = QPushButton("Reset")
        self.btn_cut_reset.setObjectName("iconSmallBtn")
        self.btn_cut_reset.clicked.connect(self._reset_cut_plane)
        cut_hdr.addWidget(lbl_cut_t)
        cut_hdr.addStretch(1)
        cut_hdr.addWidget(self.btn_cut_reset)
        cv.addLayout(cut_hdr)

        compact_spin_qss = (
            "QDoubleSpinBox { padding: 2px 5px; min-height: 16px; } "
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }"
        )

        cut_grid = QGridLayout()
        cut_grid.setHorizontalSpacing(4)
        cut_grid.setVerticalSpacing(4)

        self.sp_cut_ox = make_double_spin(110.0, -500.0, 1000.0, 1.0, 1, "")
        self.sp_cut_oy = make_double_spin(110.0, -500.0, 1000.0, 1.0, 1, "")
        self.sp_cut_oz = make_double_spin(15.0, -100.0, 600.0, 0.5, 1, "")
        self.sp_cut_rx = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")
        self.sp_cut_ry = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")
        self.sp_cut_rz = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")

        for sb in (
            self.sp_cut_ox,
            self.sp_cut_oy,
            self.sp_cut_oz,
            self.sp_cut_rx,
            self.sp_cut_ry,
            self.sp_cut_rz,
        ):
            sb.setStyleSheet(compact_spin_qss)
            sb.valueChanged.connect(self._on_cut_spin_changed)

        for ci, ax in enumerate(("X", "Y", "Z"), start=1):
            lh = QLabel(ax)
            lh.setObjectName("mutedLabel")
            lh.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cut_grid.addWidget(lh, 0, ci)

        cut_grid.addWidget(QLabel("Pos (mm)"), 1, 0)
        cut_grid.addWidget(self.sp_cut_ox, 1, 1)
        cut_grid.addWidget(self.sp_cut_oy, 1, 2)
        cut_grid.addWidget(self.sp_cut_oz, 1, 3)

        cut_grid.addWidget(QLabel("Rot (°)"), 2, 0)
        cut_grid.addWidget(self.sp_cut_rx, 2, 1)
        cut_grid.addWidget(self.sp_cut_ry, 2, 2)
        cut_grid.addWidget(self.sp_cut_rz, 2, 3)
        cv.addLayout(cut_grid)

        # Quick plane orientation presets
        ori_row = QHBoxLayout()
        ori_row.setSpacing(4)
        for lbl_o, rx, ry in (("Horiz Z", 0.0, 0.0), ("Vert X", 0.0, 90.0), ("Vert Y", 90.0, 0.0)):
            bo = QPushButton(lbl_o)
            bo.setObjectName("iconSmallBtn")
            bo.clicked.connect(lambda _c=False, a=rx, b=ry: self._set_cut_orientation(a, b))
            ori_row.addWidget(bo)
        cv.addLayout(ori_row)

        self.chk_cut_upper = QCheckBox("Keep Upper Part")
        self.chk_cut_upper.setChecked(True)
        self.chk_cut_lower = QCheckBox("Keep Lower Part")
        self.chk_cut_lower.setChecked(True)
        self.chk_cut_place = QCheckBox("Place cut face flat on bed")
        self.chk_cut_place.setChecked(False)
        cv.addWidget(self.chk_cut_upper)
        cv.addWidget(self.chk_cut_lower)
        cv.addWidget(self.chk_cut_place)

        self.btn_perform_cut = QPushButton("Perform Cut")
        self.btn_perform_cut.setObjectName("primaryBtn")
        self.btn_perform_cut.clicked.connect(self._emit_perform_cut)
        cv.addWidget(self.btn_perform_cut)

        lbl_cut_tip = QLabel("Drag yellow handle for Z  |  Shift+drag to tilt")
        lbl_cut_tip.setObjectName("mutedLabel")
        lbl_cut_tip.setWordWrap(True)
        cv.addWidget(lbl_cut_tip)

        self._cut_popover.setVisible(False)

        # ---- 4. Floating Contextual Paint-on Supports Popover ----
        self._paint_popover = QFrame(self.viewport)
        self._paint_popover.setObjectName("viewportPopover")
        self._paint_popover.setFixedWidth(258)
        pv = QVBoxLayout(self._paint_popover)
        pv.setContentsMargins(10, 9, 10, 10)
        pv.setSpacing(6)

        lbl_pt_t = QLabel("Paint-on Supports")
        lbl_pt_t.setObjectName("sectionTitle")
        pv.addWidget(lbl_pt_t)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        for m_val, m_label, m_tip in (
            (1, "Enforcer +", "Force supports under painted faces (green)"),
            (-1, "Blocker -", "Block supports under painted faces (red)"),
            (0, "Erase", "Clear paint on brushed faces"),
        ):
            bm = QPushButton(m_label)
            bm.setObjectName("iconSmallBtn")
            bm.setCheckable(True)
            bm.setChecked(m_val == 1)
            bm.setToolTip(m_tip)
            bm.clicked.connect(lambda _c=False, v=m_val: self._set_paint_brush_mode(v))
            mode_row.addWidget(bm)
            self._paint_mode_btns[m_val] = bm
        pv.addLayout(mode_row)

        rad_hdr = QHBoxLayout()
        rad_lbl = QLabel("Brush radius:")
        rad_lbl.setObjectName("mutedLabel")
        self.lbl_brush_radius = QLabel("4.0 mm")
        self.lbl_brush_radius.setObjectName("accentValue")
        rad_hdr.addWidget(rad_lbl)
        rad_hdr.addStretch(1)
        rad_hdr.addWidget(self.lbl_brush_radius)
        pv.addLayout(rad_hdr)

        self.slider_brush = QSlider(Qt.Orientation.Horizontal)
        self.slider_brush.setRange(10, 200)  # 1.0 .. 20.0 mm in 0.1 mm steps
        self.slider_brush.setValue(40)
        self.slider_brush.valueChanged.connect(self._on_brush_slider_changed)
        pv.addWidget(self.slider_brush)

        # Smart overhang auto-paint
        smart_row = QHBoxLayout()
        smart_row.setSpacing(5)
        self.sp_smart_angle = make_double_spin(45.0, 10.0, 80.0, 5.0, 0, "°")
        self.sp_smart_angle.setStyleSheet(compact_spin_qss)
        self.sp_smart_angle.setFixedWidth(68)
        self.btn_smart_paint = QPushButton("Smart Fill")
        self.btn_smart_paint.setObjectName("iconSmallBtn")
        self.btn_smart_paint.setToolTip("Automatically paint support enforcers on overhangs steeper than angle")
        self.btn_smart_paint.clicked.connect(
            lambda: self.paint_smart_overhangs_requested.emit(self.sp_smart_angle.value())
        )
        self.btn_clear_paint = QPushButton("Clear")
        self.btn_clear_paint.setObjectName("dangerBtn")
        self.btn_clear_paint.setToolTip("Clear all painted support enforcers and blockers")
        self.btn_clear_paint.clicked.connect(self.paint_clear_requested.emit)
        smart_row.addWidget(self.sp_smart_angle)
        smart_row.addWidget(self.btn_smart_paint, stretch=1)
        smart_row.addWidget(self.btn_clear_paint)
        pv.addLayout(smart_row)

        self.lbl_paint_counts = QLabel("Enforcers: 0  |  Blockers: 0 faces")
        self.lbl_paint_counts.setObjectName("mutedLabel")
        pv.addWidget(self.lbl_paint_counts)

        lbl_pt_tip = QLabel("Left-drag: Enforcer  |  Right-drag: Blocker  |  Shift: Erase")
        lbl_pt_tip.setObjectName("mutedLabel")
        lbl_pt_tip.setWordWrap(True)
        pv.addWidget(lbl_pt_tip)

        self._paint_popover.setVisible(False)

    # -------------------------------------------------------------------------
    # Build Plate Bar, Center Workspace, Legend & Right Sidebar
    # -------------------------------------------------------------------------

    def _build_plate_bar(self) -> QFrame:
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
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 4, 0)
        vbox.setSpacing(6)

        # Top Multi-Bed Switcher Bar
        vbox.addWidget(self._build_plate_bar())

        self.view_stack = QStackedWidget()

        # ---- Page 0: 3D OpenGL Viewport + Side Legend & Layer Slider ----
        gl_page = QWidget()
        gl_row = QHBoxLayout(gl_page)
        gl_row.setContentsMargins(0, 0, 0, 0)
        gl_row.setSpacing(6)

        self.legend_card = self._build_legend_card()
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

        # ---- Page 1: 2D Layer & Resin Basin Inspector ----
        self.inspector_2d = LayerInspector2D()
        self.view_stack.addWidget(self.inspector_2d)

        vbox.addWidget(self.view_stack, stretch=1)

        # ---- Bottom Bar: View Mode Switcher + Contextual Controls / Move Scrubber ----
        bottom = QFrame()
        bottom.setObjectName("card")
        b_row = QHBoxLayout(bottom)
        b_row.setContentsMargins(8, 6, 8, 6)
        b_row.setSpacing(8)

        self.btn_mode_editor = QPushButton("3D Editor")
        self.btn_mode_editor.setObjectName("viewToggle")
        self.btn_mode_editor.setCheckable(True)
        self._register_icon_btn(self.btn_mode_editor, "view_3d", size=15, accent_bg_on_check=True)
        self.btn_mode_editor.clicked.connect(lambda: self.set_view_mode("editor"))

        self.btn_mode_preview = QPushButton("G-Code Preview")
        self.btn_mode_preview.setObjectName("viewToggle")
        self.btn_mode_preview.setCheckable(True)
        self.btn_mode_preview.setEnabled(False)
        self._register_icon_btn(self.btn_mode_preview, "view_gcode", size=15, accent_bg_on_check=True)
        self.btn_mode_preview.clicked.connect(lambda: self.set_view_mode("preview"))

        self.btn_mode_basins = QPushButton("2D Basins")
        self.btn_mode_basins.setObjectName("viewToggle")
        self.btn_mode_basins.setCheckable(True)
        self.btn_mode_basins.setEnabled(False)
        self._register_icon_btn(self.btn_mode_basins, "view_2d", size=15, accent_bg_on_check=True)
        self.btn_mode_basins.clicked.connect(lambda: self.set_view_mode("basins"))

        b_row.addWidget(self.btn_mode_editor)
        b_row.addWidget(self.btn_mode_preview)
        b_row.addWidget(self.btn_mode_basins)

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

    def _build_legend_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("floatingCard")
        card.setFixedWidth(212)

        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(5)

        title = QLabel("Feature Types")
        title.setObjectName("sectionTitle")
        vbox.addWidget(title)

        self._feature_checks: Dict[int, QCheckBox] = {}
        for idx, name in enumerate(FEATURE_TYPES):
            row = QHBoxLayout()
            row.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(11, 11)
            swatch.setStyleSheet(
                f"background-color: {_rgb_hex(FEATURE_COLORS[name])}; "
                "border-radius: 3px; border: 1px solid rgba(0,0,0,0.35);"
            )
            chk = QCheckBox(name)
            chk.setChecked(True)
            chk.toggled.connect(self._sync_preview_filters)
            self._feature_checks[idx] = chk
            row.addWidget(swatch)
            row.addWidget(chk, stretch=1)
            vbox.addLayout(row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        vbox.addWidget(sep)

        lbl_ov = QLabel("Overlays")
        lbl_ov.setObjectName("sectionTitle")
        vbox.addWidget(lbl_ov)

        trv_row = QHBoxLayout()
        trv_row.setSpacing(6)
        sw_trv = QLabel()
        sw_trv.setFixedSize(11, 11)
        sw_trv.setStyleSheet(f"background-color: {_rgb_hex(TRAVEL_COLOR)}; border-radius: 3px;")
        self.chk_travels = QCheckBox("Travel moves")
        self.chk_travels.setChecked(False)
        self.chk_travels.toggled.connect(self._sync_preview_filters)
        trv_row.addWidget(sw_trv)
        trv_row.addWidget(self.chk_travels, stretch=1)
        vbox.addLayout(trv_row)

        dos_row = QHBoxLayout()
        dos_row.setSpacing(6)
        sw_dos = QLabel()
        sw_dos.setFixedSize(11, 11)
        sw_dos.setStyleSheet(f"background-color: {_rgb_hex(DOSING_COLOR)}; border-radius: 3px;")
        self.chk_dosing = QCheckBox("Resin dosing")
        self.chk_dosing.setChecked(True)
        self.chk_dosing.toggled.connect(self._sync_preview_filters)
        dos_row.addWidget(sw_dos)
        dos_row.addWidget(self.chk_dosing, stretch=1)
        vbox.addLayout(dos_row)

        vbox.addStretch(1)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("All")
        btn_all.setObjectName("iconSmallBtn")
        btn_all.clicked.connect(lambda: self._set_all_features(True))
        btn_none = QPushButton("None")
        btn_none.setObjectName("iconSmallBtn")
        btn_none.clicked.connect(lambda: self._set_all_features(False))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        vbox.addLayout(btn_row)

        return card

    def _build_right_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(315)
        panel.setMaximumWidth(500)
        root = QVBoxLayout(panel)
        root.setContentsMargins(4, 0, 0, 0)
        root.setSpacing(8)

        cards_holder = QWidget()
        cards_v = QVBoxLayout(cards_holder)
        cards_v.setContentsMargins(0, 0, 6, 0)
        cards_v.setSpacing(8)

        # ---- Presets Card ----
        grp_presets = QGroupBox("Presets")
        p_vbox = QVBoxLayout(grp_presets)
        p_vbox.setSpacing(5)

        self._preset_combos: Dict[str, QComboBox] = {}
        self._preset_mod_labels: Dict[str, QLabel] = {}

        for cat in PRESET_CATEGORIES:
            hdr = QHBoxLayout()
            hdr.setSpacing(4)
            lbl = QLabel(f"{CATEGORY_LABELS[cat]}:")
            lbl.setStyleSheet("font-weight: 600; font-size: 11px;")
            mod_lbl = QLabel("* modified")
            mod_lbl.setObjectName("accentValue")
            mod_lbl.setVisible(False)
            hdr.addWidget(lbl)
            hdr.addStretch(1)
            hdr.addWidget(mod_lbl)
            p_vbox.addLayout(hdr)

            row = QHBoxLayout()
            row.setSpacing(5)
            cb = QComboBox()
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(10)
            cb.currentIndexChanged.connect(
                lambda idx, c=cat: self._on_sidebar_preset_changed(c, idx)
            )
            btn_edit = QPushButton()
            btn_edit.setObjectName("iconSmallBtn")
            btn_edit.setFixedSize(30, 28)
            btn_edit.setToolTip(f"Open {CATEGORY_LABELS[cat]} tab")
            self._register_icon_btn(btn_edit, "settings", size=15)
            btn_edit.clicked.connect(lambda _checked=False, c=cat: self.open_settings_requested.emit(c))

            row.addWidget(cb, stretch=1)
            row.addWidget(btn_edit)
            p_vbox.addLayout(row)

            self._preset_combos[cat] = cb
            self._preset_mod_labels[cat] = mod_lbl

        cards_v.addWidget(grp_presets)

        # ---- Build Plate Objects Card ----
        grp_list = QGroupBox("Build Plate Objects")
        l_vbox = QVBoxLayout(grp_list)
        l_vbox.setSpacing(6)

        self.list_objects = QListWidget()
        self.list_objects.setObjectName("objectList")
        self.list_objects.setFixedHeight(68)
        self.list_objects.currentItemChanged.connect(self._on_object_list_changed)
        l_vbox.addWidget(self.list_objects)

        obj_btns = QHBoxLayout()
        obj_btns.setSpacing(5)
        self.btn_dup_inst = QPushButton("Clone")
        self.btn_dup_inst.setObjectName("iconSmallBtn")
        self.btn_dup_inst.setToolTip("Clone selected object on this build plate")
        self._register_icon_btn(self.btn_dup_inst, "clone", size=14)
        self.btn_dup_inst.clicked.connect(self.duplicate_instance_requested.emit)

        self.btn_move_plate = QPushButton("To Plate...")
        self.btn_move_plate.setObjectName("iconSmallBtn")
        self.btn_move_plate.setToolTip("Move selected object to another build plate")
        self.btn_move_plate.clicked.connect(self.move_to_plate_requested.emit)

        self.btn_del_inst = QPushButton("Remove")
        self.btn_del_inst.setObjectName("iconSmallBtn")
        self.btn_del_inst.setToolTip("Delete selected object")
        self._register_icon_btn(self.btn_del_inst, "delete", size=14)
        self.btn_del_inst.clicked.connect(self.remove_instance_requested.emit)

        obj_btns.addWidget(self.btn_dup_inst)
        obj_btns.addWidget(self.btn_move_plate)
        obj_btns.addWidget(self.btn_del_inst)
        l_vbox.addLayout(obj_btns)
        cards_v.addWidget(grp_list)

        # ---- Object Manipulation Card ----
        grp_obj = QGroupBox("Object Manipulation")
        o_vbox = QVBoxLayout(grp_obj)
        o_vbox.setSpacing(5)

        self.lbl_model_name = QLabel("No object selected")
        self.lbl_model_name.setStyleSheet("font-weight: 700;")
        self.lbl_model_name.setWordWrap(True)
        o_vbox.addWidget(self.lbl_model_name)

        self.lbl_model_dims = QLabel("Size: —   |   Faces: —")
        self.lbl_model_dims.setObjectName("mutedLabel")
        o_vbox.addWidget(self.lbl_model_dims)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        self.sp_pos_x = make_double_spin(110.0, -200.0, 1000.0, 1.0, 1, "")
        self.sp_pos_y = make_double_spin(110.0, -200.0, 1000.0, 1.0, 1, "")
        self.sp_pos_z = make_double_spin(0.0, 0.0, 600.0, 0.5, 1, "")

        self.sp_rot_x = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")
        self.sp_rot_y = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")
        self.sp_rot_z = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")

        self.sp_scale_x = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")
        self.sp_scale_y = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")
        self.sp_scale_z = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")
        self.chk_uniform_scale = QCheckBox("Lock uniform scale")
        self.chk_uniform_scale.setChecked(True)
        self.chk_uniform_scale.toggled.connect(self._on_uniform_toggled)

        compact_spin_qss = (
            "QDoubleSpinBox { padding: 3px 6px; min-height: 16px; } "
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }"
        )
        for w in (
            self.sp_pos_x,
            self.sp_pos_y,
            self.sp_pos_z,
            self.sp_rot_x,
            self.sp_rot_y,
            self.sp_rot_z,
            self.sp_scale_x,
            self.sp_scale_y,
            self.sp_scale_z,
        ):
            w.setStyleSheet(compact_spin_qss)
            w.setMinimumWidth(62)

        for w in (self.sp_pos_x, self.sp_pos_y, self.sp_pos_z, self.sp_rot_x, self.sp_rot_y, self.sp_rot_z):
            w.valueChanged.connect(self._on_transform_edited)

        self.sp_scale_x.valueChanged.connect(lambda v: self._on_scale_axis_edited("x", v))
        self.sp_scale_y.valueChanged.connect(lambda v: self._on_scale_axis_edited("y", v))
        self.sp_scale_z.valueChanged.connect(lambda v: self._on_scale_axis_edited("z", v))

        for col_i, axis_lbl in enumerate(("X", "Y", "Z"), start=1):
            lh = QLabel(axis_lbl)
            lh.setObjectName("mutedLabel")
            lh.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lh, 0, col_i)

        grid.addWidget(QLabel("Pos (mm)"), 1, 0)
        grid.addWidget(self.sp_pos_x, 1, 1)
        grid.addWidget(self.sp_pos_y, 1, 2)
        grid.addWidget(self.sp_pos_z, 1, 3)

        grid.addWidget(QLabel("Rot (°)"), 2, 0)
        grid.addWidget(self.sp_rot_x, 2, 1)
        grid.addWidget(self.sp_rot_y, 2, 2)
        grid.addWidget(self.sp_rot_z, 2, 3)

        grid.addWidget(QLabel("Scale (%)"), 3, 0)
        grid.addWidget(self.sp_scale_x, 3, 1)
        grid.addWidget(self.sp_scale_y, 3, 2)
        grid.addWidget(self.sp_scale_z, 3, 3)

        for c in (1, 2, 3):
            grid.setColumnStretch(c, 1)
        o_vbox.addLayout(grid)

        o_vbox.addWidget(self.chk_uniform_scale)

        # Quick 90° rotate & Place-on-Face shortcut buttons
        q_row = QHBoxLayout()
        q_row.setSpacing(4)
        self.btn_rot_x90 = QPushButton("X +90°")
        self.btn_rot_y90 = QPushButton("Y +90°")
        self.btn_rot_z90 = QPushButton("Z +90°")
        self.btn_place_face_quick = QPushButton("Place Face")
        self.btn_place_face_quick.setCheckable(True)
        for b in (self.btn_rot_x90, self.btn_rot_y90, self.btn_rot_z90, self.btn_place_face_quick):
            b.setObjectName("iconSmallBtn")
        self.btn_rot_x90.clicked.connect(lambda: self.quick_rotate_requested.emit("x", 90.0))
        self.btn_rot_y90.clicked.connect(lambda: self.quick_rotate_requested.emit("y", 90.0))
        self.btn_rot_z90.clicked.connect(lambda: self.quick_rotate_requested.emit("z", 90.0))
        self.btn_place_face_quick.clicked.connect(lambda: self._toggle_edit_tool("place_on_face"))
        q_row.addWidget(self.btn_rot_x90)
        q_row.addWidget(self.btn_rot_y90)
        q_row.addWidget(self.btn_rot_z90)
        q_row.addWidget(self.btn_place_face_quick)
        o_vbox.addLayout(q_row)

        self.btn_reset_xform = QPushButton("Reset Transform")
        self._register_icon_btn(self.btn_reset_xform, "revert", size=14)
        self.btn_reset_xform.clicked.connect(self.reset_transform_requested.emit)
        o_vbox.addWidget(self.btn_reset_xform)
        cards_v.addWidget(grp_obj)

        # ---- Slice Summary Card ----
        grp_sum = QGroupBox("Slice Summary")
        s_vbox = QVBoxLayout(grp_sum)
        s_vbox.setSpacing(6)

        self.lbl_mode_badge = QLabel("HYBRID: FFF + RESIN + UV")
        self.lbl_mode_badge.setObjectName("modeBannerHybrid")
        self.lbl_mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_vbox.addWidget(self.lbl_mode_badge)

        s_form = QFormLayout()
        s_form.setHorizontalSpacing(10)
        s_form.setVerticalSpacing(4)
        self.lbl_stat_layers = QLabel("—")
        self.lbl_stat_cycles = QLabel("—")
        self.lbl_stat_filament = QLabel("—")
        self.lbl_stat_resin = QLabel("—")
        self.lbl_stat_time = QLabel("—")
        self.lbl_stat_lines = QLabel("—")

        for lbl_v in (
            self.lbl_stat_layers,
            self.lbl_stat_cycles,
            self.lbl_stat_filament,
            self.lbl_stat_resin,
            self.lbl_stat_time,
            self.lbl_stat_lines,
        ):
            lbl_v.setStyleSheet("font-weight: 600;")

        s_form.addRow("Layers:", self.lbl_stat_layers)
        s_form.addRow("UV Cure Cycles:", self.lbl_stat_cycles)
        s_form.addRow("Filament:", self.lbl_stat_filament)
        s_form.addRow("Photopolymer:", self.lbl_stat_resin)
        s_form.addRow("Est. Print Time:", self.lbl_stat_time)
        s_form.addRow("G-Code Lines:", self.lbl_stat_lines)
        s_vbox.addLayout(s_form)

        cards_v.addWidget(grp_sum)
        cards_v.addStretch(1)

        sidebar_scroll = wrap_scroll(cards_holder)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(sidebar_scroll, stretch=1)

        # ---- Pinned Action Footer ----
        footer = QFrame()
        footer.setObjectName("card")
        f_vbox = QVBoxLayout(footer)
        f_vbox.setContentsMargins(10, 10, 10, 10)
        f_vbox.setSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        f_vbox.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Ready. Load a model and click Slice Plate.")
        self.lbl_status.setObjectName("mutedLabel")
        self.lbl_status.setWordWrap(True)
        f_vbox.addWidget(self.lbl_status)

        self.btn_slice = QPushButton("Slice Plate")
        self.btn_slice.setObjectName("primaryBtn")
        self._register_icon_btn(self.btn_slice, "slice", size=17)
        self.btn_slice.clicked.connect(self.slice_requested.emit)
        f_vbox.addWidget(self.btn_slice)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        self.btn_slice_all = QPushButton("Slice All Plates")
        self.btn_slice_all.clicked.connect(self.slice_all_plates_requested.emit)
        self.btn_export = QPushButton("Export G-Code...")
        self.btn_export.setEnabled(False)
        self._register_icon_btn(self.btn_export, "export", size=15)
        self.btn_export.clicked.connect(self.export_gcode_requested.emit)
        act_row.addWidget(self.btn_slice_all)
        act_row.addWidget(self.btn_export)
        f_vbox.addLayout(act_row)

        root.addWidget(footer)
        return panel

    # -------------------------------------------------------------------------
    # Public Sync & State API
    # -------------------------------------------------------------------------

    def set_edit_tool(self, tool: str) -> None:
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
        if self._current_tool == tool:
            self.set_edit_tool("select")
        else:
            if self._current_view != "editor":
                self.set_view_mode("editor")
            self.set_edit_tool(tool)

    def update_paint_counts(self, enforcers: int, blockers: int) -> None:
        self.lbl_paint_counts.setText(f"Enforcers: {enforcers}  |  Blockers: {blockers} faces")

    def update_plates(self, plates: List[BuildPlate], active_idx: int) -> None:
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
        self._suppress_obj_list = True
        try:
            self.list_objects.clear()
            sel_row = -1
            for row_idx, inst in enumerate(instances):
                ext = inst.transformed_mesh().extents
                label = f"{inst.name}   ({ext[0]:.0f}×{ext[1]:.0f}×{ext[2]:.0f} mm)"
                item = QListWidgetItem(label)
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
        has_inst = inst is not None
        for w in (
            self.btn_dup_inst,
            self.btn_move_plate,
            self.btn_del_inst,
            self.sp_pos_x,
            self.sp_pos_y,
            self.sp_pos_z,
            self.sp_rot_x,
            self.sp_rot_y,
            self.sp_rot_z,
            self.sp_scale_x,
            self.sp_scale_y,
            self.sp_scale_z,
            self.chk_uniform_scale,
            self.btn_rot_x90,
            self.btn_rot_y90,
            self.btn_rot_z90,
            self.btn_place_face_quick,
            self.btn_reset_xform,
            self.btn_perform_cut,
            self.btn_smart_paint,
            self.btn_clear_paint,
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
                enf, blk = inst.paint_counts()
                self.update_paint_counts(enf, blk)
            else:
                self.lbl_model_name.setText("No object selected")
                self.lbl_model_dims.setText("Size: —   |   Faces: —")
                self.update_paint_counts(0, 0)
        finally:
            self._suppress_transform = False

    def refresh_preset_dropdown(self, category: str, selected_name: str, is_modified: bool) -> None:
        cb = self._preset_combos[category]
        self._suppress_presets = True
        try:
            cb.clear()
            presets = self.preset_manager.list_presets(category)
            sel_idx = 0
            for idx, p in enumerate(presets):
                label = p.name if p.builtin else f"{p.name} (User)"
                cb.addItem(label, userData=p.name)
                if p.name == selected_name:
                    sel_idx = idx
            if presets:
                cb.setCurrentIndex(sel_idx)
        finally:
            self._suppress_presets = False
        self._preset_mod_labels[category].setVisible(is_modified)

    def set_preset_modified(self, category: str, is_modified: bool) -> None:
        self._preset_mod_labels[category].setVisible(is_modified)

    def update_mode_badge(self, cfg: SlicerConfig) -> None:
        pump = cfg.hybrid.enable_resin_pumping
        uv = cfg.hybrid.enable_uv_curing
        if pump and uv:
            self.lbl_mode_badge.setText("HYBRID: FFF + RESIN + UV")
            self.lbl_mode_badge.setObjectName("modeBannerHybrid")
        elif pump and not uv:
            self.lbl_mode_badge.setText("RESIN DOSING ONLY (NO UV)")
            self.lbl_mode_badge.setObjectName("modeBannerHybrid")
        elif not pump and uv:
            self.lbl_mode_badge.setText("FFF + UV SWEEP (NO DOSING)")
            self.lbl_mode_badge.setObjectName("modeBannerHybrid")
        else:
            self.lbl_mode_badge.setText("STANDARD FFF ONLY")
            self.lbl_mode_badge.setObjectName("modeBannerFFF")
        self.lbl_mode_badge.style().unpolish(self.lbl_mode_badge)
        self.lbl_mode_badge.style().polish(self.lbl_mode_badge)

    def set_slice_result(
        self,
        res: Optional[SliceResult],
        preview: Optional[GCodePreviewData],
        switch_to_preview: bool = True,
    ) -> None:
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
            self.lbl_stat_time.setText(_format_duration(res.est_print_time_s))
            self.lbl_stat_lines.setText(f"{res.gcode_line_count:,}")
            self.btn_export.setEnabled(True)
        else:
            self.btn_mode_basins.setEnabled(False)
            self.lbl_stat_layers.setText("—")
            self.lbl_stat_cycles.setText("—")
            self.lbl_stat_filament.setText("—")
            self.lbl_stat_resin.setText("—")
            self.lbl_stat_time.setText("—")
            self.lbl_stat_lines.setText("—")
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

            # Toggle in-viewport overlays for Editor vs Preview
            self._vp_tool_rail.setVisible(not is_prev)
            for w in self._top_edit_widgets:
                w.setVisible(not is_prev)
            self._cut_popover.setVisible((not is_prev) and self._current_tool == "cut")
            self._paint_popover.setVisible((not is_prev) and self._current_tool == "paint_supports")
            self._reposition_viewport_overlays()

        self._refresh_all_icons()

    def jump_to_layer(self, layer_num: int) -> None:
        if self._preview_data is not None and self.btn_mode_preview.isEnabled():
            self.set_view_mode("preview")
            self.layer_bar.set_top_layer(layer_num)
        if self.btn_mode_basins.isEnabled():
            self.inspector_2d.select_layer(layer_num)

    def set_slicing_busy(self, busy: bool, status_text: str = "") -> None:
        self.btn_slice.setEnabled(not busy)
        self.btn_slice_all.setEnabled(not busy)
        if busy:
            self.btn_export.setEnabled(False)
            self.progress_bar.setValue(0)
        if status_text:
            self.lbl_status.setText(status_text)

    def set_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    # -------------------------------------------------------------------------
    # Cut Plane & Paint-on Supports Overlay Slots
    # -------------------------------------------------------------------------

    def _on_viewport_cut_plane_changed(
        self, ox: float, oy: float, oz: float, rx: float, ry: float, rz: float
    ) -> None:
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
        if self._suppress_cut_ui:
            return
        self.viewport.set_cut_plane(
            self.sp_cut_ox.value(),
            self.sp_cut_oy.value(),
            self.sp_cut_oz.value(),
            self.sp_cut_rx.value(),
            self.sp_cut_ry.value(),
            self.sp_cut_rz.value(),
            emit_signal=False,
        )

    def _set_cut_orientation(self, rx: float, ry: float) -> None:
        self._suppress_cut_ui = True
        try:
            self.sp_cut_rx.setValue(rx)
            self.sp_cut_ry.setValue(ry)
            self.sp_cut_rz.setValue(0.0)
        finally:
            self._suppress_cut_ui = False
        self._on_cut_spin_changed()

    def _reset_cut_plane(self) -> None:
        self.viewport._reset_cut_plane_to_selected()

    def _emit_perform_cut(self) -> None:
        keep_up = self.chk_cut_upper.isChecked()
        keep_lo = self.chk_cut_lower.isChecked()
        if not keep_up and not keep_lo:
            return
        n = self.viewport.cut_plane_normal()
        self.cut_performed.emit(
            self.sp_cut_ox.value(),
            self.sp_cut_oy.value(),
            self.sp_cut_oz.value(),
            float(n[0]),
            float(n[1]),
            float(n[2]),
            keep_up,
            keep_lo,
            self.chk_cut_place.isChecked(),
        )

    def _set_paint_brush_mode(self, mode: int) -> None:
        for mv, btn in self._paint_mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(mv == mode)
            btn.blockSignals(False)
        self.viewport.set_paint_brush(self.slider_brush.value() / 10.0, mode)

    def _on_brush_slider_changed(self, raw: int) -> None:
        rad = raw / 10.0
        self.lbl_brush_radius.setText(f"{rad:.1f} mm")
        self.viewport.set_paint_brush(rad, self.viewport.paint_brush_mode)

    # -------------------------------------------------------------------------
    # Internal Slots
    # -------------------------------------------------------------------------

    def _on_sidebar_preset_changed(self, category: str, idx: int) -> None:
        if self._suppress_presets or idx < 0:
            return
        name = self._preset_combos[category].itemData(idx)
        if name:
            self.preset_selected.emit(category, str(name))

    def _on_object_list_changed(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        if self._suppress_obj_list or current is None:
            return
        inst_id = current.data(Qt.ItemDataRole.UserRole)
        if inst_id:
            self.instance_selected.emit(str(inst_id))

    def _on_uniform_toggled(self, checked: bool) -> None:
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
        if self._suppress_transform:
            return
        self._emit_transform()

    def _emit_transform(self) -> None:
        self.instance_transform_changed.emit(
            self.sp_pos_x.value(),
            self.sp_pos_y.value(),
            self.sp_pos_z.value(),
            self.sp_rot_x.value(),
            self.sp_rot_y.value(),
            self.sp_rot_z.value(),
            self.sp_scale_x.value(),
            self.sp_scale_y.value(),
            self.sp_scale_z.value(),
            self.chk_uniform_scale.isChecked(),
        )

    def _on_wireframe_toggled(self, checked: bool) -> None:
        self.viewport.show_infill_wireframe = checked
        self.viewport.update()

    def _on_legend_toggled(self, checked: bool) -> None:
        if self._current_view == "preview":
            self.legend_card.setVisible(checked)

    def _set_all_features(self, enabled: bool) -> None:
        for chk in self._feature_checks.values():
            chk.blockSignals(True)
            chk.setChecked(enabled)
            chk.blockSignals(False)
        self._sync_preview_filters()

    def _sync_preview_filters(self) -> None:
        vis = {idx: chk.isChecked() for idx, chk in self._feature_checks.items()}
        self.viewport.set_preview_filter(
            visible_types=vis,
            show_travels=self.chk_travels.isChecked(),
            show_dosing=self.chk_dosing.isChecked(),
        )

    def _configure_move_slider_for_layer(self, layer_num: int, reset_to_max: bool = True) -> None:
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
        self._stop_playback()
        self._configure_move_slider_for_layer(high, reset_to_max=True)
        self.viewport.set_preview_filter(
            layer_min=low,
            layer_max=high,
            top_move_limit=self.slider_move.value(),
        )

    def _on_move_slider_changed(self, move_limit: int) -> None:
        self.lbl_move_count.setText(f"{move_limit} / {self.slider_move.maximum()}")
        self.viewport.set_preview_filter(top_move_limit=move_limit)

    def _toggle_playback(self) -> None:
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
        if self._play_timer.isActive():
            self._play_timer.stop()
        self.btn_play.setText("Play")
        pal = get_palette(self._resolved_theme)
        self.btn_play.setIcon(make_icon("play", color=pal.icon_fg, accent=pal.icon_accent, size=14))

    def _on_play_tick(self) -> None:
        step = max(1, self.slider_move.maximum() // 90)
        nxt = self.slider_move.value() + step
        if nxt >= self.slider_move.maximum():
            self.slider_move.setValue(self.slider_move.maximum())
            self._stop_playback()
        else:
            self.slider_move.setValue(nxt)
