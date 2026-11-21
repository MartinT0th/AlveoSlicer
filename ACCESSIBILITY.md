# Accessibility Statement

The **AlveoSlicer** project is committed to making hybrid additive manufacturing
and 3D slicing software accessible and usable for as many people as possible,
including users with visual, motor, or cognitive preferences and requirements.

---

## Supported Environments & Accessibility Features

AlveoSlicer runs on Windows, Linux, and macOS (Python 3.10+ with PySide6 / Qt 6)
and includes the following usability and accessibility features:

- **High-Contrast Dark, Light, and System Themes:**
  Switch instantly between **Dark Mode**, **Light Mode**, and **System Mode**
  (automatically following OS light/dark color-scheme preferences) from the
  top-right theme switcher or **View → Appearance**.
- **High-DPI Vector Icons:**
  All toolbar, viewport, and tab icons are rendered procedurally via vector paths
  so they remain sharp at arbitrary Windows/macOS/Linux display scaling factors
  (100%–250%+).
- **Keyboard Shortcuts & Menu Access:**
  Core workflows—including file import/export (`Ctrl+O`, `Ctrl+E`), tool switching
  (`Esc`, `M`, `R`, `S`, `F`, `C`, `L`), multi-plate management (`Ctrl+N`), view
  switching (`Ctrl+1`/`2`/`3`), and auto-arrange (`A`)—are accessible via keyboard
  shortcuts and standard top-level menus (`&File`, `&Edit`, `&Presets`, `&View`).
- **Headless Command-Line Interface (CLI):**
  Every slicing, configuration, and G-code generation feature is fully accessible
  via the screen-reader-friendly CLI (`alveoslicer` / `python -m alveoslicer`)
  without requiring the 3D graphical interface.
- **Stable Layout Controls:**
  Controls such as the vertical layer range slider reserve fixed space for dynamic
  status badges (e.g., `UV CURE`) to prevent sudden layout shifts during pointer
  or keyboard interaction.

---

## Known Limitations

We are aware of the following current accessibility gaps and welcome contributions
to improve them:

- **3D OpenGL Viewport (`AlveoViewport3D`):** Direct manipulation tools (3D gizmos,
  Place-on-Face, Cut Plane handle dragging, and Paint-on-Supports brushing) rely
  primarily on mouse input. Numeric alternatives are provided in the right-hand
  **Object Manipulation** panel and the **Cut Plane** popover for exact keyboard
  entry.
- **Color-Coded Toolpaths:** G-code feature types and painted support masks use
  color coding (e.g., green enforcers / red blockers). Individual feature types can
  be isolated using the checkboxes in the **Feature Types** legend, and a
  colorblind-friendly palette option is planned for future releases.
- **Screen Reader Metadata:** Custom-painted Qt widgets (such as the dual-handle
  vertical layer track) do not yet expose full `QAccessibleInterface` announcements.

---

## Reporting Accessibility Barriers

If you encounter an accessibility barrier in AlveoSlicer's GUI, CLI, or
documentation, please let us know by opening an issue on GitHub:

- [Open an Issue on MartinT0th/AlveoSlicer](https://github.com/MartinT0th/AlveoSlicer/issues)

Please include:
1. Your operating system, display scaling, and assistive technology (if any)
2. The screen, dialog, or CLI command where you encountered the barrier
3. A brief description of the issue and any suggestions that would help you
