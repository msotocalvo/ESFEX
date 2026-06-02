"""Phase A step widgets for the EV & V2G Assessment wizard.

Steps 1-5: Transport Context, Macro & Policy Data, Adoption Modeling,
Fleet Results, Scenario Selection.
"""

from __future__ import annotations

import csv
import json
import logging
from typing import Optional

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
    QRadioButton,
    QScrollArea,
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
    DEFAULT_ENERGY_CONSUMPTION,
    EVAdoptionCurve,
    EVMacroData,
    EVValidationData,
    TransportContext,
    run_ev_bass_diffusion,
    run_ev_logistic_adoption,
    run_ev_policy_driven,
    run_ev_tco_parity,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Step 1: Transport Context (Domain + Fleet Data)
# =====================================================================


class EVDomainStep(QWidget):
    """Define the study area and enter baseline vehicle fleet data."""

    domainChanged = Signal()

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._bounds: Optional[tuple[float, float, float, float]] = None
        self._fetchers: list = []

        layout = QVBoxLayout(self)

        # Description
        layout.addWidget(QLabel(tr("wizard_ev.domain_instruction")))

        # -- Domain group --
        domain_grp = QGroupBox(tr("wizard_ev.domain_title"))
        dg_layout = QHBoxLayout(domain_grp)

        self._btn_draw = QPushButton(tr("wizard_common.draw_on_map"))
        self._btn_draw.clicked.connect(self._start_drawing)
        dg_layout.addWidget(self._btn_draw)

        form = QFormLayout()
        self._spin_south = QDoubleSpinBox(); self._spin_south.setRange(-90, 90); self._spin_south.setDecimals(4)
        self._spin_north = QDoubleSpinBox(); self._spin_north.setRange(-90, 90); self._spin_north.setDecimals(4)
        self._spin_west = QDoubleSpinBox(); self._spin_west.setRange(-180, 180); self._spin_west.setDecimals(4)
        self._spin_east = QDoubleSpinBox(); self._spin_east.setRange(-180, 180); self._spin_east.setDecimals(4)
        form.addRow(tr("wizard_common.south_lat"), self._spin_south)
        form.addRow(tr("wizard_common.north_lat"), self._spin_north)
        form.addRow(tr("wizard_common.west_lng"), self._spin_west)
        form.addRow(tr("wizard_common.east_lng"), self._spin_east)
        dg_layout.addLayout(form)

        self._btn_apply = QPushButton(tr("wizard_common.apply_coords"))
        self._btn_apply.clicked.connect(self._apply_manual)
        dg_layout.addWidget(self._btn_apply)
        layout.addWidget(domain_grp)

        # -- Auto-detect group --
        detect_grp = QGroupBox(tr("wizard_ev.autodetect_title"))
        det_layout = QVBoxLayout(detect_grp)

        self._btn_fetch = QPushButton(tr("wizard_ev.osm_fetch"))
        self._btn_fetch.clicked.connect(self._fetch_osm_data)
        det_layout.addWidget(self._btn_fetch)

        self._detect_progress = QProgressBar()
        self._detect_progress.setRange(0, 100)
        self._detect_progress.setValue(0)
        det_layout.addWidget(self._detect_progress)

        self._detect_status = QLabel("")
        det_layout.addWidget(self._detect_status)
        layout.addWidget(detect_grp)

        # -- Fleet table --
        fleet_grp = QGroupBox(tr("wizard_ev.fleet_table_title"))
        fl_layout = QVBoxLayout(fleet_grp)

        self._fleet_table = QTableWidget(4, 3)
        self._fleet_table.setHorizontalHeaderLabels([
            tr("wizard_ev.col_fleet_count"),
            tr("wizard_ev.col_daily_km"),
            tr("wizard_ev.col_consumption"),
        ])
        self._fleet_table.setVerticalHeaderLabels([
            tr("wizard_ev.cat_light"),
            tr("wizard_ev.cat_medium"),
            tr("wizard_ev.cat_heavy"),
            tr("wizard_ev.cat_buses"),
        ])
        self._fleet_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        # Defaults
        defaults = [
            (1000, 40.0, 18.0),
            (200, 80.0, 25.0),
            (50, 150.0, 55.0),
            (30, 200.0, 80.0),
        ]
        for row, (count, km, cons) in enumerate(defaults):
            self._fleet_table.setItem(row, 0, QTableWidgetItem(str(count)))
            self._fleet_table.setItem(row, 1, QTableWidgetItem(str(km)))
            self._fleet_table.setItem(row, 2, QTableWidgetItem(str(cons)))

        fl_layout.addWidget(self._fleet_table)
        layout.addWidget(fleet_grp)

        # -- OSM results --
        self._osm_results: dict = {}

        # Connect map rectangle signal
        if hasattr(map_widget, 'bridge'):
            bridge = map_widget.bridge
            if hasattr(bridge, 'rectangleDrawn'):
                bridge.rectangleDrawn.connect(self._on_rectangle_drawn)
            map_widget.install_draw_cancel_handler(self, self._btn_draw)

    def _start_drawing(self):
        self._btn_draw.setEnabled(False)
        wizard = self.window()
        if wizard:
            wizard.showMinimized()
        self._map_widget.enable_rectangle_draw()

    def _on_rectangle_drawn(self, bounds_json: str):
        data = json.loads(bounds_json)
        self._bounds = (
            float(data["south"]), float(data["west"]),
            float(data["north"]), float(data["east"]),
        )
        self._spin_south.setValue(self._bounds[0])
        self._spin_west.setValue(self._bounds[1])
        self._spin_north.setValue(self._bounds[2])
        self._spin_east.setValue(self._bounds[3])
        self._btn_draw.setEnabled(True)
        self._map_widget.disable_rectangle_draw()
        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()
        self.domainChanged.emit()

    def _apply_manual(self):
        self._bounds = (
            self._spin_south.value(), self._spin_west.value(),
            self._spin_north.value(), self._spin_east.value(),
        )
        self.domainChanged.emit()

    def _fetch_osm_data(self):
        if self._bounds is None:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_domain"))
            return

        from esfex.visualization.workflows.ev_fetchers import (
            OSMChargingStationFetcher,
            OSMRoadNetworkFetcher,
        )

        self._btn_fetch.setEnabled(False)
        self._detect_progress.setValue(0)
        self._pending_fetches = 2

        self._cs_fetcher = OSMChargingStationFetcher(self._bounds, parent=self)
        self._cs_fetcher.progress.connect(
            lambda p, m: self._detect_progress.setValue(p // 2)
        )
        self._cs_fetcher.finished.connect(self._on_cs_finished)
        self._cs_fetcher.error.connect(self._on_fetch_error)
        self._cs_fetcher.start()

        self._road_fetcher = OSMRoadNetworkFetcher(self._bounds, parent=self)
        self._road_fetcher.progress.connect(
            lambda p, m: self._detect_progress.setValue(50 + p // 2)
        )
        self._road_fetcher.finished.connect(self._on_road_finished)
        self._road_fetcher.error.connect(self._on_fetch_error)
        self._road_fetcher.start()

    def _on_cs_finished(self, data: dict):
        self._osm_results.update(data)
        self._pending_fetches -= 1
        cs = data.get("charging_stations", 0)
        pk = data.get("parking_areas", 0)
        self._detect_status.setText(
            f"{cs} charging stations, {pk} parking areas"
        )
        if self._pending_fetches <= 0:
            self._btn_fetch.setEnabled(True)
            self._detect_progress.setValue(100)

    def _on_road_finished(self, data: dict):
        self._osm_results.update(data)
        self._pending_fetches -= 1
        rd = data.get("road_density_km2", 0)
        txt = self._detect_status.text()
        self._detect_status.setText(f"{txt}, road density: {rd:.1f} km/km²")
        if self._pending_fetches <= 0:
            self._btn_fetch.setEnabled(True)
            self._detect_progress.setValue(100)

    def _on_fetch_error(self, msg: str):
        self._pending_fetches -= 1
        self._detect_status.setText(f"Error: {msg}")
        if self._pending_fetches <= 0:
            self._btn_fetch.setEnabled(True)

    def get_bounds(self) -> Optional[tuple]:
        return self._bounds

    def get_transport_context(self) -> TransportContext:
        cats = list(DEFAULT_CATEGORIES)
        fleet = {}
        km = {}
        cons = {}
        for i, cat in enumerate(cats):
            try:
                fleet[cat] = int(self._fleet_table.item(i, 0).text())
            except (ValueError, AttributeError):
                fleet[cat] = 0
            try:
                km[cat] = float(self._fleet_table.item(i, 1).text())
            except (ValueError, AttributeError):
                km[cat] = 40.0
            try:
                cons[cat] = float(self._fleet_table.item(i, 2).text())
            except (ValueError, AttributeError):
                cons[cat] = DEFAULT_ENERGY_CONSUMPTION.get(cat, 18.0)

        return TransportContext(
            fleet_by_category=fleet,
            avg_daily_km=km,
            energy_consumption=cons,
            charging_stations=self._osm_results.get("charging_stations", 0),
            road_density_km2=self._osm_results.get("road_density_km2", 0),
        )

    def is_valid(self) -> bool:
        if self._bounds is None:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_domain"))
            return False
        return True

    def cancel_all(self):
        for attr in ("_cs_fetcher", "_road_fetcher"):
            fetcher = getattr(self, attr, None)
            if fetcher and fetcher.isRunning():
                fetcher.cancel()


# =====================================================================
# Step 2: Macroeconomic & Policy Data
# =====================================================================


class EVMacroDataStep(QWidget):
    """Configure macroeconomic, cost, and policy inputs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fetchers: list = []
        self._country_iso = ""

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        # -- Macroeconomic --
        macro_grp = QGroupBox(tr("wizard_ev.macro_title"))
        mf = QFormLayout(macro_grp)

        self._spin_gdp = QDoubleSpinBox(); self._spin_gdp.setRange(100, 200000); self._spin_gdp.setValue(5000); self._spin_gdp.setPrefix("$ ")
        self._spin_urban = QDoubleSpinBox(); self._spin_urban.setRange(0, 100); self._spin_urban.setValue(75); self._spin_urban.setSuffix(" %")
        self._spin_pop = QSpinBox(); self._spin_pop.setRange(1000, 2_000_000_000); self._spin_pop.setValue(1_000_000)
        self._spin_inflation = QDoubleSpinBox(); self._spin_inflation.setRange(-5, 50); self._spin_inflation.setValue(3); self._spin_inflation.setSuffix(" %")
        self._spin_gdp_growth = QDoubleSpinBox(); self._spin_gdp_growth.setRange(-10, 20); self._spin_gdp_growth.setValue(3); self._spin_gdp_growth.setSuffix(" %")

        mf.addRow(tr("wizard_ev.gdp_per_capita"), self._spin_gdp)
        mf.addRow(tr("wizard_ev.urbanization"), self._spin_urban)
        mf.addRow(tr("wizard_ev.population"), self._spin_pop)
        mf.addRow(tr("wizard_ev.inflation"), self._spin_inflation)
        mf.addRow(tr("wizard_ev.gdp_growth"), self._spin_gdp_growth)

        self._btn_fetch_macro = QPushButton(tr("wizard_ev.fetch_macro"))
        self._btn_fetch_macro.clicked.connect(self._fetch_macro)
        mf.addRow(self._btn_fetch_macro)

        self._macro_progress = QProgressBar()
        self._macro_progress.setRange(0, 100)
        mf.addRow(self._macro_progress)
        layout.addWidget(macro_grp)

        # -- EV Economics --
        econ_grp = QGroupBox(tr("wizard_ev.econ_title"))
        ef = QFormLayout(econ_grp)

        # Vehicle price table
        self._price_table = QTableWidget(4, 2)
        self._price_table.setHorizontalHeaderLabels([
            tr("wizard_ev.col_ev_price"), tr("wizard_ev.col_ice_price"),
        ])
        self._price_table.setVerticalHeaderLabels([
            tr("wizard_ev.cat_light"), tr("wizard_ev.cat_medium"),
            tr("wizard_ev.cat_heavy"), tr("wizard_ev.cat_buses"),
        ])
        self._price_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        price_defaults = [
            (35000, 25000), (55000, 40000), (120000, 90000), (300000, 250000),
        ]
        for row, (ev_p, ice_p) in enumerate(price_defaults):
            self._price_table.setItem(row, 0, QTableWidgetItem(str(ev_p)))
            self._price_table.setItem(row, 1, QTableWidgetItem(str(ice_p)))
        ef.addRow(tr("wizard_ev.vehicle_prices"), self._price_table)

        self._spin_bat_cost = QDoubleSpinBox(); self._spin_bat_cost.setRange(30, 1000); self._spin_bat_cost.setValue(140); self._spin_bat_cost.setPrefix("$ ")
        self._spin_bat_decline = QDoubleSpinBox(); self._spin_bat_decline.setRange(0, 30); self._spin_bat_decline.setValue(8); self._spin_bat_decline.setSuffix(" %")
        self._spin_gasoline = QDoubleSpinBox(); self._spin_gasoline.setRange(0.1, 10); self._spin_gasoline.setValue(1.20); self._spin_gasoline.setPrefix("$ "); self._spin_gasoline.setDecimals(2)
        self._spin_diesel = QDoubleSpinBox(); self._spin_diesel.setRange(0.1, 10); self._spin_diesel.setValue(1.10); self._spin_diesel.setPrefix("$ "); self._spin_diesel.setDecimals(2)
        self._spin_tariff = QDoubleSpinBox(); self._spin_tariff.setRange(0.001, 2.0); self._spin_tariff.setValue(0.15); self._spin_tariff.setPrefix("$ "); self._spin_tariff.setDecimals(3)
        self._spin_maint_diff = QDoubleSpinBox(); self._spin_maint_diff.setRange(0, 5000); self._spin_maint_diff.setValue(500); self._spin_maint_diff.setPrefix("$ ")

        ef.addRow(tr("wizard_ev.battery_cost"), self._spin_bat_cost)
        ef.addRow(tr("wizard_ev.battery_decline"), self._spin_bat_decline)
        ef.addRow(tr("wizard_ev.gasoline_price"), self._spin_gasoline)
        ef.addRow(tr("wizard_ev.diesel_price"), self._spin_diesel)
        ef.addRow(tr("wizard_ev.electricity_tariff"), self._spin_tariff)
        ef.addRow(tr("wizard_ev.maintenance_diff"), self._spin_maint_diff)

        self._btn_fetch_battery = QPushButton(tr("wizard_ev.fetch_battery"))
        self._btn_fetch_battery.clicked.connect(self._fetch_battery_costs)
        ef.addRow(self._btn_fetch_battery)
        layout.addWidget(econ_grp)

        # -- Policy --
        policy_grp = QGroupBox(tr("wizard_ev.policy_title"))
        pf = QFormLayout(policy_grp)

        self._spin_ban_year = QSpinBox(); self._spin_ban_year.setRange(0, 2070); self._spin_ban_year.setValue(0); self._spin_ban_year.setSpecialValueText(tr("wizard_ev.no_ban"))
        self._spin_subsidy = QDoubleSpinBox(); self._spin_subsidy.setRange(0, 100); self._spin_subsidy.setValue(0); self._spin_subsidy.setSuffix(" %")
        self._spin_reg_tax = QDoubleSpinBox(); self._spin_reg_tax.setRange(0, 50000); self._spin_reg_tax.setValue(0); self._spin_reg_tax.setPrefix("$ ")
        self._spin_emission = QDoubleSpinBox(); self._spin_emission.setRange(0, 100); self._spin_emission.setValue(0); self._spin_emission.setSuffix(" %")

        pf.addRow(tr("wizard_ev.ice_ban_year"), self._spin_ban_year)
        pf.addRow(tr("wizard_ev.ev_subsidy"), self._spin_subsidy)
        pf.addRow(tr("wizard_ev.reg_tax_diff"), self._spin_reg_tax)
        pf.addRow(tr("wizard_ev.emission_target"), self._spin_emission)
        layout.addWidget(policy_grp)

        layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    def set_bounds(self, bounds: tuple | None):
        """Receive bounds from Step 1 for country detection."""
        if bounds is None:
            return

        from esfex.visualization.workflows.solar_macro_fetchers import CountryDetector

        self._detector = CountryDetector(bounds, parent=self)
        self._detector.finished.connect(self._on_country_detected)
        self._detector.error.connect(lambda m: logger.warning("Country detection: %s", m))
        self._detector.start()

    def _on_country_detected(self, iso3: str, name: str):
        self._country_iso = iso3
        logger.info("Detected country: %s (%s)", name, iso3)

    def _fetch_macro(self):
        if not self._country_iso:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_country"))
            return

        from esfex.visualization.workflows.ev_fetchers import (
            IMFEVFetcher,
            WorldBankEVFetcher,
        )

        self._btn_fetch_macro.setEnabled(False)
        self._macro_progress.setValue(0)
        self._pending_macro = 2

        self._wb_fetcher = WorldBankEVFetcher(self._country_iso, parent=self)
        self._wb_fetcher.progress.connect(lambda p, _: self._macro_progress.setValue(p // 2))
        self._wb_fetcher.finished.connect(self._on_wb_finished)
        self._wb_fetcher.error.connect(self._on_macro_error)
        self._wb_fetcher.start()

        self._imf_fetcher = IMFEVFetcher(self._country_iso, parent=self)
        self._imf_fetcher.progress.connect(lambda p, _: self._macro_progress.setValue(50 + p // 2))
        self._imf_fetcher.finished.connect(self._on_imf_finished)
        self._imf_fetcher.error.connect(self._on_macro_error)
        self._imf_fetcher.start()

    def _on_wb_finished(self, data: dict):
        if data.get("gdp_per_capita") is not None:
            self._spin_gdp.setValue(data["gdp_per_capita"])
        if data.get("urbanization_pct") is not None:
            self._spin_urban.setValue(data["urbanization_pct"])
        if data.get("population") is not None:
            self._spin_pop.setValue(int(data["population"]))
        self._pending_macro -= 1
        if self._pending_macro <= 0:
            self._btn_fetch_macro.setEnabled(True)
            self._macro_progress.setValue(100)

    def _on_imf_finished(self, data: dict):
        if data.get("gdp_growth_rate") is not None:
            self._spin_gdp_growth.setValue(data["gdp_growth_rate"] * 100)
        if data.get("inflation_rate") is not None:
            self._spin_inflation.setValue(data["inflation_rate"] * 100)
        self._pending_macro -= 1
        if self._pending_macro <= 0:
            self._btn_fetch_macro.setEnabled(True)
            self._macro_progress.setValue(100)

    def _on_macro_error(self, msg: str):
        self._pending_macro -= 1
        logger.warning("Macro fetch error: %s", msg)
        if self._pending_macro <= 0:
            self._btn_fetch_macro.setEnabled(True)

    def _fetch_battery_costs(self):
        from esfex.visualization.workflows.ev_fetchers import EVBatteryCostFetcher

        self._bat_fetcher = EVBatteryCostFetcher(parent=self)
        self._bat_fetcher.finished.connect(self._on_battery_finished)
        self._bat_fetcher.error.connect(lambda m: logger.warning("Battery cost fetch: %s", m))
        self._bat_fetcher.start()

    def _on_battery_finished(self, data: dict):
        if data.get("battery_cost_per_kwh") is not None:
            self._spin_bat_cost.setValue(data["battery_cost_per_kwh"])
        if data.get("annual_decline_rate") is not None:
            self._spin_bat_decline.setValue(data["annual_decline_rate"] * 100)

    def get_ev_macro_data(self) -> EVMacroData:
        cats = list(DEFAULT_CATEGORIES)
        ev_price = {}
        ice_price = {}
        for i, cat in enumerate(cats):
            try:
                ev_price[cat] = float(self._price_table.item(i, 0).text())
            except (ValueError, AttributeError):
                ev_price[cat] = 35000
            try:
                ice_price[cat] = float(self._price_table.item(i, 1).text())
            except (ValueError, AttributeError):
                ice_price[cat] = 25000

        return EVMacroData(
            country_iso=self._country_iso,
            gdp_per_capita=self._spin_gdp.value(),
            urbanization_pct=self._spin_urban.value(),
            population=self._spin_pop.value(),
            inflation_rate=self._spin_inflation.value() / 100,
            gdp_growth_rate=self._spin_gdp_growth.value() / 100,
            ev_price=ev_price,
            ice_price=ice_price,
            battery_cost_per_kwh=self._spin_bat_cost.value(),
            battery_cost_decline_rate=self._spin_bat_decline.value() / 100,
            fuel_price_gasoline=self._spin_gasoline.value(),
            fuel_price_diesel=self._spin_diesel.value(),
            electricity_tariff=self._spin_tariff.value(),
            maintenance_diff_annual=self._spin_maint_diff.value(),
            ice_phaseout_year=self._spin_ban_year.value(),
            ev_subsidy_pct=self._spin_subsidy.value() / 100,
            registration_tax_diff=self._spin_reg_tax.value(),
            emission_target_pct=self._spin_emission.value(),
        )

    def is_valid(self) -> bool:
        return self._spin_gdp.value() > 0

    def cancel_all(self):
        for attr in ("_wb_fetcher", "_imf_fetcher", "_bat_fetcher", "_detector"):
            f = getattr(self, attr, None)
            if f and hasattr(f, "cancel") and f.isRunning():
                f.cancel()


# =====================================================================
# Step 3: Adoption Modeling
# =====================================================================


class _EVAdoptionWorker(QThread):
    """Background thread to run EV adoption models."""

    progress = Signal(int, str)
    finished = Signal(list)  # list[EVAdoptionCurve]
    error = Signal(str)

    def __init__(
        self, methods: list[str], macro: EVMacroData,
        transport: TransportContext, base_year: int, target_year: int,
        params: dict, parent=None,
    ):
        super().__init__(parent)
        self._methods = methods
        self._macro = macro
        self._transport = transport
        self._base_year = base_year
        self._target_year = target_year
        self._params = params

    def run(self):
        try:
            results = []
            total = len(self._methods)
            for i, method in enumerate(self._methods):
                pct = int((i / total) * 100)
                self.progress.emit(pct, f"Running {method}...")

                if method == "logistic":
                    curve = run_ev_logistic_adoption(
                        self._macro, self._transport,
                        self._base_year, self._target_year,
                        coefficients=self._params.get("logistic"),
                    )
                elif method == "bass":
                    p_bass = self._params.get("bass", {})
                    curve = run_ev_bass_diffusion(
                        self._transport, self._base_year, self._target_year,
                        p=p_bass.get("p", 0.02),
                        q=p_bass.get("q", 0.40),
                        initial_penetration=p_bass.get("initial_penetration", 0.005),
                    )
                elif method == "tco_parity":
                    p_tco = self._params.get("tco_parity", {})
                    curve = run_ev_tco_parity(
                        self._macro, self._transport,
                        self._base_year, self._target_year,
                        price_sensitivity=p_tco.get("price_sensitivity", 8.0),
                    )
                elif method == "policy_driven":
                    curve = run_ev_policy_driven(
                        self._macro, self._transport,
                        self._base_year, self._target_year,
                    )
                else:
                    continue

                results.append(curve)

            self.progress.emit(100, "All methods complete.")
            self.finished.emit(results)

        except Exception as exc:
            logger.exception("EVAdoptionWorker error")
            self.error.emit(str(exc))


class EVAdoptionModelStep(QWidget):
    """Configure and run 4 EV adoption methods."""

    modelsFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._macro: Optional[EVMacroData] = None
        self._transport: Optional[TransportContext] = None
        self._curves: list[EVAdoptionCurve] = []
        self._validation_data: list[EVValidationData] = []
        self._worker: Optional[_EVAdoptionWorker] = None

        layout = QVBoxLayout(self)

        # Method selection
        methods_grp = QGroupBox(tr("wizard_ev.methods_title"))
        mg = QVBoxLayout(methods_grp)
        self._chk_logistic = QCheckBox(tr("wizard_ev.method_logistic")); self._chk_logistic.setChecked(True)
        self._chk_bass = QCheckBox(tr("wizard_ev.method_bass")); self._chk_bass.setChecked(True)
        self._chk_tco = QCheckBox(tr("wizard_ev.method_tco")); self._chk_tco.setChecked(True)
        self._chk_policy = QCheckBox(tr("wizard_ev.method_policy")); self._chk_policy.setChecked(True)
        mg.addWidget(self._chk_logistic)
        mg.addWidget(self._chk_bass)
        mg.addWidget(self._chk_tco)
        mg.addWidget(self._chk_policy)
        layout.addWidget(methods_grp)

        # Preset + years
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel(tr("wizard_ev.preset")))
        self._combo_preset = QComboBox()
        self._combo_preset.addItems([
            tr("wizard_ev.preset_conservative"),
            tr("wizard_ev.preset_moderate"),
            tr("wizard_ev.preset_aggressive"),
        ])
        self._combo_preset.setCurrentIndex(1)
        config_row.addWidget(self._combo_preset)

        config_row.addWidget(QLabel(tr("wizard_ev.base_year")))
        self._spin_base = QSpinBox(); self._spin_base.setRange(2020, 2040); self._spin_base.setValue(2025)
        config_row.addWidget(self._spin_base)

        config_row.addWidget(QLabel(tr("wizard_ev.target_year")))
        self._spin_target = QSpinBox(); self._spin_target.setRange(2030, 2070); self._spin_target.setValue(2050)
        config_row.addWidget(self._spin_target)

        config_row.addStretch()
        layout.addLayout(config_row)

        # Validation data
        val_grp = QGroupBox(tr("wizard_ev.validation_title"))
        vl = QHBoxLayout(val_grp)

        self._btn_iea = QPushButton(tr("wizard_ev.fetch_iea"))
        self._btn_iea.clicked.connect(self._fetch_iea)
        vl.addWidget(self._btn_iea)

        self._btn_csv = QPushButton(tr("wizard_ev.import_csv"))
        self._btn_csv.clicked.connect(self._import_csv)
        vl.addWidget(self._btn_csv)

        self._lbl_validation = QLabel("")
        vl.addWidget(self._lbl_validation)
        layout.addWidget(val_grp)

        # Run
        run_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_ev.run_models"))
        self._btn_run.clicked.connect(self._run_models)
        run_row.addWidget(self._btn_run)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        run_row.addWidget(self._progress)
        layout.addLayout(run_row)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        layout.addWidget(self._log)

    def set_inputs(self, macro: EVMacroData, transport: TransportContext):
        self._macro = macro
        self._transport = transport

    def _get_selected_methods(self) -> list[str]:
        methods = []
        if self._chk_logistic.isChecked():
            methods.append("logistic")
        if self._chk_bass.isChecked():
            methods.append("bass")
        if self._chk_tco.isChecked():
            methods.append("tco_parity")
        if self._chk_policy.isChecked():
            methods.append("policy_driven")
        return methods

    def _get_preset_params(self) -> dict:
        idx = self._combo_preset.currentIndex()
        presets = [
            {  # Conservative
                "logistic": {"beta_0": -4.0, "beta_fuel_savings": 2.0},
                "bass": {"p": 0.01, "q": 0.30},
                "tco_parity": {"price_sensitivity": 5.0},
            },
            {  # Moderate
                "logistic": {"beta_0": -3.5, "beta_fuel_savings": 3.0},
                "bass": {"p": 0.02, "q": 0.40},
                "tco_parity": {"price_sensitivity": 8.0},
            },
            {  # Aggressive
                "logistic": {"beta_0": -2.5, "beta_fuel_savings": 4.0},
                "bass": {"p": 0.04, "q": 0.50},
                "tco_parity": {"price_sensitivity": 12.0},
            },
        ]
        return presets[idx] if idx < len(presets) else presets[1]

    def _run_models(self):
        methods = self._get_selected_methods()
        if not methods:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_methods"))
            return
        if self._macro is None or self._transport is None:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_inputs"))
            return

        self._btn_run.setEnabled(False)
        self._progress.setValue(0)
        self._log.clear()

        self._worker = _EVAdoptionWorker(
            methods, self._macro, self._transport,
            self._spin_base.value(), self._spin_target.value(),
            self._get_preset_params(), parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._log.append(msg)

    def _on_finished(self, curves: list):
        self._curves = curves
        self._progress.setValue(100)
        self._btn_run.setEnabled(True)

        for c in curves:
            final_pen = c.penetration[-1] * 100 if c.penetration else 0
            self._log.append(f"  {c.method}: {final_pen:.1f}% by {c.years[-1]}")

        self.modelsFinished.emit()

    def _on_error(self, msg: str):
        self._btn_run.setEnabled(True)
        QMessageBox.critical(self, tr("wizard_ev.title"), msg)

    def _fetch_iea(self):
        iso = getattr(self, '_macro', None)
        if iso is None or not hasattr(self._macro, 'country_iso'):
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_country"))
            return

        from esfex.visualization.workflows.ev_fetchers import IEAEVDataFetcher

        fetcher = IEAEVDataFetcher(self._macro.country_iso, parent=self)
        fetcher.finished.connect(self._on_iea_finished)
        fetcher.error.connect(lambda m: self._log.append(f"IEA error: {m}"))
        fetcher.start()

    def _on_iea_finished(self, data: dict):
        if data.get("years"):
            vd = EVValidationData(
                label=data["label"],
                years=data["years"],
                ev_stock=data["ev_stock"],
                source="iea",
            )
            # Replace existing IEA data
            self._validation_data = [
                v for v in self._validation_data if v.source != "iea"
            ]
            self._validation_data.append(vd)
            self._lbl_validation.setText(
                f"IEA: {len(vd.years)} years loaded"
            )
        else:
            self._lbl_validation.setText("No IEA data for this country")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wizard_ev.import_csv"), "", "CSV Files (*.csv)",
        )
        if not path:
            return

        try:
            years = []
            stock = []
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    years.append(int(row["year"]))
                    stock.append(int(float(row.get("ev_stock", row.get("stock", 0)))))

            vd = EVValidationData(
                label=f"CSV: {path.split('/')[-1]}",
                years=years, ev_stock=stock, source="user_csv",
            )
            self._validation_data.append(vd)
            self._lbl_validation.setText(f"CSV: {len(years)} rows loaded")
        except Exception as exc:
            QMessageBox.warning(self, "CSV Error", str(exc))

    def get_curves(self) -> list[EVAdoptionCurve]:
        return self._curves

    def get_validation_data(self) -> list[EVValidationData]:
        return self._validation_data

    def is_valid(self) -> bool:
        if not self._curves:
            QMessageBox.warning(self, tr("wizard_ev.title"), tr("wizard_ev.no_results"))
            return False
        return True


# =====================================================================
# Step 4: Fleet Results
# =====================================================================


class EVFleetResultsStep(QWidget):
    """Display fleet evolution charts and tables."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curves: list[EVAdoptionCurve] = []
        self._validation_data: list[EVValidationData] = []

        layout = QVBoxLayout(self)

        # Chart area
        self._chart_container = QVBoxLayout()
        layout.addLayout(self._chart_container)

        # Table
        self._table = QTableWidget()
        self._table.setMaximumHeight(200)
        layout.addWidget(self._table)

        # Export
        export_row = QHBoxLayout()
        self._btn_png = QPushButton(tr("wizard_ev.export_png"))
        self._btn_png.clicked.connect(self._export_png)
        export_row.addWidget(self._btn_png)

        self._btn_csv = QPushButton(tr("wizard_ev.export_csv"))
        self._btn_csv.clicked.connect(self._export_csv)
        export_row.addWidget(self._btn_csv)
        export_row.addStretch()
        layout.addLayout(export_row)

        self._fig = None

    def set_results(self, curves: list[EVAdoptionCurve], validation: list[EVValidationData]):
        self._curves = curves
        self._validation_data = validation
        self._build_chart()
        self._build_table()

    def _build_chart(self):
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_container.addWidget(QLabel("matplotlib not available"))
            return

        # Clear previous
        while self._chart_container.count():
            item = self._chart_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._fig = Figure(figsize=(8, 5), facecolor="white")
        canvas = FigureCanvasQTAgg(self._fig)

        ax1 = self._fig.add_subplot(121)
        ax2 = self._fig.add_subplot(122)

        colors = {"logistic": "#e67e22", "bass": "#2980b9", "tco_parity": "#27ae60", "policy_driven": "#c0392b"}

        for curve in self._curves:
            c = colors.get(curve.method, "#7f8c8d")
            ax1.plot(curve.years, [p * 100 for p in curve.penetration],
                     label=curve.method.replace("_", " ").title(), color=c, linewidth=2)
            ax2.plot(curve.years, curve.energy_demand_gwh,
                     label=curve.method.replace("_", " ").title(), color=c, linewidth=2)

        # Validation overlay
        for vd in self._validation_data:
            if vd.ev_stock:
                ax1_twin = ax1  # plot stock on same axis as count
                # Convert to approximate penetration (skip if no fleet total)

        ax1.set_xlabel("Year")
        ax1.set_ylabel("EV Fleet Share (%)")
        ax1.set_title("Fleet Electrification")
        ax1.legend(fontsize=8)
        ax1.grid(True, alpha=0.3)

        ax2.set_xlabel("Year")
        ax2.set_ylabel("Energy Demand (GWh)")
        ax2.set_title("EV Energy Demand")
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)

        self._fig.tight_layout()
        self._chart_container.addWidget(canvas)

    def _build_table(self):
        if not self._curves:
            return

        # Show every 5th year
        sample_curve = self._curves[0]
        year_indices = [i for i, y in enumerate(sample_curve.years) if y % 5 == 0 or i == len(sample_curve.years) - 1]
        year_labels = [str(sample_curve.years[i]) for i in year_indices]

        self._table.setRowCount(len(self._curves))
        self._table.setColumnCount(len(year_labels))
        self._table.setHorizontalHeaderLabels(year_labels)
        self._table.setVerticalHeaderLabels([c.method.replace("_", " ").title() for c in self._curves])

        for row, curve in enumerate(self._curves):
            for col, yi in enumerate(year_indices):
                if yi < len(curve.penetration):
                    val = f"{curve.penetration[yi]*100:.1f}%"
                else:
                    val = ""
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _export_png(self):
        if self._fig is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Chart", "ev_fleet_evolution.png", "PNG (*.png)")
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight")

    def _export_csv(self):
        if not self._curves:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Data", "ev_fleet_data.csv", "CSV (*.csv)")
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["year", "method", "penetration", "total_ev", "energy_gwh", "peak_mw"])
            for curve in self._curves:
                for i, yr in enumerate(curve.years):
                    writer.writerow([
                        yr, curve.method,
                        f"{curve.penetration[i]:.4f}",
                        curve.total_fleet_ev[i] if i < len(curve.total_fleet_ev) else "",
                        curve.energy_demand_gwh[i] if i < len(curve.energy_demand_gwh) else "",
                        curve.peak_charging_mw[i] if i < len(curve.peak_charging_mw) else "",
                    ])

    def is_valid(self) -> bool:
        return bool(self._curves)


# =====================================================================
# Step 5: Scenario Selection
# =====================================================================


class EVScenarioSelectionStep(QWidget):
    """Compare and select preferred adoption scenario."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curves: list[EVAdoptionCurve] = []
        self._selected_index: int = 0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_ev.select_scenario")))

        # Radio buttons
        self._radio_container = QVBoxLayout()
        layout.addLayout(self._radio_container)

        # Summary table
        self._summary_table = QTableWidget()
        self._summary_table.setMaximumHeight(150)
        layout.addWidget(self._summary_table)

        layout.addStretch()

    def set_curves(self, curves: list[EVAdoptionCurve], validation: list[EVValidationData]):
        self._curves = curves
        self._build_selection()
        self._build_summary()

    def _build_selection(self):
        # Clear previous radios
        while self._radio_container.count():
            item = self._radio_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._radios: list[QRadioButton] = []
        for i, curve in enumerate(self._curves):
            final_pct = curve.penetration[-1] * 100 if curve.penetration else 0
            final_yr = curve.years[-1] if curve.years else "?"
            label = f"{curve.method.replace('_', ' ').title()} — {final_pct:.0f}% by {final_yr}"
            radio = QRadioButton(label)
            if i == 0:
                radio.setChecked(True)
            radio.toggled.connect(lambda checked, idx=i: self._on_radio(checked, idx))
            self._radios.append(radio)
            self._radio_container.addWidget(radio)

    def _on_radio(self, checked: bool, idx: int):
        if checked:
            self._selected_index = idx

    def _build_summary(self):
        if not self._curves:
            return

        headers = ["Method", "Final %", "Total EVs", "Energy (GWh)", "Peak (MW)"]
        self._summary_table.setRowCount(len(self._curves))
        self._summary_table.setColumnCount(len(headers))
        self._summary_table.setHorizontalHeaderLabels(headers)

        for row, c in enumerate(self._curves):
            vals = [
                c.method.replace("_", " ").title(),
                f"{c.penetration[-1]*100:.1f}%" if c.penetration else "—",
                str(c.total_fleet_ev[-1]) if c.total_fleet_ev else "—",
                f"{c.energy_demand_gwh[-1]:.1f}" if c.energy_demand_gwh else "—",
                f"{c.peak_charging_mw[-1]:.1f}" if c.peak_charging_mw else "—",
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._summary_table.setItem(row, col, item)

        self._summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def get_selected_curve(self) -> Optional[EVAdoptionCurve]:
        if 0 <= self._selected_index < len(self._curves):
            return self._curves[self._selected_index]
        return None

    def get_all_curves(self) -> list[EVAdoptionCurve]:
        return self._curves

    def is_valid(self) -> bool:
        return self.get_selected_curve() is not None
