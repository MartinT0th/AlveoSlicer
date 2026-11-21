<p align="center">
  <img src="alveoslicer/assets/AlveoSlicerLogo.png" alt="AlveoSlicer Logo" width="140" />
</p>

<h1 align="center">AlveoSlicer</h1>

**AlveoSlicer** is a standalone, modular slicer built from scratch for **hybrid FFF + in-situ UV photopolymerization 3D printing**, while also offering full PrusaSlicer-class FFF slicing and clean single-extruder G-code export for standard FFF printers.

It turns arbitrary watertight 3D models (`STL`, `OBJ`, `3MF`, `PLY`, `STEP`) into exact-vector thermoplastic shells filled with **3D surface-normal cellular formwork** (or classic 2D/3D PrusaSlicer infills), schedules optimal resin-injection and UV-curing cycles via global dynamic programming, and exports **PrusaSlicer 2.9.6 G-code Viewer** compatible G-code with hardware safety interlocks.

---

## Table of Contents

1. [Goal & Vision of the Hybrid Printer](#1-goal--vision-of-the-hybrid-printer)
2. [Key Capabilities & Features](#2-key-capabilities--features)
3. [6-Stage Slicing Pipeline](#3-6-stage-slicing-pipeline)
4. [Code Structure & Package Architecture](#4-code-structure--package-architecture)
5. [Installation & Quick Start](#5-installation--quick-start)
6. [User Manual](#6-user-manual)
   - [Desktop Workspace Overview](#61-desktop-workspace-overview)
   - [3D Viewport Navigation & Shortcuts](#62-3d-viewport-navigation--shortcuts)
   - [Multi-Plate & Object Manipulation Workflow](#63-multi-plate--object-manipulation-workflow)
   - [Interactive 3D Cut Plane](#64-interactive-3d-cut-plane)
   - [Paint-on Supports](#65-paint-on-supports)
   - [Settings Categories, Dual Infill Engines & Presets](#66-settings-categories-dual-infill-engines--presets)
   - [Slicing, G-Code Previewer & 2D Basin Inspector](#67-slicing-g-code-previewer--2d-basin-inspector)
   - [Command-Line Interface (CLI) Reference](#68-command-line-interface-cli-reference)
7. [Developer & Extension Guide](#7-developer--extension-guide)
8. [Testing & Verification](#8-testing--verification)

---

## 1. Goal & Vision of the Hybrid Printer

Conventional **Fused Filament Fabrication (FFF)** excels at rapid, low-cost, multi-material freeform printing without resin vats, but suffers from fundamental mechanical and sealing limitations:

- **Z-Axis Anisotropy:** Adjacent horizontal layers bond thermally rather than chemically, making FFF parts significantly weaker under Z-tension and shear.
- **Micro-Porosity:** Minute gaps between extruded beads make watertight or pressure-tight parts difficult to achieve reliably.
- **Support & Core Trade-offs:** Solid FFF parts are slow and prone to warping, whereas sparse infill compromises structural load transfer.

**AlveoSlicer's hybrid printer architecture** solves these problems by combining FFF thermoplastic printing with **in-situ liquid photopolymer injection and UV curing**:

1. **Thermoplastic Formwork (FFF Shell + Cellular Cavities):** The FFF extruder prints a watertight outer skin and an internal cellular lattice oriented along the 3D surface normal (or an optional hollow shell with a continuous photopolymer moat), forming sealed basins that contain liquid resin directly inside the part—no resin vat required.
2. **Periodic Volumetric Resin Injection (Needle Pump):** Every $k$ layers—computed globally via dynamic programming—a stepper-driven dosing needle injects calibrated volumes of UV photopolymer into every open fluid basin, followed immediately by active pump suck-back to prevent dripping.
3. **In-Situ UV Photopolymerization (Hardening Passes):** An overhead UV LED strip (bedslinger prototype) or dedicated docked UV toolhead (CoreXY toolchanger) sweeps across the part at a Beer-Lambert calibrated scan speed, chemically cross-linking the liquid resin across multiple FFF layers simultaneously.
4. **Isotropic Composite Core + Full Standard FFF Mode:** Because cured photopolymer spans multiple FFF layers as monolithic vertical/3D columns bonded inside the cellular alveoli, parts gain strong Z-axis cohesion and internal sealing. Whenever you want to print on an ordinary FFF machine, toggling off Resin Pumping and UV Curing (`Standard FFF Only` preset or `--fff-only`) cleanly outputs standard single-extruder G-code with zero hybrid tool changes.

---

## 2. Key Capabilities & Features

### Dual Infill Engines
- **3D Surface-Normal Cellular & TPMS Engine (`surface_normal_3d`):**
  - **8 3D Patterns:** `hexagonal` (equal-side regular 0°/60° flat-top honeycombs), `gyroid` (analytic 3D Triply Periodic Minimal Surface), `cubic` (54.7° self-supporting 3D tilted cubic lattice), `diamond` (45° rhombic), `triangular` (equilateral iso-grid), `voronoi` (organic Lloyd-relaxed), `square`, and `brick`.
  - **Modes:** `hollow_shell` (continuous peripheral photopolymer channel + dry core with automatic internal ceiling supports) and `full_to_medial` (cellular lattice reaching the 3D medial axis).
  - **Shallow-Face Flattening:** Automatically flattens cellular roof/floor bisectors shallower than a configurable angle to eliminate unprintable near-horizontal overhangs.
- **Standard PrusaSlicer Infill Engine (`standard`):**
  - **17 Classic 2D/3D Patterns:** `rectilinear`, `aligned_rectilinear`, `grid`, `triangles`, `stars`, `cubic`, `line`, `concentric`, `honeycomb`, `3d_honeycomb`, `gyroid`, `hilbert_curve`, `archimedean_chords`, `octagram_spiral`, `adaptive_cubic`, `support_cubic`, and `lightning`.
  - **7 Top/Bottom Surface Patterns:** `monotonic_lines`, `monotonic`, `rectilinear`, `concentric`, `hilbert_curve`, `archimedean_chords`, and `octagram_spiral`.
  - Full control over fill density %, fill angle, infill anchor length, infill-before-perimeters, and combine-infill-every-N-layers.

### Hybrid Photopolymer & UV Intelligence
- **Global DP Cure-Cycle Scheduler:** Models Beer-Lambert UV depth attenuation ($C_d = D_p \ln(E / E_c)$), airlock roof closures, needle nozzle clearance, and UV shadowing ratio. Rebalances earlier pump cadences globally so forced `hard_roof_closure` short cycles are minimized or eliminated.
- **Ghost-Island Suppression with Exact Volume Conservation:** Filters sub-threshold raster sliver artifacts ("ghost islands") out of the pump route and merges their exact liquid volume into the nearest touching real resin basin so the dosing needle never wastes stops on micro-artifacts while keeping total resin volume exact.
- **Geodesic Basin Attribution & TSP Routing:** Segments connected 3D fluid pockets, computes shrinkage- and headspace-compensated doses, and optimizes needle travel order via Traveling Salesman (nearest-neighbor + 2-opt) routing.
- **Hardware Abstraction Layer (HAL) Safety Interlocks:**
  - **Bedslinger Prototype (`bedslinger_prototype`):** Enforces suck-back after every dispense and parks the carriage needle behind the physical UV shield before turning UV LEDs on.
  - **CoreXY Toolchanger (`corexy_toolchanger`):** Docks the shaded resin toolhead (`T1`) and picks up the independent UV curing toolhead (`T2`) for serpentine curing sweeps.

### PrusaSlicer-Class FFF Feature Set
- **Layers & Perimeters:** Variable first-layer height, configurable perimeter count, external-perimeters-first, seam placement (`aligned`, `rear`, `nearest`, `random`), spiral vase mode, thin-wall detection, extra perimeters on overhangs, avoid-crossing-perimeters, elephant-foot compensation, and XY size/hole compensation.
- **Surface Finishes:** Top-surface **Ironing** (configurable flow & spacing) and **Fuzzy Skin** (`outside` or `all` walls with configurable thickness and point distance).
- **Adhesion & Purging:** Multi-loop **Skirt**, **Draft Shield**, **Brim** (`outer_only`, `inner_only`, `outer_and_inner` with separation gap), and live 3D **Wipe / Purge Tower** preview.
- **Advanced Supports:** `grid`, `snug`, `organic` (tree branches), and `pillars` styles; `rectilinear`, `honeycomb`, and `grid` support patterns; top & bottom dense interface layers; first-layer expansion; `auto`, `enforcers_only`, and `everywhere` modes; plus topology-safe internal dry-core roof supports.
- **Speeds, Cooling & Printer Control:** Per-feature widths/speeds, `M204` acceleration control, volumetric speed clamping (`max_volumetric_speed_mm3_s`), first-layer vs. running nozzle/bed temps, chamber temperature, fan ramp-up & bridge overrides, firmware retraction (`G10`/`G11`), circular or rectangular beds, and custom start/end/layer-change/tool-change G-code hooks.

### Desktop 3D UI & Visualization
- **PrusaSlicer-Inspired Layout:** Clean rounded controls, yellow (`#f5c518`) accents, crisp procedural vector icons, resizable splitters, and live **Dark / Light / System** theme switching.
- **Multi-Bed 3D Plater:** Display multiple build plates side-by-side in a 3D grid (active plate opaque & crisp, inactive plates dimmed; click any bed to activate it, click empty space to deselect). Looking upward from underneath (`eye_z < 0` or **Bottom** preset) automatically hides the build plate for unobstructed overhang inspection.
- **Volumetric G-Code Previewer:** Renders continuous mitered **rounded-capsule filament beads** with hemispherical end-caps (just like PrusaSlicer), plus a dedicated **Filled resin** feature type whose translucent cyan resin slabs flood each basin only after its dosing layer completes. Features a non-jittering UV CURE badge on the vertical dual-handle layer slider, move-by-move playback scrubber, and 2D Fluid Basin Inspector.

---

## 3. 6-Stage Slicing Pipeline

```text
  3D Model (STL / OBJ / 3MF / PLY / STEP)
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 1 — Mesh Loading & Volumetric SDF        [core/geometry.py]   │
  │   Grounds mesh on Z=0, validates watertightness, builds 3D SDF      │
  └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 2 — Infill Lattice Synthesis             [infill/]            │
  │   surface_normal_3d (3D half-planes / TPMS)  OR  standard (2D/3D)   │
  └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 3 — Exact-Vector XY Slicing              [slicing/]           │
  │   Perimeters, solid caps, infill clipping, ironing, fuzzy skin,     │
  │   skirt/brim/draft shield, wipe tower, external & dry-core supports │
  └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 4 — Global DP Cure-Cycle Scheduling      [hybrid/scheduler.py]│
  │   Beer-Lambert depth, airlock closures, cadence rebalancing         │
  │   (skipped automatically when Resin & UV are both disabled)         │
  └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 5 — Volumetric Resin Dosing Planner      [hybrid/dosing.py]   │
  │   3D basin segmentation, ghost-island merging, BFS volume, TSP tour │
  └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Stage 6 — Toolpath Ordering & HAL G-Code       [gcode/]             │
  │   Seam alignment, NN ordering, M204, volumetric clamp, HAL cycles   │
  │   → PrusaSlicer 2.9.6 Compatible G-Code (.gcode)                    │
  └─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Code Structure & Package Architecture

AlveoSlicer strictly enforces a **headless core invariant**: everything outside `alveoslicer/ui/` has zero dependency on `PySide6` or `matplotlib` and can run headless in CLI, server, or CI environments.

```text
AlveoSlicer/
├── .github/                         # GitHub Issue Forms (bug/feature) & Pull Request template
├── ACCESSIBILITY.md                 # Accessibility statement, themes, shortcuts & CLI support
├── CODE_OF_CONDUCT.md               # Contributor Covenant v2.1 community standards
├── CONTRIBUTING.md                  # Contributor guide, invariants & PR checklist
├── LICENSE                          # GNU AGPL v3.0 license
├── SECURITY.md                      # Software & physical printer UV/thermal safety reporting
├── pyproject.toml                   # Package metadata, version single source of truth, extras & CLI entry points
├── README.md                        # Project overview, architecture & user manual
├── packaging/                       # Standalone portable executable build scripts
│   ├── launch_gui.py                #   Frozen PyInstaller GUI entry point
│   └── build_exe.py                 #   Single-file Windows AlveoSlicer.exe builder
├── alveoslicer/
│   ├── __init__.py                  # Lazy public Python facade (reads __version__ from pyproject.toml)
│   ├── __main__.py                  # Enables `python -m alveoslicer`
│   ├── pipeline.py                  # Unified 6-stage orchestrator (SliceResult, slice_mesh, run_slicer; multi-body aware)
│   ├── cli.py                       # Command-line interface & argument parser
│   │
│   ├── assets/                      # Bundled application icons & frozen-safe asset path resolver
│   │   ├── __init__.py              #   PyInstaller-aware get_app_icon_path() & get_logo_path()
│   │   ├── alveoslicer.ico          #   Multi-size Windows desktop & taskbar icon
│   │   ├── alveoslicer_icon.png     #   High-resolution rounded-tile app icon
│   │   └── AlveoSlicerLogo.png      #   Transparent logo artwork
│   │
│   ├── core/                        # Configuration, geometry, mesh operations, scene & presets
│   │   ├── config.py                #   JSON-serializable dataclasses (FFF, Infill, Resin, UV, Printer, Hybrid, SlicerConfig)
│   │   ├── geometry.py              #   Multi-format mesh loader, body splitter, VolumetricField SDF, topology-safe Shapely ops
│   │   ├── mesh_ops.py              #   Simplified Place-on-Face clustering & watertight 3D plane cutting
│   │   ├── scene.py                 #   Multi-plate WorkspaceScene, BuildPlate, ModelInstance, auto-arrange, paint-on masks
│   │   ├── presets_data.py          #   Built-in factory preset definitions across all 4 categories
│   │   └── presets.py               #   PresetManager (load/save/delete user presets, modified tracking, CATEGORY_FIELDS)
│   │
│   ├── infill/                      # Dual infill synthesis engines
│   │   ├── lattice.py               #   Capsule, CellHalfPlane, NormalVoronoiLattice & 2D Sutherland-Hodgman clipper
│   │   ├── surface_normal.py        #   Stage 2–3: 8 per-body 3D surface-normal cellular & Gyroid TPMS generators
│   │   └── standard.py              #   17 classic PrusaSlicer 2D/3D infill patterns & 7 top/bottom surface patterns
│   │
│   ├── slicing/                     # Exact-vector planar slicing, supports & bed adhesion
│   │   ├── layer.py                 #   SlicedLayer dataclass (vector paths + 2D resin/solid/void raster masks)
│   │   ├── supports.py              #   Grid/Snug/Organic/Pillars external supports, paint-on masks & dry-core supports
│   │   ├── adhesion.py              #   Skirt, draft shield, outer/inner/both brim & wipe/purge tower builders
│   │   └── engine.py                #   Exact-vector per-body & per-layer slicing engine uniting shells, infill, supports & adhesion
│   │
│   ├── hybrid/                      # Photopolymer cure scheduling & volumetric dosing
│   │   ├── scheduler.py             #   Stage 4: Global DP cure-cycle scheduler with hard-roof cadence rebalancing
│   │   └── dosing.py                #   Stage 5: 3D fluid basin segmentation, ghost-island merging, BFS dosing & TSP routing
│   │
│   ├── gcode/                       # Toolpath planning, G-code parsing & printer HALs
│   │   ├── toolpath.py              #   Stage 6a: Seam alignment, fuzzy skin, ironing, spiral vase & NN path ordering
│   │   ├── parser.py                #   Fast G-code parser producing vectorized GPU buffers (extrusions, travels, resin, cures)
│   │   └── hal/
│   │       ├── base.py              #   GCodeHAL base class (PrusaSlicer 2.9.6 contract, M204, volumetric clamp, hooks)
│   │       ├── bedslinger.py        #   BedslingerPrototypeHAL (carriage needle + overhead X-axis UV strip + shield park)
│   │       └── corexy.py            #   CoreXYToolchangerHAL (docked T0=FFF, T1=Resin, T2=UV toolheads)
│   │
│   └── ui/                          # PySide6 PrusaSlicer-style desktop GUI
│       ├── __init__.py              #   Lazy exports for AlveoSlicerWindow, AlveoSplashScreen, and main()
│       ├── __main__.py              #   Enables `python -m alveoslicer.ui`
│       ├── app.py                   #   QApplication bootstrap, startup splash screen & Windows taskbar icon setup
│       ├── splash.py                #   Frameless PyCharm/PrusaSlicer-style startup splash card with animated throbber
│       ├── main_window.py           #   AlveoSlicerWindow orchestrating tabs, menus, themes, multi-plate & slicing queue
│       ├── worker.py                #   Background QThread SliceWorker
│       ├── styling/
│       │   ├── theme.py             #   Dark / Light / System QSS theme engine & persisted theme preference
│       │   ├── icons.py             #   High-DPI procedural QPainter vector icon library
│       │   └── widgets.py           #   Spinbox, scroll container & form row helpers
│       ├── viewport/
│       │   ├── shaders.py           #   GLSL lit-mesh & unlit-line shaders
│       │   ├── math_utils.py        #   Projection/lookAt matrices, raycasting, Möller-Trumbore intersection
│       │   ├── builders.py          #   GPU VBO builders: lit meshes, rounded mitered beads, filled resin slabs, gizmos
│       │   └── widget.py            #   AlveoViewport3D QOpenGLWidget (multi-bed grid, bottom-up hide, Cut/Paint, Preview)
│       ├── plater/
│       │   ├── layer_slider.py      #   Non-jittering dual-handle vertical layer slider with cyan UV cure ticks
│       │   ├── panels.py            #   Cut Plane popover, Paint-on Supports popover, Feature Legend & Right Sidebar cards
│       │   └── view.py              #   PlaterView coordinating viewport overlays, mode toggles & move playback
│       ├── settings/
│       │   ├── base_tab.py          #   Two-pane PrusaSlicer-style CategorySettingsTab base class & preset bar
│       │   ├── print_tab.py         #   Unified Print Settings tab (Layers, Dual Infill, Supports, Speed, Hybrid Mode, Pump & UV)
│       │   ├── material_tabs.py     #   Filament Settings & Photopolymer Resin Settings tabs
│       │   └── machine_tabs.py      #   Printer Settings tab
│       └── views/
│           ├── inspector_2d.py      #   2D Matplotlib layer & fluid basin cross-section inspector
│           ├── schedule_view.py     #   Clickable DP UV Cure Schedule table
│           └── console_view.py      #   Pipeline console log & G-code text previewer
│
└── tests/                           # Domain-organized unittest suite
    ├── test_config_presets.py       #   JSON roundtrip, schema resilience, 100% CATEGORY_FIELDS coverage & preset CRUD
    ├── test_geometry_scene.py       #   Watertight meshes, headless isolation, multi-plate, Place-on-Face, Cut Plane, GEOS safety
    ├── test_infill_slicing.py       #   Hexagonal regularity, all 8 3D patterns, 17 standard infills, Organic/Paint supports, Ironing
    ├── test_hybrid_pipeline.py      #   Bedslinger & CoreXY HAL interlocks, FFF-only mode, ghost islands, DP cadence rebalancing
    └── test_ui.py                   #   Offscreen PySide6 smoke tests, 100% UI widget bindings, rounded beads & post-dosing resin
```

---

## 5. Installation & Quick Start

### Prerequisites
- **Python 3.10+** (tested on Python 3.10 – 3.14)
- An OpenGL 3.3+ compatible graphics driver (for the desktop 3D GUI)

### Install in a Virtual Environment

```bash
# 1. Create and activate a virtual environment
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 2. Install AlveoSlicer with Desktop GUI + STEP CAD support
pip install -e ".[all]"

# Or install headless engine only (no PySide6 / matplotlib)
pip install -e .
```

### Launch AlveoSlicer

```bash
# Launch the Desktop GUI
python -m alveoslicer.ui
# (or, after editable install)
alveoslicer-gui

# Run the Headless CLI
python -m alveoslicer --help
# (or, after editable install)
alveoslicer --help
```

### Standalone Portable Executable (`AlveoSlicer.exe`)

You can package AlveoSlicer as a single-file Windows executable that embeds Python, PySide6, OpenGL, scientific libraries, and the application icon—no Python installation or repository folder is required at runtime:

```bash
# Install PyInstaller once inside your virtual environment
pip install pyinstaller

# Build dist/AlveoSlicer.exe
python packaging/build_exe.py
```

Once built, copy or move `dist/AlveoSlicer.exe` anywhere on your PC (such as your **Desktop**, Start Menu folder, or a USB drive) and double-click it to launch AlveoSlicer directly.

---

## 6. User Manual

### 6.1 Desktop Workspace Overview

AlveoSlicer launches with a crisp startup splash screen and opens with **6 top-level tabs** modeled directly after PrusaSlicer, plus a live **Dark / Light / System** theme switcher in the top-right corner:

| Tab | Purpose |
| --- | --- |
| **Plater** | 3D multi-bed build workspace, floating viewport tools, presets sidebar, slicing controls, 3D G-Code Previewer, and 2D Basin Inspector |
| **Print Settings** | Layers & Perimeters, Infill (Dual Engine), Skirt/Brim/Draft Shield, Supports, Speed/Accel, Wipe Tower, **Hybrid Mode** (Enable Resin Pumping / Enable UV Curing), **Resin Pump & Needle**, **UV Hardening**, and Output Options |
| **Filament** | Filament diameter, density, cost, extrusion multiplier, first-layer & running temperatures, cooling fan ramp, retraction & Z-hop |
| **Resin** | Photopolymer Beer-Lambert cure parameters, slab limits, DP scheduler weights, and ghost-island threshold |
| **Printer** | Machine profile (`bedslinger_prototype` / `corexy_toolchanger`), bed size & shape, Z-offset, tool indices, needle offsets, UV shield park, custom G-code hooks |
| **Schedule & Log** | Clickable DP UV Cure Cycle table (click any row to jump directly to that layer in the Plater) + live pipeline log and raw G-code text preview |

### 6.2 3D Viewport Navigation & Shortcuts

#### Mouse Navigation
- **Orbit Camera:** Left-drag on empty space (or Right-drag anywhere)
- **Pan Camera:** Middle-drag (or `Shift` + Right-drag)
- **Zoom:** Mouse scroll wheel
- **Select Model:** Left-click on any model on the active plate
- **Deselect Model:** Left-click on empty background space
- **Switch Active Build Plate:** Left-click directly on another build plate in the 3D grid
- **Underside Inspection:** Orbit below the bed (`Z < 0`) or click the **Bottom** camera button in the top pill bar—the build plate automatically turns invisible so overhangs and undersides are completely unobstructed.

#### Keyboard Shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` | Import 3D Model (`STL`, `OBJ`, `3MF`, `PLY`, `STEP`) |
| `Ctrl+N` | Add New Build Plate |
| `Ctrl+G` | Open External G-Code in 3D Previewer |
| `Ctrl+E` | Export Active Plate G-Code |
| `Esc` | Select / Orbit Tool |
| `M` | Move Tool (3D RGB Axis Arrows & Bed Drag) |
| `R` | Rotate Tool (3D RGB Rings; `Shift`+drag for X/Y) |
| `S` | Scale Tool (3D Handles & Uniform Drag) |
| `F` | Place on Face Tool (Click highlighted cyan face to lay flat on `Z = 0`) |
| `C` | Cut Plane Tool (Interactive movable/rotatable 3D cutting plane) |
| `L` | Paint-on Supports Tool (Enforcer / Blocker / Erase 3D brush) |
| `A` | Auto-Arrange all models on the active build plate |
| `Ctrl+D` | Duplicate selected model instance |
| `Del` | Delete selected model instance |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Switch between **3D Editor**, **G-Code Preview**, and **2D Basins** |

### 6.3 Multi-Plate & Object Manipulation Workflow

1. **Import or Create Models:** Click **Add** in the top floating viewport bar (or use the built-in **Test Sphere** / **Test Curved Wall** buttons to experiment immediately).
2. **Arrange & Clone:** Use **Clone** (`Ctrl+D`), **Auto-Arrange** (`A`), **Center**, **Drop to Bed**, or exact **Pos / Rot / Scale** spinboxes in the right **Object Manipulation** card. Toggle **Lock uniform scale** for proportional or non-uniform scaling. When multiple bodies sit on the same build plate, each body automatically receives its own independent 3D cellular wireframe and is sliced separately.
3. **Place on Face (`F`):** Press `F` to highlight simplified planar landing faces on the selected model in translucent cyan. Click any face to rotate the model so that face sits flush on `Z = 0`.
4. **Multiple Build Plates:** Click **Add Plate** (`Ctrl+N`) in the top bar to add independent print beds arranged side-by-side in 3D space. Use **To Plate...** in the Build Plate Objects card to transfer models between plates, or click **Slice All Plates** to queue and slice every populated bed sequentially.

### 6.4 Interactive 3D Cut Plane

Press **`C`** (or click the Cut icon on the left floating tool rail) to open the **Cut Plane** popover:

1. **Position & Tilt:** Drag the yellow vertical handle in the 3D viewport to raise/lower the cut plane (`Shift`+drag to tilt), or enter exact **Pos (X, Y, Z)** and **Rot (°)** values. Quick buttons snap to **Horiz Z**, **Vert X**, or **Vert Y**.
2. **Choose Outputs:** Toggle **Keep Upper Part**, **Keep Lower Part**, and optionally **Place cut face flat on bed** to automatically orient the flat cut seam onto `Z = 0`.
3. **Execute:** Click **Perform Cut**. AlveoSlicer slices the mesh along the plane, caps the open cross-sections into watertight solids, and replaces the original model with the resulting part(s) on the build plate.

### 6.5 Paint-on Supports

Press **`L`** (or click the Paint-on Supports icon) to paint custom support regions directly onto the 3D mesh:

- **Left-drag:** Paint **Enforcer** faces (green) — forces support generation underneath these faces.
- **Right-drag:** Paint **Blocker** faces (red) — prevents supports under these faces even in `auto` mode.
- **Shift + drag:** Erase painted regions.
- **Smart Fill:** Set an overhang threshold angle (e.g., `45°`) and click **Smart Fill** to automatically paint enforcers on all steeper downward-facing triangles.
- **Support Mode:** In **Print Settings → Supports**, enable **Generate support material** and choose `auto` (automatic overhangs + enforcers minus blockers), `enforcers_only` (only where painted green), or `everywhere`.

### 6.6 Settings Categories, Dual Infill Engines & Presets

- **Presets Sidebar:** Every category (**Print**, **Filament**, **Resin**, **Printer**) ships with built-in presets. Selecting a preset updates the configuration immediately; editing any parameter marks the category `* modified`. Use the **Save / Save As / Revert / Delete** toolbar inside any settings tab to manage persistent custom user presets (stored in `~/.alveoslicer/presets/`).
- **Switching Between Hybrid & Standard FFF Only:**
  - In the Plater's **Print Settings** dropdown (or **Print Settings → Hybrid Mode**), select **`0.20mm STANDARD FFF (Gyroid 15%, No Resin/UV)`** or **`0.20mm CELLULAR FFF ONLY (Hexagonal, No Resin/UV)`** (or uncheck **Enable Resin Pumping** and **Enable UV Curing**) to turn off photopolymer dosing and UV curing. The Slice Summary banner updates to **`STANDARD FFF ONLY`**, and exported G-code uses a single extruder (`T0`) compatible with any standard FFF printer.
- **Switching Between 3D Surface-Normal & Standard Infill:**
  - Open **Print Settings → Infill** and select **Infill Engine**:
    - **`3D Surface-Normal Cellular / TPMS`** for hybrid photopolymer alveoli (`hexagonal`, `gyroid`, `cubic`, `diamond`, `triangular`, `voronoi`, `square`, `brick`).
    - **`Standard PrusaSlicer Infill`** for classic 2D/3D FFF infills (`gyroid`, `grid`, `rectilinear`, `honeycomb`, `cubic`, `adaptive_cubic`, `lightning`, `hilbert_curve`, etc.), or choose a built-in **`STANDARD FFF`** print preset directly from the sidebar.
- **Full JSON Profiles:** Use **File → Export Config JSON...** and **File → Import Config JSON...** to share or archive complete 4-category configurations.

### 6.7 Slicing, G-Code Previewer & 2D Basin Inspector

1. Click **Slice Plate** (yellow primary button in the bottom-right footer). Progress updates live without blocking the UI.
2. When slicing finishes, AlveoSlicer automatically switches to **G-Code Preview**:
   - **Feature Legend (Left):** Toggle individual extrusion types (`External perimeter`, `Perimeter`, `Solid infill`, `Top solid infill`, `Internal infill`, `Support material`, `Support material interface`, `Skirt/Brim`, `Wipe tower`, `Ironing`, **`Filled resin`**), plus **Travel moves** and **Resin dosing** needles.
   - **Vertical Dual-Handle Layer Slider (Right):** Drag the yellow upper handle to inspect layer by layer, or the gray lower handle to isolate a vertical slice window. Cyan ticks indicate scheduled UV cure layers; clicking **Next Cure** jumps directly to the next cure cycle.
   - **Post-Dosing Filled Resin Rendering:** Translucent cyan **Filled resin** slabs appear in a cycle's channels **only once the cycle's dosing layer has completed**, accurately mirroring physical printing rather than appearing layer-by-layer prematurely.
   - **Move Scrubber & Play (Bottom):** Drag the horizontal **Move** slider (or press **Play**) to watch the nozzle trace continuous rounded extrusion beads step by step across the top visible layer.
3. Click **2D Basins** (`Ctrl+3`) to inspect any layer's exact 2D raster classification (Watertight Shell, Inside Perimeters/Caps, Photopolymer Channels, Dry-Core Supports) along with needle dosing target coordinates and TSP routing order.
4. Click **Export G-Code...** (`Ctrl+E`) to save the finished `.gcode` file.

### 6.8 Command-Line Interface (CLI) Reference

```bash
# 1. Slice a built-in 30 mm sphere with default Hybrid FFF + Resin + UV settings
python -m alveoslicer --test-sphere -o sphere_hybrid.gcode

# 2. Slice a built-in curved wall with 45° self-supporting diamond cells for a standard FFF printer
python -m alveoslicer --test-wall --pattern diamond --fff-only -o wall_fff.gcode

# 3. Slice an external STL/OBJ/3MF/STEP file using Standard PrusaSlicer Gyroid infill
python -m alveoslicer part.step -o part_gyroid.gcode \
    --infill-engine standard --standard-pattern gyroid --fff-only

# 4. Slice for a CoreXY Toolchanger with hollow_shell photopolymer moat
python -m alveoslicer part.stl -o part_corexy.gcode \
    --printer-type corexy_toolchanger --infill-mode hollow_shell --pattern hexagonal

# 5. Export default configuration JSON, edit it, and slice with -c
python -m alveoslicer --dump-default-config my_profile.json
python -m alveoslicer part.stl -c my_profile.json -o part_custom.gcode
```

---

## 7. Developer & Extension Guide

AlveoSlicer is structured so new capabilities plug in with minimal coupling:

- **Adding a Configuration Parameter:**
  1. Add the typed field + default to the appropriate dataclass in `alveoslicer/core/config.py`.
  2. Register `(section_name, field_name)` in `CATEGORY_FIELDS` in `alveoslicer/core/presets.py` (enforced by `test_category_fields_cover_entire_slicer_config_without_duplicates`).
  3. Bind a widget in the matching settings tab under `alveoslicer/ui/settings/` (enforced by `test_prusaslicer_ui_multi_plate_presets_transforms_and_gcode_preview`).
- **Adding a New Printer HAL:**
  1. Subclass `GCodeHAL` (`alveoslicer/gcode/hal/base.py`) in a new file under `alveoslicer/gcode/hal/` and implement `emit_resin_and_uv_cycle(...)`.
  2. Register its `printer_type` key in `create_hal(...)` inside `alveoslicer/gcode/hal/__init__.py` and expose the option in `alveoslicer/ui/settings/machine_tabs.py`.
- **Adding a New Infill Pattern:**
  - For classic 2D/3D infill patterns, add the generator to `alveoslicer/infill/standard.py` and list its name in `STANDARD_INFILL_PATTERNS`.
  - For 3D surface-normal cellular lattices, add the seed/surface generator to `alveoslicer/infill/surface_normal.py` and list its name in `CELL_PATTERNS`.
- **Headless Core Rule:** Never import `PySide6` or `matplotlib` inside `core/`, `infill/`, `slicing/`, `hybrid/`, `gcode/`, `pipeline.py`, or `cli.py` (verified automatically by `test_headless_core_does_not_import_gui_libs`).

---

## 8. Testing & Verification

Run the entire modular unit and offscreen GUI test suite:

```bash
python -m unittest discover -s tests -v
```

The test suite verifies:
- `test_config_presets.py` — JSON roundtrip, forward-compatible unknown keys, 100% `CATEGORY_FIELDS` coverage, and user preset persistence.
- `test_geometry_scene.py` — Watertight built-in meshes, headless core isolation, multi-plate arrangement, Place-on-Face alignment, watertight 3D plane cutting, and GEOS `TopologyException` resilience.
- `test_infill_slicing.py` — Regular equal-side hexagonal uniformity, all 8 3D cellular patterns, 17 standard PrusaSlicer infill patterns, organic tree supports, paint-on enforcers/blockers, ironing, and fuzzy skin.
- `test_hybrid_pipeline.py` — Bedslinger & CoreXY HAL safety interlocks, PrusaSlicer 2.9.6 G-code contract, Standard FFF-only mode, ghost-island volume conservation, and DP cadence rebalancing minimizing `hard_roof_closure`.
- `test_ui.py` — Full offscreen PySide6 desktop GUI workflows, 100% settings-widget binding parity, Dark/Light/System themes, non-jittering UV CURE badge, rounded mitered extrusion beads, post-dosing Filled Resin rendering, and multi-bed 3D grid interaction.
