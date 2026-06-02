"""Step widgets for the Solar PV Potential Assessment wizard.

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

from esfex.visualization.i18n import tr
from solarex import (
    CriterionConfig,
    DEFAULT_LULC_SCORES,
    MCDAConfig,
    ModuleSpec,
    load_module_database,
)
from solarex.config import SolarConfig as SolarPVConfig


class _ModuleLoaderThread(QThread):
    """Load CEC module database on background thread to avoid blocking the UI."""

    finished = Signal(list)
    error = Signal(str)

    def run(self):
        try:
            modules = load_module_database()
            self.finished.emit(modules)
        except Exception as exc:
            self.error.emit(str(exc))


# =====================================================================
# Step 1: Domain Definition
# =====================================================================


class SolarPVDomainStep(QWidget):
    """Define the geographic bounding box for solar PV assessment."""

    domainChanged = Signal()

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._bounds: Optional[tuple[float, float, float, float]] = None

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                tr("wizard_solar_pv.domain_desc")
            )
        )

        # Draw on map
        draw_group = QGroupBox(tr("wizard_common.draw_on_map"))
        draw_lay = QVBoxLayout(draw_group)
        self._btn_draw = QPushButton(tr("wizard_common.draw_rect"))
        self._btn_draw.clicked.connect(self._start_drawing)
        draw_lay.addWidget(self._btn_draw)
        self._draw_status = QLabel("")
        draw_lay.addWidget(self._draw_status)
        layout.addWidget(draw_group)

        # Manual coordinates
        manual_group = QGroupBox(tr("wizard_common.manual_coords"))
        form = QFormLayout(manual_group)

        self._spin_south = self._coord_spin(-90, 90, "South latitude")
        self._spin_north = self._coord_spin(-90, 90, "North latitude")
        self._spin_west = self._coord_spin(-180, 180, "West longitude")
        self._spin_east = self._coord_spin(-180, 180, "East longitude")

        form.addRow(tr("wizard_common.south_lat"), self._spin_south)
        form.addRow(tr("wizard_common.north_lat"), self._spin_north)
        form.addRow(tr("wizard_common.west_lng"), self._spin_west)
        form.addRow(tr("wizard_common.east_lng"), self._spin_east)

        btn_row = QHBoxLayout()
        self._btn_apply = QPushButton(tr("wizard_common.apply_coords"))
        self._btn_apply.clicked.connect(self._apply_manual)
        btn_row.addWidget(self._btn_apply)

        self._btn_show = QPushButton(tr("wizard_common.show_on_map"))
        self._btn_show.clicked.connect(self._show_on_map)
        self._btn_show.setEnabled(False)
        btn_row.addWidget(self._btn_show)
        form.addRow(btn_row)

        layout.addWidget(manual_group)

        # Area info
        self._area_label = QLabel("")
        self._area_label.setStyleSheet("font-weight: bold; padding: 8px;")
        layout.addWidget(self._area_label)

        layout.addStretch()

        # Connect bridge signal
        bridge = self._map_widget.bridge
        bridge.rectangleDrawn.connect(self._on_rectangle_drawn)
        self._map_widget.install_draw_cancel_handler(self, self._btn_draw)

    def _coord_spin(self, min_val, max_val, tooltip):
        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(6)
        spin.setSingleStep(0.01)
        spin.setToolTip(tooltip)
        return spin

    def _start_drawing(self):
        self._draw_status.setText(tr("wizard_solar.draw_status"))
        self._btn_draw.setEnabled(False)
        wizard = self.window()
        if wizard:
            wizard.showMinimized()
        self._map_widget.enable_rectangle_draw()

    def _on_rectangle_drawn(self, bounds_json: str):
        data = json.loads(bounds_json)
        south = float(data["south"])
        west = float(data["west"])
        north = float(data["north"])
        east = float(data["east"])
        self._bounds = (south, west, north, east)
        self._spin_south.setValue(south)
        self._spin_north.setValue(north)
        self._spin_west.setValue(west)
        self._spin_east.setValue(east)
        self._draw_status.setText(
            f"Domain: ({south:.4f}, {west:.4f}) to ({north:.4f}, {east:.4f})"
        )
        self._btn_draw.setEnabled(True)
        self._btn_show.setEnabled(True)
        self._update_area()
        self._show_on_map()
        self._map_widget.disable_rectangle_draw()
        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()
        self.domainChanged.emit()

    def _apply_manual(self):
        s = self._spin_south.value()
        n = self._spin_north.value()
        w = self._spin_west.value()
        e = self._spin_east.value()
        if n <= s or e <= w:
            QMessageBox.warning(
                self,
                tr("wizard_common.invalid_domain_title"),
                tr("wizard_solar.invalid_domain_msg"),
            )
            return
        self._bounds = (s, w, n, e)
        self._btn_show.setEnabled(True)
        self._update_area()
        self.domainChanged.emit()

    def _show_on_map(self):
        if self._bounds:
            s, w, n, e = self._bounds
            self._map_widget.show_solar_pv_domain(s, w, n, e)

    def _update_area(self):
        if not self._bounds:
            return
        s, w, n, e = self._bounds
        lat_mid = (s + n) / 2.0
        lat_km = (n - s) * 111.32
        lon_km = (e - w) * 111.32 * math.cos(math.radians(lat_mid))
        area = lat_km * lon_km
        self._area_label.setText(f"Approximate area: {area:.2f} km\u00b2")

    def get_bounds(self) -> tuple[float, float, float, float]:
        return self._bounds  # type: ignore[return-value]

    def is_valid(self) -> bool:
        if self._bounds is None:
            QMessageBox.warning(
                self,
                tr("wizard_solar_pv.domain_required_title"),
                tr("wizard_solar_pv.domain_required_msg"),
            )
            return False
        return True


# =====================================================================
# Step 2: Solar PV Configuration (Module Database + Parameters)
# =====================================================================


class SolarPVConfigStep(QWidget):
    """Configure PV module (from CEC database) and assessment parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._modules: list[ModuleSpec] = []
        self._filtered_modules: list[ModuleSpec] = []
        self._selected_module: ModuleSpec | None = None

        layout = QVBoxLayout(self)

        # -- Module Selection --
        mod_group = QGroupBox(tr("wizard_solar_pv.group_module_sel"))
        mod_lay = QVBoxLayout(mod_group)

        # Filter bar row 1: Manufacturer + Technology
        filter_row1 = QHBoxLayout()

        filter_row1.addWidget(QLabel(tr("wizard_solar_pv.manufacturer")))
        self._combo_manufacturer = QComboBox()
        self._combo_manufacturer.addItem("All")
        self._combo_manufacturer.currentIndexChanged.connect(self._apply_filter)
        filter_row1.addWidget(self._combo_manufacturer)

        filter_row1.addWidget(QLabel(tr("wizard_solar_pv.technology")))
        self._combo_technology = QComboBox()
        self._combo_technology.addItems([
            "All", "Mono-c-Si", "Multi-c-Si", "CdTe", "CIGS", "a-Si", "Thin Film",
        ])
        self._combo_technology.currentIndexChanged.connect(self._apply_filter)
        filter_row1.addWidget(self._combo_technology)

        mod_lay.addLayout(filter_row1)

        # Filter bar row 2: Power range + Bifacial
        filter_row2 = QHBoxLayout()

        filter_row2.addWidget(QLabel(tr("wizard_solar_pv.min_power")))
        self._spin_min_power = QDoubleSpinBox()
        self._spin_min_power.setRange(0, 700)
        self._spin_min_power.setValue(0)
        self._spin_min_power.setSuffix(" W")
        self._spin_min_power.setDecimals(0)
        self._spin_min_power.valueChanged.connect(self._apply_filter)
        filter_row2.addWidget(self._spin_min_power)

        filter_row2.addWidget(QLabel(tr("wizard_solar_pv.max_power")))
        self._spin_max_power = QDoubleSpinBox()
        self._spin_max_power.setRange(0, 700)
        self._spin_max_power.setValue(700)
        self._spin_max_power.setSuffix(" W")
        self._spin_max_power.setDecimals(0)
        self._spin_max_power.valueChanged.connect(self._apply_filter)
        filter_row2.addWidget(self._spin_max_power)

        self._chk_bifacial = QCheckBox(tr("wizard_solar_pv.bifacial_only"))
        self._chk_bifacial.toggled.connect(self._apply_filter)
        filter_row2.addWidget(self._chk_bifacial)

        mod_lay.addLayout(filter_row2)

        # Module table
        self._mod_table = QTableWidget(0, 7)
        self._mod_table.setHorizontalHeaderLabels([
            "Manufacturer", "Model", "Technology",
            "STC (W)", "Eff (%)", "Area (m\u00b2)", "Bifacial",
        ])
        header = self._mod_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._mod_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows,
        )
        self._mod_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection,
        )
        self._mod_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers,
        )
        self._mod_table.currentCellChanged.connect(self._on_module_selected)
        mod_lay.addWidget(self._mod_table)

        # Module details panel
        self._details_label = QLabel(
            "<i>Select a module from the table above to see its details.</i>"
        )
        self._details_label.setWordWrap(True)
        self._details_label.setStyleSheet(
            "background-color: #2c2c2c; color: #e0e0e0; "
            "padding: 8px; border-radius: 4px;"
        )
        self._details_label.setMinimumHeight(80)
        mod_lay.addWidget(self._details_label)

        layout.addWidget(mod_group)

        # -- Orientation & Tracking --
        orient_group = QGroupBox(tr("wizard_solar_pv.group_orientation"))
        orient_form = QFormLayout(orient_group)

        self._combo_orient = QComboBox()
        self._combo_orient.addItems([
            tr("wizard_solar_pv.orient_latitude"),
            tr("wizard_solar_pv.orient_custom"),
        ])
        self._combo_orient.currentIndexChanged.connect(self._on_orient_changed)
        orient_form.addRow(tr("wizard_solar_pv.orientation"), self._combo_orient)

        self._spin_tilt = QDoubleSpinBox()
        self._spin_tilt.setRange(0, 90)
        self._spin_tilt.setValue(20)
        self._spin_tilt.setSuffix("\u00b0")
        self._spin_tilt.setEnabled(False)
        orient_form.addRow(tr("wizard_solar_pv.tilt"), self._spin_tilt)

        self._spin_azimuth = QDoubleSpinBox()
        self._spin_azimuth.setRange(0, 360)
        self._spin_azimuth.setValue(180)
        self._spin_azimuth.setSuffix("\u00b0")
        self._spin_azimuth.setEnabled(False)
        orient_form.addRow(tr("wizard_solar_pv.azimuth"), self._spin_azimuth)

        self._combo_tracking = QComboBox()
        self._combo_tracking.addItems([
            tr("wizard_solar_pv.tracking_none"),
            tr("wizard_solar_pv.tracking_h1"),
            tr("wizard_solar_pv.tracking_v1"),
            tr("wizard_solar_pv.tracking_dual"),
        ])
        orient_form.addRow(tr("wizard_solar_pv.tracking"), self._combo_tracking)

        layout.addWidget(orient_group)

        # -- Analysis Parameters --
        analysis_group = QGroupBox(tr("wizard_solar_pv.group_analysis_params"))
        analysis_form = QFormLayout(analysis_group)

        self._spin_year = QSpinBox()
        self._spin_year.setRange(1979, 2023)
        self._spin_year.setValue(2022)
        analysis_form.addRow(tr("wizard_solar_pv.analysis_year"), self._spin_year)

        self._spin_grid_res = QDoubleSpinBox()
        self._spin_grid_res.setRange(0.05, 2.0)
        self._spin_grid_res.setValue(0.25)
        self._spin_grid_res.setDecimals(2)
        self._spin_grid_res.setSuffix(" \u00b0")
        analysis_form.addRow(tr("wizard_solar_pv.grid_res"), self._spin_grid_res)

        self._combo_install = QComboBox()
        self._combo_install.addItems([
            tr("wizard_solar_pv.install_ground"),
            tr("wizard_solar_pv.install_floating"),
        ])
        analysis_form.addRow(
            tr("wizard_solar_pv.installation_type"), self._combo_install
        )

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
        self._spin_workers.setToolTip(tr("wizard_solar_pv.workers_tip"))
        analysis_form.addRow(tr("wizard_solar_pv.parallel_workers"), self._spin_workers)

        layout.addWidget(analysis_group)

        # -- Zone Criteria --
        zone_group = QGroupBox(tr("wizard_solar_pv.group_zone_criteria"))
        zone_form = QFormLayout(zone_group)

        self._spin_min_cf = QDoubleSpinBox()
        self._spin_min_cf.setRange(0.05, 0.80)
        self._spin_min_cf.setValue(0.15)
        self._spin_min_cf.setDecimals(2)
        self._spin_min_cf.setSingleStep(0.05)
        zone_form.addRow(tr("wizard_solar_pv.min_cf"), self._spin_min_cf)

        self._spin_buffer = QDoubleSpinBox()
        self._spin_buffer.setRange(1, 100)
        self._spin_buffer.setValue(5)
        self._spin_buffer.setDecimals(1)
        self._spin_buffer.setSuffix(" km")
        zone_form.addRow(tr("wizard_solar_pv.zone_buffer"), self._spin_buffer)

        layout.addWidget(zone_group)

        # Load module database (background thread)
        self._load_database()

    # ------------------------------------------------------------------
    # Database loading
    # ------------------------------------------------------------------

    def _load_database(self):
        """Load CEC module database on a background thread."""
        self._loader_thread = _ModuleLoaderThread(self)
        self._loader_thread.finished.connect(self._on_modules_loaded)
        self._loader_thread.error.connect(self._on_modules_error)
        self._loader_thread.start()

    def _on_modules_loaded(self, modules):
        """Populate table once module data arrives from background thread."""
        self._modules = modules
        self._populate_manufacturers()
        self._apply_filter()
        if self._mod_table.rowCount() > 0:
            self._mod_table.selectRow(0)

    def _on_modules_error(self, msg):
        """Handle module loading failure."""
        QMessageBox.warning(
            self,
            tr("wizard_solar_pv.module_db_title"),
            f"Could not load CEC module database:\n{msg}\n\n"
            "Make sure pvlib is installed: pip install pvlib",
        )

    def _populate_manufacturers(self):
        """Populate manufacturer filter combo from loaded modules."""
        manufacturers = sorted(
            {m.manufacturer for m in self._modules if m.manufacturer},
        )
        current = self._combo_manufacturer.currentText()
        self._combo_manufacturer.blockSignals(True)
        self._combo_manufacturer.clear()
        self._combo_manufacturer.addItem("All")
        for m in manufacturers:
            self._combo_manufacturer.addItem(m)
        idx = self._combo_manufacturer.findText(current)
        if idx >= 0:
            self._combo_manufacturer.setCurrentIndex(idx)
        self._combo_manufacturer.blockSignals(False)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self):
        """Filter module table based on manufacturer, technology, power, bifacial."""
        mfr = self._combo_manufacturer.currentText()
        tech = self._combo_technology.currentText()
        min_w = self._spin_min_power.value()
        max_w = self._spin_max_power.value()
        bifacial_only = self._chk_bifacial.isChecked()

        self._filtered_modules = [
            m for m in self._modules
            if (mfr == "All" or m.manufacturer == mfr)
            and (tech == "All" or m.technology == tech)
            and min_w <= m.stc_power_w <= max_w
            and (not bifacial_only or m.bifacial)
        ]

        # Limit display to 500 for performance (21k modules is too many rows)
        display = self._filtered_modules[:500]

        self._mod_table.setRowCount(len(display))
        for row, m in enumerate(display):
            self._mod_table.setItem(row, 0, QTableWidgetItem(m.manufacturer))
            self._mod_table.setItem(row, 1, QTableWidgetItem(m.name))
            self._mod_table.setItem(row, 2, QTableWidgetItem(m.technology))
            self._mod_table.setItem(
                row, 3, QTableWidgetItem(f"{m.stc_power_w:.0f}"),
            )
            self._mod_table.setItem(
                row, 4, QTableWidgetItem(f"{m.efficiency*100:.1f}"),
            )
            self._mod_table.setItem(
                row, 5,
                QTableWidgetItem(
                    f"{m.area_m2:.2f}" if m.area_m2 > 0 else "\u2014"
                ),
            )
            self._mod_table.setItem(
                row, 6, QTableWidgetItem("\u2713" if m.bifacial else ""),
            )

        # Show count info
        total = len(self._filtered_modules)
        shown = len(display)
        if total > shown:
            self._details_label.setText(
                f"<i>Showing {shown} of {total} matching modules. "
                f"Refine filters to narrow results.</i>"
            )
        elif display:
            self._mod_table.selectRow(0)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_module_selected(
        self, row: int, _col: int, _prev_row: int, _prev_col: int
    ):
        if row < 0 or row >= len(self._filtered_modules):
            self._selected_module = None
            self._details_label.setText(
                "<i>Select a module from the table above.</i>"
            )
            return

        m = self._filtered_modules[row]
        self._selected_module = m

        # Build details text
        lines = [
            f"<b>{m.manufacturer} {m.name}</b>",
            f"Rated power: <b>{m.stc_power_w:.0f} W</b> STC"
            + (f" / {m.ptc_power_w:.0f} W PTC" if m.ptc_power_w > 0 else ""),
            f"Technology: <b>{m.technology}</b>"
            + (f" | Bifacial: <b>Yes</b>" if m.bifacial else " | Bifacial: No"),
        ]

        if m.area_m2 > 0:
            lines.append(
                f"Area: <b>{m.area_m2:.2f} m\u00b2</b> | "
                f"Efficiency: <b>{m.efficiency*100:.1f}%</b>"
            )

        if m.length_m > 0 and m.width_m > 0:
            lines.append(
                f"Dimensions: {m.length_m:.2f}m \u00d7 {m.width_m:.2f}m | "
                f"{m.n_cells} cells"
            )

        lines.append("")
        lines.append("<b>Electrical at STC:</b>")
        lines.append(
            f"  Voc: {m.v_oc:.1f} V | Vmp: {m.v_mp:.1f} V"
        )
        lines.append(
            f"  Isc: {m.i_sc:.2f} A | Imp: {m.i_mp:.2f} A"
        )

        lines.append("")
        lines.append("<b>Temperature coefficients:</b>")
        lines.append(
            f"  Power: {m.gamma_pmax:.2f} %/\u00b0C | NOCT: {m.t_noct:.0f} \u00b0C"
        )

        self._details_label.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    # Orientation
    # ------------------------------------------------------------------

    def _on_orient_changed(self, index: int):
        custom = (index == 1)
        self._spin_tilt.setEnabled(custom)
        self._spin_azimuth.setEnabled(custom)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> SolarPVConfig:
        m = self._selected_module

        tracking_map = {0: "none", 1: "horizontal", 2: "vertical", 3: "dual"}
        install_map = {0: "ground", 1: "floating"}
        ds_map = {0: "open_meteo", 1: "nasa_power", 2: "era5_atlite"}

        return SolarPVConfig(
            module_key=m.key if m else "",
            module_efficiency=m.efficiency if m else 0.20,
            module_gamma_pmax=m.gamma_pmax if m else -0.40,
            module_stc_w=m.stc_power_w if m else 400.0,
            module_t_noct=m.t_noct if m else 45.0,
            orientation="latitude_optimal" if self._combo_orient.currentIndex() == 0 else "custom",
            tilt=self._spin_tilt.value(),
            azimuth=self._spin_azimuth.value(),
            tracking=tracking_map.get(
                self._combo_tracking.currentIndex(), "none",
            ),
            installation=install_map.get(
                self._combo_install.currentIndex(), "ground",
            ),
            year=self._spin_year.value(),
            grid_resolution=self._spin_grid_res.value(),
            min_capacity_factor=self._spin_min_cf.value(),
            zone_buffer_km=self._spin_buffer.value(),
            module_capacity_kw=m.stc_power_w / 1000.0 if m else 0.4,
            data_source=ds_map.get(
                self._combo_data_source.currentIndex(), "open_meteo",
            ),
            parallel_workers=self._spin_workers.value(),
        )

    def get_module_spec(self) -> ModuleSpec | None:
        """Return the currently selected module spec."""
        return self._selected_module

    def is_valid(self) -> bool:
        if self._selected_module is None:
            QMessageBox.warning(
                self,
                tr("wizard_solar_pv.module_required_title"),
                tr("wizard_solar_pv.module_required_msg"),
            )
            return False
        return True


# =====================================================================
# Step 3: MCDA Criteria Configuration
# =====================================================================

_CRITERIA_DEFS = [
    ("capacity_factor", "Solar Capacity Factor", "maximize", 0.40),
    ("slope", "Terrain Slope", "minimize", 0.20),
    ("elevation", "Elevation", "minimize", 0.05),
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


class SolarPVCriteriaStep(QWidget):
    """Configure MCDA criteria and weighting method for solar PV."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(tr("wizard_solar_pv.criteria_desc"))
        )

        # Weighting method
        method_group = QGroupBox(tr("wizard_solar_pv.group_weighting"))
        method_form = QFormLayout(method_group)

        self._combo_method = QComboBox()
        self._combo_method.addItems([
            tr("wizard_solar_pv.method_manual"),
            tr("wizard_solar_pv.method_entropy"),
            tr("wizard_solar_pv.method_pca"),
        ])
        self._combo_method.currentIndexChanged.connect(self._on_method_changed)
        method_form.addRow(
            tr("wizard_solar_pv.weighting_method"), self._combo_method
        )

        self._method_info = QLabel(
            "Manually assign weights to each criterion. "
            "Weights will be normalized to sum to 1."
        )
        self._method_info.setWordWrap(True)
        self._method_info.setStyleSheet("color: #888; font-style: italic;")
        method_form.addRow(self._method_info)

        layout.addWidget(method_group)

        # Criteria table
        criteria_group = QGroupBox(tr("wizard_solar_pv.group_criteria"))
        criteria_lay = QVBoxLayout(criteria_group)

        self._criteria_table = QTableWidget(len(_CRITERIA_DEFS), 4)
        self._criteria_table.setHorizontalHeaderLabels([
            "Enabled", "Criterion", "Direction", "Weight",
        ])
        crit_header = self._criteria_table.horizontalHeader()
        crit_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        crit_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        crit_header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        crit_header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )

        for row, (key, label, direction, default_weight) in enumerate(
            _CRITERIA_DEFS
        ):
            chk = QCheckBox()
            chk.setChecked(True)
            self._criteria_table.setCellWidget(row, 0, chk)

            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._criteria_table.setItem(row, 1, item)

            item_dir = QTableWidgetItem(direction.capitalize())
            item_dir.setFlags(item_dir.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._criteria_table.setItem(row, 2, item_dir)

            weight_spin = QDoubleSpinBox()
            weight_spin.setRange(0.0, 1.0)
            weight_spin.setDecimals(2)
            weight_spin.setSingleStep(0.05)
            weight_spin.setValue(default_weight)
            self._criteria_table.setCellWidget(row, 3, weight_spin)

        criteria_lay.addWidget(self._criteria_table)
        layout.addWidget(criteria_group)

        # LULC scoring table (collapsible)
        self._lulc_check = QCheckBox(tr("wizard_solar_pv.customize_lulc"))
        self._lulc_check.toggled.connect(self._toggle_lulc)
        layout.addWidget(self._lulc_check)

        self._lulc_group = QGroupBox(tr("wizard_solar_pv.group_lulc_scores"))
        lulc_lay = QVBoxLayout(self._lulc_group)

        self._lulc_table = QTableWidget(len(_LULC_CLASS_NAMES), 3)
        self._lulc_table.setHorizontalHeaderLabels([
            "Code", "Land Cover Class", "Suitability (0-1)",
        ])
        lulc_header = self._lulc_table.horizontalHeader()
        lulc_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        lulc_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lulc_header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )

        for row, (code, name) in enumerate(_LULC_CLASS_NAMES.items()):
            code_item = QTableWidgetItem(str(code))
            code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._lulc_table.setItem(row, 0, code_item)

            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._lulc_table.setItem(row, 1, name_item)

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
        descriptions = [
            "Manually assign weights to each criterion. "
            "Weights will be normalized to sum to 1.",
            "Weights computed automatically from data using Shannon entropy. "
            "Criteria with more variation get higher weights.",
            "Weights derived from first principal component loadings. "
            "Criteria that explain most variance get higher weights.",
        ]
        self._method_info.setText(descriptions[index])

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
        any_enabled = False
        for row in range(self._criteria_table.rowCount()):
            chk = self._criteria_table.cellWidget(row, 0)
            if isinstance(chk, QCheckBox) and chk.isChecked():
                any_enabled = True
                break
        if not any_enabled:
            QMessageBox.warning(
                self,
                tr("wizard_solar_pv.no_criteria_title"),
                tr("wizard_solar_pv.no_criteria_msg"),
            )
            return False
        return True


# =====================================================================
# Step 4: Analysis
# =====================================================================


class SolarPVAnalysisStep(QWidget):
    """Run solar PV resource assessment in background."""

    analysisFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analyzer = None
        self._summary = None

        layout = QVBoxLayout(self)

        # Input summary
        self._summary_label = QLabel(tr("wizard_solar_pv.config_summary"))
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # Run button
        btn_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_solar_pv.run_analysis"))
        self._btn_run.clicked.connect(self._run_analysis)
        self._btn_run.setEnabled(False)
        btn_row.addWidget(self._btn_run)

        self._btn_cancel = QPushButton(tr("wizard_solar_pv.cancel_analysis"))
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
        solar_config: SolarPVConfig,
        mcda_config: MCDAConfig,
        transmission_lines: list | None = None,
    ):
        self._bounds = bounds
        self._solar_config = solar_config
        self._mcda_config = mcda_config
        self._transmission_lines = transmission_lines or []

        s, w, n, e = bounds
        enabled_criteria = [
            name for name, c in mcda_config.criteria.items() if c.enabled
        ]

        orient_desc = solar_config.orientation
        if orient_desc == "custom":
            orient_desc = (
                f"tilt={solar_config.tilt}\u00b0, "
                f"azimuth={solar_config.azimuth}\u00b0"
            )

        ds_labels = {
            "open_meteo": "Open-Meteo (ERA5)",
            "nasa_power": "NASA POWER (MERRA-2)",
            "era5_atlite": "ERA5 via atlite",
        }
        self._summary_label.setText(
            f"<b>Domain:</b> ({s:.4f}, {w:.4f}) to ({n:.4f}, {e:.4f})<br>"
            f"<b>Module:</b> {solar_config.module_key.split('__')[-1].replace('_', ' ') if solar_config.module_key else 'N/A'} | "
            f"<b>Efficiency:</b> {solar_config.module_efficiency*100:.1f}%<br>"
            f"<b>Orientation:</b> {orient_desc} | "
            f"<b>Tracking:</b> {solar_config.tracking}<br>"
            f"<b>Year:</b> {solar_config.year} | "
            f"<b>Grid:</b> {solar_config.grid_resolution}\u00b0 | "
            f"<b>Type:</b> {solar_config.installation}<br>"
            f"<b>Data Source:</b> {ds_labels.get(solar_config.data_source, solar_config.data_source)}<br>"
            f"<b>MCDA Method:</b> {mcda_config.method} | "
            f"<b>Criteria:</b> {', '.join(enabled_criteria)}"
        )
        self._btn_run.setEnabled(True)
        self._summary = None

    def _run_analysis(self):
        # Check CDS API credentials only for ERA5/atlite data source
        if self._solar_config.data_source == "era5_atlite":
            from pathlib import Path as _Path
            cdsapirc = _Path.home() / ".cdsapirc"
            if not cdsapirc.exists():
                QMessageBox.critical(
                    self,
                    tr("wizard_solar_pv.cds_api_title"),
                    "ERA5 irradiance data download requires a Copernicus Climate "
                    "Data Store account.\n\n"
                    "1. Register at: https://cds.climate.copernicus.eu/\n"
                    "2. Go to your profile page and copy your API key\n"
                    "3. Create the file ~/.cdsapirc with:\n\n"
                    "   url: https://cds.climate.copernicus.eu/api\n"
                    "   key: YOUR_UID:YOUR_API_KEY\n\n"
                    "Then retry the analysis.",
                )
                return

        from esfex.visualization.workflows._qt_adapters import (
            QtSolarPVAnalyzer as SolarPVAnalyzer,
        )

        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._log.clear()

        self._analyzer = SolarPVAnalyzer(
            self._bounds,
            self._solar_config,
            self._mcda_config,
            self._transmission_lines,
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
        self._log.append(tr("wizard_solar_pv.analysis_cancelled"))

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
            f"Avg GHI: {summary.ghi_avg:.0f} kWh/m\u00b2/yr\n"
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
        QMessageBox.critical(
            self, tr("wizard_solar_pv.analysis_error_title"), msg
        )

    def get_summary(self):
        return self._summary

    def is_valid(self) -> bool:
        return self._summary is not None


# =====================================================================
# Step 5: Results & Development Zones
# =====================================================================


class SolarPVResultsStep(QWidget):
    """Display solar PV assessment results and generate development zones."""

    def __init__(self, map_widget, model=None, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._model = model
        self._summary = None
        self._solar_config = None
        self._zones_gdf = None

        layout = QVBoxLayout(self)

        # Summary stats
        stats_group = QGroupBox(tr("wizard_solar_pv.group_summary"))
        stats_form = QFormLayout(stats_group)

        self._lbl_total = QLabel("\u2014")
        self._lbl_feasible = QLabel("\u2014")
        self._lbl_cf_min = QLabel("\u2014")
        self._lbl_cf_avg = QLabel("\u2014")
        self._lbl_cf_max = QLabel("\u2014")
        self._lbl_ghi = QLabel("\u2014")
        self._lbl_mcda_range = QLabel("\u2014")
        self._lbl_capacity = QLabel("\u2014")

        stats_form.addRow(tr("wizard_solar_pv.total_cells"), self._lbl_total)
        stats_form.addRow(
            tr("wizard_solar_pv.feasible_cells"), self._lbl_feasible
        )
        stats_form.addRow(tr("wizard_solar_pv.min_cf"), self._lbl_cf_min)
        stats_form.addRow(tr("wizard_solar_pv.avg_cf"), self._lbl_cf_avg)
        stats_form.addRow(tr("wizard_solar_pv.max_cf"), self._lbl_cf_max)
        stats_form.addRow(tr("wizard_solar_pv.avg_ghi"), self._lbl_ghi)
        stats_form.addRow(
            tr("wizard_solar_pv.mcda_range"), self._lbl_mcda_range
        )
        stats_form.addRow(
            tr("wizard_solar_pv.total_installable"), self._lbl_capacity
        )

        layout.addWidget(stats_group)

        # Computed weights
        self._weights_group = QGroupBox(tr("wizard_solar_pv.group_weights"))
        self._weights_lay = QVBoxLayout(self._weights_group)
        self._weights_label = QLabel("")
        self._weights_label.setWordWrap(True)
        self._weights_lay.addWidget(self._weights_label)
        self._weights_group.setVisible(False)
        layout.addWidget(self._weights_group)

        # Map actions
        map_group = QGroupBox(tr("wizard_solar_pv.group_map_viz"))
        map_lay = QHBoxLayout(map_group)

        self._btn_show_results = QPushButton(
            tr("wizard_solar_pv.show_results")
        )
        self._btn_show_results.clicked.connect(self._show_results_on_map)
        self._btn_show_results.setEnabled(False)
        map_lay.addWidget(self._btn_show_results)

        self._btn_clear_results = QPushButton(
            tr("wizard_solar_pv.clear_results")
        )
        self._btn_clear_results.clicked.connect(self._clear_results)
        self._btn_clear_results.setEnabled(False)
        map_lay.addWidget(self._btn_clear_results)

        layout.addWidget(map_group)

        # Development zones
        zones_group = QGroupBox(tr("wizard_solar_pv.group_dev_zones"))
        zones_lay = QVBoxLayout(zones_group)

        self._btn_gen_zones = QPushButton(tr("wizard_solar_pv.gen_zones"))
        self._btn_gen_zones.clicked.connect(self._generate_zones)
        self._btn_gen_zones.setEnabled(False)
        zones_lay.addWidget(self._btn_gen_zones)

        self._zones_info = QTextEdit()
        self._zones_info.setReadOnly(True)
        self._zones_info.setMaximumHeight(120)
        zones_lay.addWidget(self._zones_info)

        layout.addWidget(zones_group)

        # Export
        export_group = QGroupBox(tr("wizard_solar_pv.group_export"))
        export_lay = QHBoxLayout(export_group)

        self._btn_export_csv = QPushButton(
            tr("wizard_solar_pv.export_results_csv")
        )
        self._btn_export_csv.clicked.connect(self._export_csv)
        self._btn_export_csv.setEnabled(False)
        export_lay.addWidget(self._btn_export_csv)

        self._btn_export_zones = QPushButton(
            tr("wizard_solar_pv.export_zones_geojson")
        )
        self._btn_export_zones.clicked.connect(self._export_zones_geojson)
        self._btn_export_zones.setEnabled(False)
        export_lay.addWidget(self._btn_export_zones)

        layout.addWidget(export_group)

        layout.addStretch()

    def set_results(self, summary, solar_config: SolarPVConfig):
        self._summary = summary
        self._solar_config = solar_config

        self._lbl_total.setText(str(summary.total_cells))
        self._lbl_feasible.setText(str(summary.feasible_cells))

        if summary.total_cells > 0:
            self._lbl_cf_min.setText(
                f"{summary.cf_min:.3f} ({summary.cf_min*100:.1f}%)"
            )
            self._lbl_cf_avg.setText(
                f"{summary.cf_avg:.3f} ({summary.cf_avg*100:.1f}%)"
            )
            self._lbl_cf_max.setText(
                f"{summary.cf_max:.3f} ({summary.cf_max*100:.1f}%)"
            )
            self._lbl_ghi.setText(
                f"{summary.ghi_avg:.0f} kWh/m\u00b2/yr"
            )
            self._lbl_mcda_range.setText(
                f"{summary.mcda_score_min:.3f} \u2013 "
                f"{summary.mcda_score_max:.3f}"
            )
            self._lbl_capacity.setText(
                f"{summary.total_capacity_mw:.1f} MW"
            )
        else:
            for lbl in (
                self._lbl_cf_min, self._lbl_cf_avg, self._lbl_cf_max,
                self._lbl_ghi, self._lbl_mcda_range, self._lbl_capacity,
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

        self._btn_show_results.setEnabled(
            summary.results_gdf is not None
        )
        self._btn_clear_results.setEnabled(True)
        self._btn_gen_zones.setEnabled(summary.feasible_cells > 0)
        self._btn_gen_zones.setText(
            tr("wizard_solar_pv.gen_zones")
        )
        self._btn_export_csv.setEnabled(
            summary.results_gdf is not None
        )

    def _show_results_on_map(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        gdf = self._summary.results_gdf.copy()
        geojson_str = gdf.to_json()
        self._map_widget.show_solar_pv_results(geojson_str)

    def _clear_results(self):
        self._map_widget.clear_solar_pv_results()
        self._map_widget.clear_solar_pv_dev_zones()
        self._map_widget.clear_solar_pv_domain()

    def _generate_zones(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        if self._solar_config is None:
            return

        from solarex.regional.zones import (
            generate_development_zones as generate_solar_pv_development_zones,
        )

        gdf = self._summary.results_gdf
        feasible = gdf[
            gdf["capacity_factor"] >= self._solar_config.min_capacity_factor
        ]
        if feasible.empty:
            self._zones_info.setText(
                "No feasible sites for zone generation."
            )
            return

        min_mcda = float(feasible["mcda_score"].quantile(0.5))

        self._zones_gdf = generate_solar_pv_development_zones(
            self._summary.results_gdf,
            min_cf=self._solar_config.min_capacity_factor,
            min_mcda_score=min_mcda,
            buffer_km=self._solar_config.zone_buffer_km,
            grid_resolution_deg=self._solar_config.grid_resolution,
            installation_type=self._solar_config.installation,
        )

        if self._zones_gdf.empty:
            self._zones_info.setText(
                "No development zones generated.\n"
                "Try lowering the capacity factor threshold."
            )
            self._btn_export_zones.setEnabled(False)
            return

        # Display zone info
        lines = [
            f"Generated {len(self._zones_gdf)} development zone(s):\n"
        ]
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
        self._map_widget.show_solar_pv_dev_zones(geojson_str)

    def _add_zones_to_system(self):
        """Convert generated zones into GuiDevelopmentZone elements."""
        if (
            self._zones_gdf is None
            or self._zones_gdf.empty
            or self._model is None
        ):
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
            zone_id = row.get("zone_id", f"solar_pv_zone_{added}")
            cap_mw = row.get("total_capacity_mw", None)

            try:
                self._model.add_zone(
                    name=zone_id,
                    technology="Solar",
                    polygon=polygon,
                    max_capacity_mw=cap_mw if cap_mw else None,
                    node=node_idx,
                )
                added += 1
            except Exception as exc:
                self._zones_info.append(
                    f"Error adding {zone_id}: {exc}"
                )

        # Clear temporary overlay since real zones are now in the model
        self._map_widget.clear_solar_pv_dev_zones()

        if added > 0:
            self._zones_info.append(
                f"\n{added} solar PV development zone(s) added to the system."
            )
            self._btn_gen_zones.setEnabled(False)
            self._btn_gen_zones.setText(f"{added} zone(s) added")

    def _export_csv(self):
        if self._summary is None or self._summary.results_gdf is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("wizard_solar_pv.export_solar_title"),
            "solar_pv_results.csv",
            "CSV Files (*.csv)",
        )
        if path:
            df = self._summary.results_gdf.drop(
                columns=["geometry"]
            ).copy()
            df.to_csv(path, index=False)
            QMessageBox.information(
                self, "Exported", f"Results exported to:\n{path}"
            )

    def _export_zones_geojson(self):
        if self._zones_gdf is None or self._zones_gdf.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("wizard_solar_pv.export_dev_zones_title"),
            "solar_pv_dev_zones.geojson",
            "GeoJSON Files (*.geojson)",
        )
        if path:
            self._zones_gdf.to_file(path, driver="GeoJSON")
            QMessageBox.information(
                self, "Exported", f"Development zones exported to:\n{path}"
            )

    def is_valid(self) -> bool:
        return self._summary is not None
