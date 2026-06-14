"""Step widgets for the Wind Resource Assessment wizard.

Each step is a QWidget displayed in the wizard's QStackedWidget.
"""

from __future__ import annotations

import json
import math
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from windrex import (
    CriterionConfig,
    DEFAULT_LULC_SCORES,
    MCDAConfig,
    TurbineSpec,
    load_turbine_database,
)

# esfex's own fat wind config (turbine as a key string, hub_height, installation,
# effective_workers, …) — matches what esfex's analyzer/wizard read. windrex's
# WindConfig is a different, slimmer dataclass (turbine as a TurbineSpec) used by
# windrex's own analyzer, which esfex does not use; importing it here passed
# kwargs it no longer accepts and broke get_config().
from esfex.visualization.workflows.wind_analysis import WindConfig

from esfex.visualization.i18n import tr


class _TurbineLoaderThread(QThread):
    """Load turbine database on background thread to avoid blocking the UI."""

    finished = Signal(list)
    error = Signal(str)

    def run(self):
        try:
            turbines = load_turbine_database()
            self.finished.emit(turbines)
        except Exception as exc:
            self.error.emit(str(exc))


# =====================================================================
# Step 1: Domain Definition
# =====================================================================


class WindDomainStep(QWidget):
    """Define the geographic bounding box for wind assessment."""

    domainChanged = Signal()

    def __init__(self, map_widget, parent=None, geo_assets_provider=None):
        super().__init__(parent)
        self._map_widget = map_widget

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Define the analysis domain by drawing a polygon on the map "
                "or applying an imported GeoAsset."
            )
        )

        # Standard two-column domain selector: draw a polygon OR apply a GeoAsset.
        from esfex.visualization.workflows._domain_definition import (
            DomainDefinitionWidget,
        )
        self._domain = DomainDefinitionWidget(map_widget, geo_assets_provider)
        self._domain.domainChanged.connect(self.domainChanged)
        layout.addWidget(self._domain)
        layout.addStretch()

    def get_bounds(self) -> tuple[float, float, float, float]:
        return self._domain.get_bounds()  # type: ignore[return-value]

    def get_polygon(self) -> list[tuple[float, float]]:
        return self._domain.get_polygon()

    def is_valid(self) -> bool:
        if not self._domain.is_defined():
            QMessageBox.warning(
                self, tr("wizard_otec.domain_required_title"), tr("wizard_otec.domain_required_msg")
            )
            return False
        return True


# =====================================================================
# Step 2: Wind Configuration (Turbine Database + Parameters)
# =====================================================================


class WindConfigStep(QWidget):
    """Configure wind turbine (from database) and assessment parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._turbines: list[TurbineSpec] = []
        self._filtered_turbines: list[TurbineSpec] = []
        self._selected_turbine: TurbineSpec | None = None

        layout = QVBoxLayout(self)

        # ── Turbine Selection ──
        turb_group = QGroupBox(tr("wizard_wind.group_turbine_sel"))
        turb_lay = QVBoxLayout(turb_group)

        # Filter bar
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel(tr("wizard_wind.manufacturer")))
        self._combo_manufacturer = QComboBox()
        self._combo_manufacturer.addItem("All")
        self._combo_manufacturer.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._combo_manufacturer)

        filter_row.addWidget(QLabel(tr("wizard_wind.min_power")))
        self._spin_min_power = QDoubleSpinBox()
        self._spin_min_power.setRange(0, 20)
        self._spin_min_power.setValue(0)
        self._spin_min_power.setSuffix(" MW")
        self._spin_min_power.setDecimals(1)
        self._spin_min_power.valueChanged.connect(self._apply_filter)
        filter_row.addWidget(self._spin_min_power)

        filter_row.addWidget(QLabel(tr("wizard_wind.max_power")))
        self._spin_max_power = QDoubleSpinBox()
        self._spin_max_power.setRange(0, 20)
        self._spin_max_power.setValue(20)
        self._spin_max_power.setSuffix(" MW")
        self._spin_max_power.setDecimals(1)
        self._spin_max_power.valueChanged.connect(self._apply_filter)
        filter_row.addWidget(self._spin_max_power)

        self._btn_load_oedb = QPushButton(tr("wizard_wind.load_oedb"))
        self._btn_load_oedb.setToolTip(
            "Download turbine data from the Open Energy Database\n"
            "(100+ additional turbine models, requires internet)"
        )
        self._btn_load_oedb.clicked.connect(self._load_oedb_database)
        filter_row.addWidget(self._btn_load_oedb)

        turb_lay.addLayout(filter_row)

        # Turbine table
        self._turb_table = QTableWidget(0, 6)
        self._turb_table.setHorizontalHeaderLabels([
            "Manufacturer", "Model", "Rated (MW)",
            "Rotor (m)", "Hub (m)", "Source",
        ])
        header = self._turb_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._turb_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows,
        )
        self._turb_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection,
        )
        self._turb_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers,
        )
        self._turb_table.currentCellChanged.connect(self._on_turbine_selected)
        turb_lay.addWidget(self._turb_table)

        # Turbine details panel
        self._details_label = QLabel(
            "<i>Select a turbine from the table above to see its details.</i>"
        )
        self._details_label.setWordWrap(True)
        self._details_label.setStyleSheet(
            "background-color: #2c2c2c; color: #e0e0e0; "
            "padding: 8px; border-radius: 4px;"
        )
        self._details_label.setMinimumHeight(80)
        turb_lay.addWidget(self._details_label)

        # Hub height override
        hub_row = QHBoxLayout()
        hub_row.addWidget(QLabel(tr("wizard_wind.hub_height_override")))
        self._spin_hub_height = QSpinBox()
        self._spin_hub_height.setRange(30, 300)
        self._spin_hub_height.setValue(80)
        self._spin_hub_height.setSuffix(" m")
        self._spin_hub_height.setToolTip(
            "Override the default hub height from the turbine database.\n"
            "Leave at the turbine's default or adjust for your specific site."
        )
        hub_row.addWidget(self._spin_hub_height)
        hub_row.addStretch()
        turb_lay.addLayout(hub_row)

        layout.addWidget(turb_group)

        # ── Analysis Parameters ──
        analysis_group = QGroupBox(tr("wizard_wind.group_analysis_params"))
        analysis_form = QFormLayout(analysis_group)

        self._spin_year = QSpinBox()
        self._spin_year.setRange(1979, 2023)
        self._spin_year.setValue(2022)
        analysis_form.addRow(tr("wizard_wind.analysis_year"), self._spin_year)

        self._spin_grid_res = QDoubleSpinBox()
        self._spin_grid_res.setRange(0.05, 2.0)
        self._spin_grid_res.setValue(0.25)
        self._spin_grid_res.setDecimals(2)
        self._spin_grid_res.setSuffix(" \u00b0")
        self._spin_grid_res.setToolTip("Grid spacing in degrees")
        analysis_form.addRow(tr("wizard_wind.grid_res"), self._spin_grid_res)

        self._combo_install = QComboBox()
        self._combo_install.addItems(["Onshore", "Offshore"])
        analysis_form.addRow(tr("wizard_wind.installation_type"), self._combo_install)

        self._combo_data_source = QComboBox()
        self._combo_data_source.addItems([
            tr("wizard_common.ds_open_meteo"),
            tr("wizard_common.ds_nasa_power"),
            tr("wizard_common.ds_era5_atlite"),
        ])
        self._combo_data_source.setToolTip(
            "Open-Meteo: Fast download, same ERA5 data\n"
            "NASA POWER: Fast download, MERRA-2 reanalysis\n"
            "ERA5 via atlite: Slow (hours), requires CDS API key"
        )
        analysis_form.addRow(tr("wizard_common.data_source"), self._combo_data_source)

        self._spin_workers = QSpinBox()
        self._spin_workers.setRange(0, 64)
        self._spin_workers.setValue(0)
        self._spin_workers.setSpecialValueText("Auto")
        self._spin_workers.setToolTip(
            tr("wizard_wind.workers_tip")
        )
        analysis_form.addRow(tr("wizard_wind.parallel_workers"), self._spin_workers)

        layout.addWidget(analysis_group)

        # ── Zone Criteria ──
        zone_group = QGroupBox(tr("wizard_wind.group_zone_criteria"))
        zone_form = QFormLayout(zone_group)

        self._spin_min_cf = QDoubleSpinBox()
        self._spin_min_cf.setRange(0.05, 0.80)
        self._spin_min_cf.setValue(0.25)
        self._spin_min_cf.setDecimals(2)
        self._spin_min_cf.setSingleStep(0.05)
        self._spin_min_cf.setToolTip(
            "Minimum capacity factor for a site to be considered feasible"
        )
        zone_form.addRow(tr("wizard_wind.min_cf"), self._spin_min_cf)

        self._spin_buffer = QDoubleSpinBox()
        self._spin_buffer.setRange(1, 100)
        self._spin_buffer.setValue(5)
        self._spin_buffer.setDecimals(1)
        self._spin_buffer.setSuffix(" km")
        zone_form.addRow(tr("wizard_wind.zone_buffer"), self._spin_buffer)

        layout.addWidget(zone_group)

        # Load built-in turbine database (background thread)
        self._turbines: list[TurbineSpec] = []
        self._load_builtin_database()

    # ------------------------------------------------------------------
    # Database loading
    # ------------------------------------------------------------------

    def _load_builtin_database(self):
        """Load atlite's built-in turbine database on a background thread."""
        self._loader_thread = _TurbineLoaderThread(self)
        self._loader_thread.finished.connect(self._on_turbines_loaded)
        self._loader_thread.error.connect(self._on_turbines_error)
        self._loader_thread.start()

    def _on_turbines_loaded(self, turbines):
        """Populate table once turbine data arrives from background thread."""
        self._turbines = turbines
        self._populate_manufacturers()
        self._apply_filter()
        if self._turb_table.rowCount() > 0:
            self._turb_table.selectRow(0)

    def _on_turbines_error(self, msg):
        """Handle turbine loading failure."""
        QMessageBox.warning(
            self,
            tr("wizard_wind.turbine_db_title"),
            f"Could not load turbine database:\n{msg}\n\n"
            "Make sure atlite is installed: pip install atlite",
        )

    def _load_oedb_database(self):
        """Download OEDB turbine database (100+ additional turbines)."""
        self._btn_load_oedb.setEnabled(False)
        self._btn_load_oedb.setText(tr("wizard_wind.loading_oedb"))

        try:
            from windrex.data.turbine_db import (
                _load_oedb_turbines,
            )
            oedb = _load_oedb_turbines()
            existing_keys = {t.key for t in self._turbines}
            added = 0
            for t in oedb:
                if t.key not in existing_keys:
                    self._turbines.append(t)
                    added += 1

            self._turbines.sort(
                key=lambda t: (t.manufacturer.lower(), t.rated_power_mw),
            )
            self._populate_manufacturers()
            self._apply_filter()

            self._btn_load_oedb.setText(f"OEDB loaded (+{added})")
        except Exception as exc:
            self._btn_load_oedb.setEnabled(True)
            self._btn_load_oedb.setText("Load OEDB")
            QMessageBox.warning(
                self,
                "OEDB Download Failed",
                f"Could not download OEDB database:\n{exc}\n\n"
                "Check your internet connection.",
            )

    def _populate_manufacturers(self):
        """Populate manufacturer filter combo from loaded turbines."""
        manufacturers = sorted(
            {t.manufacturer for t in self._turbines if t.manufacturer},
        )
        current = self._combo_manufacturer.currentText()
        self._combo_manufacturer.blockSignals(True)
        self._combo_manufacturer.clear()
        self._combo_manufacturer.addItem("All")
        for m in manufacturers:
            self._combo_manufacturer.addItem(m)
        # Restore selection
        idx = self._combo_manufacturer.findText(current)
        if idx >= 0:
            self._combo_manufacturer.setCurrentIndex(idx)
        self._combo_manufacturer.blockSignals(False)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self):
        """Filter turbine table based on manufacturer and power range."""
        mfr = self._combo_manufacturer.currentText()
        min_mw = self._spin_min_power.value()
        max_mw = self._spin_max_power.value()

        self._filtered_turbines = [
            t for t in self._turbines
            if (mfr == "All" or t.manufacturer == mfr)
            and min_mw <= t.rated_power_mw <= max_mw
        ]

        self._turb_table.setRowCount(len(self._filtered_turbines))
        for row, t in enumerate(self._filtered_turbines):
            self._turb_table.setItem(row, 0, QTableWidgetItem(t.manufacturer))
            self._turb_table.setItem(row, 1, QTableWidgetItem(t.name))
            self._turb_table.setItem(
                row, 2, QTableWidgetItem(f"{t.rated_power_mw:.2f}"),
            )
            self._turb_table.setItem(
                row, 3,
                QTableWidgetItem(
                    f"{t.rotor_diameter_m:.0f}" if t.rotor_diameter_m > 0 else "\u2014"
                ),
            )
            self._turb_table.setItem(
                row, 4, QTableWidgetItem(f"{t.hub_height_m:.0f}"),
            )
            self._turb_table.setItem(row, 5, QTableWidgetItem(t.source))

        # Re-select if possible
        if self._filtered_turbines:
            self._turb_table.selectRow(0)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_turbine_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int):
        if row < 0 or row >= len(self._filtered_turbines):
            self._selected_turbine = None
            self._details_label.setText(
                "<i>Select a turbine from the table above.</i>"
            )
            return

        t = self._filtered_turbines[row]
        self._selected_turbine = t

        # Update hub height to turbine default
        self._spin_hub_height.setValue(int(t.hub_height_m))

        # Build details text. windrex's TurbineSpec carries no specific_power
        # property, so derive it (W/m² of rotor area) from the rated power.
        _area = math.pi * (t.rotor_diameter_m / 2) ** 2
        sp = (t.rated_power_mw * 1e6) / _area if _area > 0 else 0.0
        lines = [
            f"<b>{t.manufacturer} {t.name}</b>",
            f"Rated power: <b>{t.rated_power_mw:.2f} MW</b>",
        ]
        if t.rotor_diameter_m > 0:
            lines.append(
                f"Rotor diameter: <b>{t.rotor_diameter_m:.0f} m</b> "
                f"(specific power: {sp:.0f} W/m\u00b2)"
            )
        lines.append(f"Default hub height: <b>{t.hub_height_m:.0f} m</b>")
        lines.append(f"Source: {t.source}")

        # Power curve summary
        if t.power_curve and t.wind_speeds:
            cut_in = next(
                (ws for ws, pw in zip(t.wind_speeds, t.power_curve) if pw > 0),
                None,
            )
            cut_out = t.wind_speeds[-1] if t.wind_speeds else None
            # Rated wind speed = first speed where power reaches rated
            rated_ws = next(
                (ws for ws, pw in zip(t.wind_speeds, t.power_curve)
                 if pw >= t.rated_power_mw * 0.99),
                None,
            )
            curve_info = []
            if cut_in is not None:
                curve_info.append(f"Cut-in: {cut_in:.0f} m/s")
            if rated_ws is not None:
                curve_info.append(f"Rated: {rated_ws:.0f} m/s")
            if cut_out is not None:
                curve_info.append(f"Cut-out: {cut_out:.0f} m/s")
            if curve_info:
                lines.append("Power curve: " + " | ".join(curve_info))

            # ASCII mini power curve (compact)
            lines.append(self._mini_power_curve(t))

        self._details_label.setText("<br>".join(lines))

    @staticmethod
    def _mini_power_curve(t: TurbineSpec) -> str:
        """Build a compact text representation of the power curve."""
        if not t.power_curve or not t.wind_speeds:
            return ""
        max_pw = max(t.power_curve) if t.power_curve else 1
        if max_pw <= 0:
            return ""

        # Sample at key wind speeds: 3, 5, 7, 9, 11, 13, 15, 20, 25
        sample_ws = [3, 5, 7, 9, 11, 13, 15, 20, 25]
        parts = []
        for ws in sample_ws:
            if ws > max(t.wind_speeds):
                break
            # Find nearest
            best_i = min(
                range(len(t.wind_speeds)),
                key=lambda i: abs(t.wind_speeds[i] - ws),
            )
            pw = t.power_curve[best_i]
            bar_len = int(pw / max_pw * 8)
            bar = "\u2588" * bar_len
            parts.append(f"{ws:>2d} m/s: {bar} {pw:.2f} MW")

        return "<br>".join(["<b>Power curve:</b>"] + parts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> WindConfig:
        t = self._selected_turbine
        turbine_key = t.key if t else "Vestas_V112_3MW"
        rated_mw = t.rated_power_mw if t else 3.0

        install_map = {0: "onshore", 1: "offshore"}
        ds_map = {0: "open_meteo", 1: "nasa_power", 2: "era5_atlite"}
        return WindConfig(
            turbine=turbine_key,
            hub_height=self._spin_hub_height.value(),
            year=self._spin_year.value(),
            grid_resolution=self._spin_grid_res.value(),
            min_capacity_factor=self._spin_min_cf.value(),
            installation=install_map.get(
                self._combo_install.currentIndex(), "onshore",
            ),
            zone_buffer_km=self._spin_buffer.value(),
            turbine_capacity_mw=rated_mw,
            data_source=ds_map.get(
                self._combo_data_source.currentIndex(), "open_meteo",
            ),
            parallel_workers=self._spin_workers.value(),
            wind_speeds=t.wind_speeds if t else [],
            power_curve=t.power_curve if t else [],
        )

    def get_turbine_spec(self) -> "TurbineSpec | None":
        """Return the selected turbine specification."""
        return self._selected_turbine

    def is_valid(self) -> bool:
        if self._selected_turbine is None:
            QMessageBox.warning(
                self, tr("wizard_wind.turbine_required_title"), tr("wizard_wind.turbine_required_msg")
            )
            return False
        return True


# =====================================================================
# Step 3: MCDA Criteria Configuration
# =====================================================================

_CRITERIA_DEFS = [
    ("capacity_factor", "Wind Capacity Factor", "maximize", 0.40),
    ("slope", "Terrain Slope", "minimize", 0.15),
    ("elevation", "Elevation", "minimize", 0.10),
    ("lulc_score", "LULC Suitability", "maximize", 0.20),
    ("dist_grid_km", "Distance to Grid", "minimize", 0.15),
]

_LULC_CLASS_NAMES = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse veg.",
    70: "Snow and ice",
    80: "Water bodies",
    90: "Herbaceous wetland",
    95: "Mangroves",
}


class CriteriaConfigStep(QWidget):
    """Configure MCDA criteria and weighting method."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Configure the multi-criteria decision analysis (MCDA) for "
                "evaluating wind development zones."
            )
        )

        # Weighting method
        method_group = QGroupBox(tr("wizard_wind.group_weighting"))
        method_form = QFormLayout(method_group)

        self._combo_method = QComboBox()
        self._combo_method.addItems([
            tr("wizard_wind.method_manual"),
            tr("wizard_wind.method_entropy"),
            tr("wizard_wind.method_pca"),
        ])
        self._combo_method.currentIndexChanged.connect(self._on_method_changed)
        method_form.addRow(tr("wizard_wind.weighting_method"), self._combo_method)

        self._method_info = QLabel(
            "Manually assign weights to each criterion. "
            "Weights will be normalized to sum to 1."
        )
        self._method_info.setWordWrap(True)
        self._method_info.setStyleSheet("color: #888; font-style: italic;")
        method_form.addRow(self._method_info)

        layout.addWidget(method_group)

        # Criteria table
        criteria_group = QGroupBox(tr("wizard_wind.group_criteria"))
        criteria_lay = QVBoxLayout(criteria_group)

        self._criteria_table = QTableWidget(len(_CRITERIA_DEFS), 4)
        self._criteria_table.setHorizontalHeaderLabels([
            "Enabled", "Criterion", "Direction", "Weight",
        ])
        header = self._criteria_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        for row, (key, label, direction, default_weight) in enumerate(_CRITERIA_DEFS):
            # Enable checkbox
            chk = QCheckBox()
            chk.setChecked(True)
            self._criteria_table.setCellWidget(row, 0, chk)

            # Name
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._criteria_table.setItem(row, 1, item)

            # Direction
            item_dir = QTableWidgetItem(direction.capitalize())
            item_dir.setFlags(item_dir.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._criteria_table.setItem(row, 2, item_dir)

            # Weight
            weight_spin = QDoubleSpinBox()
            weight_spin.setRange(0.0, 1.0)
            weight_spin.setDecimals(2)
            weight_spin.setSingleStep(0.05)
            weight_spin.setValue(default_weight)
            self._criteria_table.setCellWidget(row, 3, weight_spin)

        criteria_lay.addWidget(self._criteria_table)
        layout.addWidget(criteria_group)

        # LULC scoring table (collapsible)
        self._lulc_check = QCheckBox(tr("wizard_wind.customize_lulc"))
        self._lulc_check.toggled.connect(self._toggle_lulc)
        layout.addWidget(self._lulc_check)

        self._lulc_group = QGroupBox(tr("wizard_wind.group_lulc_scores"))
        lulc_lay = QVBoxLayout(self._lulc_group)

        self._lulc_table = QTableWidget(len(_LULC_CLASS_NAMES), 3)
        self._lulc_table.setHorizontalHeaderLabels([
            "Code", "Land Cover Class", "Suitability (0-1)",
        ])
        lulc_header = self._lulc_table.horizontalHeader()
        lulc_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        lulc_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lulc_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for row, (code, name) in enumerate(_LULC_CLASS_NAMES.items()):
            # Code
            code_item = QTableWidgetItem(str(code))
            code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._lulc_table.setItem(row, 0, code_item)

            # Name
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._lulc_table.setItem(row, 1, name_item)

            # Score
            score_spin = QDoubleSpinBox()
            score_spin.setRange(0.0, 1.0)
            score_spin.setDecimals(2)
            score_spin.setSingleStep(0.1)
            score_spin.setValue(DEFAULT_LULC_SCORES.get(code, 0.5))
            self._lulc_table.setCellWidget(row, 2, score_spin)

        lulc_lay.addWidget(self._lulc_table)
        self._lulc_group.setVisible(False)
        layout.addWidget(self._lulc_group)

        layout.addStretch()

    def _on_method_changed(self, index: int):
        method_descriptions = [
            "Manually assign weights to each criterion. "
            "Weights will be normalized to sum to 1.",
            "Weights computed automatically from data using Shannon entropy. "
            "Criteria with more variation get higher weights.",
            "Weights derived from first principal component loadings. "
            "Criteria that explain most variance get higher weights.",
        ]
        self._method_info.setText(method_descriptions[index])

        # Enable/disable weight editing for non-manual methods
        is_manual = (index == 0)
        for row in range(self._criteria_table.rowCount()):
            spin = self._criteria_table.cellWidget(row, 3)
            if isinstance(spin, QDoubleSpinBox):
                spin.setEnabled(is_manual)

    def _toggle_lulc(self, checked: bool):
        self._lulc_group.setVisible(checked)

    def get_config(self) -> MCDAConfig:
        method_map = {0: "manual", 1: "entropy", 2: "pca"}
        method = method_map.get(self._combo_method.currentIndex(), "manual")

        criteria: dict[str, CriterionConfig] = {}
        for row, (key, _, direction, _) in enumerate(_CRITERIA_DEFS):
            chk = self._criteria_table.cellWidget(row, 0)
            spin = self._criteria_table.cellWidget(row, 3)
            criteria[key] = CriterionConfig(
                enabled=chk.isChecked() if isinstance(chk, QCheckBox) else True,
                weight=spin.value() if isinstance(spin, QDoubleSpinBox) else 0.2,
                direction=direction,
            )

        lulc_scores = dict(DEFAULT_LULC_SCORES)
        if self._lulc_check.isChecked():
            codes = list(_LULC_CLASS_NAMES.keys())
            for row, code in enumerate(codes):
                spin = self._lulc_table.cellWidget(row, 2)
                if isinstance(spin, QDoubleSpinBox):
                    lulc_scores[code] = spin.value()

        return MCDAConfig(
            method=method,
            criteria=criteria,
            lulc_scores=lulc_scores,
        )

    def is_valid(self) -> bool:
        # At least one criterion must be enabled
        any_enabled = False
        for row in range(self._criteria_table.rowCount()):
            chk = self._criteria_table.cellWidget(row, 0)
            if isinstance(chk, QCheckBox) and chk.isChecked():
                any_enabled = True
                break
        if not any_enabled:
            QMessageBox.warning(
                self,
                tr("wizard_wind.no_criteria_title"),
                tr("wizard_wind.no_criteria_msg"),
            )
            return False
        return True


# =====================================================================
# Step 4: Analysis
# =====================================================================


class WindAnalysisStep(QWidget):
    """Run wind resource assessment in background."""

    analysisFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analyzer = None
        self._summary = None

        layout = QVBoxLayout(self)

        # Input summary
        self._summary_label = QLabel(tr("wizard_wind.config_summary"))
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # Run button
        btn_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_wind.run_analysis"))
        self._btn_run.clicked.connect(self._run_analysis)
        self._btn_run.setEnabled(False)
        btn_row.addWidget(self._btn_run)

        self._btn_cancel = QPushButton(tr("wizard_wind.cancel_analysis"))
        self._btn_cancel.clicked.connect(self._cancel_analysis)
        self._btn_cancel.setEnabled(False)
        btn_row.addWidget(self._btn_cancel)

        layout.addLayout(btn_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(200)
        layout.addWidget(self._log)

        layout.addStretch()

    def set_inputs(
        self,
        bounds: tuple[float, float, float, float],
        wind_config: WindConfig,
        mcda_config: MCDAConfig,
        transmission_lines: list | None = None,
        polygon: list | None = None,
    ):
        self._bounds = bounds
        self._wind_config = wind_config
        self._mcda_config = mcda_config
        self._transmission_lines = transmission_lines or []
        self._polygon = polygon or []

        s, w, n, e = bounds
        enabled_criteria = [
            name for name, c in mcda_config.criteria.items() if c.enabled
        ]
        ds_labels = {
            "open_meteo": "Open-Meteo (ERA5)",
            "nasa_power": "NASA POWER (MERRA-2)",
            "era5_atlite": "ERA5 via atlite",
        }
        self._summary_label.setText(
            f"<b>Domain:</b> ({s:.4f}, {w:.4f}) to ({n:.4f}, {e:.4f})<br>"
            f"<b>Turbine:</b> {wind_config.turbine} | "
            f"<b>Hub Height:</b> {wind_config.hub_height} m<br>"
            f"<b>Year:</b> {wind_config.year} | "
            f"<b>Grid:</b> {wind_config.grid_resolution}\u00b0 | "
            f"<b>Type:</b> {wind_config.installation}<br>"
            f"<b>Data Source:</b> {ds_labels.get(wind_config.data_source, wind_config.data_source)}<br>"
            f"<b>MCDA Method:</b> {mcda_config.method} | "
            f"<b>Criteria:</b> {', '.join(enabled_criteria)}"
        )
        self._btn_run.setEnabled(True)
        self._summary = None

    def set_input_provider(self, fn):
        """Callable returning the set_inputs() args tuple (consolidated layout).

        Invoked at Run time so the analysis uses the live sibling Criteria + the
        prior Domain/Config instead of a stale push.
        """
        self._input_provider = fn

    def _run_analysis(self):
        provider = getattr(self, "_input_provider", None)
        if provider is not None:
            args = provider()
            if not args or args[0] is None:
                QMessageBox.warning(
                    self,
                    tr("wizard_otec.domain_required_title"),
                    tr("wizard_otec.domain_required_msg"),
                )
                return
            self.set_inputs(*args)
        # Check CDS API credentials only for ERA5/atlite data source
        if self._wind_config.data_source == "era5_atlite":
            from pathlib import Path as _Path
            cdsapirc = _Path.home() / ".cdsapirc"
            if not cdsapirc.exists():
                QMessageBox.critical(
                    self,
                    tr("wizard_wind.cds_api_title"),
                    "ERA5 wind data download requires a Copernicus Climate Data Store "
                    "account.\n\n"
                    "1. Register at: https://cds.climate.copernicus.eu/\n"
                    "2. Go to your profile page and copy your API key\n"
                    "3. Create the file ~/.cdsapirc with:\n\n"
                    "   url: https://cds.climate.copernicus.eu/api\n"
                    "   key: YOUR_UID:YOUR_API_KEY\n\n"
                    "Then retry the analysis.",
                )
                return

        from esfex.visualization.workflows._qt_adapters import QtWindAnalyzer as WindAnalyzer

        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._log.clear()

        self._analyzer = WindAnalyzer(
            self._bounds,
            self._wind_config,
            self._mcda_config,
            self._transmission_lines,
            polygon=getattr(self, "_polygon", None),
        )
        self._analyzer.progress.connect(self._on_progress)
        self._analyzer.finished.connect(self._on_finished)
        self._analyzer.error.connect(self._on_error)
        self._analyzer.start()

    def _cancel_analysis(self):
        if self._analyzer:
            self._analyzer.cancel()
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._log.append(tr("wizard_wind.analysis_cancelled"))

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._log.append(msg)

    def _on_finished(self, summary):
        self._summary = summary
        self._progress.setValue(100)
        self._btn_cancel.setEnabled(False)
        self._log.append(
            f"\nAnalysis complete!\n"
            f"Total grid cells: {summary.total_cells}\n"
            f"Feasible cells: {summary.feasible_cells}\n"
            f"CF range: {summary.cf_min:.3f} \u2013 {summary.cf_max:.3f}\n"
            f"MCDA score range: {summary.mcda_score_min:.3f} \u2013 "
            f"{summary.mcda_score_max:.3f}"
        )
        if summary.computed_weights:
            self._log.append("\nComputed weights:")
            for name, w in summary.computed_weights.items():
                self._log.append(f"  {name}: {w:.3f}")
        self.analysisFinished.emit()

    def _on_error(self, msg: str):
        self._progress.setValue(0)
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._log.append(f"ERROR: {msg}")
        QMessageBox.critical(self, tr("wizard_wind.analysis_error_title"), msg)

    def get_summary(self):
        return self._summary

    def is_valid(self) -> bool:
        return self._summary is not None


# =====================================================================
# Step 5: Results & Development Zones
# =====================================================================


class WindResultsStep(QWidget):
    """Display wind assessment results and generate development zones."""

    def __init__(self, map_widget, model=None, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._model = model
        self._summary = None
        self._wind_config = None
        self._zones_gdf = None

        layout = QVBoxLayout(self)

        # Summary stats
        stats_group = QGroupBox(tr("wizard_wind.group_summary"))
        stats_form = QFormLayout(stats_group)

        self._lbl_total = QLabel("\u2014")
        self._lbl_feasible = QLabel("\u2014")
        self._lbl_cf_min = QLabel("\u2014")
        self._lbl_cf_avg = QLabel("\u2014")
        self._lbl_cf_max = QLabel("\u2014")
        self._lbl_mcda_range = QLabel("\u2014")
        self._lbl_capacity = QLabel("\u2014")

        stats_form.addRow(tr("wizard_wind.total_cells"), self._lbl_total)
        stats_form.addRow(tr("wizard_wind.feasible_cells"), self._lbl_feasible)
        stats_form.addRow(tr("wizard_wind.min_cf"), self._lbl_cf_min)
        stats_form.addRow(tr("wizard_wind.avg_cf"), self._lbl_cf_avg)
        stats_form.addRow(tr("wizard_wind.max_cf"), self._lbl_cf_max)
        stats_form.addRow(tr("wizard_wind.mcda_range"), self._lbl_mcda_range)
        stats_form.addRow(tr("wizard_wind.total_installable"), self._lbl_capacity)

        layout.addWidget(stats_group)

        # Computed weights
        self._weights_group = QGroupBox(tr("wizard_wind.group_weights"))
        self._weights_lay = QVBoxLayout(self._weights_group)
        self._weights_label = QLabel("")
        self._weights_label.setWordWrap(True)
        self._weights_lay.addWidget(self._weights_label)
        self._weights_group.setVisible(False)
        layout.addWidget(self._weights_group)

        # Map actions
        map_group = QGroupBox(tr("wizard_wind.group_map_viz"))
        map_lay = QHBoxLayout(map_group)

        self._btn_show_results = QPushButton(tr("wizard_wind.show_results"))
        self._btn_show_results.clicked.connect(self._show_results_on_map)
        self._btn_show_results.setEnabled(False)
        map_lay.addWidget(self._btn_show_results)

        self._btn_clear_results = QPushButton(tr("wizard_wind.clear_results"))
        self._btn_clear_results.clicked.connect(self._clear_results)
        self._btn_clear_results.setEnabled(False)
        map_lay.addWidget(self._btn_clear_results)

        layout.addWidget(map_group)

        # Development zones
        zones_group = QGroupBox(tr("wizard_wind.group_dev_zones"))
        zones_lay = QVBoxLayout(zones_group)

        self._btn_gen_zones = QPushButton(tr("wizard_wind.gen_zones"))
        self._btn_gen_zones.clicked.connect(self._generate_zones)
        self._btn_gen_zones.setEnabled(False)
        zones_lay.addWidget(self._btn_gen_zones)

        self._zones_info = QTextEdit()
        self._zones_info.setReadOnly(True)
        self._zones_info.setMaximumHeight(120)
        zones_lay.addWidget(self._zones_info)

        layout.addWidget(zones_group)

        # Export
        export_group = QGroupBox(tr("wizard_wind.group_export"))
        export_lay = QHBoxLayout(export_group)

        self._btn_export_csv = QPushButton(tr("wizard_wind.export_results_csv"))
        self._btn_export_csv.clicked.connect(self._export_csv)
        self._btn_export_csv.setEnabled(False)
        export_lay.addWidget(self._btn_export_csv)

        self._btn_export_zones = QPushButton(tr("wizard_wind.export_zones_geojson"))
        self._btn_export_zones.clicked.connect(self._export_zones_geojson)
        self._btn_export_zones.setEnabled(False)
        export_lay.addWidget(self._btn_export_zones)

        layout.addWidget(export_group)

        layout.addStretch()

    def set_results(self, summary, wind_config: WindConfig):
        self._summary = summary
        self._wind_config = wind_config

        self._lbl_total.setText(str(summary.total_cells))
        self._lbl_feasible.setText(str(summary.feasible_cells))

        if summary.total_cells > 0:
            self._lbl_cf_min.setText(f"{summary.cf_min:.3f} ({summary.cf_min*100:.1f}%)")
            self._lbl_cf_avg.setText(f"{summary.cf_avg:.3f} ({summary.cf_avg*100:.1f}%)")
            self._lbl_cf_max.setText(f"{summary.cf_max:.3f} ({summary.cf_max*100:.1f}%)")
            self._lbl_mcda_range.setText(
                f"{summary.mcda_score_min:.3f} \u2013 {summary.mcda_score_max:.3f}"
            )
            self._lbl_capacity.setText(f"{summary.total_capacity_mw:.1f} MW")
        else:
            for lbl in (
                self._lbl_cf_min, self._lbl_cf_avg, self._lbl_cf_max,
                self._lbl_mcda_range, self._lbl_capacity,
            ):
                lbl.setText("N/A")

        # Show computed weights
        if summary.computed_weights:
            lines = []
            for name, w in summary.computed_weights.items():
                bar = "\u2588" * int(w * 20)
                lines.append(f"<b>{name}:</b> {w:.3f} {bar}")
            self._weights_label.setText("<br>".join(lines))
            self._weights_group.setVisible(True)

        self._btn_show_results.setEnabled(summary.results_gdf is not None)
        self._btn_clear_results.setEnabled(True)
        self._btn_gen_zones.setEnabled(summary.feasible_cells > 0)
        self._btn_gen_zones.setText("Generate Development Zones")
        self._btn_export_csv.setEnabled(summary.results_gdf is not None)

    def _show_results_on_map(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        gdf = self._summary.results_gdf.copy()
        geojson_str = gdf.to_json()
        self._map_widget.show_wind_results(geojson_str)

    def _clear_results(self):
        self._map_widget.clear_wind_results()
        self._map_widget.clear_wind_dev_zones()
        self._map_widget.clear_wind_domain()

    def _generate_zones(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        if self._wind_config is None:
            return

        from windrex.regional.zones import (
            generate_development_zones as generate_wind_development_zones,
        )

        # Use median MCDA score as zone inclusion threshold
        gdf = self._summary.results_gdf
        feasible = gdf[gdf["capacity_factor"] >= self._wind_config.min_capacity_factor]
        if feasible.empty:
            self._zones_info.setText("No feasible sites for zone generation.")
            return

        min_mcda = float(feasible["mcda_score"].quantile(0.5))

        self._zones_gdf = generate_wind_development_zones(
            self._summary.results_gdf,
            min_cf=self._wind_config.min_capacity_factor,
            min_mcda_score=min_mcda,
            buffer_km=self._wind_config.zone_buffer_km,
            grid_resolution_deg=self._wind_config.grid_resolution,
            installation_type=self._wind_config.installation,
        )

        if self._zones_gdf.empty:
            self._zones_info.setText(
                "No development zones generated.\n"
                "Try lowering the capacity factor threshold."
            )
            self._btn_export_zones.setEnabled(False)
            return

        # Display zone info
        lines = [f"Generated {len(self._zones_gdf)} development zone(s):\n"]
        for _, zone in self._zones_gdf.iterrows():
            lines.append(
                f"  {zone['zone_id']}: "
                f"{zone['area_km2']:.1f} km\u00b2, "
                f"{zone['num_sites']} sites, "
                f"avg CF {zone['avg_cf']:.3f}, "
                f"avg MCDA {zone['avg_mcda']:.3f}, "
                f"{zone['total_capacity_mw']:.1f} MW"
            )
        self._zones_info.setText("\n".join(lines))

        self._btn_export_zones.setEnabled(True)

        # Auto-show on map and add to system
        self._show_zones_on_map()
        if self._model is not None:
            self._add_zones_to_system()

    def _show_zones_on_map(self):
        if self._zones_gdf is None or self._zones_gdf.empty:
            return
        geojson_str = self._zones_gdf.to_json()
        self._map_widget.show_wind_dev_zones(geojson_str)

    def _add_zones_to_system(self):
        """Convert generated zones into GuiDevelopmentZone elements."""
        if self._zones_gdf is None or self._zones_gdf.empty or self._model is None:
            return

        from esfex.visualization.data.gui_model import GeoPoint

        node_idx = (
            self._model.state.nodes[0].index
            if self._model.state.nodes
            else 0
        )

        added = 0
        for _, row in self._zones_gdf.iterrows():
            geom = row.geometry
            if geom.geom_type == "Polygon":
                coords = list(geom.exterior.coords)
            elif geom.geom_type == "MultiPolygon":
                largest = max(geom.geoms, key=lambda g: g.area)
                coords = list(largest.exterior.coords)
            else:
                continue

            polygon = [GeoPoint(lat=c[1], lng=c[0]) for c in coords]
            zone_id = row.get("zone_id", f"wind_zone_{added}")
            cap_mw = row.get("total_capacity_mw", None)

            try:
                self._model.add_zone(
                    name=zone_id,
                    technology="Wind",
                    polygon=polygon,
                    max_capacity_mw=cap_mw if cap_mw else None,
                    node=node_idx,
                )
                added += 1
            except Exception as exc:
                self._zones_info.append(f"Error adding {zone_id}: {exc}")

        # Clear temporary overlay since real zones are now in the model
        self._map_widget.clear_wind_dev_zones()

        if added > 0:
            self._zones_info.append(
                f"\n{added} wind development zone(s) added to the system."
            )
            self._btn_gen_zones.setEnabled(False)
            self._btn_gen_zones.setText(f"{added} zone(s) added")

    def _export_csv(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("wizard_wind.export_wind_title"),
            "wind_results.csv",
            "CSV Files (*.csv)",
        )
        if path:
            df = self._summary.results_gdf.drop(columns=["geometry"]).copy()
            df.to_csv(path, index=False)
            QMessageBox.information(
                self, "Exported", f"Results exported to:\n{path}"
            )

    def _export_zones_geojson(self):
        if self._zones_gdf is None or self._zones_gdf.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("wizard_wind.export_dev_zones_title"),
            "wind_dev_zones.geojson",
            "GeoJSON Files (*.geojson)",
        )
        if path:
            self._zones_gdf.to_file(path, driver="GeoJSON")
            QMessageBox.information(
                self, "Exported", f"Development zones exported to:\n{path}"
            )

    def is_valid(self) -> bool:
        return self._summary is not None
