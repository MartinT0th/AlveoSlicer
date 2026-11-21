"""PrusaSlicer-style vertical dual-handle layer range bar with UV cure-cycle tick markers."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _DualVerticalTrack(QWidget):
    """Custom-painted vertical track supporting upper/lower handles and cyan cure-cycle ticks."""

    range_changed = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(40)
        self.setMinimumHeight(180)
        self._min_val = 1
        self._max_val = 1
        self._low = 1
        self._high = 1
        self._cure_layers: List[int] = []
        self._dragging: Optional[str] = None
        self._is_light = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_light_mode(self, is_light: bool) -> None:
        self._is_light = is_light
        self.update()

    def configure(self, num_layers: int, cure_layers: Sequence[int]) -> None:
        self._min_val = 1
        self._max_val = max(1, int(num_layers))
        self._low = 1
        self._high = self._max_val
        self._cure_layers = sorted(set(cure_layers))
        self.update()

    def set_range(self, low: int, high: int, emit: bool = True) -> None:
        low = max(self._min_val, min(self._max_val, int(low)))
        high = max(low, min(self._max_val, int(high)))
        if low == self._low and high == self._high:
            return
        self._low = low
        self._high = high
        self.update()
        if emit:
            self.range_changed.emit(self._low, self._high)

    @property
    def low(self) -> int:
        return self._low

    @property
    def high(self) -> int:
        return self._high

    def _val_to_y(self, val: int) -> float:
        pad = 14.0
        span_px = max(1.0, self.height() - 2.0 * pad)
        if self._max_val <= self._min_val:
            return pad
        frac = (val - self._min_val) / float(self._max_val - self._min_val)
        return (self.height() - pad) - frac * span_px

    def _y_to_val(self, y: float) -> int:
        pad = 14.0
        span_px = max(1.0, self.height() - 2.0 * pad)
        frac = ((self.height() - pad) - y) / span_px
        frac = max(0.0, min(1.0, frac))
        val = int(round(self._min_val + frac * (self._max_val - self._min_val)))
        return max(self._min_val, min(self._max_val, val))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() * 0.5
        pad = 14.0
        track_w = 6.0
        track_rect = QRectF(cx - track_w * 0.5, pad, track_w, max(1.0, self.height() - 2.0 * pad))

        groove_col = QColor("#cfd6e2") if self._is_light else QColor("#2c303c")
        accent_col = QColor("#eab308") if self._is_light else QColor("#f5c518")
        cure_col = QColor("#0284c7") if self._is_light else QColor("#00d4ff")
        rim_col = QColor("#475569") if self._is_light else QColor("#141518")
        lo_col = QColor("#64748b") if self._is_light else QColor("#9aa2b1")

        # Background groove
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(groove_col)
        p.drawRoundedRect(track_rect, 3.0, 3.0)

        # Active yellow range
        y_hi = self._val_to_y(self._high)
        y_lo = self._val_to_y(self._low)
        active_rect = QRectF(cx - track_w * 0.5, y_hi, track_w, max(2.0, y_lo - y_hi))
        p.setBrush(accent_col)
        p.drawRoundedRect(active_rect, 3.0, 3.0)

        # Cyan ticks for every UV cure cycle layer
        pen_tick = QPen(cure_col, 1.6)
        p.setPen(pen_tick)
        for cl in self._cure_layers:
            yc = self._val_to_y(cl)
            p.drawLine(int(cx - 11), int(yc), int(cx - 5), int(yc))
            p.drawLine(int(cx + 5), int(yc), int(cx + 11), int(yc))

        # Lower handle
        p.setPen(QPen(rim_col, 1.4))
        p.setBrush(lo_col)
        p.drawRoundedRect(QRectF(cx - 9.0, y_lo - 5.0, 18.0, 10.0), 4.0, 4.0)

        # Upper handle (bright AlveoSlicer yellow)
        p.setPen(QPen(rim_col, 1.5))
        p.setBrush(accent_col)
        p.drawRoundedRect(QRectF(cx - 11.0, y_hi - 6.0, 22.0, 12.0), 4.0, 4.0)
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        y = event.position().y()
        d_hi = abs(y - self._val_to_y(self._high))
        d_lo = abs(y - self._val_to_y(self._low))
        if d_lo < d_hi and d_lo <= 16.0:
            self._dragging = "low"
        else:
            self._dragging = "high"
        self._apply_drag(y)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._apply_drag(event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        del event
        self._dragging = None

    def _apply_drag(self, y: float) -> None:
        val = self._y_to_val(y)
        if self._dragging == "low":
            self.set_range(min(val, self._high), self._high)
        else:
            self.set_range(self._low, max(val, self._low))

    def wheelEvent(self, event: QWheelEvent) -> None:
        step = 1 if event.angleDelta().y() > 0 else -1
        self.set_range(self._low, self._high + step)


class LayerRangeBar(QFrame):
    """Complete PrusaSlicer-style vertical layer inspector bar with Z readout & cure jump."""

    range_changed = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("floatingCard")
        self.setFixedWidth(96)

        self._layer_zs: List[float] = []
        self._cure_layers: List[int] = []

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 8, 6, 8)
        vbox.setSpacing(5)

        self.lbl_top = QLabel("L 0 / 0")
        self.lbl_top.setObjectName("accentValue")
        self.lbl_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.lbl_top)

        self.lbl_z = QLabel("Z 0.00 mm")
        self.lbl_z.setObjectName("mutedLabel")
        self.lbl_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.lbl_z)

        self.lbl_cure = QLabel("UV CURE")
        self.lbl_cure.setObjectName("modeBannerFFF")
        self.lbl_cure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cure.setFixedHeight(26)
        cure_sp = self.lbl_cure.sizePolicy()
        cure_sp.setRetainSizeWhenHidden(True)
        self.lbl_cure.setSizePolicy(cure_sp)
        self.lbl_cure.setVisible(False)
        vbox.addWidget(self.lbl_cure)

        step_row = QHBoxLayout()
        step_row.setSpacing(4)
        self.btn_up = QPushButton("+")
        self.btn_up.setObjectName("iconSmallBtn")
        self.btn_up.clicked.connect(lambda: self.step_top(1))
        self.btn_dn = QPushButton("-")
        self.btn_dn.setObjectName("iconSmallBtn")
        self.btn_dn.clicked.connect(lambda: self.step_top(-1))
        step_row.addWidget(self.btn_dn)
        step_row.addWidget(self.btn_up)
        vbox.addLayout(step_row)

        self.track = _DualVerticalTrack()
        self.track.range_changed.connect(self._on_track_changed)
        vbox.addWidget(self.track, stretch=1)

        self.lbl_bot = QLabel("Min: 1")
        self.lbl_bot.setObjectName("mutedLabel")
        self.lbl_bot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.lbl_bot)

        self.btn_next_cure = QPushButton("Next Cure")
        self.btn_next_cure.setObjectName("iconSmallBtn")
        self.btn_next_cure.setEnabled(False)
        self.btn_next_cure.clicked.connect(self.jump_next_cure)
        vbox.addWidget(self.btn_next_cure)

    def apply_theme(self, resolved_mode: str) -> None:
        self.track.set_light_mode(resolved_mode == "light")

    def configure(self, layer_zs: Sequence[float], cure_layers: Sequence[int]) -> None:
        self._layer_zs = list(layer_zs)
        self._cure_layers = sorted(set(cure_layers))
        n = len(self._layer_zs)
        self.track.configure(n, self._cure_layers)
        self.btn_next_cure.setEnabled(len(self._cure_layers) > 0)
        self._refresh_labels(self.track.low, self.track.high)

    def set_top_layer(self, layer_num: int) -> None:
        if not self._layer_zs:
            return
        hi = max(1, min(len(self._layer_zs), int(layer_num)))
        lo = min(self.track.low, hi)
        self.track.set_range(lo, hi, emit=True)

    def step_top(self, delta: int) -> None:
        self.set_top_layer(self.track.high + delta)

    def jump_next_cure(self) -> None:
        if not self._cure_layers:
            return
        cur = self.track.high
        for cl in self._cure_layers:
            if cl > cur:
                self.set_top_layer(cl)
                return
        self.set_top_layer(self._cure_layers[0])

    def _on_track_changed(self, low: int, high: int) -> None:
        self._refresh_labels(low, high)
        self.range_changed.emit(low, high)

    def _refresh_labels(self, low: int, high: int) -> None:
        n = len(self._layer_zs)
        self.lbl_top.setText(f"L {high} / {n}")
        z_val = self._layer_zs[high - 1] if (1 <= high <= n) else 0.0
        self.lbl_z.setText(f"Z {z_val:.2f} mm")
        self.lbl_bot.setText(f"Min: {low}")
        is_cure = high in self._cure_layers
        self.lbl_cure.setVisible(is_cure)
        if is_cure:
            self.lbl_cure.setText("UV CURE")
