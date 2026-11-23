"""Command-line interface for headless AlveoSlicer execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from .core.config import (
    CELL_PATTERNS,
    INFILL_ENGINES,
    PRINTER_CAPABILITIES,
    SLICING_GENERATORS,
    STANDARD_INFILL_PATTERNS,
    SlicerConfig,
)
from .core.geometry import create_test_curved_wall_mesh, create_test_sphere_mesh, load_and_center_mesh
from .pipeline import run_slicer


def build_parser() -> argparse.ArgumentParser:
    """Create and configure the AlveoSlicer CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="alveoslicer",
        description="AlveoSlicer - Standalone Hybrid FFF + In-Situ UV Photopolymer Slicer (Exact Vector Engine)",
    )
    parser.add_argument("input", nargs="?", type=Path, help="Input 3D model (STL, OBJ, 3MF, STEP)")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.gcode"), help="Output G-code path")
    parser.add_argument("-c", "--config", type=Path, help="Load slicer/printer settings from JSON")
    parser.add_argument("--dump-default-config", type=Path, help="Write default JSON config and exit")
    parser.add_argument("--printer-type", choices=["bedslinger_prototype", "corexy_toolchanger"])
    parser.add_argument("--printer-capability", choices=list(PRINTER_CAPABILITIES), help="Hybrid vs FFF-only capability")
    parser.add_argument("--infill-engine", choices=list(INFILL_ENGINES), help="Infill generator engine")
    parser.add_argument("--generator", choices=list(SLICING_GENERATORS), help="Cellular infill generator")
    parser.add_argument("--infill-mode", choices=["hollow_shell", "full_to_medial"])
    parser.add_argument("--pattern", choices=list(CELL_PATTERNS), help="3D surface-normal cellular pattern")
    parser.add_argument("--standard-pattern", choices=list(STANDARD_INFILL_PATTERNS), help="Standard 2D/3D infill pattern")
    parser.add_argument("--flatten-angle", type=float, help="Flatten faces tilted < angle (deg) to horizontal")
    parser.add_argument(
        "--fff-only",
        action="store_true",
        help="Disable resin pumping and UV curing for standard FFF printers",
    )
    parser.add_argument("--test-sphere", action="store_true", help="Slice a built-in 30 mm sphere")
    parser.add_argument("--test-wall", action="store_true", help="Slice a built-in 60x25x5 mm curved wavy wall")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse CLI arguments, load the mesh and config, and execute the slicing pipeline."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dump_default_config:
        SlicerConfig().to_json(args.dump_default_config)
        print(f"Wrote default configuration to '{args.dump_default_config}'.")
        return

    cfg = SlicerConfig.from_json(args.config) if args.config else SlicerConfig()
    if args.printer_type:
        cfg.printer.printer_type = args.printer_type
    if args.printer_capability:
        cfg.printer.printer_capability = args.printer_capability
    if args.infill_engine:
        cfg.infill.infill_engine = args.infill_engine
    if args.generator:
        cfg.infill.slicing_generator = args.generator
    if args.infill_mode:
        cfg.infill.mode = args.infill_mode
    if args.pattern:
        cfg.infill.pattern = args.pattern
    if args.standard_pattern:
        cfg.infill.standard_fill_pattern = args.standard_pattern
    if args.flatten_angle is not None:
        cfg.infill.flatten_angle_deg = args.flatten_angle
    if args.fff_only:
        cfg.hybrid.enable_resin_pumping = False
        cfg.hybrid.enable_uv_curing = False

    if args.test_wall:
        mesh = create_test_curved_wall_mesh(cfg)
    elif args.test_sphere:
        mesh = create_test_sphere_mesh(cfg)
    elif args.input:
        mesh = load_and_center_mesh(args.input, cfg)
    else:
        parser.error("Provide an input model file or pass --test-wall / --test-sphere.")

    run_slicer(mesh, args.output, cfg)


if __name__ == "__main__":
    main()
