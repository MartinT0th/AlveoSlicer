---
version: 1
slug: "alveoslicer-ui"
primary_target: "alveoslicer/ui"
related_targets: []
---

# Surface Brief: AlveoSlicer Desktop UI

- **Scope:** `alveoslicer/ui/` (Plater workspace, 4 Settings tabs, Schedule & Log tab, 2D Basin Inspector, Layer Range Bar, Startup Splash, OpenGL Viewport bed/grid theme)
- **Visitor mode:** Operate (desktop 3D CAD / hybrid slicer)
- **Audience & job:** 3D printing engineers & researchers preparing models, tuning hybrid FFF + UV photopolymer parameters, slicing, and inspecting volumetric toolpaths & fluid basins.

## Direction contract

- **THESIS:** Replace AlveoSlicer's noisy yellow-everywhere boxed slicer UI with an **Optical Glass Lab** workstation where the 3D model and toolpaths lead, active controls carry crisp glacial teal, UV curing carries restrained ultraviolet indigo, and every setting tab adapts cleanly between Simple and Advanced modes without clipping or 950px stretched inputs. Refuses the category-default dark-charcoal + safety-yellow/orange boxed slicer clone.
- **OWN-WORLD:** Deep optical-flint dark ground (`#0e1217`), mineral-slate surfaces (`#151b23` / `#1c2430`), recessed obsidian inputs (`#0a0d11`), paired with a crisp quartz-paper light mode (`#f2f5f8` / `#ffffff` / `#e8eef4`). Primary action accent: glacial teal (`#2dd4bf` dark / `#0d9488` light). UV/photopolymer semantic accent: ultraviolet indigo (`#818cf8` dark / `#4f46e5` light). Typography: clean geometric humanist sans (`"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif`) with clear 11/12/13/14px hierarchy and tabular monospace (`"Cascadia Mono", "Consolas", monospace`) for coordinates, Z heights, and telemetry. No one-sided left bars on rounded boxes; flat tonal shelves with subtle 1px borders; unified translucent mineral-glass overlay plane above the 3D canvas.
- **STORY:** Engineers open AlveoSlicer and immediately read the 3D build plate, active presets, unclipped XYZ transform grid, and hybrid cure cadence without visual friction. They switch between Simple (daily parameters) and Advanced (full calibration) across all settings tabs, scrub 3D G-code & UV cure layers smoothly, and trust both dark and light modes across every tab including the 2D Basin Inspector and Schedule view.
- **FIRST VIEWPORT:** 1440x900 desktop window: sleek single-row 6-tab top bar with right-aligned Simple/Advanced mode toggle + Dark/Light/System segmented switcher; full-bleed 3D OpenGL viewport with unified translucent mineral-glass floating tool rail (left), compact action & camera pill bar (top center), optical UV-cure layer rail (right of viewport in Preview), and bottom view-mode bar; unclipped 356px right inspector organized into quiet tonal shelves (Presets, Objects, Transform, Telemetry with optical cure-cadence strip, pinned Slice Plate primary teal CTA + Slice All / Export).
- **FORM:** Optical Glass Lab, position 4 of 7 grounded directions, `seed:2543518546ae85cf`.
- **FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
