"""Background QThread worker executing the AlveoSlicer pipeline without blocking the UI."""

from __future__ import annotations

import traceback
from typing import List, Optional

import trimesh
from PySide6.QtCore import QThread, Signal

from ..core.config import SlicerConfig
from ..pipeline import slice_mesh


class SliceWorker(QThread):
    """Runs :func:`alveoslicer.pipeline.slice_mesh` on a worker thread and streams progress/logs."""

    progress = Signal(int, str)
    log_msg = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        mesh: trimesh.Trimesh,
        cfg: SlicerConfig,
        bodies: Optional[List[trimesh.Trimesh]] = None,
    ) -> None:
        """Store an isolated copy of ``mesh``, optional per-body meshes, and the slicing configuration."""
        super().__init__()
        self.mesh = mesh.copy()
        self.bodies = [b.copy() for b in bodies] if bodies is not None else None
        self.cfg = cfg

    def run(self) -> None:
        """Execute the 6-stage slicing pipeline and emit either ``finished_ok`` or ``failed``."""
        try:
            result = slice_mesh(
                self.mesh,
                self.cfg,
                on_progress=self.progress.emit,
                on_log=self.log_msg.emit,
                bodies=self.bodies,
            )
            self.finished_ok.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
