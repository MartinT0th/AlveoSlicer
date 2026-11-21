"""PrusaSlicer-style two-pane settings tabs with per-category preset management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import SlicerConfig
from ..presets import CATEGORY_LABELS, PresetManager, apply_category_values, extract_category_values
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
      - Top Preset Bar (dropdown, modified badge, Save, Save As, Delete, Revert)
      - Left Category Sidebar (QListWidget)
      - Right Stacked Scrollable Form Cards
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
        lbl_title.setStyleSheet("font-weight: 700; color: #eceff4;")
        bar_layout.addWidget(lbl_title)

        self.cb_preset = QComboBox()
        self.cb_preset.setMinimumWidth(290)
        self.cb_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
        bar_layout.addWidget(self.cb_preset, stretch=1)

        self.lbl_modified = QLabel("Modified")
        self.lbl_modified.setVisible(False)
        self.lbl_modified.setStyleSheet(
            "background-color: rgba(245, 197, 24, 0.18); color: #f5c518; "
            "border: 1px solid rgba(245, 197, 24, 0.55); border-radius: 4px; "
            "padding: 2px 8px; font-weight: 700; font-size: 11px;"
        )
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

        # ---- Main Two-Pane Body (Left Sidebar + Right Form Stack) ----
        body = QHBoxLayout()
        body.setSpacing(10)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("categoryList")
        self.sidebar.setFixedWidth(220)
        self.sidebar.currentRowChanged.connect(self._on_section_changed)
        body.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        body.addWidget(self.pages, stretch=1)

        root.addLayout(body, stretch=1)

        self.refresh_preset_list(select_name=self._active_preset_name)

    # -------------------------------------------------------------------------
    # Page & Field Registration API
    # -------------------------------------------------------------------------

    def add_section(self, title: str, content_widget: QWidget) -> None:
        self.sidebar.addItem(title)
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

    # 1. Layers and Perimeters
    g_layer, fl = _card("Layer Height & Extrusion Width")
    fl.addRow("Layer height:", tab.bind_dspin("fff.layer_height", f.layer_height, 0.05, 1.0, 0.05, 2, "mm"))
    fl.addRow("Line width:", tab.bind_dspin("fff.line_width", f.line_width, 0.20, 2.0, 0.05, 2, "mm"))

    g_shells, fs = _card("Watertight Shells & Solid Skins")
    fs.addRow("Outer perimeters:", tab.bind_spin("fff.perimeters", f.perimeters, 1, 10))
    fs.addRow("Bottom solid layers:", tab.bind_spin("fff.bottom_solid_layers", f.bottom_solid_layers, 1, 20))
    fs.addRow("Top solid layers:", tab.bind_spin("fff.top_solid_layers", f.top_solid_layers, 1, 20))
    tab.add_section("Layers & Perimeters", _page_container([g_layer, g_shells]))

    # 2. 3D Cellular Infill
    g_pat, fp = _card("3D Surface-Normal Cellular Pattern")
    cb_pat = tab.bind_combo("infill.pattern", ["hexagonal", "voronoi"], i.pattern)
    cb_mode = tab.bind_combo("infill.mode", ["hollow_shell", "full_to_medial"], i.mode)
    sp_hw = tab.bind_dspin("infill.hollow_wall_thickness", i.hollow_wall_thickness, 1.5, 30.0, 0.5, 2, "mm")
    sp_is = tab.bind_dspin("infill.inner_skin_thickness", i.inner_skin_thickness, 0.4, 5.0, 0.1, 2, "mm")
    sp_br = tab.bind_dspin("infill.max_bridge_span", i.max_bridge_span, 2.0, 30.0, 0.5, 2, "mm")
    chk_nd, sp_nd = tab.bind_optional_dspin(
        "infill.max_normal_depth", "Cap normal depth (isotropic core)", 10.0, 2.0, 80.0, 1.0, 1, "mm"
    )
    fp.addRow("Cell pattern:", cb_pat)
    fp.addRow("Core topology mode:", cb_mode)
    fp.addRow("Hollow composite wall:", sp_hw)
    fp.addRow("Hollow inner skin:", sp_is)
    fp.addRow("Max bridgeable span:", sp_br)
    fp.addRow(chk_nd)
    fp.addRow("Core transition depth:", sp_nd)

    g_geo, fg = _card("Cell Formwork & Photopolymer Channel")
    fg.addRow("Target cell pitch:", tab.bind_dspin("infill.target_cell_pitch", i.target_cell_pitch, 2.5, 25.0, 0.5, 2, "mm"))
    fg.addRow("Cell wall half-thickness (r):", tab.bind_dspin("infill.strut_radius", i.strut_radius, 0.20, 3.0, 0.05, 2, "mm"))
    fg.addRow("Resin channel width:", tab.bind_dspin("infill.min_channel_clearance", i.min_channel_clearance, 0.40, 10.0, 0.1, 2, "mm"))
    fg.addRow("Flatten shallow faces (<):", tab.bind_dspin("infill.flatten_angle_deg", i.flatten_angle_deg, 0.0, 80.0, 1.0, 1, "deg"))
    fg.addRow("Cell corner fillet radius:", tab.bind_dspin("infill.smin_blend_radius", i.smin_blend_radius, 0.0, 2.0, 0.05, 2, "mm"))
    fg.addRow("Divergence split ratio:", tab.bind_dspin("infill.max_divergence_ratio", i.max_divergence_ratio, 1.2, 3.0, 0.1, 2))
    fg.addRow("Medial weld ratio:", tab.bind_dspin("infill.medial_buffer_ratio", i.medial_buffer_ratio, 0.50, 0.99, 0.02, 2))
    fg.addRow("Shock cos threshold:", tab.bind_dspin("infill.shock_cos_threshold", i.shock_cos_threshold, -0.5, 0.95, 0.05, 2))

    def _sync_mode() -> None:
        is_hollow = cb_mode.currentText() == "hollow_shell"
        sp_hw.setEnabled(is_hollow)
        sp_is.setEnabled(is_hollow)
        chk_nd.setEnabled(not is_hollow)
        sp_nd.setEnabled((not is_hollow) and chk_nd.isChecked())

    tab.add_post_change_hook(_sync_mode)
    tab.add_section("3D Cellular Infill", _page_container([g_pat, g_geo]))

    # 3. Speed
    g_spd, fsp = _card("FFF Motion Speeds")
    fsp.addRow("Print speed:", tab.bind_dspin("fff.print_speed_mm_s", f.print_speed_mm_s, 5.0, 300.0, 5.0, 1, "mm/s"))
    fsp.addRow("Travel speed:", tab.bind_dspin("fff.travel_speed_mm_s", f.travel_speed_mm_s, 20.0, 500.0, 10.0, 1, "mm/s"))
    tab.add_section("Speed", _page_container([g_spd]))

    # 4. Dry Core Support
    g_sup, fsu = _card("Hollow Dry-Core Support")
    fsu.addRow(
        "Rectilinear support spacing:",
        tab.bind_dspin("fff.dry_support_spacing_mm", f.dry_support_spacing_mm, 1.5, 20.0, 0.5, 1, "mm"),
    )
    note_sup = QLabel(
        "Sparse dry FFF support is automatically projected strictly underneath overhanging inner ceilings "
        "inside the dry hollow core (zero resin contact)."
    )
    note_sup.setObjectName("mutedLabel")
    note_sup.setWordWrap(True)
    fsu.addRow(note_sup)
    tab.add_section("Dry Core Support", _page_container([g_sup]))

    # 5. Engine & Precision
    g_eng, fe = _card("Slicing Engine & CAD Tessellation")
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
    tab.add_section("Engine & Precision", _page_container([g_eng]))

    tab.load_from_config(cfg)
    return tab


def build_filament_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("filament", preset_manager)
    f = cfg.fff

    g_fil, ff = _card("Filament & Temperatures")
    ff.addRow("Filament diameter:", tab.bind_dspin("fff.filament_diameter", f.filament_diameter, 1.0, 3.0, 0.05, 2, "mm"))
    ff.addRow("Extrusion multiplier:", tab.bind_dspin("fff.flow_multiplier", f.flow_multiplier, 0.5, 1.5, 0.02, 2))
    ff.addRow("Nozzle temperature:", tab.bind_spin("fff.nozzle_temp_c", f.nozzle_temp_c, 0, 350, 5, "°C"))
    ff.addRow("Bed temperature:", tab.bind_spin("fff.bed_temp_c", f.bed_temp_c, 0, 130, 5, "°C"))
    tab.add_section("Filament", _page_container([g_fil]))

    g_cool, fc = _card("Part Cooling")
    fc.addRow("Part cooling fan speed:", tab.bind_spin("fff.part_cooling_fan_pct", f.part_cooling_fan_pct, 0, 100, 5, "%"))
    tab.add_section("Cooling", _page_container([g_cool]))

    g_ret, fr = _card("Retraction & Z-Hop")
    fr.addRow("Retraction length:", tab.bind_dspin("fff.retract_length_mm", f.retract_length_mm, 0.0, 10.0, 0.2, 2, "mm"))
    fr.addRow("Retraction speed:", tab.bind_dspin("fff.retract_speed_mm_s", f.retract_speed_mm_s, 5.0, 120.0, 5.0, 1, "mm/s"))
    fr.addRow("Lift Z (Z-hop):", tab.bind_dspin("fff.z_hop_mm", f.z_hop_mm, 0.0, 5.0, 0.1, 2, "mm"))
    tab.add_section("Retraction", _page_container([g_ret]))

    tab.load_from_config(cfg)
    return tab


def build_resin_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("resin", preset_manager)
    r = cfg.resin

    g_phys, fp = _card("Beer-Lambert Optical & Curing Physics")
    fp.addRow("Max cure depth (C_d):", tab.bind_dspin("resin.max_cure_depth_mm", r.max_cure_depth_mm, 0.2, 10.0, 0.1, 2, "mm"))
    fp.addRow("Cure depth safety factor (η):", tab.bind_dspin("resin.cure_depth_safety_eta", r.cure_depth_safety_eta, 0.5, 1.0, 0.05, 2))
    fp.addRow("Meniscus headspace:", tab.bind_dspin("resin.headspace_margin_mm", r.headspace_margin_mm, 0.0, 1.0, 0.05, 2, "mm"))
    fp.addRow("Shrinkage compensation:", tab.bind_dspin("resin.shrinkage_factor", r.shrinkage_factor, 0.90, 1.25, 0.01, 3))
    tab.add_section("Curing Physics", _page_container([g_phys]))

    g_fluid, ff = _card("Viscosity & Channel Flow")
    ff.addRow("Max basin flow radius:", tab.bind_dspin("resin.max_flow_radius_mm", r.max_flow_radius_mm, 2.0, 50.0, 1.0, 1, "mm"))
    ff.addRow("Viscosity leveling dwell:", tab.bind_spin("resin.settle_dwell_ms", r.settle_dwell_ms, 0, 30000, 250, "ms"))
    tab.add_section("Fluid & Flow", _page_container([g_fluid]))

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
    tab.add_section("DP Scheduler", _page_container([g_dp]))

    tab.load_from_config(cfg)
    return tab


def build_printer_settings_tab(preset_manager: PresetManager, cfg: SlicerConfig) -> CategorySettingsTab:
    tab = CategorySettingsTab("printer", preset_manager)
    p = cfg.printer
    f = cfg.fff

    g_gen, fg = _card("Kinematics & Build Volume")
    cb_pr = tab.bind_combo("printer.printer_type", ["bedslinger_prototype", "corexy_toolchanger"], p.printer_type)
    fg.addRow("Printer HAL architecture:", cb_pr)
    fg.addRow("Bed size X:", tab.bind_dspin("printer.bed_size_x", p.bed_size_x, 50.0, 600.0, 10.0, 1, "mm"))
    fg.addRow("Bed size Y:", tab.bind_dspin("printer.bed_size_y", p.bed_size_y, 50.0, 600.0, 10.0, 1, "mm"))
    fg.addRow("Max print height Z:", tab.bind_dspin("printer.bed_size_z", p.bed_size_z, 50.0, 600.0, 10.0, 1, "mm"))
    tab.add_section("General & Bed", _page_container([g_gen]))

    g_ext, fe = _card("Extruder & Tool Commands")
    fe.addRow("FFF nozzle diameter:", tab.bind_dspin("fff.nozzle_diameter", f.nozzle_diameter, 0.15, 1.5, 0.05, 2, "mm"))
    fe.addRow("FFF tool command:", tab.bind_line_edit("printer.tool_fff", p.tool_fff, fallback="T0"))
    fe.addRow("Resin pump tool command:", tab.bind_line_edit("printer.tool_resin", p.tool_resin, fallback="T1"))
    ed_uv_tool = tab.bind_line_edit("printer.tool_uv", p.tool_uv, fallback="T2")
    fe.addRow("UV toolhead command:", ed_uv_tool)
    fe.addRow("Resin pump axis:", tab.bind_line_edit("printer.resin_extruder_axis", p.resin_extruder_axis, fallback="E"))
    tab.add_section("Extruder & Tools", _page_container([g_ext]))

    g_off, fo = _card("Needle & Overhead UV Strip Offsets")
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
    tab.add_section("Needle & UV Offsets", _page_container([g_off]))

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
    tab.add_section("Hybrid Mode", _page_container([g_mode]))

    # 2. Resin Pump & Needle
    g_pump, fp = _card("Dispenser Pump & Needle Geometry")
    fp.addRow("Needle outer radius:", tab.bind_dspin("resin.needle_outer_radius_mm", r.needle_outer_radius_mm, 0.1, 3.0, 0.05, 2, "mm"))
    fp.addRow("Needle wall safety clearance:", tab.bind_dspin("resin.needle_safety_clear_mm", r.needle_safety_clear_mm, 0.0, 2.0, 0.05, 2, "mm"))
    fp.addRow("Pump calibration:", tab.bind_dspin("resin.pump_mm3_per_e_mm", r.pump_mm3_per_e_mm, 0.05, 50.0, 0.1, 3, "mm³/E"))
    fp.addRow("Dispense speed:", tab.bind_dspin("resin.dispense_speed_e_mm_s", r.dispense_speed_e_mm_s, 0.5, 50.0, 0.5, 1, "mm/s"))
    fp.addRow("Suck-back retraction:", tab.bind_dspin("resin.suckback_e_mm", r.suckback_e_mm, 0.0, 10.0, 0.1, 2, "mm"))
    fp.addRow("Suck-back speed:", tab.bind_dspin("resin.suckback_speed_e_mm_s", r.suckback_speed_e_mm_s, 1.0, 80.0, 1.0, 1, "mm/s"))
    tab.add_section("Resin Pump", _page_container([g_pump]))

    # 3. UV Hardening
    g_uv, fu = _card("UV Optics & Thermal Split-Passes")
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
    tab.add_section("UV Light", _page_container([g_uv]))

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
        # Re-polish stylesheet after dynamic objectName change
        banner.style().unpolish(banner)
        banner.style().polish(banner)

    tab.add_post_change_hook(_sync_hybrid_toggles)
    tab.load_from_config(cfg)
    return tab
