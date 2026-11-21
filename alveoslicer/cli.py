"""Command-line interface for AlveoSlicer."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from .config import SlicerConfig
from .geometry import create_test_curved_wall_mesh, create_test_sphere_mesh, load_and_center_mesh
from .pipeline import run_slicer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alveoslicer",
        description="AlveoSlicer - Standalone Hybrid FFF + In-Situ UV Photopolymer Slicer (Exact Vector Engine)",
    )
    parser.add_argument("input", nargs="?", type=Path, help="Input 3D model (STL, OBJ, 3MF, STEP)")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.gcode"), help="Output G-code path")
    parser.add_argument("-c", "--config", type=Path, help="Load slicer/printer settings from JSON")
    parser.add_argument("--dump-default-config", type=Path, help="Write default JSON config and exit")
    parser.add_argument("--printer-type", choices=["bedslinger_prototype", "corexy_toolchanger"])
    parser.add_argument("--infill-mode", choices=["hollow_shell", "full_to_medial"])
    parser.add_argument("--pattern", choices=["voronoi", "hexagonal"])
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
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dump_default_config:
        SlicerConfig().to_json(args.dump_default_config)
        print(f"Wrote default configuration to '{args.dump_default_config}'.")
        return

    cfg = SlicerConfig.from_json(args.config) if args.config else SlicerConfig()
    if args.printer_type:
        cfg.printer.printer_type = args.printer_type
    if args.infill_mode:
        cfg.infill.mode = args.infill_mode
    if args.pattern:
        cfg.infill.pattern = args.pattern
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
