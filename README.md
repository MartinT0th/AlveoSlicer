# AlveoSlicer

**AlveoSlicer** is a standalone slicer built from scratch for hybrid additive manufacturing (**FFF + in-situ UV photopolymerization**). It generates exact-vector watertight thermoplastic shells containing 3D surface-normal cellular formwork (hexagonal or Voronoi empty air cells) surrounded by a single continuous interconnected photopolymer cavity, scheduled via global dynamic programming and exported as **PrusaSlicer 2.9.6 G-code Viewer** compatible G-code.

## Project Architecture

```text
alveoslicer/
├── config.py          # JSON-serializable dataclasses (FFF, Infill, Resin, UV, Printer, SlicerConfig)
├── geometry.py        # Stage 1: Multi-format mesh loading (STL/OBJ/3MF/STEP), test shapes, 3D SDF & 2D helpers
├── infill.py          # Stage 2-3: 3D surface seeding (geodesic/planar/CVT), bisector half-planes, shallow-face flattening
├── slicing.py         # Stage 2-3: Exact analytical mesh-plane slicing, 3D spherical shell offsets, dry supports, cell clipping
├── scheduler.py       # Stage 4: Global DP cure-cycle scheduler (Beer-Lambert, airlocks, needle clearance, UV shadows)
├── dosing.py          # Stage 5: 3D fluid basin segmentation, geodesic BFS volume attribution, TSP needle routing
├── toolpath.py        # Stage 6a: Exact floating-point vector FFF toolpath generation
├── hal/               # Stage 6b: Hardware Abstraction Layer (PrusaSlicer 2.9.6 G-code compatibility)
│   ├── base.py        #   Base GCodeHAL & PrusaSlicer 2.9.6 metadata/config footer
│   ├── bedslinger.py  #   Prototype 1: Bedslinger + carriage needle + overhead X-axis UV strip
│   └── corexy.py      #   CoreXY Toolchanger (T0=FFF, T1=Resin, T2=UV)
├── pipeline.py        # Unified 6-stage orchestrator (SliceResult, slice_mesh, run_slicer)
├── cli.py             # Command-line interface
└── ui/                # PySide6 desktop GUI
    ├── app.py         #   Application bootstrap
    ├── main_window.py #   AlveoSlicerWindow composing controls, viewports & worker
    ├── settings_panel.py # 5-tab settings editor synced with SlicerConfig
    ├── inspector_2d.py #  2D layer & fluid basin inspector with dosing overlay
    ├── preview_3d.py  #   3D outer mesh & cellular wireframe preview
    ├── schedule_view.py # Clickable DP cure schedule table
    ├── console_view.py #  Pipeline log & G-code preview
    ├── worker.py      #   QThread background slicing worker
    ├── widgets.py     #   Form widget helpers
    └── theme.py       #   Dark CAD stylesheet
```

## Usage

### Desktop GUI
```bash
python -m alveoslicer.ui
```

### Command-Line Interface
```bash
# Slice a built-in 30 mm test sphere
python -m alveoslicer --test-sphere -o sphere.gcode

# Slice a built-in 60x25x5 mm wavy curved wall
python -m alveoslicer --test-wall -o wall.gcode

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
