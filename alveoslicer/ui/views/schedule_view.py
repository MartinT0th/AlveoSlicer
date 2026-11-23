"""DP Cure Schedule table tab widget.

Displays every scheduled photopolymer injection + UV cure cycle (layer span,
slab thickness, trigger reason, shadowed volume, dosing count, and volume in mL)
and emits ``layer_selected`` when a row is clicked.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...pipeline import SliceResult


class CureScheduleView(QWidget):
    """Clickable table summarizing all scheduled DP cure cycles."""

    layer_selected = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize the summary header banner and 8-column cure schedule table."""
        super().__init__(parent)
        self._slice_result: Optional[SliceResult] = None

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 10, 10, 6)
        vbox.setSpacing(8)

        self.lbl_summary = QLabel("No slice summary yet.")
        self.lbl_summary.setObjectName("summaryBanner")
        vbox.addWidget(self.lbl_summary)

        self.tbl_schedule = QTableWidget(0, 8)
        self.tbl_schedule.setHorizontalHeaderLabels(
            [
                "Cycle #",
                "Layers",
                "Span",
                "Slab (mm)",
                "Trigger",
                "Shadowed (mm3)",
                "Dosing Pts",
                "Resin (mL)",
            ]
        )
        self.tbl_schedule.setAlternatingRowColors(True)
        self.tbl_schedule.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_schedule.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_schedule.verticalHeader().setVisible(False)
        self.tbl_schedule.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_schedule.cellClicked.connect(self._on_row_clicked)
        vbox.addWidget(self.tbl_schedule, stretch=1)

        hint = QLabel("Click any row to jump directly to that cure layer in the Plater G-Code Previewer.")
        hint.setObjectName("mutedLabel")
        vbox.addWidget(hint)

    def apply_theme(self, resolved_mode: str) -> None:
        """Refresh widget polish when the active application theme changes."""
        del resolved_mode
        self.lbl_summary.style().unpolish(self.lbl_summary)
        self.lbl_summary.style().polish(self.lbl_summary)

    def populate(self, res: SliceResult) -> None:
        """Populate the summary banner and schedule table rows from ``res``."""
        self._slice_result = res
        self.lbl_summary.setText(
            f"Layers: {len(res.layers)}  |  3D Wireframe Edges: {len(res.capsules):,}  |  "
            f"Cure Cycles: {len(res.schedule)}  |  Total Resin: {res.total_resin_ml:.2f} mL  |  "
            f"G-Code Lines: {res.gcode_line_count:,}"
        )
        self.tbl_schedule.setRowCount(len(res.schedule))
        for row, cyc in enumerate(res.schedule):
            pts = res.dosing_by_layer.get(cyc.end_layer, [])
            vol_ml = sum(p.volume_mm3 for p in pts) / 1000.0
            span = cyc.end_layer - cyc.start_layer + 1
            vals = [
                str(row + 1),
                f"{cyc.start_layer} - {cyc.end_layer}",
                f"{span}",
                f"{cyc.slab_thickness_mm:.2f}",
                cyc.trigger,
                f"{cyc.shadowed_vol_mm3:.2f}",
                str(len(pts)),
                f"{vol_ml:.3f}",
            ]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tbl_schedule.setItem(row, col, item)

    def _on_row_clicked(self, row: int, _col: int) -> None:
        """Emit ``layer_selected`` for the clicked cycle's end layer."""
        if self._slice_result is None or row >= len(self._slice_result.schedule):
            return
        self.layer_selected.emit(self._slice_result.schedule[row].end_layer)
