"""Base PrusaSlicer-style two-pane settings tab with preset bar and typed field bindings.

Provides ``CategorySettingsTab`` (preset selector, Save/Save As/Revert/Delete,
modified badge, resizable icon sidebar + stacked scrollable form cards) along
with ``make_card`` and ``make_page_container`` helpers shared by every settings tab.
"""

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

        # ---- Resizable Two-Pane Body ----
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

    def add_section(self, title: str, content_widget: QWidget, icon_name: str = "settings") -> None:
        """Append a sidebar entry and scrollable page to this settings tab."""
        pal = get_palette(self._resolved_theme)
        item = QListWidgetItem(make_icon(icon_name, pal.icon_fg, pal.icon_accent, 16), title)
        self.sidebar.addItem(item)
        self._section_icons.append((item, icon_name))
        self.pages.addWidget(wrap_scroll(content_widget))
        if self.sidebar.count() == 1:
            self.sidebar.setCurrentRow(0)

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
        if tooltip:
            sb.setToolTip(tooltip)
        sb.valueChanged.connect(self._on_any_widget_changed)
        self._bindings[key] = _FieldBinding(key, sb, sb.value, lambda v, w=sb: w.setValue(int(v)))
        return sb

    def bind_combo(self, key: str, items: List[str], current: str, tooltip: str = "") -> QComboBox:
        """Create and bind a ``QComboBox`` to config dot-path ``key``."""
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
        """Create and bind a single-line ``QLineEdit`` with empty-string fallback."""
        ed = QLineEdit(val)
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

    def refresh_preset_list(self, select_name: Optional[str] = None) -> None:
        """Rebuild the preset combo box from ``PresetManager`` while preserving selection."""
        target = select_name or self._active_preset_name
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        presets = self.preset_manager.list_presets(self.category)
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


def make_card(title: str) -> Tuple[QGroupBox, QFormLayout]:
    """Create a titled ``QGroupBox`` card with a right-aligned ``QFormLayout``."""
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    return box, form


def make_page_container(widgets: List[QWidget]) -> QWidget:
    """Stack ``widgets`` vertically inside a padded settings page with trailing stretch."""
    w = QWidget()
    vbox = QVBoxLayout(w)
    vbox.setContentsMargins(6, 4, 10, 12)
    vbox.setSpacing(10)
    for child in widgets:
        vbox.addWidget(child)
    vbox.addStretch(1)
    return w
