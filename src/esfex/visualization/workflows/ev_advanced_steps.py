"""Phase B step widgets for the EV & V2G Assessment wizard.

Steps 6-9: Charging Demand, V2G Potential, Grid Impact, Integration.
"""

from __future__ import annotations

import csv
import json
import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

from evrex import (
    DEFAULT_CATEGORIES,
    DEFAULT_CONNECTED_PROFILE,
    ChargingScenarioResult,
    DegradationResult,
    EVAdoptionCurve,
    EVMacroData,
    GridImpactResult,
    TransportContext,
    V2GPotential,
    assess_grid_impact,
    compute_battery_degradation,
    compute_v2g_potential,
    fit_adoption_to_ev_config,
    generate_all_scenarios,
)

logger = logging.getLogger(__name__)

# Default EV technical parameters per category
_DEFAULT_EV_PARAMS: dict[str, dict] = {
    "light": {
        "charging_power": 7.0, "battery_capacity": 50.0,
        "v2g_power": 5.0, "v2g_participation": 0.3,
        "efficiency_discharge": 0.90, "energy_consumption": 18.0,
        "avg_daily_km": 40.0,
    },
    "medium": {
        "charging_power": 11.0, "battery_capacity": 75.0,
        "v2g_power": 8.0, "v2g_participation": 0.4,
        "efficiency_discharge": 0.90, "energy_consumption": 25.0,
        "avg_daily_km": 80.0,
    },
    "heavy": {
        "charging_power": 22.0, "battery_capacity": 150.0,
        "v2g_power": 15.0, "v2g_participation": 0.5,
        "efficiency_discharge": 0.90, "energy_consumption": 55.0,
        "avg_daily_km": 150.0,
    },
    "buses": {
        "charging_power": 50.0, "battery_capacity": 300.0,
        "v2g_power": 40.0, "v2g_participation": 0.7,
        "efficiency_discharge": 0.90, "energy_consumption": 80.0,
        "avg_daily_km": 200.0,
    },
}


# =====================================================================
# Step 6: Charging Demand
# =====================================================================


class EVChargingDemandStep(QWidget):
    """Generate and visualize 3 charging demand scenarios."""

    scenariosGenerated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curve: Optional[EVAdoptionCurve] = None
        self._scenarios: dict[str, ChargingScenarioResult] = {}
        self._target_year_fleet: dict[str, int] = {}
        self._fig = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel(tr("wizard_ev.charging_instruction")))

        # -- Smart charging fraction --
        slider_grp = QGroupBox(tr("wizard_ev.smart_charging_title"))
        sg = QHBoxLayout(slider_grp)
        sg.addWidget(QLabel(tr("wizard_ev.smart_fraction")))
        self._slider_smart = QSlider(Qt.Orientation.Horizontal)
        self._slider_smart.setRange(0, 100)
        self._slider_smart.setValue(50)
        self._slider_smart.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider_smart.setTickInterval(10)
        sg.addWidget(self._slider_smart)
        self._lbl_smart = QLabel("50 %")
        self._slider_smart.valueChanged.connect(
            lambda v: self._lbl_smart.setText(f"{v} %")
        )
        sg.addWidget(self._lbl_smart)
        layout.addWidget(slider_grp)

        # -- Base demand (optional) --
        demand_grp = QGroupBox(tr("wizard_ev.base_demand_title"))
        dg = QHBoxLayout(demand_grp)
        self._btn_load_demand = QPushButton(tr("wizard_ev.load_demand_csv"))
        self._btn_load_demand.clicked.connect(self._load_demand)
        dg.addWidget(self._btn_load_demand)
        self._lbl_demand = QLabel(tr("wizard_ev.no_demand_loaded"))
        dg.addWidget(self._lbl_demand)
        layout.addWidget(demand_grp)
        self._base_demand_24h: Optional[list[float]] = None

        # -- Target year selector --
        year_row = QHBoxLayout()
        year_row.addWidget(QLabel(tr("wizard_ev.analysis_year")))
        self._spin_year = QSpinBox()
        self._spin_year.setRange(2025, 2070)
        self._spin_year.setValue(2035)
        year_row.addWidget(self._spin_year)
        year_row.addStretch()
        layout.addLayout(year_row)

        # -- Generate button --
        self._btn_generate = QPushButton(tr("wizard_ev.generate_profiles"))
        self._btn_generate.clicked.connect(self._generate)
        layout.addWidget(self._btn_generate)

        # -- Chart area --
        self._chart_container = QVBoxLayout()
        layout.addLayout(self._chart_container)

        # -- Summary table --
        self._summary_table = QTableWidget(3, 3)
        self._summary_table.setHorizontalHeaderLabels([
            tr("wizard_ev.col_scenario"),
            tr("wizard_ev.col_peak_mw"),
            tr("wizard_ev.col_daily_mwh"),
        ])
        self._summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._summary_table.setMaximumHeight(130)
        layout.addWidget(self._summary_table)

        layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    def set_curve(self, curve: EVAdoptionCurve):
        self._curve = curve
        if curve and curve.years:
            mid = curve.years[len(curve.years) // 2]
            self._spin_year.setValue(mid)

    def _load_demand(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wizard_ev.load_demand_csv"), "", "CSV (*.csv)",
        )
        if not path:
            return
        try:
            values = []
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    for cell in row:
                        try:
                            values.append(float(cell))
                        except ValueError:
                            pass
                    if len(values) >= 24:
                        break
            self._base_demand_24h = values[:24]
            self._lbl_demand.setText(
                f"Loaded: peak={max(self._base_demand_24h):.0f} MW"
            )
        except Exception as exc:
            QMessageBox.warning(self, "CSV Error", str(exc))

    def _get_fleet_at_year(self) -> dict[str, int]:
        """Extract fleet per category at selected year from curve."""
        if not self._curve or not self._curve.years:
            return {cat: 100 for cat in DEFAULT_CATEGORIES}

        year = self._spin_year.value()
        if year in self._curve.years:
            idx = self._curve.years.index(year)
        else:
            idx = min(
                range(len(self._curve.years)),
                key=lambda i: abs(self._curve.years[i] - year),
            )

        fleet = {}
        for cat in DEFAULT_CATEGORIES:
            cat_list = self._curve.fleet_by_category.get(cat, [])
            fleet[cat] = cat_list[idx] if idx < len(cat_list) else 0
        return fleet

    def _generate(self):
        fleet = self._get_fleet_at_year()
        self._target_year_fleet = fleet

        self._scenarios = generate_all_scenarios(
            fleet_by_category=fleet,
            ev_categories=_DEFAULT_EV_PARAMS,
            smart_charging_fraction=self._slider_smart.value() / 100.0,
            base_demand_24h=self._base_demand_24h,
        )
        self._build_chart()
        self._build_summary()
        self.scenariosGenerated.emit()

    def _build_chart(self):
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_container.addWidget(QLabel("matplotlib not available"))
            return

        while self._chart_container.count():
            item = self._chart_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._fig = Figure(figsize=(9, 4), facecolor="white")
        canvas = FigureCanvasQTAgg(self._fig)
        ax = self._fig.add_subplot(111)

        hours = list(range(24))
        colors = {
            "uncontrolled": "#e74c3c",
            "tou_shifted": "#f39c12",
            "optimized": "#27ae60",
        }
        labels = {
            "uncontrolled": "Uncontrolled",
            "tou_shifted": "Time-of-Use Shifted",
            "optimized": "Optimized",
        }

        for name, scenario in self._scenarios.items():
            ax.plot(
                hours, scenario.aggregate_hourly_mw,
                label=labels.get(name, name), color=colors.get(name, "#999"),
                linewidth=2,
            )

        if self._base_demand_24h:
            ax.plot(
                hours, self._base_demand_24h[:24],
                label="Base Demand", color="#3498db",
                linewidth=1.5, linestyle="--", alpha=0.6,
            )

        ax.set_xlabel("Hour of Day")
        ax.set_ylabel("Charging Demand (MW)")
        ax.set_title(f"EV Charging Profiles — Year {self._spin_year.value()}")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xticks(range(0, 24, 2))
        self._fig.tight_layout()
        self._chart_container.addWidget(canvas)

    def _build_summary(self):
        scenario_order = ["uncontrolled", "tou_shifted", "optimized"]
        labels = ["Uncontrolled", "Time-of-Use Shifted", "Optimized"]
        self._summary_table.setRowCount(3)

        for row, (key, label) in enumerate(zip(scenario_order, labels)):
            s = self._scenarios.get(key)
            if s is None:
                continue
            for col, val in enumerate([
                label,
                f"{s.peak_demand_mw:.1f}",
                f"{s.daily_energy_mwh:.1f}",
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._summary_table.setItem(row, col, item)

    def get_scenarios(self) -> dict[str, ChargingScenarioResult]:
        return self._scenarios

    def get_fleet_at_year(self) -> dict[str, int]:
        return self._target_year_fleet

    def get_base_demand(self) -> Optional[list[float]]:
        return self._base_demand_24h

    def get_analysis_year(self) -> int:
        return self._spin_year.value()

    def is_valid(self) -> bool:
        if not self._scenarios:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_charging_profiles")
            )
            return False
        return True


# =====================================================================
# Step 7: V2G Technical Potential
# =====================================================================


class EVV2GPotentialStep(QWidget):
    """Assess V2G potential and battery degradation."""

    analysisComplete = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fleet: dict[str, int] = {}
        self._v2g: Optional[V2GPotential] = None
        self._degradation: Optional[DegradationResult] = None
        self._fig = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel(tr("wizard_ev.v2g_instruction")))

        # -- Connected-time profile (table) --
        conn_grp = QGroupBox(tr("wizard_ev.connected_profile_title"))
        cg = QVBoxLayout(conn_grp)

        self._conn_table = QTableWidget(4, 6)
        self._conn_table.setVerticalHeaderLabels([
            "00-05", "06-11", "12-17", "18-23",
        ])
        self._conn_table.setHorizontalHeaderLabels([
            "h+0", "h+1", "h+2", "h+3", "h+4", "h+5",
        ])
        self._conn_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        # Fill with defaults
        for row in range(4):
            for col in range(6):
                idx = row * 6 + col
                val = DEFAULT_CONNECTED_PROFILE[idx] if idx < 24 else 0.5
                item = QTableWidgetItem(f"{val:.2f}")
                self._conn_table.setItem(row, col, item)

        cg.addWidget(self._conn_table)
        cg.addWidget(QLabel(tr("wizard_ev.connected_hint")))
        layout.addWidget(conn_grp)

        # -- SOC and degradation parameters --
        params_grp = QGroupBox(tr("wizard_ev.v2g_params_title"))
        pf = QFormLayout(params_grp)

        self._spin_min_soc = QDoubleSpinBox()
        self._spin_min_soc.setRange(0.10, 0.60)
        self._spin_min_soc.setValue(0.30)
        self._spin_min_soc.setSingleStep(0.05)
        self._spin_min_soc.setDecimals(2)
        pf.addRow(tr("wizard_ev.min_soc"), self._spin_min_soc)

        self._spin_max_soc = QDoubleSpinBox()
        self._spin_max_soc.setRange(0.60, 1.00)
        self._spin_max_soc.setValue(0.90)
        self._spin_max_soc.setSingleStep(0.05)
        self._spin_max_soc.setDecimals(2)
        pf.addRow(tr("wizard_ev.max_soc"), self._spin_max_soc)

        self._spin_cycles = QDoubleSpinBox()
        self._spin_cycles.setRange(0.1, 3.0)
        self._spin_cycles.setValue(0.5)
        self._spin_cycles.setSingleStep(0.1)
        self._spin_cycles.setDecimals(1)
        pf.addRow(tr("wizard_ev.v2g_cycles_per_day"), self._spin_cycles)

        self._combo_chemistry = QComboBox()
        self._combo_chemistry.addItems(["NMC", "LFP"])
        pf.addRow(tr("wizard_ev.battery_chemistry"), self._combo_chemistry)

        self._spin_bat_cap = QDoubleSpinBox()
        self._spin_bat_cap.setRange(20, 500)
        self._spin_bat_cap.setValue(50)
        self._spin_bat_cap.setSuffix(" kWh")
        pf.addRow(tr("wizard_ev.avg_battery_capacity"), self._spin_bat_cap)

        layout.addWidget(params_grp)

        # -- Run button --
        self._btn_analyze = QPushButton(tr("wizard_ev.run_v2g_analysis"))
        self._btn_analyze.clicked.connect(self._run_analysis)
        layout.addWidget(self._btn_analyze)

        # -- Charts --
        self._chart_container = QVBoxLayout()
        layout.addLayout(self._chart_container)

        # -- Degradation summary --
        self._deg_summary = QLabel("")
        self._deg_summary.setWordWrap(True)
        layout.addWidget(self._deg_summary)

        layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    def set_fleet(self, fleet: dict[str, int]):
        self._fleet = fleet

    def _read_connected_profile(self) -> list[float]:
        profile = []
        for row in range(4):
            for col in range(6):
                item = self._conn_table.item(row, col)
                try:
                    val = float(item.text())
                    val = max(0.0, min(1.0, val))
                except (ValueError, AttributeError):
                    val = 0.5
                profile.append(val)
        return profile

    def _run_analysis(self):
        if not self._fleet or sum(self._fleet.values()) == 0:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_fleet_data")
            )
            return

        connected = self._read_connected_profile()
        min_soc = self._spin_min_soc.value()
        max_soc = self._spin_max_soc.value()

        self._v2g = compute_v2g_potential(
            fleet_by_category=self._fleet,
            ev_categories=_DEFAULT_EV_PARAMS,
            connected_profile=connected,
            v2g_min_soc=min_soc,
            v2g_max_soc=max_soc,
        )

        dod = max_soc - min_soc
        self._degradation = compute_battery_degradation(
            v2g_cycles_per_day=self._spin_cycles.value(),
            battery_capacity_kwh=self._spin_bat_cap.value(),
            depth_of_discharge=dod,
            chemistry=self._combo_chemistry.currentText(),
        )

        self._build_chart()
        self._update_degradation_summary()
        self.analysisComplete.emit()

    def _build_chart(self):
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_container.addWidget(QLabel("matplotlib not available"))
            return

        while self._chart_container.count():
            item = self._chart_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._fig = Figure(figsize=(9, 4), facecolor="white")
        canvas = FigureCanvasQTAgg(self._fig)

        ax1 = self._fig.add_subplot(121)
        ax2 = self._fig.add_subplot(122)

        hours = list(range(24))

        # V2G power availability
        ax1.fill_between(
            hours, self._v2g.max_v2g_power_mw,
            alpha=0.4, color="#2ecc71", label="Max V2G Power",
        )
        ax1.plot(
            hours, self._v2g.max_v2g_power_mw,
            color="#27ae60", linewidth=2,
        )
        ax1.set_xlabel("Hour of Day")
        ax1.set_ylabel("V2G Power (MW)")
        ax1.set_title("V2G Discharge Capacity")
        ax1.legend(fontsize=8)
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(range(0, 24, 4))

        # Connected fraction
        connected = self._v2g.hourly_connected_fraction
        ax2.bar(hours, connected, color="#3498db", alpha=0.7, label="Connected")
        ax2.set_xlabel("Hour of Day")
        ax2.set_ylabel("Fraction Connected")
        ax2.set_title("Fleet Connected Profile")
        ax2.set_ylim(0, 1.0)
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(range(0, 24, 4))

        self._fig.tight_layout()
        self._chart_container.addWidget(canvas)

    def _update_degradation_summary(self):
        if self._degradation is None or self._v2g is None:
            return

        d = self._degradation
        v = self._v2g
        text = (
            f"<b>{d.chemistry}</b> — "
            f"Total degradation: {d.total_degradation_pct_per_year:.2f} %/year "
            f"(cycle: {d.total_degradation_pct_per_year - d.calendar_aging_pct_per_year:.2f}% "
            f"+ calendar: {d.calendar_aging_pct_per_year:.1f}%)<br>"
            f"Degradation cost: <b>${d.degradation_cost_per_kwh:.4f}/kWh</b> | "
            f"Break-even compensation: <b>${d.breakeven_compensation:.1f}/MWh</b><br>"
            f"Daily V2G energy: {v.daily_v2g_energy_mwh:.1f} MWh | "
            f"Annual V2G potential: {v.annual_v2g_potential_gwh:.2f} GWh"
        )
        self._deg_summary.setText(text)

    def get_v2g_potential(self) -> Optional[V2GPotential]:
        return self._v2g

    def get_degradation(self) -> Optional[DegradationResult]:
        return self._degradation

    def is_valid(self) -> bool:
        if self._v2g is None:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_v2g_analysis")
            )
            return False
        return True


# =====================================================================
# Step 8: Grid Impact
# =====================================================================


class EVGridImpactStep(QWidget):
    """Assess grid impact of EV charging and V2G."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scenarios: dict[str, ChargingScenarioResult] = {}
        self._v2g: Optional[V2GPotential] = None
        self._base_demand: Optional[list[float]] = None
        self._result: Optional[GridImpactResult] = None
        self._fig = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel(tr("wizard_ev.grid_instruction")))

        # -- Scenario selector --
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel(tr("wizard_ev.charging_scenario")))
        self._combo_scenario = QComboBox()
        self._combo_scenario.addItems([
            "Uncontrolled", "Time-of-Use Shifted", "Optimized",
        ])
        self._combo_scenario.setCurrentIndex(2)
        sel_row.addWidget(self._combo_scenario)

        self._spin_compensation = QDoubleSpinBox()
        self._spin_compensation.setRange(0, 500)
        self._spin_compensation.setValue(50)
        self._spin_compensation.setPrefix("$ ")
        self._spin_compensation.setSuffix("/MWh")
        sel_row.addWidget(QLabel(tr("wizard_ev.v2g_compensation")))
        sel_row.addWidget(self._spin_compensation)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # -- Analyze button --
        self._btn_analyze = QPushButton(tr("wizard_ev.run_grid_analysis"))
        self._btn_analyze.clicked.connect(self._run_analysis)
        layout.addWidget(self._btn_analyze)

        # -- Chart area --
        self._chart_container = QVBoxLayout()
        layout.addLayout(self._chart_container)

        # -- Flexibility table --
        flex_grp = QGroupBox(tr("wizard_ev.flexibility_title"))
        fg = QVBoxLayout(flex_grp)

        self._flex_table = QTableWidget(6, 2)
        self._flex_table.setHorizontalHeaderLabels([
            tr("wizard_ev.col_metric"), tr("wizard_ev.col_value"),
        ])
        self._flex_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._flex_table.setMaximumHeight(220)
        fg.addWidget(self._flex_table)
        layout.addWidget(flex_grp)

        # -- Economic summary --
        econ_grp = QGroupBox(tr("wizard_ev.economic_title"))
        eg = QVBoxLayout(econ_grp)
        self._econ_label = QLabel("")
        self._econ_label.setWordWrap(True)
        eg.addWidget(self._econ_label)
        layout.addWidget(econ_grp)

        # -- Export --
        export_row = QHBoxLayout()
        self._btn_export = QPushButton(tr("wizard_ev.export_grid_csv"))
        self._btn_export.clicked.connect(self._export_csv)
        export_row.addWidget(self._btn_export)
        export_row.addStretch()
        layout.addLayout(export_row)

        layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    def set_inputs(
        self,
        scenarios: dict[str, ChargingScenarioResult],
        v2g: Optional[V2GPotential],
        base_demand: Optional[list[float]],
    ):
        self._scenarios = scenarios
        self._v2g = v2g
        self._base_demand = base_demand

    def _get_selected_scenario_key(self) -> str:
        idx = self._combo_scenario.currentIndex()
        return ["uncontrolled", "tou_shifted", "optimized"][idx]

    def _run_analysis(self):
        key = self._get_selected_scenario_key()
        scenario = self._scenarios.get(key)
        if scenario is None:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_charging_profiles")
            )
            return

        if self._v2g is None:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_v2g_analysis")
            )
            return

        # Use base demand or generate synthetic
        if self._base_demand and len(self._base_demand) >= 24:
            base = self._base_demand[:24]
        else:
            # Synthetic typical daily demand (MW)
            hours = np.arange(24)
            base = (
                200
                + 80 * np.exp(-0.5 * ((hours - 9) / 2.0) ** 2)
                + 100 * np.exp(-0.5 * ((hours - 20) / 2.5) ** 2)
            ).tolist()

        self._result = assess_grid_impact(
            base_demand_24h=base,
            ev_charging_24h=scenario.aggregate_hourly_mw,
            v2g_potential=self._v2g,
            v2g_compensation_per_mwh=self._spin_compensation.value(),
        )

        self._build_chart()
        self._build_flexibility_table()
        self._build_economic_summary()

    def _build_chart(self):
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_container.addWidget(QLabel("matplotlib not available"))
            return

        while self._chart_container.count():
            item = self._chart_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        r = self._result
        self._fig = Figure(figsize=(10, 4), facecolor="white")
        canvas = FigureCanvasQTAgg(self._fig)

        ax1 = self._fig.add_subplot(121)
        ax2 = self._fig.add_subplot(122)

        hours = list(range(24))

        # Stacked area: base + EV - V2G
        base = np.array(r.base_demand_24h)
        ev = np.array(r.ev_charging_24h)
        v2g = np.array(r.v2g_discharge_24h)
        net = np.array(r.net_load_24h)

        ax1.fill_between(hours, 0, base, alpha=0.4, color="#3498db", label="Base Demand")
        ax1.fill_between(hours, base, base + ev, alpha=0.4, color="#e74c3c", label="EV Charging")
        ax1.fill_between(hours, base + ev, net, alpha=0.4, color="#2ecc71", label="V2G Discharge")
        ax1.plot(hours, net, color="#2c3e50", linewidth=2, label="Net Load")
        ax1.set_xlabel("Hour of Day")
        ax1.set_ylabel("Power (MW)")
        ax1.set_title("Net Load Profile")
        ax1.legend(fontsize=7, loc="upper left")
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(range(0, 24, 4))

        # Load duration curve comparison
        base_sorted = np.sort(base)[::-1]
        with_ev_sorted = np.sort(base + ev)[::-1]
        net_sorted = np.sort(net)[::-1]

        ax2.plot(range(24), base_sorted, label="Base", color="#3498db", linewidth=2)
        ax2.plot(range(24), with_ev_sorted, label="Base + EV", color="#e74c3c", linewidth=2)
        ax2.plot(range(24), net_sorted, label="Net (+ V2G)", color="#2ecc71", linewidth=2)
        ax2.set_xlabel("Hours (sorted)")
        ax2.set_ylabel("Power (MW)")
        ax2.set_title("Load Duration Curve")
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)

        self._fig.tight_layout()
        self._chart_container.addWidget(canvas)

    def _build_flexibility_table(self):
        if self._result is None:
            return

        r = self._result
        metrics = [
            (tr("wizard_ev.peak_shaving_smart"), f"{r.peak_shaving_mw:.1f} MW"),
            (tr("wizard_ev.valley_filling"), f"{r.valley_filling_mw:.1f} MW"),
            (tr("wizard_ev.ptv_before"), f"{r.peak_to_valley_before:.2f}"),
            (tr("wizard_ev.ptv_after"), f"{r.peak_to_valley_after:.2f}"),
            (tr("wizard_ev.re_curtailment_reduction"), f"{r.re_curtailment_reduction_pct:.1f} %"),
            (tr("wizard_ev.freq_regulation"), f"{r.frequency_regulation_mw:.1f} MW"),
        ]

        self._flex_table.setRowCount(len(metrics))
        for row, (metric, value) in enumerate(metrics):
            for col, text in enumerate([metric, value]):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._flex_table.setItem(row, col, item)

    def _build_economic_summary(self):
        if self._result is None:
            return

        r = self._result
        self._econ_label.setText(
            f"<b>Arbitrage revenue (annual):</b> ${r.arbitrage_revenue_annual:,.0f}<br>"
            f"<b>Avoided grid reinforcement:</b> ${r.avoided_reinforcement:,.0f}<br>"
            f"<b>Net V2G program value:</b> ${r.net_v2g_value:,.0f}"
        )

    def _export_csv(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Grid Impact", "grid_impact.csv", "CSV (*.csv)",
        )
        if not path:
            return

        r = self._result
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "hour", "base_mw", "ev_charging_mw",
                "v2g_discharge_mw", "net_load_mw",
            ])
            for h in range(24):
                writer.writerow([
                    h,
                    f"{r.base_demand_24h[h]:.2f}",
                    f"{r.ev_charging_24h[h]:.2f}",
                    f"{r.v2g_discharge_24h[h]:.2f}",
                    f"{r.net_load_24h[h]:.2f}",
                ])

    def get_result(self) -> Optional[GridImpactResult]:
        return self._result

    def is_valid(self) -> bool:
        if self._result is None:
            QMessageBox.warning(
                self, tr("wizard_ev.title"), tr("wizard_ev.no_grid_analysis")
            )
            return False
        return True


# =====================================================================
# Step 9: ESFEX Integration
# =====================================================================


class EVIntegrationStep(QWidget):
    """Apply results to ESFEX model or export configuration."""

    def __init__(self, model=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._curve: Optional[EVAdoptionCurve] = None
        self._transport: Optional[TransportContext] = None
        self._macro: Optional[EVMacroData] = None
        self._v2g: Optional[V2GPotential] = None
        self._degradation: Optional[DegradationResult] = None
        self._grid_impact: Optional[GridImpactResult] = None
        self._scenarios: dict[str, ChargingScenarioResult] = {}
        self._ev_config: dict = {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel(tr("wizard_ev.integration_instruction")))

        # -- Config preview --
        preview_grp = QGroupBox(tr("wizard_ev.config_preview"))
        pg = QVBoxLayout(preview_grp)
        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMinimumHeight(300)
        self._preview.setStyleSheet("font-family: monospace; font-size: 11px;")
        pg.addWidget(self._preview)
        layout.addWidget(preview_grp)

        # -- Actions --
        actions_grp = QGroupBox(tr("wizard_ev.actions_title"))
        ag = QVBoxLayout(actions_grp)

        row1 = QHBoxLayout()
        self._btn_apply = QPushButton(tr("wizard_ev.apply_to_model"))
        self._btn_apply.clicked.connect(self._apply_to_model)
        if model is None:
            self._btn_apply.setEnabled(False)
            self._btn_apply.setToolTip(tr("wizard_ev.no_model_tooltip"))
        row1.addWidget(self._btn_apply)

        self._btn_yaml = QPushButton(tr("wizard_ev.export_yaml"))
        self._btn_yaml.clicked.connect(self._export_yaml)
        row1.addWidget(self._btn_yaml)
        ag.addLayout(row1)

        row2 = QHBoxLayout()
        self._btn_export_all = QPushButton(tr("wizard_ev.export_all"))
        self._btn_export_all.clicked.connect(self._export_all)
        row2.addWidget(self._btn_export_all)
        row2.addStretch()
        ag.addLayout(row2)

        layout.addWidget(actions_grp)

        # -- Status --
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    def set_inputs(
        self,
        curve: EVAdoptionCurve,
        transport: TransportContext,
        macro: EVMacroData,
        v2g: Optional[V2GPotential],
        degradation: Optional[DegradationResult],
        grid_impact: Optional[GridImpactResult],
        scenarios: dict[str, ChargingScenarioResult],
    ):
        self._curve = curve
        self._transport = transport
        self._macro = macro
        self._v2g = v2g
        self._degradation = degradation
        self._grid_impact = grid_impact
        self._scenarios = scenarios
        self._generate_config()

    def _generate_config(self):
        if self._curve is None or self._transport is None:
            return

        # Determine number of nodes from model or default to 1
        num_nodes = 1
        if self._model is not None:
            state = getattr(self._model, "state", None)
            if state is not None:
                nodes = getattr(state, "nodes", [])
                num_nodes = max(len(nodes), 1)

        # Get charging profiles from optimized scenario
        charging_profiles = None
        opt = self._scenarios.get("optimized")
        if opt:
            charging_profiles = {}
            for cat, profile in opt.profiles_by_category.items():
                # Normalize to [0,1] pattern
                total = sum(profile.hourly_mw) if profile.hourly_mw else 1
                if total > 0:
                    charging_profiles[cat] = [h / total for h in profile.hourly_mw]

        # V2G parameter overrides
        v2g_params = None
        if self._v2g is not None:
            v2g_params = {}
            for cat in DEFAULT_CATEGORIES:
                params = _DEFAULT_EV_PARAMS.get(cat, {})
                v2g_params[cat] = {
                    "v2g_power": params.get("v2g_power", 5.0),
                    "v2g_participation": params.get("v2g_participation", 0.3),
                }

        self._ev_config = fit_adoption_to_ev_config(
            curve=self._curve,
            transport=self._transport,
            num_nodes=num_nodes,
            charging_profiles=charging_profiles,
            v2g_params=v2g_params,
        )

        self._update_preview()

    def _update_preview(self):
        if not self._ev_config:
            self._preview.setPlainText("No configuration generated.")
            return

        try:
            import yaml
            text = yaml.dump(
                {"ev_config": self._ev_config},
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        except ImportError:
            text = json.dumps(self._ev_config, indent=2, default=str)

        # Add summary header
        header = "# EV & V2G Configuration for ESFEX\n"
        header += f"# Method: {self._ev_config.get('method', 'unknown')}\n"
        header += f"# Period: {self._ev_config.get('base_year', '?')}"
        header += f" - {self._ev_config.get('target_year', '?')}\n"
        if self._degradation:
            header += f"# Degradation cost: ${self._degradation.degradation_cost_per_kwh:.4f}/kWh\n"
            header += f"# Break-even V2G: ${self._degradation.breakeven_compensation:.1f}/MWh\n"
        header += "#\n"

        self._preview.setPlainText(header + text)

    def _apply_to_model(self):
        if self._model is None or not self._ev_config:
            return

        try:
            from esfex.visualization.data.gui_model import (
                GuiEVCategory,
                GuiEVConfig,
            )

            state = self._model.state
            categories = {}
            for cat_name, cat_cfg in self._ev_config.get("categories", {}).items():
                categories[cat_name] = GuiEVCategory(
                    category_id=cat_name,
                    battery_capacity=cat_cfg.get("battery_capacity", 50.0),
                    charging_power=cat_cfg.get("charging_power", 7.0),
                    v2g_power=cat_cfg.get("v2g_power", 5.0),
                    v2g_participation=cat_cfg.get("v2g_participation", 0.3),
                    efficiency_charge=cat_cfg.get("efficiency_charge", 0.9),
                    efficiency_discharge=cat_cfg.get("efficiency_discharge", 0.9),
                    min_soc=cat_cfg.get("min_soc", 0.2),
                    max_adoption=cat_cfg.get("max_adoption", 35.0),
                    growth_rate=cat_cfg.get("growth_rate", 0.14),
                    mid_point_fraction=cat_cfg.get("mid_point_fraction", 0.5),
                    quantity=cat_cfg.get("quantity", []),
                    base_pattern=cat_cfg.get("base_pattern", []),
                )

            state.ev_config = GuiEVConfig(
                initial_soc=self._ev_config.get("initial_soc", []),
                categories=categories,
            )
            self._status.setText(tr("wizard_ev.applied_success"))
            logger.info("EV config applied to model: %d categories", len(categories))

        except Exception as exc:
            logger.exception("Failed to apply EV config")
            QMessageBox.critical(
                self, tr("wizard_ev.title"),
                f"{tr('wizard_ev.apply_error')}: {exc}",
            )

    def _export_yaml(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export YAML", "ev_config.yaml", "YAML (*.yaml *.yml)",
        )
        if not path:
            return

        try:
            try:
                import yaml
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(
                        {"ev_config": self._ev_config},
                        f, default_flow_style=False,
                        sort_keys=False, allow_unicode=True,
                    )
            except ImportError:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"ev_config": self._ev_config}, f, indent=2, default=str)

            self._status.setText(f"Exported to {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Error", str(exc))

    def _export_all(self):
        directory = QFileDialog.getExistingDirectory(
            self, tr("wizard_ev.export_all_dir"),
        )
        if not directory:
            return

        from pathlib import Path
        base = Path(directory)

        try:
            # 1. Adoption curves CSV
            if self._curve:
                with open(base / "ev_adoption_curve.csv", "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["year", "penetration", "total_ev", "energy_gwh", "peak_mw"])
                    for i, yr in enumerate(self._curve.years):
                        writer.writerow([
                            yr,
                            f"{self._curve.penetration[i]:.4f}",
                            self._curve.total_fleet_ev[i] if i < len(self._curve.total_fleet_ev) else "",
                            self._curve.energy_demand_gwh[i] if i < len(self._curve.energy_demand_gwh) else "",
                            self._curve.peak_charging_mw[i] if i < len(self._curve.peak_charging_mw) else "",
                        ])

            # 2. Charging profiles CSV
            for name, scenario in self._scenarios.items():
                with open(base / f"charging_{name}.csv", "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["hour", "aggregate_mw"] + list(scenario.profiles_by_category.keys()))
                    for h in range(24):
                        row = [h, f"{scenario.aggregate_hourly_mw[h]:.2f}"]
                        for cat_profile in scenario.profiles_by_category.values():
                            row.append(f"{cat_profile.hourly_mw[h]:.2f}" if h < len(cat_profile.hourly_mw) else "")
                        writer.writerow(row)

            # 3. V2G analysis JSON
            if self._v2g:
                with open(base / "v2g_analysis.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "max_v2g_power_mw": self._v2g.max_v2g_power_mw,
                        "daily_v2g_energy_mwh": self._v2g.daily_v2g_energy_mwh,
                        "annual_v2g_potential_gwh": self._v2g.annual_v2g_potential_gwh,
                        "hourly_connected_fraction": self._v2g.hourly_connected_fraction,
                    }, f, indent=2)

            # 4. Grid impact JSON
            if self._grid_impact:
                r = self._grid_impact
                with open(base / "grid_impact.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "base_demand_24h": r.base_demand_24h,
                        "ev_charging_24h": r.ev_charging_24h,
                        "v2g_discharge_24h": r.v2g_discharge_24h,
                        "net_load_24h": r.net_load_24h,
                        "peak_shaving_mw": r.peak_shaving_mw,
                        "valley_filling_mw": r.valley_filling_mw,
                        "arbitrage_revenue_annual": r.arbitrage_revenue_annual,
                        "net_v2g_value": r.net_v2g_value,
                    }, f, indent=2)

            # 5. EV config YAML/JSON
            try:
                import yaml
                with open(base / "ev_config.yaml", "w", encoding="utf-8") as f:
                    yaml.dump({"ev_config": self._ev_config}, f, default_flow_style=False, sort_keys=False)
            except ImportError:
                with open(base / "ev_config.json", "w", encoding="utf-8") as f:
                    json.dump({"ev_config": self._ev_config}, f, indent=2, default=str)

            # 6. Degradation summary
            if self._degradation:
                d = self._degradation
                with open(base / "degradation_summary.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "chemistry": d.chemistry,
                        "cycles_per_day": d.cycles_per_day,
                        "depth_of_discharge": d.depth_of_discharge,
                        "total_degradation_pct_per_year": d.total_degradation_pct_per_year,
                        "degradation_cost_per_kwh": d.degradation_cost_per_kwh,
                        "breakeven_compensation_per_mwh": d.breakeven_compensation,
                    }, f, indent=2)

            self._status.setText(f"All results exported to {directory}")

        except Exception as exc:
            logger.exception("Export error")
            QMessageBox.warning(self, "Export Error", str(exc))

    def is_valid(self) -> bool:
        return bool(self._ev_config)
