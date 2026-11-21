"""3D OpenGL Viewport subpackage: shaders, raycast math, geometry builders, and ``AlveoViewport3D``."""

from .builders import (
    build_bed_geometry,
    build_brush_reticle,
    build_cut_plane_geometry,
    build_extrusion_beads,
    build_filled_resin_cycles,
    build_gizmo_geometry,
    build_nozzle_cone,
    mesh_to_lit_buffer,
    triangulate_resin_layer_slab,
)
from .math_utils import (
    euler_matrix_xyz,
    plate_grid_offset,
    ray_intersects_aabb,
    ray_segment_dist,
    ray_triangles_hit,
    ray_triangles_hit_full,
)
from .widget import AlveoViewport3D

# Compatibility alias for tests importing the builder directly
_build_extrusion_beads = build_extrusion_beads

__all__ = [
    "AlveoViewport3D",
    "build_extrusion_beads",
    "_build_extrusion_beads",
    "build_filled_resin_cycles",
    "triangulate_resin_layer_slab",
    "build_bed_geometry",
    "build_gizmo_geometry",
    "build_cut_plane_geometry",
    "build_brush_reticle",
    "build_nozzle_cone",
    "mesh_to_lit_buffer",
    "euler_matrix_xyz",
    "plate_grid_offset",
    "ray_intersects_aabb",
    "ray_triangles_hit",
    "ray_triangles_hit_full",
    "ray_segment_dist",
]
