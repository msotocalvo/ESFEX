"""Phase B step widgets for the Wind Resource Assessment wizard.

Step 6: Wind Characterization (Weibull, wind rose, diurnal, seasonal)
Step 7: Financial Analysis (LCOE, NPV, IRR, sensitivity)
Step 8: Wake Effect Modeling (Jensen/Park, array efficiency)
Step 9: Availability Profile Generation (hourly CF for model generators)
"""

from __future__ import annotations

import csv
import logging
from typing import Any

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

logger = logging.getLogger(__name__)


def _white_chart_style(fig, ax):
    """Apply consistent white-background chart style."""
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.tick_params(colors="black", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#ccc")
    ax.grid(True, alpha=0.3, color="gray")


# =====================================================================
# Step 6: Wind Characterization
# =====================================================================


class WindCharacterizationStep(QWidget):
    """Weibull fit, wind rose, diurnal/seasonal patterns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hourly_data = None  # dict[(lat,lon)] -> HourlyWindData
        self._summary = None
        self._wind_rose_data = None
        self._stats = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Cell selector
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel(tr("wizard_wind.select_cell")))
        self._cell_combo = QComboBox()
        self._cell_combo.addItem(tr("wizard_wind.all_cells"), "all")
        self._cell_combo.currentIndexChanged.connect(self._on_cell_changed)
        sel_row.addWidget(self._cell_combo, 1)

        self._btn_export = QPushButton(tr("wizard_wind.export_charts"))
        self._btn_export.clicked.connect(self._export_charts)
        sel_row.addWidget(self._btn_export)

        layout.addLayout(sel_row)

        # Statistics panel
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "background-color: #f8f8f8; border: 1px solid #ddd; "
            "border-radius: 4px; padding: 8px; color: black;"
        )
        self._stats_label.setWordWrap(True)
        layout.addWidget(self._stats_label)

        # Chart area
        self._chart_widget = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_widget)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chart_widget, 1)

    def set_inputs(self, hourly_data, summary):
        """Receive hourly wind data and analysis summary from Phase A."""
        self._hourly_data = hourly_data
        self._summary = summary

        # Populate cell selector
        self._cell_combo.blockSignals(True)
        self._cell_combo.clear()
        self._cell_combo.addItem(tr("wizard_wind.all_cells"), "all")
        if hourly_data:
            for (lat, lon) in sorted(hourly_data.keys()):
                self._cell_combo.addItem(f"({lat:.3f}, {lon:.3f})", (lat, lon))
        self._cell_combo.blockSignals(False)
        self._on_cell_changed()

    def _get_selected_data(self):
        """Get aggregated or single-cell wind speed/direction arrays."""
        import numpy as np

        if not self._hourly_data:
            return None, None, None

        idx = self._cell_combo.currentIndex()
        data_key = self._cell_combo.itemData(idx)

        if data_key == "all":
            # Aggregate all cells
            all_ws = []
            all_wd = []
            timestamps = None
            for hd in self._hourly_data.values():
                if hd.wind_speed is not None:
                    all_ws.append(np.asarray(hd.wind_speed))
                if hd.wind_direction is not None:
                    all_wd.append(np.asarray(hd.wind_direction))
                if timestamps is None:
                    timestamps = hd.timestamps
            ws = np.concatenate(all_ws) if all_ws else None
            wd = np.concatenate(all_wd) if all_wd else None
            return ws, wd, timestamps
        else:
            hd = self._hourly_data.get(data_key)
            if hd is None:
                return None, None, None
            return (
                np.asarray(hd.wind_speed) if hd.wind_speed is not None else None,
                np.asarray(hd.wind_direction) if hd.wind_direction is not None else None,
                hd.timestamps,
            )

    def _on_cell_changed(self):
        """Recompute statistics and redraw charts."""
        import numpy as np

        from windrex import (
            compute_diurnal_pattern,
            compute_seasonal_pattern,
            compute_wind_rose,
            fit_weibull,
            weibull_mean_power_density,
            weibull_pdf,
        )

        ws, wd, timestamps = self._get_selected_data()
        if ws is None or len(ws) == 0:
            self._stats_label.setText(tr("wizard_wind.char_no_data"))
            return

        # Fit Weibull
        k, A = fit_weibull(ws)
        mean_speed = float(np.mean(ws))
        power_density = weibull_mean_power_density(k, A)

        # Wind rose
        if wd is not None and len(wd) == len(ws):
            self._wind_rose_data = compute_wind_rose(ws, wd)
            dominant_idx = int(np.argmax(self._wind_rose_data.frequencies))
            dominant_dir = self._wind_rose_data.sectors[dominant_idx]
        else:
            self._wind_rose_data = None
            dominant_dir = None

        self._stats = {
            "k": k, "A": A,
            "mean_speed": mean_speed,
            "power_density": power_density,
            "dominant_dir": dominant_dir,
        }

        # Update stats label
        lines = [
            f"<b>{tr('wizard_wind.weibull_params')}:</b> k = {k:.2f}, A = {A:.2f} m/s",
            f"<b>{tr('wizard_wind.mean_speed')}:</b> {mean_speed:.2f} m/s",
            f"<b>{tr('wizard_wind.power_density')}:</b> {power_density:.0f} W/m\u00b2",
        ]
        if dominant_dir is not None:
            lines.append(
                f"<b>{tr('wizard_wind.dominant_dir')}:</b> {dominant_dir:.0f}\u00b0"
            )
        self._stats_label.setText("<br>".join(lines))

        # Build 2x2 charts
        self._build_charts(ws, wd, timestamps, k, A)

    def _build_charts(self, ws, wd, timestamps, k, A):
        """Create 2x2 matplotlib chart grid."""
        import numpy as np

        from windrex import (
            compute_diurnal_pattern,
            compute_seasonal_pattern,
            weibull_pdf,
        )

        # Clear old charts
        for i in reversed(range(self._chart_layout.count())):
            w = self._chart_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_layout.addWidget(QLabel("matplotlib not available"))
            return

        fig = Figure(figsize=(10, 7))
        fig.patch.set_facecolor("white")

        # 1. Weibull histogram
        ax1 = fig.add_subplot(2, 2, 1)
        _white_chart_style(fig, ax1)
        bins = np.linspace(0, max(ws.max(), 25), 40)
        ax1.hist(ws, bins=bins, density=True, alpha=0.6, color="#3498db",
                 edgecolor="white", linewidth=0.5)
        x_pdf = np.linspace(0, bins[-1], 200)
        ax1.plot(x_pdf, weibull_pdf(x_pdf, k, A), "r-", linewidth=2,
                 label=f"Weibull (k={k:.2f}, A={A:.2f})")
        ax1.set_xlabel("Wind Speed (m/s)", color="black")
        ax1.set_ylabel("Probability Density", color="black")
        ax1.set_title(tr("wizard_wind.weibull_params"), color="black", fontsize=10)
        ax1.legend(fontsize=8, facecolor="white", edgecolor="#ccc", labelcolor="black")

        # 2. Wind rose (polar)
        if self._wind_rose_data is not None:
            ax2 = fig.add_subplot(2, 2, 2, projection="polar")
            ax2.set_facecolor("white")
            wr = self._wind_rose_data
            n_sectors = len(wr.sectors)
            theta = np.radians(wr.sectors)
            width = 2 * np.pi / n_sectors * 0.85
            # Color by mean speed
            max_sp = max(wr.mean_speeds.max(), 1)
            colors = [
                (0.2, 0.4 + 0.6 * s / max_sp, 0.9)
                for s in wr.mean_speeds
            ]
            ax2.bar(theta, wr.frequencies * 100, width=width, bottom=0,
                    color=colors, edgecolor="white", linewidth=0.5, alpha=0.85)
            ax2.set_theta_zero_location("N")
            ax2.set_theta_direction(-1)
            ax2.set_title(tr("wizard_wind.wind_rose"), color="black",
                          fontsize=10, pad=15)
            ax2.tick_params(colors="black", labelsize=7)
        else:
            ax2 = fig.add_subplot(2, 2, 2)
            _white_chart_style(fig, ax2)
            ax2.text(0.5, 0.5, tr("wizard_wind.char_no_direction"),
                     ha="center", va="center", transform=ax2.transAxes,
                     color="black")
            ax2.set_title(tr("wizard_wind.wind_rose"), color="black", fontsize=10)

        # 3. Diurnal pattern
        ax3 = fig.add_subplot(2, 2, 3)
        _white_chart_style(fig, ax3)
        if timestamps:
            # Use first cell's timestamps for temporal analysis
            first_key = next(iter(self._hourly_data)) if self._hourly_data else None
            if first_key:
                hd = self._hourly_data[first_key]
                hours, hourly_means = compute_diurnal_pattern(
                    np.asarray(hd.wind_speed), hd.timestamps,
                )
                ax3.plot(hours, hourly_means, "o-", color="#e67e22",
                         markersize=4, linewidth=1.5)
                ax3.set_xlabel("Hour of Day", color="black")
                ax3.set_ylabel("Mean Wind Speed (m/s)", color="black")
                ax3.set_xlim(-0.5, 23.5)
                ax3.set_xticks(range(0, 24, 3))
        ax3.set_title(tr("wizard_wind.diurnal"), color="black", fontsize=10)

        # 4. Seasonal pattern
        ax4 = fig.add_subplot(2, 2, 4)
        _white_chart_style(fig, ax4)
        if timestamps:
            first_key = next(iter(self._hourly_data)) if self._hourly_data else None
            if first_key:
                hd = self._hourly_data[first_key]
                months, monthly_means = compute_seasonal_pattern(
                    np.asarray(hd.wind_speed), hd.timestamps,
                )
                month_labels = [
                    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                ]
                ax4.bar(months, monthly_means, color="#2ecc71", edgecolor="white",
                        alpha=0.85)
                ax4.set_xticks(months)
                ax4.set_xticklabels(month_labels, fontsize=7)
                ax4.set_xlabel("Month", color="black")
                ax4.set_ylabel("Mean Wind Speed (m/s)", color="black")
        ax4.set_title(tr("wizard_wind.seasonal"), color="black", fontsize=10)

        fig.tight_layout()
        canvas = FigureCanvasQTAgg(fig)
        self._chart_layout.addWidget(canvas)
        self._canvas = canvas
        self._fig = fig

    def _export_charts(self):
        """Export charts to PNG."""
        if not hasattr(self, "_fig"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_wind.export_charts"), "wind_characterization.png",
            "PNG (*.png)",
        )
        if path:
            self._fig.savefig(path, dpi=150, facecolor="white",
                              bbox_inches="tight")

    def get_wind_rose(self):
        """Return computed WindRoseData for Phase B downstream steps."""
        return self._wind_rose_data

    def get_stats(self) -> dict:
        """Return characterization statistics."""
        return self._stats

    def is_valid(self) -> bool:
        return self._hourly_data is not None and len(self._hourly_data) > 0


# =====================================================================
# Background Workers (keep UI responsive for Phase B computations)
# =====================================================================


class _SensitivityWorker(QThread):
    """Compute LCOE sensitivity sweep on background thread."""

    finished = Signal(object, object)  # sweep_values, lcoe_values

    def __init__(self, inputs, param_name, sweep, max_workers=0, parent=None):
        super().__init__(parent)
        self._inputs = inputs
        self._param_name = param_name
        self._sweep = sweep
        self._max_workers = max_workers

    def run(self):
        from windrex import compute_lcoe_sensitivity
        lcoes = compute_lcoe_sensitivity(
            self._inputs, self._param_name, self._sweep.tolist(),
            max_workers=self._max_workers,
        )
        self.finished.emit(self._sweep, lcoes)


class _WakeWorker(QThread):
    """Compute wake array efficiency + spacing curve on background thread."""

    finished = Signal(float, object, object)  # efficiency, spacings, efficiencies

    def __init__(self, n_turb, spacing, rotor_d, ct, wind_rose, max_workers=0, parent=None):
        super().__init__(parent)
        self._n_turb = n_turb
        self._spacing = spacing
        self._rotor_d = rotor_d
        self._ct = ct
        self._wind_rose = wind_rose
        self._max_workers = max_workers

    def run(self):
        from windrex import (
            compute_array_efficiency,
            compute_spacing_curve,
        )
        eff = compute_array_efficiency(
            self._n_turb, self._spacing, self._rotor_d, self._ct, self._wind_rose,
        )
        spacings, efficiencies = compute_spacing_curve(
            self._rotor_d, self._ct, self._wind_rose, n_turbines=self._n_turb,
            max_workers=self._max_workers,
        )
        self.finished.emit(eff, spacings, efficiencies)


# =====================================================================
# Step 7: Financial Analysis
# =====================================================================


# Regional CAPEX/OPEX presets ($/kW, $/kW/yr)
_FINANCIAL_PRESETS = {
    "onshore": {"capex": 1300, "opex": 25, "lifetime": 25},
    "offshore": {"capex": 3500, "opex": 80, "lifetime": 25},
}


class WindFinancialStep(QWidget):
    """LCOE, NPV, IRR calculation with sensitivity analysis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capacity_mw = 10.0
        self._capacity_factor = 0.30
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("wizard_wind.fin_preset")))
        self._preset_combo = QComboBox()
        self._preset_combo.addItem(tr("wizard_wind.preset_onshore"), "onshore")
        self._preset_combo.addItem(tr("wizard_wind.preset_offshore"), "offshore")
        self._preset_combo.addItem(tr("wizard_wind.preset_custom"), "custom")
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        layout.addLayout(preset_row)

        # Input parameters
        params_group = QGroupBox(tr("wizard_wind.fin_title"))
        params_form = QFormLayout(params_group)

        self._spin_capex = QDoubleSpinBox()
        self._spin_capex.setRange(100, 10000)
        self._spin_capex.setValue(1300)
        self._spin_capex.setSuffix(" $/kW")
        self._spin_capex.setDecimals(0)
        params_form.addRow(tr("wizard_wind.capex"), self._spin_capex)

        self._spin_opex = QDoubleSpinBox()
        self._spin_opex.setRange(0, 500)
        self._spin_opex.setValue(25)
        self._spin_opex.setSuffix(" $/kW/yr")
        self._spin_opex.setDecimals(1)
        params_form.addRow(tr("wizard_wind.opex"), self._spin_opex)

        self._spin_discount = QDoubleSpinBox()
        self._spin_discount.setRange(0.01, 0.30)
        self._spin_discount.setValue(0.08)
        self._spin_discount.setSingleStep(0.01)
        self._spin_discount.setDecimals(3)
        params_form.addRow(tr("wizard_wind.discount_rate"), self._spin_discount)

        self._spin_lifetime = QSpinBox()
        self._spin_lifetime.setRange(5, 40)
        self._spin_lifetime.setValue(25)
        self._spin_lifetime.setSuffix(" yr")
        params_form.addRow(tr("wizard_wind.lifetime"), self._spin_lifetime)

        self._spin_price = QDoubleSpinBox()
        self._spin_price.setRange(5, 500)
        self._spin_price.setValue(50)
        self._spin_price.setSuffix(" $/MWh")
        self._spin_price.setDecimals(1)
        params_form.addRow(tr("wizard_wind.elec_price"), self._spin_price)

        self._spin_degradation = QDoubleSpinBox()
        self._spin_degradation.setRange(0, 0.05)
        self._spin_degradation.setValue(0.005)
        self._spin_degradation.setSingleStep(0.001)
        self._spin_degradation.setDecimals(4)
        params_form.addRow(tr("wizard_wind.degradation"), self._spin_degradation)

        layout.addWidget(params_group)

        # Calculate button
        btn_row = QHBoxLayout()
        self._btn_calc = QPushButton(tr("wizard_wind.fin_calculate"))
        self._btn_calc.clicked.connect(self._calculate)
        btn_row.addWidget(self._btn_calc)

        self._btn_export = QPushButton(tr("wizard_wind.fin_export"))
        self._btn_export.clicked.connect(self._export_results)
        self._btn_export.setEnabled(False)
        btn_row.addWidget(self._btn_export)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Results display
        self._results_label = QLabel("")
        self._results_label.setStyleSheet(
            "background-color: #f8f8f8; border: 1px solid #ddd; "
            "border-radius: 4px; padding: 10px; color: black; font-size: 13px;"
        )
        self._results_label.setWordWrap(True)
        layout.addWidget(self._results_label)

        # Sensitivity chart
        sens_row = QHBoxLayout()
        sens_row.addWidget(QLabel(tr("wizard_wind.sensitivity")))
        self._sens_param = QComboBox()
        self._sens_param.addItem(tr("wizard_wind.capex"), "capex_per_kw")
        self._sens_param.addItem(tr("wizard_wind.discount_rate"), "discount_rate")
        self._sens_param.addItem(tr("wizard_wind.elec_price"), "electricity_price")
        self._sens_param.addItem("CF", "capacity_factor")
        self._sens_param.currentIndexChanged.connect(self._update_sensitivity)
        sens_row.addWidget(self._sens_param, 1)
        layout.addLayout(sens_row)

        self._sens_chart_widget = QWidget()
        self._sens_chart_layout = QVBoxLayout(self._sens_chart_widget)
        self._sens_chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._sens_chart_widget, 1)

        self._financial_results = None

    def set_inputs(self, capacity_mw: float, capacity_factor: float, max_workers: int = 0):
        """Receive capacity and CF from Phase A analysis."""
        self._capacity_mw = capacity_mw
        self._capacity_factor = capacity_factor
        self._max_workers = max_workers

    def _on_preset_changed(self):
        key = self._preset_combo.currentData()
        if key in _FINANCIAL_PRESETS:
            preset = _FINANCIAL_PRESETS[key]
            self._spin_capex.setValue(preset["capex"])
            self._spin_opex.setValue(preset["opex"])
            self._spin_lifetime.setValue(preset["lifetime"])

    def _get_inputs(self):
        from windrex import WindFinancialInputs
        return WindFinancialInputs(
            capacity_mw=self._capacity_mw,
            capacity_factor=self._capacity_factor,
            capex_per_kw=self._spin_capex.value(),
            opex_per_kw_yr=self._spin_opex.value(),
            discount_rate=self._spin_discount.value(),
            lifetime_years=self._spin_lifetime.value(),
            electricity_price=self._spin_price.value(),
            degradation_rate=self._spin_degradation.value(),
        )

    def _calculate(self):
        from windrex import compute_wind_financials

        inputs = self._get_inputs()
        results = compute_wind_financials(inputs)
        self._financial_results = results

        lines = [
            f"<b>{tr('wizard_wind.lcoe')}:</b> {results.lcoe:.2f} $/MWh",
            f"<b>{tr('wizard_wind.npv')}:</b> ${results.npv:,.0f}",
            f"<b>{tr('wizard_wind.irr')}:</b> {results.irr * 100:.1f}%",
            f"<b>{tr('wizard_wind.payback')}:</b> {results.payback_years:.1f} yr",
            f"<b>{tr('wizard_wind.annual_gen')}:</b> "
            f"{results.annual_revenue / inputs.electricity_price:,.0f} MWh/yr",
            f"<b>CAPEX:</b> ${results.capex_total:,.0f}",
        ]
        self._results_label.setText("<br>".join(lines))
        self._btn_export.setEnabled(True)
        self._update_sensitivity()

    def _update_sensitivity(self):
        """Compute sensitivity on background thread, render chart when done."""
        if self._financial_results is None:
            return

        import numpy as np

        inputs = self._get_inputs()
        param_name = self._sens_param.currentData()

        current_val = getattr(inputs, param_name, 1.0)
        if current_val <= 0:
            current_val = 1.0
        sweep = np.linspace(current_val * 0.5, current_val * 1.5, 30)

        workers = getattr(self, "_max_workers", 0)
        self._sens_worker = _SensitivityWorker(inputs, param_name, sweep, workers, self)
        self._sens_worker.finished.connect(self._on_sensitivity_done)
        self._sens_worker.start()

    def _on_sensitivity_done(self, sweep, lcoes):
        """Render sensitivity chart on main thread after worker completes."""
        # Clear old chart
        for i in reversed(range(self._sens_chart_layout.count())):
            w = self._sens_chart_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            return

        inputs = self._get_inputs()
        param_name = self._sens_param.currentData()
        current_val = getattr(inputs, param_name, 1.0)
        if current_val <= 0:
            current_val = 1.0

        fig = Figure(figsize=(8, 3))
        ax = fig.add_subplot(1, 1, 1)
        _white_chart_style(fig, ax)

        ax.plot(sweep, lcoes, "-", color="#e74c3c", linewidth=2)
        ax.axvline(current_val, color="#2980b9", linestyle="--", linewidth=1,
                   label=f"Current: {current_val:.3g}")
        ax.axhline(self._financial_results.lcoe, color="#888", linestyle=":",
                   linewidth=1)
        ax.set_xlabel(self._sens_param.currentText(), color="black")
        ax.set_ylabel("LCOE ($/MWh)", color="black")
        ax.set_title(tr("wizard_wind.sensitivity"), color="black", fontsize=10)
        ax.legend(fontsize=8, facecolor="white", edgecolor="#ccc", labelcolor="black")

        fig.tight_layout()
        canvas = FigureCanvasQTAgg(fig)
        self._sens_chart_layout.addWidget(canvas)
        self._sens_fig = fig

    def _export_results(self):
        """Export financial results to CSV."""
        if self._financial_results is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_wind.fin_export"), "wind_financial_summary.csv",
            "CSV (*.csv)",
        )
        if not path:
            return

        r = self._financial_results
        inp = self._get_inputs()
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Parameter", "Value", "Unit"])
            w.writerow(["Capacity", inp.capacity_mw, "MW"])
            w.writerow(["Capacity Factor", f"{inp.capacity_factor:.3f}", ""])
            w.writerow(["CAPEX", inp.capex_per_kw, "$/kW"])
            w.writerow(["OPEX", inp.opex_per_kw_yr, "$/kW/yr"])
            w.writerow(["Discount Rate", inp.discount_rate, ""])
            w.writerow(["Lifetime", inp.lifetime_years, "years"])
            w.writerow(["Electricity Price", inp.electricity_price, "$/MWh"])
            w.writerow(["Degradation", inp.degradation_rate, "/yr"])
            w.writerow([])
            w.writerow(["LCOE", f"{r.lcoe:.2f}", "$/MWh"])
            w.writerow(["NPV", f"{r.npv:.0f}", "$"])
            w.writerow(["IRR", f"{r.irr * 100:.1f}", "%"])
            w.writerow(["Payback", f"{r.payback_years:.1f}", "years"])
            w.writerow(["Total Generation", f"{r.total_generation_mwh:.0f}", "MWh"])

    def get_financial_results(self):
        return self._financial_results

    def get_inputs(self):
        return self._get_inputs()

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 8: Wake Effect Modeling
# =====================================================================


class WindWakeLayoutStep(QWidget):
    """Jensen/Park wake model, array efficiency, spacing curve."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_rose = None
        self._rotor_diameter = 126.0  # default (V90 class)
        self._capacity_factor = 0.30
        self._capacity_mw = 10.0
        self._array_efficiency = 1.0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Parameters group
        params_group = QGroupBox(tr("wizard_wind.wake_title"))
        params_form = QFormLayout(params_group)

        self._spin_n_turbines = QSpinBox()
        self._spin_n_turbines.setRange(1, 500)
        self._spin_n_turbines.setValue(25)
        params_form.addRow(tr("wizard_wind.n_turbines"), self._spin_n_turbines)

        self._spin_spacing = QDoubleSpinBox()
        self._spin_spacing.setRange(3.0, 15.0)
        self._spin_spacing.setValue(7.0)
        self._spin_spacing.setSuffix(" D")
        self._spin_spacing.setSingleStep(0.5)
        self._spin_spacing.setDecimals(1)
        params_form.addRow(tr("wizard_wind.spacing"), self._spin_spacing)

        self._spin_ct = QDoubleSpinBox()
        self._spin_ct.setRange(0.1, 1.0)
        self._spin_ct.setValue(0.80)
        self._spin_ct.setSingleStep(0.05)
        self._spin_ct.setDecimals(2)
        params_form.addRow(tr("wizard_wind.thrust_ct"), self._spin_ct)

        layout.addWidget(params_group)

        # Calculate button
        btn_row = QHBoxLayout()
        self._btn_calc = QPushButton(tr("wizard_wind.wake_calculate"))
        self._btn_calc.clicked.connect(self._calculate)
        btn_row.addWidget(self._btn_calc)

        self._btn_export = QPushButton(tr("wizard_wind.wake_export"))
        self._btn_export.clicked.connect(self._export_results)
        self._btn_export.setEnabled(False)
        btn_row.addWidget(self._btn_export)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Results display
        self._results_label = QLabel("")
        self._results_label.setStyleSheet(
            "background-color: #f8f8f8; border: 1px solid #ddd; "
            "border-radius: 4px; padding: 10px; color: black; font-size: 13px;"
        )
        self._results_label.setWordWrap(True)
        layout.addWidget(self._results_label)

        # Spacing curve chart
        self._chart_widget = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_widget)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chart_widget, 1)

    def set_inputs(
        self,
        wind_rose,
        rotor_diameter: float,
        capacity_factor: float,
        capacity_mw: float,
        max_workers: int = 0,
    ):
        """Receive wind rose and turbine specs from upstream steps."""
        self._wind_rose = wind_rose
        self._rotor_diameter = rotor_diameter
        self._capacity_factor = capacity_factor
        self._capacity_mw = capacity_mw
        self._max_workers = max_workers

    def _calculate(self):
        """Launch wake computation on background thread."""
        import numpy as np

        from windrex import WindRoseData

        n_turb = self._spin_n_turbines.value()
        spacing = self._spin_spacing.value()
        ct = self._spin_ct.value()

        # Use computed wind rose or create uniform fallback
        if self._wind_rose is not None:
            wr = self._wind_rose
        else:
            n_sec = 16
            wr = WindRoseData(
                sectors=np.arange(n_sec) * (360.0 / n_sec),
                frequencies=np.ones(n_sec) / n_sec,
                mean_speeds=np.full(n_sec, 8.0),
            )

        self._btn_calc.setEnabled(False)
        self._btn_calc.setText("...")
        self._pending_spacing = spacing
        self._pending_n_turb = n_turb

        workers = getattr(self, "_max_workers", 0)
        self._wake_worker = _WakeWorker(
            n_turb, spacing, self._rotor_diameter, ct, wr, workers, self,
        )
        self._wake_worker.finished.connect(self._on_wake_done)
        self._wake_worker.start()

    def _on_wake_done(self, efficiency, spacings, efficiencies):
        """Display results and chart on main thread after worker completes."""
        self._btn_calc.setEnabled(True)
        self._btn_calc.setText(tr("wizard_wind.wake_calculate"))

        self._array_efficiency = efficiency
        n_turb = self._pending_n_turb
        spacing = self._pending_spacing

        gross_cf = self._capacity_factor
        net_cf = gross_cf * self._array_efficiency
        wake_loss = (1.0 - self._array_efficiency) * 100.0
        annual_gen = self._capacity_mw * n_turb * net_cf * 8760.0

        lines = [
            f"<b>{tr('wizard_wind.gross_cf')}:</b> {gross_cf:.3f} "
            f"({gross_cf * 100:.1f}%)",
            f"<b>{tr('wizard_wind.array_efficiency')}:</b> "
            f"{self._array_efficiency * 100:.1f}%",
            f"<b>{tr('wizard_wind.wake_loss')}:</b> {wake_loss:.1f}%",
            f"<b>{tr('wizard_wind.net_cf')}:</b> {net_cf:.3f} "
            f"({net_cf * 100:.1f}%)",
            f"<b>{tr('wizard_wind.annual_gen')}:</b> {annual_gen:,.0f} MWh/yr",
            f"<b>{tr('wizard_wind.n_turbines')}:</b> {n_turb} "
            f"\u00d7 {self._capacity_mw:.1f} MW = "
            f"{n_turb * self._capacity_mw:.1f} MW",
        ]
        self._results_label.setText("<br>".join(lines))
        self._btn_export.setEnabled(True)

        self._draw_spacing_curve(spacings, efficiencies, spacing)

    def _draw_spacing_curve(self, spacings, efficiencies, current_spacing):
        """Draw array efficiency vs spacing chart."""
        # Clear old
        for i in reversed(range(self._chart_layout.count())):
            w = self._chart_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            return

        fig = Figure(figsize=(8, 3.5))
        ax = fig.add_subplot(1, 1, 1)
        _white_chart_style(fig, ax)

        ax.plot(spacings, [e * 100 for e in efficiencies], "o-",
                color="#2980b9", linewidth=2, markersize=5)
        ax.axvline(current_spacing, color="#e74c3c", linestyle="--",
                   linewidth=1.5, label=f"Selected: {current_spacing:.1f}D")

        # Mark current point
        import numpy as np
        idx = np.argmin(np.abs(np.array(spacings) - current_spacing))
        ax.plot(spacings[idx], efficiencies[idx] * 100, "s",
                color="#e74c3c", markersize=10, zorder=5)

        ax.set_xlabel(tr("wizard_wind.spacing") + " (D)", color="black")
        ax.set_ylabel(tr("wizard_wind.array_efficiency") + " (%)", color="black")
        ax.set_title(tr("wizard_wind.spacing_curve"), color="black", fontsize=10)
        ax.legend(fontsize=8, facecolor="white", edgecolor="#ccc", labelcolor="black")
        ax.set_ylim(50, 105)

        fig.tight_layout()
        canvas = FigureCanvasQTAgg(fig)
        self._chart_layout.addWidget(canvas)
        self._fig = fig

    def _export_results(self):
        """Export wake analysis summary to CSV."""
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_wind.wake_export"), "wind_wake_analysis.csv",
            "CSV (*.csv)",
        )
        if not path:
            return

        n_turb = self._spin_n_turbines.value()
        gross_cf = self._capacity_factor
        net_cf = gross_cf * self._array_efficiency

        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Parameter", "Value", "Unit"])
            w.writerow(["N Turbines", n_turb, ""])
            w.writerow(["Rotor Diameter", self._rotor_diameter, "m"])
            w.writerow(["Spacing", self._spin_spacing.value(), "D"])
            w.writerow(["Thrust Ct", self._spin_ct.value(), ""])
            w.writerow(["Gross CF", f"{gross_cf:.4f}", ""])
            w.writerow(["Array Efficiency", f"{self._array_efficiency:.4f}", ""])
            w.writerow(["Net CF", f"{net_cf:.4f}", ""])
            w.writerow(["Wake Loss", f"{(1 - self._array_efficiency) * 100:.1f}", "%"])
            w.writerow([
                "Annual Generation",
                f"{self._capacity_mw * n_turb * net_cf * 8760:.0f}",
                "MWh/yr",
            ])

    def get_array_efficiency(self) -> float:
        return self._array_efficiency

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 9: Availability Profile Generation
# =====================================================================

_WIND_FUEL_HINTS = {"wind", "eolic", "eólic", "turbine", "aerogenerador"}
_MAX_DISTANCE_DEG = 0.5


def _find_nearest_cell(lat, lon, hourly_data):
    """Find nearest (lat,lon) key in *hourly_data* dict.

    Returns ``(key, distance_deg)`` or ``(None, inf)`` if empty.
    """
    import numpy as np

    keys = list(hourly_data.keys())
    if not keys:
        return None, float("inf")
    dists = [((lat - k[0]) ** 2 + (lon - k[1]) ** 2) ** 0.5 for k in keys]
    idx = int(np.argmin(dists))
    return keys[idx], dists[idx]


class _WindProfileWorker(QThread):
    """Generate wind availability profiles on a background thread."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        gen_groups,
        hourly_data,
        wind_config,
        output_dir,
        year,
        parent=None,
    ):
        super().__init__(parent)
        self._gen_groups = gen_groups
        self._hourly_data = hourly_data
        self._wind_config = wind_config
        self._output_dir = output_dir
        self._year = year

    def run(self):
        import numpy as np
        from pathlib import Path

        try:
            from windrex import compute_wind_hourly_cf
            from windrex.core.capacity_factor import _wind_speed_to_hourly_cf
        except ImportError:
            self.error.emit(
                "Could not import wind CF functions from windrex"
            )
            return

        try:
            output_dir = Path(self._output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            cfg = self._wind_config
            pc_ws = cfg.wind_speeds if cfg else []
            pc_mw = cfg.power_curve if cfg else []
            rated_mw = cfg.turbine_capacity_mw if cfg else 3.0
            hub_height = cfg.hub_height if cfg else 80
            data_source = cfg.data_source if cfg else "open_meteo"
            turbine_key = cfg.turbine if cfg else None

            results = {}
            total = sum(len(insts) for insts in self._gen_groups.values())
            done = 0

            for unit_key, instances in self._gen_groups.items():
                max_node = max(inst["node"] for inst in instances)
                profile_array = np.ones((8760, max_node + 1))

                for inst in instances:
                    lat = inst["lat"]
                    lon = inst["lon"]
                    node = inst["node"]
                    iid = inst["instance_id"]
                    done += 1
                    pct = int(100 * done / total)

                    if abs(lat) < 0.01 and abs(lon) < 0.01:
                        self.progress.emit(pct, f"Skipping {iid} (no position)")
                        results[iid] = {"status": "skipped", "mean_cf": 0.0}
                        continue

                    self.progress.emit(pct, f"Computing CF for {iid}...")

                    cf = None

                    if self._hourly_data:
                        cell, dist = _find_nearest_cell(
                            lat, lon, self._hourly_data,
                        )
                        if cell is not None and dist < _MAX_DISTANCE_DEG:
                            ws = self._hourly_data[cell].wind_speed
                            if pc_ws and pc_mw:
                                cf = _wind_speed_to_hourly_cf(
                                    ws, pc_ws, pc_mw, rated_mw,
                                )
                            else:
                                cf = _wind_speed_to_hourly_cf(
                                    ws,
                                    list(range(26)),
                                    [0, 0, 0, 0.03, 0.16, 0.36, 0.67,
                                     1.08, 1.58, 2.12, 2.58, 2.85, 2.98,
                                     3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0,
                                     3.0, 3.0, 3.0, 3.0, 3.0, 0],
                                    3.0,
                                )

                    if cf is None:
                        cf = compute_wind_hourly_cf(
                            lat, lon, self._year, data_source,
                            wind_speeds=pc_ws or None,
                            power_curve=pc_mw or None,
                            rated_power_mw=rated_mw,
                            hub_height=hub_height,
                            turbine_key=turbine_key,
                        )

                    cf = np.asarray(cf, dtype=float)
                    if len(cf) > 8760:
                        cf = cf[:8760]
                    elif len(cf) < 8760:
                        padded = np.zeros(8760)
                        padded[: len(cf)] = cf
                        cf = padded

                    profile_array[:, node] = cf
                    mean_cf = float(np.mean(cf))
                    results[iid] = {"status": "done", "mean_cf": mean_cf}

                csv_name = f"{unit_key}_availability.csv"
                csv_path = output_dir / csv_name
                np.savetxt(csv_path, profile_array, delimiter=",", fmt="%.6f")

                for inst in instances:
                    iid = inst["instance_id"]
                    if iid in results and results[iid]["status"] == "done":
                        results[iid]["path"] = str(csv_path)
                        results[iid]["unit_key"] = unit_key

            self.finished.emit(results)

        except Exception as exc:
            self.error.emit(str(exc))


class WindAvailabilityStep(QWidget):
    """Step 9 — Generate availability profiles for wind generators."""

    def __init__(self, model=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._hourly_data = None
        self._wind_config = None
        self._summary = None
        self._worker = None
        self._results = {}
        self._gen_rows = []

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Instruction
        self._lbl_instruction = QLabel(tr("wizard_wind.avail_instruction"))
        self._lbl_instruction.setWordWrap(True)
        layout.addWidget(self._lbl_instruction)

        # Warning label (hidden by default)
        self._lbl_warning = QLabel()
        self._lbl_warning.setWordWrap(True)
        self._lbl_warning.setStyleSheet("color: #e67e22; font-weight: bold;")
        self._lbl_warning.setVisible(False)
        layout.addWidget(self._lbl_warning)

        # Select all / Deselect all + output dir
        top_row = QHBoxLayout()
        btn_sel = QPushButton(tr("wizard_wind.avail_select_all"))
        btn_sel.clicked.connect(lambda: self._set_all_checked(True))
        btn_desel = QPushButton(tr("wizard_wind.avail_deselect_all"))
        btn_desel.clicked.connect(lambda: self._set_all_checked(False))
        top_row.addWidget(btn_sel)
        top_row.addWidget(btn_desel)
        top_row.addStretch()

        top_row.addWidget(QLabel(tr("wizard_wind.avail_output_dir")))
        self._lbl_output_dir = QLabel("./availability/")
        self._lbl_output_dir.setStyleSheet(
            "background-color: #333; padding: 2px 6px; border-radius: 3px;"
        )
        top_row.addWidget(self._lbl_output_dir)
        btn_browse = QPushButton(tr("wizard_wind.avail_browse"))
        btn_browse.clicked.connect(self._browse_output_dir)
        top_row.addWidget(btn_browse)
        layout.addLayout(top_row)

        # Generator table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "",
            tr("wizard_wind.avail_col_name"),
            tr("wizard_wind.avail_col_unit"),
            tr("wizard_wind.avail_col_node"),
            tr("wizard_wind.avail_col_position"),
            tr("wizard_wind.avail_col_status"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setMinimumHeight(120)
        self._table.setMaximumHeight(220)
        layout.addWidget(self._table)

        # Preview chart
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self._fig = Figure(figsize=(8, 2.5), dpi=100)
            self._ax = self._fig.add_subplot(111)
            self._canvas = FigureCanvasQTAgg(self._fig)
            _white_chart_style(self._fig, self._ax)
            self._ax.set_title(
                tr("wizard_wind.avail_preview"), fontsize=10, color="black",
            )
            layout.addWidget(self._canvas, 1)
        except ImportError:
            self._canvas = None
            layout.addWidget(QLabel("(matplotlib not available)"))

        # Generate button + progress
        gen_row = QHBoxLayout()
        self._btn_generate = QPushButton(tr("wizard_wind.avail_generate"))
        self._btn_generate.clicked.connect(self._on_generate)
        gen_row.addWidget(self._btn_generate)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        gen_row.addWidget(self._progress, 1)
        layout.addLayout(gen_row)

        # Status label
        self._lbl_status = QLabel("")
        layout.addWidget(self._lbl_status)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_inputs(self, hourly_data, wind_config, summary):
        """Called by wizard when transitioning to this step."""
        self._hourly_data = hourly_data
        self._wind_config = wind_config
        self._summary = summary

        if not hourly_data:
            self._lbl_warning.setText(tr("wizard_wind.avail_no_data"))
            self._lbl_warning.setVisible(True)
        else:
            self._lbl_warning.setVisible(False)

        self._resolve_output_dir()
        self._populate_table()

    def is_valid(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self):
        self._table.setRowCount(0)
        self._gen_rows = []

        if self._model is None:
            self._lbl_status.setText(tr("wizard_wind.avail_no_generators"))
            self._btn_generate.setEnabled(False)
            return

        state = self._model.state
        gens = getattr(state, "generators", {})

        for gen_id, gen in gens.items():
            gen_type = getattr(gen, "gen_type", "")
            fuel = getattr(gen, "fuel", "")
            if gen_type != "Renewable":
                continue
            if fuel.lower() not in _WIND_FUEL_HINTS:
                continue

            lat = getattr(gen, "latitude", 0.0)
            lon = getattr(gen, "longitude", 0.0)
            node = getattr(gen, "node", 0)
            unit_key = getattr(gen, "unit_key", gen_id)
            name = getattr(gen, "name", gen_id)

            row = self._table.rowCount()
            self._table.setRowCount(row + 1)

            chk = QCheckBox()
            chk.setChecked(True)
            chk_w = QWidget()
            chk_l = QHBoxLayout(chk_w)
            chk_l.addWidget(chk)
            chk_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_l.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row, 0, chk_w)

            self._table.setItem(row, 1, QTableWidgetItem(name))
            self._table.setItem(row, 2, QTableWidgetItem(unit_key))
            self._table.setItem(row, 3, QTableWidgetItem(str(node)))
            self._table.setItem(
                row, 4, QTableWidgetItem(f"({lat:.3f}, {lon:.3f})"),
            )
            self._table.setItem(
                row, 5, QTableWidgetItem(tr("wizard_wind.avail_status_pending")),
            )

            self._gen_rows.append({
                "instance_id": gen_id,
                "unit_key": unit_key,
                "name": name,
                "node": node,
                "lat": lat,
                "lon": lon,
                "row": row,
            })

        if not self._gen_rows:
            self._lbl_status.setText(tr("wizard_wind.avail_no_generators"))
            self._btn_generate.setEnabled(False)
        else:
            self._btn_generate.setEnabled(True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _set_all_checked(self, checked: bool):
        for row in range(self._table.rowCount()):
            w = self._table.cellWidget(row, 0)
            if w:
                chk = w.findChildren(QCheckBox)
                if chk:
                    chk[0].setChecked(checked)

    def _browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, tr("wizard_wind.avail_output_dir"),
        )
        if d:
            self._lbl_output_dir.setText(d)

    def _resolve_output_dir(self):
        parent = self.window()
        config_path = getattr(parent, "_config_path", None)
        if not config_path:
            mw = getattr(parent, "_parent", None) or parent
            config_path = getattr(mw, "_config_path", None)
        if config_path:
            from pathlib import Path
            out = str(Path(config_path).parent / "availability")
        else:
            out = "./availability/"
        self._lbl_output_dir.setText(out)

    def _on_generate(self):
        # Collect checked generators
        selected = []
        for info in self._gen_rows:
            row = info["row"]
            w = self._table.cellWidget(row, 0)
            if w:
                chk = w.findChildren(QCheckBox)
                if chk and chk[0].isChecked():
                    selected.append(info)

        if not selected:
            QMessageBox.warning(
                self, tr("wizard_wind.avail_error_title"),
                tr("wizard_wind.avail_no_selection"),
            )
            return

        # Group by unit_key
        from collections import defaultdict
        groups = defaultdict(list)
        for info in selected:
            groups[info["unit_key"]].append(info)

        year = self._wind_config.year if self._wind_config else 2022
        output_dir = self._lbl_output_dir.text()

        self._btn_generate.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)

        self._worker = _WindProfileWorker(
            dict(groups),
            self._hourly_data,
            self._wind_config,
            output_dir,
            year,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._lbl_status.setText(msg)

    def _on_finished(self, results):
        self._btn_generate.setEnabled(True)
        self._progress.setValue(100)
        self._results = results

        # Update table status column
        cf_values = []
        for info in self._gen_rows:
            iid = info["instance_id"]
            if iid in results:
                r = results[iid]
                status = r.get("status", "")
                if status == "done":
                    mean_cf = r.get("mean_cf", 0)
                    cf_values.append(mean_cf)
                    txt = f"{tr('wizard_wind.avail_status_done')} (CF={mean_cf:.3f})"
                elif status == "skipped":
                    txt = tr("wizard_wind.avail_status_skipped")
                else:
                    txt = tr("wizard_wind.avail_status_error")
                self._table.setItem(info["row"], 5, QTableWidgetItem(txt))

        # Update availability_file on model
        if self._model:
            state = self._model.state
            gens = getattr(state, "generators", {})
            for iid, r in results.items():
                if r.get("status") == "done" and "path" in r:
                    uk = r.get("unit_key", "")
                    for gen in gens.values():
                        if getattr(gen, "unit_key", "") == uk:
                            gen.availability_file = r["path"]

        # Summary
        n_done = sum(1 for r in results.values() if r.get("status") == "done")
        avg_cf = sum(cf_values) / len(cf_values) if cf_values else 0.0
        self._lbl_status.setText(
            tr("wizard_wind.avail_summary").format(n=n_done, cf=avg_cf)
        )

        # Preview first result
        if self._canvas and cf_values:
            self._draw_preview(results)

        QMessageBox.information(
            self, tr("wizard_wind.avail_done_title"),
            tr("wizard_wind.avail_done_msg").format(n=n_done),
        )

    def _on_error(self, msg):
        self._btn_generate.setEnabled(True)
        self._progress.setVisible(False)
        QMessageBox.critical(
            self, tr("wizard_wind.avail_error_title"), msg,
        )

    def _draw_preview(self, results):
        """Draw CF time series for first completed generator."""
        import numpy as np
        from pathlib import Path

        self._ax.clear()
        _white_chart_style(self._fig, self._ax)

        for iid, r in results.items():
            if r.get("status") != "done" or "path" not in r:
                continue
            try:
                data = np.loadtxt(r["path"], delimiter=",")
                if data.ndim == 1:
                    cf = data
                else:
                    cf = data[:, 0]
                hours = np.arange(len(cf))
                self._ax.plot(
                    hours, cf, linewidth=0.3, alpha=0.8, label=iid,
                )
            except Exception:
                continue
            break  # only preview first

        self._ax.set_xlabel("Hour of year", fontsize=9, color="black")
        self._ax.set_ylabel("Capacity Factor", fontsize=9, color="black")
        self._ax.set_title(
            tr("wizard_wind.avail_preview"), fontsize=10, color="black",
        )
        self._ax.set_xlim(0, 8760)
        self._ax.set_ylim(0, 1)
        self._fig.tight_layout()
        self._canvas.draw()
