"""Step widgets for the Solar Rooftop Analysis wizard.

Each step is a QWidget displayed in the wizard's QStackedWidget.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QCheckBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFormLayout,
    QMessageBox,
)

from esfex.visualization.i18n import tr

from esfex.visualization.workflows.solar_analysis import AnalysisConfig


# =====================================================================
# Step 1: Domain Definition
# =====================================================================


class DomainStep(QWidget):
    """Define the geographic bounding box for analysis."""

    domainChanged = Signal()  # emitted when bounds are set/updated

    def __init__(self, map_widget, parent=None, geo_assets_provider=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._geo_assets_provider = geo_assets_provider
        self._bounds: Optional[tuple[float, float, float, float]] = None
        self._polygon: list[tuple[float, float]] = []

        layout = QVBoxLayout(self)

        # Instructions
        layout.addWidget(QLabel(tr("wizard_solar.domain_instruction")))

        # Draw on map
        draw_group = QGroupBox(tr("wizard_common.draw_on_map"))
        draw_lay = QVBoxLayout(draw_group)
        self._btn_draw = QPushButton(tr("wizard_common.draw_rect"))
        self._btn_draw.clicked.connect(self._start_drawing)
        draw_lay.addWidget(self._btn_draw)
        self._draw_status = QLabel("")
        draw_lay.addWidget(self._draw_status)
        layout.addWidget(draw_group)

        from esfex.visualization.workflows._domain_geoasset_control import (
            GeoAssetDomainControl,
        )
        self._geo_domain_ctl = GeoAssetDomainControl(self._geo_assets_provider)
        self._geo_domain_ctl.domainPicked.connect(self._apply_domain_polygon)
        layout.addWidget(self._geo_domain_ctl)

        # Manual coordinates
        manual_group = QGroupBox(tr("wizard_common.manual_coords"))
        form = QFormLayout(manual_group)

        self._spin_south = self._coord_spin(-90, 90, tr("wizard_common.south_lat"))
        self._spin_north = self._coord_spin(-90, 90, tr("wizard_common.north_lat"))
        self._spin_west = self._coord_spin(-180, 180, tr("wizard_common.west_lng"))
        self._spin_east = self._coord_spin(-180, 180, tr("wizard_common.east_lng"))

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
        # Minimize the wizard so the user can see and interact with the map
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
        self._polygon = []   # drawn rectangle is bbox-only
        self._spin_south.setValue(south)
        self._spin_north.setValue(north)
        self._spin_west.setValue(west)
        self._spin_east.setValue(east)
        self._draw_status.setText(
            tr("wizard_solar.domain_coords",
               south=f"{south:.4f}", west=f"{west:.4f}",
               north=f"{north:.4f}", east=f"{east:.4f}")
        )
        self._btn_draw.setEnabled(True)
        self._btn_show.setEnabled(True)
        self._update_area()
        self._show_on_map()
        self._map_widget.disable_rectangle_draw()
        # Restore the wizard
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
            QMessageBox.warning(self, tr("wizard_common.invalid_domain_title"),
                                tr("wizard_solar.invalid_domain_msg"))
            return
        self._bounds = (s, w, n, e)
        self._polygon = []   # manual bbox
        self._btn_show.setEnabled(True)
        self._update_area()
        self.domainChanged.emit()

    def _apply_domain_polygon(self, poly):
        """Domain from an imported GeoAsset polygon (bbox fetch + polygon clip)."""
        from esfex.visualization.workflows.geo_domain import domain_bounds

        self._polygon = list(poly)
        s, w, n, e = domain_bounds(self._polygon)
        self._bounds = (s, w, n, e)
        self._spin_south.setValue(s)
        self._spin_north.setValue(n)
        self._spin_west.setValue(w)
        self._spin_east.setValue(e)
        self._draw_status.setText(
            f"Domain polygon: {len(self._polygon)} vertices")
        self._btn_show.setEnabled(True)
        self._update_area()
        try:
            self._map_widget.show_domain_polygon(self._polygon)
        except Exception:
            self._show_on_map()
        self.domainChanged.emit()

    def _show_on_map(self):
        if self._bounds:
            s, w, n, e = self._bounds
            self._map_widget.show_rooftop_domain(s, w, n, e)
            self._map_widget.fit_bounds(s, w, n, e)

    def _update_area(self):
        if not self._bounds:
            return
        s, w, n, e = self._bounds
        # Approximate area in km²
        lat_mid = (s + n) / 2.0
        lat_km = (n - s) * 111.32
        lon_km = (e - w) * 111.32 * math.cos(math.radians(lat_mid))
        area = lat_km * lon_km
        self._area_label.setText(tr("wizard_solar.approx_area", area=f"{area:.2f}"))

    def get_bounds(self) -> Optional[tuple[float, float, float, float]]:
        return self._bounds

    def get_polygon(self) -> list[tuple[float, float]]:
        return self._polygon

    def is_valid(self) -> bool:
        return self._bounds is not None


# =====================================================================
# Step 2: Data Sources
# =====================================================================


class DataSourcesStep(QWidget):
    """Select and fetch building footprint and solar resource data."""

    dataReady = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buildings_gdf = None
        self._solar_data = None
        self._building_fetcher = None
        self._solar_fetcher = None

        layout = QVBoxLayout(self)

        # ── Building footprints ──
        bldg_group = QGroupBox(tr("wizard_solar.group_bldg_footprints"))
        bldg_lay = QVBoxLayout(bldg_group)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel(tr("wizard_solar.source")))
        self._combo_bldg = QComboBox()
        self._combo_bldg.addItem(tr("wizard_solar.overture_maps"), "overture")
        self._combo_bldg.addItem(tr("wizard_solar.microsoft_ml"), "microsoft")
        self._combo_bldg.addItem(tr("wizard_solar.google_open"), "google")
        src_row.addWidget(self._combo_bldg, 1)
        bldg_lay.addLayout(src_row)

        fetch_row = QHBoxLayout()
        self._btn_fetch_bldg = QPushButton(tr("wizard_solar.fetch_buildings"))
        self._btn_fetch_bldg.clicked.connect(self._fetch_buildings)
        fetch_row.addWidget(self._btn_fetch_bldg)
        self._bldg_progress = QProgressBar()
        self._bldg_progress.setRange(0, 100)
        self._bldg_progress.setValue(0)
        fetch_row.addWidget(self._bldg_progress, 1)
        bldg_lay.addLayout(fetch_row)

        self._bldg_status = QLabel(tr("wizard_solar.no_data_loaded"))
        bldg_lay.addWidget(self._bldg_status)

        layout.addWidget(bldg_group)

        # ── Solar resource ──
        solar_group = QGroupBox(tr("wizard_solar.group_solar_resource"))
        solar_lay = QVBoxLayout(solar_group)

        src_row2 = QHBoxLayout()
        src_row2.addWidget(QLabel(tr("wizard_solar.source")))
        self._combo_solar = QComboBox()
        self._combo_solar.addItem(tr("wizard_solar.pvgis_no_key"), "pvgis")
        self._combo_solar.addItem(tr("wizard_solar.nsrdb_key"), "nsrdb")
        self._combo_solar.currentIndexChanged.connect(self._on_solar_source_changed)
        src_row2.addWidget(self._combo_solar, 1)
        solar_lay.addLayout(src_row2)

        # API key (hidden by default)
        self._api_key_row = QHBoxLayout()
        self._api_key_row_label = QLabel(tr("wizard_solar.api_key"))
        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText(tr("wizard_solar.api_key_placeholder"))
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_row.addWidget(self._api_key_row_label)
        self._api_key_row.addWidget(self._api_key_input, 1)
        solar_lay.addLayout(self._api_key_row)
        self._api_key_row_label.hide()
        self._api_key_input.hide()

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel(tr("wizard_solar.year")))
        self._spin_year = QSpinBox()
        self._spin_year.setRange(2005, 2024)
        self._spin_year.setValue(2022)
        year_row.addWidget(self._spin_year)
        year_row.addStretch()
        solar_lay.addLayout(year_row)

        fetch_row2 = QHBoxLayout()
        self._btn_fetch_solar = QPushButton(tr("wizard_solar.fetch_solar_data"))
        self._btn_fetch_solar.clicked.connect(self._fetch_solar)
        fetch_row2.addWidget(self._btn_fetch_solar)
        self._solar_progress = QProgressBar()
        self._solar_progress.setRange(0, 100)
        self._solar_progress.setValue(0)
        fetch_row2.addWidget(self._solar_progress, 1)
        solar_lay.addLayout(fetch_row2)

        self._solar_status = QLabel(tr("wizard_solar.no_data_loaded"))
        solar_lay.addWidget(self._solar_status)

        layout.addWidget(solar_group)
        layout.addStretch()

    def _on_solar_source_changed(self, index):
        is_nsrdb = self._combo_solar.currentData() == "nsrdb"
        self._api_key_row_label.setVisible(is_nsrdb)
        self._api_key_input.setVisible(is_nsrdb)

    def set_bounds(self, bounds: tuple[float, float, float, float]):
        """Store bounds for data fetching."""
        self._bounds = bounds

    def set_polygon(self, polygon):
        """Optional precise domain polygon; clips fetched buildings to it."""
        self._polygon = polygon or []

    def _fetch_buildings(self):
        if not hasattr(self, "_bounds") or self._bounds is None:
            QMessageBox.warning(self, tr("wizard_common.no_domain_title"), tr("wizard_common.no_domain_msg"))
            return

        from esfex.visualization.workflows.data_fetchers import BuildingFetcher

        source = self._combo_bldg.currentData()
        self._btn_fetch_bldg.setEnabled(False)
        self._bldg_status.setText(tr("wizard_solar.fetching"))
        self._bldg_progress.setValue(0)

        self._building_fetcher = BuildingFetcher(source, self._bounds)
        self._building_fetcher.progress.connect(self._on_bldg_progress)
        self._building_fetcher.finished.connect(self._on_bldg_finished)
        self._building_fetcher.error.connect(self._on_bldg_error)
        self._building_fetcher.start()

    def _on_bldg_progress(self, pct, msg):
        self._bldg_progress.setValue(pct)
        self._bldg_status.setText(msg)

    def _on_bldg_finished(self, gdf):
        # Clip to the precise domain polygon (drawn or imported GeoAsset) so
        # buildings outside the boundary — only inside the bbox — are dropped.
        poly = getattr(self, "_polygon", None)
        if poly and len(poly) >= 3 and gdf is not None and len(gdf) > 0:
            try:
                from esfex.visualization.workflows.geo_domain import (
                    domain_shapely,
                )
                gdf = gdf[gdf.geometry.intersects(domain_shapely(poly))]
            except Exception:
                pass
        self._buildings_gdf = gdf
        self._btn_fetch_bldg.setEnabled(True)
        n = len(gdf) if gdf is not None else 0
        n_height = 0
        if gdf is not None and "height" in gdf.columns:
            n_height = gdf["height"].notna().sum()
        self._bldg_status.setText(
            f"Loaded {n} buildings ({n_height} with height data)"
        )
        self._bldg_progress.setValue(100)
        self._check_ready()

    def _on_bldg_error(self, msg):
        self._btn_fetch_bldg.setEnabled(True)
        self._bldg_status.setText(f"Error: {msg}")
        self._bldg_progress.setValue(0)

    def _fetch_solar(self):
        if not hasattr(self, "_bounds") or self._bounds is None:
            QMessageBox.warning(self, tr("wizard_common.no_domain_title"), tr("wizard_common.no_domain_msg"))
            return

        from esfex.visualization.workflows.data_fetchers import SolarResourceFetcher

        source = self._combo_solar.currentData()
        s, w, n, e = self._bounds
        lat = (s + n) / 2.0
        lon = (w + e) / 2.0

        api_key = self._api_key_input.text().strip() if source == "nsrdb" else ""

        self._btn_fetch_solar.setEnabled(False)
        self._solar_status.setText(tr("wizard_solar.fetching"))
        self._solar_progress.setValue(0)

        self._solar_fetcher = SolarResourceFetcher(
            source, lat, lon, year=self._spin_year.value(), api_key=api_key
        )
        self._solar_fetcher.progress.connect(self._on_solar_progress)
        self._solar_fetcher.finished.connect(self._on_solar_finished)
        self._solar_fetcher.error.connect(self._on_solar_error)
        self._solar_fetcher.start()

    def _on_solar_progress(self, pct, msg):
        self._solar_progress.setValue(pct)
        self._solar_status.setText(msg)

    def _on_solar_finished(self, result):
        self._solar_data = result
        self._btn_fetch_solar.setEnabled(True)
        src = result.get("source", "Unknown")
        n = len(result.get("data", []))
        self._solar_status.setText(f"Loaded {n} hourly records from {src}")
        self._solar_progress.setValue(100)
        self._check_ready()

    def _on_solar_error(self, msg):
        self._btn_fetch_solar.setEnabled(True)
        self._solar_status.setText(f"Error: {msg}")
        self._solar_progress.setValue(0)

    def _check_ready(self):
        if self._buildings_gdf is not None and self._solar_data is not None:
            self.dataReady.emit()

    def get_buildings(self):
        return self._buildings_gdf

    def get_solar_data(self):
        return self._solar_data

    def is_valid(self) -> bool:
        return self._buildings_gdf is not None and self._solar_data is not None


# =====================================================================
# Step 3: Analysis Configuration
# =====================================================================


class ConfigStep(QWidget):
    """Configure PV panel specs, roof suitability, and shading parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # ── Panel specifications ──
        panel_group = QGroupBox(tr("wizard_solar.group_panel_specs"))
        pf = QFormLayout(panel_group)

        self._spin_efficiency = QDoubleSpinBox()
        self._spin_efficiency.setRange(0.05, 0.50)
        self._spin_efficiency.setValue(0.21)
        self._spin_efficiency.setSingleStep(0.01)
        self._spin_efficiency.setDecimals(2)
        pf.addRow(tr("wizard_solar.module_efficiency"), self._spin_efficiency)

        self._spin_power = QSpinBox()
        self._spin_power.setRange(100, 800)
        self._spin_power.setValue(400)
        self._spin_power.setSuffix(" W")
        pf.addRow(tr("wizard_solar.module_power"), self._spin_power)

        self._spin_area = QDoubleSpinBox()
        self._spin_area.setRange(0.5, 5.0)
        self._spin_area.setValue(2.0)
        self._spin_area.setSuffix(" m²")
        pf.addRow(tr("wizard_solar.module_area"), self._spin_area)

        self._spin_pr = QDoubleSpinBox()
        self._spin_pr.setRange(0.50, 0.99)
        self._spin_pr.setValue(0.80)
        self._spin_pr.setSingleStep(0.01)
        pf.addRow(tr("wizard_solar.performance_ratio"), self._spin_pr)

        self._spin_losses = QDoubleSpinBox()
        self._spin_losses.setRange(0.0, 0.50)
        self._spin_losses.setValue(0.14)
        self._spin_losses.setSingleStep(0.01)
        pf.addRow(tr("wizard_solar.system_losses"), self._spin_losses)

        layout.addWidget(panel_group)

        # ── Roof suitability ──
        roof_group = QGroupBox(tr("wizard_solar.group_roof_suitability"))
        rf = QFormLayout(roof_group)

        self._spin_suitable = QDoubleSpinBox()
        self._spin_suitable.setRange(0.05, 1.0)
        self._spin_suitable.setValue(0.30)
        self._spin_suitable.setSingleStep(0.05)
        rf.addRow(tr("wizard_solar.suitable_fraction"), self._spin_suitable)

        self._spin_min_area = QDoubleSpinBox()
        self._spin_min_area.setRange(1, 500)
        self._spin_min_area.setValue(20.0)
        self._spin_min_area.setSuffix(" m²")
        rf.addRow(tr("wizard_solar.min_building_area"), self._spin_min_area)

        self._spin_tilt = QDoubleSpinBox()
        self._spin_tilt.setRange(0, 60)
        self._spin_tilt.setValue(0)
        self._spin_tilt.setToolTip("0 = auto from latitude")
        self._spin_tilt.setSuffix("°")
        rf.addRow(tr("wizard_solar.default_tilt"), self._spin_tilt)

        self._spin_azimuth = QDoubleSpinBox()
        self._spin_azimuth.setRange(0, 360)
        self._spin_azimuth.setValue(180)
        self._spin_azimuth.setSuffix("°")
        rf.addRow(tr("wizard_solar.default_azimuth"), self._spin_azimuth)

        layout.addWidget(roof_group)

        # ── Shading ──
        shade_group = QGroupBox(tr("wizard_solar.group_shading"))
        sf = QFormLayout(shade_group)

        self._chk_shading = QCheckBox(tr("wizard_solar.enable_shading"))
        self._chk_shading.setChecked(True)
        sf.addRow(self._chk_shading)

        self._spin_radius = QDoubleSpinBox()
        self._spin_radius.setRange(10, 500)
        self._spin_radius.setValue(50)
        self._spin_radius.setSuffix(" m")
        sf.addRow(tr("wizard_solar.search_radius"), self._spin_radius)

        layout.addWidget(shade_group)
        layout.addStretch()

    def get_config(self) -> AnalysisConfig:
        return AnalysisConfig(
            module_efficiency=self._spin_efficiency.value(),
            module_power_w=float(self._spin_power.value()),
            module_area_m2=self._spin_area.value(),
            performance_ratio=self._spin_pr.value(),
            system_losses=self._spin_losses.value(),
            suitable_fraction=self._spin_suitable.value(),
            min_building_area_m2=self._spin_min_area.value(),
            default_tilt=self._spin_tilt.value(),
            default_azimuth=self._spin_azimuth.value(),
            enable_shading=self._chk_shading.isChecked(),
            shading_search_radius_m=self._spin_radius.value(),
        )

    def is_valid(self) -> bool:
        return True  # all fields have defaults


# =====================================================================
# Step 4: Run Analysis
# =====================================================================


class AnalysisStep(QWidget):
    """Run the analysis with progress feedback."""

    analysisFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analyzer = None
        self._summary = None

        layout = QVBoxLayout(self)

        # Summary of inputs
        self._summary_label = QLabel(tr("wizard_solar.config_summary"))
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # Controls
        btn_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_solar.run_analysis"))
        self._btn_run.clicked.connect(self._run_analysis)
        btn_row.addWidget(self._btn_run)

        self._btn_cancel = QPushButton(tr("wizard_solar.cancel_analysis"))
        self._btn_cancel.clicked.connect(self._cancel_analysis)
        self._btn_cancel.setEnabled(False)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(300)
        layout.addWidget(self._log, 1)

        layout.addStretch()

    def set_inputs(self, buildings_gdf, solar_data, config):
        self._buildings_gdf = buildings_gdf
        self._solar_data = solar_data
        self._config = config

        n = len(buildings_gdf) if buildings_gdf is not None else 0
        src = solar_data.get("source", "?") if solar_data else "?"
        self._summary_label.setText(
            f"Ready to analyze {n} buildings using solar data from {src}.\n"
            f"Panel: {config.module_power_w:.0f}W, eff={config.module_efficiency:.0%}, "
            f"PR={config.performance_ratio:.0%}, losses={config.system_losses:.0%}\n"
            f"Roof: {config.suitable_fraction:.0%} suitable, "
            f"min area={config.min_building_area_m2:.0f} m², "
            f"shading={'ON' if config.enable_shading else 'OFF'}"
        )

    def _run_analysis(self):
        from esfex.visualization.workflows.solar_analysis import SolarRooftopAnalyzer

        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._log.clear()
        self._log.append(tr("wizard_solar.starting_analysis"))

        self._analyzer = SolarRooftopAnalyzer(
            self._buildings_gdf, self._solar_data, self._config
        )
        self._analyzer.progress.connect(self._on_progress)
        self._analyzer.finished.connect(self._on_finished)
        self._analyzer.error.connect(self._on_error)
        self._analyzer.start()

    def _cancel_analysis(self):
        if self._analyzer:
            self._analyzer.cancel()
            self._log.append(tr("wizard_solar.cancelling"))
            self._btn_cancel.setEnabled(False)

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._log.append(msg)

    def _on_finished(self, summary):
        self._summary = summary
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress.setValue(100)
        self._log.append(
            f"\nAnalysis complete!\n"
            f"  Suitable buildings: {summary.suitable_buildings}/{summary.total_buildings}\n"
            f"  Total capacity: {summary.total_capacity_kwp:.1f} kWp\n"
            f"  Annual yield: {summary.total_annual_yield_mwh:.1f} MWh/yr\n"
            f"  Avg capacity factor: {summary.avg_capacity_factor:.1f}%"
        )
        self.analysisFinished.emit()

    def _on_error(self, msg):
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._log.append(f"\nERROR: {msg}")

    def get_summary(self):
        return self._summary

    def is_valid(self) -> bool:
        return self._summary is not None


# =====================================================================
# Step 5: Results
# =====================================================================


class ResultsStep(QWidget):
    """Display analysis results and provide export options."""

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._summary = None
        self._buildings_gdf = None

        layout = QVBoxLayout(self)

        # Summary stats
        stats_group = QGroupBox(tr("wizard_solar.group_summary"))
        self._stats_form = QFormLayout(stats_group)

        self._lbl_total = QLabel("-")
        self._lbl_suitable = QLabel("-")
        self._lbl_area = QLabel("-")
        self._lbl_capacity = QLabel("-")
        self._lbl_yield = QLabel("-")
        self._lbl_cf = QLabel("-")
        self._lbl_sy = QLabel("-")

        self._stats_form.addRow(tr("wizard_solar.total_buildings"), self._lbl_total)
        self._stats_form.addRow(tr("wizard_solar.suitable_buildings"), self._lbl_suitable)
        self._stats_form.addRow(tr("wizard_solar.total_roof_area"), self._lbl_area)
        self._stats_form.addRow(tr("wizard_solar.total_capacity"), self._lbl_capacity)
        self._stats_form.addRow(tr("wizard_solar.annual_yield"), self._lbl_yield)
        self._stats_form.addRow(tr("wizard_solar.avg_cf"), self._lbl_cf)
        self._stats_form.addRow(tr("wizard_solar.avg_sy"), self._lbl_sy)

        layout.addWidget(stats_group)

        # Actions
        btn_row = QHBoxLayout()

        self._btn_show_map = QPushButton(tr("wizard_solar.show_on_map"))
        self._btn_show_map.clicked.connect(self._show_on_map)
        btn_row.addWidget(self._btn_show_map)

        self._btn_export_geojson = QPushButton(tr("wizard_solar.export_geojson"))
        self._btn_export_geojson.clicked.connect(self._export_geojson)
        btn_row.addWidget(self._btn_export_geojson)

        self._btn_export_csv = QPushButton(tr("wizard_solar.export_csv"))
        self._btn_export_csv.clicked.connect(self._export_csv)
        btn_row.addWidget(self._btn_export_csv)

        layout.addLayout(btn_row)
        layout.addStretch()

    def set_results(self, summary, buildings_gdf):
        self._summary = summary
        self._buildings_gdf = buildings_gdf

        if summary is None:
            return

        self._lbl_total.setText(str(summary.total_buildings))
        self._lbl_suitable.setText(str(summary.suitable_buildings))
        self._lbl_area.setText(f"{summary.total_usable_area_m2:,.0f} m²")
        self._lbl_capacity.setText(f"{summary.total_capacity_kwp:,.1f} kWp")
        self._lbl_yield.setText(f"{summary.total_annual_yield_mwh:,.1f} MWh/yr")
        self._lbl_cf.setText(f"{summary.avg_capacity_factor:.1f}%")
        self._lbl_sy.setText(f"{summary.avg_specific_yield:,.0f} kWh/kWp/yr")

    def _show_on_map(self):
        if self._buildings_gdf is None or self._summary is None:
            return

        # Build GeoJSON with results
        gdf = self._buildings_gdf.copy()

        # Add per-building results as columns
        results_map = {}
        for br in self._summary.building_results:
            results_map[br.building_id] = br

        for idx in gdf.index:
            br = results_map.get(idx)
            if br:
                gdf.loc[idx, "capacity_kw"] = br.capacity_kw
                gdf.loc[idx, "annual_kwh"] = br.annual_kwh
                gdf.loc[idx, "specific_yield"] = br.specific_yield
                gdf.loc[idx, "usable_roof_area"] = br.usable_roof_area
                gdf.loc[idx, "shading_loss"] = br.shading_loss
                gdf.loc[idx, "suitable"] = br.suitable

        # Filter to suitable buildings for display
        suitable_gdf = gdf[gdf.get("suitable", False) == True]
        if suitable_gdf.empty:
            QMessageBox.information(self, tr("wizard_solar.no_suitable_title"), tr("wizard_solar.no_suitable_msg"))
            return

        # Convert to GeoJSON (ensure WGS84)
        if suitable_gdf.crs and suitable_gdf.crs.to_epsg() != 4326:
            suitable_gdf = suitable_gdf.to_crs("EPSG:4326")

        # Select only needed columns for GeoJSON
        export_cols = ["geometry", "capacity_kw", "annual_kwh",
                       "specific_yield", "usable_roof_area", "shading_loss"]
        existing_cols = [c for c in export_cols if c in suitable_gdf.columns]
        geojson_str = suitable_gdf[existing_cols].to_json()

        self._map_widget.show_rooftop_results(geojson_str)

    def _export_geojson(self):
        if self._buildings_gdf is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.export_geojson_title"), "rooftop_analysis.geojson", tr("wizard_solar.export_geojson_filter")
        )
        if not path:
            return

        gdf = self._prepare_export_gdf()
        gdf.to_file(path, driver="GeoJSON")
        QMessageBox.information(self, tr("wizard_solar.export_title"), tr("wizard_solar.exported_msg", path=path))

    def _export_csv(self):
        if self._summary is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, tr("wizard_solar.export_csv_title"), "rooftop_analysis.csv", tr("wizard_solar.export_csv_filter")
        )
        if not path:
            return

        import csv
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "building_id", "usable_roof_area_m2", "capacity_kw",
                "annual_kwh", "specific_yield_kwh_kwp",
                "shading_loss", "suitable"
            ])
            for br in self._summary.building_results:
                writer.writerow([
                    br.building_id, f"{br.usable_roof_area:.1f}",
                    f"{br.capacity_kw:.2f}", f"{br.annual_kwh:.1f}",
                    f"{br.specific_yield:.0f}", f"{br.shading_loss:.3f}",
                    br.suitable,
                ])

        QMessageBox.information(self, tr("wizard_solar.export_title"), tr("wizard_solar.exported_msg", path=path))

    def _prepare_export_gdf(self):
        gdf = self._buildings_gdf.copy()
        results_map = {br.building_id: br for br in self._summary.building_results}
        for idx in gdf.index:
            br = results_map.get(idx)
            if br:
                gdf.loc[idx, "capacity_kw"] = br.capacity_kw
                gdf.loc[idx, "annual_kwh"] = br.annual_kwh
                gdf.loc[idx, "specific_yield"] = br.specific_yield
                gdf.loc[idx, "usable_roof_area"] = br.usable_roof_area
                gdf.loc[idx, "shading_loss"] = br.shading_loss
                gdf.loc[idx, "suitable"] = br.suitable
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        return gdf

    def is_valid(self) -> bool:
        return self._summary is not None
