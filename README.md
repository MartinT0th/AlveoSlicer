# AlveoSlicer

**AlveoSlicer** is a standalone slicer built from scratch for hybrid additive manufacturing (**FFF + in-situ UV photopolymerization**), with full support for exporting clean single-extruder G-code for standard FFF printers. It generates exact-vector watertight thermoplastic shells containing 3D surface-normal cellular formwork (hexagonal or Voronoi empty air cells) surrounded by a single continuous interconnected photopolymer cavity, scheduled via global dynamic programming and exported as **PrusaSlicer 2.9.6 G-code Viewer** compatible G-code.

## Key UI & Slicer Features

- **PrusaSlicer-Style Workspace (`Plater` + 5 Settings Tabs):** Dedicated top-level tabs for **Plater**, **Print Settings**, **Filament**, **Resin**, **Printer**, **Hybrid**, and **Schedule & Log**, featuring a clean dark CAD theme with yellow (`#f5c518`) accents and crisp rounded corners.
- **Per-Category Presets System:** Built-in premade presets for every category (`print`, `filament`, `resin`, `printer`, `hybrid`) plus custom user preset creation, editing, reverting, and deletion persisted under `~/.alveoslicer/presets/`.
- **Standard FFF Only Mode:** Independent switches in the **Hybrid** tab (or `--fff-only` on the CLI) disable photopolymer pumping and/or UV LED curing so any sliced part can be printed directly on a regular single-extruder FFF printer.
- **GPU-Accelerated 3D Model Editor & Volumetric G-Code Viewer:** Built-in OpenGL viewport (`AlveoViewport3D`) with build-plate grid, studio-shaded 3D model manipulation (`Scale`, `Rotate Z`, `X/Y` positioning), optional 3D cellular wireframe overlay, volumetric 3D extrusion beads colored by PrusaSlicer `;TYPE:`, dual-handle vertical layer range slider with UV cure-cycle ticks, move-by-move playback scrubber, feature-type filters, travel & resin dosing overlays, and 2D fluid basin inspection.

## Project Architecture

```text
alveoslicer/
├── config.py           # JSON-serializable dataclasses (FFF, Infill, Resin, UV, Printer, Hybrid, SlicerConfig)
├── presets.py          # Per-category built-in & persistent user PresetManager (print, filament, resin, printer, hybrid)
├── gcode_parser.py     # Fast G-code parser producing GPU-ready extrusion, travel, cure-layer & dosing buffers
├── geometry.py         # Stage 1: Multi-format mesh loading (STL/OBJ/3MF/STEP), test shapes, 3D SDF & 2D helpers
├── infill.py           # Stage 2-3: 3D surface seeding (geodesic/planar/CVT), bisector half-planes, shallow-face flattening
├── slicing.py          # Stage 2-3: Exact analytical mesh-plane slicing, 3D spherical shell offsets, dry supports, cell clipping
├── scheduler.py        # Stage 4: Global DP cure-cycle scheduler (Beer-Lambert, airlocks, needle clearance, UV shadows)
├── dosing.py           # Stage 5: 3D fluid basin segmentation, geodesic BFS volume attribution, TSP needle routing
├── toolpath.py         # Stage 6a: Exact floating-point vector FFF toolpath generation
├── hal/                # Stage 6b: Hardware Abstraction Layer (PrusaSlicer 2.9.6 G-code compatibility)
│   ├── base.py         #   Base GCodeHAL & PrusaSlicer 2.9.6 metadata/config footer (Hybrid & Standard FFF)
│   ├── bedslinger.py   #   Prototype 1: Bedslinger + carriage needle + overhead X-axis UV strip
│   └── corexy.py       #   CoreXY Toolchanger (T0=FFF, T1=Resin, T2=UV)
├── pipeline.py         # Unified 6-stage orchestrator (SliceResult, slice_mesh, run_slicer)
├── cli.py              # Command-line interface (including --fff-only)
└── ui/                 # PrusaSlicer-style PySide6 desktop GUI
    ├── app.py          #   Application bootstrap
    ├── main_window.py  #   AlveoSlicerWindow orchestrating Plater, 5 Settings Tabs, Presets & Slicing
    ├── plater_view.py  #   Plater workspace (toolstrip, 3D viewport, legend, scrubbers, preset & object sidebar)
    ├── viewport_gl.py  #   Native PySide6 OpenGL 3D Model Editor & Volumetric G-Code Viewer
    ├── layer_slider.py #   Vertical dual-handle layer range bar with UV cure-cycle markers
    ├── settings_pages.py # Two-pane PrusaSlicer-style settings tabs & preset management bar
    ├── inspector_2d.py #   2D layer & fluid basin inspector with dosing overlay
    ├── schedule_view.py #  Clickable DP cure schedule table
    ├── console_view.py #   Pipeline log & G-code text preview
    ├── worker.py       #   QThread background slicing worker
    ├── widgets.py      #   Form widget helpers
    └── theme.py        #   Dark CAD stylesheet with yellow (#f5c518) accents
```

## Usage

### Desktop GUI
```bash
python -m alveoslicer.ui
```

### Command-Line Interface
```bash
# Slice a built-in 30 mm test sphere (Hybrid FFF + Resin + UV)
python -m alveoslicer --test-sphere -o sphere.gcode

# Slice a built-in 60x25x5 mm wavy curved wall for a standard FFF printer (no resin pumping / no UV)
python -m alveoslicer --test-wall --fff-only -o wall_fff.gcode

# Slice an external CAD/mesh file (STL, OBJ, 3MF, STEP/STP)
python -m alveoslicer model.step -o model.gcode --pattern hexagonal --infill-mode hollow_shell

# Dump default JSON configuration
python -m alveoslicer --dump-default-config profile.json
```

### Optional Editable Install (Console Entry Points)
```bash
pip install -e ".[all]"
alveoslicer --help
alveoslicer-gui
```

### Running Tests
```bash
python -m unittest discover -s tests -v
```
