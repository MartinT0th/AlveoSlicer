"""Background QThread worker executing the AlveoSlicer pipeline without blocking the UI."""

from __future__ import annotations

import traceback

import trimesh
from PySide6.QtCore import QThread, Signal

from ..config import SlicerConfig
from ..pipeline import slice_mesh


class SliceWorker(QThread):
    """Runs ``alveoslicer.pipeline.slice_mesh`` on a worker thread and streams progress/logs."""

    progress = Signal(int, str)
    log_msg = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, mesh: trimesh.Trimesh, cfg: SlicerConfig):
        super().__init__()
        self.mesh = mesh.copy()
        self.cfg = cfg

    def run(self) -> None:
        try:
            result = slice_mesh(
                self.mesh,
                self.cfg,
                on_progress=self.progress.emit,
                on_log=self.log_msg.emit,
            )
            self.finished_ok.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
