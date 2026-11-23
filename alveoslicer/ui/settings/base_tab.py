"""Base PrusaSlicer-style two-pane settings tab with preset bar and typed field bindings.

Provides ``CategorySettingsTab`` (preset selector, Save/Save As/Revert/Delete,
modified badge, resizable icon sidebar + stacked scrollable form cards) along
with ``make_card`` and ``make_page_container`` helpers shared by every settings tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
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

from ...core.config import SlicerConfig
from ...core.presets import CATEGORY_LABELS, PresetManager, apply_category_values, extract_category_values
from ..styling import get_palette, make_double_spin, make_icon, make_spin, wrap_scroll


@dataclass
class _FieldBinding:
    """Two-way binding between a dot-path config key and its Qt input widget."""

    key: str
    widget: QWidget
    getter: Callable[[], Any]
    setter: Callable[[Any], None]


class CategorySettingsTab(QWidget):
    """PrusaSlicer-style settings tab for a single preset category.

    Layout:
      - Top Preset Bar (dropdown, ``Modified`` pill, Save, Save As, Revert, Delete)
      - Resizable horizontal splitter with Left Icon Category Sidebar + Right Stacked Form Cards
    """

    values_changed = Signal(str)          # emitted when any bound field changes (category)
    preset_selected = Signal(str, str)    # emitted when user picks a preset (category, name)
    presets_mutated = Signal(str)         # emitted when user saves or deletes a preset (category)

    def __init__(
        self,
        category: str,
        preset_manager: PresetManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Build the preset bar, icon sidebar, and page stack for ``category``."""
        super().__init__(parent)
        self.category = category
        self.preset_manager = preset_manager
        self._active_preset_name: str = preset_manager.default_preset_name(category)
        self._bindings: Dict[str, _FieldBinding] = {}
        self._suppress_signals = False
        self._post_change_hooks: List[Callable[[], None]] = []
        self._section_icons: List[Tuple[QListWidgetItem, str]] = []
        self._section_meta: List[Tuple[QListWidgetItem, bool, bool]] = []
        self._visibility_widgets: List[QWidget] = []
        self._advanced_ids: Set[int] = set()
        self._hybrid_only_ids: Set[int] = set()
        self._hybrid_only_form_rows: List[Tuple[QFormLayout, QWidget]] = []
        self._experience_mode: str = "advanced"
        self._hybrid_enabled: bool = True
        self._preset_filter: Optional[Callable[[Any], bool]] = None
        self._resolved_theme: str = "dark"

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---- Top Preset Management Bar ----
        bar = QFrame()
        bar.setObjectName("presetBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)

        lbl_title = QLabel(f"{CATEGORY_LABELS[category]} Preset:")
        lbl_title.setObjectName("sectionTitle")
        bar_layout.addWidget(lbl_title)

        self.cb_preset = QComboBox()
        self.cb_preset.setMinimumWidth(260)
        self.cb_preset.setMaximumWidth(420)
        self.cb_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
        bar_layout.addWidget(self.cb_preset, stretch=1)

        self.lbl_modified = QLabel("Modified")
        self.lbl_modified.setObjectName("modifiedPill")
        self.lbl_modified.setVisible(False)
        bar_layout.addWidget(self.lbl_modified)

        bar_layout.addStretch(1)

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

        self.lbl_experience_hint = QLabel(
            "Simple mode — showing essential parameters. Switch to Advanced in the top bar for full calibration."
        )
        self.lbl_experience_hint.setObjectName("experienceHint")
        self.lbl_experience_hint.setVisible(False)
        root.addWidget(self.lbl_experience_hint)

        # ---- Resizable Two-Pane Body ----
        body_split = QSplitter(Qt.Orientation.Horizontal)
        body_split.setChildrenCollapsible(False)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("categoryList")
        self.sidebar.setIconSize(QSize(16, 16))
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setMinimumWidth(214)
        self.sidebar.setMaximumWidth(270)
        self.sidebar.currentRowChanged.connect(self._on_section_changed)
        body_split.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        body_split.addWidget(self.pages)
        body_split.setStretchFactor(0, 0)
        body_split.setStretchFactor(1, 1)
        body_split.setSizes([230, 960])

        root.addWidget(body_split, stretch=1)

        self.refresh_preset_list(select_name=self._active_preset_name)
        self.apply_theme("dark")

    # ------------------------------------------------------------------
    # Theme & Icons
    # ------------------------------------------------------------------

    def apply_theme(self, resolved_mode: str) -> None:
        """Re-tint preset toolbar and sidebar icons to match ``resolved_mode``."""
        self._resolved_theme = resolved_mode
        pal = get_palette(resolved_mode)
        self.btn_save.setIcon(make_icon("save", pal.icon_fg, pal.icon_accent, 15))
        self.btn_save_as.setIcon(make_icon("plus", pal.icon_fg, pal.icon_accent, 15))
        self.btn_revert.setIcon(make_icon("revert", pal.icon_fg, pal.icon_accent, 15))
        self.btn_delete.setIcon(make_icon("delete", pal.danger_fg, pal.danger_fg, 15))
        for item, icon_name in self._section_icons:
            item.setIcon(make_icon(icon_name, pal.icon_fg, pal.icon_accent, 16))

    # ------------------------------------------------------------------
    # Page & Typed Field Binding API
    # ------------------------------------------------------------------

    def add_section(
        self,
        title: str,
        content_widget: QWidget,
        icon_name: str = "settings",
        advanced: bool = False,
        hybrid_only: bool = False,
    ) -> None:
        """Append a sidebar entry and scrollable page to this settings tab."""
        pal = get_palette(self._resolved_theme)
        item = QListWidgetItem(make_icon(icon_name, pal.icon_fg, pal.icon_accent, 16), title)
        self.sidebar.addItem(item)
        self._section_icons.append((item, icon_name))
        self._section_meta.append((item, advanced, hybrid_only))
        self.pages.addWidget(wrap_scroll(content_widget))
        if self.sidebar.count() == 1:
            self.sidebar.setCurrentRow(0)

    def _track_visibility_widget(self, w: QWidget) -> None:
        if w not in self._visibility_widgets:
            self._visibility_widgets.append(w)

    def mark_advanced(self, *widgets: QWidget) -> None:
        """Register cards or controls that are hidden in Simple mode."""
        for w in widgets:
            self._track_visibility_widget(w)
            self._advanced_ids.add(id(w))

    def mark_hybrid_only(self, *widgets: QWidget) -> None:
        """Register cards or controls that are hidden when an FFF-only printer is selected."""
        for w in widgets:
            self._track_visibility_widget(w)
            self._hybrid_only_ids.add(id(w))

    def mark_hybrid_form_rows(self, form: QFormLayout, *field_widgets: QWidget) -> None:
        """Register individual ``QFormLayout`` rows to hide when FFF-only mode is active."""
        for w in field_widgets:
            self._hybrid_only_form_rows.append((form, w))

    @property
    def hybrid_enabled(self) -> bool:
        """Whether the active printer supports hybrid resin/UV features."""
        return self._hybrid_enabled

    def set_experience_mode(self, mode: str) -> None:
        """Toggle visibility of advanced sections and cards without altering field bindings."""
        self._experience_mode = "simple" if str(mode).lower() == "simple" else "advanced"
        self._apply_visibility_filters()

    def set_hybrid_enabled(self, enabled: bool) -> None:
        """Show or hide hybrid resin/UV sections and cards without altering field bindings."""
        self._hybrid_enabled = bool(enabled)
        self._apply_visibility_filters()
        for hook in self._post_change_hooks:
            hook()

    def _apply_visibility_filters(self) -> None:
        is_adv = self._experience_mode == "advanced"
        is_hyb = self._hybrid_enabled

        has_hidden_adv = False
        first_visible_row = -1
        for idx, (item, sec_adv, sec_hyb) in enumerate(self._section_meta):
            hide = (sec_adv and not is_adv) or (sec_hyb and not is_hyb)
            item.setHidden(hide)
            if sec_adv and not is_adv and not (sec_hyb and not is_hyb):
                has_hidden_adv = True
            elif not hide and first_visible_row < 0:
                first_visible_row = idx

        for w in self._visibility_widgets:
            wid = id(w)
            hide_adv = (wid in self._advanced_ids) and (not is_adv)
            hide_hyb = (wid in self._hybrid_only_ids) and (not is_hyb)
            w.setVisible(not (hide_adv or hide_hyb))
            if hide_adv and not hide_hyb:
                has_hidden_adv = True

        for form, field_w in self._hybrid_only_form_rows:
            if hasattr(form, "setRowVisible"):
                form.setRowVisible(field_w, is_hyb)
            else:
                lbl = form.labelForField(field_w)
                if lbl is not None:
                    lbl.setVisible(is_hyb)
                field_w.setVisible(is_hyb)

        self.lbl_experience_hint.setVisible((not is_adv) and has_hidden_adv)

        cur_row = self.sidebar.currentRow()
        if (
            0 <= cur_row < len(self._section_meta)
            and self._section_meta[cur_row][0].isHidden()
            and first_visible_row >= 0
        ):
            self.sidebar.setCurrentRow(first_visible_row)

    def add_post_change_hook(self, fn: Callable[[], None]) -> None:
        """Register a callback invoked whenever bound values load or change."""
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
        """Create and bind a ``QDoubleSpinBox`` to config dot-path ``key``."""
        sb = make_double_spin(val, lo, hi, step, decimals, suffix)
        sb.setFixedWidth(156)
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
        """Create and bind an integer ``QSpinBox`` to config dot-path ``key``."""
        sb = make_spin(val, lo, hi, step, suffix)
        sb.setFixedWidth(156)
        if tooltip:
            sb.setToolTip(tooltip)
        sb.valueChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(key, sb, sb.value, lambda v, w=sb: w.setValue(int(v)))
        return sb

    def bind_combo(self, key: str, items: List[str], current: str, tooltip: str = "") -> QComboBox:
        """Create and bind a ``QComboBox`` to config dot-path ``key``."""
        cb = QComboBox()
        cb.setMinimumWidth(210)
        cb.setMaximumWidth(280)
        cb.addItems(items)
        cb.setCurrentText(current)
        if tooltip:
            cb.setToolTip(tooltip)
        cb.currentTextChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(
            key, cb, cb.currentText, lambda v, w=cb: w.setCurrentText(str(v))
        )
        return cb

    def bind_combo_mapped(
        self,
        key: str,
        items: Sequence[Tuple[str, str]],
        current: str,
        tooltip: str = "",
    ) -> QComboBox:
        """Create and bind a ``QComboBox`` showing human labels while storing raw enum values."""
        cb = QComboBox()
        cb.setMinimumWidth(220)
        cb.setMaximumWidth(320)
        for val, label in items:
            cb.addItem(label, userData=val)
        cur_idx = cb.findData(str(current))
        if cur_idx >= 0:
            cb.setCurrentIndex(cur_idx)
        if tooltip:
            cb.setToolTip(tooltip)
        cb.currentIndexChanged.connect(self._on_any_widget_changed)

        def _set_val(v: Any, w: QComboBox = cb) -> None:
            idx = w.findData(str(v))
            if idx >= 0:
                w.setCurrentIndex(idx)

        self._bindings[key] = _FieldBinding(
            key, cb, lambda w=cb: str(w.currentData() if w.currentData() is not None else w.currentText()), _set_val
        )
        return cb

    def bind_line_edit(self, key: str, val: str, fallback: str = "", tooltip: str = "") -> QLineEdit:
        """Create and bind a single-line ``QLineEdit`` with empty-string fallback."""
        ed = QLineEdit(val)
        ed.setMinimumWidth(190)
        ed.setMaximumWidth(260)
        if tooltip:
            ed.setToolTip(tooltip)
        ed.textChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(
            key, ed, lambda w=ed, fb=fallback: w.text().strip() or fb, lambda v, w=ed: w.setText(str(v))
        )
        return ed

    def bind_plain_text(
        self, key: str, val: str, placeholder: str = "", height: int = 86, tooltip: str = ""
    ) -> QPlainTextEdit:
        """Create and bind a multi-line ``QPlainTextEdit`` (e.g., custom G-code hooks)."""
        ed = QPlainTextEdit(val)
        ed.setPlaceholderText(placeholder)
        ed.setFixedHeight(height)
        ed.setMinimumWidth(380)
        ed.setMaximumWidth(580)
        if tooltip:
            ed.setToolTip(tooltip)
        ed.textChanged.connect(self._on_any_widget_changed)

        def _set_text(v: Any, w: QPlainTextEdit = ed) -> None:
            txt = "" if v is None else str(v)
            if w.toPlainText() != txt:
                w.setPlainText(txt)

        self._bindings[key] = _FieldBinding(key, ed, lambda w=ed: w.toPlainText(), _set_text)
        return ed

    def bind_checkbox(self, key: str, label: str, checked: bool, tooltip: str = "") -> QCheckBox:
        """Create and bind a ``QCheckBox`` to boolean config dot-path ``key``."""
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
        """Create and bind a checkbox-gated ``Optional[float]`` spinbox."""
        chk = QCheckBox(check_label)
        sb = make_double_spin(default_val, lo, hi, step, decimals, suffix)
        sb.setFixedWidth(156)
        sb.setEnabled(False)

        def _sync() -> None:
            sb.setEnabled(chk.isChecked())
            self._on_any_widget_changed()

        chk.toggled.connect(_sync)
        sb.valueChanged.connect(self._on_any_widget_changed)

        def _set(v: Any) -> None:
            enabled = v is not None
            chk.setChecked(enabled)
            if enabled:
                sb.setValue(float(v))
            sb.setEnabled(enabled)

        self._bindings[key] = _FieldBinding(
            key, sb, lambda: sb.value() if chk.isChecked() else None, _set
        )
        return chk, sb

    def add_dspin_rows(
        self,
        form: QFormLayout,
        specs: List[Tuple[str, str, float, float, float, float, int, str]],
    ) -> List[QDoubleSpinBox]:
        """Batch-bind and append multiple ``QDoubleSpinBox`` rows from declarative specs."""
        widgets: List[QDoubleSpinBox] = []
        for label, key, val, lo, hi, step, decimals, suffix in specs:
            sb = self.bind_dspin(key, val, lo, hi, step, decimals, suffix)
            form.addRow(label, sb)
            widgets.append(sb)
        return widgets

    # ------------------------------------------------------------------
    # Config & Preset Sync
    # ------------------------------------------------------------------

    @property
    def active_preset_name(self) -> str:
        """Return the currently selected preset name for this tab."""
        return self._active_preset_name

    def current_values(self) -> Dict[str, Any]:
        """Collect all bound field values as a ``{dot_path: value}`` dict."""
        return {k: b.getter() for k, b in self._bindings.items()}

    def load_from_config(self, cfg: SlicerConfig) -> None:
        """Populate every bound widget from ``cfg`` without emitting spurious signals."""
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
        """Apply all current widget values back into ``cfg``."""
        apply_category_values(cfg, self.category, self.current_values())

    def set_preset_filter(
        self,
        predicate: Optional[Callable[[Any], bool]],
        select_name: Optional[str] = None,
    ) -> None:
        """Filter presets shown in the category dropdown without deleting hidden presets."""
        self._preset_filter = predicate
        self.refresh_preset_list(select_name=select_name or self._active_preset_name)

    def refresh_preset_list(self, select_name: Optional[str] = None) -> None:
        """Rebuild the preset combo box from ``PresetManager`` while preserving selection."""
        target = select_name or self._active_preset_name
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        all_presets = self.preset_manager.list_presets(self.category)
        if self._preset_filter is not None:
            presets = [p for p in all_presets if self._preset_filter(p)] or all_presets
        else:
            presets = all_presets
        selected_idx = 0
        for idx, p in enumerate(presets):
            self.cb_preset.addItem(p.name if p.builtin else f"{p.name}  (User)", userData=p.name)
            if p.name == target:
                selected_idx = idx
        if presets:
            self.cb_preset.setCurrentIndex(selected_idx)
            self._active_preset_name = presets[selected_idx].name
        self.cb_preset.blockSignals(False)
        self._update_preset_buttons()

    def select_preset_by_name(self, name: str, emit: bool = False) -> None:
        """Programmatically select preset ``name`` in the dropdown."""
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
        """Update the ``Modified`` badge and Save/Revert/Delete enabled states against ``cfg``."""
        modified = self.preset_manager.is_modified(cfg, self.category, self._active_preset_name)
        self.lbl_modified.setVisible(modified)
        self.btn_revert.setEnabled(modified)
        is_user = not self.preset_manager.is_builtin(self.category, self._active_preset_name)
        self.btn_save.setEnabled(is_user and modified)
        self.btn_delete.setEnabled(is_user)

    def _update_preset_buttons(self) -> None:
        """Enable or disable Save/Delete buttons depending on whether the preset is built-in."""
        is_user = not self.preset_manager.is_builtin(self.category, self._active_preset_name)
        self.btn_delete.setEnabled(is_user)
        if not is_user:
            self.btn_save.setEnabled(False)

    # ------------------------------------------------------------------
    # Internal Slots
    # ------------------------------------------------------------------

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
        name, ok = QInputDialog.getText(
            self,
            f"Save {CATEGORY_LABELS[self.category]} Preset",
            "Enter new preset name:",
            text=f"{self._active_preset_name} - Custom",
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


def make_card(title: str) -> Tuple[QFrame, QFormLayout]:
    """Create a flat titled settings card with a hairline header and capped-width form layout."""
    card = QFrame()
    card.setObjectName("settingsCard")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(16, 12, 16, 14)
    outer.setSpacing(10)

    hdr = QLabel(title.replace("&&", "&"))
    hdr.setObjectName("settingsCardHeader")
    outer.addWidget(hdr)

    div = QFrame()
    div.setObjectName("settingsDivider")
    outer.addWidget(div)

    form = QFormLayout()
    form.setContentsMargins(0, 2, 0, 0)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(9)
    outer.addLayout(form)
    return card, form


def make_page_container(widgets: List[QWidget]) -> QWidget:
    """Stack ``widgets`` vertically in a balanced max-width settings column."""
    outer = QWidget()
    outer_row = QHBoxLayout(outer)
    outer_row.setContentsMargins(6, 2, 12, 14)
    outer_row.setSpacing(0)

    col = QWidget()
    col.setMaximumWidth(820)
    vbox = QVBoxLayout(col)
    vbox.setContentsMargins(0, 0, 0, 0)
    vbox.setSpacing(12)
    for child in widgets:
        vbox.addWidget(child)
    vbox.addStretch(1)

    outer_row.addWidget(col, stretch=1)
    outer_row.addStretch(0)
    return outer
