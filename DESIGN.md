---
name: AlveoSlicer
description: Optical Glass Lab design system for a hybrid FFF and UV photopolymer desktop slicer
colors:
  amber-gold-dark: "#f5b82e"
  amber-gold-hover-dark: "#fbc950"
  amber-gold-light: "#e5a415"
  amber-gold-hover-light: "#f5b82e"
  amber-gold-text-light: "#9a6700"
  ultraviolet-indigo-dark: "#818cf8"
  ultraviolet-indigo-light: "#4f46e5"
  optical-flint-base: "#0e1217"
  optical-flint-bar: "#090c10"
  mineral-slate-surface: "#151b23"
  mineral-slate-elevated: "#1c2430"
  obsidian-input: "#0b0f14"
  quartz-paper-base: "#f2f5f8"
  quartz-paper-bar: "#e6ecf2"
  pure-surface-light: "#ffffff"
  cool-mist-elevated: "#ebf0f5"
  ink-primary-dark: "#e8eef5"
  ink-secondary-dark: "#b6c3d2"
  ink-muted-dark: "#8797ab"
  ink-primary-light: "#131a24"
  ink-secondary-light: "#354355"
  ink-muted-light: "#55667a"
  border-dark: "#222b37"
  border-strong-dark: "#313e50"
  border-light: "#d2dce6"
  border-strong-light: "#b2c2d3"
  danger-dark: "#f896a0"
  danger-light: "#be123c"
typography:
  display:
    fontFamily: "Segoe UI Variable Display, Segoe UI, Inter, sans-serif"
    fontSize: "36px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Segoe UI Variable Text, Segoe UI, Inter, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, Inter, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, Inter, sans-serif"
    fontSize: "10.5px"
    fontWeight: 700
    letterSpacing: "0.06em"
  mono:
    fontFamily: "Cascadia Mono, Consolas, JetBrains Mono, monospace"
    fontSize: "11.5px"
    fontWeight: 600
    lineHeight: 1.35
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "9px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "10px"
  lg: "16px"
components:
  button-primary:
    backgroundColor: "{colors.amber-gold-dark}"
    textColor: "#120e04"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.amber-gold-hover-dark}"
    textColor: "#120e04"
  button-secondary:
    backgroundColor: "{colors.mineral-slate-elevated}"
    textColor: "{colors.ink-primary-dark}"
    rounded: "{rounded.sm}"
    padding: "6px 12px"
  card-surface:
    backgroundColor: "{colors.mineral-slate-surface}"
    textColor: "{colors.ink-primary-dark}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
  input-numeric:
    backgroundColor: "{colors.obsidian-input}"
    textColor: "{colors.ink-primary-dark}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
    width: "156px"
  badge-uv:
    backgroundColor: "rgba(129, 140, 248, 0.16)"
    textColor: "#a5b4fc"
    rounded: "{rounded.xs}"
    padding: "2px 7px"
---

# Design System: AlveoSlicer

## Overview

**Creative North Star: "Optical Glass Lab"**

AlveoSlicer treats hybrid FFF and photopolymer slicing like a precision laboratory optical bench. The 3D OpenGL build plate owns the visual center of the application, framed by quiet optical-flint dark surfaces (and crisp quartz-paper surfaces in Light mode) so geometry, toolpaths, and translucent resin channels stay immediately legible without competing chrome.

Color is rationed strictly to active states and physical meaning: glacial teal marks the primary action, active plate, and focused selection, while ultraviolet indigo is reserved exclusively for photopolymer curing cycles, UV markers, and hybrid mode semantics. Everything at rest recedes into calm mineral slate and hairline dividers.

**Key Characteristics:**
- Dark-first optical-flint workspace with a full, first-class quartz-paper Light mode and OS System tracking.
- Strict active-only accent discipline separating interactive glacial teal from UV-curing ultraviolet indigo.
- Monospace numeric precision for coordinates, layer counters, Z heights, telemetry, and G-code views.
- Progressive complexity via a global Simple / Advanced toggle that keeps essential controls scannable without disconnecting any configuration binding.
- Compact, capped-width settings forms and zero-clipping sidebar shelves engineered for desktop monitors.

## Colors

The palette pairs cool optical-flint neutrals with a single interactive glacial-teal voice and a domain-specific ultraviolet-indigo curing signal.

### Primary
- **Amber Gold Dark** (`#f5b82e`): Primary action buttons, active build plate pill, active tool highlight, External Perimeter G-code toolpaths, and focused input rings in Dark mode (paired with `#120e04` ink at >11:1 contrast).
- **Amber Gold Light** (`#e5a415`) & **Deep Amber Text** (`#9a6700`): Filled buttons and active pills in Light mode (paired with `#161206` dark ink at >9:1 contrast), with deepened `#9a6700` for light-mode text accents (>4.7:1 on white).

### Secondary
- **Ultraviolet Indigo Dark** (`#818cf8`) / **Light** (`#4f46e5`): Dedicated exclusively to photopolymer and UV curing semantics — UV CURE badges, vertical cure ticks on the layer rail, cure cadence pulses in Slice Summary, and UV cycle banners in the 2D Basin Inspector.

### Neutral
- **Optical Flint Base** (`#0e1217`) & **Bar** (`#090c10`): Dark workspace canvas and top tab strip.
- **Mineral Slate Surface** (`#151b23`) & **Elevated** (`#1c2430`): Sidebar shelves, settings cards, and interactive hover fills.
- **Obsidian Input** (`#0b0f14`): Recessed numeric inputs, combo boxes, stat tiles, and G-code editors in Dark mode.
- **Quartz Paper Base** (`#f2f5f8`), **Bar** (`#e6ecf2`), **Pure Surface** (`#ffffff`), & **Cool Mist Elevated** (`#ebf0f5`): Crisp daytime laboratory hierarchy in Light mode.
- **Ink Ramp**: Primary (`#e8eef5` dark / `#131a24` light), Secondary (`#b6c3d2` / `#354355`), and Muted (`#8797ab` / `#55667a`), calibrated to maintain >=4.5:1 text contrast on all card and elevated surfaces.

### Named Rules
**The Active-Only Accent Rule.** Inactive chrome, unselected beds, and rest-state controls stay neutral. Glacial teal appears only on the primary call to action, active mode/tool pills, and focused fields; ultraviolet indigo appears only where photopolymer or UV curing is active.

**The Dual-Theme Parity Rule.** Every custom widget — including the 3D OpenGL bed, vertical layer slider, cure cadence strip, and Matplotlib 2D Basin Inspector — reads its colors from `ThemePalette` via `get_palette(resolved_mode)` with zero hardcoded dark-only stylesheets.

## Typography

**Display Font:** Segoe UI Variable Display (fallback: Segoe UI, Inter, sans-serif)
**Body Font:** Segoe UI Variable Text (fallback: Segoe UI, Inter, sans-serif)
**Label/Mono Font:** Cascadia Mono (fallback: Consolas, JetBrains Mono, monospace)

**Character:** Clean Windows-native technical sans-serif paired with crisp tabular monospace so every coordinate, Z height, layer counter, and material metric aligns cleanly down columns.

### Hierarchy
- **Display** (700, 36px, 1.15): Startup splash wordmark (`Alveo` in primary ink + `Slicer` in glacial teal).
- **Title** (700, 12.5–13px, 1.3): Settings card headers (`#settingsCardHeader`), section titles, and primary action button text.
- **Body** (400–600, 12px, 1.4): Tab labels, form labels, checkboxes, combo options, and helper notes.
- **Label** (700, 10–10.5px, 0.06em uppercase): Compact sidebar shelf headers (`#cardHeader`) and stat labels.
- **Mono** (600–700, 11–11.5px, 1.35): Spinboxes, layer/Z readouts, slice telemetry values, object count pills, schedule banners, and G-code/log editors.

### Named Rules
**The Tabular Numerics Rule.** All mutable numeric readouts and engineering spinboxes use the monospace stack so values never jump horizontally while scrubbing layers or dragging objects.

## Layout

AlveoSlicer uses a full-bleed desktop workspace anchored by a 6-tab top bar (`Plater`, `Print Settings`, `Filament`, `Resin`, `Printer`, `Schedule & Log`) with top-right segmented pills for `Simple | Advanced` and `Dark | Light | System`.

- **Plater Workspace:** Horizontal splitter giving the 3D/2D canvas full stretch on the left and a `344px–460px` right sidebar (`356px` default) on the right. All four sidebar shelves (`Presets`, `Build Plate Objects`, `Object Manipulation`, `Slice Summary` with `CureCadenceStrip`) plus the pinned action footer fit above the fold at `1380x860` without horizontal clipping.
- **Settings Workspace:** Top preset bar, optional Simple-mode hint strip, and a two-pane splitter with a `214px–270px` left category list (`HorizontalScrollBarAlwaysOff`) and a scrollable right column capped at `820px` max width. Numeric spinboxes lock to `156px` width and combo boxes cap at `280px` so forms never stretch across ultrawide monitors.

## Elevation & Depth

Surfaces rely on tonal layering and crisp 1px hairlines rather than heavy drop shadows.

- **Plane 0 (Recessed):** Obsidian input wells (`#0b0f14` dark) and the 3D OpenGL viewport ground.
- **Plane 1 (Base):** Application window ground (`#0e1217` dark / `#f2f5f8` light).
- **Plane 2 (Shelf / Card):** Flat tonal cards (`#151b23` / `#ffffff`) bounded by 1px borders (`#222b37` / `#d2dce6`).
- **Plane 3 (Mineral Glass Overlay):** Translucent floating controls over the 3D canvas (`rgba(15, 20, 27, 234)` dark / `rgba(255, 255, 255, 238)` light) — left tool rail, top camera/action pill bar, contextual Cut/Paint popovers, feature legend, and layer slider rail — all sharing one unified overlay plane.

### Named Rules
**The Flat-By-Default Rule.** Cards and shelves sit flat with 1px structural borders; depth is communicated through surface step contrast and translucent overlay framing rather than stacked card shadows.

## Shapes

- **Compact Badges & Indicators:** `4px` radius (`#unitToggle`, checkboxes, `#uvBadge`).
- **Inputs, Buttons & List Items:** `6px` radius (`QSpinBox`, `QComboBox`, `QPushButton`, `#categoryList::item`).
- **Cards, Shelves & Segmented Pills:** `7px–8px` radius (`#sidebarCard`, `#settingsCard`, `#floatingCard`, `#segmentedPill`).
- **Viewport Floating Rails & Popovers:** `9px` radius (`#viewportRail`, `#viewportTopBar`, `#viewportPopover`).
- **Selected Navigation Items:** Full rounded pill highlight (`border: 1px solid accent_border_soft`) — never a one-sided accent border bar on a rounded rectangle.

## Components

### Buttons
- **Primary (`#primaryBtn`):** Filled glacial teal (`#2dd4bf` dark / `#0f766e` light), `7px` radius, `9px 16px` padding, bold `13px` text; reserved for `Slice Plate` and `Perform Cut`.
- **Secondary (`QPushButton`):** Elevated mineral slate fill, `1px` strong border, `6px` radius; border and text shift to glacial teal on hover.
- **Segmented Pills (`#modeBtn`, `#themeBtn`):** Hosted inside a `#segmentedPill` frame (`2px` padding); inactive items stay transparent muted text, active item fills with solid glacial teal.
- **Danger (`#dangerBtn`):** Muted crimson-tinted surface with rose ink for destructive actions (`Delete`, `Clear`).

### Cards / Containers
- **Settings Card (`#settingsCard`):** Flat `QFrame` (`8px` radius, `16px 12px 16px 14px` padding) with a bold title label, a 1px `#settingsDivider` hairline, and a right-aligned `QFormLayout` (`FieldsStayAtSizeHint`). Replaces floating-legend `QGroupBox` frames.
- **Sidebar Shelf (`#sidebarCard`):** Compact `8px` radius shelf with uppercase muted header (`10.5px`, `0.7px` letter-spacing) and inline icon toolbar.

### Inputs / Fields
- **Numeric Spinboxes:** Monospace `11.5px`, `156px` fixed width in settings, recessed input background, `6px` radius, crisp vector up/down chevrons (`chevron_up.svg` / `chevron_down.svg`), glacial-teal focus border.
- **Combo Boxes:** `210px–280px` width in settings (`Ignored` horizontal policy in sidebar to prevent clipping), crisp `chevron_down.svg` indicator.
- **Checkboxes:** `15x15px` rounded square (`4px` radius), filled glacial teal with high-contrast vector checkmark (`check_dark.svg` / `check_light.svg`) when checked.

### Signature Components
- **Optical Layer Range Rail (`LayerRangeBar`):** Docked vertical dual-handle track (`98px` wide) with a glacial-teal active window, ultraviolet-indigo cure tick marks, a zero-jitter reserved-height `UV CURE` badge (`26px` fixed, `RetainSizeWhenHidden`), and a `Next Cure` jump trigger.
- **Optical UV Cure Cadence Strip (`CureCadenceStrip`):** Compact `10px`-tall strip anchored at the bottom of `Slice Summary` displaying every scheduled UV cure cycle as crisp indigo pulses along the full sliced layer stack.

## Do's and Don'ts

### Do:
- **Do** source every Qt, Matplotlib, and OpenGL color from `ThemePalette` (`get_palette(resolved_mode)`) so Dark and Light modes stay strictly synchronized.
- **Do** cap settings input widths (`156px` spinboxes, `<=280px` combos, `<=820px` page column) and use `QSizePolicy.Ignored` horizontally on sidebar inputs to prevent clipping.
- **Do** reserve ultraviolet indigo exclusively for photopolymer and UV curing states, and glacial teal for primary/active interaction states.
- **Do** implement Simple/Advanced filtering purely via visibility (`setVisible` / `setHidden`) so all `SlicerConfig` field bindings remain registered and testable.

### Don't:
- **Don't** place a thick one-sided left border bar (`border-left: 3px solid ...`) on rounded sidebar or list items; use a full subtle rounded pill border instead.
- **Don't** hardcode dark hex colors inside view widgets (`inspector_2d.py`, `schedule_view.py`, `layer_slider.py`) or bypass `build_stylesheet`.
- **Don't** stretch settings inputs across the full viewport width or nest bordered cards inside bordered cards.
- **Don't** import `PySide6` or `matplotlib` outside `alveoslicer/ui/`.
