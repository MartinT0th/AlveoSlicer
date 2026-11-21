"""Standalone PyInstaller entry-point script for launching the AlveoSlicer Desktop GUI."""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> None:
    """Enable multiprocessing freeze support and launch AlveoSlicer's Qt desktop GUI."""
    multiprocessing.freeze_support()
    from alveoslicer.ui.app import main

    main()


if __name__ == "__main__":
    _run()
    sys.exit(0)
