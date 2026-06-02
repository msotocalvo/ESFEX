# -*- coding: utf-8 -*-
"""OTEC Studio — Economics panel (M3).

Runs the nominal on-design cost model, then layers OTEX's lifetime power
degradation to compute an NPV-based LCOE — surfacing how degradation compounds
over a 20-30 year life (the wizard reports only point-in-time LCOE). Shows the
CAPEX component breakdown, the per-year power curve, and the nameplate-vs-NPV
LCOE gap. The cost level (low/high) and degradation model are user-selectable.
"""

from __future__ import annotations

import logging

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.otec_studio import economics as eco
from esfex.visualization.workflows.otec_studio.project import StudioConfig
from esfex.visualization.workflows.otec_studio.workers import EconomicsWorker

logger = logging.getLogger(__name__)


class EconomicsPanel(QWidget):
    """On-design cost model + lifetime degradation + NPV-LCOE."""

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

        ctrl = QGroupBox("Economics")
        ctrl.setMaximumWidth(320)
        form = QFormLayout(ctrl)

        self._t_ww = self._spin(0, 40, 26.0, " °C")
        self._t_cw = self._spin(0, 40, 5.0, " °C")
        self._dist = self._spin(0, 1000, 20.0, " km")
        form.addRow("Warm-water T:", self._t_ww)
        form.addRow("Cold-water T:", self._t_cw)
        form.addRow("Distance to shore:", self._dist)

        self._combo_cost = QComboBox()
        self._combo_cost.addItems(["low_cost", "high_cost"])
        form.addRow("Cost level:", self._combo_cost)

        self._combo_deg = QComboBox()
        self._combo_deg.addItems(["constant", "logistic"])
        self._combo_deg.currentIndexChanged.connect(self._on_deg_changed)
        form.addRow("Degradation:", self._combo_deg)

        self._rate = self._spin(0, 0.1, 0.005, "", decimals=4, step=0.001)
        form.addRow("Annual rate:", self._rate)
        self._log_L = self._spin(0, 1, 0.30, "", decimals=2, step=0.05)
        self._log_k = self._spin(0, 2, 0.30, "", decimals=2, step=0.05)
        self._log_t0 = self._spin(0, 40, 15.0, " yr", decimals=0, step=1.0)
        self._row_L = form.addRow("Logistic L:", self._log_L) or self._log_L
        form.addRow("Logistic k:", self._log_k)
        form.addRow("Logistic t₀:", self._log_t0)

        self._btn = QPushButton("Analyze economics")
        self._btn.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 8px;"
        )
        self._btn.clicked.connect(self._run)
        form.addRow(self._btn)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        form.addRow(self._progress)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        form.addRow(self._status)

        root.addWidget(ctrl)
        self._on_deg_changed()

        # ── Right: results ──
        self._tabs = QTabWidget()

        summ = QWidget()
        sl = QVBoxLayout(summ)
        sl.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._lbl_summary = QLabel("Run an analysis to see results.")
        self._lbl_summary.setTextFormat(Qt.TextFormat.RichText)
        sl.addWidget(self._lbl_summary)
        self._tabs.addTab(summ, "Summary")

        self._fig_capex = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self._canvas_capex = FigureCanvasQTAgg(self._fig_capex)
        self._ax_capex = self._fig_capex.add_subplot(111)
        self._tabs.addTab(self._canvas_capex, "CAPEX breakdown")

        self._fig_deg = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self._canvas_deg = FigureCanvasQTAgg(self._fig_deg)
        self._ax_deg = self._fig_deg.add_subplot(111)
        self._tabs.addTab(self._canvas_deg, "Power over life")

        root.addWidget(self._tabs, 1)

    @staticmethod
    def _spin(lo, hi, val, suffix="", decimals=1, step=0.5):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        return s

    def _on_deg_changed(self, *_):
        is_log = self._combo_deg.currentText() == "logistic"
        for w in (self._log_L, self._log_k, self._log_t0):
            w.setEnabled(is_log)

    # ------------------------------------------------------------------
    # Scenario sync
    # ------------------------------------------------------------------

    def on_scenario_changed(self, scenario, project):
        self._project = project
        cfg = scenario.config
        idx = self._combo_cost.findText(cfg.cost_level)
        if idx >= 0:
            self._combo_cost.setCurrentIndex(idx)
        # Prefer the optimized SiteContext, else the project's shared resource
        # (set by the Site & Resource panel).
        site = getattr(scenario, "site", None)
        res = getattr(project, "resource", None)
        if site is not None:
            self._t_ww.setValue(getattr(site, "T_WW_in", self._t_ww.value()))
            self._t_cw.setValue(getattr(site, "T_CW_in", self._t_cw.value()))
            self._dist.setValue(getattr(site, "dist_shore", self._dist.value()))
        elif res is not None and res.has_design_point:
            self._t_ww.setValue(res.t_ww)
            self._t_cw.setValue(res.t_cw)

    def _config(self) -> StudioConfig:
        if self._project is not None:
            cfg = self._project.active.config
            from dataclasses import replace
            return replace(cfg, cost_level=self._combo_cost.currentText())
        return StudioConfig(cost_level=self._combo_cost.currentText())

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run(self):
        model = self._combo_deg.currentText()
        deg_kw = {}
        if model == "logistic":
            deg_kw = {
                "logistic_L": self._log_L.value(),
                "logistic_k": self._log_k.value(),
                "logistic_t0": self._log_t0.value(),
            }
        self._btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Running on-design + degradation…")
        self._worker = EconomicsWorker(
            self._config(), self._t_ww.value(), self._t_cw.value(),
            self._dist.value(), model, self._rate.value(), deg_kw,
        )
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, out):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._status.setText("Done.")
        self._status.setStyleSheet("color: #2ecc71;")

        premium = (out["lcoe_npv"] - out["lcoe_nominal"])
        pct = 100 * premium / out["lcoe_nominal"] if out["lcoe_nominal"] else 0
        self._lbl_summary.setText(
            f"<table cellpadding=4>"
            f"<tr><td><b>Nominal LCOE</b></td><td>{out['lcoe_nominal']:.3f}</td></tr>"
            f"<tr><td><b>NPV LCOE (with degradation)</b></td>"
            f"<td>{out['lcoe_npv']:.3f}</td></tr>"
            f"<tr><td><b>Degradation premium</b></td>"
            f"<td>+{premium:.3f} ({pct:.1f}%)</td></tr>"
            f"<tr><td><b>CAPEX total</b></td>"
            f"<td>${out['capex_total'] / 1e6:,.1f} M</td></tr>"
            f"<tr><td><b>OPEX / yr</b></td>"
            f"<td>${(out['opex'] or 0) / 1e6:,.2f} M</td></tr>"
            f"<tr><td><b>Net power (nameplate)</b></td>"
            f"<td>{abs(out['p_net_nom']) / 1000:,.1f} MW</td></tr>"
            f"<tr><td><b>Lifetime</b></td><td>{out['lifetime']} yr</td></tr>"
            f"</table>"
        )

        self._draw_capex(out["capex_components"])
        self._draw_degradation(out["p_net_by_year"])

        if self._project is not None:
            self._project.active.cost_breakdown = {
                "CAPEX_total": out["capex_total"],
                "LCOE": out["lcoe_npv"],
            }

    def _on_error(self, msg):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        self._status.setStyleSheet("color: #e74c3c;")

    def _draw_capex(self, comps):
        ax = self._ax_capex
        ax.clear()
        items = sorted(comps.items(), key=lambda kv: kv[1], reverse=True)
        names = [k for k, _ in items]
        vals = [v / 1e6 for _, v in items]
        ax.barh(range(len(names)), vals, color="#2980b9", edgecolor="white")
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("CAPEX ($M)")
        ax.set_title("CAPEX breakdown", fontsize=11, fontweight="bold")
        ax.grid(axis="x", alpha=0.3)
        self._canvas_capex.draw()

    def _draw_degradation(self, p_by_year):
        ax = self._ax_deg
        ax.clear()
        years = np.arange(len(p_by_year))
        ax.fill_between(years, p_by_year, alpha=0.2, color="#e67e22")
        ax.plot(years, p_by_year, "o-", color="#e67e22", ms=3, lw=1.5)
        ax.axhline(p_by_year[0], color="#555", lw=0.6, ls="--",
                   label="nameplate")
        ax.set_xlabel("Year")
        ax.set_ylabel("Net power (MW)")
        ax.set_title("Net power over plant life", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        self._canvas_deg.draw()
