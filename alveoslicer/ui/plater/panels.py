"""Modular UI panel and floating popover builders for :class:`PlaterView`.

Keeping the declarative widget construction for the Cut Plane popover,
Paint-on-Supports popover, G-code Feature Legend card, and Right Sidebar cards
in a dedicated module keeps :mod:`alveoslicer.ui.plater.view` focused on state
synchronization and user interactions.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Dict, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.presets import CATEGORY_LABELS, PRESET_CATEGORIES
from ...gcode.parser import DOSING_COLOR, FEATURE_COLORS, FEATURE_TYPES, TRAVEL_COLOR
from ..styling.widgets import make_double_spin, wrap_scroll

if TYPE_CHECKING:
    from .view import PlaterView


COMPACT_SPIN_QSS = (
    "QDoubleSpinBox { padding: 2px 5px; min-height: 16px; } "
    "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }"
)

COMPACT_COMBO_QSS = "QComboBox { padding: 2px 6px; min-height: 16px; }"

_SHORT_CAT_LABELS: Dict[str, str] = {
    "print": "Print",
    "filament": "Filament",
    "resin": "Resin",
    "printer": "Printer",
}


def rgb_hex(rgb: Tuple[float, float, float]) -> str:
    """Convert normalized ``(r, g, b)`` floats in ``[0, 1]`` to a ``#rrggbb`` hex string."""
    r = max(0, min(255, int(round(rgb[0] * 255))))
    g = max(0, min(255, int(round(rgb[1] * 255))))
    b = max(0, min(255, int(round(rgb[2] * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``Xh YYm``, ``Xm YYs``, or ``Xs``."""
    sec = max(0, int(round(seconds)))
    hrs, rem = divmod(sec, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def filament_mm_to_grams(length_mm: float, diameter_mm: float, density_g_cm3: float) -> float:
    """Convert extruded filament length (mm) to mass (g) from diameter and material density."""
    radius_mm = max(0.1, float(diameter_mm)) * 0.5
    vol_cm3 = (math.pi * radius_mm * radius_mm * max(0.0, float(length_mm))) / 1000.0
    return vol_cm3 * max(0.0, float(density_g_cm3))


def resin_ml_to_grams(volume_ml: float, density_g_cm3: float) -> float:
    """Convert liquid photopolymer volume (mL == cm^3) to mass (g) from resin density."""
    return max(0.0, float(volume_ml)) * max(0.0, float(density_g_cm3))


def build_cut_popover(view: "PlaterView") -> QFrame:
    """Build the floating contextual Cut Plane popover parented to ``view.viewport``."""
    pop = QFrame(view.viewport)
    pop.setObjectName("viewportPopover")
    pop.setFixedWidth(258)
    cv = QVBoxLayout(pop)
    cv.setContentsMargins(10, 9, 10, 10)
    cv.setSpacing(6)

    cut_hdr = QHBoxLayout()
    lbl_cut_t = QLabel("Cut Plane")
    lbl_cut_t.setObjectName("sectionTitle")
    view.btn_cut_reset = QPushButton("Reset")
    view.btn_cut_reset.setObjectName("iconSmallBtn")
    view.btn_cut_reset.clicked.connect(view._reset_cut_plane)
    cut_hdr.addWidget(lbl_cut_t)
    cut_hdr.addStretch(1)
    cut_hdr.addWidget(view.btn_cut_reset)
    cv.addLayout(cut_hdr)

    cut_grid = QGridLayout()
    cut_grid.setHorizontalSpacing(4)
    cut_grid.setVerticalSpacing(4)

    view.sp_cut_ox = make_double_spin(110.0, -500.0, 1000.0, 1.0, 1, "")
    view.sp_cut_oy = make_double_spin(110.0, -500.0, 1000.0, 1.0, 1, "")
    view.sp_cut_oz = make_double_spin(15.0, -100.0, 600.0, 0.5, 1, "")
    view.sp_cut_rx = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")
    view.sp_cut_ry = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")
    view.sp_cut_rz = make_double_spin(0.0, -180.0, 180.0, 5.0, 1, "")

    for sb in (
        view.sp_cut_ox,
        view.sp_cut_oy,
        view.sp_cut_oz,
        view.sp_cut_rx,
        view.sp_cut_ry,
        view.sp_cut_rz,
    ):
        sb.setStyleSheet(COMPACT_SPIN_QSS)
        sb.valueChanged.connect(view._on_cut_spin_changed)

    for ci, ax in enumerate(("X", "Y", "Z"), start=1):
        lh = QLabel(ax)
        lh.setObjectName("mutedLabel")
        lh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cut_grid.addWidget(lh, 0, ci)

    cut_grid.addWidget(QLabel("Pos (mm)"), 1, 0)
    cut_grid.addWidget(view.sp_cut_ox, 1, 1)
    cut_grid.addWidget(view.sp_cut_oy, 1, 2)
    cut_grid.addWidget(view.sp_cut_oz, 1, 3)

    cut_grid.addWidget(QLabel("Rot (°)"), 2, 0)
    cut_grid.addWidget(view.sp_cut_rx, 2, 1)
    cut_grid.addWidget(view.sp_cut_ry, 2, 2)
    cut_grid.addWidget(view.sp_cut_rz, 2, 3)
    cv.addLayout(cut_grid)

    ori_row = QHBoxLayout()
    ori_row.setSpacing(4)
    for lbl_o, rx, ry in (("Horiz Z", 0.0, 0.0), ("Vert X", 0.0, 90.0), ("Vert Y", 90.0, 0.0)):
        bo = QPushButton(lbl_o)
        bo.setObjectName("iconSmallBtn")
        bo.clicked.connect(lambda _c=False, a=rx, b=ry: view._set_cut_orientation(a, b))
        ori_row.addWidget(bo)
    cv.addLayout(ori_row)

    view.chk_cut_upper = QCheckBox("Keep Upper Part")
    view.chk_cut_upper.setChecked(True)
    view.chk_cut_lower = QCheckBox("Keep Lower Part")
    view.chk_cut_lower.setChecked(True)
    view.chk_cut_place = QCheckBox("Place cut face flat on bed")
    view.chk_cut_place.setChecked(False)
    cv.addWidget(view.chk_cut_upper)
    cv.addWidget(view.chk_cut_lower)
    cv.addWidget(view.chk_cut_place)

    view.btn_perform_cut = QPushButton("Perform Cut")
    view.btn_perform_cut.setObjectName("primaryBtn")
    view.btn_perform_cut.clicked.connect(view._emit_perform_cut)
    cv.addWidget(view.btn_perform_cut)

    lbl_cut_tip = QLabel("Drag yellow handle for Z  |  Shift+drag to tilt")
    lbl_cut_tip.setObjectName("mutedLabel")
    lbl_cut_tip.setWordWrap(True)
    cv.addWidget(lbl_cut_tip)

    pop.setVisible(False)
    return pop


def build_paint_popover(view: "PlaterView") -> QFrame:
    """Build the floating contextual Paint-on-Supports popover parented to ``view.viewport``."""
    pop = QFrame(view.viewport)
    pop.setObjectName("viewportPopover")
    pop.setFixedWidth(258)
    pv = QVBoxLayout(pop)
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
        bm.clicked.connect(lambda _c=False, v=m_val: view._set_paint_brush_mode(v))
        mode_row.addWidget(bm)
        view._paint_mode_btns[m_val] = bm
    pv.addLayout(mode_row)

    rad_hdr = QHBoxLayout()
    rad_lbl = QLabel("Brush radius:")
    rad_lbl.setObjectName("mutedLabel")
    view.lbl_brush_radius = QLabel("4.0 mm")
    view.lbl_brush_radius.setObjectName("accentValue")
    rad_hdr.addWidget(rad_lbl)
    rad_hdr.addStretch(1)
    rad_hdr.addWidget(view.lbl_brush_radius)
    pv.addLayout(rad_hdr)

    view.slider_brush = QSlider(Qt.Orientation.Horizontal)
    view.slider_brush.setRange(10, 200)  # 1.0 .. 20.0 mm in 0.1 mm steps
    view.slider_brush.setValue(40)
    view.slider_brush.valueChanged.connect(view._on_brush_slider_changed)
    pv.addWidget(view.slider_brush)

    smart_row = QHBoxLayout()
    smart_row.setSpacing(5)
    view.sp_smart_angle = make_double_spin(45.0, 10.0, 80.0, 5.0, 0, "°")
    view.sp_smart_angle.setStyleSheet(COMPACT_SPIN_QSS)
    view.sp_smart_angle.setFixedWidth(68)
    view.btn_smart_paint = QPushButton("Smart Fill")
    view.btn_smart_paint.setObjectName("iconSmallBtn")
    view.btn_smart_paint.setToolTip("Automatically paint support enforcers on overhangs steeper than angle")
    view.btn_smart_paint.clicked.connect(
        lambda: view.paint_smart_overhangs_requested.emit(view.sp_smart_angle.value())
    )
    view.btn_clear_paint = QPushButton("Clear")
    view.btn_clear_paint.setObjectName("dangerBtn")
    view.btn_clear_paint.setToolTip("Clear all painted support enforcers and blockers")
    view.btn_clear_paint.clicked.connect(view.paint_clear_requested.emit)
    smart_row.addWidget(view.sp_smart_angle)
    smart_row.addWidget(view.btn_smart_paint, stretch=1)
    smart_row.addWidget(view.btn_clear_paint)
    pv.addLayout(smart_row)

    view.lbl_paint_counts = QLabel("Enforcers: 0  |  Blockers: 0 faces")
    view.lbl_paint_counts.setObjectName("mutedLabel")
    pv.addWidget(view.lbl_paint_counts)

    lbl_pt_tip = QLabel("Left-drag: Enforcer  |  Right-drag: Blocker  |  Shift: Erase")
    lbl_pt_tip.setObjectName("mutedLabel")
    lbl_pt_tip.setWordWrap(True)
    pv.addWidget(lbl_pt_tip)

    pop.setVisible(False)
    return pop


def build_legend_card(view: "PlaterView") -> QFrame:
    """Build the G-code Preview feature-type visibility legend card."""
    card = QFrame()
    card.setObjectName("floatingCard")
    card.setFixedWidth(212)

    vbox = QVBoxLayout(card)
    vbox.setContentsMargins(10, 10, 10, 10)
    vbox.setSpacing(5)

    title = QLabel("Feature Types")
    title.setObjectName("sectionTitle")
    vbox.addWidget(title)

    def _swatch_row(
        color_rgb: Tuple[float, float, float],
        label: str,
        checked: bool,
        bordered: bool = False,
    ) -> QCheckBox:
        row = QHBoxLayout()
        row.setSpacing(6)
        swatch = QLabel()
        swatch.setFixedSize(11, 11)
        border = " border: 1px solid rgba(0,0,0,0.35);" if bordered else ""
        swatch.setStyleSheet(f"background-color: {rgb_hex(color_rgb)}; border-radius: 3px;{border}")
        chk = QCheckBox(label)
        chk.setChecked(checked)
        chk.toggled.connect(view._sync_preview_filters)
        row.addWidget(swatch)
        row.addWidget(chk, stretch=1)
        vbox.addLayout(row)
        return chk

    view._feature_checks: Dict[int, QCheckBox] = {
        idx: _swatch_row(FEATURE_COLORS[name], name, checked=True, bordered=True)
        for idx, name in enumerate(FEATURE_TYPES)
    }

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    vbox.addWidget(sep)

    lbl_ov = QLabel("Overlays")
    lbl_ov.setObjectName("sectionTitle")
    vbox.addWidget(lbl_ov)

    view.chk_travels = _swatch_row(TRAVEL_COLOR, "Travel moves", checked=False)
    view.chk_dosing = _swatch_row(DOSING_COLOR, "Resin dosing", checked=True)

    vbox.addStretch(1)

    btn_row = QHBoxLayout()
    for lbl, state in (("All", True), ("None", False)):
        btn = QPushButton(lbl)
        btn.setObjectName("iconSmallBtn")
        btn.clicked.connect(lambda _c=False, s=state: view._set_all_features(s))
        btn_row.addWidget(btn)
    vbox.addLayout(btn_row)
    return card


def _make_sidebar_card(title: str) -> Tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    """Create a sleek flat sidebar card with a compact header bar and body layout."""
    card = QFrame()
    card.setObjectName("sidebarCard")
    vbox = QVBoxLayout(card)
    vbox.setContentsMargins(10, 7, 10, 8)
    vbox.setSpacing(5)

    hdr = QHBoxLayout()
    hdr.setContentsMargins(0, 0, 0, 0)
    hdr.setSpacing(5)
    lbl = QLabel(title.upper())
    lbl.setObjectName("cardHeader")
    hdr.addWidget(lbl)
    vbox.addLayout(hdr)
    return card, vbox, hdr


def build_right_sidebar(view: "PlaterView") -> QWidget:
    """Build the scrollable right-hand sidebar (Presets, Objects, Transform, Summary) and footer."""
    panel = QWidget()
    panel.setMinimumWidth(315)
    panel.setMaximumWidth(500)
    root = QVBoxLayout(panel)
    root.setContentsMargins(4, 0, 0, 0)
    root.setSpacing(6)

    cards_holder = QWidget()
    cards_v = QVBoxLayout(cards_holder)
    cards_v.setContentsMargins(0, 0, 4, 0)
    cards_v.setSpacing(6)

    cards_v.addWidget(_build_presets_card(view))
    cards_v.addWidget(_build_objects_card(view))
    cards_v.addWidget(_build_transform_card(view))
    cards_v.addWidget(_build_summary_card(view))
    cards_v.addStretch(1)

    sidebar_scroll = wrap_scroll(cards_holder)
    sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    root.addWidget(sidebar_scroll, stretch=1)
    root.addWidget(_build_action_footer(view))
    return panel


def _build_presets_card(view: "PlaterView") -> QFrame:
    """Build the compact 4-category preset grid (Print, Filament, Resin, Printer)."""
    card, p_vbox, hdr = _make_sidebar_card("Presets")
    hdr.addStretch(1)

    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(4)

    view._preset_combos = {}
    view._preset_mod_labels = {}

    for r_idx, cat in enumerate(PRESET_CATEGORIES):
        lbl_wrap = QWidget()
        lbl_wrap.setObjectName("cardInner")
        lbl_wrap.setFixedWidth(58)
        lw_row = QHBoxLayout(lbl_wrap)
        lw_row.setContentsMargins(0, 0, 0, 0)
        lw_row.setSpacing(3)

        lbl = QLabel(_SHORT_CAT_LABELS.get(cat, CATEGORY_LABELS[cat]))
        lbl.setObjectName("mutedLabel")
        lbl.setStyleSheet("font-weight: 600;")
        lbl.setToolTip(CATEGORY_LABELS[cat])

        mod_lbl = QLabel("●")
        mod_lbl.setObjectName("accentValue")
        mod_lbl.setToolTip(f"{CATEGORY_LABELS[cat]} modified from preset")
        mod_lbl.setVisible(False)

        lw_row.addWidget(lbl)
        lw_row.addWidget(mod_lbl)
        lw_row.addStretch(1)

        cb = QComboBox()
        cb.setStyleSheet(COMPACT_COMBO_QSS)
        cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(10)
        cb.currentIndexChanged.connect(lambda idx, c=cat: view._on_sidebar_preset_changed(c, idx))

        btn_edit = QPushButton()
        btn_edit.setObjectName("cardIconBtn")
        btn_edit.setFixedSize(24, 24)
        btn_edit.setToolTip(f"Open {CATEGORY_LABELS[cat]} tab")
        view._register_icon_btn(btn_edit, "settings", size=14)
        btn_edit.clicked.connect(lambda _checked=False, c=cat: view.open_settings_requested.emit(c))

        grid.addWidget(lbl_wrap, r_idx, 0)
        grid.addWidget(cb, r_idx, 1)
        grid.addWidget(btn_edit, r_idx, 2)

        view._preset_combos[cat] = cb
        view._preset_mod_labels[cat] = mod_lbl

    grid.setColumnStretch(1, 1)
    p_vbox.addLayout(grid)
    return card


def _build_objects_card(view: "PlaterView") -> QFrame:
    """Build the streamlined Build Plate Objects card with inline icon toolbar and compact rows."""
    card, l_vbox, hdr = _make_sidebar_card("Build Plate Objects")

    view.lbl_obj_count = QLabel("0")
    view.lbl_obj_count.setObjectName("countPill")
    view.lbl_obj_count.setFixedHeight(18)
    view.lbl_obj_count.setMinimumWidth(20)
    view.lbl_obj_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hdr.addWidget(view.lbl_obj_count)
    hdr.addStretch(1)

    view.btn_add_obj = QPushButton()
    view.btn_add_obj.setObjectName("cardIconBtn")
    view.btn_add_obj.setFixedSize(24, 24)
    view.btn_add_obj.setToolTip("Add 3D model to active build plate (Ctrl+O)")
    view._register_icon_btn(view.btn_add_obj, "add_model", size=15)
    view.btn_add_obj.clicked.connect(view.add_model_requested.emit)

    view.btn_dup_inst = QPushButton()
    view.btn_dup_inst.setObjectName("cardIconBtn")
    view.btn_dup_inst.setFixedSize(24, 24)
    view.btn_dup_inst.setToolTip("Clone selected object (Ctrl+D)")
    view._register_icon_btn(view.btn_dup_inst, "clone", size=14)
    view.btn_dup_inst.clicked.connect(view.duplicate_instance_requested.emit)

    view.btn_move_plate = QPushButton()
    view.btn_move_plate.setObjectName("cardIconBtn")
    view.btn_move_plate.setFixedSize(24, 24)
    view.btn_move_plate.setToolTip("Move selected object to another build plate")
    view._register_icon_btn(view.btn_move_plate, "move_plate", size=15)
    view.btn_move_plate.clicked.connect(view.move_to_plate_requested.emit)

    view.btn_del_inst = QPushButton()
    view.btn_del_inst.setObjectName("cardIconBtn")
    view.btn_del_inst.setFixedSize(24, 24)
    view.btn_del_inst.setToolTip("Remove selected object (Del)")
    view._register_icon_btn(view.btn_del_inst, "delete", size=14)
    view.btn_del_inst.clicked.connect(view.remove_instance_requested.emit)

    for b in (view.btn_add_obj, view.btn_dup_inst, view.btn_move_plate, view.btn_del_inst):
        hdr.addWidget(b)

    view.list_objects = QListWidget()
    view.list_objects.setObjectName("objectList")
    view.list_objects.setResizeMode(QListWidget.ResizeMode.Adjust)
    view.list_objects.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    view.list_objects.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.list_objects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    view.list_objects.customContextMenuRequested.connect(view._show_object_context_menu)
    view.list_objects.currentItemChanged.connect(view._on_object_list_changed)
    view.list_objects.setFixedHeight(36)
    l_vbox.addWidget(view.list_objects)

    view.lbl_objects_empty = QLabel("No models on this plate — click + to import.")
    view.lbl_objects_empty.setObjectName("mutedLabel")
    view.lbl_objects_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
    view.lbl_objects_empty.setStyleSheet("padding: 4px 0;")
    view.lbl_objects_empty.setVisible(False)
    l_vbox.addWidget(view.lbl_objects_empty)
    return card


def _build_transform_card(view: "PlaterView") -> QFrame:
    """Build the Object Manipulation card with collapsible empty state and RGB axis grid."""
    card, o_vbox, hdr = _make_sidebar_card("Object Manipulation")
    hdr.addStretch(1)

    view.btn_place_face_quick = QPushButton()
    view.btn_place_face_quick.setObjectName("cardIconBtn")
    view.btn_place_face_quick.setCheckable(True)
    view.btn_place_face_quick.setFixedSize(24, 24)
    view.btn_place_face_quick.setToolTip("Place on Face (F) — click any highlighted face to lay flat")
    view._register_icon_btn(view.btn_place_face_quick, "place_on_face", size=15)
    view.btn_place_face_quick.clicked.connect(lambda: view._toggle_edit_tool("place_on_face"))

    view.btn_reset_xform = QPushButton()
    view.btn_reset_xform.setObjectName("cardIconBtn")
    view.btn_reset_xform.setFixedSize(24, 24)
    view.btn_reset_xform.setToolTip("Reset position, rotation, and scale")
    view._register_icon_btn(view.btn_reset_xform, "revert", size=14)
    view.btn_reset_xform.clicked.connect(view.reset_transform_requested.emit)

    hdr.addWidget(view.btn_place_face_quick)
    hdr.addWidget(view.btn_reset_xform)

    view._transform_empty_lbl = QLabel("Click an object on the plate to move, rotate, or scale.")
    view._transform_empty_lbl.setObjectName("mutedLabel")
    view._transform_empty_lbl.setWordWrap(True)
    o_vbox.addWidget(view._transform_empty_lbl)

    view._transform_body = QWidget()
    view._transform_body.setObjectName("cardInner")
    body_v = QVBoxLayout(view._transform_body)
    body_v.setContentsMargins(0, 0, 0, 0)
    body_v.setSpacing(4)

    meta_row = QHBoxLayout()
    meta_row.setContentsMargins(0, 0, 0, 0)
    meta_row.setSpacing(6)
    view.lbl_model_name = QLabel("No object selected")
    view.lbl_model_name.setStyleSheet("font-weight: 700; font-size: 11px;")
    view.lbl_model_dims = QLabel("—")
    view.lbl_model_dims.setObjectName("mutedLabel")
    view.lbl_model_dims.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    meta_row.addWidget(view.lbl_model_name)
    meta_row.addStretch(1)
    meta_row.addWidget(view.lbl_model_dims)
    body_v.addLayout(meta_row)

    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(3)

    view.sp_pos_x = make_double_spin(110.0, -200.0, 1000.0, 1.0, 1, "")
    view.sp_pos_y = make_double_spin(110.0, -200.0, 1000.0, 1.0, 1, "")
    view.sp_pos_z = make_double_spin(0.0, 0.0, 600.0, 0.5, 1, "")
    view.sp_rot_x = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")
    view.sp_rot_y = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")
    view.sp_rot_z = make_double_spin(0.0, -180.0, 180.0, 15.0, 1, "")
    view.sp_scale_x = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")
    view.sp_scale_y = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")
    view.sp_scale_z = make_double_spin(100.0, 5.0, 2000.0, 5.0, 1, "")

    view.chk_uniform_scale = QCheckBox("Uniform")
    view.chk_uniform_scale.setToolTip("Lock proportional X / Y / Z scaling")
    view.chk_uniform_scale.setChecked(True)
    view.chk_uniform_scale.toggled.connect(view._on_uniform_toggled)

    all_spins = (
        view.sp_pos_x, view.sp_pos_y, view.sp_pos_z,
        view.sp_rot_x, view.sp_rot_y, view.sp_rot_z,
        view.sp_scale_x, view.sp_scale_y, view.sp_scale_z,
    )
    for w in all_spins:
        w.setStyleSheet(COMPACT_SPIN_QSS)
        w.setMinimumWidth(58)

    for w in all_spins[:6]:
        w.valueChanged.connect(view._on_transform_edited)

    view.sp_scale_x.valueChanged.connect(lambda v: view._on_scale_axis_edited("x", v))
    view.sp_scale_y.valueChanged.connect(lambda v: view._on_scale_axis_edited("y", v))
    view.sp_scale_z.valueChanged.connect(lambda v: view._on_scale_axis_edited("z", v))

    for col_i, (axis_lbl, axis_col) in enumerate(
        (("X", "#e85d5d"), ("Y", "#4cc374"), ("Z", "#5b9bff")), start=1
    ):
        lh = QLabel(axis_lbl)
        lh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lh.setStyleSheet(f"color: {axis_col}; font-size: 10px; font-weight: 700;")
        grid.addWidget(lh, 0, col_i)

    rows = (
        ("Pos (mm)", view.sp_pos_x, view.sp_pos_y, view.sp_pos_z),
        ("Rot (°)", view.sp_rot_x, view.sp_rot_y, view.sp_rot_z),
        ("Scale (%)", view.sp_scale_x, view.sp_scale_y, view.sp_scale_z),
    )
    for r_idx, (r_lbl, wx, wy, wz) in enumerate(rows, start=1):
        rl = QLabel(r_lbl)
        rl.setObjectName("mutedLabel")
        grid.addWidget(rl, r_idx, 0)
        grid.addWidget(wx, r_idx, 1)
        grid.addWidget(wy, r_idx, 2)
        grid.addWidget(wz, r_idx, 3)

    for c in (1, 2, 3):
        grid.setColumnStretch(c, 1)
    body_v.addLayout(grid)

    q_row = QHBoxLayout()
    q_row.setContentsMargins(0, 2, 0, 0)
    q_row.setSpacing(4)
    q_row.addWidget(view.chk_uniform_scale)
    q_row.addStretch(1)

    view.btn_rot_x90 = QPushButton("X+90°")
    view.btn_rot_y90 = QPushButton("Y+90°")
    view.btn_rot_z90 = QPushButton("Z+90°")
    for b in (view.btn_rot_x90, view.btn_rot_y90, view.btn_rot_z90):
        b.setObjectName("miniChipBtn")
        b.setToolTip(f"Rotate selected object {b.text()}")
        q_row.addWidget(b)
    view.btn_rot_x90.clicked.connect(lambda: view.quick_rotate_requested.emit("x", 90.0))
    view.btn_rot_y90.clicked.connect(lambda: view.quick_rotate_requested.emit("y", 90.0))
    view.btn_rot_z90.clicked.connect(lambda: view.quick_rotate_requested.emit("z", 90.0))
    body_v.addLayout(q_row)

    o_vbox.addWidget(view._transform_body)
    return card


def _build_summary_card(view: "PlaterView") -> QFrame:
    """Build the compact Slice Summary card with 2x2 stat tiles and live m/g & mL/g unit toggles."""
    card, s_vbox, hdr = _make_sidebar_card("Slice Summary")
    hdr.addStretch(1)

    view.lbl_mode_badge = QLabel("HYBRID")
    view.lbl_mode_badge.setObjectName("modeBannerHybrid")
    view.lbl_mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hdr.addWidget(view.lbl_mode_badge)

    # 2x2 compact single-line stat tiles
    tiles_grid = QGridLayout()
    tiles_grid.setContentsMargins(0, 0, 0, 0)
    tiles_grid.setHorizontalSpacing(5)
    tiles_grid.setVerticalSpacing(4)

    def _make_tile(label_txt: str) -> Tuple[QFrame, QLabel]:
        tile = QFrame()
        tile.setObjectName("statTile")
        th = QHBoxLayout(tile)
        th.setContentsMargins(8, 5, 8, 5)
        th.setSpacing(6)
        lt = QLabel(label_txt)
        lt.setObjectName("statLabel")
        lv = QLabel("—")
        lv.setObjectName("statValue")
        lv.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th.addWidget(lt)
        th.addStretch(1)
        th.addWidget(lv)
        return tile, lv

    t_layers, view.lbl_stat_layers = _make_tile("Layers")
    t_cycles, view.lbl_stat_cycles = _make_tile("UV Cures")
    t_time, view.lbl_stat_time = _make_tile("Time")
    t_lines, view.lbl_stat_lines = _make_tile("Lines")

    tiles_grid.addWidget(t_layers, 0, 0)
    tiles_grid.addWidget(t_cycles, 0, 1)
    tiles_grid.addWidget(t_time, 1, 0)
    tiles_grid.addWidget(t_lines, 1, 1)
    tiles_grid.setColumnStretch(0, 1)
    tiles_grid.setColumnStretch(1, 1)
    s_vbox.addLayout(tiles_grid)

    # Material usage rows with inline m/g and mL/g unit toggles
    def _make_material_row(
        label_txt: str,
        unit_a: str,
        unit_b: str,
        cb_a,
        cb_b,
    ) -> Tuple[QFrame, QLabel, QPushButton, QPushButton]:
        row_box = QFrame()
        row_box.setObjectName("statTile")
        rh = QHBoxLayout(row_box)
        rh.setContentsMargins(8, 4, 6, 4)
        rh.setSpacing(5)

        lbl = QLabel(label_txt)
        lbl.setObjectName("mutedLabel")
        lbl.setStyleSheet("font-weight: 600;")

        val = QLabel("—")
        val.setObjectName("statValue")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        ba = QPushButton(unit_a)
        ba.setObjectName("unitToggle")
        ba.setCheckable(True)
        ba.setChecked(True)
        ba.setToolTip(f"Show {label_txt.lower()} in {unit_a}")
        ba.clicked.connect(cb_a)

        bb = QPushButton(unit_b)
        bb.setObjectName("unitToggle")
        bb.setCheckable(True)
        bb.setChecked(False)
        bb.setToolTip(f"Show {label_txt.lower()} in {unit_b} (using material density)")
        bb.clicked.connect(cb_b)

        rh.addWidget(lbl)
        rh.addStretch(1)
        rh.addWidget(val)
        rh.addWidget(ba)
        rh.addWidget(bb)
        return row_box, val, ba, bb

    fil_box, view.lbl_stat_filament, view.btn_fil_unit_m, view.btn_fil_unit_g = _make_material_row(
        "Filament", "m", "g",
        lambda: view.set_filament_unit("m"),
        lambda: view.set_filament_unit("g"),
    )
    res_box, view.lbl_stat_resin, view.btn_res_unit_ml, view.btn_res_unit_g = _make_material_row(
        "Resin", "mL", "g",
        lambda: view.set_resin_unit("ml"),
        lambda: view.set_resin_unit("g"),
    )
    s_vbox.addWidget(fil_box)
    s_vbox.addWidget(res_box)
    return card


def _build_action_footer(view: "PlaterView") -> QFrame:
    """Build the pinned bottom action card (progress bar, Slice Plate, Slice All, Export)."""
    footer = QFrame()
    footer.setObjectName("card")
    f_vbox = QVBoxLayout(footer)
    f_vbox.setContentsMargins(10, 10, 10, 10)
    f_vbox.setSpacing(6)

    view.progress_bar = QProgressBar()
    view.progress_bar.setRange(0, 100)
    view.progress_bar.setValue(0)
    f_vbox.addWidget(view.progress_bar)

    view.lbl_status = QLabel("Ready. Load a model and click Slice Plate.")
    view.lbl_status.setObjectName("mutedLabel")
    view.lbl_status.setWordWrap(True)
    f_vbox.addWidget(view.lbl_status)

    view.btn_slice = QPushButton("Slice Plate")
    view.btn_slice.setObjectName("primaryBtn")
    view._register_icon_btn(view.btn_slice, "slice", size=17)
    view.btn_slice.clicked.connect(view.slice_requested.emit)
    f_vbox.addWidget(view.btn_slice)

    act_row = QHBoxLayout()
    act_row.setSpacing(6)
    view.btn_slice_all = QPushButton("Slice All Plates")
    view.btn_slice_all.clicked.connect(view.slice_all_plates_requested.emit)
    view.btn_export = QPushButton("Export G-Code...")
    view.btn_export.setEnabled(False)
    view._register_icon_btn(view.btn_export, "export", size=15)
    view.btn_export.clicked.connect(view.export_gcode_requested.emit)
    act_row.addWidget(view.btn_slice_all)
    act_row.addWidget(view.btn_export)
    f_vbox.addLayout(act_row)
    return footer
