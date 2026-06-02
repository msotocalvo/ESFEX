"""Phase B step widgets for the Solar PV Assessment wizard.

Step 6: Solar Characterization (GHI patterns, diurnal, seasonal, temperature)
Step 7: Financial Analysis (LCOE, NPV, IRR, sensitivity)
Step 8: Array / Shading Analysis (GCR, inter-row shading, bifacial gain)
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
# Step 6: Solar Characterization
# =====================================================================


class SolarCharacterizationStep(QWidget):
    """GHI distribution, diurnal/seasonal patterns, temperature analysis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hourly_data = None  # dict[(lat,lon)] -> HourlyIrradianceData
        self._summary = None
        self._config = None
        self._stats = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Cell selector
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel(tr("wizard_solar_pv.select_cell")))
        self._cell_combo = QComboBox()
        self._cell_combo.addItem(tr("wizard_solar_pv.all_cells"), "all")
        self._cell_combo.currentIndexChanged.connect(self._on_cell_changed)
        sel_row.addWidget(self._cell_combo, 1)

        self._btn_export = QPushButton(tr("wizard_solar_pv.export_charts"))
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

    def set_inputs(self, hourly_data, summary, config=None):
        """Receive hourly irradiance data and analysis summary from Phase A."""
        self._hourly_data = hourly_data
        self._summary = summary
        self._config = config

        # Populate cell selector
        self._cell_combo.blockSignals(True)
        self._cell_combo.clear()
        self._cell_combo.addItem(tr("wizard_solar_pv.all_cells"), "all")
        if hourly_data:
            for (lat, lon) in sorted(hourly_data.keys()):
                self._cell_combo.addItem(f"({lat:.3f}, {lon:.3f})", (lat, lon))
        self._cell_combo.blockSignals(False)
        self._on_cell_changed()

    def _get_selected_data(self):
        """Get aggregated or single-cell GHI/temp arrays."""
        import numpy as np

        if not self._hourly_data:
            return None, None, None

        idx = self._cell_combo.currentIndex()
        data_key = self._cell_combo.itemData(idx)

        if data_key == "all":
            all_ghi = []
            all_temp = []
            timestamps = None
            for hd in self._hourly_data.values():
                if hd.ghi is not None:
                    all_ghi.append(np.asarray(hd.ghi))
                if hd.temperature is not None:
                    all_temp.append(np.asarray(hd.temperature))
                if timestamps is None:
                    timestamps = hd.timestamps
            ghi = np.concatenate(all_ghi) if all_ghi else None
            temp = np.concatenate(all_temp) if all_temp else None
            return ghi, temp, timestamps
        else:
            hd = self._hourly_data.get(data_key)
            if hd is None:
                return None, None, None
            return (
                np.asarray(hd.ghi) if hd.ghi is not None else None,
                np.asarray(hd.temperature) if hd.temperature is not None else None,
                hd.timestamps,
            )

    def _on_cell_changed(self):
        """Recompute statistics and redraw charts."""
        import numpy as np

        from solarex import (
            compute_clearness_index,
            compute_diurnal_irradiance,
            compute_monthly_irradiance,
            compute_peak_sun_hours,
            compute_performance_ratio,
            compute_temp_analysis,
        )

        ghi, temp, timestamps = self._get_selected_data()
        if ghi is None or len(ghi) == 0:
            self._stats_label.setText(tr("wizard_solar_pv.char_no_data"))
            return

        # Compute stats
        mean_ghi_wm2 = float(np.mean(ghi[ghi > 0])) if np.any(ghi > 0) else 0.0
        psh = compute_peak_sun_hours(ghi)

        efficiency = 0.20
        gamma = -0.40
        t_noct = 45.0
        if self._config:
            efficiency = self._config.module_efficiency
            gamma = self._config.module_gamma_pmax
            t_noct = self._config.module_t_noct

        pr = compute_performance_ratio(ghi, temp, efficiency, gamma, t_noct)

        # Clearness index — use first cell's lat for computation
        latitude = 0.0
        if self._hourly_data:
            first_key = next(iter(self._hourly_data))
            latitude = first_key[0]
        kt = compute_clearness_index(ghi, latitude, timestamps) if timestamps else 0.0

        # Cell temperature
        t_cell, derating = compute_temp_analysis(ghi, temp, t_noct)
        mean_cell_temp = float(np.mean(t_cell[ghi > 0])) if np.any(ghi > 0) else 0.0

        self._stats = {
            "mean_ghi": mean_ghi_wm2,
            "psh": psh,
            "pr": pr,
            "kt": kt,
            "mean_cell_temp": mean_cell_temp,
        }

        lines = [
            f"<b>{tr('wizard_solar_pv.mean_ghi')}:</b> {mean_ghi_wm2:.0f} W/m\u00b2 "
            f"({float(np.nansum(ghi)) / 1000.0:.0f} kWh/m\u00b2/yr)",
            f"<b>{tr('wizard_solar_pv.peak_sun_hours')}:</b> {psh:.1f} h/day",
            f"<b>{tr('wizard_solar_pv.performance_ratio')}:</b> {pr:.3f} ({pr * 100:.1f}%)",
            f"<b>{tr('wizard_solar_pv.clearness_index')}:</b> {kt:.3f}",
            f"<b>{tr('wizard_solar_pv.mean_cell_temp')}:</b> {mean_cell_temp:.1f} \u00b0C",
        ]
        self._stats_label.setText("<br>".join(lines))

        self._build_charts(ghi, temp, timestamps)

    def _build_charts(self, ghi, temp, timestamps):
        """Create 2x2 matplotlib chart grid."""
        import numpy as np

        from solarex import (
            compute_diurnal_irradiance,
            compute_monthly_irradiance,
            compute_temp_analysis,
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

        t_noct = self._config.module_t_noct if self._config else 45.0

        # 1. GHI histogram (daytime only)
        ax1 = fig.add_subplot(2, 2, 1)
        _white_chart_style(fig, ax1)
        ghi_day = ghi[ghi > 10]  # Filter daytime hours
        if len(ghi_day) > 0:
            bins = np.linspace(0, min(ghi_day.max(), 1200), 40)
            ax1.hist(ghi_day, bins=bins, density=True, alpha=0.6,
                     color="#f39c12", edgecolor="white", linewidth=0.5)
        ax1.set_xlabel("GHI (W/m\u00b2)", color="black")
        ax1.set_ylabel("Probability Density", color="black")
        ax1.set_title(tr("wizard_solar_pv.ghi_distribution"), color="black", fontsize=10)

        # 2. Monthly irradiance
        ax2 = fig.add_subplot(2, 2, 2)
        _white_chart_style(fig, ax2)
        if timestamps:
            first_key = next(iter(self._hourly_data)) if self._hourly_data else None
            if first_key:
                hd = self._hourly_data[first_key]
                months, totals = compute_monthly_irradiance(
                    np.asarray(hd.ghi), hd.timestamps,
                )
                month_labels = [
                    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                ]
                ax2.bar(months, totals, color="#e67e22", edgecolor="white", alpha=0.85)
                ax2.set_xticks(months)
                ax2.set_xticklabels(month_labels, fontsize=7)
                ax2.set_xlabel("Month", color="black")
                ax2.set_ylabel("GHI (kWh/m\u00b2)", color="black")
        ax2.set_title(tr("wizard_solar_pv.monthly_irradiance"), color="black", fontsize=10)

        # 3. Diurnal pattern
        ax3 = fig.add_subplot(2, 2, 3)
        _white_chart_style(fig, ax3)
        if timestamps:
            first_key = next(iter(self._hourly_data)) if self._hourly_data else None
            if first_key:
                hd = self._hourly_data[first_key]
                hours, hourly_means = compute_diurnal_irradiance(
                    np.asarray(hd.ghi), hd.timestamps,
                )
                ax3.plot(hours, hourly_means, "o-", color="#f39c12",
                         markersize=4, linewidth=1.5)
                ax3.fill_between(hours, 0, hourly_means, alpha=0.2, color="#f39c12")
                ax3.set_xlabel("Hour of Day", color="black")
                ax3.set_ylabel("Mean GHI (W/m\u00b2)", color="black")
                ax3.set_xlim(-0.5, 23.5)
                ax3.set_xticks(range(0, 24, 3))
        ax3.set_title(tr("wizard_solar_pv.diurnal_irradiance"), color="black", fontsize=10)

        # 4. Cell temperature vs ambient
        ax4 = fig.add_subplot(2, 2, 4)
        _white_chart_style(fig, ax4)
        if timestamps and temp is not None:
            first_key = next(iter(self._hourly_data)) if self._hourly_data else None
            if first_key:
                hd = self._hourly_data[first_key]
                ghi_cell = np.asarray(hd.ghi)
                temp_cell = np.asarray(hd.temperature)
                t_cell, _ = compute_temp_analysis(ghi_cell, temp_cell, t_noct)

                # Sample for scatter (max 2000 points for performance)
                step = max(1, len(temp_cell) // 2000)
                mask = ghi_cell[::step] > 10  # daytime only
                ax4.scatter(
                    temp_cell[::step][mask], t_cell[::step][mask],
                    s=3, alpha=0.3, color="#e74c3c", label="Cell temp",
                )
                # Reference line
                t_range = np.linspace(
                    float(temp_cell.min()), float(temp_cell.max()), 50,
                )
                ax4.plot(t_range, t_range, "--", color="#888", linewidth=1,
                         label="T_cell = T_amb")
                ax4.set_xlabel("Ambient Temperature (\u00b0C)", color="black")
                ax4.set_ylabel("Cell Temperature (\u00b0C)", color="black")
                ax4.legend(fontsize=7, facecolor="white", edgecolor="#ccc",
                           labelcolor="black")
        ax4.set_title(tr("wizard_solar_pv.temp_analysis"), color="black", fontsize=10)

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
            self, tr("wizard_solar_pv.export_charts"), "solar_characterization.png",
            "PNG (*.png)",
        )
        if path:
            self._fig.savefig(path, dpi=150, facecolor="white",
                              bbox_inches="tight")

    def get_stats(self) -> dict:
        return self._stats

    def is_valid(self) -> bool:
        return self._hourly_data is not None and len(self._hourly_data) > 0


# =====================================================================
# Background Workers
# =====================================================================


class _SolarSensitivityWorker(QThread):
    """Compute LCOE sensitivity sweep on background thread."""

    finished = Signal(object, object)  # sweep_values, lcoe_values

    def __init__(self, inputs, param_name, sweep, max_workers=0, parent=None):
        super().__init__(parent)
        self._inputs = inputs
        self._param_name = param_name
        self._sweep = sweep
        self._max_workers = max_workers

    def run(self):
        from solarex import compute_pv_lcoe_sensitivity
        lcoes = compute_pv_lcoe_sensitivity(
            self._inputs, self._param_name, self._sweep.tolist(),
            max_workers=self._max_workers,
        )
        self.finished.emit(self._sweep, lcoes)


class _GCRWorker(QThread):
    """Compute GCR shading curve + bifacial gain on background thread."""

    finished = Signal(float, object, object, float)  # loss, gcrs, losses, bifi_gain

    def __init__(self, latitude, tilt, gcr, module_height, albedo,
                 is_bifacial, max_workers=0, parent=None):
        super().__init__(parent)
        self._latitude = latitude
        self._tilt = tilt
        self._gcr = gcr
        self._module_height = module_height
        self._albedo = albedo
        self._is_bifacial = is_bifacial
        self._max_workers = max_workers

    def run(self):
        from solarex import (
            compute_bifacial_gain,
            compute_gcr_curve,
            compute_gcr_shading_loss,
        )
        loss = compute_gcr_shading_loss(
            self._latitude, self._tilt, self._gcr, self._module_height,
        )
        gcrs, losses = compute_gcr_curve(
            self._latitude, self._tilt, module_height=self._module_height,
            max_workers=self._max_workers,
        )
        bifi_gain = 0.0
        if self._is_bifacial:
            bifi_gain = compute_bifacial_gain(
                self._albedo, self._gcr, self._module_height, self._tilt,
            )
        self.finished.emit(loss, gcrs, losses, bifi_gain)


# =====================================================================
# Step 7: Financial Analysis
# =====================================================================


_FINANCIAL_PRESETS = {
    "ground_mount": {"capex": 1000, "opex": 15, "lifetime": 25},
    "floating": {"capex": 1400, "opex": 25, "lifetime": 25},
}


class SolarFinancialStep(QWidget):
    """LCOE, NPV, IRR calculation with sensitivity analysis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capacity_mw = 10.0
        self._capacity_factor = 0.20
        self._max_workers = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("wizard_solar_pv.fin_preset")))
        self._preset_combo = QComboBox()
        self._preset_combo.addItem(tr("wizard_solar_pv.preset_ground_mount"), "ground_mount")
        self._preset_combo.addItem(tr("wizard_solar_pv.preset_floating"), "floating")
        self._preset_combo.addItem(tr("wizard_solar_pv.preset_custom"), "custom")
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        layout.addLayout(preset_row)

        # Input parameters
        params_group = QGroupBox(tr("wizard_solar_pv.fin_title"))
        params_form = QFormLayout(params_group)

        self._spin_capex = QDoubleSpinBox()
        self._spin_capex.setRange(100, 10000)
        self._spin_capex.setValue(1000)
        self._spin_capex.setSuffix(" $/kW")
        self._spin_capex.setDecimals(0)
        params_form.addRow(tr("wizard_solar_pv.capex"), self._spin_capex)

        self._spin_opex = QDoubleSpinBox()
        self._spin_opex.setRange(0, 500)
        self._spin_opex.setValue(15)
        self._spin_opex.setSuffix(" $/kW/yr")
        self._spin_opex.setDecimals(1)
        params_form.addRow(tr("wizard_solar_pv.opex"), self._spin_opex)

        self._spin_discount = QDoubleSpinBox()
        self._spin_discount.setRange(0.01, 0.30)
        self._spin_discount.setValue(0.08)
        self._spin_discount.setSingleStep(0.01)
        self._spin_discount.setDecimals(3)
        params_form.addRow(tr("wizard_solar_pv.discount_rate"), self._spin_discount)

        self._spin_lifetime = QSpinBox()
        self._spin_lifetime.setRange(5, 40)
        self._spin_lifetime.setValue(25)
        self._spin_lifetime.setSuffix(" yr")
        params_form.addRow(tr("wizard_solar_pv.lifetime"), self._spin_lifetime)

        self._spin_price = QDoubleSpinBox()
        self._spin_price.setRange(5, 500)
        self._spin_price.setValue(50)
        self._spin_price.setSuffix(" $/MWh")
        self._spin_price.setDecimals(1)
        params_form.addRow(tr("wizard_solar_pv.elec_price"), self._spin_price)

        self._spin_degradation = QDoubleSpinBox()
        self._spin_degradation.setRange(0, 0.05)
        self._spin_degradation.setValue(0.005)
        self._spin_degradation.setSingleStep(0.001)
        self._spin_degradation.setDecimals(4)
        params_form.addRow(tr("wizard_solar_pv.degradation"), self._spin_degradation)

        layout.addWidget(params_group)

        # Calculate button
        btn_row = QHBoxLayout()
        self._btn_calc = QPushButton(tr("wizard_solar_pv.fin_calculate"))
        self._btn_calc.clicked.connect(self._calculate)
        btn_row.addWidget(self._btn_calc)

        self._btn_export = QPushButton(tr("wizard_solar_pv.fin_export"))
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
        sens_row.addWidget(QLabel(tr("wizard_solar_pv.sensitivity")))
        self._sens_param = QComboBox()
        self._sens_param.addItem(tr("wizard_solar_pv.capex"), "capex_per_kw")
        self._sens_param.addItem(tr("wizard_solar_pv.discount_rate"), "discount_rate")
        self._sens_param.addItem(tr("wizard_solar_pv.elec_price"), "electricity_price")
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
        from solarex import SolarFinancialInputs
        return SolarFinancialInputs(
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
        from solarex import compute_pv_financials

        inputs = self._get_inputs()
        results = compute_pv_financials(inputs)
        self._financial_results = results

        lines = [
            f"<b>{tr('wizard_solar_pv.lcoe')}:</b> {results.lcoe:.2f} $/MWh",
            f"<b>{tr('wizard_solar_pv.npv')}:</b> ${results.npv:,.0f}",
            f"<b>{tr('wizard_solar_pv.irr')}:</b> {results.irr * 100:.1f}%",
            f"<b>{tr('wizard_solar_pv.payback')}:</b> {results.payback_years:.1f} yr",
            f"<b>{tr('wizard_solar_pv.annual_gen')}:</b> "
            f"{results.annual_revenue / inputs.electricity_price:,.0f} MWh/yr",
            f"<b>CAPEX:</b> ${results.capex_total:,.0f}",
        ]
        self._results_label.setText("<br>".join(lines))
        self._btn_export.setEnabled(True)
        self._update_sensitivity()

    def _update_sensitivity(self):
        """Compute sensitivity on background thread."""
        if self._financial_results is None:
            return

        import numpy as np

        inputs = self._get_inputs()
        param_name = self._sens_param.currentData()

        current_val = getattr(inputs, param_name, 1.0)
        if current_val <= 0:
            current_val = 1.0
        sweep = np.linspace(current_val * 0.5, current_val * 1.5, 30)

        self._sens_worker = _SolarSensitivityWorker(
            inputs, param_name, sweep, self._max_workers, self,
        )
        self._sens_worker.finished.connect(self._on_sensitivity_done)
        self._sens_worker.start()

    def _on_sensitivity_done(self, sweep, lcoes):
        """Render sensitivity chart on main thread."""
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
        ax.set_title(tr("wizard_solar_pv.sensitivity"), color="black", fontsize=10)
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
            self, tr("wizard_solar_pv.fin_export"), "solar_pv_financial_summary.csv",
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

    def is_valid(self) -> bool:
        return True


# =====================================================================
# Step 8: Array / Shading Analysis
# =====================================================================


class SolarArrayStep(QWidget):
    """GCR shading analysis, bifacial gain, row spacing optimization."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._latitude = 0.0
        self._tilt = 20.0
        self._capacity_factor = 0.20
        self._capacity_mw = 10.0
        self._is_bifacial = False
        self._max_workers = 0
        self._shading_loss = 0.0
        self._bifacial_gain = 0.0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Parameters group
        params_group = QGroupBox(tr("wizard_solar_pv.array_title"))
        params_form = QFormLayout(params_group)

        self._spin_gcr = QDoubleSpinBox()
        self._spin_gcr.setRange(0.15, 0.90)
        self._spin_gcr.setValue(0.40)
        self._spin_gcr.setSingleStep(0.05)
        self._spin_gcr.setDecimals(2)
        params_form.addRow(tr("wizard_solar_pv.gcr"), self._spin_gcr)

        self._spin_mod_height = QDoubleSpinBox()
        self._spin_mod_height.setRange(1.0, 4.0)
        self._spin_mod_height.setValue(2.0)
        self._spin_mod_height.setSuffix(" m")
        self._spin_mod_height.setSingleStep(0.1)
        self._spin_mod_height.setDecimals(1)
        params_form.addRow(tr("wizard_solar_pv.module_height"), self._spin_mod_height)

        self._spin_albedo = QDoubleSpinBox()
        self._spin_albedo.setRange(0.0, 0.80)
        self._spin_albedo.setValue(0.25)
        self._spin_albedo.setSingleStep(0.05)
        self._spin_albedo.setDecimals(2)
        params_form.addRow(tr("wizard_solar_pv.ground_albedo"), self._spin_albedo)

        self._chk_bifacial = QCheckBox(tr("wizard_solar_pv.bifacial_gain"))
        self._chk_bifacial.setChecked(False)
        params_form.addRow("", self._chk_bifacial)

        layout.addWidget(params_group)

        # Calculate button
        btn_row = QHBoxLayout()
        self._btn_calc = QPushButton(tr("wizard_solar_pv.array_calculate"))
        self._btn_calc.clicked.connect(self._calculate)
        btn_row.addWidget(self._btn_calc)

        self._btn_export = QPushButton(tr("wizard_solar_pv.array_export"))
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

        # GCR curve chart
        self._chart_widget = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_widget)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chart_widget, 1)

    def set_inputs(
        self,
        latitude: float,
        tilt: float,
        capacity_factor: float,
        capacity_mw: float,
        is_bifacial: bool = False,
        max_workers: int = 0,
    ):
        """Receive site and module info from upstream steps."""
        self._latitude = latitude
        self._tilt = tilt
        self._capacity_factor = capacity_factor
        self._capacity_mw = capacity_mw
        self._is_bifacial = is_bifacial
        self._max_workers = max_workers
        self._chk_bifacial.setChecked(is_bifacial)
        self._chk_bifacial.setEnabled(is_bifacial)

    def _calculate(self):
        """Launch GCR computation on background thread."""
        gcr = self._spin_gcr.value()
        mod_h = self._spin_mod_height.value()
        albedo = self._spin_albedo.value()
        is_bifi = self._chk_bifacial.isChecked()

        self._btn_calc.setEnabled(False)
        self._btn_calc.setText("...")
        self._pending_gcr = gcr

        self._gcr_worker = _GCRWorker(
            self._latitude, self._tilt, gcr, mod_h, albedo, is_bifi,
            self._max_workers, self,
        )
        self._gcr_worker.finished.connect(self._on_gcr_done)
        self._gcr_worker.start()

    def _on_gcr_done(self, loss, gcrs, losses, bifi_gain):
        """Display results and chart on main thread."""
        self._btn_calc.setEnabled(True)
        self._btn_calc.setText(tr("wizard_solar_pv.array_calculate"))

        self._shading_loss = loss
        self._bifacial_gain = bifi_gain
        gcr = self._pending_gcr

        net_factor = (1.0 - loss) * (1.0 + bifi_gain)
        net_cf = self._capacity_factor * net_factor

        lines = [
            f"<b>{tr('wizard_solar_pv.gcr')}:</b> {gcr:.2f}",
            f"<b>{tr('wizard_solar_pv.shading_loss')}:</b> {loss * 100:.1f}%",
        ]
        if bifi_gain > 0:
            lines.append(
                f"<b>{tr('wizard_solar_pv.bifacial_gain')}:</b> +{bifi_gain * 100:.1f}%"
            )
        lines.extend([
            f"<b>{tr('wizard_solar_pv.net_efficiency')}:</b> {net_factor * 100:.1f}%",
            f"<b>Gross CF:</b> {self._capacity_factor:.3f} "
            f"({self._capacity_factor * 100:.1f}%)",
            f"<b>Net CF:</b> {net_cf:.3f} ({net_cf * 100:.1f}%)",
            f"<b>{tr('wizard_solar_pv.annual_gen')}:</b> "
            f"{self._capacity_mw * net_cf * 8760:,.0f} MWh/yr",
        ])
        self._results_label.setText("<br>".join(lines))
        self._btn_export.setEnabled(True)

        self._draw_gcr_curve(gcrs, losses, gcr)

    def _draw_gcr_curve(self, gcrs, losses, current_gcr):
        """Draw shading loss vs GCR chart."""
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

        import numpy as np

        fig = Figure(figsize=(8, 3.5))
        ax = fig.add_subplot(1, 1, 1)
        _white_chart_style(fig, ax)

        ax.plot(gcrs, [l * 100 for l in losses], "o-",
                color="#e67e22", linewidth=2, markersize=5)
        ax.axvline(current_gcr, color="#e74c3c", linestyle="--",
                   linewidth=1.5, label=f"Selected: {current_gcr:.2f}")

        # Mark current point
        idx = np.argmin(np.abs(np.array(gcrs) - current_gcr))
        ax.plot(gcrs[idx], losses[idx] * 100, "s",
                color="#e74c3c", markersize=10, zorder=5)

        ax.set_xlabel(tr("wizard_solar_pv.gcr"), color="black")
        ax.set_ylabel(tr("wizard_solar_pv.shading_loss") + " (%)", color="black")
        ax.set_title(tr("wizard_solar_pv.gcr_curve"), color="black", fontsize=10)
        ax.legend(fontsize=8, facecolor="white", edgecolor="#ccc", labelcolor="black")

        fig.tight_layout()
        canvas = FigureCanvasQTAgg(fig)
        self._chart_layout.addWidget(canvas)
        self._fig = fig

    def _export_results(self):
        """Export array analysis to CSV."""
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar_pv.array_export"), "solar_pv_array_analysis.csv",
            "CSV (*.csv)",
        )
        if not path:
            return

        net_factor = (1.0 - self._shading_loss) * (1.0 + self._bifacial_gain)
        net_cf = self._capacity_factor * net_factor

        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Parameter", "Value", "Unit"])
            w.writerow(["Latitude", self._latitude, "°"])
            w.writerow(["Tilt", self._tilt, "°"])
            w.writerow(["GCR", self._spin_gcr.value(), ""])
            w.writerow(["Module Height", self._spin_mod_height.value(), "m"])
            w.writerow(["Ground Albedo", self._spin_albedo.value(), ""])
            w.writerow(["Shading Loss", f"{self._shading_loss * 100:.1f}", "%"])
            w.writerow(["Bifacial Gain", f"{self._bifacial_gain * 100:.1f}", "%"])
            w.writerow(["Net Efficiency", f"{net_factor * 100:.1f}", "%"])
            w.writerow(["Gross CF", f"{self._capacity_factor:.4f}", ""])
            w.writerow(["Net CF", f"{net_cf:.4f}", ""])
            w.writerow([
                "Annual Generation",
                f"{self._capacity_mw * net_cf * 8760:.0f}",
                "MWh/yr",
            ])

    def is_valid(self) -> bool:  # noqa: D102 — SolarArrayStep
        return True


# =====================================================================
# Step 9: Availability Profile Generation
# =====================================================================

_SOLAR_FUEL_HINTS = {"sun", "solar", "pv", "photovoltaic", "fotovoltaic"}
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


class _SolarProfileWorker(QThread):
    """Generate solar PV availability profiles on a background thread."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        gen_groups,
        hourly_data,
        solar_config,
        output_dir,
        year,
        parent=None,
    ):
        super().__init__(parent)
        self._gen_groups = gen_groups
        self._hourly_data = hourly_data
        self._solar_config = solar_config
        self._output_dir = output_dir
        self._year = year

    def run(self):
        import numpy as np
        from pathlib import Path

        try:
            from solarex import compute_solar_hourly_cf
            from solarex.core.capacity_factor import _irradiance_to_hourly_cf
        except ImportError:
            self.error.emit(
                "Could not import solar CF functions from solarex"
            )
            return

        try:
            output_dir = Path(self._output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            cfg = self._solar_config
            efficiency = cfg.module_efficiency if cfg else 0.20
            gamma_pmax = cfg.module_gamma_pmax if cfg else -0.40
            t_noct = cfg.module_t_noct if cfg else 45.0
            tilt_val = cfg.tilt if cfg and cfg.orientation == "custom" else None
            azimuth = cfg.azimuth if cfg else 180.0
            tracking = cfg.tracking if cfg else "none"
            data_source = cfg.data_source if cfg else "open_meteo"

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
                            cell_data = self._hourly_data[cell]
                            cf = _irradiance_to_hourly_cf(
                                cell_data.ghi,
                                cell_data.temperature,
                                efficiency,
                                gamma_pmax,
                                t_noct,
                            )

                    if cf is None:
                        cf = compute_solar_hourly_cf(
                            lat, lon, self._year, data_source,
                            efficiency=efficiency,
                            gamma_pmax=gamma_pmax,
                            t_noct=t_noct,
                            tilt=tilt_val,
                            azimuth=azimuth,
                            tracking=tracking,
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


class SolarAvailabilityStep(QWidget):
    """Step 9 — Generate availability profiles for solar PV generators."""

    def __init__(self, model=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._hourly_data = None
        self._solar_config = None
        self._summary = None
        self._worker = None
        self._results = {}
        self._gen_rows = []

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Instruction
        self._lbl_instruction = QLabel(tr("wizard_solar_pv.avail_instruction"))
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
        btn_sel = QPushButton(tr("wizard_solar_pv.avail_select_all"))
        btn_sel.clicked.connect(lambda: self._set_all_checked(True))
        btn_desel = QPushButton(tr("wizard_solar_pv.avail_deselect_all"))
        btn_desel.clicked.connect(lambda: self._set_all_checked(False))
        top_row.addWidget(btn_sel)
        top_row.addWidget(btn_desel)
        top_row.addStretch()

        top_row.addWidget(QLabel(tr("wizard_solar_pv.avail_output_dir")))
        self._lbl_output_dir = QLabel("./availability/")
        self._lbl_output_dir.setStyleSheet(
            "background-color: #333; padding: 2px 6px; border-radius: 3px;"
        )
        top_row.addWidget(self._lbl_output_dir)
        btn_browse = QPushButton(tr("wizard_solar_pv.avail_browse"))
        btn_browse.clicked.connect(self._browse_output_dir)
        top_row.addWidget(btn_browse)
        layout.addLayout(top_row)

        # Generator table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "",
            tr("wizard_solar_pv.avail_col_name"),
            tr("wizard_solar_pv.avail_col_unit"),
            tr("wizard_solar_pv.avail_col_node"),
            tr("wizard_solar_pv.avail_col_position"),
            tr("wizard_solar_pv.avail_col_status"),
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
                tr("wizard_solar_pv.avail_preview"),
                fontsize=10, color="black",
            )
            layout.addWidget(self._canvas, 1)
        except ImportError:
            self._canvas = None
            layout.addWidget(QLabel("(matplotlib not available)"))

        # Generate button + progress
        gen_row = QHBoxLayout()
        self._btn_generate = QPushButton(tr("wizard_solar_pv.avail_generate"))
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

    def set_inputs(self, hourly_data, solar_config, summary):
        """Called by wizard when transitioning to this step."""
        self._hourly_data = hourly_data
        self._solar_config = solar_config
        self._summary = summary

        if not hourly_data:
            self._lbl_warning.setText(tr("wizard_solar_pv.avail_no_data"))
            self._lbl_warning.setVisible(True)
        else:
            self._lbl_warning.setVisible(False)

        self._resolve_output_dir()
        self._populate_table()

    def is_valid(self) -> bool:  # noqa: D102 — SolarAvailabilityStep
        return True

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self):
        self._table.setRowCount(0)
        self._gen_rows = []

        if self._model is None:
            self._lbl_status.setText(
                tr("wizard_solar_pv.avail_no_generators"),
            )
            self._btn_generate.setEnabled(False)
            return

        state = self._model.state
        gens = getattr(state, "generators", {})

        for gen_id, gen in gens.items():
            gen_type = getattr(gen, "gen_type", "")
            fuel = getattr(gen, "fuel", "")
            if gen_type != "Renewable":
                continue
            if fuel.lower() not in _SOLAR_FUEL_HINTS:
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
                row, 5,
                QTableWidgetItem(tr("wizard_solar_pv.avail_status_pending")),
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
            self._lbl_status.setText(
                tr("wizard_solar_pv.avail_no_generators"),
            )
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
            self, tr("wizard_solar_pv.avail_output_dir"),
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
                self, tr("wizard_solar_pv.avail_error_title"),
                tr("wizard_solar_pv.avail_no_selection"),
            )
            return

        # Group by unit_key
        from collections import defaultdict
        groups = defaultdict(list)
        for info in selected:
            groups[info["unit_key"]].append(info)

        year = self._solar_config.year if self._solar_config else 2022
        output_dir = self._lbl_output_dir.text()

        self._btn_generate.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)

        self._worker = _SolarProfileWorker(
            dict(groups),
            self._hourly_data,
            self._solar_config,
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
                    txt = (
                        f"{tr('wizard_solar_pv.avail_status_done')}"
                        f" (CF={mean_cf:.3f})"
                    )
                elif status == "skipped":
                    txt = tr("wizard_solar_pv.avail_status_skipped")
                else:
                    txt = tr("wizard_solar_pv.avail_status_error")
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
            tr("wizard_solar_pv.avail_summary").format(n=n_done, cf=avg_cf)
        )

        # Preview first result
        if self._canvas and cf_values:
            self._draw_preview(results)

        QMessageBox.information(
            self, tr("wizard_solar_pv.avail_done_title"),
            tr("wizard_solar_pv.avail_done_msg").format(n=n_done),
        )

    def _on_error(self, msg):
        self._btn_generate.setEnabled(True)
        self._progress.setVisible(False)
        QMessageBox.critical(
            self, tr("wizard_solar_pv.avail_error_title"), msg,
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
            tr("wizard_solar_pv.avail_preview"),
            fontsize=10, color="black",
        )
        self._ax.set_xlim(0, 8760)
        self._ax.set_ylim(0, 1)
        self._fig.tight_layout()
        self._canvas.draw()
