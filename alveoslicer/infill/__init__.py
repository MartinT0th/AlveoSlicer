"""Stage 2-3 Infill Package: 3D Surface-Normal Cellular Formwork & Standard PrusaSlicer Infill engines."""

from .lattice import Capsule, CellHalfPlane, NormalVoronoiLattice, clip_voronoi_cell_2d
from .standard import (
    generate_standard_infill_layer,
    hatch_polygon,
    hilbert_curve_points,
    honeycomb_hatch_polygon,
)
from .surface_normal import generate_cellular_infill_for_mesh, generate_normal_dendritic_infill

__all__ = [
    "Capsule",
    "CellHalfPlane",
    "NormalVoronoiLattice",
    "clip_voronoi_cell_2d",
    "generate_cellular_infill_for_mesh",
    "generate_normal_dendritic_infill",
    "hatch_polygon",
    "honeycomb_hatch_polygon",
    "hilbert_curve_points",
    "generate_standard_infill_layer",
]
