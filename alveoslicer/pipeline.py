"""End-to-end 6-stage AlveoSlicer pipeline orchestrator shared by the CLI and desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

import numpy as np
import trimesh

from .config import SlicerConfig
from .dosing import DosingPoint, plan_cycle_dosing
from .geometry import VolumetricField
from .hal import create_hal
from .infill import Capsule, generate_normal_dendritic_infill
from .scheduler import CureCycle, compute_optimal_cure_schedule
from .slicing import SlicedLayer, slice_model_to_layers
from .toolpath import generate_fff_paths_for_layer

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


@dataclass
class SliceResult:
    """Complete output of an AlveoSlicer run, ready for export or interactive UI inspection."""

    mesh: trimesh.Trimesh
    capsules: List[Capsule]
    layers: List[SlicedLayer]
    xs: np.ndarray
    ys: np.ndarray
    schedule: List[CureCycle]
    cycle_by_layer: Dict[int, CureCycle]
    dosing_by_layer: Dict[int, List[DosingPoint]]
    gcode_text: str
    total_resin_ml: float
    gcode_line_count: int
    total_filament_mm: float = 0.0
    est_print_time_s: float = 0.0


def slice_mesh(
    mesh: trimesh.Trimesh,
    cfg: SlicerConfig,
    on_progress: Optional[ProgressCallback] = None,
    on_log: Optional[LogCallback] = None,
) -> SliceResult:
    """Execute all 6 slicing stages on ``mesh`` and return a complete ``SliceResult``."""

    def _progress(pct: int, msg: str) -> None:
        if on_progress is not None:
            on_progress(pct, msg)

    def _log(msg: str) -> None:
        if on_log is not None:
            on_log(msg)

    _progress(5, "Stage 1: Building lightweight 3D surface field...")
    _log(f"[Stage 1] Building lightweight surface field (pitch={cfg.voxel_pitch_mm} mm)...")
    field = VolumetricField(mesh, pitch=cfg.voxel_pitch_mm)

    _progress(22, "Stage 2-3: Synthesizing 3D surface-normal cellular infill...")
    _log(
        f"[Stage 2-3] Synthesizing 3D outer-surface {cfg.infill.pattern} pattern "
        f"(mode={cfg.infill.mode}, pitch={cfg.infill.target_cell_pitch} mm, "
        f"resin_channel={cfg.infill.min_channel_clearance} mm, "
        f"flatten_angle=<{cfg.infill.flatten_angle_deg} deg)..."
    )
    lattice = generate_normal_dendritic_infill(field, cfg)
    _log(f"            Placed {len(lattice.seeds)} cells ({len(lattice)} 3D wireframe edges).")

    _progress(42, "Stage 3: Exact analytical vector XY slicing...")
    _log(
        f"[Stage 3] Exact vector XY slicing (h={cfg.fff.layer_height} mm, "
        f"raster={cfg.slice_raster_pitch_mm} mm)..."
    )
    layers, xs, ys = slice_model_to_layers(mesh, field, lattice, cfg)
    _log(f"          Sliced {len(layers)} exact vector layers.")

    if cfg.hybrid.is_fff_only:
        _progress(65, "Stage 4: Standard FFF mode (resin & UV disabled)...")
        _log("[Stage 4] Standard FFF-only mode active: skipping photopolymer dosing & UV cure cycles.")
        schedule: List[CureCycle] = []
        cycle_by_layer: Dict[int, CureCycle] = {}
    else:
        _progress(65, "Stage 4: Solving Global Dynamic Programming cure schedule...")
        _log(
            f"[Stage 4] Solving global DP cure schedule "
            f"(window=[{cfg.resin.x_min_layers}, {cfg.resin.x_max_layers}] layers)..."
        )
        schedule = compute_optimal_cure_schedule(layers, cfg)
        cycle_by_layer = {c.end_layer: c for c in schedule}
        _log(f"          Scheduled {len(schedule)} inject + UV cure cycles.")

    _progress(75, "Stage 5-6: Generating FFF toolpaths & G-code...")
    _log("[Stage 5-6] Generating exact vector FFF toolpaths & G-code...")
    hal = create_hal(cfg)
    hal.start_gcode()

    dosing_by_layer: Dict[int, List[DosingPoint]] = {}
    total_resin_mm3 = 0.0
    n_layers = max(1, len(layers))
    progress_stride = max(1, n_layers // 15)

    for idx, ly in enumerate(layers):
        fff_paths = generate_fff_paths_for_layer(ly, xs, ys, cfg)
        hal.emit_fff_layer(ly.layer_idx, ly.z, fff_paths)

        if (not cfg.hybrid.is_fff_only) and (ly.layer_idx in cycle_by_layer):
            cycle = cycle_by_layer[ly.layer_idx]
            dosing_pts, bbox_xy = plan_cycle_dosing(layers, cycle, xs, ys, cfg)
            dosing_by_layer[ly.layer_idx] = dosing_pts
            if dosing_pts or cfg.hybrid.enable_uv_curing:
                if cfg.hybrid.enable_resin_pumping:
                    total_resin_mm3 += sum(d.volume_mm3 for d in dosing_pts)
                hal.emit_resin_and_uv_cycle(cycle, ly.z, dosing_pts, bbox_xy)

        if idx % progress_stride == 0:
            pct = 75 + int(23 * (idx + 1) / n_layers)
            _progress(pct, f"Stage 5-6: Layer {ly.layer_idx}/{n_layers}...")

    hal.end_gcode()
    gcode_text = "\n".join(hal.lines) + "\n"
    total_ml = total_resin_mm3 / 1000.0

    _log(
        f"[Done] Generated {len(hal.lines)} G-code lines | "
        f"Total photopolymer: {total_ml:.2f} mL across {len(schedule)} cure cycles."
    )
    _progress(100, "Slicing complete.")

    return SliceResult(
        mesh=mesh,
        capsules=lattice,
        layers=layers,
        xs=xs,
        ys=ys,
        schedule=schedule,
        cycle_by_layer=cycle_by_layer,
        dosing_by_layer=dosing_by_layer,
        gcode_text=gcode_text,
        total_resin_ml=total_ml,
        gcode_line_count=len(hal.lines),
        total_filament_mm=hal.total_e_fff_mm,
        est_print_time_s=hal.est_time_s,
    )


def run_slicer(mesh: trimesh.Trimesh, output_gcode: Union[str, Path], cfg: SlicerConfig) -> SliceResult:
    """Run the full 6-stage pipeline, print progress logs, and write the output G-code file."""
    out_path = Path(output_gcode)
    result = slice_mesh(mesh, cfg, on_log=print)
    out_path.write_text(result.gcode_text, encoding="utf-8")
    print(
        f"[Export] Wrote {result.gcode_line_count} G-code lines to '{out_path}' "
        f"(total photopolymer: {result.total_resin_ml:.2f} mL across {len(result.schedule)} cures)."
    )
    return result
