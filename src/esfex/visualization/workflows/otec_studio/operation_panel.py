# -*- coding: utf-8 -*-
"""OTEC Studio — Operation panel (M4).

Simulates time-series operation of a nominal plant against a seasonal seawater
temperature profile (per-site, not averaged like the wizard), and diagnoses why
net power drops below nameplate — attributing the deficit to a smaller ΔT lift
(gross power) versus higher seawater-pump parasitics.
"""

from __future__ import annotations

import logging

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.otec_studio import operation as oper
from esfex.visualization.workflows.otec_studio.project import StudioConfig
from esfex.visualization.workflows.otec_studio.workers import OperationWorker

logger = logging.getLogger(__name__)


class OperationPanel(QWidget):
    """Per-site time-series operation + power-loss diagnostics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = None
        self._worker = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)

        ctrl = QGroupBox("Design point & seawater profile")
        ctrl.setMaximumWidth(330)
        form = QFormLayout(ctrl)

        self._t_ww_d = self._spin(0, 40, 26.0, " °C")
        self._t_cw_d = self._spin(0, 40, 5.0, " °C")
        self._dist = self._spin(0, 1000, 20.0, " km")
        form.addRow("Design warm T:", self._t_ww_d)
        form.addRow("Design cold T:", self._t_cw_d)
        form.addRow("Distance to shore:", self._dist)

        self._ww_mean = self._spin(0, 40, 26.0, " °C")
        self._ww_amp = self._spin(0, 10, 2.0, " K")
        self._cw_mean = self._spin(0, 40, 5.0, " °C")
        self._cw_amp = self._spin(0, 10, 0.5, " K")
        form.addRow("Warm mean:", self._ww_mean)
        form.addRow("Warm seasonal ±:", self._ww_amp)
        form.addRow("Cold mean:", self._cw_mean)
        form.addRow("Cold seasonal ±:", self._cw_amp)
        self._n_steps = QSpinBox()
        self._n_steps.setRange(4, 365)
        self._n_steps.setValue(12)
        form.addRow("Time steps:", self._n_steps)

        self._btn = QPushButton("Run operation")
        self._btn.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 8px;"
        )
        self._btn.clicked.connect(self._run)
        form.addRow(self._btn)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        form.addRow(self._progress)

        self._diag = QLabel("")
        self._diag.setWordWrap(True)
        self._diag.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(self._diag)
        root.addWidget(ctrl)

        # ── Right: plots ──
        self._tabs = QTabWidget()
        self._axes = {}
        for key, title in [
            ("power", "Power"), ("parasitics", "Parasitics"),
            ("temps", "Temperatures"), ("perf", "Performance"),
        ]:
            fig = Figure(figsize=(5, 4), dpi=100, layout="constrained")
            canvas = FigureCanvasQTAgg(fig)
            ax = fig.add_subplot(111)
            self._axes[key] = (fig, canvas, ax)
            self._tabs.addTab(canvas, title)
        # Ported from OTEC Analysis: CWP sizing/transmission and annual CF.
        self._tabs.addTab(self._build_pipe_tab(), "Pipe & CWP")
        self._tabs.addTab(self._build_annual_cf_tab(), "Annual CF (8760h)")
        root.addWidget(self._tabs, 1)

    def _build_pipe_tab(self):
        """Cold-water-pipe sizing, pumping parasitics, and diameter sweep."""
        w = QWidget()
        lay = QVBoxLayout(w)
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("CW depth:"))
        self._pipe_depth = self._spin(100, 5000, 1000.0, " m")
        ctrl.addWidget(self._pipe_depth)
        ctrl.addWidget(QLabel("Diameter:"))
        self._pipe_dia = self._spin(2, 25, 10.0, " m")
        ctrl.addWidget(self._pipe_dia)
        ctrl.addWidget(QLabel("Slope:"))
        self._pipe_slope = self._spin(1, 45, 7.0, "°")
        ctrl.addWidget(self._pipe_slope)
        b = QPushButton("Analyze pipe + sweep")
        b.clicked.connect(self._run_pipe)
        ctrl.addWidget(b)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        self._pipe_info = QLabel("")
        self._pipe_info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._pipe_info)
        fig = Figure(figsize=(5, 3.4), dpi=100, layout="constrained")
        self._pipe_canvas = FigureCanvasQTAgg(fig)
        self._pipe_ax = fig.add_subplot(111)
        lay.addWidget(self._pipe_canvas, 1)
        return w

    def _build_annual_cf_tab(self):
        """8760-hour capacity-factor profile from the design ΔT, with export."""
        w = QWidget()
        lay = QVBoxLayout(w)
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Nominal CF:"))
        self._cf_nom = self._spin(0.1, 1.0, 0.914, "")
        self._cf_nom.setDecimals(3)
        ctrl.addWidget(self._cf_nom)
        ctrl.addWidget(QLabel("Design ΔT:"))
        self._cf_dt = self._spin(1, 30, 21.0, " K")
        ctrl.addWidget(self._cf_dt)
        b = QPushButton("Generate")
        b.clicked.connect(self._run_annual_cf)
        ctrl.addWidget(b)
        self._cf_export = QPushButton("Export CSV")
        self._cf_export.setEnabled(False)
        self._cf_export.clicked.connect(self._export_cf)
        ctrl.addWidget(self._cf_export)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        self._cf_info = QLabel("")
        lay.addWidget(self._cf_info)
        fig = Figure(figsize=(5, 3.4), dpi=100, layout="constrained")
        self._cf_canvas = FigureCanvasQTAgg(fig)
        self._cf_ax = fig.add_subplot(111)
        lay.addWidget(self._cf_canvas, 1)
        self._annual_cf = None
        return w

    @staticmethod
    def _spin(lo, hi, val, suffix=""):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setSingleStep(0.5)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        return s

    # ------------------------------------------------------------------
    # Scenario sync
    # ------------------------------------------------------------------

    def on_scenario_changed(self, scenario, project):
        self._project = project
        site = getattr(scenario, "site", None)
        res = getattr(project, "resource", None)
        if site is not None:
            self._t_ww_d.setValue(getattr(site, "T_WW_in", self._t_ww_d.value()))
            self._t_cw_d.setValue(getattr(site, "T_CW_in", self._t_cw_d.value()))
            self._dist.setValue(getattr(site, "dist_shore", self._dist.value()))
            self._ww_mean.setValue(getattr(site, "T_WW_in", self._ww_mean.value()))
            self._cw_mean.setValue(getattr(site, "T_CW_in", self._cw_mean.value()))
        elif res is not None and res.has_design_point:
            self._t_ww_d.setValue(res.t_ww)
            self._t_cw_d.setValue(res.t_cw)
            self._ww_mean.setValue(res.t_ww)
            self._cw_mean.setValue(res.t_cw)

    def _config(self) -> StudioConfig:
        if self._project is not None:
            return self._project.active.config
        return StudioConfig()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run(self):
        n = self._n_steps.value()
        ww = oper.seasonal_profile(self._ww_mean.value(), self._ww_amp.value(), n)
        cw = oper.seasonal_profile(self._cw_mean.value(), self._cw_amp.value(), n)
        self._btn.setEnabled(False)
        self._progress.setVisible(True)
        self._diag.setText("Simulating operation…")
        self._worker = OperationWorker(
            self._config(), self._t_ww_d.value(), self._t_cw_d.value(),
            self._dist.value(), ww, cw,
        )
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, out):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        d = out["diagnosis"]
        ww = np.asarray(out["ww"])
        cw = np.asarray(out["cw"])
        t = np.arange(len(d["p_net_mw"]))

        self._diag.setText(
            f"<b>Capacity factor:</b> {d['cf'] * 100:.1f}%<br>"
            f"<b>Net power:</b> {d['p_net_min_mw']:.1f}–{d['p_net_max_mw']:.1f} MW<br>"
            f"<b>Dominant loss driver:</b> {d['dominant']}<br>"
            f"&nbsp;&nbsp;ΔT/gross: {d['loss_gross_frac'] * 100:.0f}% &nbsp;·&nbsp; "
            f"parasitic: {d['loss_parasitic_frac'] * 100:.0f}%"
        )

        # Power
        _f, c, ax = self._axes["power"]
        ax.clear()
        ax.plot(t, d["p_gross_mw"], "o-", color="#7f8c8d", ms=3, label="gross")
        ax.plot(t, d["p_net_mw"], "o-", color="#2980b9", ms=3, label="net")
        ax.set_xlabel("Time step"); ax.set_ylabel("Power (MW)")
        ax.set_title("Gross vs net power", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(alpha=0.3); c.draw()

        # Parasitics (stacked)
        _f, c, ax = self._axes["parasitics"]
        ax.clear()
        ax.stackplot(
            t, d["pump_ww_mw"], d["pump_cw_mw"], d["pump_nh3_mw"],
            labels=["WW pump", "CW pump", "NH₃ pump"],
            colors=["#e74c3c", "#3498db", "#9b59b6"],
        )
        ax.set_xlabel("Time step"); ax.set_ylabel("Parasitic power (MW)")
        ax.set_title("Parasitic loads", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right"); ax.grid(alpha=0.3); c.draw()

        # Temperatures
        _f, c, ax = self._axes["temps"]
        ax.clear()
        ax.plot(t, ww, "o-", color="#e67e22", ms=3, label="warm SW")
        ax.plot(t, cw, "o-", color="#3498db", ms=3, label="cold SW")
        ax.plot(t, d["t_evap"], "--", color="#e74c3c", label="T evap")
        ax.plot(t, d["t_cond"], "--", color="#2980b9", label="T cond")
        ax.set_xlabel("Time step"); ax.set_ylabel("Temperature (°C)")
        ax.set_title("Seawater & cycle temperatures", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(alpha=0.3); c.draw()

        # Performance
        _f, c, ax = self._axes["perf"]
        ax.clear()
        ax.plot(t, d["eff_net"] * 100, "o-", color="#16a085", ms=3, label="η net (%)")
        ax2 = ax.twinx()
        ax2.plot(t, d["lcoe"], "s-", color="#c0392b", ms=3, label="LCOE")
        ax.set_xlabel("Time step"); ax.set_ylabel("Net efficiency (%)")
        ax2.set_ylabel("LCOE")
        ax.set_title("Efficiency & LCOE", fontsize=11, fontweight="bold")
        ax.grid(alpha=0.3); c.draw()

    def _on_error(self, msg):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._diag.setText(f"<span style='color:#e74c3c;'>Error: {msg}</span>")

    # ------------------------------------------------------------------
    # Pipe & CWP (ported from OTEC Analysis)
    # ------------------------------------------------------------------

    def _gross_power_kw(self):
        """Gross power magnitude from the active design, else the config."""
        if self._project is not None:
            d = self._project.active.design
            if d is not None and hasattr(d, "x"):
                return abs(d.x.p_gross)
            return abs(self._project.active.config.gross_power)
        return 136000.0

    def _run_pipe(self):
        from esfex.visualization.workflows.otec_studio import engineering as eng
        depth = self._pipe_depth.value()
        dist = self._dist.value()
        gp = self._gross_power_kw()
        pa = eng.pipe_analysis(depth, dist, gp, self._pipe_dia.value(),
                               self._pipe_slope.value())
        sw = eng.pipe_diameter_sweep(depth, dist, gp)
        color = "#e74c3c" if pa.pumping_fraction > 0.3 else "#2ecc71"
        self._pipe_info.setText(
            f"Pipe length <b>{pa.pipe_length_m:,.0f} m</b> · pumping "
            f"<b>{pa.pumping_power_kw:,.0f} kW</b> · parasitic "
            f"<b style='color:{color};'>{pa.pumping_fraction * 100:.0f}%</b> · "
            f"net after pumping <b>{pa.net_power_after_pumping_kw / 1000:,.1f} "
            f"MW</b> · η_trans <b>{pa.eff_trans:.3f}</b><br>"
            f"Optimal diameter ≈ <b>{sw['best_diameter']:.1f} m</b> "
            f"(net delivered {sw['best_net_kw'] / 1000:,.1f} MW)")
        ax = self._pipe_ax
        ax.clear()
        ax.plot(sw["diameters"], np.array(sw["net_kw"]) / 1000, "o-",
                color="#2980b9", ms=3)
        ax.axvline(sw["best_diameter"], color="#e74c3c", ls="--", lw=1.2,
                   label=f"optimum {sw['best_diameter']:.1f} m")
        ax.axvline(self._pipe_dia.value(), color="#888", ls=":", lw=1,
                   label=f"current {self._pipe_dia.value():.1f} m")
        ax.set_xlabel("CWP diameter (m)")
        ax.set_ylabel("Net delivered power (MW)")
        ax.set_title("Net power vs pipe diameter", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(alpha=0.3)
        self._pipe_canvas.draw()

    # ------------------------------------------------------------------
    # Annual 8760-h CF profile (ported from OTEC Analysis)
    # ------------------------------------------------------------------

    def _run_annual_cf(self):
        from esfex.visualization.workflows.otec_studio import engineering as eng
        daily = eng.synthetic_daily(self._t_ww_d.value(), self._t_cw_d.value(),
                                    self._ww_amp.value(), self._cw_amp.value())
        out = eng.annual_cf_profile(daily, self._cf_nom.value(), self._cf_dt.value())
        self._annual_cf = out["hourly_cf"]
        self._cf_export.setEnabled(True)
        self._cf_info.setText(
            f"Annual mean CF <b>{out['annual_mean_cf']:.3f}</b> "
            f"· 8760 hourly values")
        ax = self._cf_ax
        ax.clear()
        ax.plot(np.arange(8760), out["hourly_cf"], color="#16a085", lw=0.4)
        ax.fill_between(np.arange(8760), out["hourly_cf"], alpha=0.2,
                        color="#16a085")
        ax.set_xlabel("Hour of year")
        ax.set_ylabel("Capacity factor")
        ax.set_title("Annual CF profile (8760 h)", fontsize=11, fontweight="bold")
        ax.grid(alpha=0.3)
        self._cf_canvas.draw()

    def _export_cf(self):
        if self._annual_cf is None:
            return
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(
            self, "Export annual CF (8760h)", "otec_cf_8760.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            np.savetxt(path, self._annual_cf, fmt="%.5f", delimiter=",",
                       header="capacity_factor", comments="")
            QMessageBox.information(self, "Export", f"Saved to {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
