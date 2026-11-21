"""PrusaSlicer-style two-pane settings tabs with full PrusaSlicer parameters, vector icons, and preset management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    BED_SHAPES,
    BRIM_TYPES,
    CELL_PATTERNS,
    FILAMENT_TYPES,
    FUZZY_SKIN_MODES,
    GCODE_FLAVORS,
    INFILL_ENGINES,
    SEAM_POSITIONS,
    STANDARD_INFILL_PATTERNS,
    SUPPORT_MODES,
    SUPPORT_PATTERNS,
    SUPPORT_STYLES,
    TOP_BOTTOM_FILL_PATTERNS,
    SlicerConfig,
)
from ..presets import CATEGORY_LABELS, PresetManager, apply_category_values, extract_category_values
from .icons import make_icon
from .theme import get_palette
from .widgets import make_double_spin, make_spin, wrap_scroll


@dataclass
class _FieldBinding:
    key: str
    widget: QWidget
    getter: Callable[[], Any]
    setter: Callable[[Any], None]


class CategorySettingsTab(QWidget):
    """
    PrusaSlicer-style settings tab for a single preset category:
      - Top Preset Bar (dropdown, modified pill, Save, Save As, Revert, Delete with crisp icons)
      - Resizable QSplitter with Left Icon Category Sidebar (QListWidget) + Right Stacked Scrollable Form Cards
    """

    values_changed = Signal(str)          # emitted when any field changes (category)
    preset_selected = Signal(str, str)    # emitted when user picks a preset (category, name)
    presets_mutated = Signal(str)         # emitted when user saves or deletes a preset (category)

    def __init__(
        self,
        category: str,
        preset_manager: PresetManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.category = category
        self.preset_manager = preset_manager
        self._active_preset_name: str = preset_manager.default_preset_name(category)
        self._bindings: Dict[str, _FieldBinding] = {}
        self._suppress_signals = False
        self._post_change_hooks: List[Callable[[], None]] = []
        self._section_icons: List[Tuple[QListWidgetItem, str]] = []
        self._resolved_theme: str = "dark"

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---- Top Preset Management Bar ----
        bar = QFrame()
        bar.setObjectName("presetBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)

        lbl_title = QLabel(f"{CATEGORY_LABELS[category]} Preset:")
        lbl_title.setStyleSheet("font-weight: 700;")
        bar_layout.addWidget(lbl_title)

        self.cb_preset = QComboBox()
        self.cb_preset.setMinimumWidth(290)
        self.cb_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
        bar_layout.addWidget(self.cb_preset, stretch=1)

        self.lbl_modified = QLabel("Modified")
        self.lbl_modified.setObjectName("modifiedPill")
        self.lbl_modified.setVisible(False)
        bar_layout.addWidget(self.lbl_modified)

        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Overwrite the current custom user preset")
        self.btn_save.clicked.connect(self._save_current_preset)
        bar_layout.addWidget(self.btn_save)

        self.btn_save_as = QPushButton("Save As...")
        self.btn_save_as.setToolTip("Save current parameters as a new reusable preset")
        self.btn_save_as.clicked.connect(self._save_preset_as)
        bar_layout.addWidget(self.btn_save_as)

        self.btn_revert = QPushButton("Revert")
        self.btn_revert.setToolTip("Reset modified parameters back to the selected preset")
        self.btn_revert.clicked.connect(self._revert_to_preset)
        bar_layout.addWidget(self.btn_revert)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_delete.setToolTip("Delete the selected user preset")
        self.btn_delete.clicked.connect(self._delete_current_preset)
        bar_layout.addWidget(self.btn_delete)

        root.addWidget(bar)

        # ---- Resizable Two-Pane Body (Left Sidebar + Right Form Stack) ----
        body_split = QSplitter(Qt.Orientation.Horizontal)
        body_split.setChildrenCollapsible(False)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("categoryList")
        self.sidebar.setIconSize(QSize(16, 16))
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.currentRowChanged.connect(self._on_section_changed)
        body_split.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        body_split.addWidget(self.pages)
        body_split.setStretchFactor(0, 0)
        body_split.setStretchFactor(1, 1)
        body_split.setSizes([224, 960])

        root.addWidget(body_split, stretch=1)

        self.refresh_preset_list(select_name=self._active_preset_name)
        self.apply_theme("dark")

    # -------------------------------------------------------------------------
    # Theme & Icons
    # -------------------------------------------------------------------------

    def apply_theme(self, resolved_mode: str) -> None:
        self._resolved_theme = resolved_mode
        pal = get_palette(resolved_mode)
        self.btn_save.setIcon(make_icon("save", pal.icon_fg, pal.icon_accent, 15))
        self.btn_save_as.setIcon(make_icon("plus", pal.icon_fg, pal.icon_accent, 15))
        self.btn_revert.setIcon(make_icon("revert", pal.icon_fg, pal.icon_accent, 15))
        self.btn_delete.setIcon(make_icon("delete", pal.danger_fg, pal.danger_fg, 15))
        for item, icon_name in self._section_icons:
            item.setIcon(make_icon(icon_name, pal.icon_fg, pal.icon_accent, 16))

    # -------------------------------------------------------------------------
    # Page & Field Registration API
    # -------------------------------------------------------------------------

    def add_section(self, title: str, content_widget: QWidget, icon_name: str = "settings") -> None:
        pal = get_palette(self._resolved_theme)
        item = QListWidgetItem(make_icon(icon_name, pal.icon_fg, pal.icon_accent, 16), title)
        self.sidebar.addItem(item)
        self._section_icons.append((item, icon_name))
        self.pages.addWidget(wrap_scroll(content_widget))
        if self.sidebar.count() == 1:
            self.sidebar.setCurrentRow(0)

    def add_post_change_hook(self, fn: Callable[[], None]) -> None:
        self._post_change_hooks.append(fn)

    def bind_dspin(
        self,
        key: str,
        val: float,
        lo: float,
        hi: float,
        step: float = 0.1,
        decimals: int = 2,
        suffix: str = "",
        tooltip: str = "",
    ) -> QDoubleSpinBox:
        sb = make_double_spin(val, lo, hi, step, decimals, suffix)
        if tooltip:
            sb.setToolTip(tooltip)
        sb.valueChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(key, sb, sb.value, lambda v, w=sb: w.setValue(float(v)))
        return sb

    def bind_spin(
        self,
        key: str,
        val: int,
        lo: int,
        hi: int,
        step: int = 1,
        suffix: str = "",
        tooltip: str = "",
    ) -> QSpinBox:
        sb = make_spin(val, lo, hi, step, suffix)
        if tooltip:
            sb.setToolTip(tooltip)
        sb.valueChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(key, sb, sb.value, lambda v, w=sb: w.setValue(int(v)))
        return sb

    def bind_combo(
        self, key: str, items: List[str], current: str, tooltip: str = ""
    ) -> QComboBox:
        cb = QComboBox()
        cb.addItems(items)
        cb.setCurrentText(current)
        if tooltip:
            cb.setToolTip(tooltip)
        cb.currentTextChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(
            key, cb, cb.currentText, lambda v, w=cb: w.setCurrentText(str(v))
        )
        return cb

    def bind_line_edit(self, key: str, val: str, fallback: str = "", tooltip: str = "") -> QLineEdit:
        ed = QLineEdit(val)
        if tooltip:
            ed.setToolTip(tooltip)
        ed.textChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(
            key,
            ed,
            lambda w=ed, fb=fallback: w.text().strip() or fb,
            lambda v, w=ed: w.setText(str(v)),
        )
        return ed

    def bind_plain_text(
        self, key: str, val: str, placeholder: str = "", height: int = 86, tooltip: str = ""
    ) -> QPlainTextEdit:
        ed = QPlainTextEdit(val)
        ed.setPlaceholderText(placeholder)
        ed.setFixedHeight(height)
        if tooltip:
            ed.setToolTip(tooltip)
        ed.textChanged.connect(self._on_any_widget_changed)

        def _set_text(v: Any, w: QPlainTextEdit = ed) -> None:
            txt = "" if v is None else str(v)
            if w.toPlainText() != txt:
                w.setPlainText(txt)

        self._bindings[key] = _FieldBinding(
            key,
            ed,
            lambda w=ed: w.toPlainText(),
            _set_text,
        )
        return ed

    def bind_checkbox(self, key: str, label: str, checked: bool, tooltip: str = "") -> QCheckBox:
        chk = QCheckBox(label)
        chk.setChecked(checked)
        if tooltip:
            chk.setToolTip(tooltip)
        chk.toggled.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(
            key, chk, chk.isChecked, lambda v, w=chk: w.setChecked(bool(v))
        )
        return chk

    def bind_optional_dspin(
        self,
        key: str,
        check_label: str,
        default_val: float,
        lo: float,
        hi: float,
        step: float = 1.0,
        decimals: int = 1,
        suffix: str = "mm",
    ) -> Tuple[QCheckBox, QDoubleSpinBox]:
        chk = QCheckBox(check_label)
        sb = make_double_spin(default_val, lo, hi, step, decimals, suffix)
        sb.setEnabled(False)

        def _sync() -> None:
            sb.setEnabled(chk.isChecked())
            self._on_any_widget_changed()

        chk.toggled.connect(_sync)
        sb.valueChanged.connect(self._on_any_widget_changed)

        def _get() -> Optional[float]:
            return sb.value() if chk.isChecked() else None

        def _set(v: Any) -> None:
            if v is None:
                chk.setChecked(False)
                sb.setEnabled(False)
            else:
                chk.setChecked(True)
                sb.setValue(float(v))
                sb.setEnabled(True)

        self._bindings[key] = _FieldBinding(key, sb, _get, _set)
        return chk, sb

    # -------------------------------------------------------------------------
    # Config & Preset Sync
    # -------------------------------------------------------------------------

    @property
    def active_preset_name(self) -> str:
        return self._active_preset_name

    def current_values(self) -> Dict[str, Any]:
        return {k: b.getter() for k, b in self._bindings.items()}

    def load_from_config(self, cfg: SlicerConfig) -> None:
        vals = extract_category_values(cfg, self.category)
        self._suppress_signals = True
        try:
            for k, v in vals.items():
                if k in self._bindings:
                    self._bindings[k].setter(v)
        finally:
            self._suppress_signals = False
        for hook in self._post_change_hooks:
            hook()
        self.update_modified_indicator(cfg)

    def write_to_config(self, cfg: SlicerConfig) -> None:
        apply_category_values(cfg, self.category, self.current_values())

    def refresh_preset_list(self, select_name: Optional[str] = None) -> None:
        target = select_name or self._active_preset_name
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        presets = self.preset_manager.list_presets(self.category)
        selected_idx = 0
        for idx, p in enumerate(presets):
            label = p.name if p.builtin else f"{p.name}  (User)"
            self.cb_preset.addItem(label, userData=p.name)
            if p.name == target:
                selected_idx = idx
        if presets:
            self.cb_preset.setCurrentIndex(selected_idx)
            self._active_preset_name = presets[selected_idx].name
        self.cb_preset.blockSignals(False)
        self._update_preset_buttons()

    def select_preset_by_name(self, name: str, emit: bool = False) -> None:
        self.cb_preset.blockSignals(True)
        for idx in range(self.cb_preset.count()):
            if self.cb_preset.itemData(idx) == name:
                self.cb_preset.setCurrentIndex(idx)
                self._active_preset_name = name
                break
        self.cb_preset.blockSignals(False)
        self._update_preset_buttons()
        if emit:
            self.preset_selected.emit(self.category, self._active_preset_name)

    def update_modified_indicator(self, cfg: SlicerConfig) -> None:
        modified = self.preset_manager.is_modified(cfg, self.category, self._active_preset_name)
        self.lbl_modified.setVisible(modified)
        self.btn_revert.setEnabled(modified)
        is_user = not self.preset_manager.is_builtin(self.category, self._active_preset_name)
        self.btn_save.setEnabled(is_user and modified)
        self.btn_delete.setEnabled(is_user)

    def _update_preset_buttons(self) -> None:
        is_user = not self.preset_manager.is_builtin(self.category, self._active_preset_name)
        self.btn_delete.setEnabled(is_user)
        if not is_user:
            self.btn_save.setEnabled(False)

    # -------------------------------------------------------------------------
    # Internal Slots
    # -------------------------------------------------------------------------

    def _on_section_changed(self, row: int) -> None:
        if 0 <= row < self.pages.count():
            self.pages.setCurrentIndex(row)

    def _on_any_widget_changed(self, *_args: Any) -> None:
        if self._suppress_signals:
            return
        for hook in self._post_change_hooks:
            hook()
        self.values_changed.emit(self.category)

    def _on_preset_combo_changed(self, idx: int) -> None:
        if idx < 0:
            return
        name = self.cb_preset.itemData(idx)
        if name:
            self._active_preset_name = str(name)
            self._update_preset_buttons()
            self.preset_selected.emit(self.category, self._active_preset_name)

    def _save_current_preset(self) -> None:
        if self.preset_manager.is_builtin(self.category, self._active_preset_name):
            self._save_preset_as()
            return
        try:
            self.preset_manager.save_user_preset(
                self.category, self._active_preset_name, self.current_values()
            )
            self.presets_mutated.emit(self.category)
        except Exception as exc:
            QMessageBox.warning(self, "Save Preset", str(exc))

    def _save_preset_as(self) -> None:
        suggested = f"{self._active_preset_name} - Custom"
        name, ok = QInputDialog.getText(
            self,
            f"Save {CATEGORY_LABELS[self.category]} Preset",
            "Enter new preset name:",
            text=suggested,
        )
        if not ok or not name.strip():
            return
        try:
            preset = self.preset_manager.save_user_preset(
                self.category, name.strip(), self.current_values()
            )
            self._active_preset_name = preset.name
            self.refresh_preset_list(select_name=preset.name)
            self.presets_mutated.emit(self.category)
        except Exception as exc:
            QMessageBox.warning(self, "Save Preset Error", str(exc))

    def _revert_to_preset(self) -> None:
        self.preset_selected.emit(self.category, self._active_preset_name)

    def _delete_current_preset(self) -> None:
        name = self._active_preset_name
        if self.preset_manager.is_builtin(self.category, name):
            return
        reply = QMessageBox.question(
            self,
            "Delete Preset",
            f"Delete custom {CATEGORY_LABELS[self.category].lower()} preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self.preset_manager.delete_user_preset(self.category, name):
            fallback = self.preset_manager.default_preset_name(self.category)
            self._active_preset_name = fallback
            self.refresh_preset_list(select_name=fallback)
            self.presets_mutated.emit(self.category)
            self.preset_selected.emit(self.category, fallback)


def _card(title: str) -> Tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    return box, form


def _page_container(widgets: List[QWidget]) -> QWidget:
    w = QWidget()
    vbox = QVBoxLayout(w)
    vbox.setContentsMargins(6, 4, 10, 12)
    vbox.setSpacing(10)
    for child in widgets:
        vbox.addWidget(child)
    vbox.addStretch(1)
    return w


# =============================================================================
# Builders for the 5 PrusaSlicer-Style Settings Tabs
# =============================================================================

def build_print_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("print", preset_manager)
    f, i = cfg.fff, cfg.infill

    # 1. Layers and perimeters
    g_layer, fl = _card("Layer Height")
    fl.addRow("Layer height:", tab.bind_dspin("fff.layer_height", f.layer_height, 0.05, 1.0, 0.05, 2, "mm"))
    fl.addRow(
        "First layer height:",
        tab.bind_dspin("fff.first_layer_height", f.first_layer_height, 0.08, 1.0, 0.05, 2, "mm"),
    )

    g_vert, fv = _card("Vertical Shells")
    fv.addRow("Perimeters:", tab.bind_spin("fff.perimeters", f.perimeters, 1, 12))
    fv.addRow(
        tab.bind_checkbox(
            "fff.spiral_vase",
            "Spiral vase (single continuous perimeter, no top solid layers)",
            f.spiral_vase,
        )
    )
    fv.addRow(
        tab.bind_checkbox(
            "fff.extra_perimeters_on_overhangs",
            "Extra perimeters on overhangs",
            f.extra_perimeters_on_overhangs,
        )
    )
    fv.addRow(
        tab.bind_checkbox(
            "fff.detect_thin_walls",
            "Detect thin walls",
            f.detect_thin_walls,
        )
    )
    fv.addRow(
        tab.bind_checkbox(
            "fff.avoid_crossing_perimeters",
            "Avoid crossing perimeters",
            f.avoid_crossing_perimeters,
        )
    )
    fv.addRow(
        tab.bind_checkbox(
            "fff.external_perimeters_first",
            "External perimeters first",
            f.external_perimeters_first,
            tooltip="Print the outermost wall before inner perimeters for sharper dimensional accuracy.",
        )
    )
    fv.addRow(
        "Seam position:",
        tab.bind_combo("fff.seam_position", list(SEAM_POSITIONS), f.seam_position),
    )

    g_horiz, fh = _card("Horizontal Shells && Surface Finish")
    fh.addRow("Top solid layers:", tab.bind_spin("fff.top_solid_layers", f.top_solid_layers, 0, 30))
    fh.addRow("Bottom solid layers:", tab.bind_spin("fff.bottom_solid_layers", f.bottom_solid_layers, 0, 30))
    fh.addRow(
        "Minimum top shell thickness:",
        tab.bind_dspin("fff.min_top_shell_thickness_mm", f.min_top_shell_thickness_mm, 0.0, 10.0, 0.1, 2, "mm"),
    )
    fh.addRow(
        "Minimum bottom shell thickness:",
        tab.bind_dspin("fff.min_bottom_shell_thickness_mm", f.min_bottom_shell_thickness_mm, 0.0, 10.0, 0.1, 2, "mm"),
    )
    fh.addRow(
        "Solid fill angle:",
        tab.bind_dspin("fff.infill_angle_deg", f.infill_angle_deg, 0.0, 180.0, 15.0, 1, "°"),
    )

    chk_iron = tab.bind_checkbox(
        "fff.ironing_enable",
        "Enable Top-Surface Ironing (smooth hot-nozzle finishing pass)",
        f.ironing_enable,
    )
    sp_iron_sp = tab.bind_dspin("fff.ironing_spacing_mm", f.ironing_spacing_mm, 0.05, 0.6, 0.02, 2, "mm")
    sp_iron_fl = tab.bind_dspin("fff.ironing_flow_pct", f.ironing_flow_pct, 0.0, 50.0, 1.0, 1, "%")
    fh.addRow(chk_iron)
    fh.addRow("Ironing line spacing:", sp_iron_sp)
    fh.addRow("Ironing flow rate:", sp_iron_fl)

    g_fuzzy, ffz = _card("Fuzzy Skin (Textured Surface)")
    cb_fuzzy = tab.bind_combo("fff.fuzzy_skin_mode", list(FUZZY_SKIN_MODES), f.fuzzy_skin_mode)
    sp_fz_th = tab.bind_dspin("fff.fuzzy_skin_thickness_mm", f.fuzzy_skin_thickness_mm, 0.05, 2.0, 0.05, 2, "mm")
    sp_fz_pd = tab.bind_dspin("fff.fuzzy_skin_point_dist_mm", f.fuzzy_skin_point_dist_mm, 0.2, 5.0, 0.1, 2, "mm")
    ffz.addRow("Fuzzy skin:", cb_fuzzy)
    ffz.addRow("Fuzzy skin thickness:", sp_fz_th)
    ffz.addRow("Fuzzy skin point distance:", sp_fz_pd)

    g_comp, fcmp = _card("Dimensional Compensation")
    fcmp.addRow(
        "Elephant foot compensation:",
        tab.bind_dspin(
            "fff.elephant_foot_compensation_mm",
            f.elephant_foot_compensation_mm,
            0.0,
            1.0,
            0.05,
            2,
            "mm",
            tooltip="Shrinks Layer 1 XY contour inward to compensate for first-layer squish.",
        ),
    )
    fcmp.addRow(
        "XY size compensation:",
        tab.bind_dspin(
            "fff.xy_size_compensation_mm",
            f.xy_size_compensation_mm,
            -2.0,
            2.0,
            0.05,
            2,
            "mm",
            tooltip="Global XY expansion (+) or shrinkage (-) applied to all object contours.",
        ),
    )
    fcmp.addRow(
        "XY hole compensation:",
        tab.bind_dspin(
            "fff.xy_hole_compensation_mm",
            f.xy_hole_compensation_mm,
            -2.0,
            2.0,
            0.05,
            2,
            "mm",
            tooltip="Dedicated XY expansion/shrinkage applied strictly to internal holes.",
        ),
    )

    def _sync_layers_finish() -> None:
        sp_iron_sp.setEnabled(chk_iron.isChecked())
        sp_iron_fl.setEnabled(chk_iron.isChecked())
        fz_on = cb_fuzzy.currentText() != "none"
        sp_fz_th.setEnabled(fz_on)
        sp_fz_pd.setEnabled(fz_on)

    tab.add_post_change_hook(_sync_layers_finish)
    tab.add_section("Layers and perimeters", _page_container([g_layer, g_vert, g_horiz, g_fuzzy, g_comp]), "layers")

    # 2. Infill (Standard PrusaSlicer Infill vs 3D Surface-Normal Cellular Infill)
    g_eng, feng = _card("Infill Engine")
    cb_engine = tab.bind_combo(
        "infill.infill_engine",
        list(INFILL_ENGINES),
        i.infill_engine,
        tooltip=(
            "surface_normal_3d: AlveoSlicer 3D surface-normal cellular formwork & photopolymer channels | "
            "standard: PrusaSlicer-style density-driven infill (Rectilinear, Grid, Gyroid, Honeycomb, Cubic, Lightning, etc.)"
        ),
    )
    lbl_eng_info = QLabel()
    lbl_eng_info.setWordWrap(True)
    feng.addRow("Infill architecture:", cb_engine)
    feng.addRow(lbl_eng_info)

    g_std, fstd = _card("Standard PrusaSlicer Infill")
    sp_dens = tab.bind_dspin(
        "infill.fill_density_pct", i.fill_density_pct, 0.0, 100.0, 5.0, 1, "%",
        tooltip="Fill density percentage for Standard PrusaSlicer infill (0% = hollow, 100% = solid).",
    )
    cb_std_pat = tab.bind_combo(
        "infill.standard_fill_pattern",
        list(STANDARD_INFILL_PATTERNS),
        i.standard_fill_pattern,
        tooltip="Standard PrusaSlicer sparse infill pattern.",
    )
    chk_only_need = tab.bind_checkbox(
        "infill.only_infill_where_needed",
        "Only infill where needed (support overhanging ceilings only)",
        i.only_infill_where_needed,
    )
    sp_solid_every = tab.bind_spin(
        "infill.solid_infill_every_layers",
        i.solid_infill_every_layers,
        0,
        200,
        1,
        "layers",
        tooltip="Force a full solid diaphragm layer every N layers (0 = disabled).",
    )
    sp_solid_area = tab.bind_dspin(
        "infill.solid_infill_threshold_area_mm2",
        i.solid_infill_threshold_area_mm2,
        0.0,
        500.0,
        5.0,
        1,
        "mm²",
        tooltip="Regions smaller than this cross-section area are printed 100% solid.",
    )
    sp_anchor = tab.bind_dspin(
        "infill.infill_anchor_mm", i.infill_anchor_mm, 0.0, 20.0, 0.5, 2, "mm",
        tooltip="Length of infill line anchor tucked into adjacent perimeters.",
    )
    sp_anchor_max = tab.bind_dspin(
        "infill.infill_anchor_max_mm", i.infill_anchor_max_mm, 0.0, 50.0, 1.0, 1, "mm",
        tooltip="Maximum anchor extension along perimeter wall.",
    )
    fstd.addRow("Fill density:", sp_dens)
    fstd.addRow("Fill pattern:", cb_std_pat)
    fstd.addRow(chk_only_need)
    fstd.addRow("Solid infill every:", sp_solid_every)
    fstd.addRow("Solid infill threshold area:", sp_solid_area)
    fstd.addRow("Length of the infill anchor:", sp_anchor)
    fstd.addRow("Maximum length of the infill anchor:", sp_anchor_max)

    g_pat, fp = _card("3D Surface-Normal Cellular && TPMS Pattern")
    cb_pat = tab.bind_combo(
        "infill.pattern",
        list(CELL_PATTERNS),
        i.pattern,
        tooltip=(
            "hexagonal: equal-side regular 60°/0° honeycombs | gyroid: continuous 3D TPMS sine wave | "
            "cubic: tilted 3D isotropic cubes | diamond: 45° self-supporting | triangular: iso-grid | "
            "voronoi: organic | square: orthogonal | brick: running-bond"
        ),
    )
    cb_mode = tab.bind_combo("infill.mode", ["hollow_shell", "full_to_medial"], i.mode)
    sp_hw = tab.bind_dspin("infill.hollow_wall_thickness", i.hollow_wall_thickness, 1.5, 30.0, 0.5, 2, "mm")
    sp_is = tab.bind_dspin("infill.inner_skin_thickness", i.inner_skin_thickness, 0.4, 5.0, 0.1, 2, "mm")
    sp_br = tab.bind_dspin("infill.max_bridge_span", i.max_bridge_span, 2.0, 30.0, 0.5, 2, "mm")
    chk_nd, sp_nd = tab.bind_optional_dspin(
        "infill.max_normal_depth", "Cap normal depth (isotropic core)", 10.0, 2.0, 80.0, 1.0, 1, "mm"
    )
    fp.addRow("Cellular / TPMS pattern:", cb_pat)
    fp.addRow("Core topology mode:", cb_mode)
    fp.addRow("Hollow composite wall:", sp_hw)
    fp.addRow("Hollow inner skin:", sp_is)
    fp.addRow("Max bridgeable span:", sp_br)
    fp.addRow(chk_nd)
    fp.addRow("Core transition depth:", sp_nd)

    g_geo, fg = _card("Cell Formwork && Photopolymer Channel")
    fg.addRow("Target cell pitch:", tab.bind_dspin("infill.target_cell_pitch", i.target_cell_pitch, 2.5, 25.0, 0.5, 2, "mm"))
    fg.addRow("Cell wall half-thickness (r):", tab.bind_dspin("infill.strut_radius", i.strut_radius, 0.20, 3.0, 0.05, 2, "mm"))
    fg.addRow("Photopolymer channel width:", tab.bind_dspin("infill.min_channel_clearance", i.min_channel_clearance, 0.40, 10.0, 0.1, 2, "mm"))
    fg.addRow("Flatten shallow faces (<):", tab.bind_dspin("infill.flatten_angle_deg", i.flatten_angle_deg, 0.0, 80.0, 1.0, 1, "°"))
    fg.addRow("Cell corner fillet radius:", tab.bind_dspin("infill.smin_blend_radius", i.smin_blend_radius, 0.0, 2.0, 0.05, 2, "mm"))
    fg.addRow("Divergence split ratio:", tab.bind_dspin("infill.max_divergence_ratio", i.max_divergence_ratio, 1.2, 3.0, 0.1, 2))
    fg.addRow("Medial weld ratio:", tab.bind_dspin("infill.medial_buffer_ratio", i.medial_buffer_ratio, 0.50, 0.99, 0.02, 2))
    fg.addRow("Shock cos threshold:", tab.bind_dspin("infill.shock_cos_threshold", i.shock_cos_threshold, -0.5, 0.95, 0.05, 2))

    g_inf_adv, fia = _card("Top / Bottom Patterns, Anchoring && Bridging")
    fia.addRow(
        "Top fill pattern:",
        tab.bind_combo("infill.top_fill_pattern", list(TOP_BOTTOM_FILL_PATTERNS), i.top_fill_pattern),
    )
    fia.addRow(
        "Bottom fill pattern:",
        tab.bind_combo("infill.bottom_fill_pattern", list(TOP_BOTTOM_FILL_PATTERNS), i.bottom_fill_pattern),
    )
    fia.addRow("Infill/perimeters overlap:", tab.bind_dspin("fff.infill_overlap_pct", f.infill_overlap_pct, 0.0, 60.0, 5.0, 1, "%"))
    fia.addRow("Bridge flow ratio:", tab.bind_dspin("fff.bridge_flow_ratio", f.bridge_flow_ratio, 0.5, 1.5, 0.05, 2))
    fia.addRow(
        "Bridging angle (0 = auto):",
        tab.bind_dspin("infill.bridge_angle_deg", i.bridge_angle_deg, 0.0, 180.0, 15.0, 1, "°"),
    )
    fia.addRow(
        "Combine infill every:",
        tab.bind_spin("fff.combine_infill_every_n_layers", f.combine_infill_every_n_layers, 1, 5, 1, "layers"),
    )
    fia.addRow(
        tab.bind_checkbox(
            "infill.infill_before_perimeters",
            "Infill before perimeters (print infill before outer shells)",
            i.infill_before_perimeters,
        )
    )

    def _sync_mode() -> None:
        is_std = cb_engine.currentText() == "standard"
        g_std.setEnabled(is_std)
        g_pat.setEnabled(not is_std)
        g_geo.setEnabled(not is_std)
        if is_std:
            lbl_eng_info.setObjectName("modeBannerFFF")
            lbl_eng_info.setText(
                "Standard PrusaSlicer Infill Active — Uses density-driven sparse infill patterns "
                "(Gyroid, Grid, Honeycomb, Cubic, Lightning, etc.) without 3D Voronoi formwork."
            )
        else:
            lbl_eng_info.setObjectName("modeBannerHybrid")
            lbl_eng_info.setText(
                "3D Surface-Normal Cellular Infill Active — Synthesizes surface-conformal cellular "
                "formwork walls and connected photopolymer resin channels."
            )
        lbl_eng_info.style().unpolish(lbl_eng_info)
        lbl_eng_info.style().polish(lbl_eng_info)

        is_hollow = (not is_std) and (cb_mode.currentText() == "hollow_shell")
        sp_hw.setEnabled(is_hollow)
        sp_is.setEnabled(is_hollow)
        chk_nd.setEnabled((not is_std) and (not is_hollow))
        sp_nd.setEnabled((not is_std) and (not is_hollow) and chk_nd.isChecked())

    tab.add_post_change_hook(_sync_mode)
    tab.add_section("Infill", _page_container([g_eng, g_std, g_pat, g_geo, g_inf_adv]), "infill")

    # 3. Skirt and brim
    g_sk, fsk = _card("Skirt && Draft Shield")
    fsk.addRow("Loops (minimum):", tab.bind_spin("fff.skirt_loops", f.skirt_loops, 0, 10))
    fsk.addRow("Distance from object:", tab.bind_dspin("fff.skirt_distance_mm", f.skirt_distance_mm, 0.0, 30.0, 1.0, 1, "mm"))
    fsk.addRow("Skirt height:", tab.bind_spin("fff.skirt_height_layers", f.skirt_height_layers, 1, 50, 1, "layers"))
    fsk.addRow(
        "Minimum extrusion length:",
        tab.bind_dspin("fff.skirt_min_length_mm", f.skirt_min_length_mm, 0.0, 500.0, 5.0, 1, "mm"),
    )
    fsk.addRow(
        tab.bind_checkbox(
            "fff.draft_shield_enable",
            "Draft shield (full-height skirt around part to prevent warping)",
            f.draft_shield_enable,
        )
    )

    g_br, fbr = _card("Brim")
    cb_brim = tab.bind_combo("fff.brim_type", list(BRIM_TYPES), f.brim_type)
    sp_brim_w = tab.bind_dspin("fff.brim_width_mm", f.brim_width_mm, 0.0, 30.0, 1.0, 1, "mm")
    sp_brim_gap = tab.bind_dspin("fff.brim_separation_gap_mm", f.brim_separation_gap_mm, 0.0, 2.0, 0.05, 2, "mm")
    fbr.addRow("Brim type:", cb_brim)
    fbr.addRow("Brim width:", sp_brim_w)
    fbr.addRow("Brim separation gap:", sp_brim_gap)

    def _sync_brim() -> None:
        en = cb_brim.currentText() != "no_brim"
        sp_brim_w.setEnabled(en)
        sp_brim_gap.setEnabled(en)

    tab.add_post_change_hook(_sync_brim)
    tab.add_section("Skirt and brim", _page_container([g_sk, g_br]), "skirt")

    # 4. Support material
    g_sup, fsu = _card("External Overhang && Paint-on Supports")
    chk_sup = tab.bind_checkbox("fff.support_enable", "Generate support material", f.support_enable)
    cb_sup_mode = tab.bind_combo(
        "fff.support_mode",
        list(SUPPORT_MODES),
        f.support_mode,
        tooltip="auto: overhangs + paint enforcers - blockers | enforcers_only: only painted green regions",
    )
    cb_sup_style = tab.bind_combo(
        "fff.support_style",
        list(SUPPORT_STYLES),
        f.support_style,
        tooltip="organic: branching tree supports | snug: tight overhang columns | grid: full rectangular columns",
    )
    cb_sup_pat = tab.bind_combo("fff.support_pattern", list(SUPPORT_PATTERNS), f.support_pattern)
    sp_sup_ang = tab.bind_dspin("fff.support_overhang_angle_deg", f.support_overhang_angle_deg, 10.0, 80.0, 5.0, 1, "°")
    chk_sup_bp = tab.bind_checkbox("fff.support_buildplate_only", "Support on build plate only", f.support_buildplate_only)
    chk_sup_br = tab.bind_checkbox("fff.support_dont_support_bridges", "Don't support bridges", f.support_dont_support_bridges)
    sp_sup_xy = tab.bind_dspin("fff.support_xy_distance_mm", f.support_xy_distance_mm, 0.10, 5.0, 0.10, 2, "mm")
    sp_sup_z = tab.bind_dspin("fff.support_z_distance_mm", f.support_z_distance_mm, 0.0, 1.0, 0.05, 2, "mm")
    sp_sup_bz = tab.bind_dspin("fff.support_bottom_z_distance_mm", f.support_bottom_z_distance_mm, 0.0, 1.0, 0.05, 2, "mm")
    sp_sup_sp = tab.bind_dspin("fff.support_spacing_mm", f.support_spacing_mm, 0.8, 12.0, 0.25, 2, "mm")
    sp_sup_if_l = tab.bind_spin("fff.support_interface_layers", f.support_interface_layers, 0, 8, 1, "layers")
    sp_sup_bif_l = tab.bind_spin("fff.support_bottom_interface_layers", f.support_bottom_interface_layers, 0, 8, 1, "layers")
    sp_sup_if_s = tab.bind_dspin("fff.support_interface_spacing_mm", f.support_interface_spacing_mm, 0.2, 4.0, 0.1, 2, "mm")
    sp_sup_raft = tab.bind_spin("fff.support_raft_layers", f.support_raft_layers, 0, 10, 1, "layers")
    sp_sup_exp = tab.bind_dspin("fff.support_first_layer_expansion_mm", f.support_first_layer_expansion_mm, 0.0, 15.0, 0.5, 1, "mm")

    fsu.addRow(chk_sup)
    fsu.addRow("Support generation mode:", cb_sup_mode)
    fsu.addRow("Style:", cb_sup_style)
    fsu.addRow("Pattern:", cb_sup_pat)
    fsu.addRow("Overhang threshold:", sp_sup_ang)
    fsu.addRow(chk_sup_bp)
    fsu.addRow(chk_sup_br)
    fsu.addRow("XY separation:", sp_sup_xy)
    fsu.addRow("Top contact Z distance:", sp_sup_z)
    fsu.addRow("Bottom contact Z distance:", sp_sup_bz)
    fsu.addRow("Pattern spacing:", sp_sup_sp)
    fsu.addRow("Top interface layers:", sp_sup_if_l)
    fsu.addRow("Bottom interface layers:", sp_sup_bif_l)
    fsu.addRow("Interface pattern spacing:", sp_sup_if_s)
    fsu.addRow("Raft layers:", sp_sup_raft)
    fsu.addRow("First layer expansion:", sp_sup_exp)

    g_org, forg = _card("Organic Tree Supports")
    sp_org_dia = tab.bind_dspin("fff.support_branch_diameter_mm", f.support_branch_diameter_mm, 0.8, 12.0, 0.2, 1, "mm")
    sp_org_ang = tab.bind_dspin("fff.support_branch_angle_deg", f.support_branch_angle_deg, 15.0, 65.0, 5.0, 1, "°")
    forg.addRow("Tip / branch diameter:", sp_org_dia)
    forg.addRow("Max branch lean angle:", sp_org_ang)

    def _sync_support() -> None:
        en = chk_sup.isChecked()
        for w in (
            cb_sup_mode,
            cb_sup_style,
            cb_sup_pat,
            sp_sup_ang,
            chk_sup_bp,
            chk_sup_br,
            sp_sup_xy,
            sp_sup_z,
            sp_sup_bz,
            sp_sup_sp,
            sp_sup_if_l,
            sp_sup_bif_l,
            sp_sup_if_s,
            sp_sup_raft,
            sp_sup_exp,
        ):
            w.setEnabled(en)
        is_org = en and cb_sup_style.currentText() == "organic"
        sp_org_dia.setEnabled(is_org)
        sp_org_ang.setEnabled(is_org)

    tab.add_post_change_hook(_sync_support)

    g_dry, fdry = _card("Hollow Dry-Core Internal Support")
    fdry.addRow(
        "Internal roof support spacing:",
        tab.bind_dspin("fff.dry_support_spacing_mm", f.dry_support_spacing_mm, 1.5, 20.0, 0.5, 1, "mm"),
    )
    note_sup = QLabel(
        "Sparse dry FFF support is automatically projected strictly underneath overhanging inner ceilings "
        "inside the dry hollow core (zero resin contact)."
    )
    note_sup.setObjectName("mutedLabel")
    note_sup.setWordWrap(True)
    fdry.addRow(note_sup)
    tab.add_section("Support material", _page_container([g_sup, g_org, g_dry]), "support")

    # 5. Speed & Acceleration
    g_spd, fsp = _card("Speed for Print Moves")
    fsp.addRow("Perimeters:", tab.bind_dspin("fff.perimeter_speed_mm_s", f.perimeter_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Small perimeters:", tab.bind_dspin("fff.small_perimeter_speed_mm_s", f.small_perimeter_speed_mm_s, 5.0, 300.0, 5.0, 1, "mm/s"))
    fsp.addRow("External perimeters:", tab.bind_dspin("fff.external_perimeter_speed_mm_s", f.external_perimeter_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Cellular infill / default:", tab.bind_dspin("fff.print_speed_mm_s", f.print_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Solid infill:", tab.bind_dspin("fff.solid_infill_speed_mm_s", f.solid_infill_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Top solid infill:", tab.bind_dspin("fff.top_solid_infill_speed_mm_s", f.top_solid_infill_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Bridges:", tab.bind_dspin("fff.bridge_speed_mm_s", f.bridge_speed_mm_s, 5.0, 200.0, 5.0, 1, "mm/s"))
    fsp.addRow("Gap fill:", tab.bind_dspin("fff.gap_fill_speed_mm_s", f.gap_fill_speed_mm_s, 5.0, 200.0, 5.0, 1, "mm/s"))
    fsp.addRow("Support material:", tab.bind_dspin("fff.support_speed_mm_s", f.support_speed_mm_s, 5.0, 350.0, 5.0, 1, "mm/s"))
    fsp.addRow("Support material interface:", tab.bind_dspin("fff.support_interface_speed_mm_s", f.support_interface_speed_mm_s, 5.0, 250.0, 5.0, 1, "mm/s"))

    g_trv, ftr = _card("First Layer && Non-Print Moves")
    ftr.addRow("First layer speed:", tab.bind_dspin("fff.first_layer_speed_mm_s", f.first_layer_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s"))
    ftr.addRow("Travel speed:", tab.bind_dspin("fff.travel_speed_mm_s", f.travel_speed_mm_s, 20.0, 600.0, 10.0, 1, "mm/s"))
    ftr.addRow("Z travel speed:", tab.bind_dspin("fff.z_travel_speed_mm_s", f.z_travel_speed_mm_s, 2.0, 80.0, 2.0, 1, "mm/s"))

    g_acc, facc = _card("Acceleration Control && Volumetric Autospeed")
    facc.addRow("Default acceleration:", tab.bind_dspin("fff.default_accel_mm_s2", f.default_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"))
    facc.addRow("Perimeters acceleration:", tab.bind_dspin("fff.perimeter_accel_mm_s2", f.perimeter_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"))
    facc.addRow("External perimeters accel:", tab.bind_dspin("fff.external_perimeter_accel_mm_s2", f.external_perimeter_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"))
    facc.addRow("Infill acceleration:", tab.bind_dspin("fff.infill_accel_mm_s2", f.infill_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"))
    facc.addRow("Bridge acceleration:", tab.bind_dspin("fff.bridge_accel_mm_s2", f.bridge_accel_mm_s2, 0.0, 25000.0, 250.0, 0, "mm/s²"))
    facc.addRow("First layer acceleration:", tab.bind_dspin("fff.first_layer_accel_mm_s2", f.first_layer_accel_mm_s2, 0.0, 15000.0, 250.0, 0, "mm/s²"))
    facc.addRow("Max volumetric speed:", tab.bind_dspin("fff.max_volumetric_speed_mm3_s", f.max_volumetric_speed_mm3_s, 0.0, 100.0, 1.0, 1, "mm³/s"))
    tab.add_section("Speed", _page_container([g_spd, g_trv, g_acc]), "speed")

    # 6. Wipe tower
    g_wt, fwt = _card("Purge / Wipe Tower")
    chk_wt = tab.bind_checkbox("fff.wipe_tower_enable", "Enable wipe tower", f.wipe_tower_enable)
    sp_wt_x = tab.bind_dspin("fff.wipe_tower_x", f.wipe_tower_x, 0.0, 550.0, 5.0, 1, "mm")
    sp_wt_y = tab.bind_dspin("fff.wipe_tower_y", f.wipe_tower_y, 0.0, 550.0, 5.0, 1, "mm")
    sp_wt_w = tab.bind_dspin("fff.wipe_tower_width_mm", f.wipe_tower_width_mm, 10.0, 120.0, 5.0, 1, "mm")
    sp_wt_d = tab.bind_dspin("fff.wipe_tower_depth_mm", f.wipe_tower_depth_mm, 6.0, 80.0, 2.0, 1, "mm")
    sp_wt_b = tab.bind_dspin("fff.wipe_tower_brim_mm", f.wipe_tower_brim_mm, 0.0, 15.0, 0.5, 1, "mm")
    sp_wt_v = tab.bind_dspin("fff.wipe_tower_prime_volume_mm3", f.wipe_tower_prime_volume_mm3, 2.0, 120.0, 2.0, 1, "mm³")
    fwt.addRow(chk_wt)
    fwt.addRow("Position X:", sp_wt_x)
    fwt.addRow("Position Y:", sp_wt_y)
    fwt.addRow("Width:", sp_wt_w)
    fwt.addRow("Depth:", sp_wt_d)
    fwt.addRow("Brim width:", sp_wt_b)
    fwt.addRow("Prime volume per layer:", sp_wt_v)

    def _sync_wt() -> None:
        en = chk_wt.isChecked()
        for w in (sp_wt_x, sp_wt_y, sp_wt_w, sp_wt_d, sp_wt_b, sp_wt_v):
            w.setEnabled(en)

    tab.add_post_change_hook(_sync_wt)
    tab.add_section("Wipe tower", _page_container([g_wt]), "tower")

    # 7. Advanced (Extrusion widths + Engine Precision)
    g_ew, few = _card("Extrusion Width")
    few.addRow("Default extrusion width:", tab.bind_dspin("fff.line_width", f.line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("First layer:", tab.bind_dspin("fff.first_layer_line_width", f.first_layer_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("Perimeters:", tab.bind_dspin("fff.perimeter_line_width", f.perimeter_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("External perimeters:", tab.bind_dspin("fff.external_perimeter_line_width", f.external_perimeter_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("Cellular infill:", tab.bind_dspin("fff.infill_line_width", f.infill_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("Solid infill:", tab.bind_dspin("fff.solid_infill_line_width", f.solid_infill_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("Top solid infill:", tab.bind_dspin("fff.top_infill_line_width", f.top_infill_line_width, 0.20, 2.0, 0.02, 2, "mm"))
    few.addRow("Support material:", tab.bind_dspin("fff.support_line_width", f.support_line_width, 0.20, 2.0, 0.02, 2, "mm"))

    g_eng, fe = _card("Slicing Engine && CAD Tessellation")
    fe.addRow("3D SDF voxel pitch:", tab.bind_dspin("voxel_pitch_mm", cfg.voxel_pitch_mm, 0.15, 2.0, 0.05, 2, "mm"))
    fe.addRow("2D DP/BFS raster pitch:", tab.bind_dspin("slice_raster_pitch_mm", cfg.slice_raster_pitch_mm, 0.10, 1.0, 0.05, 2, "mm"))
    fe.addRow("STEP linear tolerance:", tab.bind_dspin("step_tol_linear", cfg.step_tol_linear, 0.005, 0.5, 0.005, 3, "mm"))
    fe.addRow("STEP angular tolerance:", tab.bind_dspin("step_tol_angular", cfg.step_tol_angular, 0.01, 0.5, 0.01, 3, "rad"))
    note_eng = QLabel(
        "Outer and inner walls are always sliced in exact floating-point vector geometry. "
        "The raster pitch is used exclusively for DP airlock/shadow masks and 3D fluid basin BFS."
    )
    note_eng.setObjectName("mutedLabel")
    note_eng.setWordWrap(True)
    fe.addRow(note_eng)
    tab.add_section("Advanced", _page_container([g_ew, g_eng]), "settings")

    tab.load_from_config(cfg)
    return tab


def build_filament_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("filament", preset_manager)
    f = cfg.fff

    g_fil, ff = _card("Filament Properties")
    ff.addRow("Material type:", tab.bind_combo("fff.filament_type", list(FILAMENT_TYPES), f.filament_type))
    ff.addRow("Diameter:", tab.bind_dspin("fff.filament_diameter", f.filament_diameter, 1.0, 3.0, 0.05, 2, "mm"))
    ff.addRow("Density:", tab.bind_dspin("fff.filament_density_g_cm3", f.filament_density_g_cm3, 0.5, 5.0, 0.02, 2, "g/cm³"))
    ff.addRow("Cost:", tab.bind_dspin("fff.filament_cost_per_kg", f.filament_cost_per_kg, 0.0, 500.0, 1.0, 2, "/kg"))
    ff.addRow("Extrusion multiplier:", tab.bind_dspin("fff.flow_multiplier", f.flow_multiplier, 0.5, 1.5, 0.02, 2))

    g_tmp, ft = _card("Temperature (°C)")
    ft.addRow("Nozzle (first layer):", tab.bind_spin("fff.first_layer_nozzle_temp_c", f.first_layer_nozzle_temp_c, 0, 350, 5, "°C"))
    ft.addRow("Nozzle (other layers):", tab.bind_spin("fff.nozzle_temp_c", f.nozzle_temp_c, 0, 350, 5, "°C"))
    ft.addRow("Bed (first layer):", tab.bind_spin("fff.first_layer_bed_temp_c", f.first_layer_bed_temp_c, 0, 140, 5, "°C"))
    ft.addRow("Bed (other layers):", tab.bind_spin("fff.bed_temp_c", f.bed_temp_c, 0, 140, 5, "°C"))
    ft.addRow("Chamber (0 = off):", tab.bind_spin("fff.chamber_temp_c", f.chamber_temp_c, 0, 100, 5, "°C"))
    tab.add_section("Filament", _page_container([g_fil, g_tmp]), "filament")

    g_cool, fc = _card("Fan Settings && Cooling Thresholds")
    fc.addRow(tab.bind_checkbox("fff.fan_auto_cooling", "Enable auto cooling", f.fan_auto_cooling))
    fc.addRow("Max fan speed:", tab.bind_spin("fff.part_cooling_fan_pct", f.part_cooling_fan_pct, 0, 100, 5, "%"))
    fc.addRow("Min fan speed:", tab.bind_spin("fff.min_fan_speed_pct", f.min_fan_speed_pct, 0, 100, 5, "%"))
    fc.addRow("Bridges fan speed:", tab.bind_spin("fff.fan_bridge_pct", f.fan_bridge_pct, 0, 100, 5, "%"))
    fc.addRow("Disable fan for the first:", tab.bind_spin("fff.fan_disable_first_layers", f.fan_disable_first_layers, 0, 10, 1, "layers"))
    fc.addRow("Full fan speed at layer:", tab.bind_spin("fff.fan_full_speed_at_layer", f.fan_full_speed_at_layer, 0, 20, 1, "layer"))
    fc.addRow(
        "Slow down if layer print time is below:",
        tab.bind_dspin("fff.slowdown_below_layer_time_s", f.slowdown_below_layer_time_s, 0.0, 120.0, 1.0, 1, "s"),
    )
    fc.addRow("Min print speed:", tab.bind_dspin("fff.min_print_speed_mm_s", f.min_print_speed_mm_s, 1.0, 100.0, 1.0, 1, "mm/s"))
    tab.add_section("Cooling", _page_container([g_cool]), "cooling")

    g_ret, fr = _card("Retraction Overrides")
    fr.addRow("Retraction length:", tab.bind_dspin("fff.retract_length_mm", f.retract_length_mm, 0.0, 12.0, 0.2, 2, "mm"))
    fr.addRow("Retraction speed:", tab.bind_dspin("fff.retract_speed_mm_s", f.retract_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s"))
    fr.addRow("Deretraction speed:", tab.bind_dspin("fff.deretract_speed_mm_s", f.deretract_speed_mm_s, 0.0, 120.0, 5.0, 1, "mm/s"))
    fr.addRow("Minimum travel after retraction:", tab.bind_dspin("fff.retract_min_travel_mm", f.retract_min_travel_mm, 0.0, 20.0, 0.2, 2, "mm"))
    fr.addRow("Lift Z (Z-hop):", tab.bind_dspin("fff.z_hop_mm", f.z_hop_mm, 0.0, 5.0, 0.1, 2, "mm"))
    fr.addRow(tab.bind_checkbox("fff.wipe_on_retract", "Wipe while retracting", f.wipe_on_retract))
    fr.addRow(tab.bind_checkbox("fff.retract_on_layer_change", "Retract on layer change", f.retract_on_layer_change))
    tab.add_section("Filament Overrides", _page_container([g_ret]), "settings")

    tab.load_from_config(cfg)
    return tab


def build_resin_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("resin", preset_manager)
    r = cfg.resin

    g_phys, fp = _card("Beer-Lambert Curing && Meniscus")
    fp.addRow("Max cure depth (C_d):", tab.bind_dspin("resin.max_cure_depth_mm", r.max_cure_depth_mm, 0.2, 10.0, 0.1, 2, "mm"))
    fp.addRow("Cure depth safety factor (η):", tab.bind_dspin("resin.cure_depth_safety_eta", r.cure_depth_safety_eta, 0.5, 1.0, 0.05, 2))
    fp.addRow("Meniscus headspace:", tab.bind_dspin("resin.headspace_margin_mm", r.headspace_margin_mm, 0.0, 1.0, 0.05, 2, "mm"))
    fp.addRow("Shrinkage compensation:", tab.bind_dspin("resin.shrinkage_factor", r.shrinkage_factor, 0.90, 1.25, 0.01, 3))
    tab.add_section("Curing & Exposure", _page_container([g_phys]), "uv")

    g_fluid, ff = _card("Viscosity && Channel Flow")
    ff.addRow("Max basin flow radius:", tab.bind_dspin("resin.max_flow_radius_mm", r.max_flow_radius_mm, 2.0, 50.0, 1.0, 1, "mm"))
    ff.addRow("Viscosity leveling dwell:", tab.bind_spin("resin.settle_dwell_ms", r.settle_dwell_ms, 0, 30000, 250, "ms"))
    ff.addRow(
        "Ghost island filter area:",
        tab.bind_dspin(
            "resin.ghost_island_min_area_mm2",
            r.ghost_island_min_area_mm2,
            0.0,
            10.0,
            0.05,
            2,
            "mm²",
            tooltip="Tiny rasterized 2D islands below this area are skipped by the pump and their volume is merged into the touching or nearest real basin.",
        ),
    )
    tab.add_section("Fluid & Flow", _page_container([g_fluid]), "resin")

    g_dp, fd = _card("Global DP Cure-Cycle Scheduler")
    sp_xmin = tab.bind_spin("resin.x_min_layers", r.x_min_layers, 1, 20, 1, "layers")
    sp_xmax = tab.bind_spin("resin.x_max_layers", r.x_max_layers, 1, 30, 1, "layers")
    fd.addRow("Min interval (x_min):", sp_xmin)
    fd.addRow("Max interval (x_max):", sp_xmax)
    fd.addRow("UV shadow bleed radius:", tab.bind_dspin("resin.uv_shadow_bleed_mm", r.uv_shadow_bleed_mm, 0.0, 2.0, 0.05, 2, "mm"))
    fd.addRow("Max shadowed void ratio:", tab.bind_dspin("resin.max_shadow_vol_ratio", r.max_shadow_vol_ratio, 0.0, 0.50, 0.01, 3))
    fd.addRow("DP cycle base cost:", tab.bind_dspin("resin.dp_w_cycle", r.dp_w_cycle, 0.0, 500.0, 1.0, 1))
    fd.addRow("DP short-span penalty:", tab.bind_dspin("resin.dp_w_short", r.dp_w_short, 0.0, 500.0, 5.0, 1))
    fd.addRow("DP shadow volume penalty:", tab.bind_dspin("resin.dp_w_shadow", r.dp_w_shadow, 0.0, 1000.0, 10.0, 1))

    def _clamp_window() -> None:
        if sp_xmax.value() < sp_xmin.value():
            sp_xmax.setValue(sp_xmin.value())

    tab.add_post_change_hook(_clamp_window)
    tab.add_section("Cure Schedule (DP)", _page_container([g_dp]), "layers")

    tab.load_from_config(cfg)
    return tab


def build_printer_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("printer", preset_manager)
    p = cfg.printer
    f = cfg.fff

    # 1. General
    g_gen, fg = _card("Size, Coordinates && Firmware")
    cb_pr = tab.bind_combo("printer.printer_type", ["bedslinger_prototype", "corexy_toolchanger"], p.printer_type)
    fg.addRow("Printer HAL architecture:", cb_pr)
    fg.addRow("G-code flavor:", tab.bind_combo("printer.gcode_flavor", list(GCODE_FLAVORS), p.gcode_flavor))
    fg.addRow("Bed shape:", tab.bind_combo("printer.bed_shape", list(BED_SHAPES), p.bed_shape))
    fg.addRow("Bed size X:", tab.bind_dspin("printer.bed_size_x", p.bed_size_x, 50.0, 600.0, 10.0, 1, "mm"))
    fg.addRow("Bed size Y:", tab.bind_dspin("printer.bed_size_y", p.bed_size_y, 50.0, 600.0, 10.0, 1, "mm"))
    fg.addRow("Max print height Z:", tab.bind_dspin("printer.bed_size_z", p.bed_size_z, 50.0, 600.0, 10.0, 1, "mm"))
    fg.addRow("Z offset:", tab.bind_dspin("printer.z_offset_mm", p.z_offset_mm, -5.0, 5.0, 0.02, 3, "mm"))
    fg.addRow(tab.bind_checkbox("printer.use_relative_e_distances", "Use relative E distances (M83)", p.use_relative_e_distances))
    fg.addRow(tab.bind_checkbox("printer.use_firmware_retraction", "Use firmware retraction (G10 / G11)", p.use_firmware_retraction))
    fg.addRow("Min layer height limit:", tab.bind_dspin("printer.min_layer_height_mm", p.min_layer_height_mm, 0.02, 0.5, 0.01, 2, "mm"))
    fg.addRow("Max layer height limit:", tab.bind_dspin("printer.max_layer_height_mm", p.max_layer_height_mm, 0.10, 1.5, 0.05, 2, "mm"))
    tab.add_section("General", _page_container([g_gen]), "printer")

    # 2. Extruder & Tools
    g_ext, fe = _card("Extruder && Tool Commands")
    fe.addRow("Nozzle diameter:", tab.bind_dspin("fff.nozzle_diameter", f.nozzle_diameter, 0.15, 1.5, 0.05, 2, "mm"))
    fe.addRow("FFF tool command:", tab.bind_line_edit("printer.tool_fff", p.tool_fff, fallback="T0"))
    fe.addRow("Resin pump tool command:", tab.bind_line_edit("printer.tool_resin", p.tool_resin, fallback="T1"))
    ed_uv_tool = tab.bind_line_edit("printer.tool_uv", p.tool_uv, fallback="T2")
    fe.addRow("UV toolhead command:", ed_uv_tool)
    fe.addRow("Resin pump axis:", tab.bind_line_edit("printer.resin_extruder_axis", p.resin_extruder_axis, fallback="E"))
    tab.add_section("Extruder & Tools", _page_container([g_ext]), "filament")

    # 3. Machine Limits
    g_lim_f, flf = _card("Maximum Feedrates")
    flf.addRow("Max feedrate X:", tab.bind_dspin("printer.max_feedrate_x_mm_s", p.max_feedrate_x_mm_s, 10.0, 1500.0, 10.0, 0, "mm/s"))
    flf.addRow("Max feedrate Y:", tab.bind_dspin("printer.max_feedrate_y_mm_s", p.max_feedrate_y_mm_s, 10.0, 1500.0, 10.0, 0, "mm/s"))
    flf.addRow("Max feedrate Z:", tab.bind_dspin("printer.max_feedrate_z_mm_s", p.max_feedrate_z_mm_s, 2.0, 200.0, 2.0, 0, "mm/s"))
    flf.addRow("Max feedrate E:", tab.bind_dspin("printer.max_feedrate_e_mm_s", p.max_feedrate_e_mm_s, 5.0, 300.0, 5.0, 0, "mm/s"))

    g_lim_a, fla = _card("Maximum Accelerations")
    fla.addRow("Max acceleration X:", tab.bind_dspin("printer.max_accel_x_mm_s2", p.max_accel_x_mm_s2, 100.0, 50000.0, 500.0, 0, "mm/s²"))
    fla.addRow("Max acceleration Y:", tab.bind_dspin("printer.max_accel_y_mm_s2", p.max_accel_y_mm_s2, 100.0, 50000.0, 500.0, 0, "mm/s²"))
    fla.addRow("Max acceleration Z:", tab.bind_dspin("printer.max_accel_z_mm_s2", p.max_accel_z_mm_s2, 50.0, 5000.0, 50.0, 0, "mm/s²"))
    fla.addRow("Max acceleration E:", tab.bind_dspin("printer.max_accel_e_mm_s2", p.max_accel_e_mm_s2, 100.0, 25000.0, 250.0, 0, "mm/s²"))

    g_lim_j, flj = _card("Maximum Jerk / Junction Limits")
    flj.addRow("Max jerk X:", tab.bind_dspin("printer.max_jerk_x_mm_s", p.max_jerk_x_mm_s, 0.1, 50.0, 1.0, 1, "mm/s"))
    flj.addRow("Max jerk Y:", tab.bind_dspin("printer.max_jerk_y_mm_s", p.max_jerk_y_mm_s, 0.1, 50.0, 1.0, 1, "mm/s"))
    flj.addRow("Max jerk Z:", tab.bind_dspin("printer.max_jerk_z_mm_s", p.max_jerk_z_mm_s, 0.05, 10.0, 0.1, 2, "mm/s"))
    flj.addRow("Max jerk E:", tab.bind_dspin("printer.max_jerk_e_mm_s", p.max_jerk_e_mm_s, 0.1, 30.0, 0.5, 1, "mm/s"))
    tab.add_section("Machine Limits", _page_container([g_lim_f, g_lim_a, g_lim_j]), "speed")

    # 4. Custom G-code
    g_gc, fgc = _card("Custom G-Code Hooks")
    fgc.addRow(
        "Start G-code:",
        tab.bind_plain_text("printer.start_gcode", p.start_gcode, "; Optional custom lines appended after heating/homing", 80),
    )
    fgc.addRow(
        "End G-code:",
        tab.bind_plain_text("printer.end_gcode", p.end_gcode, "; Optional custom lines before cooldown/shutdown", 80),
    )
    fgc.addRow(
        "Before layer change:",
        tab.bind_plain_text("printer.before_layer_gcode", p.before_layer_gcode, "; Runs before each layer (supports {layer_num}, {layer_z})", 68),
    )
    fgc.addRow(
        "After layer change:",
        tab.bind_plain_text("printer.after_layer_gcode", p.after_layer_gcode, "; Runs after each layer (supports {layer_num}, {layer_z})", 68),
    )
    tab.add_section("Custom G-code", _page_container([g_gc]), "gcode")

    # 5. Needle & UV Offsets
    g_off, fo = _card("Needle && Overhead UV Strip Offsets")
    sp_nx = tab.bind_dspin("printer.needle_offset_x", p.needle_offset_x, -150.0, 150.0, 1.0, 2, "mm")
    sp_ny = tab.bind_dspin("printer.needle_offset_y", p.needle_offset_y, -150.0, 150.0, 1.0, 2, "mm")
    sp_nz = tab.bind_dspin("printer.needle_offset_z", p.needle_offset_z, -10.0, 20.0, 0.2, 2, "mm")
    sp_xp = tab.bind_dspin("printer.x_uv_shield_park", p.x_uv_shield_park, 0.0, 600.0, 5.0, 1, "mm")
    sp_uy = tab.bind_dspin("printer.uv_strip_y_offset", p.uv_strip_y_offset, 0.0, 600.0, 5.0, 1, "mm")
    fo.addRow("Needle offset X:", sp_nx)
    fo.addRow("Needle offset Y:", sp_ny)
    fo.addRow("Needle dispense Z clearance:", sp_nz)
    fo.addRow("X UV-shield park position:", sp_xp)
    fo.addRow("Overhead UV strip Y position:", sp_uy)

    def _sync_printer_type() -> None:
        is_bed = cb_pr.currentText() == "bedslinger_prototype"
        sp_nx.setEnabled(is_bed)
        sp_ny.setEnabled(is_bed)
        sp_xp.setEnabled(is_bed)
        sp_uy.setEnabled(is_bed)
        ed_uv_tool.setEnabled(not is_bed)

    tab.add_post_change_hook(_sync_printer_type)
    tab.add_section("Needle & UV Offsets", _page_container([g_off]), "uv")

    tab.load_from_config(cfg)
    return tab


def build_hybrid_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("hybrid", preset_manager)
    r = cfg.resin
    u = cfg.uv
    h = cfg.hybrid

    # 1. Hybrid Mode & Interlocks (with FFF-only toggle!)
    g_mode, fm = _card("Hybrid Process Switches")
    banner = QLabel()
    banner.setWordWrap(True)
    chk_pump = tab.bind_checkbox(
        "hybrid.enable_resin_pumping",
        "Enable Photopolymer Pumping (resin && needle dosing G-code)",
        h.enable_resin_pumping,
        tooltip="Uncheck together with UV Curing to export clean G-code for a regular FFF printer.",
    )
    chk_uv = tab.bind_checkbox(
        "hybrid.enable_uv_curing",
        "Enable In-Situ UV Curing (UV LED sweeps && ON/OFF G-code)",
        h.enable_uv_curing,
        tooltip="Uncheck together with Photopolymer Pumping to export clean G-code for a regular FFF printer.",
    )
    fm.addRow(banner)
    fm.addRow(chk_pump)
    fm.addRow(chk_uv)

    note_fff = QLabel(
        "Tip: Disabling both Photopolymer Pumping and UV Curing keeps AlveoSlicer's 3D surface-normal "
        "cellular geometry while emitting clean single-extruder G-code ready for any standard FFF printer."
    )
    note_fff.setObjectName("mutedLabel")
    note_fff.setWordWrap(True)
    fm.addRow(note_fff)
    tab.add_section("Hybrid Mode", _page_container([g_mode]), "settings")

    # 2. Resin Pump & Needle
    g_pump, fp = _card("Dispenser Pump && Needle Geometry")
    fp.addRow("Needle outer radius:", tab.bind_dspin("resin.needle_outer_radius_mm", r.needle_outer_radius_mm, 0.1, 3.0, 0.05, 2, "mm"))
    fp.addRow("Needle wall safety clearance:", tab.bind_dspin("resin.needle_safety_clear_mm", r.needle_safety_clear_mm, 0.0, 2.0, 0.05, 2, "mm"))
    fp.addRow("Pump calibration:", tab.bind_dspin("resin.pump_mm3_per_e_mm", r.pump_mm3_per_e_mm, 0.05, 50.0, 0.1, 3, "mm³/E"))
    fp.addRow("Dispense speed:", tab.bind_dspin("resin.dispense_speed_e_mm_s", r.dispense_speed_e_mm_s, 0.5, 50.0, 0.5, 1, "mm/s"))
    fp.addRow("Suck-back retraction:", tab.bind_dspin("resin.suckback_e_mm", r.suckback_e_mm, 0.0, 10.0, 0.1, 2, "mm"))
    fp.addRow("Suck-back speed:", tab.bind_dspin("resin.suckback_speed_e_mm_s", r.suckback_speed_e_mm_s, 1.0, 80.0, 1.0, 1, "mm/s"))
    tab.add_section("Resin Pump & Needle", _page_container([g_pump]), "resin")

    # 3. UV Hardening
    g_uv, fu = _card("UV Optics && Thermal Split-Passes")
    fu.addRow("UV irradiance:", tab.bind_dspin("uv.irradiance_mw_cm2", u.irradiance_mw_cm2, 1.0, 500.0, 1.0, 1, "mW/cm²"))
    fu.addRow("Beam width:", tab.bind_dspin("uv.beam_width_cm", u.beam_width_cm, 0.1, 20.0, 0.1, 2, "cm"))
    fu.addRow("Target dose / mm:", tab.bind_dspin("uv.target_dose_mj_cm2_per_mm", u.target_dose_mj_cm2_per_mm, 5.0, 1000.0, 5.0, 1, "mJ/cm²/mm"))
    fu.addRow("Min thermal passes:", tab.bind_spin("uv.min_passes", u.min_passes, 1, 12))
    fu.addRow("Inter-pass cooldown:", tab.bind_spin("uv.inter_pass_cooldown_ms", u.inter_pass_cooldown_ms, 0, 20000, 250, "ms"))
    fu.addRow("Focal Z clearance:", tab.bind_dspin("uv.focal_z_clearance_mm", u.focal_z_clearance_mm, 2.0, 150.0, 1.0, 1, "mm"))
    fu.addRow("Min scan speed:", tab.bind_dspin("uv.min_scan_speed_mm_s", u.min_scan_speed_mm_s, 0.5, 100.0, 0.5, 1, "mm/s"))
    fu.addRow("Max scan speed:", tab.bind_dspin("uv.max_scan_speed_mm_s", u.max_scan_speed_mm_s, 1.0, 200.0, 2.0, 1, "mm/s"))
    fu.addRow("UV ON G-code:", tab.bind_line_edit("uv.uv_on_gcode", u.uv_on_gcode, fallback="M106 P2 S255"))
    fu.addRow("UV OFF G-code:", tab.bind_line_edit("uv.uv_off_gcode", u.uv_off_gcode, fallback="M106 P2 S0"))
    tab.add_section("UV Hardening", _page_container([g_uv]), "uv")

    def _sync_hybrid_toggles() -> None:
        p_on = chk_pump.isChecked()
        u_on = chk_uv.isChecked()
        g_pump.setEnabled(p_on)
        g_uv.setEnabled(u_on)
        if not p_on and not u_on:
            banner.setObjectName("modeBannerFFF")
            banner.setText(
                "Standard FFF Mode Active — Resin pumping and UV curing are disabled. "
                "Output G-code is clean single-extruder FFF ready for any regular 3D printer."
            )
        elif p_on and u_on:
            banner.setObjectName("modeBannerHybrid")
            banner.setText(
                "Full Hybrid Mode Active — Photopolymer dosing and in-situ UV curing sweeps are enabled."
            )
        elif p_on:
            banner.setObjectName("modeBannerFFF")
            banner.setText("Partial Hybrid Mode — Photopolymer pumping ON, in-situ UV curing OFF.")
        else:
            banner.setObjectName("modeBannerFFF")
            banner.setText("Partial Hybrid Mode — Photopolymer pumping OFF, in-situ UV curing ON.")
        banner.style().unpolish(banner)
        banner.style().polish(banner)

    tab.add_post_change_hook(_sync_hybrid_toggles)
    tab.load_from_config(cfg)
    return tab
