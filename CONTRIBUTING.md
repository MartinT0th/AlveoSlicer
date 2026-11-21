# Contributing to AlveoSlicer

Thank you for your interest in contributing to **AlveoSlicer**! Whether you are
reporting a bug, refining cellular infill geometry, adding a new printer HAL,
improving the PySide6 3D viewport, or expanding documentation, we welcome your
contributions.

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

---

## Ways to Contribute

- **Report Bugs:** Open an issue using the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.yml).
- **Propose Features:** Open an issue using the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.yml).
- **Submit Code or Documentation:** Fork the repository, develop on a topic branch, and open a Pull Request.
- **Share Printer Profiles & Resin Presets:** Help expand built-in profiles in `alveoslicer/core/presets_data.py`.

---

## Development Environment Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/MartinT0th/AlveoSlicer.git
   cd AlveoSlicer
   ```

2. **Create a Python 3.10+ virtual environment and install in editable mode:**
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # macOS / Linux:
   source .venv/bin/activate

   pip install -e ".[all]"
   ```

3. **Verify everything passes before making changes:**
   ```bash
   python -m unittest discover -s tests -v
   ```

---

## Architectural Guidelines & Invariants

AlveoSlicer enforces a few strict invariants that all Pull Requests must preserve:

1. **Headless Core Boundary:**
   - Modules outside `alveoslicer/ui/` (`core/`, `infill/`, `slicing/`, `hybrid/`, `gcode/`, `pipeline.py`, `cli.py`) **must never import `PySide6` or `matplotlib`**.
   - Verified automatically by `test_headless_core_does_not_import_gui_libs`.
2. **100% Configuration & UI Binding Parity:**
   - Every field added to `SlicerConfig` (`alveoslicer/core/config.py`) must be registered in `CATEGORY_FIELDS` (`alveoslicer/core/presets.py`) and bound to a settings widget in `alveoslicer/ui/settings/`.
   - Verified automatically by `test_category_fields_cover_entire_slicer_config_without_duplicates` and `test_prusaslicer_ui_multi_plate_presets_transforms_and_gcode_preview`.
3. **PrusaSlicer 2.9.6 G-Code Compatibility & Hardware Safety Interlocks:**
   - Exported G-code must preserve `;LAYER_CHANGE`, `;Z:`, `;HEIGHT:`, `;TYPE:`, `;WIDTH:`, and the `; prusaslicer_config = begin/end` footer block.
   - Hybrid resin/UV cycles must preserve pump suck-back immediately after every dispense and UV-shield parking / toolhead docking before UV LEDs turn on.
4. **Documentation & Docstrings:**
   - Every new module, class, and public function or method should include a clear docstring describing its purpose and inputs.

---

## Project Layout Quick Reference

- `alveoslicer/core/` — `SlicerConfig`, geometry & SDF, mesh cutting / Place-on-Face, multi-plate `WorkspaceScene`, and `PresetManager`
- `alveoslicer/infill/` — 3D surface-normal cellular/TPMS engine and 17 standard PrusaSlicer 2D/3D infill patterns
- `alveoslicer/slicing/` — Exact-vector planar slicing, external/paint-on/dry-core supports, skirt/brim/wipe tower
- `alveoslicer/hybrid/` — Global DP cure-cycle scheduler and ghost-island volume-conserving dosing planner
- `alveoslicer/gcode/` — Toolpath ordering, G-code preview parser, and Printer HALs (`bedslinger.py`, `corexy.py`)
- `alveoslicer/ui/` — PySide6 desktop GUI (`styling/`, `viewport/`, `plater/`, `settings/`, `views/`)
- `tests/` — Domain-organized `unittest` suites

---

## Pull Request Process

1. Create a descriptive branch from `main` (e.g., `feat/voronoi-smoothing` or `fix/corexy-uv-sweep`).
2. Keep commits focused and well-scoped.
3. Add or update unit tests in the matching file under `tests/` (`test_config_presets.py`, `test_geometry_scene.py`, `test_infill_slicing.py`, `test_hybrid_pipeline.py`, or `test_ui.py`).
4. Run the full test suite (`python -m unittest discover -s tests -v`) and ensure all tests pass.
5. Open a Pull Request and complete the checklist in `.github/PULL_REQUEST_TEMPLATE.md`.

---

## License

By contributing to AlveoSlicer, you agree that your contributions will be licensed
under the **GNU Affero General Public License v3.0 (AGPL-3.0)**, as specified in
[LICENSE](LICENSE).
