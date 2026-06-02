# -*- coding: utf-8 -*-
"""
Step widgets for the Financial Analysis wizard.

Eight steps organized in two phases:
Phase A — Economic Overview (steps 1-4)
Phase B — Deep Financial Analysis (steps 5-8)
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from esfex.models.financial_analysis import (
    FinancialAssumptions,
    MonteCarloResult,
    SensitivityResult,
    SystemFinancials,
    TechnologyFinancials,
    compute_system_financials,
    compute_technology_financials,
    run_monte_carlo,
    run_sensitivity_analysis,
)
from esfex.visualization.workflows.financial_charts import (
    BubbleChart,
    CostPieChart,
    CumulativeNPVChart,
    DSCRTimeline,
    IRRHistogram,
    NPVHistogram,
    SpiderPlot,
    StackedBarChart,
    TechRevenueVsCostChart,
    TornadoDiagram,
    WaterfallChart,
    PriceDurationCurve,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Background Workers
# =====================================================================


class _FinancialWorker(QThread):
    """Run compute_system_financials in background."""
    finished = Signal(object, object)  # (SystemFinancials, dict[str, TechnologyFinancials])
    error = Signal(str)

    def __init__(self, h5_path: str, assumptions: FinancialAssumptions):
        super().__init__()
        self._h5_path = h5_path
        self._assumptions = assumptions

    def run(self):
        try:
            sf = compute_system_financials(self._h5_path, self._assumptions)
            tf = compute_technology_financials(self._h5_path, self._assumptions)
            self.finished.emit(sf, tf)
        except Exception as e:
            self.error.emit(str(e))


class _SensitivityWorker(QThread):
    """Run sensitivity analysis in background."""
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, h5_path, assumptions, variables, n_points):
        super().__init__()
        self._h5_path = h5_path
        self._assumptions = assumptions
        self._variables = variables
        self._n_points = n_points

    def run(self):
        try:
            result = run_sensitivity_analysis(
                self._h5_path, self._assumptions,
                variables=self._variables, n_points=self._n_points,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _MonteCarloWorker(QThread):
    """Run Monte Carlo simulation in background."""
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, h5_path, assumptions, n_samples):
        super().__init__()
        self._h5_path = h5_path
        self._assumptions = assumptions
        self._n_samples = n_samples

    def run(self):
        try:
            result = run_monte_carlo(
                self._h5_path, self._assumptions, n_samples=self._n_samples,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# =====================================================================
# Helper: Metric Card Widget
# =====================================================================


def _pct_or_na(value, decimals: int = 1) -> str:
    """Format a fraction as a percentage, or 'N/A' when it is NaN/None.

    IRR is NaN for operational runs with no investment outflow (undefined
    return) — show N/A rather than a misleading number.
    """
    import math
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:.{decimals}%}"


def _metric_card(label: str, value: str, color: str = "#2980b9") -> QGroupBox:
    """Create a compact metric display card."""
    box = QGroupBox()
    box.setStyleSheet(
        f"QGroupBox {{ border: 2px solid {color}; border-radius: 6px; padding: 8px; }}"
    )
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 8, 8, 8)
    val_lbl = QLabel(value)
    val_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")
    val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(val_lbl)
    name_lbl = QLabel(label)
    name_lbl.setStyleSheet("font-size: 10px; color: #888;")
    name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(name_lbl)
    return box


# =====================================================================
# Step 1: Load Results & Configure Assumptions
# =====================================================================


class LoadResultsStep(QWidget):
    """HDF5 file picker + financial assumptions form + analysis trigger."""

    analysisFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h5_path: Optional[str] = None
        self._assumptions = FinancialAssumptions()
        self._system_financials: Optional[SystemFinancials] = None
        self._tech_financials: Optional[dict] = None
        self._worker: Optional[_FinancialWorker] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # File picker
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("Results HDF5:"))
        self._lbl_path = QLabel("(no file selected)")
        self._lbl_path.setStyleSheet("color: #888;")
        file_row.addWidget(self._lbl_path, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse)
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        # Scrollable 2-column assumptions form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        columns = QHBoxLayout(form_widget)

        # ── Left column ──
        left = QVBoxLayout()

        cap_group = QGroupBox("Capital Structure")
        cap_form = QFormLayout(cap_group)
        self._spin_debt_frac = self._pct_spin(0.60)
        cap_form.addRow("Debt fraction:", self._spin_debt_frac)
        self._spin_cost_debt = self._pct_spin(0.05)
        cap_form.addRow("Cost of debt:", self._spin_cost_debt)
        self._spin_cost_equity = self._pct_spin(0.12)
        cap_form.addRow("Cost of equity:", self._spin_cost_equity)
        self._spin_debt_tenor = QSpinBox()
        self._spin_debt_tenor.setRange(1, 40)
        self._spin_debt_tenor.setValue(15)
        cap_form.addRow("Debt tenor (years):", self._spin_debt_tenor)
        left.addWidget(cap_group)

        tax_group = QGroupBox("Tax & Depreciation")
        tax_form = QFormLayout(tax_group)
        self._spin_tax_rate = self._pct_spin(0.25)
        tax_form.addRow("Tax rate:", self._spin_tax_rate)
        self._combo_depreciation = QComboBox()
        self._combo_depreciation.addItems(["straight_line", "macrs"])
        tax_form.addRow("Depreciation:", self._combo_depreciation)
        self._spin_dep_years = QSpinBox()
        self._spin_dep_years.setRange(1, 40)
        self._spin_dep_years.setValue(20)
        tax_form.addRow("Depreciation years:", self._spin_dep_years)
        self._spin_itc = self._pct_spin(0.0)
        tax_form.addRow("ITC rate:", self._spin_itc)
        self._spin_ptc = QDoubleSpinBox()
        self._spin_ptc.setRange(0, 100)
        self._spin_ptc.setDecimals(2)
        self._spin_ptc.setSuffix(" $/MWh")
        tax_form.addRow("PTC rate:", self._spin_ptc)
        left.addWidget(tax_group)

        env_group = QGroupBox("Environmental")
        env_form = QFormLayout(env_group)
        self._spin_carbon = QDoubleSpinBox()
        self._spin_carbon.setRange(0, 500)
        self._spin_carbon.setDecimals(1)
        self._spin_carbon.setSuffix(" $/tCO2")
        env_form.addRow("Carbon price:", self._spin_carbon)
        self._spin_carbon_esc = self._pct_spin(0.02)
        env_form.addRow("Carbon price escalation:", self._spin_carbon_esc)
        left.addWidget(env_group)

        left.addStretch()
        columns.addLayout(left)

        # ── Right column ──
        right = QVBoxLayout()

        rev_group = QGroupBox("Revenue Assumptions")
        rev_form = QFormLayout(rev_group)
        self._spin_ppa = QDoubleSpinBox()
        self._spin_ppa.setRange(0, 500)
        self._spin_ppa.setDecimals(1)
        self._spin_ppa.setSuffix(" $/MWh")
        self._spin_ppa.setToolTip("0 = use nodal prices from optimization results")
        rev_form.addRow("PPA price (0=nodal):", self._spin_ppa)
        self._spin_ppa_esc = self._pct_spin(0.02)
        rev_form.addRow("PPA escalation:", self._spin_ppa_esc)
        self._spin_cap_pay = QDoubleSpinBox()
        self._spin_cap_pay.setRange(0, 500000)
        self._spin_cap_pay.setDecimals(0)
        self._spin_cap_pay.setSuffix(" $/MW-yr")
        rev_form.addRow("Capacity payment:", self._spin_cap_pay)
        self._spin_rec = QDoubleSpinBox()
        self._spin_rec.setRange(0, 200)
        self._spin_rec.setDecimals(1)
        self._spin_rec.setSuffix(" $/MWh")
        rev_form.addRow("REC price:", self._spin_rec)
        right.addWidget(rev_group)

        other_group = QGroupBox("Other")
        other_form = QFormLayout(other_group)
        self._spin_insurance = self._pct_spin(0.005)
        other_form.addRow("Insurance rate:", self._spin_insurance)
        self._spin_salvage = self._pct_spin(0.05)
        other_form.addRow("Salvage fraction:", self._spin_salvage)
        self._spin_discount = self._pct_spin(0.08)
        other_form.addRow("Discount rate:", self._spin_discount)
        right.addWidget(other_group)

        right.addStretch()
        columns.addLayout(right)

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)

        # Analysis button + progress
        btn_row = QHBoxLayout()
        self._btn_analyze = QPushButton("Load && Analyze")
        self._btn_analyze.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; "
            "padding: 8px 16px; border-radius: 4px;"
        )
        self._btn_analyze.clicked.connect(self._run_analysis)
        btn_row.addWidget(self._btn_analyze)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        btn_row.addWidget(self._progress)
        self._lbl_status = QLabel("")
        btn_row.addWidget(self._lbl_status)
        layout.addLayout(btn_row)

    def _pct_spin(self, default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 1.0)
        spin.setDecimals(4)
        spin.setSingleStep(0.005)
        spin.setValue(default)
        return spin

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Results HDF5", "", "HDF5 Files (*.h5 *.hdf5);;All Files (*)",
        )
        if path:
            self._h5_path = path
            self._lbl_path.setText(Path(path).name)
            self._lbl_path.setStyleSheet("color: #2ecc71; font-weight: bold;")

    def _collect_assumptions(self) -> FinancialAssumptions:
        return FinancialAssumptions(
            debt_fraction=self._spin_debt_frac.value(),
            cost_of_debt=self._spin_cost_debt.value(),
            cost_of_equity=self._spin_cost_equity.value(),
            debt_tenor=self._spin_debt_tenor.value(),
            tax_rate=self._spin_tax_rate.value(),
            depreciation_method=self._combo_depreciation.currentText(),
            depreciation_years=self._spin_dep_years.value(),
            itc_rate=self._spin_itc.value(),
            ptc_rate=self._spin_ptc.value(),
            ppa_price=self._spin_ppa.value(),
            ppa_escalation=self._spin_ppa_esc.value(),
            capacity_payment=self._spin_cap_pay.value(),
            rec_price=self._spin_rec.value(),
            carbon_price=self._spin_carbon.value(),
            carbon_price_escalation=self._spin_carbon_esc.value(),
            insurance_rate=self._spin_insurance.value(),
            salvage_fraction=self._spin_salvage.value(),
            discount_rate=self._spin_discount.value(),
        )

    def _run_analysis(self):
        if not self._h5_path:
            QMessageBox.warning(self, "No File", "Please select an HDF5 results file first.")
            return
        self._assumptions = self._collect_assumptions()
        self._btn_analyze.setEnabled(False)
        self._progress.setVisible(True)
        self._lbl_status.setText("Analyzing...")

        self._worker = _FinancialWorker(self._h5_path, self._assumptions)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, sf, tf):
        self._system_financials = sf
        self._tech_financials = tf
        self._progress.setVisible(False)
        self._btn_analyze.setEnabled(True)
        self._lbl_status.setText("Analysis complete!")
        self._lbl_status.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.analysisFinished.emit()

    def _on_error(self, msg):
        self._progress.setVisible(False)
        self._btn_analyze.setEnabled(True)
        self._lbl_status.setText(f"Error: {msg}")
        self._lbl_status.setStyleSheet("color: #e74c3c;")
        QMessageBox.critical(self, "Analysis Error", msg)

    def is_valid(self) -> bool:
        return self._system_financials is not None

    def get_h5_path(self) -> Optional[str]:
        return self._h5_path

    def get_assumptions(self) -> FinancialAssumptions:
        return self._assumptions

    def get_system_financials(self) -> Optional[SystemFinancials]:
        return self._system_financials

    def get_tech_financials(self) -> Optional[dict]:
        return self._tech_financials


# =====================================================================
# Step 2: Cost Decomposition
# =====================================================================


class CostDecompositionStep(QWidget):
    """NPV waterfall, annual stacked bar, and pie chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()

        self._waterfall = WaterfallChart()
        self._tabs.addTab(self._waterfall, "NPV Waterfall")

        self._stacked = StackedBarChart()
        self._tabs.addTab(self._stacked, "Annual Cash Flows")

        self._pie = CostPieChart()
        self._tabs.addTab(self._pie, "Cost Breakdown")

        layout.addWidget(self._tabs)

    def set_inputs(self, sf: SystemFinancials):
        # Waterfall — components must include Tax so the running sum lands on
        # the Net NPV total bar (see WaterfallChart.update_chart).
        labels = [
            "Revenue", "Fuel", "O&M", "CAPEX", "Penalties",
            "Tax", "Tax Benefits", "Salvage", "Net NPV",
        ]
        values = [
            sf.npv_revenue, -sf.npv_fuel, -sf.npv_om, -sf.npv_capex,
            -sf.npv_penalties, -sf.npv_tax, sf.npv_tax_benefits,
            sf.npv_salvage, sf.npv_total,
        ]
        self._waterfall.update_chart(labels, values)

        # Stacked bar
        self._stacked.update_chart(sf.cash_flows)

        # Pie
        pie_labels = ["Fuel", "O&M", "CAPEX", "Penalties"]
        pie_values = [sf.npv_fuel, sf.npv_om, sf.npv_capex, sf.npv_penalties]
        self._pie.update_chart(pie_labels, pie_values, title="NPV Cost Composition")

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 3: Technology Economics
# =====================================================================


class TechnologyEconomicsStep(QWidget):
    """Per-technology metrics table, bubble chart, revenue vs cost bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()

        # Table tab
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "Technology", "Type", "Installed MW", "Generation GWh",
            "CF %", "LCOE $/MWh", "Revenue $M", "ROI %",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._table, "Metrics Table")

        # Bubble chart tab
        self._bubble = BubbleChart()
        self._tabs.addTab(self._bubble, "CF vs LCOE")

        # Revenue vs Cost tab
        self._rev_cost = TechRevenueVsCostChart()
        self._tabs.addTab(self._rev_cost, "Revenue vs Cost")

        layout.addWidget(self._tabs)

    def set_inputs(self, tech_financials: dict[str, TechnologyFinancials]):
        # Table
        items = list(tech_financials.values())
        self._table.setRowCount(len(items))
        for i, tf in enumerate(items):
            self._table.setItem(i, 0, QTableWidgetItem(tf.name))
            self._table.setItem(i, 1, QTableWidgetItem(tf.tech_type))
            self._table.setItem(i, 2, QTableWidgetItem(f"{tf.installed_mw:.1f}"))
            self._table.setItem(i, 3, QTableWidgetItem(f"{tf.generation_mwh/1e3:.1f}"))
            self._table.setItem(i, 4, QTableWidgetItem(f"{tf.capacity_factor*100:.1f}"))
            lcoe_str = f"{tf.lcoe:.1f}" if tf.lcoe < 1000 else "N/A"
            self._table.setItem(i, 5, QTableWidgetItem(lcoe_str))
            self._table.setItem(i, 6, QTableWidgetItem(f"{tf.revenue_total/1e6:.2f}"))
            self._table.setItem(i, 7, QTableWidgetItem(f"{tf.roi*100:.1f}"))

        # Bubble chart
        self._bubble.update_chart(tech_financials)

        # Revenue vs Cost
        self._rev_cost.update_chart(tech_financials)

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 4: Market Analysis
# =====================================================================


class MarketAnalysisStep(QWidget):
    """Price duration curve and revenue breakdown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()

        self._price_curve = PriceDurationCurve()
        self._tabs.addTab(self._price_curve, "Price Duration Curve")

        self._cum_npv = CumulativeNPVChart()
        self._tabs.addTab(self._cum_npv, "Cumulative NPV")

        layout.addWidget(self._tabs)

    def set_inputs(self, sf: SystemFinancials, prices: Optional[np.ndarray] = None):
        if prices is not None:
            self._price_curve.update_chart(prices)
        self._cum_npv.update_chart(sf.cash_flows)

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 5: Cash Flow Pro Forma
# =====================================================================


class CashFlowStep(QWidget):
    """Pro forma table + cumulative NPV chart + DSCR timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        self._table = QTableWidget()
        splitter.addWidget(self._table)

        # Charts
        chart_tabs = QTabWidget()
        self._cum_chart = CumulativeNPVChart()
        chart_tabs.addTab(self._cum_chart, "Cumulative NPV")
        self._dscr_chart = DSCRTimeline()
        chart_tabs.addTab(self._dscr_chart, "DSCR")
        splitter.addWidget(chart_tabs)

        splitter.setSizes([300, 300])
        layout.addWidget(splitter)

    def set_inputs(self, sf: SystemFinancials):
        cf = sf.cash_flows
        if cf.empty:
            return

        # Table
        cols = list(cf.columns)
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setRowCount(len(cf))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        for r in range(len(cf)):
            for c, col in enumerate(cols):
                val = cf.iloc[r][col]
                if isinstance(val, float):
                    text = f"{val:,.0f}" if abs(val) > 100 else f"{val:.3f}"
                else:
                    text = str(val)
                self._table.setItem(r, c, QTableWidgetItem(text))

        # Charts
        self._cum_chart.update_chart(cf)
        years = cf["year"].tolist()
        self._dscr_chart.update_chart(years, sf.dscr_annual)

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 6: Investment Metrics Dashboard
# =====================================================================


class InvestmentMetricsStep(QWidget):
    """Large-font metric cards for key financial indicators."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Key Investment Metrics</b>"
        ))

        self._cards_layout = QHBoxLayout()
        layout.addLayout(self._cards_layout)

        self._cards_layout_2 = QHBoxLayout()
        layout.addLayout(self._cards_layout_2)

        layout.addWidget(QLabel(
            "<b>New-Investment Economics</b> "
            "<span style='color:#888;'>(built capacity only — excludes "
            "revenue from existing sunk-cost plants)</span>"
        ))
        self._cards_layout_3 = QHBoxLayout()
        layout.addLayout(self._cards_layout_3)

        layout.addStretch()

    def set_inputs(self, sf: SystemFinancials):
        # Clear existing cards
        for lay in (self._cards_layout, self._cards_layout_2, self._cards_layout_3):
            while lay.count():
                item = lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        # Row 1
        self._cards_layout.addWidget(
            _metric_card("NPV", f"${sf.npv_total/1e6:,.1f}M",
                         "#27ae60" if sf.npv_total > 0 else "#e74c3c")
        )
        self._cards_layout.addWidget(
            _metric_card("Project IRR", _pct_or_na(sf.project_irr), "#2980b9")
        )
        self._cards_layout.addWidget(
            _metric_card("Equity IRR", _pct_or_na(sf.equity_irr), "#16a085")
        )
        self._cards_layout.addWidget(
            _metric_card("MIRR", _pct_or_na(sf.mirr), "#8e44ad")
        )

        # Row 2
        pb_str = f"{sf.payback_simple:.1f} yr" if sf.payback_simple < 100 else "N/A"
        self._cards_layout_2.addWidget(_metric_card("Simple Payback", pb_str, "#e67e22"))

        dpb_str = f"{sf.payback_discounted:.1f} yr" if sf.payback_discounted < 100 else "N/A"
        self._cards_layout_2.addWidget(_metric_card("Discounted Payback", dpb_str, "#f39c12"))

        self._cards_layout_2.addWidget(
            _metric_card("WACC", f"{sf.wacc:.2%}", "#2c3e50")
        )

        dscr_str = f"{sf.dscr_min:.2f}×" if sf.dscr_min < 100 else "∞"
        dscr_color = "#27ae60" if sf.dscr_min >= 1.2 else "#e74c3c"
        self._cards_layout_2.addWidget(_metric_card("Min DSCR", dscr_str, dscr_color))

        self._cards_layout_2.addWidget(
            _metric_card("System LCOE", f"${sf.lcoe_system:.1f}/MWh", "#2980b9")
        )

        # Row 3 — new-investment economics
        self._cards_layout_3.addWidget(
            _metric_card("Investment NPV", f"${sf.investment_npv/1e6:,.1f}M",
                         "#27ae60" if sf.investment_npv > 0 else "#e74c3c")
        )
        self._cards_layout_3.addWidget(
            _metric_card("Investment IRR", _pct_or_na(sf.investment_irr), "#2980b9")
        )
        inv_pb = (f"{sf.investment_payback:.1f} yr"
                  if sf.investment_payback < 100 else "N/A")
        self._cards_layout_3.addWidget(
            _metric_card("Investment Payback", inv_pb, "#e67e22")
        )
        self._cards_layout_3.addWidget(
            _metric_card("Investment CAPEX", f"${sf.investment_capex/1e6:,.1f}M",
                         "#2c3e50")
        )
        inv_lcoe = (f"${sf.investment_lcoe:.1f}/MWh"
                    if sf.investment_lcoe < 1e4 else "N/A")
        self._cards_layout_3.addWidget(
            _metric_card("Investment LCOE", inv_lcoe, "#16a085")
        )

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 7: Sensitivity & Monte Carlo
# =====================================================================


class SensitivityStep(QWidget):
    """Sensitivity variable selection + tornado/spider + Monte Carlo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h5_path: Optional[str] = None
        self._assumptions: Optional[FinancialAssumptions] = None
        self._sf: Optional[SystemFinancials] = None
        self._sensitivity_result: Optional[SensitivityResult] = None
        self._mc_result: Optional[MonteCarloResult] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Controls row
        controls = QHBoxLayout()

        # Variable checkboxes
        var_group = QGroupBox("Sensitivity Variables")
        var_lay = QVBoxLayout(var_group)
        self._var_checks: dict[str, QCheckBox] = {}
        default_vars = ["discount_rate", "ppa_price", "carbon_price",
                        "debt_fraction", "cost_of_debt", "tax_rate"]
        for var in default_vars:
            cb = QCheckBox(var.replace("_", " ").title())
            cb.setChecked(True)
            cb.setProperty("var_name", var)
            self._var_checks[var] = cb
            var_lay.addWidget(cb)
        controls.addWidget(var_group)

        # Run buttons
        btn_col = QVBoxLayout()
        self._spin_points = QSpinBox()
        self._spin_points.setRange(3, 21)
        self._spin_points.setValue(7)
        btn_col.addWidget(QLabel("Sweep points:"))
        btn_col.addWidget(self._spin_points)

        self._btn_sensitivity = QPushButton("Run Sensitivity")
        self._btn_sensitivity.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 6px;"
        )
        self._btn_sensitivity.clicked.connect(self._run_sensitivity)
        btn_col.addWidget(self._btn_sensitivity)

        btn_col.addSpacing(20)
        self._spin_mc_samples = QSpinBox()
        self._spin_mc_samples.setRange(50, 10000)
        self._spin_mc_samples.setValue(500)
        self._spin_mc_samples.setSingleStep(100)
        btn_col.addWidget(QLabel("Monte Carlo samples:"))
        btn_col.addWidget(self._spin_mc_samples)

        self._btn_mc = QPushButton("Run Monte Carlo")
        self._btn_mc.setStyleSheet(
            "background-color: #e67e22; color: white; font-weight: bold; padding: 6px;"
        )
        self._btn_mc.clicked.connect(self._run_monte_carlo)
        btn_col.addWidget(self._btn_mc)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        btn_col.addWidget(self._progress)
        btn_col.addStretch()
        controls.addLayout(btn_col)
        layout.addLayout(controls)

        # Charts
        self._chart_tabs = QTabWidget()
        self._tornado = TornadoDiagram()
        self._chart_tabs.addTab(self._tornado, "Tornado")
        self._spider = SpiderPlot()
        self._chart_tabs.addTab(self._spider, "Spider Plot")
        self._npv_hist = NPVHistogram()
        self._chart_tabs.addTab(self._npv_hist, "NPV Distribution")
        self._irr_hist = IRRHistogram()
        self._chart_tabs.addTab(self._irr_hist, "IRR Distribution")
        layout.addWidget(self._chart_tabs, 1)

    def set_inputs(self, h5_path: str, assumptions: FinancialAssumptions, sf: SystemFinancials):
        self._h5_path = h5_path
        self._assumptions = assumptions
        self._sf = sf

    def _get_selected_vars(self) -> list[str]:
        return [
            cb.property("var_name")
            for cb in self._var_checks.values()
            if cb.isChecked()
        ]

    def _run_sensitivity(self):
        if not self._h5_path or not self._assumptions:
            return
        variables = self._get_selected_vars()
        if not variables:
            QMessageBox.warning(self, "No Variables", "Select at least one variable.")
            return

        self._progress.setVisible(True)
        self._btn_sensitivity.setEnabled(False)
        self._worker = _SensitivityWorker(
            self._h5_path, self._assumptions, variables, self._spin_points.value(),
        )
        self._worker.finished.connect(self._on_sensitivity_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_sensitivity_done(self, result: SensitivityResult):
        self._sensitivity_result = result
        self._progress.setVisible(False)
        self._btn_sensitivity.setEnabled(True)
        self._tornado.update_chart(result.tornado, result.base_npv)
        self._spider.update_chart(result.sweeps, result.base_npv)
        self._chart_tabs.setCurrentIndex(0)

    def _run_monte_carlo(self):
        if not self._h5_path or not self._assumptions:
            return
        self._progress.setVisible(True)
        self._btn_mc.setEnabled(False)
        self._mc_worker = _MonteCarloWorker(
            self._h5_path, self._assumptions, self._spin_mc_samples.value(),
        )
        self._mc_worker.finished.connect(self._on_mc_done)
        self._mc_worker.error.connect(self._on_worker_error)
        self._mc_worker.start()

    def _on_mc_done(self, result: MonteCarloResult):
        self._mc_result = result
        self._progress.setVisible(False)
        self._btn_mc.setEnabled(True)
        self._npv_hist.update_chart(result.npv_samples, result.npv_var_5, result.npv_cvar_5)
        wacc = self._sf.wacc if self._sf else 0.0
        self._irr_hist.update_chart(result.irr_samples, wacc)
        self._chart_tabs.setCurrentIndex(2)

    def _on_worker_error(self, msg):
        self._progress.setVisible(False)
        self._btn_sensitivity.setEnabled(True)
        self._btn_mc.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)

    def is_valid(self) -> bool:
        return True

    def get_sensitivity_result(self) -> Optional[SensitivityResult]:
        return self._sensitivity_result

    def get_monte_carlo_result(self) -> Optional[MonteCarloResult]:
        return self._mc_result


# =====================================================================
# Step 8: Report & Export
# =====================================================================


class ReportStep(QWidget):
    """Auto-generated executive summary with export options."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sf: Optional[SystemFinancials] = None
        self._tf: Optional[dict] = None
        self._sensitivity: Optional[SensitivityResult] = None
        self._mc: Optional[MonteCarloResult] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Executive Summary & Export</b>"))

        self._report = QTextEdit()
        self._report.setReadOnly(True)
        self._report.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._report, 1)

        # Export buttons
        btn_row = QHBoxLayout()
        btn_csv = QPushButton("Export CSV")
        btn_csv.clicked.connect(self._export_csv)
        btn_row.addWidget(btn_csv)
        btn_txt = QPushButton("Export Report (TXT)")
        btn_txt.clicked.connect(self._export_txt)
        btn_row.addWidget(btn_txt)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def set_inputs(
        self,
        sf: SystemFinancials,
        tf: Optional[dict] = None,
        sensitivity: Optional[SensitivityResult] = None,
        mc: Optional[MonteCarloResult] = None,
    ):
        self._sf = sf
        self._tf = tf
        self._sensitivity = sensitivity
        self._mc = mc
        self._generate_report()

    def _generate_report(self):
        sf = self._sf
        if sf is None:
            return

        lines = []
        lines.append("=" * 60)
        lines.append("  FINANCIAL ANALYSIS — EXECUTIVE SUMMARY")
        lines.append("=" * 60)
        lines.append("")

        lines.append("--- KEY METRICS ---")
        lines.append(f"  NPV:                ${sf.npv_total:>15,.0f}")
        lines.append(f"  Project IRR:        {_pct_or_na(sf.project_irr, 2):>15}")
        lines.append(f"  Equity IRR:         {_pct_or_na(sf.equity_irr, 2):>15}")
        lines.append(f"  MIRR:               {_pct_or_na(sf.mirr, 2):>15}")
        lines.append(f"  WACC:               {sf.wacc:>15.2%}")
        pb = f"{sf.payback_simple:.1f} years" if sf.payback_simple < 100 else "N/A"
        lines.append(f"  Simple Payback:     {pb:>15}")
        dpb = f"{sf.payback_discounted:.1f} years" if sf.payback_discounted < 100 else "N/A"
        lines.append(f"  Discounted Payback: {dpb:>15}")
        lines.append(f"  System LCOE:        ${sf.lcoe_system:>14.2f}/MWh")
        dscr = f"{sf.dscr_min:.2f}×" if sf.dscr_min < 100 else "∞"
        lines.append(f"  Min DSCR:           {dscr:>15}")
        lines.append(f"  LLCR:               {sf.llcr:>15.2f}")
        lines.append(f"  Profitability Idx:  {sf.profitability_index:>15.2f}")
        lines.append("")

        lines.append("--- NEW-INVESTMENT ECONOMICS (built capacity only) ---")
        lines.append(f"  Investment NPV:     ${sf.investment_npv:>15,.0f}")
        lines.append(f"  Investment IRR:     {_pct_or_na(sf.investment_irr, 2):>15}")
        inv_pb = (f"{sf.investment_payback:.1f} years"
                  if sf.investment_payback < 100 else "N/A")
        lines.append(f"  Investment Payback: {inv_pb:>15}")
        lines.append(f"  Investment CAPEX:   ${sf.investment_capex:>15,.0f}")
        inv_lcoe = (f"${sf.investment_lcoe:.2f}/MWh"
                    if sf.investment_lcoe < 1e4 else "N/A")
        lines.append(f"  Investment LCOE:    {inv_lcoe:>15}")
        lines.append("")

        lines.append("--- NPV DECOMPOSITION ---")
        lines.append(f"  Revenue:            ${sf.npv_revenue:>15,.0f}")
        lines.append(f"  Fuel Cost:          ${sf.npv_fuel:>15,.0f}")
        lines.append(f"  O&M Cost:           ${sf.npv_om:>15,.0f}")
        lines.append(f"  CAPEX:              ${sf.npv_capex:>15,.0f}")
        lines.append(f"  Penalties:          ${sf.npv_penalties:>15,.0f}")
        lines.append(f"  Tax Benefits:       ${sf.npv_tax_benefits:>15,.0f}")
        lines.append(f"  Salvage:            ${sf.npv_salvage:>15,.0f}")
        lines.append(f"  Net NPV:            ${sf.npv_total:>15,.0f}")
        lines.append("")

        if self._tf:
            lines.append("--- TECHNOLOGY ECONOMICS ---")
            lines.append(f"  {'Technology':<20} {'MW':>8} {'GWh':>8} {'CF%':>6} {'LCOE':>8} {'ROI%':>6}")
            lines.append("  " + "-" * 58)
            for name, tf in self._tf.items():
                lcoe_s = f"{tf.lcoe:.1f}" if tf.lcoe < 1000 else "N/A"
                lines.append(
                    f"  {name:<20} {tf.installed_mw:>8.1f} "
                    f"{tf.generation_mwh/1e3:>8.1f} {tf.capacity_factor*100:>5.1f} "
                    f"{lcoe_s:>8} {tf.roi*100:>5.1f}"
                )
            lines.append("")

        if self._mc:
            lines.append("--- MONTE CARLO RESULTS ---")
            lines.append(f"  Samples:            {self._mc.n_samples:>15,}")
            lines.append(f"  NPV Mean:           ${self._mc.npv_mean:>15,.0f}")
            lines.append(f"  NPV Std Dev:        ${self._mc.npv_std:>15,.0f}")
            lines.append(f"  NPV 5th %ile:       ${self._mc.npv_p5:>15,.0f}")
            lines.append(f"  NPV 95th %ile:      ${self._mc.npv_p95:>15,.0f}")
            lines.append(f"  VaR (5%):           ${self._mc.npv_var_5:>15,.0f}")
            lines.append(f"  CVaR (5%):          ${self._mc.npv_cvar_5:>15,.0f}")
            lines.append(f"  IRR Mean:           {_pct_or_na(self._mc.irr_mean, 2):>15}")
            lines.append("")

        lines.append("=" * 60)
        self._report.setPlainText("\n".join(lines))

    def _export_csv(self):
        if self._sf is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Cash Flows CSV", "financial_analysis.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if path:
            self._sf.cash_flows.to_csv(path, index=False, float_format="%.2f")
            QMessageBox.information(self, "Export", f"Saved to {path}")

    def _export_txt(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Report", "financial_report.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if path:
            with open(path, "w") as f:
                f.write(self._report.toPlainText())
            QMessageBox.information(self, "Export", f"Saved to {path}")

    def is_valid(self) -> bool:
        return True
