"""Background worker for running sensitivity analysis without blocking the GUI."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from esfex.sensitivity.engine import SensitivityEngine, SobolResult

logger = logging.getLogger(__name__)


class SensitivityWorker(QThread):
    """Background worker that runs Sobol sensitivity analysis."""

    progressChanged = Signal(int, int, str)  # (current, total, message)
    resultReady = Signal(object)  # SobolResult
    errorOccurred = Signal(str)  # error message

    def __init__(
        self,
        engine: SensitivityEngine,
        lp_path: str | None = None,
        config_path: str | None = None,
        output_dir: str | None = None,
    ):
        super().__init__()
        self._engine = engine
        self._lp_path = lp_path
        self._config_path = config_path
        self._output_dir = output_dir
        self._cancelled = False

    def run(self):
        try:
            if self._engine.mode == "lp":
                if not self._lp_path:
                    self.errorOccurred.emit("No LP file specified.")
                    return
                result = self._engine.run_lp_analysis(
                    self._lp_path,
                    progress_callback=self._report_progress,
                )
            else:
                if not self._config_path or not self._output_dir:
                    self.errorOccurred.emit("Config path and output dir required for config mode.")
                    return
                result = self._engine.run_config_analysis(
                    self._config_path,
                    self._output_dir,
                    progress_callback=self._report_progress,
                )
            self.resultReady.emit(result)
        except InterruptedError:
            self.errorOccurred.emit("Analysis cancelled.")
        except Exception as e:
            logger.exception("Sensitivity analysis failed")
            self.errorOccurred.emit(str(e))

    def cancel(self):
        self._cancelled = True

    def _report_progress(self, current: int, total: int, msg: str):
        if self._cancelled:
            raise InterruptedError("Analysis cancelled by user")
        self.progressChanged.emit(current, total, msg)
