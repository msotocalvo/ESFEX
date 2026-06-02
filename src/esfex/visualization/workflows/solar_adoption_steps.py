"""Phase B step widgets for the Solar Rooftop Analysis wizard.

Step 6: Macroeconomic Data — fetch/edit macro parameters
Step 7: Adoption Modeling — configure & run 4 methods
Step 8: Scenario Comparison — compare curves, select one
Step 9: Model Integration — apply to RooftopSolarConfig + export
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

logger = logging.getLogger(__name__)


# =====================================================================
# Step 6: Macroeconomic Data
# =====================================================================


class MacroDataStep(QWidget):
    """Fetch and edit macroeconomic data for adoption modeling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bounds: Optional[tuple] = None
        self._fetchers: list[QThread] = []
        self._country_iso = ""
        self._country_name = ""

        layout = QVBoxLayout(self)

        # Instructions
        layout.addWidget(QLabel(tr("wizard_solar.macro_instruction")))

        # Country detection
        country_group = QGroupBox(tr("wizard_solar.macro_country"))
        country_lay = QHBoxLayout(country_group)
        self._lbl_country = QLabel(tr("wizard_solar.macro_not_detected"))
        country_lay.addWidget(self._lbl_country)
        self._edit_iso = QLineEdit()
        self._edit_iso.setPlaceholderText("ISO-3 (e.g. CUB, USA)")
        self._edit_iso.setMaximumWidth(120)
        country_lay.addWidget(self._edit_iso)
        self._btn_detect = QPushButton(tr("wizard_solar.macro_detect"))
        self._btn_detect.clicked.connect(self._detect_country)
        country_lay.addWidget(self._btn_detect)
        country_lay.addStretch()
        layout.addWidget(country_group)

        # Fetch controls
        fetch_row = QHBoxLayout()
        self._btn_fetch = QPushButton(tr("wizard_solar.macro_fetch_all"))
        self._btn_fetch.clicked.connect(self._fetch_all)
        fetch_row.addWidget(self._btn_fetch)
        self._fetch_progress = QProgressBar()
        self._fetch_progress.setRange(0, 100)
        self._fetch_progress.setMaximumWidth(200)
        fetch_row.addWidget(self._fetch_progress)
        self._fetch_status = QLabel("")
        fetch_row.addWidget(self._fetch_status)
        fetch_row.addStretch()
        layout.addLayout(fetch_row)

        # Editable fields (scroll area for many fields)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        fields_widget = QWidget()
        form = QFormLayout(fields_widget)

        self._spin_gdp = QDoubleSpinBox()
        self._spin_gdp.setRange(100, 200000)
        self._spin_gdp.setDecimals(0)
        self._spin_gdp.setValue(5000)
        self._spin_gdp.setSuffix(" USD")
        form.addRow(tr("wizard_solar.macro_gdp"), self._spin_gdp)

        self._spin_tariff = QDoubleSpinBox()
        self._spin_tariff.setRange(0.001, 2.0)
        self._spin_tariff.setDecimals(3)
        self._spin_tariff.setValue(0.15)
        self._spin_tariff.setSuffix(" $/kWh")
        form.addRow(tr("wizard_solar.macro_tariff"), self._spin_tariff)

        self._spin_pv_cost = QDoubleSpinBox()
        self._spin_pv_cost.setRange(100, 10000)
        self._spin_pv_cost.setDecimals(0)
        self._spin_pv_cost.setValue(1200)
        self._spin_pv_cost.setSuffix(" $/kW")
        form.addRow(tr("wizard_solar.macro_pv_cost"), self._spin_pv_cost)

        self._spin_learning = QDoubleSpinBox()
        self._spin_learning.setRange(0.01, 0.50)
        self._spin_learning.setDecimals(2)
        self._spin_learning.setValue(0.20)
        form.addRow(tr("wizard_solar.macro_learning_rate"), self._spin_learning)

        self._spin_urban = QDoubleSpinBox()
        self._spin_urban.setRange(0, 100)
        self._spin_urban.setDecimals(1)
        self._spin_urban.setValue(75.0)
        self._spin_urban.setSuffix(" %")
        form.addRow(tr("wizard_solar.macro_urban"), self._spin_urban)

        self._spin_pop = QSpinBox()
        self._spin_pop.setRange(1000, 2_000_000_000)
        self._spin_pop.setValue(1_000_000)
        form.addRow(tr("wizard_solar.macro_population"), self._spin_pop)

        self._spin_discount = QDoubleSpinBox()
        self._spin_discount.setRange(0.01, 0.50)
        self._spin_discount.setDecimals(3)
        self._spin_discount.setValue(0.08)
        form.addRow(tr("wizard_solar.macro_discount"), self._spin_discount)

        self._spin_inflation = QDoubleSpinBox()
        self._spin_inflation.setRange(-0.05, 0.50)
        self._spin_inflation.setDecimals(3)
        self._spin_inflation.setValue(0.03)
        form.addRow(tr("wizard_solar.macro_inflation"), self._spin_inflation)

        self._spin_gdp_growth = QDoubleSpinBox()
        self._spin_gdp_growth.setRange(-0.10, 0.20)
        self._spin_gdp_growth.setDecimals(3)
        self._spin_gdp_growth.setValue(0.03)
        form.addRow(tr("wizard_solar.macro_gdp_growth"), self._spin_gdp_growth)

        scroll.setWidget(fields_widget)
        layout.addWidget(scroll, 1)

        # PV cost trajectory (from IRENA)
        self._pv_cost_trajectory: dict[int, float] = {}

    def set_bounds(self, bounds: tuple):
        """Called when entering this step — store domain bounds."""
        self._bounds = bounds

    def _detect_country(self):
        if self._bounds is None:
            QMessageBox.warning(
                self, tr("wizard_solar.macro_warn_title"),
                tr("wizard_solar.macro_no_bounds"),
            )
            return

        from esfex.visualization.workflows.solar_macro_fetchers import (
            CountryDetector,
        )

        detector = CountryDetector(self._bounds, parent=self)
        detector.finished.connect(self._on_country_detected)
        detector.error.connect(self._on_country_error)
        self._fetchers.append(detector)
        self._lbl_country.setText(tr("wizard_solar.macro_detecting"))
        detector.start()

    def _on_country_detected(self, iso3: str, name: str):
        self._country_iso = iso3
        self._country_name = name
        self._edit_iso.setText(iso3)
        self._lbl_country.setText(f"{name} ({iso3})")

    def _on_country_error(self, msg: str):
        self._lbl_country.setText(tr("wizard_solar.macro_detect_failed"))
        logger.warning("Country detection failed: %s", msg)

    def _fetch_all(self):
        iso = self._edit_iso.text().strip().upper()
        if not iso or len(iso) < 2:
            QMessageBox.warning(
                self, tr("wizard_solar.macro_warn_title"),
                tr("wizard_solar.macro_no_iso"),
            )
            return

        self._country_iso = iso
        self._fetch_progress.setValue(0)
        self._fetch_status.setText(tr("wizard_solar.macro_fetching"))
        self._btn_fetch.setEnabled(False)

        self._pending_fetches = 3
        self._fetch_errors: list[str] = []

        from esfex.visualization.workflows.solar_macro_fetchers import (
            IMFFetcher,
            IRENACostFetcher,
            WorldBankFetcher,
        )

        wb = WorldBankFetcher(iso, parent=self)
        wb.progress.connect(lambda p, m: self._fetch_progress.setValue(p // 3))
        wb.finished.connect(self._on_wb_finished)
        wb.error.connect(lambda m: self._on_fetch_error("World Bank", m))
        self._fetchers.append(wb)
        wb.start()

        imf = IMFFetcher(iso, parent=self)
        imf.progress.connect(
            lambda p, m: self._fetch_progress.setValue(33 + p // 3)
        )
        imf.finished.connect(self._on_imf_finished)
        imf.error.connect(lambda m: self._on_fetch_error("IMF", m))
        self._fetchers.append(imf)
        imf.start()

        irena = IRENACostFetcher(
            learning_rate=self._spin_learning.value(), parent=self
        )
        irena.progress.connect(
            lambda p, m: self._fetch_progress.setValue(66 + p // 3)
        )
        irena.finished.connect(self._on_irena_finished)
        irena.error.connect(lambda m: self._on_fetch_error("IRENA", m))
        self._fetchers.append(irena)
        irena.start()

    def _on_wb_finished(self, data: dict):
        if data.get("gdp_per_capita") is not None:
            self._spin_gdp.setValue(data["gdp_per_capita"])
        if data.get("urbanization_pct") is not None:
            self._spin_urban.setValue(data["urbanization_pct"])
        if data.get("population") is not None:
            self._spin_pop.setValue(int(data["population"]))
        self._check_all_fetches()

    def _on_imf_finished(self, data: dict):
        if data.get("gdp_growth_rate") is not None:
            self._spin_gdp_growth.setValue(data["gdp_growth_rate"])
        if data.get("inflation_rate") is not None:
            self._spin_inflation.setValue(data["inflation_rate"])
        self._check_all_fetches()

    def _on_irena_finished(self, data: dict):
        if data.get("pv_system_cost") is not None:
            self._spin_pv_cost.setValue(data["pv_system_cost"])
        if data.get("pv_cost_trajectory"):
            self._pv_cost_trajectory = data["pv_cost_trajectory"]
        self._check_all_fetches()

    def _on_fetch_error(self, source: str, msg: str):
        self._fetch_errors.append(f"{source}: {msg}")
        self._check_all_fetches()

    def _check_all_fetches(self):
        self._pending_fetches -= 1
        if self._pending_fetches <= 0:
            self._btn_fetch.setEnabled(True)
            self._fetch_progress.setValue(100)
            if self._fetch_errors:
                self._fetch_status.setText(
                    tr("wizard_solar.macro_partial_errors",
                       count=len(self._fetch_errors))
                )
            else:
                self._fetch_status.setText(tr("wizard_solar.macro_done"))

    def get_macro_data(self):
        """Return a MacroeconomicData populated from the UI fields."""
        from esfex.models.adoption_models import MacroeconomicData

        return MacroeconomicData(
            country_iso=self._edit_iso.text().strip().upper(),
            gdp_per_capita=self._spin_gdp.value(),
            electricity_tariff=self._spin_tariff.value(),
            pv_system_cost=self._spin_pv_cost.value(),
            pv_cost_learning_rate=self._spin_learning.value(),
            urbanization_pct=self._spin_urban.value(),
            population=self._spin_pop.value(),
            discount_rate=self._spin_discount.value(),
            inflation_rate=self._spin_inflation.value(),
            gdp_growth_rate=self._spin_gdp_growth.value(),
            pv_cost_trajectory=dict(self._pv_cost_trajectory),
        )

    def is_valid(self) -> bool:
        return self._spin_gdp.value() > 0

    def cancel_all(self):
        for f in self._fetchers:
            if hasattr(f, "cancel"):
                f.cancel()


# =====================================================================
# Step 7: Adoption Modeling
# =====================================================================


class _AdoptionWorker(QThread):
    """Run selected adoption models in a background thread."""

    progress = Signal(int, str)
    finished = Signal(list)  # list[AdoptionCurve]
    error = Signal(str)

    def __init__(
        self,
        methods: list[str],
        macro,
        max_potential_mw: float,
        base_year: int,
        target_year: int,
        method_params: dict,
        building_positions=None,
        parent=None,
    ):
        super().__init__(parent)
        self._methods = methods
        self._macro = macro
        self._max_mw = max_potential_mw
        self._base = base_year
        self._target = target_year
        self._params = method_params
        self._positions = building_positions

    def run(self):
        try:
            from esfex.models.adoption_models import (
                run_abm_adoption,
                run_bass_diffusion,
                run_logistic_adoption,
                run_techno_economic,
            )

            curves = []
            total = len(self._methods)

            for i, method in enumerate(self._methods):
                pct = int((i / total) * 100)
                self.progress.emit(pct, f"Running {method}...")

                p = self._params.get(method, {})
                if method == "logistic":
                    c = run_logistic_adoption(
                        self._macro, self._max_mw,
                        self._base, self._target,
                        coefficients=p.get("coefficients"),
                    )
                elif method == "bass":
                    c = run_bass_diffusion(
                        self._max_mw, self._base, self._target,
                        p=p.get("p", 0.03),
                        q=p.get("q", 0.38),
                        initial_penetration=p.get("initial", 0.01),
                    )
                elif method == "techno_economic":
                    c = run_techno_economic(
                        self._macro, self._max_mw,
                        avg_irradiance_kwh_m2=p.get("irradiance", 1600.0),
                        base_year=self._base,
                        target_year=self._target,
                        system_lifetime=p.get("lifetime", 25),
                        performance_ratio=p.get("pr", 0.80),
                        degradation_rate=p.get("degradation", 0.005),
                        price_sensitivity=p.get("sensitivity", 15.0),
                    )
                elif method == "abm":
                    c = run_abm_adoption(
                        self._macro, self._max_mw,
                        base_year=self._base,
                        target_year=self._target,
                        n_agents=p.get("n_agents", 1000),
                        n_iterations=p.get("n_iterations", 20),
                        building_positions=self._positions,
                        neighbor_radius_km=p.get("radius", 1.0),
                        w_economic=p.get("w_econ", 0.5),
                        w_social=p.get("w_social", 0.3),
                        w_awareness=p.get("w_aware", 0.2),
                        adoption_threshold=p.get("threshold", 0.5),
                    )
                else:
                    continue
                curves.append(c)

            self.progress.emit(100, "All models complete.")
            self.finished.emit(curves)

        except Exception as exc:
            logger.exception("AdoptionWorker error")
            self.error.emit(str(exc))


class AdoptionModelStep(QWidget):
    """Configure and run adoption models."""

    modelsFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curves: list = []
        self._validation_data: list = []  # list[ValidationData]
        self._worker: Optional[_AdoptionWorker] = None
        self._max_potential_mw = 0.0
        self._macro = None
        self._building_positions = None
        self._country_iso = ""
        self._irena_fetcher = None

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("wizard_solar.adopt_instruction")))

        # Year range
        year_row = QHBoxLayout()
        year_row.addWidget(QLabel(tr("wizard_solar.adopt_base_year")))
        self._spin_base = QSpinBox()
        self._spin_base.setRange(2020, 2040)
        self._spin_base.setValue(2025)
        year_row.addWidget(self._spin_base)
        year_row.addWidget(QLabel(tr("wizard_solar.adopt_target_year")))
        self._spin_target = QSpinBox()
        self._spin_target.setRange(2030, 2080)
        self._spin_target.setValue(2050)
        year_row.addWidget(self._spin_target)
        year_row.addStretch()
        layout.addLayout(year_row)

        # Method checkboxes
        methods_group = QGroupBox(tr("wizard_solar.adopt_methods"))
        methods_lay = QVBoxLayout(methods_group)

        self._cb_logistic = QCheckBox(tr("wizard_solar.adopt_logistic"))
        self._cb_logistic.setChecked(True)
        methods_lay.addWidget(self._cb_logistic)

        self._cb_bass = QCheckBox(tr("wizard_solar.adopt_bass"))
        self._cb_bass.setChecked(True)
        methods_lay.addWidget(self._cb_bass)

        self._cb_techno = QCheckBox(tr("wizard_solar.adopt_techno"))
        self._cb_techno.setChecked(True)
        methods_lay.addWidget(self._cb_techno)

        self._cb_abm = QCheckBox(tr("wizard_solar.adopt_abm"))
        self._cb_abm.setChecked(True)
        methods_lay.addWidget(self._cb_abm)

        layout.addWidget(methods_group)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("wizard_solar.adopt_preset")))
        self._combo_preset = QComboBox()
        self._combo_preset.addItems([
            tr("wizard_solar.adopt_conservative"),
            tr("wizard_solar.adopt_moderate"),
            tr("wizard_solar.adopt_aggressive"),
        ])
        self._combo_preset.setCurrentIndex(1)
        preset_row.addWidget(self._combo_preset)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        # Validation data
        valid_group = QGroupBox(tr("wizard_solar.adopt_validation"))
        valid_lay = QVBoxLayout(valid_group)

        valid_btns = QHBoxLayout()
        self._btn_fetch_irena = QPushButton(tr("wizard_solar.adopt_fetch_irena"))
        self._btn_fetch_irena.clicked.connect(self._fetch_irena)
        valid_btns.addWidget(self._btn_fetch_irena)

        self._btn_import_csv = QPushButton(tr("wizard_solar.adopt_import_csv"))
        self._btn_import_csv.clicked.connect(self._import_csv)
        valid_btns.addWidget(self._btn_import_csv)

        self._btn_manual_input = QPushButton(tr("wizard_solar.adopt_manual_input"))
        self._btn_manual_input.clicked.connect(self._manual_input)
        valid_btns.addWidget(self._btn_manual_input)

        valid_btns.addStretch()
        valid_lay.addLayout(valid_btns)

        self._lbl_validation = QLabel("")
        valid_lay.addWidget(self._lbl_validation)

        layout.addWidget(valid_group)

        # Run controls
        run_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_solar.adopt_run"))
        self._btn_run.clicked.connect(self._run_models)
        run_row.addWidget(self._btn_run)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        run_row.addWidget(self._progress)
        layout.addLayout(run_row)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(180)
        layout.addWidget(self._log, 1)

    def set_inputs(self, macro, max_potential_mw: float, building_positions=None):
        """Called from wizard transition: pass macro data and potential."""
        self._macro = macro
        self._max_potential_mw = max_potential_mw
        self._building_positions = building_positions
        self._country_iso = getattr(macro, "country_iso", "")

    # ── Validation data ─────────────────────────────────────────

    def _update_validation_label(self):
        total = sum(len(vd.capacity_mw) for vd in self._validation_data)
        sources = ", ".join(vd.label for vd in self._validation_data)
        if total > 0:
            self._lbl_validation.setText(
                tr("wizard_solar.adopt_validation_loaded",
                   n=total, source=sources)
            )
        else:
            self._lbl_validation.setText("")

    def _fetch_irena(self):
        iso = self._country_iso or ""
        if not iso:
            QMessageBox.warning(
                self, tr("wizard_solar.adopt_warn_title"),
                tr("wizard_solar.macro_no_iso"),
            )
            return

        from esfex.visualization.workflows.solar_macro_fetchers import (
            IRENACapacityFetcher,
        )

        self._btn_fetch_irena.setEnabled(False)
        self._irena_fetcher = IRENACapacityFetcher(iso, parent=self)
        self._irena_fetcher.finished.connect(self._on_irena_cap_finished)
        self._irena_fetcher.error.connect(self._on_irena_cap_error)
        self._irena_fetcher.start()

    def _on_irena_cap_finished(self, vd):
        self._btn_fetch_irena.setEnabled(True)
        # Replace any existing IRENA validation
        self._validation_data = [
            v for v in self._validation_data if v.source != "irena"
        ]
        self._validation_data.append(vd)
        self._update_validation_label()
        self._log.append(f"IRENA: loaded {len(vd.capacity_mw)} data points.")

    def _on_irena_cap_error(self, msg: str):
        self._btn_fetch_irena.setEnabled(True)
        self._log.append(f"IRENA error: {msg}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wizard_solar.adopt_import_csv"),
            "", tr("wizard_solar.adopt_csv_filter"),
        )
        if not path:
            return

        try:
            import csv as csv_mod

            from esfex.models.adoption_models import ValidationData

            years = []
            capacity = []
            with open(path, newline="") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    yr = int(row.get("year", 0))
                    mw = float(row.get("capacity_mw", 0))
                    if yr > 0:
                        years.append(yr)
                        capacity.append(mw)

            if not years:
                QMessageBox.warning(
                    self, tr("wizard_solar.adopt_warn_title"),
                    "CSV must have 'year' and 'capacity_mw' columns.",
                )
                return

            vd = ValidationData(
                label=Path(path).stem,
                years=years,
                capacity_mw=capacity,
                source="user_csv",
            )
            self._validation_data.append(vd)
            self._update_validation_label()
            self._log.append(f"CSV: loaded {len(years)} points from {Path(path).name}.")

        except Exception as exc:
            QMessageBox.warning(
                self, tr("wizard_solar.adopt_warn_title"), str(exc)
            )

    def _manual_input(self):
        """Open a dialog for manual entry of observed data points."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("wizard_solar.adopt_manual_title"))
        dlg.setMinimumWidth(350)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("wizard_solar.adopt_manual_instruction")))

        table = QTableWidget(10, 2)
        table.setHorizontalHeaderLabels(["Year", "Capacity (MW)"])
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        from esfex.models.adoption_models import ValidationData

        years = []
        capacity = []
        for row in range(table.rowCount()):
            yr_item = table.item(row, 0)
            mw_item = table.item(row, 1)
            if yr_item and mw_item:
                try:
                    yr = int(yr_item.text())
                    mw = float(mw_item.text())
                    if yr > 0:
                        years.append(yr)
                        capacity.append(mw)
                except ValueError:
                    continue

        if not years:
            return

        vd = ValidationData(
            label="Manual",
            years=years,
            capacity_mw=capacity,
            source="manual",
        )
        self._validation_data.append(vd)
        self._update_validation_label()
        self._log.append(f"Manual: added {len(years)} data points.")

    def _get_selected_methods(self) -> list[str]:
        methods = []
        if self._cb_logistic.isChecked():
            methods.append("logistic")
        if self._cb_bass.isChecked():
            methods.append("bass")
        if self._cb_techno.isChecked():
            methods.append("techno_economic")
        if self._cb_abm.isChecked():
            methods.append("abm")
        return methods

    def _get_preset_params(self) -> dict:
        """Return method-specific parameters based on preset selection."""
        idx = self._combo_preset.currentIndex()
        if idx == 0:  # Conservative
            return {
                "logistic": {"coefficients": {"beta_0": -4.0, "beta_policy": 0.2}},
                "bass": {"p": 0.02, "q": 0.25, "initial": 0.005},
                "techno_economic": {"sensitivity": 10.0},
                "abm": {"w_econ": 0.6, "w_social": 0.2, "w_aware": 0.2, "threshold": 0.6},
            }
        elif idx == 2:  # Aggressive
            return {
                "logistic": {"coefficients": {"beta_0": -2.0, "beta_policy": 0.8}},
                "bass": {"p": 0.05, "q": 0.50, "initial": 0.02},
                "techno_economic": {"sensitivity": 20.0},
                "abm": {"w_econ": 0.4, "w_social": 0.35, "w_aware": 0.25, "threshold": 0.4},
            }
        else:  # Moderate (default)
            return {
                "logistic": {},
                "bass": {"p": 0.03, "q": 0.38, "initial": 0.01},
                "techno_economic": {"sensitivity": 15.0},
                "abm": {},
            }

    def _run_models(self):
        methods = self._get_selected_methods()
        if not methods:
            QMessageBox.warning(
                self, tr("wizard_solar.adopt_warn_title"),
                tr("wizard_solar.adopt_no_methods"),
            )
            return

        if self._macro is None:
            QMessageBox.warning(
                self, tr("wizard_solar.adopt_warn_title"),
                tr("wizard_solar.adopt_no_macro"),
            )
            return

        self._btn_run.setEnabled(False)
        self._progress.setValue(0)
        self._log.clear()
        self._log.append(tr("wizard_solar.adopt_starting"))

        params = self._get_preset_params()

        self._worker = _AdoptionWorker(
            methods=methods,
            macro=self._macro,
            max_potential_mw=self._max_potential_mw,
            base_year=self._spin_base.value(),
            target_year=self._spin_target.value(),
            method_params=params,
            building_positions=self._building_positions,
            parent=self,
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
        self._btn_run.setEnabled(True)
        self._progress.setValue(100)
        self._log.append(
            f"\n{tr('wizard_solar.adopt_complete')}\n"
            f"  {len(curves)} method(s) computed."
        )
        for c in curves:
            final_mw = c.capacity_mw[-1] if c.capacity_mw else 0
            self._log.append(
                f"  {c.method}: {final_mw:.1f} MW by {c.years[-1]}"
            )
        self.modelsFinished.emit()

    def _on_error(self, msg: str):
        self._btn_run.setEnabled(True)
        self._log.append(f"\nERROR: {msg}")

    def get_curves(self) -> list:
        return self._curves

    def get_validation_data(self) -> list:
        """Return collected validation data sets."""
        return list(self._validation_data)

    def get_max_potential_mw(self) -> float:
        return self._max_potential_mw

    def is_valid(self) -> bool:
        return len(self._curves) > 0


# =====================================================================
# Step 8: Scenario Comparison
# =====================================================================


class ScenarioComparisonStep(QWidget):
    """Compare adoption curves from different methods."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curves: list = []
        self._validation_data: list = []
        self._max_potential_mw = 0.0
        self._selected_index = 0

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("wizard_solar.compare_instruction")))

        # Chart area (matplotlib)
        self._chart_widget = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_widget)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chart_widget, 2)

        # Selection radios + table
        sel_group = QGroupBox(tr("wizard_solar.compare_select"))
        sel_lay = QVBoxLayout(sel_group)
        self._radio_group_lay = QVBoxLayout()
        sel_lay.addLayout(self._radio_group_lay)
        self._radios: list[QRadioButton] = []

        # Summary table
        self._table = QTableWidget()
        self._table.setMaximumHeight(180)
        sel_lay.addWidget(self._table)

        layout.addWidget(sel_group)

        # Export buttons
        export_row = QHBoxLayout()
        self._btn_export_png = QPushButton(tr("wizard_solar.compare_export_png"))
        self._btn_export_png.clicked.connect(self._export_png)
        export_row.addWidget(self._btn_export_png)

        self._btn_export_csv = QPushButton(tr("wizard_solar.compare_export_csv"))
        self._btn_export_csv.clicked.connect(self._export_csv)
        export_row.addWidget(self._btn_export_csv)

        export_row.addStretch()
        layout.addLayout(export_row)

    def set_curves(
        self,
        curves: list,
        validation_data: list | None = None,
        max_potential_mw: float = 0.0,
    ):
        """Called from wizard transition: populate chart and selection."""
        self._curves = curves
        self._validation_data = validation_data or []
        self._max_potential_mw = max_potential_mw
        self._build_chart()
        self._build_selection()
        self._build_table()

    def _build_chart(self):
        # Clear previous chart
        while self._chart_layout.count():
            w = self._chart_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

        if not self._curves:
            self._chart_layout.addWidget(QLabel(tr("wizard_solar.compare_no_data")))
            return

        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._chart_layout.addWidget(
                QLabel(tr("wizard_solar.compare_no_matplotlib"))
            )
            return

        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor("white")
        ax = fig.add_subplot(111)
        ax.set_facecolor("white")

        # Model curves — plotted as installed capacity (MW)
        colors = ["#e67e22", "#2980b9", "#27ae60", "#e74c3c", "#9b59b6"]
        for i, curve in enumerate(self._curves):
            color = colors[i % len(colors)]
            ax.plot(
                curve.years, curve.capacity_mw,
                label=curve.method, color=color, linewidth=2,
            )

            # Confidence band for ABM (convert penetration bounds → MW)
            if curve.confidence_low and curve.confidence_high:
                max_mw = self._max_potential_mw or 1.0
                low_mw = [p * max_mw for p in curve.confidence_low]
                high_mw = [p * max_mw for p in curve.confidence_high]
                ax.fill_between(
                    curve.years, low_mw, high_mw, alpha=0.15, color=color,
                )

        # Validation data — scatter points
        val_markers = ["o", "s", "D", "^", "v"]
        val_colors = ["#333333", "#7f8c8d", "#2c3e50", "#8e44ad"]
        for j, vd in enumerate(self._validation_data):
            marker = val_markers[j % len(val_markers)]
            vcolor = val_colors[j % len(val_colors)]
            ax.scatter(
                vd.years, vd.capacity_mw,
                marker=marker, color=vcolor, s=40, zorder=5,
                label=vd.label, edgecolors="black", linewidths=0.5,
            )

        ax.set_xlabel("Year", color="black")
        ax.set_ylabel("Installed Capacity (MW)", color="black")
        ax.set_title("Solar PV Adoption Scenarios", color="black")
        ax.legend(
            facecolor="white", edgecolor="#ccc", labelcolor="black",
            fontsize=8, loc="upper left",
        )
        ax.tick_params(colors="black")
        for spine in ax.spines.values():
            spine.set_color("#ccc")
        ax.grid(True, alpha=0.3, color="#ddd")

        fig.tight_layout()

        canvas = FigureCanvasQTAgg(fig)
        self._canvas = canvas
        self._fig = fig
        self._chart_layout.addWidget(canvas)

    def _build_selection(self):
        # Clear old radios
        for r in self._radios:
            r.deleteLater()
        self._radios.clear()

        for i, curve in enumerate(self._curves):
            final_mw = curve.capacity_mw[-1] if curve.capacity_mw else 0
            radio = QRadioButton(
                f"{curve.method} — {final_mw:.1f} MW by "
                f"{curve.years[-1] if curve.years else '?'}"
            )
            if i == 0:
                radio.setChecked(True)
            radio.toggled.connect(lambda checked, idx=i: self._on_radio(checked, idx))
            self._radio_group_lay.addWidget(radio)
            self._radios.append(radio)

    def _on_radio(self, checked: bool, idx: int):
        if checked:
            self._selected_index = idx

    def _build_table(self):
        if not self._curves:
            return

        methods = [c.method for c in self._curves]
        years = self._curves[0].years if self._curves else []

        # Show every 5th year
        display_years = [y for y in years if y % 5 == 0 or y == years[-1]]

        self._table.setRowCount(len(methods))
        self._table.setColumnCount(len(display_years))
        self._table.setHorizontalHeaderLabels([str(y) for y in display_years])
        self._table.setVerticalHeaderLabels(methods)

        for row, curve in enumerate(self._curves):
            yr_map = dict(zip(curve.years, curve.capacity_mw))
            for col, yr in enumerate(display_years):
                val = yr_map.get(yr, 0)
                item = QTableWidgetItem(f"{val:.1f} MW")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

        self._table.resizeColumnsToContents()

    def _export_png(self):
        if not hasattr(self, "_fig"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.compare_save_png"),
            "adoption_scenarios.png", "PNG (*.png)"
        )
        if path:
            self._fig.savefig(path, dpi=150, facecolor="white")
            QMessageBox.information(
                self, tr("wizard_solar.export_title"),
                tr("wizard_solar.exported_msg", path=path),
            )

    def _export_csv(self):
        if not self._curves:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.compare_save_csv"),
            "adoption_curves.csv", "CSV (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["year"]
            for c in self._curves:
                header.extend([f"{c.method}_penetration", f"{c.method}_capacity_mw"])
            writer.writerow(header)

            years = self._curves[0].years
            for yi, yr in enumerate(years):
                row = [yr]
                for c in self._curves:
                    pen = c.penetration[yi] if yi < len(c.penetration) else 0
                    cap = c.capacity_mw[yi] if yi < len(c.capacity_mw) else 0
                    row.extend([f"{pen:.4f}", f"{cap:.2f}"])
                writer.writerow(row)

        QMessageBox.information(
            self, tr("wizard_solar.export_title"),
            tr("wizard_solar.exported_msg", path=path),
        )

    def get_selected_curve(self):
        """Return the user-selected adoption curve."""
        if 0 <= self._selected_index < len(self._curves):
            return self._curves[self._selected_index]
        return None

    def get_all_curves(self) -> list:
        return self._curves

    def is_valid(self) -> bool:
        return len(self._curves) > 0


# =====================================================================
# Step 9: Model Integration
# =====================================================================


class IntegrationStep(QWidget):
    """Apply adoption results to the ESFEX model or export files."""

    def __init__(self, model=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._curve = None
        self._macro = None
        self._analysis_summary = None
        self._all_curves: list = []

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("wizard_solar.integ_instruction")))

        # Selected curve summary
        self._lbl_summary = QLabel("")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setStyleSheet("font-weight: bold; padding: 8px;")
        layout.addWidget(self._lbl_summary)

        # Option A: Apply to model
        apply_group = QGroupBox(tr("wizard_solar.integ_apply_group"))
        apply_lay = QVBoxLayout(apply_group)

        apply_lay.addWidget(QLabel(tr("wizard_solar.integ_apply_desc")))

        self._btn_apply = QPushButton(tr("wizard_solar.integ_apply_btn"))
        self._btn_apply.clicked.connect(self._apply_to_model)
        apply_lay.addWidget(self._btn_apply)

        self._apply_status = QLabel("")
        apply_lay.addWidget(self._apply_status)

        layout.addWidget(apply_group)

        # Option B: Export files
        export_group = QGroupBox(tr("wizard_solar.integ_export_group"))
        export_lay = QVBoxLayout(export_group)

        btn_row1 = QHBoxLayout()
        self._btn_export_curves = QPushButton(tr("wizard_solar.integ_export_curves"))
        self._btn_export_curves.clicked.connect(self._export_curves_csv)
        btn_row1.addWidget(self._btn_export_curves)

        self._btn_export_macro = QPushButton(tr("wizard_solar.integ_export_macro"))
        self._btn_export_macro.clicked.connect(self._export_macro_json)
        btn_row1.addWidget(self._btn_export_macro)
        export_lay.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self._btn_export_params = QPushButton(tr("wizard_solar.integ_export_params"))
        self._btn_export_params.clicked.connect(self._export_params_json)
        btn_row2.addWidget(self._btn_export_params)

        self._btn_export_buildings = QPushButton(tr("wizard_solar.integ_export_buildings"))
        self._btn_export_buildings.clicked.connect(self._export_buildings)
        btn_row2.addWidget(self._btn_export_buildings)
        export_lay.addLayout(btn_row2)

        layout.addWidget(export_group)
        layout.addStretch()

    def set_inputs(
        self,
        selected_curve,
        all_curves: list,
        macro,
        analysis_summary=None,
    ):
        """Called from wizard transition."""
        self._curve = selected_curve
        self._all_curves = all_curves
        self._macro = macro
        self._analysis_summary = analysis_summary

        if selected_curve:
            final = selected_curve.penetration[-1] if selected_curve.penetration else 0
            final_mw = selected_curve.capacity_mw[-1] if selected_curve.capacity_mw else 0
            self._lbl_summary.setText(
                f"Selected: {selected_curve.method}\n"
                f"Period: {selected_curve.years[0]}–{selected_curve.years[-1]}\n"
                f"Final penetration: {final:.1%}\n"
                f"Final installed capacity: {final_mw:.1f} MW"
            )
        else:
            self._lbl_summary.setText(tr("wizard_solar.integ_no_curve"))

        # Enable/disable apply based on model availability
        self._btn_apply.setEnabled(self._model is not None and self._curve is not None)

    def _apply_to_model(self):
        """Write adoption parameters into the GUI model's rooftop solar config."""
        if self._model is None or self._curve is None:
            return

        try:
            from esfex.models.adoption_models import fit_adoption_to_rooftop_config

            # Get per-node data from analysis summary
            summary = self._analysis_summary
            num_nodes = 1  # default single node
            systems_per_node = [0]
            avg_system_size = [0.0]

            if summary is not None:
                # Estimate from building results
                total_systems = summary.suitable_buildings
                total_capacity = summary.total_capacity_kwp
                systems_per_node = [total_systems]
                if total_systems > 0:
                    avg_system_size = [total_capacity / total_systems]
                else:
                    avg_system_size = [5.0]

            config = fit_adoption_to_rooftop_config(
                curve=self._curve,
                macro=self._macro,
                num_nodes=num_nodes,
                systems_per_node=systems_per_node,
                avg_system_size=avg_system_size,
            )

            # Apply to model
            state = self._model.state
            rs = state.rooftop_solar
            for key, val in config.items():
                if hasattr(rs, key):
                    setattr(rs, key, val)

            # Enable rooftop solar simulation
            if hasattr(state.settings, "sim_rooftop"):
                state.settings.sim_rooftop = True

            self._apply_status.setText(tr("wizard_solar.integ_applied"))
            self._apply_status.setStyleSheet("color: #27ae60; font-weight: bold;")

        except Exception as exc:
            logger.exception("Failed to apply adoption to model")
            self._apply_status.setText(f"Error: {exc}")
            self._apply_status.setStyleSheet("color: #e74c3c;")

    def _export_curves_csv(self):
        if not self._all_curves:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.integ_save_curves"),
            "adoption_curves.csv", "CSV (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["year"]
            for c in self._all_curves:
                header.extend([
                    f"{c.method}_penetration",
                    f"{c.method}_capacity_mw",
                ])
            writer.writerow(header)

            years = self._all_curves[0].years
            for yi, yr in enumerate(years):
                row = [yr]
                for c in self._all_curves:
                    pen = c.penetration[yi] if yi < len(c.penetration) else 0
                    cap = c.capacity_mw[yi] if yi < len(c.capacity_mw) else 0
                    row.extend([f"{pen:.4f}", f"{cap:.2f}"])
                writer.writerow(row)

        QMessageBox.information(
            self, tr("wizard_solar.export_title"),
            tr("wizard_solar.exported_msg", path=path),
        )

    def _export_macro_json(self):
        if self._macro is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.integ_save_macro"),
            "macro_data.json", "JSON (*.json)"
        )
        if not path:
            return

        from dataclasses import asdict
        data = asdict(self._macro)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        QMessageBox.information(
            self, tr("wizard_solar.export_title"),
            tr("wizard_solar.exported_msg", path=path),
        )

    def _export_params_json(self):
        if not self._all_curves:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.integ_save_params"),
            "model_parameters.json", "JSON (*.json)"
        )
        if not path:
            return

        params = {}
        for c in self._all_curves:
            params[c.method] = {
                "parameters": c.parameters,
                "final_penetration": c.penetration[-1] if c.penetration else 0,
                "final_capacity_mw": c.capacity_mw[-1] if c.capacity_mw else 0,
            }

        with open(path, "w") as f:
            json.dump(params, f, indent=2)

        QMessageBox.information(
            self, tr("wizard_solar.export_title"),
            tr("wizard_solar.exported_msg", path=path),
        )

    def _export_buildings(self):
        """Export building analysis as GeoJSON (from Phase A)."""
        if self._analysis_summary is None:
            QMessageBox.warning(
                self, tr("wizard_solar.integ_warn_title"),
                tr("wizard_solar.integ_no_buildings"),
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.integ_save_buildings"),
            "buildings_analysis.csv", "CSV (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "building_id", "capacity_kw", "annual_kwh",
                "specific_yield", "usable_roof_area", "suitable",
            ])
            for br in self._analysis_summary.building_results:
                writer.writerow([
                    br.building_id, f"{br.capacity_kw:.2f}",
                    f"{br.annual_kwh:.1f}", f"{br.specific_yield:.0f}",
                    f"{br.usable_roof_area:.1f}", br.suitable,
                ])

        QMessageBox.information(
            self, tr("wizard_solar.export_title"),
            tr("wizard_solar.exported_msg", path=path),
        )

    def is_valid(self) -> bool:
        return True
