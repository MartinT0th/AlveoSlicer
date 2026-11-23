# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

3D printing engineers and researchers operating hybrid FFF + in-situ UV photopolymerization printers (and standard FFF printers) at a lab or workshop workstation in mixed or dim ambient light.

## Product Purpose

AlveoSlicer turns watertight 3D models (STL, OBJ, 3MF, PLY, STEP) into exact-vector thermoplastic shells filled with 3D surface-normal cellular formwork (or classic PrusaSlicer infills), schedules optimal periodic resin-injection and UV-curing cycles via global dynamic programming, and exports PrusaSlicer 2.9.6-compatible G-code with hardware safety interlocks. Success means preparing plates, tuning print/resin/UV/printer settings, inspecting sliced toolpaths and cure cycles, and exporting safe G-code quickly without fighting cramped panels, visual clutter, or ambiguous states.

## Positioning

Standalone desktop slicer purpose-built for hybrid FFF + in-situ liquid photopolymer injection and UV curing—combining 3D surface-normal cellular formwork, Beer-Lambert global DP cure scheduling, ghost-island volume-conserving dosing, and full single-extruder standard FFF slicing in one PySide6 desktop tool.

## Operating Context

- Desktop PySide6 (Qt 6 + OpenGL 3.3+) application on Windows/macOS/Linux; primary use at a lab or workshop monitor in mixed/dim light (dark-first with a complete, high-contrast light mode and OS system-theme tracking).
- Core workflow loop: load/arrange models across one or more build plates -> transform/cut/place-on-face/paint supports -> select or edit Print/Filament/Resin/Printer presets -> slice -> scrub 3D volumetric G-code & filled-resin preview and 2D Fluid Basin Inspector -> inspect UV Cure Schedule & G-code log -> export `.gcode`.

## Capabilities and Constraints

- **Desktop stack**: Python 3.10+, PySide6 (`QMainWindow`, `QOpenGLWidget`, QSS stylesheets), Matplotlib (`LayerInspector2D`).
- **Headless core boundary**: Modules outside `alveoslicer/ui/` must never import `PySide6` or `matplotlib`.
- **100% config & UI parity**: Every field in `SlicerConfig` must stay registered in `CATEGORY_FIELDS` (`core/presets.py`) and bound in `alveoslicer/ui/settings/` (`win.tabs.count() == 6` and exact `_bindings` keys are enforced by automated tests).
- **Hardware & viewer contracts**: Preserve PrusaSlicer 2.9.6 G-code compatibility, hybrid safety interlocks, zero-jitter vertical layer slider UV cure badge geometry, rounded-bead & post-dosing filled-resin 3D preview behavior, and existing public widget attributes checked by `tests/test_ui.py`.
- **Ergonomics goal**: Keep the familiar 6-tab PrusaSlicer structure while fixing cramped/clipping sidebars, stretched full-width setting inputs, weak typographic hierarchy, and chrome clutter; add Simple / Advanced setting filtering and cleaner collapsible/structured grouping for fast day-to-day slicing.

## Brand Commitments

- Product name: **AlveoSlicer** (assets in `alveoslicer/assets/AlveoSlicerLogo.png`, `alveoslicer_icon.png`, `alveoslicer.ico`).
- No binding attachment to the legacy yellow/cyan color scheme — the color system may be replaced entirely.

## Evidence on Hand

- Live runnable desktop application (`python -m alveoslicer.ui`) and automated offscreen GUI test suite (`tests/test_ui.py`).
- Bundled logo and icon assets under `alveoslicer/assets/`.
- Real procedural test models (wavy curved wall, icosphere) and full 6-stage slicer outputs.

## Product Principles

- **Precision over ornament**: The 3D model, toolpaths, and fluid basins are the hero; chrome recedes so engineers scan geometry and telemetry effortlessly.
- **Progressive disclosure without losing control**: Common daily slicing controls are immediate and scannable; deep calibration parameters stay one click away via Simple/Advanced filtering and clean section grouping.
- **Single coherent component language**: Every button, input, pill, slider, and panel across all 6 tabs and both dark/light themes shares one consistent visual grammar and clear interactive states.
- **Honest engineering telemetry**: Numeric dimensions, temperatures, volumes, and schedules read with tabular clarity and unmistakable units/states.

## Accessibility & Inclusion

- High-contrast dark and light themes (>= 4.5:1 body text contrast, >= 3:1 large text and active UI controls), clear keyboard focus indicators, and full support for Dark / Light / OS System theme switching (`ACCESSIBILITY.md`).
