# AlveoSlicer

**AlveoSlicer** is a standalone slicer built from scratch for hybrid additive manufacturing (**FFF + in-situ UV photopolymerization**), with full PrusaSlicer-class FFF capabilities and clean single-extruder G-code export for standard FFF printers. It generates exact-vector watertight thermoplastic shells containing 3D surface-normal cellular formwork surrounded by a single continuous interconnected photopolymer cavity, scheduled via global dynamic programming and exported as **PrusaSlicer 2.9.6 G-code Viewer** compatible G-code.

## Key UI & Slicer Features

- **Modern Overhauled PrusaSlicer-Style UI with Dark / Light / System Themes:**
  - Dedicated top-level tabs (**Plater**, **Print Settings**, **Filament**, **Resin**, **Printer**, **Hybrid**, **Schedule & Log**) with crisp procedural vector icons, resizable splitters (`QSplitter`), small rounded corners, and yellow (`#f5c518`) accents.
  - Instant live theme switcher in the top-right tab bar and **View → Appearance** menu (**Dark**, **Light**, and **System** OS-synchronized mode) updating stylesheets, icons, layer slider, and OpenGL 3D viewport live.
- **In-Viewport 3D Controls & Bottom-Up Invisible Print Plate:**
  - Floating vertical icon tool rail inside the 3D viewport (**Select**, **Move**, **Rotate**, **Scale**, **Place on Face**, **Cut Plane**, **Paint-on Supports**) + floating top-center action & camera bar + interactive 3D RGB axis gizmos.
  - **Bottom-Up Invisible Build Plate:** Looking underneath the bed (`eye_z < 0` or the **Bottom** camera preset) automatically hides the build plate and grid so overhangs and bottom faces are 100% unobstructed.
- **3D Model Plane Cutting (`C`):** Interactive translucent yellow 3D cutting plane that can be moved and rotated freely (in-viewport dragging or exact XYZ/Euler spinboxes + Horiz/Vert presets) with **Keep Upper**, **Keep Lower**, and **Place Cut Face Flat on Bed**, generating watertight capped halves.
- **Paint-on Supports (`L`) & Upgraded Support Algorithms:**
  - Interactive 3D brush painting (**Left-drag = Enforcer (green)**, **Right-drag = Blocker (red)**, **Shift+drag = Erase**) plus one-click **Smart Fill Overhangs**, supporting `auto`, `enforcers_only`, and `everywhere` modes.
  - Upgraded support styles (**Organic tree supports**, **Grid**, **Snug**, **Pillars**), **Rectilinear / Honeycomb / Grid** support patterns, top & bottom dense interface layers, first-layer support expansion, and topology-safe dry-core internal roof supports.
- **8 Cellular & 3D Infill Patterns:**
  - `hexagonal` (equal-side regular 60°/0° honeycombs), `gyroid` (continuous 3D TPMS analytic wave surface), `cubic` (3D tilted cubic lattice), `diamond` (45° rhombic), `triangular`, `voronoi`, `square`, and `brick`.
- **Full PrusaSlicer FFF & Printer Settings:**
  - **Layers & Perimeters:** Variable first-layer height, perimeters, spiral vase mode, extra perimeters on overhangs, thin-wall detection, avoid crossing perimeters, external-perimeters-first, seam placement, min top/bottom shell thickness, **Top-Surface Ironing**, **Fuzzy Skin**, elephant-foot compensation, and XY size & hole compensation.
  - **Speeds, Accelerations & Cooling:** Per-feature speeds, `M204` acceleration control, volumetric flow rate clamping (`max_volumetric_speed_mm3_s`), filament density/cost/type, nozzle/bed first-layer & running temperatures, fan ramp-up & bridge/overhang fan overrides, firmware retraction (`G10`/`G11`), wipe while retracting, circular & rectangular bed shapes, and custom start/end/layer-change/tool-change G-code hooks.
  - **Skirt, Brim, Draft Shield & Wipe Tower:** Multi-loop skirt, draft shield, outer/inner/both brim, and live 3D wipe tower preview.
- **Multi-Plate & Multi-Instance Workflow + Presets & Standard FFF Mode:** Manage multiple independent build plates (`Slice Plate` / `Slice All Plates`), built-in & persistent user presets across all 5 categories, and independent resin/UV toggles (`--fff-only`) for regular FFF printers.

## Project Architecture

```text
alveoslicer/
├── config.py           # JSON-serializable dataclasses (FFF, Infill, Resin, UV, Printer, Hybrid, SlicerConfig)
├── presets.py          # Per-category built-in & persistent user PresetManager (print, filament, resin, printer, hybrid)
├── scene.py            # Headless multi-bed scene manager, auto-arrange, Place-on-Face, watertight 3D Cut Plane & Paint-on Supports
├── gcode_parser.py     # Fast G-code parser producing GPU-ready extrusion (incl. Ironing), travel, cure-layer & dosing buffers
├── geometry.py         # Stage 1: Multi-format mesh loading (STL/OBJ/3MF/STEP), test shapes, 3D SDF & topology-safe Shapely wrappers
├── infill.py           # Stage 2-3: Hexagonal, Gyroid TPMS, 3D Cubic & 5 additional cellular patterns + shallow-face flattening
├── slicing.py          # Stage 2-3: Exact vector slicing, Gyroid slicer, Organic/Honeycomb/Paint-on supports, skirt/brim/draft shield & wipe tower
├── scheduler.py        # Stage 4: Global DP cure-cycle scheduler (Beer-Lambert, airlocks, needle clearance, UV shadows)
├── dosing.py           # Stage 5: 3D fluid basin segmentation, geodesic BFS volume attribution, TSP needle routing
├── toolpath.py         # Stage 6a: Exact vector FFF toolpaths with Ironing, Fuzzy Skin, Spiral Vase & seam alignment
├── hal/                # Stage 6b: Hardware Abstraction Layer (PrusaSlicer 2.9.6 G-code compatibility)
│   ├── base.py         #   Base GCodeHAL, volumetric speed clamping, M204 accel, firmware retraction, custom G-code hooks & 2.9.6 footer
│   ├── bedslinger.py   #   Prototype 1: Bedslinger + carriage needle + overhead X-axis UV strip
│   └── corexy.py       #   CoreXY Toolchanger (T0=FFF, T1=Resin, T2=UV)
├── pipeline.py         # Unified 6-stage orchestrator (SliceResult, slice_mesh, run_slicer)
├── cli.py              # Command-line interface (supports all 8 patterns & --fff-only)
└── ui/                 # Overhauled PrusaSlicer-style PySide6 desktop GUI
    ├── app.py          #   Application bootstrap
    ├── icons.py        #   High-DPI procedural QPainter vector icon library (Dark/Light aware)
    ├── theme.py        #   Dark, Light & System theme engine with yellow (#f5c518) accents
    ├── main_window.py  #   AlveoSlicerWindow orchestrating theme switcher, multi-plate scene, Cut/Paint, Settings & Slicing
    ├── plater_view.py  #   Plater workspace (resizable QSplitter, floating 3D tool rail/top bar, Cut & Paint popovers, legend)
    ├── viewport_gl.py  #   Native PySide6 OpenGL 3D Editor (RGB gizmos, Cut Plane, Paint Brush, bottom-up invisible plate) & G-Code Viewer
    ├── layer_slider.py #   Theme-aware vertical dual-handle layer range bar with UV cure-cycle markers
    ├── settings_pages.py # Resizable two-pane PrusaSlicer-style settings tabs covering 100% of SlicerConfig
    ├── inspector_2d.py #   2D layer & fluid basin inspector with dosing overlay
    ├── schedule_view.py #  Clickable DP cure schedule table
    ├── console_view.py #   Pipeline log & G-code text preview
    ├── worker.py       #   QThread background slicing worker
    └── widgets.py      #   Form widget helpers
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

# Slice a built-in curved wall with 45° self-supporting diamond cells for a standard FFF printer
python -m alveoslicer --test-wall --pattern diamond --fff-only -o wall_fff.gcode

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
