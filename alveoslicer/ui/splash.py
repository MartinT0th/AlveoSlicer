"""PyCharm / PrusaSlicer-style frameless startup splash screen with logo, version, and throbber.

Optimized for instant startup and smooth animation:
- Zero heavy dependencies (imports only ``math``, ``pathlib``, and PySide6).
- Pre-renders the static splash card (shadow, gradient, honeycomb watermark, logo,
  typography, and version badge) once onto a high-DPI ``QPixmap`` cache so every
  throbber frame paints in sub-millisecond time.
- Also provides :func:`render_boot_splash_png` so the PyInstaller bootloader can
  display the exact same visual card immediately while unpacking ``AlveoSlicer.exe``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .. import __version__
from ..assets import get_logo_path


def _draw_hex_watermark(painter: QPainter, card: QRectF, radius: float) -> None:
    """Render a subtle geometric honeycomb motif in the upper-right corner of ``card``."""
    painter.save()
    clip_path = QPainterPath()
    if radius > 0.0:
        clip_path.addRoundedRect(card, radius, radius)
    else:
        clip_path.addRect(card)
    painter.setClipPath(clip_path)

    painter.setPen(QPen(QColor(245, 197, 24, 20), 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)

    r = 26.0
    dx = r * 1.5
    dy = r * math.sqrt(3.0)
    ox = card.right() - 108.0
    oy = card.top() + 34.0

    for col in range(-1, 5):
        for row in range(-1, 4):
            cx = ox + col * dx
            cy = oy + row * dy + (0.5 * dy if col % 2 else 0.0)
            hex_path = QPainterPath()
            for k in range(6):
                ang = math.radians(60.0 * k)
                px = cx + r * math.cos(ang)
                py = cy + r * math.sin(ang)
                if k == 0:
                    hex_path.moveTo(px, py)
                else:
                    hex_path.lineTo(px, py)
            hex_path.closeSubpath()
            painter.drawPath(hex_path)
    painter.restore()


def _paint_static_card(
    painter: QPainter,
    card: QRectF,
    logo: QPixmap,
    version: str,
    *,
    rounded: bool = True,
    draw_shadow: bool = True,
) -> None:
    """Paint all static elements of the AlveoSlicer splash card onto ``painter``."""
    corner_r = 14.0 if rounded else 0.0

    if draw_shadow and rounded:
        for layer, alpha in ((10.0, 14), (6.0, 24), (3.0, 38)):
            s_rect = card.adjusted(
                -layer * 0.5, -layer * 0.25 + 2.0, layer * 0.5, layer * 0.75 + 2.0
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(s_rect, 16.0, 16.0)

    # Card body gradient
    bg_grad = QLinearGradient(card.topLeft(), card.bottomRight())
    bg_grad.setColorAt(0.0, QColor("#1c2028"))
    bg_grad.setColorAt(0.55, QColor("#15181f"))
    bg_grad.setColorAt(1.0, QColor("#101218"))
    painter.setBrush(bg_grad)
    painter.setPen(QPen(QColor("#2c3240"), 1.2))
    if rounded:
        painter.drawRoundedRect(card, corner_r, corner_r)
    else:
        painter.drawRect(card.adjusted(0.5, 0.5, -0.5, -0.5))

    # Subtle honeycomb watermark on right edge
    _draw_hex_watermark(painter, card, corner_r)

    # Warm gold accent top hairline
    top_grad = QLinearGradient(card.left() + 24.0, card.top(), card.right() - 24.0, card.top())
    top_grad.setColorAt(0.0, QColor(245, 197, 24, 0))
    top_grad.setColorAt(0.35, QColor(245, 197, 24, 210))
    top_grad.setColorAt(0.75, QColor(245, 197, 24, 140))
    top_grad.setColorAt(1.0, QColor(245, 197, 24, 0))
    painter.setPen(QPen(top_grad, 2.0))
    painter.drawLine(
        QPointF(card.left() + 22.0, card.top() + 1.0),
        QPointF(card.right() - 22.0, card.top() + 1.0),
    )

    # Soft golden radial glow behind logo
    logo_cx = card.left() + 104.0
    logo_cy = card.top() + 124.0
    glow = QRadialGradient(QPointF(logo_cx, logo_cy), 86.0)
    glow.setColorAt(0.0, QColor(245, 197, 24, 52))
    glow.setColorAt(0.55, QColor(245, 197, 24, 16))
    glow.setColorAt(1.0, QColor(245, 197, 24, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(logo_cx, logo_cy), 86.0, 86.0)

    # Crisp logo artwork
    if not logo.isNull():
        logo_size = 108
        scaled = logo.scaled(
            logo_size * 2,
            logo_size * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(2.0)
        painter.drawPixmap(int(logo_cx - logo_size / 2), int(logo_cy - logo_size / 2), scaled)

    # Title block ("AlveoSlicer" + subtitle + version pill)
    text_x = card.left() + 184.0
    title_font = QFont("Segoe UI Variable Display")
    if not title_font.exactMatch():
        title_font = QFont("Segoe UI")
    title_font.setPixelSize(36)
    title_font.setWeight(QFont.Weight.Bold)
    title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98.0)
    painter.setFont(title_font)

    fm_title = painter.fontMetrics()
    alveo_txt = "Alveo"
    slicer_txt = "Slicer"
    title_y = int(card.top() + 112.0)
    painter.setPen(QColor("#f4f6fa"))
    painter.drawText(int(text_x), title_y, alveo_txt)
    painter.setPen(QColor("#f5c518"))
    painter.drawText(int(text_x + fm_title.horizontalAdvance(alveo_txt)), title_y, slicer_txt)

    sub_font = QFont("Segoe UI")
    sub_font.setPixelSize(14)
    painter.setFont(sub_font)
    painter.setPen(QColor("#9aa4b4"))
    painter.drawText(int(text_x + 1.0), int(card.top() + 140.0), "Hybrid FFF + Photopolymer Slicer")

    # Version pill badge
    ver_label = f"Version {version}"
    ver_font = QFont("Segoe UI")
    ver_font.setPixelSize(12)
    ver_font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(ver_font)
    fm_ver = painter.fontMetrics()
    pill_w = fm_ver.horizontalAdvance(ver_label) + 22.0
    pill_h = 25.0
    pill_rect = QRectF(text_x + 1.0, card.top() + 158.0, pill_w, pill_h)
    painter.setBrush(QColor(245, 197, 24, 28))
    painter.setPen(QPen(QColor(245, 197, 24, 115), 1.0))
    painter.drawRoundedRect(pill_rect, 7.0, 7.0)
    painter.setPen(QColor("#f7d24b"))
    painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, ver_label)

    # Footer divider line
    footer_y = card.bottom() - 56.0
    painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
    painter.drawLine(
        QPointF(card.left() + 26.0, footer_y),
        QPointF(card.right() - 26.0, footer_y),
    )

    # Right-aligned edition / license hint
    meta_font = QFont("Segoe UI")
    meta_font.setPixelSize(11)
    painter.setFont(meta_font)
    painter.setPen(QColor("#697385"))
    meta_rect = QRectF(card.right() - 165.0, footer_y + 8.0, 139.0, 40.0)
    painter.drawText(
        meta_rect,
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        "Open Source • AGPL-3.0",
    )


class AlveoSplashScreen(QWidget):
    """Minimal, crisp frameless splash screen showing the AlveoSlicer logo, version, and throbber."""

    _CARD_W = 540
    _CARD_H = 296
    _SHADOW_PAD = 14

    def __init__(self, version: str = __version__, parent: Optional[QWidget] = None) -> None:
        """Initialize the frameless translucent splash card and start the throbber animation."""
        super().__init__(
            parent,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(
            self._CARD_W + 2 * self._SHADOW_PAD,
            self._CARD_H + 2 * self._SHADOW_PAD,
        )

        self._version = version
        self._status = "Starting AlveoSlicer..."
        self._angle_deg = 0.0

        self._status_font = QFont("Segoe UI")
        self._status_font.setPixelSize(12)

        logo_path = get_logo_path()
        logo = QPixmap(str(logo_path)) if logo_path is not None else QPixmap()
        self._static_card = self._build_static_cache(logo)

        self._timer = QTimer(self)
        self._timer.setInterval(20)  # ~50 FPS smooth throbber
        self._timer.timeout.connect(self._tick_throbber)
        self._timer.start()

        self._center_on_screen()

    def _build_static_cache(self, logo: QPixmap) -> QPixmap:
        """Pre-render static splash artwork onto a 2x high-DPI ``QPixmap`` once."""
        dpr = 2.0
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pad = float(self._SHADOW_PAD)
        card = QRectF(pad, pad, float(self._CARD_W), float(self._CARD_H))
        _paint_static_card(painter, card, logo, self._version, rounded=True, draw_shadow=True)
        painter.end()
        return pm

    def _center_on_screen(self) -> None:
        """Center the splash card on the primary display's available geometry."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def _tick_throbber(self) -> None:
        """Advance the rotating throbber angle and repaint only the dynamic footer strip."""
        self._angle_deg = (self._angle_deg + 8.0) % 360.0
        self.update()

    def set_status(self, message: str) -> None:
        """Update the concise loading status message displayed next to the throbber."""
        self._status = message
        self.update()

    def finish(self, main_window: Optional[QWidget] = None) -> None:
        """Stop the throbber timer, close the splash screen, and raise ``main_window``."""
        self._timer.stop()
        if main_window is not None:
            main_window.raise_()
            main_window.activateWindow()
        self.close()

    def paintEvent(self, _event) -> None:  # noqa: N802
        """Blit the cached splash card and paint the animated throbber + status line."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPixmap(0, 0, self._static_card)

        pad = float(self._SHADOW_PAD)
        card_left = pad
        footer_y = pad + float(self._CARD_H) - 56.0

        # Animated circular throbber (NO loading bar)
        spin_cx = card_left + 40.0
        spin_cy = footer_y + 28.0
        spin_r = 9.0
        spin_rect = QRectF(spin_cx - spin_r, spin_cy - spin_r, 2.0 * spin_r, 2.0 * spin_r)

        painter.setPen(QPen(QColor(255, 255, 255, 26), 2.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(spin_rect)

        arc_pen = QPen(QColor("#f5c518"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        start_16 = int((90.0 - self._angle_deg) * 16.0)
        painter.drawArc(spin_rect, start_16, -105 * 16)

        # Concise loading status text next to throbber
        painter.setFont(self._status_font)
        painter.setPen(QColor("#b8c1cf"))
        status_rect = QRectF(spin_cx + 20.0, footer_y + 8.0, 310.0, 40.0)
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._status,
        )
        painter.end()


def render_boot_splash_png(out_path: Path, version: str = __version__) -> Path:
    """Render an opaque ``540x296`` boot splash PNG matching :class:`AlveoSplashScreen`.

    Used by ``packaging/build_exe.py`` for PyInstaller's ``--splash`` bootloader
    option so the AlveoSlicer splash appears immediately while the single-file
    executable unpacks, before handing off seamlessly to the live Qt throbber.
    """
    w, h = AlveoSplashScreen._CARD_W, AlveoSplashScreen._CARD_H
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#15181f"))

    logo_path = get_logo_path()
    logo = QPixmap(str(logo_path)) if logo_path is not None else QPixmap()

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    card = QRectF(0.0, 0.0, float(w), float(h))
    _paint_static_card(painter, card, logo, version, rounded=False, draw_shadow=False)

    # Static initial throbber ring + boot status text matching the first Qt splash frame
    footer_y = card.bottom() - 56.0
    spin_cx = 40.0
    spin_cy = footer_y + 28.0
    spin_rect = QRectF(spin_cx - 9.0, spin_cy - 9.0, 18.0, 18.0)
    painter.setPen(QPen(QColor(255, 255, 255, 26), 2.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(spin_rect)
    painter.setPen(QPen(QColor("#f5c518"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(spin_rect, 90 * 16, -105 * 16)

    status_font = QFont("Segoe UI")
    status_font.setPixelSize(12)
    painter.setFont(status_font)
    painter.setPen(QColor("#b8c1cf"))
    painter.drawText(
        QRectF(spin_cx + 20.0, footer_y + 8.0, 310.0, 40.0),
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        "Starting AlveoSlicer...",
    )
    painter.end()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG")
    return out_path
