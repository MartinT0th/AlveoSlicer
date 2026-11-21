"""Reusable PySide6 form widget factory helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox, QScrollArea, QSpinBox, QWidget


def make_double_spin(
    val: float,
    lo: float,
    hi: float,
    step: float = 0.1,
    decimals: int = 2,
    suffix: str = "",
) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setSingleStep(step)
    sb.setDecimals(decimals)
    sb.setValue(val)
    if suffix:
        sb.setSuffix(f" {suffix}")
    return sb


def make_spin(
    val: int,
    lo: int,
    hi: int,
    step: int = 1,
    suffix: str = "",
) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(lo, hi)
    sb.setSingleStep(step)
    sb.setValue(val)
    if suffix:
        sb.setSuffix(f" {suffix}")
    return sb


def wrap_scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(widget)
    return area
