"""Stage 4-5 Hybrid Photopolymer Physics: Global DP cure-cycle scheduling and 3D fluid basin dosing."""

from .dosing import DosingPoint, order_points_tsp, plan_cycle_dosing
from .scheduler import CureCycle, compute_optimal_cure_schedule

__all__ = [
    "CureCycle",
    "compute_optimal_cure_schedule",
    "DosingPoint",
    "order_points_tsp",
    "plan_cycle_dosing",
]
