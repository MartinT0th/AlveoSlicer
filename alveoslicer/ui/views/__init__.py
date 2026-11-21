"""Secondary inspection tabs: 2D basin viewer, DP cure schedule table, and G-code console."""

from .console_view import ConsoleGCodeView
from .inspector_2d import LayerInspector2D
from .schedule_view import CureScheduleView

__all__ = [
    "LayerInspector2D",
    "CureScheduleView",
    "ConsoleGCodeView",
]
