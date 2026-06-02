"""Dialog for running the ESFEX simulation as a subprocess."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from esfex.visualization.i18n import tr
from esfex.visualization.theme import current_theme

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RICH_CTRL_RE = re.compile(r"\x1b\[\??\d*[a-zA-Z]|\r")
_YEAR_RE = re.compile(r"Year\s+(\d{4})\s*\((\d+)/(\d+)\)")
_WINDOW_RE = re.compile(r"Window\s+(\d+)/(\d+)")


class SimulationDialog(QDialog):
    """Modal dialog that runs ``esfex run`` in a QProcess."""

    def __init__(
        self,
        config_path: str,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("simulation.title"))
        self.setMinimumSize(700, 500)
        self.resize(800, 550)
        self.setModal(True)

        self._config_path = config_path
        self._output_dir = output_dir
        self._process: QProcess | None = None

        # --- Layout ---
        layout = QVBoxLayout(self)

        # Status label
        self._status_label = QLabel(tr("simulation.preparing"))
        self._status_label.setObjectName("statusLabel")
        layout.addWidget(self._status_label)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        layout.addWidget(self._progress)

        # Log viewer
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("logViewer")
        layout.addWidget(self._log, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._cancel_btn = QPushButton(tr("simulation.cancel_btn"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)

        self._close_btn = QPushButton(tr("simulation.close_btn"))
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

        # Start the process
        self._start()

    # ------------------------------------------------------------------
    # Process management
    # ------------------------------------------------------------------

    def _start(self):
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)

        args = [
            "-m", "esfex.cli", "run",
            "--config", self._config_path,
            "--output", self._output_dir,
        ]

        self._status_label.setText(tr("simulation.preparing"))
        self._append_log(f"$ {sys.executable} {' '.join(args)}\n")
        self._process.start(sys.executable, args)

    def _on_stdout(self):
        if self._process is None:
            return
        data = self._process.readAllStandardOutput().data()
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        self._append_log(text)
        self._parse_progress(text)

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        c = current_theme().colors
        self._cancel_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._progress.setRange(0, 1)

        if exit_status == QProcess.ExitStatus.CrashExit:
            self._status_label.setText(tr("messages.error"))
            self._status_label.setStyleSheet(f"color: {c.status_error};")
            self._progress.setValue(0)
        elif exit_code != 0:
            self._status_label.setText(tr("messages.error"))
            self._status_label.setStyleSheet(f"color: {c.status_error};")
            self._progress.setValue(0)
        else:
            self._status_label.setText(tr("messages.running_sim"))
            self._status_label.setStyleSheet(f"color: {c.status_success};")
            self._progress.setValue(1)

        self._process = None

    def _on_cancel(self):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            c = current_theme().colors
            self._append_log("\n--- Cancelling simulation ---\n")
            self._process.kill()
            self._status_label.setText(tr("messages.error"))
            self._status_label.setStyleSheet(f"color: {c.status_warning};")

    # ------------------------------------------------------------------
    # Log and progress helpers
    # ------------------------------------------------------------------

    def _append_log(self, text: str):
        clean = _ANSI_RE.sub("", text)
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(clean)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()

    def _parse_progress(self, text: str):
        """Detect progress patterns in logger output."""
        # Look for "Window N/M" patterns (more frequent updates)
        for match in _WINDOW_RE.finditer(text):
            win_current = int(match.group(1))
            win_total = int(match.group(2))
            # Update status with window info, keep year context
            year_match = _YEAR_RE.search(self._status_label.text())
            year_str = ""
            if year_match:
                year_str = f"Year {year_match.group(1)} — "
            elif hasattr(self, "_current_year"):
                year_str = f"Year {self._current_year} — "
            self._status_label.setText(f"{year_str}Window {win_current}/{win_total}")
            self._progress.setRange(0, win_total)
            self._progress.setValue(win_current)
            return

        # Look for "Year XXXX (N/M)" patterns
        for match in _YEAR_RE.finditer(text):
            year = match.group(1)
            current = int(match.group(2))
            total = int(match.group(3))
            self._current_year = year
            self._status_label.setText(f"Operational Dispatch — Year {year} ({current}/{total})")
            self._progress.setRange(0, total)
            self._progress.setValue(current - 1)  # will fill when year completes
            return

        # Look for Master Problem phase
        if "Master Problem" in text or "master problem" in text.lower():
            self._status_label.setText(tr("simulation.preparing"))
            self._progress.setRange(0, 0)  # indeterminate

        # Look for data loading phase
        if "Loading data" in text:
            self._status_label.setText(tr("simulation.preparing"))

        # Look for completion
        if "completed successfully" in text.lower() or "Simulation completed" in text:
            self._status_label.setText(tr("messages.running_sim"))

    def closeEvent(self, event):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
        super().closeEvent(event)
