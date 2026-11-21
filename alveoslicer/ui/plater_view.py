"""PrusaSlicer-style Plater tab combining 3D Model Editor, volumetric G-code Previewer, 2D Basins, and Presets sidebar."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import trimesh
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
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
from ..infill import Capsule
from ..pipeline import SliceResult
from ..presets import CATEGORY_LABELS, PRESET_CATEGORIES, PresetManager
from .inspector_2d import LayerInspector2D
from .layer_slider import LayerRangeBar
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


class PlaterView(QWidget):
    """
    PrusaSlicer-inspired Plater workspace:
      - Left vertical toolstrip (model & camera tools)
      - Center GPU 3D Viewport (3D Editor / G-Code Preview) + 2D Basins toggle
      - G-Code Feature Legend, Dual-Handle Layer Slider, and Sequential Move Scrubber
      - Right sidebar with 5-category Preset dropdowns, Object transforms, Slice summary, & Slice/Export
    """

    # Model & action signals
    add_model_requested = Signal()
    load_sphere_requested = Signal()
    load_wall_requested = Signal()
    center_model_requested = Signal()
    drop_model_requested = Signal()
    clear_model_requested = Signal()
    transform_changed = Signal(float, float, float, float)  # scale_pct, rot_z_deg, pos_x, pos_y
    reset_transform_requested = Signal()

    # Preset & navigation signals
    preset_selected = Signal(str, str)       # (category, preset_name)
    open_settings_requested = Signal(str)    # category

    # Slicing & export signals
    slice_requested = Signal()
    export_gcode_requested = Signal()

    def __init__(self, preset_manager: PresetManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.preset_manager = preset_manager
        self._preview_data: Optional[GCodePreviewData] = None
        self._suppress_transform = False
        self._suppress_presets = False
        self._current_view: str = "editor"  # "editor" | "preview" | "basins"

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._on_play_tick)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 1. Left Toolstrip
        root.addWidget(self._build_left_toolstrip())

        # 2. Center Viewport & Bottom Controls
        root.addWidget(self._build_center_workspace(), stretch=1)

        # 3. Right Sidebar (Presets, Object, Summary, Slice/Export)
        root.addWidget(self._build_right_sidebar())

        self.set_view_mode("editor")

    # -------------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------------

    def _build_left_toolstrip(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("toolStrip")
        strip.setFixedWidth(74)

        vbox = QVBoxLayout(strip)
        vbox.setContentsMargins(5, 8, 5, 8)
        vbox.setSpacing(5)

        def _add_tool(label: str, tip: str, callback) -> QPushButton:
            btn = QPushButton(label)
            btn.setObjectName("toolBtn")
            btn.setToolTip(tip)
            btn.clicked.connect(callback)
            vbox.addWidget(btn)
            return btn

        lbl_m = QLabel("MODEL")
        lbl_m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_m.setStyleSheet("color: #7d8596; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;")
        vbox.addWidget(lbl_m)

        _add_tool("Add...", "Load STL / OBJ / 3MF / STEP model", self.add_model_requested.emit)
        _add_tool("Sphere", "Load built-in test sphere (30 mm)", self.load_sphere_requested.emit)
        _add_tool("Wall", "Load built-in curved test wall (10 mm)", self.load_wall_requested.emit)
        _add_tool("Center", "Center model on the build plate", self.center_model_requested.emit)
        _add_tool("Drop", "Drop model cleanly onto Z = 0", self.drop_model_requested.emit)
        _add_tool("Clear", "Remove model from the build plate", self.clear_model_requested.emit)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #313542;")
        vbox.addWidget(sep)

        lbl_c = QLabel("CAMERA")
        lbl_c.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_c.setStyleSheet("color: #7d8596; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;")
        vbox.addWidget(lbl_c)

        _add_tool("Iso", "Isometric 3D camera", lambda: self.viewport.set_camera_preset("iso"))
        _add_tool("Top", "Top-down orthographic-like view", lambda: self.viewport.set_camera_preset("top"))
        _add_tool("Front", "Front elevation camera", lambda: self.viewport.set_camera_preset("front"))
        _add_tool("Right", "Right side camera", lambda: self.viewport.set_camera_preset("right"))
        _add_tool("Fit", "Fit camera to build plate", lambda: self.viewport.fit_to_bed())

        vbox.addStretch(1)
        return strip

    def _build_center_workspace(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        self.view_stack = QStackedWidget()

        # ---- Page 0: 3D OpenGL Viewport + Side Legend & Layer Slider ----
        gl_page = QWidget()
        gl_row = QHBoxLayout(gl_page)
        gl_row.setContentsMargins(0, 0, 0, 0)
        gl_row.setSpacing(6)

        # G-Code Legend & Feature Filter Card (visible in G-Code Preview mode)
        self.legend_card = self._build_legend_card()
        self.legend_card.setVisible(False)
        gl_row.addWidget(self.legend_card)

        # Main GPU Viewport
        viewport_frame = QFrame()
        viewport_frame.setObjectName("card")
        vf_layout = QVBoxLayout(viewport_frame)
        vf_layout.setContentsMargins(2, 2, 2, 2)
        self.viewport = AlveoViewport3D()
        vf_layout.addWidget(self.viewport)
        gl_row.addWidget(viewport_frame, stretch=1)

        # PrusaSlicer Vertical Dual-Handle Layer Bar (visible in G-Code Preview mode)
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
        self.btn_mode_editor.clicked.connect(lambda: self.set_view_mode("editor"))

        self.btn_mode_preview = QPushButton("G-Code Preview")
        self.btn_mode_preview.setObjectName("viewToggle")
        self.btn_mode_preview.setCheckable(True)
        self.btn_mode_preview.setEnabled(False)
        self.btn_mode_preview.clicked.connect(lambda: self.set_view_mode("preview"))

        self.btn_mode_basins = QPushButton("2D Basins")
        self.btn_mode_basins.setObjectName("viewToggle")
        self.btn_mode_basins.setCheckable(True)
        self.btn_mode_basins.setEnabled(False)
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
        lbl_hint = QLabel("Left-drag: Orbit   |   Right/Middle-drag: Pan   |   Scroll: Zoom")
        lbl_hint.setObjectName("mutedLabel")
        ed_row.addWidget(lbl_hint)
        ed_row.addStretch(1)
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
        self.btn_toggle_legend.toggled.connect(self._on_legend_toggled)
        pr_row.addWidget(self.btn_toggle_legend)

        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("iconSmallBtn")
        self.btn_play.setFixedWidth(52)
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
        self.lbl_move_count.setMinimumWidth(78)
        self.lbl_move_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_move_count.setStyleSheet("color: #f5c518; font-weight: 600; font-size: 11px;")
        pr_row.addWidget(self.lbl_move_count)

        self.preview_controls.setVisible(False)
        b_row.addWidget(self.preview_controls, stretch=1)

        vbox.addWidget(bottom)
        return container

    def _build_legend_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("floatingCard")
        card.setFixedWidth(196)

        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(6)

        title = QLabel("Feature Types")
        title.setObjectName("sectionTitle")
        vbox.addWidget(title)

        self._feature_checks: Dict[int, QCheckBox] = {}
        for idx, name in enumerate(FEATURE_TYPES):
            row = QHBoxLayout()
            row.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background-color: {_rgb_hex(FEATURE_COLORS[name])}; "
                "border-radius: 3px; border: 1px solid #141518;"
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
        sep.setStyleSheet("color: #313542;")
        vbox.addWidget(sep)

        lbl_ov = QLabel("Overlays")
        lbl_ov.setObjectName("sectionTitle")
        vbox.addWidget(lbl_ov)

        # Travel overlay row
        trv_row = QHBoxLayout()
        trv_row.setSpacing(6)
        sw_trv = QLabel()
        sw_trv.setFixedSize(12, 12)
        sw_trv.setStyleSheet(
            f"background-color: {_rgb_hex(TRAVEL_COLOR)}; border-radius: 3px;"
        )
        self.chk_travels = QCheckBox("Travel moves")
        self.chk_travels.setChecked(False)
        self.chk_travels.toggled.connect(self._sync_preview_filters)
        trv_row.addWidget(sw_trv)
        trv_row.addWidget(self.chk_travels, stretch=1)
        vbox.addLayout(trv_row)

        # Resin dosing overlay row
        dos_row = QHBoxLayout()
        dos_row.setSpacing(6)
        sw_dos = QLabel()
        sw_dos.setFixedSize(12, 12)
        sw_dos.setStyleSheet(
            f"background-color: {_rgb_hex(DOSING_COLOR)}; border-radius: 3px;"
        )
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
        panel.setFixedWidth(348)
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Scrollable upper cards
        cards_holder = QWidget()
        cards_v = QVBoxLayout(cards_holder)
        cards_v.setContentsMargins(0, 0, 6, 0)
        cards_v.setSpacing(8)

        # ---- Presets Card ----
        grp_presets = QGroupBox("Presets")
        p_vbox = QVBoxLayout(grp_presets)
        p_vbox.setSpacing(6)

        self._preset_combos: Dict[str, QComboBox] = {}
        self._preset_mod_labels: Dict[str, QLabel] = {}

        for cat in PRESET_CATEGORIES:
            hdr = QHBoxLayout()
            hdr.setSpacing(4)
            lbl = QLabel(f"{CATEGORY_LABELS[cat]}:")
            lbl.setStyleSheet("color: #cfd5df; font-weight: 600; font-size: 11px;")
            mod_lbl = QLabel("* modified")
            mod_lbl.setVisible(False)
            mod_lbl.setStyleSheet("color: #f5c518; font-size: 10px; font-weight: 700;")
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
            btn_edit = QPushButton("Edit")
            btn_edit.setObjectName("iconSmallBtn")
            btn_edit.setFixedWidth(44)
            btn_edit.setToolTip(f"Open {CATEGORY_LABELS[cat]} tab")
            btn_edit.clicked.connect(lambda _checked=False, c=cat: self.open_settings_requested.emit(c))

            row.addWidget(cb, stretch=1)
            row.addWidget(btn_edit)
            p_vbox.addLayout(row)

            self._preset_combos[cat] = cb
            self._preset_mod_labels[cat] = mod_lbl

        cards_v.addWidget(grp_presets)

        # ---- Object Manipulation Card ----
        grp_obj = QGroupBox("Object")
        o_vbox = QVBoxLayout(grp_obj)
        o_vbox.setSpacing(6)

        self.lbl_model_name = QLabel("No model loaded")
        self.lbl_model_name.setStyleSheet("font-weight: 700; color: #eceff4;")
        self.lbl_model_name.setWordWrap(True)
        o_vbox.addWidget(self.lbl_model_name)

        self.lbl_model_dims = QLabel("Size: —   |   Faces: —")
        self.lbl_model_dims.setObjectName("mutedLabel")
        o_vbox.addWidget(self.lbl_model_dims)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        self.sp_scale = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "%")
        self.sp_rot_z = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "°")
        self.sp_pos_x = make_double_spin(110.0, 0.0, 1000.0, 1.0, 1, "mm")
        self.sp_pos_y = make_double_spin(110.0, 0.0, 1000.0, 1.0, 1, "mm")

        for w in (self.sp_scale, self.sp_rot_z, self.sp_pos_x, self.sp_pos_y):
            w.setMinimumWidth(86)
            w.valueChanged.connect(self._on_transform_edited)

        grid.addWidget(QLabel("Scale"), 0, 0)
        grid.addWidget(self.sp_scale, 0, 1)
        grid.addWidget(QLabel("Rot Z"), 0, 2)
        grid.addWidget(self.sp_rot_z, 0, 3)
        grid.addWidget(QLabel("X"), 1, 0)
        grid.addWidget(self.sp_pos_x, 1, 1)
        grid.addWidget(QLabel("Y"), 1, 2)
        grid.addWidget(self.sp_pos_y, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        o_vbox.addLayout(grid)

        self.btn_reset_xform = QPushButton("Reset Transform")
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
            lbl_v.setStyleSheet("font-weight: 600; color: #eceff4;")

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

        self.lbl_status = QLabel("Ready. Load a model and click Slice now.")
        self.lbl_status.setObjectName("mutedLabel")
        self.lbl_status.setWordWrap(True)
        f_vbox.addWidget(self.lbl_status)

        self.btn_slice = QPushButton("Slice now")
        self.btn_slice.setObjectName("primaryBtn")
        self.btn_slice.clicked.connect(self.slice_requested.emit)
        f_vbox.addWidget(self.btn_slice)

        self.btn_export = QPushButton("Export G-Code...")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_gcode_requested.emit)
        f_vbox.addWidget(self.btn_export)

        root.addWidget(footer)
        return panel

    # -------------------------------------------------------------------------
    # Public Sync & State API
    # -------------------------------------------------------------------------

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

    def update_model_display(
        self,
        name: str,
        mesh: Optional[trimesh.Trimesh],
        scale_pct: float = 100.0,
        rot_z_deg: float = 0.0,
        capsules: Optional[List[Capsule]] = None,
        fit_camera: bool = False,
    ) -> None:
        self.viewport.set_mesh(mesh, capsules=capsules)
        self._suppress_transform = True
        try:
            has_mesh = mesh is not None and len(mesh.faces) > 0
            for w in (self.sp_scale, self.sp_rot_z, self.sp_pos_x, self.sp_pos_y, self.btn_reset_xform):
                w.setEnabled(has_mesh)
            if has_mesh and mesh is not None:
                ext = mesh.extents
                ctr = 0.5 * (mesh.bounds[0] + mesh.bounds[1])
                self.lbl_model_name.setText(name)
                self.lbl_model_dims.setText(
                    f"Size: {ext[0]:.1f} × {ext[1]:.1f} × {ext[2]:.1f} mm  |  {len(mesh.faces):,} faces"
                )
                self.sp_scale.setValue(scale_pct)
                self.sp_rot_z.setValue(rot_z_deg)
                self.sp_pos_x.setValue(float(ctr[0]))
                self.sp_pos_y.setValue(float(ctr[1]))
            else:
                self.lbl_model_name.setText("No model loaded")
                self.lbl_model_dims.setText("Size: —   |   Faces: —")
        finally:
            self._suppress_transform = False

        if fit_camera and mesh is not None:
            self.viewport.fit_to_mesh(mesh)

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
            self.viewport.set_gcode_preview(preview)
            self.layer_bar.configure(preview.layer_zs, preview.cure_layers)
            self._configure_move_slider_for_layer(preview.num_layers, reset_to_max=True)
            self.btn_mode_preview.setEnabled(True)
            if res is None:
                # External G-code loaded directly
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

    def jump_to_layer(self, layer_num: int) -> None:
        if self._preview_data is not None and self.btn_mode_preview.isEnabled():
            self.set_view_mode("preview")
            self.layer_bar.set_top_layer(layer_num)
        if self.btn_mode_basins.isEnabled():
            self.inspector_2d.select_layer(layer_num)

    def set_slicing_busy(self, busy: bool, status_text: str = "") -> None:
        self.btn_slice.setEnabled(not busy)
        if busy:
            self.btn_export.setEnabled(False)
            self.progress_bar.setValue(0)
        if status_text:
            self.lbl_status.setText(status_text)

    def set_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    # -------------------------------------------------------------------------
    # Internal Slots
    # -------------------------------------------------------------------------

    def _on_sidebar_preset_changed(self, category: str, idx: int) -> None:
        if self._suppress_presets or idx < 0:
            return
        name = self._preset_combos[category].itemData(idx)
        if name:
            self.preset_selected.emit(category, str(name))

    def _on_transform_edited(self) -> None:
        if self._suppress_transform:
            return
        self.transform_changed.emit(
            self.sp_scale.value(),
            self.sp_rot_z.value(),
            self.sp_pos_x.value(),
            self.sp_pos_y.value(),
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
        else:
            if self.slider_move.value() >= self.slider_move.maximum():
                self.slider_move.setValue(1)
            self.btn_play.setText("Pause")
            self._play_timer.start()

    def _stop_playback(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
        self.btn_play.setText("Play")

    def _on_play_tick(self) -> None:
        cur = self.slider_move.value()
        mx = self.slider_move.maximum()
        step = max(1, mx // 120)
        if cur + step >= mx:
            self.slider_move.setValue(mx)
            self._stop_playback()
        else:
            self.slider_move.setValue(cur + step)
