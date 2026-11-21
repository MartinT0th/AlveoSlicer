"""Procedural high-DPI vector icon library rendered via ``QPainter``.

Draws crisp, resolution-independent CAD tool, camera, theme, and settings
category icons normalized to a 24x24 virtual grid and cached by
``(name, color, accent, size)`` so switching Dark/Light themes re-tints instantly.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_ICON_CACHE: Dict[Tuple[str, str, str, int], QIcon] = {}


def clear_icon_cache() -> None:
    """Clear all cached vector icons (called if palette or DPI resets)."""
    _ICON_CACHE.clear()


def make_icon(
    name: str,
    color: str = "#eceff4",
    accent: str = "#f5c518",
    size: int = 20,
) -> QIcon:
    """Render and cache a 2x high-DPI ``QIcon`` for ``name`` using ``color`` and ``accent``."""
    key = (name, color, accent, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    fg, ac = QColor(color), QColor(accent)
    cap, join = Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
    pen = QPen(fg, 1.75, Qt.PenStyle.SolidLine, cap, join)
    pen_ac = QPen(ac, 1.85, Qt.PenStyle.SolidLine, cap, join)
    pen_thin = QPen(fg, 1.35, Qt.PenStyle.SolidLine, cap, join)

    p.scale(size / 24.0, size / 24.0)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def _arrow(tip: QPointF, dx: float, dy: float, sz: float = 3.2) -> None:
        ang = math.atan2(dy, dx)
        for sign in (1, -1):
            a = ang + sign * math.radians(145)
            p.drawLine(tip, QPointF(tip.x() + sz * math.cos(a), tip.y() + sz * math.sin(a)))

    # ---- Viewport 3D Editing Tools ----
    if name == "select":
        p.drawPolygon(QPolygonF([
            QPointF(5.5, 3.5), QPointF(5.5, 18.5), QPointF(9.5, 14.8),
            QPointF(12.6, 20.5), QPointF(15.0, 19.3), QPointF(12.0, 13.7), QPointF(17.5, 13.5),
        ]))
    elif name == "move":
        p.drawLine(QPointF(12, 3), QPointF(12, 21))
        p.drawLine(QPointF(3, 12), QPointF(21, 12))
        for tip, dx, dy in ((QPointF(12, 3), 0, -1), (QPointF(12, 21), 0, 1), (QPointF(3, 12), -1, 0), (QPointF(21, 12), 1, 0)):
            _arrow(tip, dx, dy, 3.0)
    elif name == "rotate":
        p.drawArc(QRectF(4.2, 4.2, 15.6, 15.6), 35 * 16, 285 * 16)
        _arrow(QPointF(18.5, 7.6), 0.3, 1.0, 3.6)
        p.setPen(pen_ac); p.setBrush(QBrush(ac))
        p.drawEllipse(QPointF(12, 12), 2.1, 2.1)
    elif name == "scale":
        p.drawRoundedRect(QRectF(4.0, 11.5, 8.5, 8.5), 1.5, 1.5)
        p.setPen(pen_thin); p.drawRoundedRect(QRectF(4.0, 4.0, 16.0, 16.0), 1.8, 1.8)
        p.setPen(pen_ac); p.drawLine(QPointF(12.0, 12.0), QPointF(19.0, 5.0))
        _arrow(QPointF(19.2, 4.8), 1, -1, 3.4)
    elif name == "place_on_face":
        p.setPen(pen_ac); p.drawLine(QPointF(3.0, 19.5), QPointF(21.0, 19.5))
        p.setPen(pen); p.drawRoundedRect(QRectF(6.0, 10.5, 12.0, 8.8), 1.4, 1.4)
        p.drawLine(QPointF(12.0, 3.2), QPointF(12.0, 8.6))
        _arrow(QPointF(12.0, 8.8), 0, 1, 3.0)
    elif name == "cut":
        p.drawRoundedRect(QRectF(6.0, 3.8, 12.0, 6.2), 1.2, 1.2)
        p.drawRoundedRect(QRectF(6.0, 14.0, 12.0, 6.2), 1.2, 1.2)
        p.setPen(pen_ac); p.drawLine(QPointF(2.5, 12.0), QPointF(21.5, 12.0))
        p.drawLine(QPointF(19.0, 9.6), QPointF(21.5, 12.0))
        p.drawLine(QPointF(19.0, 14.4), QPointF(21.5, 12.0))
    elif name == "paint_supports":
        p.drawLine(QPointF(19.5, 4.5), QPointF(10.5, 13.5))
        path = QPainterPath()
        path.moveTo(10.5, 13.5)
        path.quadTo(6.2, 13.8, 4.5, 19.2)
        path.quadTo(9.8, 18.0, 11.8, 14.8)
        path.closeSubpath()
        p.setPen(pen_ac); p.setBrush(QBrush(ac)); p.drawPath(path)
        p.setPen(pen_thin); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(14.5, 17.0), QPointF(14.5, 20.5))
        p.drawLine(QPointF(18.0, 15.0), QPointF(18.0, 20.5))

    # ---- Viewport Quick Actions ----
    elif name == "add_model":
        p.drawRoundedRect(QRectF(3.5, 5.5, 13.5, 13.5), 2.0, 2.0)
        p.setPen(pen_ac)
        p.drawLine(QPointF(17.5, 4.5), QPointF(17.5, 11.5))
        p.drawLine(QPointF(14.0, 8.0), QPointF(21.0, 8.0))
    elif name == "sphere":
        p.drawEllipse(QRectF(4.0, 4.0, 16.0, 16.0))
        p.setPen(pen_thin)
        p.drawEllipse(QRectF(4.0, 9.0, 16.0, 6.0))
        p.drawEllipse(QRectF(9.0, 4.0, 6.0, 16.0))
    elif name == "wall":
        path = QPainterPath()
        path.moveTo(4.0, 17.5); path.cubicTo(8.5, 7.0, 15.5, 7.0, 20.0, 17.5)
        path.lineTo(20.0, 12.0); path.cubicTo(15.5, 3.0, 8.5, 3.0, 4.0, 12.0)
        path.closeSubpath(); p.drawPath(path)
    elif name == "clone":
        p.drawRoundedRect(QRectF(4.0, 8.0, 10.5, 11.5), 1.8, 1.8)
        p.setPen(pen_ac); p.drawRoundedRect(QRectF(9.5, 4.5, 10.5, 11.5), 1.8, 1.8)
    elif name == "move_plate":
        p.drawRoundedRect(QRectF(3.5, 6.0, 8.0, 12.0), 1.6, 1.6)
        p.drawRoundedRect(QRectF(14.0, 6.0, 6.5, 12.0), 1.6, 1.6)
        p.setPen(pen_ac)
        p.drawLine(QPointF(8.5, 12.0), QPointF(16.8, 12.0))
        _arrow(QPointF(17.0, 12.0), 1, 0, 3.0)
    elif name == "arrange":
        for rx, ry in ((4.0, 4.0), (13.2, 4.0), (4.0, 13.2), (13.2, 13.2)):
            p.drawRoundedRect(QRectF(rx, ry, 6.8, 6.8), 1.5, 1.5)
    elif name == "center":
        p.drawEllipse(QRectF(6.5, 6.5, 11.0, 11.0))
        for a, b in ((QPointF(12, 3), QPointF(12, 7.5)), (QPointF(12, 16.5), QPointF(12, 21)),
                     (QPointF(3, 12), QPointF(7.5, 12)), (QPointF(16.5, 12), QPointF(21, 12))):
            p.drawLine(a, b)
        p.setPen(pen_ac); p.setBrush(QBrush(ac)); p.drawEllipse(QPointF(12.0, 12.0), 1.8, 1.8)
    elif name == "drop":
        p.drawLine(QPointF(12.0, 3.5), QPointF(12.0, 15.5))
        _arrow(QPointF(12.0, 15.8), 0, 1, 3.8)
        p.setPen(pen_ac); p.drawLine(QPointF(4.0, 19.8), QPointF(20.0, 19.8))
    elif name == "delete":
        p.drawLine(QPointF(4.5, 6.5), QPointF(19.5, 6.5))
        p.drawLine(QPointF(9.5, 4.0), QPointF(14.5, 4.0))
        p.drawRoundedRect(QRectF(6.5, 6.5, 11.0, 13.5), 1.6, 1.6)
        p.setPen(pen_thin)
        p.drawLine(QPointF(10.0, 10.0), QPointF(10.0, 17.0))
        p.drawLine(QPointF(14.0, 10.0), QPointF(14.0, 17.0))

    # ---- Camera Presets ----
    elif name in ("cam_iso", "view_3d"):
        p.drawPolygon(QPolygonF([QPointF(12, 3.5), QPointF(19.5, 7.8), QPointF(12, 12.0), QPointF(4.5, 7.8)]))
        p.drawLine(QPointF(4.5, 7.8), QPointF(4.5, 16.2))
        p.drawLine(QPointF(19.5, 7.8), QPointF(19.5, 16.2))
        if name == "cam_iso":
            p.drawLine(QPointF(12.0, 12.0), QPointF(12.0, 20.5))
        p.drawLine(QPointF(4.5, 16.2), QPointF(12.0, 20.5))
        p.drawLine(QPointF(19.5, 16.2), QPointF(12.0, 20.5))
    elif name == "cam_top":
        p.drawRoundedRect(QRectF(5.0, 5.0, 14.0, 14.0), 2.0, 2.0)
        p.setPen(pen_ac); p.setBrush(QBrush(ac)); p.drawRoundedRect(QRectF(8.5, 8.5, 7.0, 7.0), 1.0, 1.0)
    elif name == "cam_bottom":
        p.drawRoundedRect(QRectF(5.0, 5.0, 14.0, 14.0), 2.0, 2.0)
        p.setPen(pen_ac); p.drawLine(QPointF(12.0, 16.5), QPointF(12.0, 7.5))
        _arrow(QPointF(12.0, 7.2), 0, -1, 3.2)
    elif name == "cam_front":
        p.drawRoundedRect(QRectF(5.0, 6.0, 14.0, 12.0), 2.0, 2.0)
        p.setPen(pen_ac); p.drawLine(QPointF(7.5, 18.0), QPointF(16.5, 18.0))
    elif name == "cam_right":
        p.drawRoundedRect(QRectF(5.0, 6.0, 14.0, 12.0), 2.0, 2.0)
        p.setPen(pen_ac); p.drawLine(QPointF(19.0, 8.0), QPointF(19.0, 16.0))
    elif name == "cam_fit":
        for cx, cy, sx, sy in ((4.5, 4.5, 1, 1), (19.5, 4.5, -1, 1), (4.5, 19.5, 1, -1), (19.5, 19.5, -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + 4.5 * sx, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + 4.5 * sy))
        p.setPen(pen_ac); p.drawEllipse(QPointF(12.0, 12.0), 2.2, 2.2)

    # ---- Theme Switcher ----
    elif name == "theme_dark":
        path, cut = QPainterPath(), QPainterPath()
        path.addEllipse(QRectF(5.0, 4.5, 14.0, 14.5))
        cut.addEllipse(QRectF(10.0, 3.0, 12.5, 12.5))
        p.setBrush(QBrush(fg)); p.drawPath(path.subtracted(cut))
    elif name == "theme_light":
        p.setPen(pen_ac); p.setBrush(QBrush(ac)); p.drawEllipse(QPointF(12.0, 12.0), 4.0, 4.0)
        for k in range(8):
            a = k * math.pi / 4.0
            p.drawLine(QPointF(12 + 6.4 * math.cos(a), 12 + 6.4 * math.sin(a)),
                       QPointF(12 + 9.2 * math.cos(a), 12 + 9.2 * math.sin(a)))
    elif name == "theme_system":
        p.drawEllipse(QRectF(4.5, 4.5, 15.0, 15.0))
        path = QPainterPath(); path.moveTo(12.0, 4.5)
        path.arcTo(QRectF(4.5, 4.5, 15.0, 15.0), 90, 180); path.closeSubpath()
        p.setBrush(QBrush(fg)); p.drawPath(path)

    # ---- View Modes, Playback & General UI ----
    elif name == "view_gcode":
        for y in (6.5, 12.0, 17.5):
            p.drawLine(QPointF(4.0, y), QPointF(20.0, y))
        p.setPen(pen_ac); p.drawLine(QPointF(15.0, 4.0), QPointF(15.0, 9.5))
    elif name == "view_2d":
        p.drawRoundedRect(QRectF(4.0, 4.0, 16.0, 16.0), 2.0, 2.0)
        p.setPen(pen_ac); p.drawEllipse(QPointF(10.0, 11.0), 3.0, 3.0)
        p.drawEllipse(QPointF(15.5, 15.0), 2.0, 2.0)
    elif name == "play":
        p.setBrush(QBrush(fg)); p.drawPolygon(QPolygonF([QPointF(7.0, 5.0), QPointF(19.0, 12.0), QPointF(7.0, 19.0)]))
    elif name == "pause":
        p.setBrush(QBrush(fg))
        p.drawRoundedRect(QRectF(6.0, 5.0, 4.0, 14.0), 1.0, 1.0)
        p.drawRoundedRect(QRectF(14.0, 5.0, 4.0, 14.0), 1.0, 1.0)
    elif name == "legend":
        for y in (6.5, 12.0, 17.5):
            p.setPen(pen_ac); p.setBrush(QBrush(ac)); p.drawRoundedRect(QRectF(4.0, y - 1.5, 3.2, 3.2), 0.8, 0.8)
            p.setPen(pen); p.drawLine(QPointF(9.5, y), QPointF(20.0, y))
    elif name == "slice":
        for dy in (0.0, 5.0, 10.0):
            p.drawPolygon(QPolygonF([QPointF(12, 3.5 + dy), QPointF(20, 7.2 + dy), QPointF(12, 10.8 + dy), QPointF(4, 7.2 + dy)]))
    elif name == "export":
        p.drawRoundedRect(QRectF(4.5, 13.5, 15.0, 6.5), 1.6, 1.6)
        p.drawLine(QPointF(12.0, 14.5), QPointF(12.0, 4.0))
        _arrow(QPointF(12.0, 3.8), 0, -1, 3.8)
    elif name == "plus":
        p.drawLine(QPointF(12.0, 5.0), QPointF(12.0, 19.0))
        p.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))
    elif name == "minus":
        p.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))
    elif name == "settings":
        p.drawEllipse(QPointF(12.0, 12.0), 4.0, 4.0)
        for k in range(6):
            a = k * math.pi / 3.0
            p.drawLine(QPointF(12 + 5.8 * math.cos(a), 12 + 5.8 * math.sin(a)),
                       QPointF(12 + 8.8 * math.cos(a), 12 + 8.8 * math.sin(a)))
    elif name == "save":
        p.drawRoundedRect(QRectF(4.5, 4.5, 15.0, 15.0), 1.8, 1.8)
        p.drawRect(QRectF(8.0, 4.5, 8.0, 5.5))
        p.setPen(pen_ac); p.drawRect(QRectF(7.5, 13.0, 9.0, 6.5))
    elif name == "revert":
        p.drawArc(QRectF(5.0, 5.0, 14.0, 14.0), 40 * 16, 260 * 16)
        _arrow(QPointF(6.2, 8.0), -0.6, 0.8, 3.4)

    # ---- Settings Section Icons ----
    elif name == "layers":
        for y in (6.5, 12.0, 17.5):
            p.drawLine(QPointF(4.0, y), QPointF(20.0, y))
    elif name == "infill":
        pts = [QPointF(12 + 7.5 * math.cos(math.radians(60 * k - 30)), 12 + 7.5 * math.sin(math.radians(60 * k - 30))) for k in range(6)]
        p.drawPolygon(QPolygonF(pts))
        p.setPen(pen_ac); p.drawEllipse(QPointF(12.0, 12.0), 2.2, 2.2)
    elif name == "skirt":
        p.drawRoundedRect(QRectF(3.5, 5.5, 17.0, 13.0), 5.0, 5.0)
        p.setPen(pen_ac); p.drawRoundedRect(QRectF(7.5, 9.0, 9.0, 6.0), 2.0, 2.0)
    elif name == "support":
        p.drawLine(QPointF(12.0, 20.0), QPointF(12.0, 11.5))
        p.drawLine(QPointF(12.0, 12.5), QPointF(6.5, 6.5))
        p.drawLine(QPointF(12.0, 12.5), QPointF(17.5, 6.5))
        p.setPen(pen_ac); p.drawLine(QPointF(4.5, 5.5), QPointF(19.5, 5.5))
    elif name == "speed":
        p.drawArc(QRectF(4.0, 5.5, 16.0, 16.0), 0, 180 * 16)
        p.setPen(pen_ac); p.drawLine(QPointF(12.0, 14.0), QPointF(17.5, 8.5))
        p.setBrush(QBrush(ac)); p.drawEllipse(QPointF(12.0, 14.0), 1.8, 1.8)
    elif name == "tower":
        p.drawRoundedRect(QRectF(7.5, 4.0, 9.0, 16.0), 1.4, 1.4)
        p.setPen(pen_thin)
        for y in (8.5, 12.5, 16.5):
            p.drawLine(QPointF(7.5, y), QPointF(16.5, y))
    elif name == "filament":
        p.drawEllipse(QRectF(4.5, 4.5, 15.0, 15.0))
        p.drawEllipse(QRectF(9.0, 9.0, 6.0, 6.0))
        p.setPen(pen_ac); p.drawLine(QPointF(17.5, 17.0), QPointF(21.0, 20.5))
    elif name == "cooling":
        for k in range(3):
            a = k * math.pi / 3.0
            p.drawLine(QPointF(12 - 7.5 * math.cos(a), 12 - 7.5 * math.sin(a)),
                       QPointF(12 + 7.5 * math.cos(a), 12 + 7.5 * math.sin(a)))
    elif name == "resin":
        path = QPainterPath()
        path.moveTo(12.0, 3.8)
        path.cubicTo(18.0, 11.5, 18.5, 19.5, 12.0, 19.5)
        path.cubicTo(5.5, 19.5, 6.0, 11.5, 12.0, 3.8)
        p.setPen(pen_ac); p.drawPath(path)
    elif name == "printer":
        p.drawRoundedRect(QRectF(4.0, 4.0, 16.0, 16.0), 2.0, 2.0)
        p.drawLine(QPointF(4.0, 9.5), QPointF(20.0, 9.5))
        p.setPen(pen_ac); p.drawLine(QPointF(12.0, 9.5), QPointF(12.0, 14.0))
        p.drawLine(QPointF(6.5, 17.5), QPointF(17.5, 17.5))
    elif name == "gcode":
        p.drawLine(QPointF(8.5, 7.0), QPointF(4.0, 12.0)); p.drawLine(QPointF(4.0, 12.0), QPointF(8.5, 17.0))
        p.drawLine(QPointF(15.5, 7.0), QPointF(20.0, 12.0)); p.drawLine(QPointF(20.0, 12.0), QPointF(15.5, 17.0))
        p.setPen(pen_ac); p.drawLine(QPointF(13.5, 6.0), QPointF(10.5, 18.0))
    elif name == "uv":
        p.setPen(pen_ac)
        p.drawPolygon(QPolygonF([
            QPointF(13.5, 3.5), QPointF(6.5, 13.0), QPointF(11.8, 13.0),
            QPointF(10.5, 20.5), QPointF(17.5, 11.0), QPointF(12.2, 11.0),
        ]))
    else:
        p.drawEllipse(QRectF(6.0, 6.0, 12.0, 12.0))

    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon
