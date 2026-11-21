# Security & Hardware Safety Policy

## Supported Versions

AlveoSlicer is actively developed. Security fixes and hardware safety patches are
applied to the latest release on `main`:

| Version | Supported |
| ------- | --------- |
| Latest (`main` / `A0.2.x`) | :white_check_mark: Supported |
| Older pre-release snapshots | :x: Unsupported |

---

## Scope: Software Security & Printer Hardware Safety

Because AlveoSlicer generates G-code that controls physical 3D printer actuators,
heaters, stepper-driven photopolymer pumps, and high-intensity UV LEDs, we treat
both **software vulnerabilities** and **hardware safety interlock regressions** as
high-priority security issues:

- **Software vulnerabilities:** Maliciously crafted 3D mesh files (`STL`, `OBJ`,
  `3MF`, `STEP`), G-code files, or JSON configuration/preset files causing code
  execution, arbitrary file writes, or unbounded resource exhaustion.
- **Hardware safety interlock bugs:** Any bug that could emit UV-ON commands before
  needle shield parking / toolhead docking, omit UV-OFF on print completion or
  abort, bypass suck-back after photopolymer dispensing, or emit unsafe thermal
  or motion commands.

---

## Reporting a Vulnerability

**Please do not report security vulnerabilities or critical hardware-safety flaws
in public GitHub issues.**

Instead, please report them privately using one of the following methods:

1. **GitHub Private Vulnerability Reporting (Preferred):**
   Navigate to the **Security** tab of
   [MartinT0th/AlveoSlicer](https://github.com/MartinT0th/AlveoSlicer/security)
   and click **"Report a vulnerability"**.
2. **Direct Maintainer Contact:**
   If private vulnerability reporting is unavailable, open a minimal issue asking
   for a private contact channel without disclosing exploit details.

### What to Include in Your Report

- A clear description of the vulnerability or safety interlock failure
- Affected AlveoSlicer version, OS, and Python / executable build
- Minimal reproduction steps (and sample mesh / config / G-code snippet if applicable)
- Potential impact (software or physical printer hazard)

### Response Timeline

- **Acknowledgment:** Within 5 business days
- **Initial Assessment:** Within 10 business days
- **Remediation & Disclosure:** We will coordinate a fix and public disclosure once
  a patched release is available.
