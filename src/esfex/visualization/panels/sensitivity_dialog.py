"""Sensitivity Analysis dialog with Sobol method visualization."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.sensitivity.engine import (
    KPI_NAMES,
    SensitivityEngine,
    SensitivityParameter,
    SobolResult,
    get_config_parameters,
    get_lp_parameters,
)
from esfex.sensitivity.worker import SensitivityWorker
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView
from esfex.visualization.theme import current_theme

logger = logging.getLogger(__name__)

_KPI_DISPLAY = {
    "total_cost": "Total Cost",
    "inv_gen_total": "Generator Investment",
    "inv_bat_total": "Battery Investment",
    "curtailment": "Curtailment",
    "load_shedding": "Load Shedding",
}


class SobolChart(FigureCanvasQTAgg):
    """Matplotlib canvas for horizontal bar chart of Sobol indices."""

    def __init__(self, parent=None):
        self.fig = Figure(figsize=(7, 5), dpi=100)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.fig.tight_layout(pad=2.0)

    def plot_sobol(self, result: SobolResult, kpi_name: str):
        self.ax.clear()

        params = result.parameters
        n = len(params)
        if n == 0:
            self.draw()
            return

        s1 = result.S1.get(kpi_name, np.zeros(n))
        st = result.ST.get(kpi_name, np.zeros(n))
        s1_conf = result.S1_conf.get(kpi_name, np.zeros(n))
        st_conf = result.ST_conf.get(kpi_name, np.zeros(n))

        y_pos = np.arange(n)
        bar_h = 0.35

        c = current_theme().colors
        self.ax.barh(
            y_pos - bar_h / 2, s1, bar_h,
            xerr=s1_conf, label="S1 (First-order)",
            color=c.status_info, ecolor=c.text_primary, capsize=3, alpha=0.85,
        )
        self.ax.barh(
            y_pos + bar_h / 2, st, bar_h,
            xerr=st_conf, label="ST (Total-order)",
            color=c.status_error, ecolor=c.text_primary, capsize=3, alpha=0.85,
        )

        self.ax.set_yticks(y_pos)
        self.ax.set_yticklabels(params, fontsize=9)
        self.ax.set_xlabel("Sobol Index")
        display = _KPI_DISPLAY.get(kpi_name, kpi_name)
        self.ax.set_title(f"Global Sensitivity: {display}", fontsize=11, fontweight="bold")
        self.ax.legend(loc="lower right", fontsize=9)
        self.ax.set_xlim(left=-0.05)
        self.ax.axvline(x=0, color="gray", linewidth=0.5)

        self.fig.tight_layout(pad=1.5)
        self.draw()

    def clear_chart(self):
        self.ax.clear()
        self.ax.set_title("Run analysis to see results")
        self.draw()


class SensitivityDialog(QDialog):
    """Dialog for configuring and running Global Sensitivity Analysis (Sobol)."""

    def __init__(
        self,
        config_path: str,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("sensitivity.title"))
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setMinimumSize(1000, 650)
        self._config_path = config_path
        self._output_dir = output_dir
        self._result: SobolResult | None = None
        self._worker: SensitivityWorker | None = None
        self._params: list[SensitivityParameter] = []
        self._build_ui()
        self._on_mode_changed()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # ── Left panel: Configuration ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # Mode
        mode_group = QGroupBox(tr("sensitivity.group_mode"))
        mode_lay = QVBoxLayout(mode_group)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["LP-Level (fast)", "Config-Level (full re-run)"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_lay.addWidget(self._mode_combo)

        # LP file
        lp_lay = QHBoxLayout()
        lp_lay.addWidget(QLabel(tr("sensitivity.lp_file")))
        self._lp_label = QLabel(tr("sensitivity.auto_detected"))
        self._lp_label.setObjectName("infoLabel")
        lp_lay.addWidget(self._lp_label, 1)
        self._lp_browse_btn = QPushButton(tr("sensitivity.browse_btn"))
        self._lp_browse_btn.clicked.connect(self._on_browse_lp)
        lp_lay.addWidget(self._lp_browse_btn)
        mode_lay.addLayout(lp_lay)

        # Auto-detect LP file
        self._lp_path = ""
        lp_dir = Path(self._output_dir) / "logs"
        if lp_dir.exists():
            lp_files = sorted(lp_dir.glob("*.lp"))
            if lp_files:
                self._lp_path = str(lp_files[0])
                self._lp_label.setText(lp_files[0].name)
                self._lp_label.setObjectName("")

        left_layout.addWidget(mode_group)

        # Samples
        samples_group = QGroupBox(tr("sensitivity.group_sampling"))
        samples_lay = QHBoxLayout(samples_group)
        samples_lay.addWidget(QLabel(tr("sensitivity.base_samples")))
        self._n_samples_spin = QSpinBox()
        self._n_samples_spin.setRange(16, 2048)
        self._n_samples_spin.setValue(64)
        self._n_samples_spin.setSingleStep(16)
        self._n_samples_spin.valueChanged.connect(self._update_eval_count)
        samples_lay.addWidget(self._n_samples_spin)
        self._eval_label = QLabel()
        samples_lay.addWidget(self._eval_label)
        left_layout.addWidget(samples_group)

        # Parameters table
        param_group = QGroupBox(tr("sensitivity.group_parameters"))
        param_lay = QVBoxLayout(param_group)
        self._param_table = QTableWidget()
        self._param_table.setHorizontalHeader(WordWrapHeaderView(self._param_table))
        self._param_table.setColumnCount(4)
        self._param_table.setHorizontalHeaderLabels(["", tr("sensitivity.col_param"), tr("sensitivity.col_lower"), tr("sensitivity.col_upper")])
        self._param_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._param_table.setColumnWidth(0, 30)
        self._param_table.setColumnWidth(2, 70)
        self._param_table.setColumnWidth(3, 70)
        param_lay.addWidget(self._param_table)

        select_lay = QHBoxLayout()
        btn_all = QPushButton(tr("sensitivity.select_all"))
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton(tr("sensitivity.select_none"))
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        select_lay.addWidget(btn_all)
        select_lay.addWidget(btn_none)
        select_lay.addStretch()
        param_lay.addLayout(select_lay)
        left_layout.addWidget(param_group, 1)

        # KPI selection
        kpi_group = QGroupBox(tr("sensitivity.group_kpis"))
        kpi_lay = QVBoxLayout(kpi_group)
        self._kpi_checks: dict[str, QCheckBox] = {}
        for kpi_key, kpi_display in _KPI_DISPLAY.items():
            cb = QCheckBox(kpi_display)
            cb.setChecked(True)
            self._kpi_checks[kpi_key] = cb
            kpi_lay.addWidget(cb)
        left_layout.addWidget(kpi_group)

        # Run / Cancel / Progress
        run_group = QGroupBox("")
        run_lay = QVBoxLayout(run_group)
        btn_lay = QHBoxLayout()
        self._run_btn = QPushButton(tr("sensitivity.run_btn"))
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_lay.addWidget(self._run_btn)
        self._cancel_btn = QPushButton(tr("sensitivity.cancel_btn"))
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_lay.addWidget(self._cancel_btn)
        run_lay.addLayout(btn_lay)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        run_lay.addWidget(self._progress_bar)
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("infoLabel")
        run_lay.addWidget(self._progress_label)
        left_layout.addWidget(run_group)

        splitter.addWidget(left)

        # ── Right panel: Results ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        # KPI selector for chart
        kpi_sel_lay = QHBoxLayout()
        kpi_sel_lay.addWidget(QLabel(tr("sensitivity.display_kpi")))
        self._kpi_display_combo = QComboBox()
        for kpi_key, kpi_display in _KPI_DISPLAY.items():
            self._kpi_display_combo.addItem(kpi_display, kpi_key)
        self._kpi_display_combo.currentIndexChanged.connect(self._on_kpi_display_changed)
        kpi_sel_lay.addWidget(self._kpi_display_combo, 1)
        right_layout.addLayout(kpi_sel_lay)

        # Chart
        self._chart = SobolChart()
        self._chart.clear_chart()
        right_layout.addWidget(self._chart, 1)

        # Export
        export_lay = QHBoxLayout()
        export_lay.addStretch()
        self._export_btn = QPushButton(tr("sensitivity.export_csv"))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_csv)
        export_lay.addWidget(self._export_btn)
        right_layout.addLayout(export_lay)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

    def _on_mode_changed(self):
        is_lp = self._mode_combo.currentIndex() == 0
        self._lp_browse_btn.setEnabled(is_lp)

        if is_lp:
            if self._lp_path:
                try:
                    params = get_lp_parameters(self._lp_path)
                except Exception as e:
                    logger.warning(f"Failed to parse LP file: {e}")
                    params = []
            else:
                params = []
        else:
            params = get_config_parameters()

        self._params = params
        self._populate_param_table(params)
        self._update_eval_count()

    def _populate_param_table(self, params: list[SensitivityParameter]):
        self._param_table.setRowCount(len(params))
        for row, p in enumerate(params):
            cb = QCheckBox()
            cb.setChecked(True)
            cb.stateChanged.connect(self._update_eval_count)
            self._param_table.setCellWidget(row, 0, cb)

            item = QTableWidgetItem(p.name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 1, item)

            lb_spin = QDoubleSpinBox()
            lb_spin.setRange(0.0, 100.0)
            lb_spin.setDecimals(2)
            lb_spin.setValue(p.lower_bound)
            lb_spin.setSingleStep(0.1)
            self._param_table.setCellWidget(row, 2, lb_spin)

            ub_spin = QDoubleSpinBox()
            ub_spin.setRange(0.0, 100.0)
            ub_spin.setDecimals(2)
            ub_spin.setValue(p.upper_bound)
            ub_spin.setSingleStep(0.1)
            self._param_table.setCellWidget(row, 3, ub_spin)

    def _set_all_checked(self, checked: bool):
        for row in range(self._param_table.rowCount()):
            cb = self._param_table.cellWidget(row, 0)
            if isinstance(cb, QCheckBox):
                cb.setChecked(checked)

    def _get_selected_params(self) -> list[SensitivityParameter]:
        selected: list[SensitivityParameter] = []
        for row in range(self._param_table.rowCount()):
            cb = self._param_table.cellWidget(row, 0)
            if not isinstance(cb, QCheckBox) or not cb.isChecked():
                continue
            p = self._params[row]
            lb_spin = self._param_table.cellWidget(row, 2)
            ub_spin = self._param_table.cellWidget(row, 3)
            selected.append(SensitivityParameter(
                name=p.name, key=p.key,
                lower_bound=lb_spin.value(),
                upper_bound=ub_spin.value(),
                category=p.category,
            ))
        return selected

    def _get_selected_kpis(self) -> list[str]:
        return [k for k, cb in self._kpi_checks.items() if cb.isChecked()]

    def _update_eval_count(self):
        n_params = sum(
            1 for row in range(self._param_table.rowCount())
            if isinstance(self._param_table.cellWidget(row, 0), QCheckBox)
            and self._param_table.cellWidget(row, 0).isChecked()
        )
        N = self._n_samples_spin.value()
        n_eval = N * (2 * n_params + 2) if n_params > 0 else 0
        self._eval_label.setText(tr("sensitivity.total_evals") + f" {n_eval}")

    def _on_browse_lp(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select LP File",
            str(Path(self._output_dir) / "logs"),
            "LP Files (*.lp);;All Files (*)",
        )
        if path:
            self._lp_path = path
            self._lp_label.setText(Path(path).name)
            self._lp_label.setObjectName("")
            self._on_mode_changed()

    def _on_run_clicked(self):
        params = self._get_selected_params()
        kpis = self._get_selected_kpis()

        if not params:
            QMessageBox.warning(self, tr("sensitivity.no_params_title"), tr("sensitivity.no_params_msg"))
            return
        if not kpis:
            QMessageBox.warning(self, tr("sensitivity.no_kpis_title"), tr("sensitivity.no_kpis_msg"))
            return

        is_lp = self._mode_combo.currentIndex() == 0
        mode = "lp" if is_lp else "config"

        if is_lp and not self._lp_path:
            QMessageBox.warning(self, tr("sensitivity.no_lp_title"), tr("sensitivity.no_lp_msg"))
            return

        engine = SensitivityEngine(
            mode=mode,
            parameters=params,
            kpi_names=kpis,
            n_base_samples=self._n_samples_spin.value(),
        )

        self._worker = SensitivityWorker(
            engine,
            lp_path=self._lp_path if is_lp else None,
            config_path=self._config_path if not is_lp else None,
            output_dir=self._output_dir if not is_lp else None,
        )
        self._worker.progressChanged.connect(self._on_progress)
        self._worker.resultReady.connect(self._on_result)
        self._worker.errorOccurred.connect(self._on_error)

        # UI state
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_label.setText("Starting...")
        self._export_btn.setEnabled(False)

        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)
        self._progress_label.setText("Cancelling...")

    def _on_progress(self, current: int, total: int, msg: str):
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_label.setText(msg)

    def _on_result(self, result: SobolResult):
        self._result = result
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._export_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._progress_label.setText(
            f"Done. {result.n_evaluations} evaluations, "
            f"{len(result.parameters)} parameters."
        )
        self._update_chart()

    def _on_error(self, msg: str):
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_label.setText(f"{tr('common.error')}: {msg}")
        QMessageBox.critical(self, tr("sensitivity.error_title"), msg)

    def _on_kpi_display_changed(self):
        if self._result:
            self._update_chart()

    def _update_chart(self):
        if not self._result:
            return
        kpi_key = self._kpi_display_combo.currentData()
        if kpi_key and kpi_key in self._result.S1:
            self._chart.plot_sobol(self._result, kpi_key)
        else:
            self._chart.clear_chart()

    def _on_export_csv(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("sensitivity.export_csv"),
            str(Path(self._output_dir) / "sobol_indices.csv"),
            "CSV Files (*.csv)",
        )
        if path:
            self._result.to_csv(path)
            QMessageBox.information(self, tr("sensitivity.exported_title"), tr("sensitivity.exported_msg", path=path))
