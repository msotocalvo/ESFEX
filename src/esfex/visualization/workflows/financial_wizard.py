# -*- coding: utf-8 -*-
"""Financial Analysis wizard dialog.

Two-phase multi-step wizard:

Phase A — Economic Overview (blue):
  1. Load Results & Configure Assumptions
  2. Cost Decomposition (waterfall, stacked bar, pie)
  3. Technology Economics (table, bubble chart, revenue vs cost)
  4. Market Analysis (price duration, cumulative NPV)

Phase B — Deep Financial Analysis (orange):
  5. Cash Flow Pro Forma (table + DSCR timeline)
  6. Investment Metrics Dashboard (NPV, IRR, MIRR, payback, WACC, DSCR)
  7. Sensitivity & Monte Carlo (tornado, spider, NPV/IRR histograms)
  8. Report & Export (executive summary, CSV, TXT)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.financial_steps import (
    CashFlowStep,
    CostDecompositionStep,
    InvestmentMetricsStep,
    LoadResultsStep,
    MarketAnalysisStep,
    ReportStep,
    SensitivityStep,
    TechnologyEconomicsStep,
)

logger = logging.getLogger(__name__)


# Phase names
_PHASE_A_NAMES = [
    "Load & Configure",
    "Cost Decomposition",
    "Technology Economics",
    "Market Analysis",
]

_PHASE_B_NAMES = [
    "Cash Flows",
    "Investment Metrics",
    "Sensitivity & MC",
    "Report & Export",
]

_PHASE_A_COUNT = len(_PHASE_A_NAMES)


class FinancialWizard(QDialog):
    """Multi-step wizard for post-optimization financial analysis.

    Unlike other wizards, this does NOT require a map_widget — it is
    purely analytical, working on HDF5 optimization results.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Financial Analysis")
        self.setMinimumSize(850, 650)
        self.resize(1100, 800)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._current_step = 0
        self._build_ui()
        self._update_navigation()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Step indicator bar — Phase A
        self._indicator_bar_a = QHBoxLayout()
        self._step_labels: list[QLabel] = []

        phase_a_lbl = QLabel("A")
        phase_a_lbl.setStyleSheet(
            "background-color: #2980b9; color: white; "
            "border-radius: 10px; padding: 2px 6px; font-weight: bold;"
        )
        phase_a_lbl.setFixedWidth(24)
        phase_a_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator_bar_a.addWidget(phase_a_lbl)

        for i, name in enumerate(_PHASE_A_NAMES):
            lbl = QLabel(f"  {i+1}. {name}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._step_style(is_current=(i == 0), is_done=False))
            self._step_labels.append(lbl)
            self._indicator_bar_a.addWidget(lbl)

        layout.addLayout(self._indicator_bar_a)

        # Phase separator
        sep_row = QHBoxLayout()
        phase_sep = QFrame()
        phase_sep.setFrameShape(QFrame.Shape.HLine)
        phase_sep.setStyleSheet("color: #444;")
        sep_row.addWidget(phase_sep)
        layout.addLayout(sep_row)

        # Step indicator bar — Phase B
        self._indicator_bar_b = QHBoxLayout()

        phase_b_lbl = QLabel("B")
        phase_b_lbl.setStyleSheet(
            "background-color: #e67e22; color: white; "
            "border-radius: 10px; padding: 2px 6px; font-weight: bold;"
        )
        phase_b_lbl.setFixedWidth(24)
        phase_b_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator_bar_b.addWidget(phase_b_lbl)

        for j, name in enumerate(_PHASE_B_NAMES):
            idx = _PHASE_A_COUNT + j
            lbl = QLabel(f"  {idx+1}. {name}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._step_style(is_current=False, is_done=False))
            self._step_labels.append(lbl)
            self._indicator_bar_b.addWidget(lbl)

        layout.addLayout(self._indicator_bar_b)

        # Separator below indicators
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #555;")
        layout.addWidget(sep)

        # Stacked widget for step pages
        self._stack = QStackedWidget()

        # Phase A steps
        self._step_load = LoadResultsStep()
        self._step_cost = CostDecompositionStep()
        self._step_tech = TechnologyEconomicsStep()
        self._step_market = MarketAnalysisStep()

        # Phase B steps
        self._step_cashflow = CashFlowStep()
        self._step_metrics = InvestmentMetricsStep()
        self._step_sensitivity = SensitivityStep()
        self._step_report = ReportStep()

        self._steps = [
            self._step_load,
            self._step_cost,
            self._step_tech,
            self._step_market,
            self._step_cashflow,
            self._step_metrics,
            self._step_sensitivity,
            self._step_report,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # Button row
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton("Close")
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        self._btn_back = QPushButton("← Back")
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next →")
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # Connect step signals
        self._step_load.analysisFinished.connect(self._on_analysis_finished)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        # Transition logic: pass data forward
        sf = self._step_load.get_system_financials()
        tf = self._step_load.get_tech_financials()

        if self._current_step == 0 and sf:
            # Load → Cost Decomposition
            self._step_cost.set_inputs(sf)
        elif self._current_step == 1 and tf:
            # Cost Decomposition → Technology Economics
            self._step_tech.set_inputs(tf)
        elif self._current_step == 2 and sf:
            # Technology Economics → Market Analysis. Load the base-year price
            # series from the results file so the price-duration curve has data.
            from esfex.models.financial_analysis import load_price_series
            h5_path = self._step_load.get_h5_path()
            prices = None
            if h5_path:
                try:
                    series = load_price_series(h5_path)
                    prices = series if series.size else None
                except Exception:
                    logger.exception("Failed to load price series for market analysis")
            self._step_market.set_inputs(sf, prices)
        elif self._current_step == 3 and sf:
            # Market Analysis → Cash Flow
            self._step_cashflow.set_inputs(sf)
        elif self._current_step == 4 and sf:
            # Cash Flow → Investment Metrics
            self._step_metrics.set_inputs(sf)
        elif self._current_step == 5:
            # Investment Metrics → Sensitivity
            h5_path = self._step_load.get_h5_path()
            assumptions = self._step_load.get_assumptions()
            if h5_path and sf:
                self._step_sensitivity.set_inputs(h5_path, assumptions, sf)
        elif self._current_step == 6:
            # Sensitivity → Report
            self._step_report.set_inputs(
                sf=sf,
                tf=tf,
                sensitivity=self._step_sensitivity.get_sensitivity_result(),
                mc=self._step_sensitivity.get_monte_carlo_result(),
            )

        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _go_back(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _update_navigation(self):
        idx = self._current_step
        n = len(self._steps)

        # Update step indicator labels
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(self._step_style(
                is_current=(i == idx),
                is_done=(i < idx),
                is_phase_b=(i >= _PHASE_A_COUNT),
            ))

        # Update buttons
        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        if idx == n - 1:
            self._btn_next.setText("Finish")
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self.accept)
        elif idx == 0:
            # Step 1: Next only enabled after analysis completes
            self._btn_next.setText("Next →")
            self._btn_next.setEnabled(self._step_load.is_valid())
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
        else:
            self._btn_next.setText("Next →")
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
            self._btn_next.setEnabled(True)

    def _on_analysis_finished(self):
        """Enable Next button when financial analysis completes."""
        self._btn_next.setEnabled(True)

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _step_style(
        is_current: bool, is_done: bool, is_phase_b: bool = False,
    ) -> str:
        if is_current:
            color = "#e67e22" if is_phase_b else "#2980b9"
            return (
                f"background-color: {color}; color: white; "
                "border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
        if is_done:
            return (
                "background-color: #27ae60; color: white; "
                "border-radius: 4px; padding: 4px 8px;"
            )
        return (
            "background-color: #555; color: #aaa; "
            "border-radius: 4px; padding: 4px 8px;"
        )
