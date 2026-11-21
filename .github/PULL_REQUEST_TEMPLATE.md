## Summary

<!-- Describe what this Pull Request changes and why. -->

## Related Issues

<!-- Link related issues, e.g. Fixes #123 -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Refactor / code cleanup (preserves existing behavior)
- [ ] Documentation / community files update
- [ ] Printer HAL / preset profile addition

## Verification & Quality Checklist

- [ ] `python -m unittest discover -s tests -v` passes completely
- [ ] Headless core invariant preserved (no `PySide6` or `matplotlib` imports outside `alveoslicer/ui/`)
- [ ] Any new `SlicerConfig` fields are registered in `CATEGORY_FIELDS` and bound in `alveoslicer/ui/settings/`
- [ ] Hardware safety interlocks preserved (pump suck-back after dispense, UV shield parking / toolhead dock before UV ON)
- [ ] New modules, classes, and public functions include clear docstrings
- [ ] Screenshots attached (if UI / 3D viewport / G-code preview changed)
