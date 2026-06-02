"""Step widgets for the Grid Builder wizard.

Each step is a QWidget displayed in the wizard's QStackedWidget.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
from collections import deque
from pathlib import Path

import numpy as np
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

logger = logging.getLogger(__name__)


# =====================================================================
# Step 1: Region & Fetch (combined domain + sources + fetch)
# =====================================================================




class GridMappingSourceFetchStep(QWidget):
    """Define region, configure data sources, and fetch in one step."""

    fetchFinished = Signal()  # all fetchers done

    def __init__(self, map_widget=None, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._features: list = []
        self._fetchers: list = []
        self._pending: int = 0
        self._errors: list[str] = []
        self._polygon: list[tuple[float, float]] = []
        self._bounds: Optional[tuple[float, float, float, float]] = None

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        layout.addWidget(QLabel(
            "<b>Step 1: Region & Fetch</b><br>"
            "Draw a polygon on the map, configure data sources, "
            "then download grid data for the selected region."
        ))

        # ── Region ──
        region_group = QGroupBox("Region")
        region_lay = QVBoxLayout(region_group)
        region_lay.addWidget(QLabel(
            "Draw a polygon on the map to define the extraction area. "
            "Click vertices to form the boundary, then click the first "
            "vertex to close."
        ))
        draw_row = QHBoxLayout()
        self._btn_draw = QPushButton("Draw Polygon on Map")
        self._btn_draw.setStyleSheet("font-size: 11px; padding: 6px 16px;")
        self._btn_draw.setMinimumWidth(180)
        self._btn_draw.clicked.connect(self._start_drawing)
        draw_row.addWidget(self._btn_draw)
        self._draw_status = QLabel("")
        self._draw_status.setWordWrap(True)
        draw_row.addWidget(self._draw_status, 1)
        region_lay.addLayout(draw_row)

        self._area_label = QLabel("")
        self._area_label.setStyleSheet("font-weight: bold;")
        region_lay.addWidget(self._area_label)

        layout.addWidget(region_group)

        # ── Data Sources ──
        src_group = QGroupBox("Data Sources")
        src_lay = QVBoxLayout(src_group)

        self._chk_osm = QCheckBox("OpenStreetMap (Overpass API)")
        self._chk_osm.setChecked(True)
        self._chk_osm.setToolTip(
            "Substations, generators, transmission lines, transformers, "
            "converters, and storage from OSM."
        )
        src_lay.addWidget(self._chk_osm)

        self._chk_wri = QCheckBox("WRI Global Power Plant Database")
        self._chk_wri.setChecked(False)
        self._chk_wri.setToolTip(
            "~30,000 power plants worldwide with capacity, fuel type, "
            "and location."
        )
        src_lay.addWidget(self._chk_wri)

        self._chk_gem = QCheckBox("GEM Global Power Plants (2025)")
        self._chk_gem.setChecked(True)
        self._chk_gem.setToolTip(
            "Global Energy Monitor power plant database (Feb 2025). "
            "More recent than WRI (2021) with similar coverage."
        )
        src_lay.addWidget(self._chk_gem)

        self._chk_gridfinder = QCheckBox("GridFinder (Predicted Grid Routes)")
        self._chk_gridfinder.setChecked(False)
        self._chk_gridfinder.setToolTip(
            "Predicted transmission/distribution line routes from satellite "
            "imagery. Useful for regions with sparse OSM data."
        )
        src_lay.addWidget(self._chk_gridfinder)

        layout.addWidget(src_group)

        # ── Settings (Filters + Element Types + Bus Strategy in 4 cols) ──
        settings_group = QGroupBox("Settings")
        settings_grid = QGridLayout(settings_group)
        for c in range(4):
            settings_grid.setColumnStretch(c, 1)

        _hdr_style = "font-weight: bold; padding-bottom: 2px;"

        # Column 1 — Filters
        col_filters_hdr = QLabel("Filters")
        col_filters_hdr.setStyleSheet(_hdr_style)
        settings_grid.addWidget(col_filters_hdr, 0, 0)

        filter_widget = QWidget()
        filter_form = QFormLayout(filter_widget)
        filter_form.setContentsMargins(0, 0, 0, 0)

        self._spin_min_voltage = QSpinBox()
        self._spin_min_voltage.setRange(10, 750)
        self._spin_min_voltage.setValue(110)
        self._spin_min_voltage.setSuffix(" kV")
        self._spin_min_voltage.setToolTip(
            "Minimum voltage for substations and lines. "
            "110 kV = high-voltage transmission. "
            "33 kV = includes sub-transmission."
        )
        filter_form.addRow("Min voltage:", self._spin_min_voltage)

        self._spin_min_capacity = QDoubleSpinBox()
        self._spin_min_capacity.setRange(0.0, 10000.0)
        self._spin_min_capacity.setValue(1.0)
        self._spin_min_capacity.setDecimals(1)
        self._spin_min_capacity.setSuffix(" MW")
        self._spin_min_capacity.setToolTip(
            "Minimum generator capacity. Set to 0 to include all."
        )
        filter_form.addRow("Min gen capacity:", self._spin_min_capacity)

        self._spin_snap = QDoubleSpinBox()
        self._spin_snap.setRange(0.1, 100.0)
        self._spin_snap.setValue(5.0)
        self._spin_snap.setDecimals(1)
        self._spin_snap.setSuffix(" km")
        self._spin_snap.setToolTip(
            "Distance threshold for snapping new elements to existing buses."
        )
        filter_form.addRow("Bus snap:", self._spin_snap)
        settings_grid.addWidget(filter_widget, 1, 0)

        # Columns 2–3 — Element Types (4+4 split)
        col_elem_hdr = QLabel("Element Types")
        col_elem_hdr.setStyleSheet(_hdr_style)
        settings_grid.addWidget(col_elem_hdr, 0, 1, 1, 2)

        self._chk_substations = QCheckBox("Substations / Buses")
        self._chk_substations.setChecked(True)
        self._chk_generators = QCheckBox("Generators")
        self._chk_generators.setChecked(True)
        self._chk_lines = QCheckBox("Transmission Lines")
        self._chk_lines.setChecked(True)
        self._chk_transformers = QCheckBox("Transformers")
        self._chk_transformers.setChecked(True)
        self._chk_storage = QCheckBox("Energy Storage")
        self._chk_storage.setChecked(True)
        self._chk_converters = QCheckBox("AC/DC Converters")
        self._chk_converters.setChecked(True)
        self._chk_fuel_entry = QCheckBox("Fuel Entry Points")
        self._chk_fuel_entry.setChecked(False)
        self._chk_fuel_storage = QCheckBox("Fuel Storage")
        self._chk_fuel_storage.setChecked(False)

        _elem_checks = [
            self._chk_substations, self._chk_generators,
            self._chk_lines, self._chk_transformers,
            self._chk_storage, self._chk_converters,
            self._chk_fuel_entry, self._chk_fuel_storage,
        ]
        elem_a = QVBoxLayout()
        elem_a.setContentsMargins(0, 0, 0, 0)
        elem_b = QVBoxLayout()
        elem_b.setContentsMargins(0, 0, 0, 0)
        for chk in _elem_checks[:4]:
            elem_a.addWidget(chk)
        for chk in _elem_checks[4:]:
            elem_b.addWidget(chk)
        elem_a_widget = QWidget()
        elem_a_widget.setLayout(elem_a)
        elem_b_widget = QWidget()
        elem_b_widget.setLayout(elem_b)
        settings_grid.addWidget(elem_a_widget, 1, 1)
        settings_grid.addWidget(elem_b_widget, 1, 2)

        # Column 4 — Bus Creation Strategy
        col_bus_hdr = QLabel("Bus Creation Strategy")
        col_bus_hdr.setStyleSheet(_hdr_style)
        settings_grid.addWidget(col_bus_hdr, 0, 3)

        bus_widget = QWidget()
        bus_lay = QVBoxLayout(bus_widget)
        bus_lay.setContentsMargins(0, 0, 0, 0)
        self._radio_per_voltage = QRadioButton("One bus per voltage level")
        self._radio_per_voltage.setToolTip(
            "Recommended: separate bus per voltage level in multi-voltage "
            "substations, with auto-created transformer between them."
        )
        self._radio_per_voltage.setChecked(True)
        bus_lay.addWidget(self._radio_per_voltage)

        self._radio_per_substation = QRadioButton("One bus per substation")
        bus_lay.addWidget(self._radio_per_substation)
        bus_lay.addStretch()
        settings_grid.addWidget(bus_widget, 1, 3)

        # Top-aligned content rows
        settings_grid.setRowStretch(2, 1)

        layout.addWidget(settings_group)

        # ── Fetch Button ──
        self._btn_fetch = QPushButton("Fetch Data")
        self._btn_fetch.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 4px 8px;"
        )
        self._btn_fetch.setEnabled(False)
        self._btn_fetch.clicked.connect(self._do_fetch)
        layout.addWidget(self._btn_fetch)

        # ── Progress ──
        self._progress_group = QGroupBox("Download Progress")
        self._progress_layout = QVBoxLayout(self._progress_group)
        self._progress_group.setVisible(False)

        # OSM
        self._lbl_osm = QLabel("OpenStreetMap:")
        self._bar_osm = QProgressBar()
        self._bar_osm.setRange(0, 100)
        self._status_osm = QLabel("")
        self._progress_layout.addWidget(self._lbl_osm)
        self._progress_layout.addWidget(self._bar_osm)
        self._progress_layout.addWidget(self._status_osm)

        # WRI
        self._lbl_wri = QLabel("WRI Power Plants:")
        self._bar_wri = QProgressBar()
        self._bar_wri.setRange(0, 100)
        self._status_wri = QLabel("")
        self._progress_layout.addWidget(self._lbl_wri)
        self._progress_layout.addWidget(self._bar_wri)
        self._progress_layout.addWidget(self._status_wri)

        # GEM
        self._lbl_gem = QLabel("GEM Power Plants:")
        self._bar_gem = QProgressBar()
        self._bar_gem.setRange(0, 100)
        self._status_gem = QLabel("")
        self._progress_layout.addWidget(self._lbl_gem)
        self._progress_layout.addWidget(self._bar_gem)
        self._progress_layout.addWidget(self._status_gem)

        # GridFinder
        self._lbl_gf = QLabel("GridFinder:")
        self._bar_gf = QProgressBar()
        self._bar_gf.setRange(0, 100)
        self._status_gf = QLabel("")
        self._progress_layout.addWidget(self._lbl_gf)
        self._progress_layout.addWidget(self._bar_gf)
        self._progress_layout.addWidget(self._status_gf)

        layout.addWidget(self._progress_group)

        # ── Summary ──
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("font-weight: bold; padding: 8px;")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # ── Error log ──
        self._error_text = QTextEdit()
        self._error_text.setReadOnly(True)
        self._error_text.setMaximumHeight(100)
        self._error_text.setVisible(False)
        layout.addWidget(self._error_text)

        layout.addStretch()
        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll)

        # Connect map bridge for polygon drawing
        self._awaiting_polygon = False
        if self._map_widget:
            self._map_widget.bridge.domainPolygonDrawn.connect(
                self._on_polygon_drawn,
            )
            # ESC during draw fires modeReset — reset our UI so the user
            # can click "Draw Polygon" again instead of being stuck
            # waiting for a polygon that was cancelled.
            self._map_widget.bridge.modeReset.connect(
                self._on_polygon_draw_cancelled,
            )

    # ------------------------------------------------------------------
    # Region drawing
    # ------------------------------------------------------------------

    def _start_drawing(self):
        if not self._map_widget:
            self._draw_status.setText("No map widget available.")
            return
        self._draw_status.setText(
            "Click on the map to place vertices. "
            "Click the first vertex to close the polygon. "
            "Press ESC to cancel."
        )
        self._btn_draw.setEnabled(False)
        self._awaiting_polygon = True
        wizard = self.window()
        if wizard:
            wizard.showMinimized()
        self._map_widget.enable_domain_polygon_draw()

    def _on_polygon_draw_cancelled(self):
        """ESC pressed mid-draw — reset UI so user can retry."""
        if not self._awaiting_polygon:
            return
        self._awaiting_polygon = False
        self._draw_status.setText("Drawing cancelled. Click Draw Polygon to retry.")
        self._btn_draw.setEnabled(True)
        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()

    def _on_polygon_drawn(self, geojson_str: str):
        self._awaiting_polygon = False
        data = json.loads(geojson_str)
        coords_raw = data.get("geometry", {}).get("coordinates", [[]])
        if not coords_raw or not coords_raw[0]:
            self._draw_status.setText("Invalid polygon. Try again.")
            self._btn_draw.setEnabled(True)
            return

        ring = coords_raw[0]
        self._polygon = [(c[1], c[0]) for c in ring]

        lats = [p[0] for p in self._polygon]
        lngs = [p[1] for p in self._polygon]
        self._bounds = (min(lats), min(lngs), max(lats), max(lngs))

        n_verts = len(self._polygon)
        s, w, n, e = self._bounds
        self._draw_status.setText(
            f"Polygon: {n_verts} vertices, "
            f"bbox ({s:.3f}, {w:.3f}) to ({n:.3f}, {e:.3f})"
        )
        self._btn_draw.setEnabled(True)
        self._btn_fetch.setEnabled(True)
        self._update_area()

        if self._map_widget:
            self._map_widget.show_domain_polygon(self._polygon)
            self._map_widget.disable_domain_polygon_draw()

        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()

    def _update_area(self):
        if len(self._polygon) < 3:
            return
        mid_lat = sum(p[0] for p in self._polygon) / len(self._polygon)
        cos_lat = math.cos(math.radians(mid_lat))
        pts_km = [
            (p[0] * 111.32, p[1] * 111.32 * cos_lat)
            for p in self._polygon
        ]
        n = len(pts_km)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += pts_km[i][0] * pts_km[j][1]
            area -= pts_km[j][0] * pts_km[i][1]
        area = abs(area) / 2.0
        self._area_label.setText(f"Region area: ~{area:.0f} km\u00b2")

    # ------------------------------------------------------------------
    # Public API (called by wizard)
    # ------------------------------------------------------------------

    def get_polygon(self) -> list[tuple[float, float]]:
        return self._polygon

    def get_bounds(self) -> Optional[tuple[float, float, float, float]]:
        return self._bounds

    def get_config(self) -> dict:
        element_types = set()
        if self._chk_substations.isChecked():
            element_types.add("substation")
        if self._chk_generators.isChecked():
            element_types.add("generator")
        if self._chk_lines.isChecked():
            element_types.add("line")
        if self._chk_transformers.isChecked():
            element_types.add("transformer")
        if self._chk_storage.isChecked():
            element_types.add("storage")
        if self._chk_converters.isChecked():
            element_types.add("converter")
        if self._chk_fuel_entry.isChecked():
            element_types.add("fuel_entry")
        if self._chk_fuel_storage.isChecked():
            element_types.add("fuel_storage")

        return {
            "sources": {
                "osm": self._chk_osm.isChecked(),
                "wri": self._chk_wri.isChecked(),
                "gem": self._chk_gem.isChecked(),
                "gridfinder": self._chk_gridfinder.isChecked(),
            },
            "min_voltage_kv": self._spin_min_voltage.value(),
            "min_capacity_mw": self._spin_min_capacity.value(),
            "snap_threshold_km": self._spin_snap.value(),
            "element_types": element_types,
            "bus_strategy": (
                "per_voltage" if self._radio_per_voltage.isChecked()
                else "per_substation"
            ),
        }

    def get_features(self) -> list:
        return self._features

    def is_valid(self) -> bool:
        cfg = self.get_config()
        has_sources = any(cfg["sources"].values())
        if not has_sources:
            return False
        # Valid once fetch has completed with results
        return len(self._features) > 0

    def cancel_all(self):
        for f in self._fetchers:
            if hasattr(f, "cancel"):
                f.cancel()

    # ------------------------------------------------------------------
    # Fetch logic
    # ------------------------------------------------------------------

    def _do_fetch(self):
        if not self._bounds:
            return
        config = self.get_config()
        if not any(config["sources"].values()):
            self._summary_label.setText("No sources selected.")
            return

        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("Fetching...")
        self._progress_group.setVisible(True)
        self._summary_label.setText("")
        self._error_text.setVisible(False)
        self._features = []
        self._errors = []
        self._pending = 0
        self._fetchers = []

        self._start_fetch(self._bounds, config, self._polygon)

    def _start_fetch(self, bounds, config, polygon):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            GEMGridFetcher,
            GridFinderFetcher,
            OSMGridFetcher,
            WRIGridFetcher,
        )

        sources = config["sources"]
        min_v = config["min_voltage_kv"]
        min_cap = config["min_capacity_mw"]
        etypes = config["element_types"]

        # Reset progress bars
        for bar in (self._bar_osm, self._bar_wri, self._bar_gem, self._bar_gf):
            bar.setValue(0)
        for lbl in (self._status_osm, self._status_wri, self._status_gem,
                     self._status_gf):
            lbl.setText("")

        # Hide unused sources
        osm_on = sources.get("osm", False)
        wri_on = sources.get("wri", False)
        gem_on = sources.get("gem", False)
        gf_on = sources.get("gridfinder", False)

        self._lbl_osm.setVisible(osm_on)
        self._bar_osm.setVisible(osm_on)
        self._status_osm.setVisible(osm_on)

        self._lbl_wri.setVisible(wri_on)
        self._bar_wri.setVisible(wri_on)
        self._status_wri.setVisible(wri_on)

        self._lbl_gem.setVisible(gem_on)
        self._bar_gem.setVisible(gem_on)
        self._status_gem.setVisible(gem_on)

        self._lbl_gf.setVisible(gf_on)
        self._bar_gf.setVisible(gf_on)
        self._status_gf.setVisible(gf_on)

        if osm_on:
            self._pending += 1
            fetcher = OSMGridFetcher(
                bounds, min_voltage_kv=min_v, min_capacity_mw=min_cap,
                element_types=etypes,
            )
            fetcher.progress.connect(
                lambda pct, msg: self._on_progress("osm", pct, msg)
            )
            fetcher.finished.connect(
                lambda feats: self._on_finished("osm", feats)
            )
            fetcher.error.connect(
                lambda err: self._on_error("osm", err)
            )
            self._fetchers.append(fetcher)
            fetcher.start()

        if wri_on:
            self._pending += 1
            fetcher = WRIGridFetcher(
                bounds, min_capacity_mw=min_cap,
            )
            fetcher.progress.connect(
                lambda pct, msg: self._on_progress("wri", pct, msg)
            )
            fetcher.finished.connect(
                lambda feats: self._on_finished("wri", feats)
            )
            fetcher.error.connect(
                lambda err: self._on_error("wri", err)
            )
            self._fetchers.append(fetcher)
            fetcher.start()

        if gem_on:
            self._pending += 1
            fetcher = GEMGridFetcher(
                bounds, min_capacity_mw=min_cap,
            )
            fetcher.progress.connect(
                lambda pct, msg: self._on_progress("gem", pct, msg)
            )
            fetcher.finished.connect(
                lambda feats: self._on_finished("gem", feats)
            )
            fetcher.error.connect(
                lambda err: self._on_error("gem", err)
            )
            self._fetchers.append(fetcher)
            fetcher.start()

        if gf_on:
            self._pending += 1
            fetcher = GridFinderFetcher(bounds)
            fetcher.progress.connect(
                lambda pct, msg: self._on_progress("gridfinder", pct, msg)
            )
            fetcher.finished.connect(
                lambda feats: self._on_finished("gridfinder", feats)
            )
            fetcher.error.connect(
                lambda err: self._on_error("gridfinder", err)
            )
            self._fetchers.append(fetcher)
            fetcher.start()

        if self._pending == 0:
            self._summary_label.setText("No sources selected.")
            self.fetchFinished.emit()

    def _on_progress(self, source: str, pct: int, msg: str):
        bar, status = self._get_widgets(source)
        if bar:
            bar.setValue(pct)
        if status:
            status.setText(msg)

    def _on_finished(self, source: str, features: list):
        if features:
            self._features.extend(features)
        self._pending -= 1
        if self._pending <= 0:
            self._finalize()

    def _on_error(self, source: str, error_msg: str):
        self._errors.append(f"{source.upper()}: {error_msg}")
        bar, status = self._get_widgets(source)
        if status:
            status.setText(f"Error: {error_msg}")
            status.setStyleSheet("color: red;")
        self._pending -= 1
        if self._pending <= 0:
            self._finalize()

    def _get_widgets(self, source: str):
        if source == "osm":
            return self._bar_osm, self._status_osm
        if source == "wri":
            return self._bar_wri, self._status_wri
        if source == "gem":
            return self._bar_gem, self._status_gem
        if source == "gridfinder":
            return self._bar_gf, self._status_gf
        return None, None

    def _finalize(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            deduplicate_features,
            filter_features_by_polygon,
        )

        if self._features and self._polygon:
            before = len(self._features)
            self._features = filter_features_by_polygon(
                self._features, self._polygon,
            )
            logger.info(
                "Polygon filter: %d → %d features",
                before, len(self._features),
            )

        if self._features:
            self._features = deduplicate_features(self._features)

        counts: dict[str, int] = {}
        for f in self._features:
            counts[f.feature_type] = counts.get(f.feature_type, 0) + 1

        parts = []
        for ftype in ["substation", "generator", "battery", "line",
                       "transformer", "converter", "fuel_entry",
                       "fuel_storage", "road"]:
            c = counts.get(ftype, 0)
            if c:
                parts.append(f"{c} {ftype}(s)")

        if parts:
            self._summary_label.setText(
                f"Found {len(self._features)} features: " + ", ".join(parts)
            )
        else:
            self._summary_label.setText(
                "No features found in the selected region."
            )

        if self._errors:
            self._error_text.setVisible(True)
            self._error_text.setPlainText("\n".join(self._errors))

        self._btn_fetch.setText("Re-fetch Data")
        self._btn_fetch.setEnabled(True)

        self.fetchFinished.emit()


# =====================================================================
# Step 3: Review & Edit (was Step 4)
# =====================================================================


class GridMappingReviewStep(QWidget):
    """Review fetched features and toggle inclusion."""

    def __init__(self, map_widget=None, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._features: list = []

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<b>Step 3: Review & Edit</b><br>"
            "Review the fetched grid features. Uncheck items you don't want "
            "to import. You can also change the element type."
        ))

        # ── Quick filters ──
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter by type:"))

        self._filter_combo = QComboBox()
        self._filter_combo.addItems([
            "All Types", "Substations", "Generators", "Batteries",
            "Lines", "Transformers", "Converters",
            "Fuel Entries", "Fuel Storage",
        ])
        self._filter_combo.currentIndexChanged.connect(self._apply_table_filter)
        filter_row.addWidget(self._filter_combo)

        filter_row.addStretch()

        self._btn_select_all = QPushButton("Select All")
        self._btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        filter_row.addWidget(self._btn_select_all)

        self._btn_deselect_all = QPushButton("Deselect All")
        self._btn_deselect_all.clicked.connect(lambda: self._set_all_checked(False))
        filter_row.addWidget(self._btn_deselect_all)

        layout.addLayout(filter_row)

        # ── Feature table ──
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Include", "Source", "Type", "Name",
            "Voltage (kV)", "Capacity (MW)", "Fuel",
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        # Summary
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self._summary_label)

    def set_features(self, features: list):
        """Populate the table with fetched features."""
        self._features = features
        self._populate_table()
        self._update_summary()

    def _populate_table(self):
        self._table.setRowCount(len(self._features))

        for row, feat in enumerate(self._features):
            # Hide road features (auxiliary data for routing)
            if feat.feature_type == "road":
                self._table.setRowHidden(row, True)
                continue

            # Include checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            chk_item.setCheckState(
                Qt.CheckState.Checked if feat.include else Qt.CheckState.Unchecked
            )
            self._table.setItem(row, 0, chk_item)

            # Source (read-only)
            src_item = QTableWidgetItem(feat.source.upper())
            src_item.setFlags(src_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, src_item)

            # Type (read-only)
            type_item = QTableWidgetItem(feat.feature_type)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, type_item)

            # Name (editable)
            self._table.setItem(row, 3, QTableWidgetItem(feat.name))

            # Voltage (editable)
            v_str = f"{feat.voltage_kv:.0f}" if feat.voltage_kv > 0 else ""
            if feat.voltage_kv_secondary > 0:
                v_str += f" / {feat.voltage_kv_secondary:.0f}"
            self._table.setItem(row, 4, QTableWidgetItem(v_str))

            # Capacity (editable)
            c_str = f"{feat.capacity_mw:.1f}" if feat.capacity_mw > 0 else ""
            self._table.setItem(row, 5, QTableWidgetItem(c_str))

            # Fuel (editable)
            self._table.setItem(row, 6, QTableWidgetItem(feat.fuel))

        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(4, 90)
        self._table.setColumnWidth(5, 90)
        self._table.setColumnWidth(6, 100)

        # Track checkbox changes
        self._table.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        col = item.column()
        if not (0 <= row < len(self._features)):
            return
        feat = self._features[row]
        if col == 0:
            feat.include = (item.checkState() == Qt.CheckState.Checked)
            self._update_summary()
        elif col == 3:  # Name
            feat.name = item.text()
        elif col == 4:  # Voltage
            self._parse_voltage_cell(feat, item.text())
        elif col == 5:  # Capacity
            try:
                feat.capacity_mw = float(item.text()) if item.text() else 0.0
            except ValueError:
                pass
        elif col == 6:  # Fuel
            feat.fuel = item.text()

    @staticmethod
    def _parse_voltage_cell(feat, text: str):
        """Parse voltage text like '220', '220 / 110', or '220/110'."""
        text = text.strip()
        if "/" in text:
            parts = text.split("/")
            try:
                feat.voltage_kv = float(parts[0].strip())
            except ValueError:
                pass
            try:
                feat.voltage_kv_secondary = float(parts[1].strip())
            except ValueError:
                pass
        else:
            try:
                feat.voltage_kv = float(text) if text else 0.0
                feat.voltage_kv_secondary = 0.0
            except ValueError:
                pass

    def _set_all_checked(self, checked: bool):
        self._table.itemChanged.disconnect(self._on_item_changed)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._table.rowCount()):
            if not self._table.isRowHidden(row):
                item = self._table.item(row, 0)
                if item:
                    item.setCheckState(state)
                    if row < len(self._features):
                        self._features[row].include = checked
        self._table.itemChanged.connect(self._on_item_changed)
        self._update_summary()

    def _apply_table_filter(self, index: int):
        type_map = {
            0: None,  # All
            1: "substation",
            2: "generator",
            3: "battery",
            4: "line",
            5: "transformer",
            6: "converter",
            7: "fuel_entry",
            8: "fuel_storage",
        }
        filter_type = type_map.get(index)

        for row in range(self._table.rowCount()):
            if filter_type is None:
                self._table.setRowHidden(row, False)
            else:
                if row < len(self._features):
                    self._table.setRowHidden(
                        row, self._features[row].feature_type != filter_type
                    )

    def _update_summary(self):
        total = len(self._features)
        selected = sum(1 for f in self._features if f.include)
        self._summary_label.setText(
            f"{selected} of {total} features selected for import"
        )

    def get_features(self) -> list:
        """Return features with updated include flags."""
        return self._features

    def is_valid(self) -> bool:
        return any(f.include for f in self._features)


# =====================================================================
# Step 4: Build Network (was Step 5)
# =====================================================================

_CRITERIA = [
    {
        "key": "infrastructure",
        "label": "Infrastructure Density",
        "description": (
            "K-means on all infrastructure positions (substations, "
            "generators, batteries, fuel entries). Places nodes at "
            "cluster centers."
        ),
    },
    {
        "key": "demand",
        "label": "Demand Proxy (Building Footprints)",
        "description": (
            "Fetches building footprints (Overture/Microsoft/Google) "
            "and clusters by building density to approximate demand "
            "hotspots. Requires additional download."
        ),
    },
    {
        "key": "regional",
        "label": "Regional Balance (Uniform Coverage)",
        "description": (
            "Modified K-means with spatially-uniform initialization. "
            "Ensures even geographic coverage regardless of density. "
            "Good for planning studies."
        ),
    },
]


class GridMappingBuildStep(QWidget):
    """Configure node placement, build and auto-connect the network."""

    buildFinished = Signal()

    def __init__(
        self, model=None, all_states=None,
        switch_system_fn=None, create_system_fn=None, parent=None,
    ):
        super().__init__(parent)
        self._model = model
        self._all_states = all_states if all_states is not None else {}
        self._switch_system_fn = switch_system_fn
        self._create_system_fn = create_system_fn
        self._built = False
        self._connected = False
        self._clustering_worker = None
        # Snapshots of each target system's state taken right before its
        # first build. Lets us restore the baseline if the user goes
        # Back and re-builds — otherwise build_grid_from_features would
        # append on top of an already-built state and duplicate elements.
        self._pre_build_snapshots: dict[str, "GuiSystemState"] = {}

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        layout.addWidget(QLabel(
            "<b>Step 4: Build & Connect</b><br>"
            "Select the target system, optionally auto-create nodes via "
            "spatial clustering, build the network, then auto-connect "
            "isolated sub-networks."
        ))

        # ── Target System ────────────────────────────────────────────
        sys_group = QGroupBox("Target System")
        sys_lay = QHBoxLayout(sys_group)

        self._combo_system = QComboBox()
        self._combo_system.setMinimumWidth(200)
        self._combo_system.setToolTip(
            "Choose which system to assign the built elements to."
        )
        sys_lay.addWidget(self._combo_system, 1)

        self._btn_new_system = QPushButton("New System...")
        self._btn_new_system.clicked.connect(self._on_new_system)
        sys_lay.addWidget(self._btn_new_system)

        layout.addWidget(sys_group)

        # ── Node Placement ───────────────────────────────────────────
        node_group = QGroupBox("Node Placement (optional)")
        node_lay = QVBoxLayout(node_group)

        self._chk_auto_nodes = QCheckBox(
            "Automatically create nodes from spatial clustering"
        )
        self._chk_auto_nodes.setChecked(True)
        self._chk_auto_nodes.toggled.connect(self._on_auto_nodes_toggled)
        node_lay.addWidget(self._chk_auto_nodes)

        # Wrap the two-column body in a single widget so we can
        # enable/disable the entire block when ``_chk_auto_nodes`` is
        # toggled without having to track each child individually.
        self._node_options_widget = QWidget()
        node_cols = QHBoxLayout(self._node_options_widget)
        node_cols.setContentsMargins(0, 0, 0, 0)

        _hdr_style = "font-weight: bold;"

        # ─ Left column ──────────────────────────────────────────────
        node_left = QVBoxLayout()
        node_left_hdr = QLabel("Cluster size")
        node_left_hdr.setStyleSheet(_hdr_style)
        node_left.addWidget(node_left_hdr)

        node_form = QFormLayout()
        node_form.setContentsMargins(0, 0, 0, 0)
        self._spin_min_nodes = QSpinBox()
        self._spin_min_nodes.setRange(1, 100)
        self._spin_min_nodes.setValue(2)
        self._spin_min_nodes.setToolTip("Minimum number of nodes to create.")
        node_form.addRow("Minimum nodes:", self._spin_min_nodes)

        self._spin_max_nodes = QSpinBox()
        self._spin_max_nodes.setRange(1, 200)
        self._spin_max_nodes.setValue(20)
        self._spin_max_nodes.setToolTip("Maximum number of nodes to create.")
        node_form.addRow("Maximum nodes:", self._spin_max_nodes)
        node_left.addLayout(node_form)
        node_left.addStretch()

        # ─ Right column ─────────────────────────────────────────────
        node_right = QVBoxLayout()
        node_right_hdr = QLabel("Clustering criteria (select one or more)")
        node_right_hdr.setStyleSheet(_hdr_style)
        node_right.addWidget(node_right_hdr)

        # Create description label first (toggled signal fires during init)
        self._lbl_criterion_info = QLabel(_CRITERIA[0]["description"])
        self._chk_criteria: dict[str, QCheckBox] = {}
        for crit in _CRITERIA:
            chk = QCheckBox(crit["label"])
            chk.setToolTip(crit["description"])
            chk.toggled.connect(self._on_criterion_toggled)
            self._chk_criteria[crit["key"]] = chk
            node_right.addWidget(chk)
        # Default: infrastructure checked
        self._chk_criteria["infrastructure"].setChecked(True)
        self._lbl_criterion_info.setWordWrap(True)
        self._lbl_criterion_info.setStyleSheet(
            "color: #888; font-size: 11px; padding: 4px 0;"
        )
        node_right.addWidget(self._lbl_criterion_info)
        node_right.addStretch()

        node_cols.addLayout(node_left, 1)
        node_cols.addLayout(node_right, 1)
        node_lay.addWidget(self._node_options_widget)

        # Clustering progress bar — full width below the two columns
        self._cluster_progress = QProgressBar()
        self._cluster_progress.setRange(0, 100)
        self._cluster_progress.setValue(0)
        self._cluster_progress.setVisible(False)
        self._cluster_progress.setTextVisible(True)
        node_lay.addWidget(self._cluster_progress)

        self._lbl_cluster_status = QLabel("")
        self._lbl_cluster_status.setWordWrap(True)
        node_lay.addWidget(self._lbl_cluster_status)

        layout.addWidget(node_group)

        # ── Build Network ────────────────────────────────────────────
        build_group = QGroupBox("Build Network")
        build_lay = QVBoxLayout(build_group)
        build_lay.addWidget(QLabel(
            "Create buses, generators, lines, transformers and converters "
            "from the fetched features, then auto-connect isolated "
            "sub-networks using transformer chains and bridges."
        ))

        # Two-column body: left = auto-connect numeric params,
        # right = build / simplify options.
        build_cols = QHBoxLayout()

        # ─ Left column: auto-connect parameters ─────────────────────
        build_left = QVBoxLayout()
        config_form = QFormLayout()
        config_form.setContentsMargins(0, 0, 0, 0)

        self._spin_max_iter = QSpinBox()
        self._spin_max_iter.setRange(1, 100)
        self._spin_max_iter.setValue(20)
        self._spin_max_iter.setToolTip(
            "Maximum number of check/fix iterations. The loop stops "
            "early when no issues remain."
        )
        config_form.addRow("Max iterations:", self._spin_max_iter)

        self._spin_voltage_ratio = QDoubleSpinBox()
        self._spin_voltage_ratio.setRange(1.1, 10.0)
        self._spin_voltage_ratio.setValue(1.5)
        self._spin_voltage_ratio.setDecimals(1)
        self._spin_voltage_ratio.setToolTip(
            "Bus-to-bus lines whose endpoint voltage ratio exceeds this "
            "threshold are replaced with a transformer chain."
        )
        config_form.addRow("Voltage mismatch ratio:", self._spin_voltage_ratio)

        self._spin_max_distance = QDoubleSpinBox()
        self._spin_max_distance.setRange(10.0, 10000.0)
        self._spin_max_distance.setValue(100.0)
        self._spin_max_distance.setDecimals(0)
        self._spin_max_distance.setSuffix(" km")
        self._spin_max_distance.setToolTip(
            "Maximum interconnection distance. Components beyond this "
            "distance form independent local networks instead of being "
            "bridged to the main network (e.g. islands)."
        )
        config_form.addRow("Max interconnection distance:", self._spin_max_distance)

        self._spin_lv_voltage = QDoubleSpinBox()
        self._spin_lv_voltage.setRange(0.001, 1500.0)
        self._spin_lv_voltage.setValue(0.48)
        self._spin_lv_voltage.setDecimals(3)
        self._spin_lv_voltage.setSuffix(" kV")
        self._spin_lv_voltage.setToolTip(
            "Voltage level for auto-created LV buses in equipment chains."
        )
        config_form.addRow("LV bus voltage:", self._spin_lv_voltage)

        build_left.addLayout(config_form)
        build_left.addStretch()

        # ─ Right column: build / simplify options ───────────────────
        build_right = QVBoxLayout()

        # Availability profiles: synthetic by default (instant);
        # weather-data only on demand (slow but realistic for wind/solar).
        avail_box = QHBoxLayout()
        self._chk_gen_availability = QCheckBox(
            "Generate availability profiles"
        )
        self._chk_gen_availability.setChecked(True)
        self._chk_gen_availability.setToolTip(
            "After building, write a per-generator availability CSV "
            "next to the YAML (or in ./availability/). Synthetic for "
            "thermal / hydro / geothermal / biomass; flat 0.20-0.32 "
            "for wind/solar unless 'use weather data' is also checked."
        )
        avail_box.addWidget(self._chk_gen_availability)
        self._chk_use_weather = QCheckBox("Use weather data (slow)")
        self._chk_use_weather.setChecked(False)
        self._chk_use_weather.setToolTip(
            "When set, fetch real solar/wind capacity factors from the "
            "selected source (Open-Meteo by default). Adds ~30 s per "
            "wind/solar generator."
        )
        avail_box.addWidget(self._chk_use_weather)
        avail_box.addStretch()
        build_right.addLayout(avail_box)

        self._chk_skip_incomplete = QCheckBox(
            "Skip incomplete elements during build"
        )
        self._chk_skip_incomplete.setChecked(False)
        self._chk_skip_incomplete.setToolTip(
            "Discards features that lack key properties before building:\n"
            "  • Generator without capacity or fuel\n"
            "  • Battery without power or energy capacity\n"
            "  • Line without voltage or geometry\n"
            "  • Substation/transformer/converter without voltage\n"
            "Skipped elements are reported in the result log."
        )
        build_right.addWidget(self._chk_skip_incomplete)

        # Simplification level (was a separate step; now implicit so the
        # GUI only ever paints the final, simplified state once).
        simp_form = QFormLayout()
        simp_form.setContentsMargins(0, 0, 0, 0)
        self._combo_simplify = QComboBox()
        for value, label in [
            (0, "0 — Cleanup only"),
            (1, "1 — Aggregate equipment & parallel lines"),
            (2, "2 — + Radial / series bus elimination"),
            (3, "3 — + Intra-node voltage collapse"),
            (4, "4 — + Full node collapse"),
        ]:
            self._combo_simplify.addItem(label, value)
        self._combo_simplify.setCurrentIndex(0)
        # Compute the natural pixel width of the widest item and force
        # the combo to at least that wide. setMinimumContentsLength
        # only affects the popup view, not the visible widget — which
        # is what was being elided when the QFormLayout constrained it.
        _fm = self._combo_simplify.fontMetrics()
        _max_text_w = max(
            _fm.horizontalAdvance(self._combo_simplify.itemText(i))
            for i in range(self._combo_simplify.count())
        )
        # +50 px for the dropdown arrow + paddings + safety margin,
        # then scale to 75% of that (text gets elided gracefully on
        # the longest item when the column is tight, but the natural
        # column width stays compact).
        self._combo_simplify.setMinimumWidth(int((_max_text_w + 50) * 0.75))
        self._combo_simplify.setToolTip(
            "Simplification applied after build & auto-connect, before "
            "the network is drawn. Higher levels = simpler network, "
            "fewer buses/lines, faster downstream simulation."
        )
        simp_form.addRow("Simplification level:", self._combo_simplify)

        # Drop tiny isolated subgraphs (single-bus substations from OSM
        # that the auto-connect distance limit couldn't bridge). The
        # largest component is always kept — this only removes debris.
        self._spin_min_component = QSpinBox()
        self._spin_min_component.setRange(1, 50)
        self._spin_min_component.setValue(2)
        self._spin_min_component.setSuffix(" bus(es)")
        self._spin_min_component.setToolTip(
            "Drop isolated subgraphs smaller than this. Useful to clear "
            "remote single-substation 'islands' from OSM that the "
            "auto-connect distance limit couldn't bridge to the main "
            "grid. Set to 1 to disable; the largest component is "
            "always kept regardless."
        )
        simp_form.addRow("Drop isolated <", self._spin_min_component)
        build_right.addLayout(simp_form)
        build_right.addStretch()

        build_cols.addLayout(build_left, 1)
        build_cols.addLayout(build_right, 1)
        build_lay.addLayout(build_cols)

        self._btn_build = QPushButton("Build Network")
        self._btn_build.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 4px 8px;"
        )
        self._btn_build.clicked.connect(self._do_build)
        build_lay.addWidget(self._btn_build)
        self._lbl_build_status = QLabel("")
        self._lbl_build_status.setWordWrap(True)
        build_lay.addWidget(self._lbl_build_status)
        layout.addWidget(build_group)

        # ── Result log ───────────────────────────────────────────────
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(150)
        layout.addWidget(self._result_text, 1)

        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_inputs(
        self,
        features: list,
        config: dict,
        bounds: Optional[tuple[float, float, float, float]] = None,
        polygon: Optional[list[tuple[float, float]]] = None,
    ):
        """Store inputs for when the user clicks Build."""
        # If features/config changed (new fetch from Step 1), drop any
        # baseline snapshots taken for previous inputs — those baselines
        # are no longer the right "before build" state.
        if (getattr(self, "_features", None) is not features
                or getattr(self, "_config", None) is not config):
            self._pre_build_snapshots = {}
        self._features = features
        self._config = config
        self._bounds = bounds
        self._polygon = polygon
        self._built = False
        self._connected = False
        self._btn_build.setEnabled(True)
        self._lbl_build_status.setText("")
        self._lbl_cluster_status.setText("")
        self._cluster_progress.setValue(0)
        self._cluster_progress.setVisible(False)
        self._result_text.clear()
        self._refresh_system_combo()

    def is_valid(self) -> bool:
        return self._built

    # ------------------------------------------------------------------
    # UI Callbacks
    # ------------------------------------------------------------------

    def _refresh_system_combo(self):
        """Populate the system combo with current system names."""
        self._combo_system.blockSignals(True)
        self._combo_system.clear()
        for name in self._all_states:
            self._combo_system.addItem(name)
        # Select the currently active system
        current = self._model.state.name if self._model else ""
        idx = self._combo_system.findText(current)
        if idx >= 0:
            self._combo_system.setCurrentIndex(idx)
        self._combo_system.blockSignals(False)

    def _on_new_system(self):
        """Create a new system and add it to the combo."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "New System", "Enter the new system name:",
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        if name in self._all_states:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A system named '{name}' already exists.",
            )
            return

        # Use the MainWindow callback to properly create the system
        # (adds to _all_states, element tree, switches, updates toolbar)
        if self._create_system_fn:
            ok = self._create_system_fn(name)
            if not ok:
                return
        else:
            # Fallback: create directly (tree won't be updated)
            from esfex.visualization.data.gui_model import GuiSystemState
            self._all_states[name] = GuiSystemState(name=name)
            if self._switch_system_fn:
                self._switch_system_fn(name)

        self._refresh_system_combo()
        self._combo_system.setCurrentText(name)

    def _switch_to_selected_system(self):
        """Switch the model to the system selected in the combo."""
        name = self._combo_system.currentText()
        if not name:
            return
        if self._switch_system_fn and name != self._model.state.name:
            self._switch_system_fn(name)

    def _snapshot_or_restore_baseline(self):
        """Ensure each build starts from the same pre-build baseline.

        First time Build runs for a given target system, snapshot the
        current state. On subsequent re-builds (user pressed Back and
        returned), restore the snapshot so we don't duplicate elements
        on top of the prior build's output.
        """
        import copy
        name = self._model.state.name
        if not name:
            return
        if name in self._pre_build_snapshots:
            # Restore: re-build starts from the original baseline
            baseline = copy.deepcopy(self._pre_build_snapshots[name])
            self._model.load_state(baseline)
            if name in self._all_states:
                self._all_states[name] = self._model.state
        else:
            # First build for this system — capture baseline
            self._pre_build_snapshots[name] = copy.deepcopy(self._model.state)

    def _on_auto_nodes_toggled(self, checked: bool):
        # Disabling the container greys out every child (form labels,
        # spin boxes, criteria checkboxes, criterion info label) in
        # one shot — no per-widget tracking needed.
        self._node_options_widget.setEnabled(checked)

    def _on_criterion_toggled(self, _checked: bool = False):
        # Update description to show info about all checked criteria
        checked = [
            crit for crit in _CRITERIA
            if self._chk_criteria[crit["key"]].isChecked()
        ]
        if checked:
            descs = [c["description"] for c in checked]
            self._lbl_criterion_info.setText(" | ".join(descs))
        else:
            self._lbl_criterion_info.setText(
                "Select at least one criterion."
            )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _do_build(self):
        if self._built:
            return

        # Validate system selection
        if not self._combo_system.currentText():
            QMessageBox.warning(
                self, "No System",
                "Select a target system or create a new one.",
            )
            return

        self._btn_build.setEnabled(False)

        # Switch to the selected system before building
        self._switch_to_selected_system()

        # Snapshot baseline (or restore it on re-build). Without this,
        # going Back to Step 1 and clicking Build again would append on
        # top of the previously-built state and duplicate everything.
        self._snapshot_or_restore_baseline()

        if self._chk_auto_nodes.isChecked():
            self._start_clustering()
        else:
            # Build directly — nodes must pre-exist
            if not self._model.state.nodes:
                QMessageBox.warning(
                    self, "No Nodes",
                    "No nodes exist in the system. Enable auto-create "
                    "nodes or create nodes manually before building.",
                )
                self._btn_build.setEnabled(True)
                return
            self._run_build()

    def _start_clustering(self):
        from esfex.visualization.workflows.grid_mapping_clustering import (
            NodeClusteringWorker,
        )

        # Gather checked criteria
        selected = [
            key for key, chk in self._chk_criteria.items()
            if chk.isChecked()
        ]
        if not selected:
            QMessageBox.warning(
                self, "No Criterion",
                "Select at least one clustering criterion.",
            )
            self._btn_build.setEnabled(True)
            return

        self._lbl_cluster_status.setText("Running node clustering...")
        self._cluster_progress.setValue(0)
        self._cluster_progress.setVisible(True)

        worker = NodeClusteringWorker(
            features=self._features,
            criteria=selected,
            min_nodes=self._spin_min_nodes.value(),
            max_nodes=self._spin_max_nodes.value(),
            bounds=self._bounds,
            polygon=self._polygon,
        )
        worker.progress.connect(self._on_clustering_progress)
        worker.finished.connect(self._on_clustering_done)
        worker.error.connect(self._on_clustering_error)
        self._clustering_worker = worker
        worker.start()

    def _on_clustering_progress(self, pct: int, msg: str):
        self._cluster_progress.setValue(pct)
        self._lbl_cluster_status.setText(msg)

    def _on_clustering_done(self, result):
        self._cluster_progress.setValue(100)

        with self._model.suspend_checkpoints():
            for lat, lng, name in result.node_positions:
                idx = self._model.add_node(name)
                self._model.update_node(
                    idx, centroid_lat=lat, centroid_lng=lng,
                )

        self._lbl_cluster_status.setText(
            f"Created {result.n_clusters} nodes via "
            f"{result.criterion_used} clustering."
        )
        # Don't emit stateLoaded here — _run_build will emit at the end
        self._run_build()

    def _on_clustering_error(self, error_msg: str):
        self._cluster_progress.setVisible(False)
        self._lbl_cluster_status.setText(f"Clustering error: {error_msg}")
        self._btn_build.setEnabled(True)

    def _run_build(self):
        from esfex.visualization.workflows.grid_mapping_builder import (
            build_grid_from_features,
        )
        from esfex.visualization.workflows.grid_mapping_quality import (
            is_feature_complete, reason_incomplete,
        )

        self._lbl_build_status.setText("Building network...")

        # Optionally drop features that lack key properties before
        # handing off to the builder. We mutate ``include`` rather than
        # filter the list so the Review step's user toggles are kept
        # in sync.
        skip_log: list[str] = []
        if self._chk_skip_incomplete.isChecked():
            n_before = sum(1 for f in self._features if f.include)
            skip_counts: dict[str, int] = {}
            for f in self._features:
                if not f.include:
                    continue
                if not is_feature_complete(f):
                    f.include = False
                    skip_counts[f.feature_type] = (
                        skip_counts.get(f.feature_type, 0) + 1
                    )
                    skip_log.append(
                        f"  · {f.feature_type} '{f.name or f.osm_id}': "
                        f"{reason_incomplete(f)}"
                    )
            n_skipped = n_before - sum(1 for f in self._features if f.include)
            if n_skipped:
                summary_line = ", ".join(
                    f"{n} {t}" for t, n in sorted(skip_counts.items())
                )
                skip_log.insert(
                    0,
                    f"Skipped {n_skipped} incomplete element(s): "
                    f"{summary_line}",
                )

        try:
            with self._model.suspend_checkpoints():
                result = build_grid_from_features(
                    model=self._model,
                    features=self._features,
                    bus_strategy=self._config.get("bus_strategy", "per_voltage"),
                    snap_threshold_km=self._config.get("snap_threshold_km", 5.0),
                    target_node=None,
                )

            summary = result.summary()
            if skip_log:
                summary = (
                    "── Pre-build filter ──\n"
                    + "\n".join(skip_log)
                    + "\n\n"
                    + summary
                )
            self._lbl_build_status.setText("Build complete. Running auto-connect...")
            self._result_text.setPlainText(summary)

            self._built = True

        except Exception as exc:
            logger.exception("Grid mapping build error")
            self._lbl_build_status.setText(f"Error: {exc}")
            self._btn_build.setEnabled(True)
            return

        # Automatically run auto-connect after building
        self._run_auto_connect()

    def _run_auto_connect(self):
        """Run iterative auto-connect, then simplify, then redraw once.

        The full pipeline runs against the model state without emitting
        ``stateLoaded`` until the very end \u2014 the GUI only repaints
        once, on the final simplified topology. This avoids the
        redraw-then-mutate-then-redraw race that was leaving orphan
        markers when simplification ran as a separate step.
        """
        from esfex.visualization.data.validation import (
            apply_simplification_level, SimplificationConfig,
            drop_dangling_refs, drop_isolated_components,
            rebuild_visual_wire_lines,
        )

        connect_summary = ""
        simplify_summary = ""
        island_summary = ""
        n_created = 0

        try:
            # Phase 1: auto-connect
            with self._model.suspend_checkpoints():
                n_created, connect_log = iterative_auto_connect(
                    self._model, self._model.state,
                    max_iterations=self._spin_max_iter.value(),
                    voltage_mismatch_ratio=self._spin_voltage_ratio.value(),
                    lv_voltage_kv=self._spin_lv_voltage.value(),
                    max_connection_km=self._spin_max_distance.value(),
                )
            connect_summary = "\n".join(connect_log)

            # Phase 1b: infer missing electrical parameters (capacities,
            # impedances) from the connected gen / demand. Without this,
            # lines and transformers built from sources lacking voltage
            # or rated MVA stay at 0 and break downstream power-flow.
            from esfex.visualization.workflows.grid_mapping_inference import (
                infer_electrical_params,
            )
            with self._model.suspend_checkpoints():
                infer_report = infer_electrical_params(self._model.state)
            connect_summary = (
                connect_summary
                + ("\n" if connect_summary else "")
                + f"Param inference: {infer_report.summary()}"
            )

            # Phase 2: simplification (no GUI emit yet)
            level = self._combo_simplify.currentData() or 0
            self._lbl_build_status.setText(
                f"Simplifying (level {level})..."
            )
            with self._model.suspend_checkpoints():
                simp_log, _issues = apply_simplification_level(
                    self._model, level, SimplificationConfig(),
                )
            simplify_summary = "\n".join(simp_log)

            # Phase 2b: drop isolated debris components. Always run —
            # even with min_buses=1 we report component sizes so the
            # user can diagnose why "isolated" elements survive.
            min_size = self._spin_min_component.value()
            with self._model.suspend_checkpoints():
                counts = drop_isolated_components(
                    self._model.state, min_buses=min_size,
                    keep_largest=True,
                )
            island_lines: list[str] = []
            n_total = counts.get("_components_total", 0)
            n_dropped = counts.get("_components_dropped", 0)
            top_sizes = counts.get("_top_sizes", [])
            island_lines.append(
                f"Components: {n_total} total "
                f"(largest = {counts.get('_largest_size', 0)} buses, "
                f"top sizes: {top_sizes})"
            )
            island_lines.append(
                f"Threshold: drop components with < {min_size} bus(es), "
                f"keeping the largest"
            )
            if n_dropped == 0:
                island_lines.append(
                    "→ Nothing dropped. If you still see isolated "
                    "elements, they are NOT in their own component "
                    "(probably broken endpoints — see below)."
                )
            else:
                island_lines.append(
                    f"→ Dropped {n_dropped} component(s): "
                    f"{counts['buses']} bus(es), "
                    f"{counts['lines']} line(s), "
                    f"{counts['transformers']} transformer(s), "
                    f"{counts['converters']} converter(s), "
                    f"{counts['generators']} generator(s), "
                    f"{counts['batteries']} battery(ies)"
                    + (f", {counts['electrolyzers']} electrolyzer(s)"
                       if counts.get('electrolyzers') else "")
                )

            # Phase 2c: hard sweep — anything still pointing at a
            # missing bus dies now. Catches ghosts left by every
            # earlier step (simplification, prune, equipment merge).
            with self._model.suspend_checkpoints():
                ref_counts = drop_dangling_refs(self._model.state)
            n_dangling = sum(ref_counts.values())
            if n_dangling > 0:
                island_lines.append(
                    f"Dangling-reference sweep removed: "
                    f"{ref_counts['lines']} line(s), "
                    f"{ref_counts['transformers']} transformer(s), "
                    f"{ref_counts['converters']} converter(s), "
                    f"{ref_counts['generators']} generator(s), "
                    f"{ref_counts['batteries']} battery(ies)"
                    + (f", {ref_counts['electrolyzers']} electrolyzer(s)"
                       if ref_counts.get('electrolyzers') else "")
                    + " with broken bus refs."
                )

            # Phase 2d: rebuild visual wire-lines. Transformers/equipment
            # render through ``EndpointRef``-decorated lines, not the
            # legacy ``from_bus``/``to_bus`` strings. Without this pass
            # the bus graph can be fully connected but the map shows
            # transformers as floating dots between unconnected buses.
            with self._model.suspend_checkpoints():
                n_wires = rebuild_visual_wire_lines(self._model.state)
            if n_wires:
                island_lines.append(
                    f"Rebuilt {n_wires} visual wire-line(s) "
                    f"(transformer / equipment connections)."
                )

            # Phase 2e: optional availability-profile generation.
            if (self._chk_gen_availability.isChecked()
                    and self._model.state.generators):
                self._lbl_build_status.setText(
                    "Generating availability profiles..."
                )
                from esfex.plugins.availability_generator.grid_builder_hook import (
                    generate_for_grid_build,
                )
                # Pick output dir: alongside the loaded YAML if known,
                # else a sibling 'availability' folder.
                main_window = self.window()
                cfg_path = getattr(main_window, "_config_path", None)
                if cfg_path:
                    out_dir = Path(cfg_path).parent / "availability"
                else:
                    out_dir = Path.cwd() / "availability"
                with self._model.suspend_checkpoints():
                    written = generate_for_grid_build(
                        self._model.state,
                        out_dir,
                        use_weather_data=self._chk_use_weather.isChecked(),
                    )
                if written:
                    island_lines.append(
                        f"Generated {len(written)} availability "
                        f"profile(s) under {out_dir}."
                    )
            island_summary = "\n".join(island_lines)

        except Exception as exc:
            logger.exception("Build pipeline error during connect/simplify")
            self._lbl_build_status.setText(
                f"Network built, but post-processing failed: {exc}"
            )

        finally:
            # Phase 3: single emit so the GUI paints the final state.
            # Even on failure we still emit so the user sees the partial
            # network instead of a blank canvas.
            self._model.stateLoaded.emit()

        # Status + log assembly
        if n_created == 0:
            head = "Network built and fully connected"
        else:
            head = (
                f"Network built. Auto-connect created/modified "
                f"{n_created} element(s)"
            )
        level = self._combo_simplify.currentData() or 0
        if level > 0:
            head += f"; simplified at level {level}."
        else:
            head += "; cleanup pass applied."
        self._lbl_build_status.setText(head)

        prev = self._result_text.toPlainText()
        sections = [prev] if prev else []
        if connect_summary:
            sections.append("\u2500\u2500 Auto-Connect \u2500\u2500\n" + connect_summary)
        if simplify_summary:
            sections.append("\u2500\u2500 Simplification \u2500\u2500\n" + simplify_summary)
        if island_summary:
            sections.append("\u2500\u2500 Isolated Cleanup \u2500\u2500\n" + island_summary)
        self._result_text.setPlainText("\n\n".join(sections))

        self._connected = True
        self.buildFinished.emit()


# =====================================================================
# Step 5: Simplify & Aggregate
# =====================================================================


class GridMappingConnectStep(QWidget):
    """Simplify and aggregate the network (Step 5).

    Progressive simplification levels (0-4) that produce electrically
    equivalent networks with controlled complexity reduction.
    """

    _LEVEL_ITEMS = [
        (0, "Level 0: Cleanup Only",
         "Remove isolated empty buses and self-loop lines."),
        (1, "Level 1: Equipment Aggregation + Parallel Lines",
         "Merge same-fuel generators/batteries by node + "
         "consolidate parallel transmission lines."),
        (2, "Level 2: Radial & Series Bus Elimination",
         "Level 1 + prune dead-end buses + eliminate pass-through "
         "buses (Kron reduction)."),
        (3, "Level 3: Intra-Node Bus Collapse",
         "Level 2 + collapse voltage levels within each node to "
         "a single bus (remove internal transformers)."),
        (4, "Level 4: Full Node Collapse",
         "Level 3 + collapse all buses per node to one + "
         "absorb negligible generators."),
    ]

    def __init__(self, model=None, parent=None):
        super().__init__(parent)
        self._model = model

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        layout.addWidget(QLabel(
            "<b>Step 5: Simplify & Aggregate</b><br>"
            "Progressive network reduction: clean up topology, "
            "aggregate equipment, and reduce buses to control "
            "problem complexity."
        ))

        # ── Simplify & Aggregate ─────────────────────────────────
        simplify_group = QGroupBox("Simplify & Aggregate")
        simplify_lay = QVBoxLayout(simplify_group)

        simplify_lay.addWidget(QLabel(
            "Select a simplification level. Higher levels include "
            "all operations from lower levels and apply increasingly "
            "aggressive reductions to the bus-level electrical graph."
        ))

        infra_form = QFormLayout()
        self._combo_infra_level = QComboBox()
        for lvl, label, tip in self._LEVEL_ITEMS:
            self._combo_infra_level.addItem(label, lvl)
        self._combo_infra_level.setCurrentIndex(1)
        tooltip_lines = [f"  {lbl}: {tip}" for _, lbl, tip in self._LEVEL_ITEMS]
        self._combo_infra_level.setToolTip("\n".join(tooltip_lines))
        self._combo_infra_level.setMinimumWidth(400)
        self._combo_infra_level.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents,
        )
        infra_form.addRow("Level:", self._combo_infra_level)
        simplify_lay.addLayout(infra_form)

        self._btn_infra_analyze = QPushButton("Analyze")
        self._btn_infra_analyze.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self._btn_infra_analyze.setEnabled(False)
        self._btn_infra_analyze.clicked.connect(self._do_analyze_infrastructure)
        simplify_lay.addWidget(self._btn_infra_analyze)

        # Summary label (before/after)
        self._lbl_summary = QLabel("")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setStyleSheet("color: #555; font-style: italic;")
        simplify_lay.addWidget(self._lbl_summary)

        self._infra_tree = QTreeWidget()
        self._infra_tree.setHeaderLabels([
            "Operation", "Type", "Details", "Elements",
        ])
        self._infra_tree.setMinimumHeight(160)
        self._infra_tree.setRootIsDecorated(True)
        self._infra_tree.setColumnWidth(0, 320)
        self._infra_tree.setColumnWidth(1, 100)
        self._infra_tree.setColumnWidth(2, 120)
        simplify_lay.addWidget(self._infra_tree)

        infra_btn_row = QHBoxLayout()
        self._btn_infra_select_all = QPushButton("Select All")
        self._btn_infra_select_all.clicked.connect(self._infra_select_all)
        infra_btn_row.addWidget(self._btn_infra_select_all)
        self._btn_infra_apply = QPushButton("Apply Selected")
        self._btn_infra_apply.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self._btn_infra_apply.setEnabled(False)
        self._btn_infra_apply.clicked.connect(self._do_apply_infrastructure)
        infra_btn_row.addWidget(self._btn_infra_apply)
        simplify_lay.addLayout(infra_btn_row)

        self._lbl_infra_status = QLabel("")
        self._lbl_infra_status.setWordWrap(True)
        simplify_lay.addWidget(self._lbl_infra_status)
        layout.addWidget(simplify_group)

        self._infra_suggestions: list = []
        self._topo_suggestions: list = []

        # ── Result log ───────────────────────────────────────────────
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(150)
        layout.addWidget(self._result_text, 1)

        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_inputs(self):
        """Enable action buttons when entering this step."""
        self._btn_infra_analyze.setEnabled(True)
        self._btn_infra_apply.setEnabled(False)
        self._infra_tree.clear()
        self._infra_suggestions.clear()
        self._topo_suggestions.clear()
        self._lbl_infra_status.setText("")
        self._lbl_summary.setText("")
        self._result_text.clear()

    def is_valid(self) -> bool:
        return True  # All actions are optional

    # ------------------------------------------------------------------
    # Simplify & Aggregate
    # ------------------------------------------------------------------

    def _do_analyze_infrastructure(self):
        from esfex.visualization.data.validation import (
            SimplificationConfig,
            find_simplifications_for_level,
        )

        self._infra_tree.clear()
        self._infra_suggestions.clear()
        self._topo_suggestions.clear()
        self._btn_infra_apply.setEnabled(False)
        self._btn_infra_analyze.setEnabled(False)
        self._lbl_summary.setText("")

        level = self._combo_infra_level.currentData()

        try:
            # ── Phase 1: Network cleanup (always) ─────────────────
            self._lbl_infra_status.setText(
                "Cleaning up network topology..."
            )
            state = self._model.state
            self._model.begin_bulk_update()
            try:
                with self._model.suspend_checkpoints():
                    n_removed, cleanup_log = _remove_empty_isolated_buses(
                        self._model, state,
                    )
            finally:
                self._model.end_bulk_update()

            cleanup_summary = "\n".join(cleanup_log)
            prev = self._result_text.toPlainText()
            self._result_text.setPlainText(
                prev + "\n\n── Network Cleanup ──\n" + cleanup_summary
            )

            if level == 0:
                if n_removed == 0:
                    self._lbl_infra_status.setText(
                        "Network is already clean — no changes needed."
                    )
                else:
                    self._lbl_infra_status.setText(
                        f"Removed {n_removed} empty element(s)."
                    )
                self._btn_infra_analyze.setEnabled(True)
                return

            # ── Phase 2: Full simplification analysis ─────────────
            self._lbl_infra_status.setText(
                f"Analyzing network (Level {level})..."
            )
            config = SimplificationConfig()
            plan = find_simplifications_for_level(
                self._model.state, level=level, config=config,
            )
            self._infra_suggestions = plan.infrastructure_suggestions
            self._topo_suggestions = plan.topology_suggestions

            # Show before/after summary
            self._lbl_summary.setText(
                f"Buses: {plan.buses_before} → {plan.buses_after}  |  "
                f"Lines: {plan.lines_before} → {plan.lines_after}  |  "
                f"Generators: {plan.generators_before} → {plan.generators_after}  |  "
                f"Transformers: {plan.transformers_before} → {plan.transformers_after}"
            )

            status_parts = []
            if n_removed > 0:
                status_parts.append(
                    f"Cleaned up {n_removed} element(s)."
                )

            has_suggestions = (
                plan.infrastructure_suggestions or plan.topology_suggestions
            )
            if not has_suggestions:
                status_parts.append(
                    "No simplifications found at this level."
                )
                self._lbl_infra_status.setText(" ".join(status_parts))
                self._btn_infra_analyze.setEnabled(True)
                return

            # ── Populate tree with grouped suggestions ────────────
            # Group 1: Equipment merges
            if plan.infrastructure_suggestions:
                group_item = QTreeWidgetItem(["Equipment Aggregation", "", "", ""])
                group_item.setFlags(
                    group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                group_item.setCheckState(0, Qt.CheckState.Unchecked)
                for i, s in enumerate(plan.infrastructure_suggestions):
                    child = QTreeWidgetItem()
                    child.setFlags(
                        child.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    child.setText(0, s.description)
                    child.setText(1, s.equipment_type)
                    child.setText(2, f"{s.total_rated_power:.1f} MW")
                    child.setText(3, f"-{s.reduction}")
                    child.setData(0, Qt.ItemDataRole.UserRole, ("infra", i))
                    group_item.addChild(child)
                self._infra_tree.addTopLevelItem(group_item)
                group_item.setExpanded(True)

            # Group 2+: Topology suggestions by action type
            _TOPO_LABELS = {
                "parallel_line_merge": "Parallel Line Consolidation",
                "radial_prune": "Radial Branch Pruning",
                "series_eliminate": "Series Bus Elimination (Kron)",
                "voltage_collapse": "Voltage Level Collapse",
                "full_node_collapse": "Full Node Collapse",
                "small_gen_absorb": "Small Generator Absorption",
            }
            # Group by action_type preserving order
            from collections import OrderedDict
            topo_groups: dict[str, list[tuple[int, object]]] = OrderedDict()
            for j, ts in enumerate(plan.topology_suggestions):
                topo_groups.setdefault(ts.action_type, []).append((j, ts))

            for action_type, items in topo_groups.items():
                label = _TOPO_LABELS.get(action_type, action_type)
                group_item = QTreeWidgetItem([label, "", "", ""])
                group_item.setFlags(
                    group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                group_item.setCheckState(0, Qt.CheckState.Unchecked)
                for j, ts in items:
                    child = QTreeWidgetItem()
                    child.setFlags(
                        child.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    child.setText(0, ts.description)
                    child.setText(1, action_type.replace("_", " "))
                    child.setText(2, f"L{ts.level}")
                    child.setText(3, f"-{ts.elements_removed}")
                    child.setData(0, Qt.ItemDataRole.UserRole, ("topo", j))
                    group_item.addChild(child)
                self._infra_tree.addTopLevelItem(group_item)
                group_item.setExpanded(True)

            self._btn_infra_apply.setEnabled(True)
            n_infra = len(plan.infrastructure_suggestions)
            n_topo = len(plan.topology_suggestions)
            status_parts.append(
                f"Found {n_infra} equipment merge(s) and "
                f"{n_topo} topology operation(s)."
            )
            self._lbl_infra_status.setText(" ".join(status_parts))

            desc_lines = (
                [s.description for s in plan.infrastructure_suggestions]
                + [s.description for s in plan.topology_suggestions]
            )
            prev = self._result_text.toPlainText()
            self._result_text.setPlainText(
                prev + f"\n\n── Level {level} Analysis ──\n"
                + "\n".join(desc_lines)
            )

        except Exception as exc:
            logger.exception("Simplify/analyze error")
            self._lbl_infra_status.setText(f"Error: {exc}")

        self._btn_infra_analyze.setEnabled(True)

    def _infra_select_all(self):
        for i in range(self._infra_tree.topLevelItemCount()):
            group = self._infra_tree.topLevelItem(i)
            group.setCheckState(0, Qt.CheckState.Checked)
            for c in range(group.childCount()):
                group.child(c).setCheckState(0, Qt.CheckState.Checked)

    def _do_apply_infrastructure(self):
        from esfex.visualization.data.validation import (
            SimplificationConfig,
            apply_simplification_level,
        )

        level = self._combo_infra_level.currentData()
        if level == 0:
            self._lbl_infra_status.setText("Level 0 cleanup already applied.")
            return

        self._btn_infra_apply.setEnabled(False)
        self._btn_infra_analyze.setEnabled(False)

        try:
            state = self._model.state
            n_bus_before = len(state.buses)
            n_line_before = len(state.transmission_lines)
            n_gen_before = len(state.generators)
            n_bat_before = len(state.batteries)
            n_trafo_before = len(state.transformers)

            self._lbl_infra_status.setText(
                f"Applying Level {level} simplification (iterating)..."
            )

            with self._model.suspend_checkpoints():
                log, remaining = apply_simplification_level(
                    self._model, level, SimplificationConfig(),
                )

            n_bus_after = len(state.buses)
            n_line_after = len(state.transmission_lines)
            n_gen_after = len(state.generators)
            n_bat_after = len(state.batteries)
            n_trafo_after = len(state.transformers)

            self._model.stateLoaded.emit()

            status = (
                f"Buses: {n_bus_before}→{n_bus_after}, "
                f"Lines: {n_line_before}→{n_line_after}, "
                f"Generators: {n_gen_before}→{n_gen_after}, "
                f"Batteries: {n_bat_before}→{n_bat_after}, "
                f"Transformers: {n_trafo_before}→{n_trafo_after}."
            )
            if remaining:
                n_err = sum(1 for i in remaining if i.severity == "error")
                if n_err:
                    status += f" {n_err} issue(s) remaining."
            self._lbl_infra_status.setText(status)

            prev = self._result_text.toPlainText()
            self._result_text.setPlainText(
                prev + "\n\n" + "\n".join(log)
            )

            # Clear tree since suggestions are now stale
            self._infra_tree.clear()
            self._infra_suggestions.clear()
            self._topo_suggestions.clear()
            self._lbl_summary.setText("")
            # Re-enable Analyze so the user can re-run
            self._btn_infra_analyze.setEnabled(True)

        except Exception as exc:
            logger.exception("Infrastructure apply error")
            self._lbl_infra_status.setText(f"Error: {exc}")
            self._btn_infra_apply.setEnabled(True)
            self._btn_infra_analyze.setEnabled(True)


# =====================================================================
# Graph-based auto-connect helpers
# =====================================================================


class _NetworkIndices:
    """Pre-computed network indices shared across audit/check functions.

    Building these once per iteration instead of 3-4 times avoids
    redundant O(L) passes over transmission lines (L can be thousands).
    """

    __slots__ = (
        "adj", "components", "bus_to_comp",
        "lines_by_from_ep", "lines_by_to_ep",
        "tr_by_from_bus", "tr_by_to_bus",
    )

    def __init__(self, state):
        from collections import defaultdict

        # ── Bus adjacency & connected components ──────────────────
        self.adj = _build_bus_adjacency(state)
        self.components = _find_connected_components(self.adj)
        self.bus_to_comp: dict[str, set[str]] = {}
        for comp in self.components:
            for bid in comp:
                self.bus_to_comp[bid] = comp

        # ── Line endpoint indices ─────────────────────────────────
        self.lines_by_from_ep: dict[tuple[str, str], list] = defaultdict(list)
        self.lines_by_to_ep: dict[tuple[str, str], list] = defaultdict(list)
        for ln in state.transmission_lines:
            if ln.from_endpoint:
                key = (ln.from_endpoint.element_type,
                       ln.from_endpoint.element_id)
                self.lines_by_from_ep[key].append(ln)
            if ln.to_endpoint:
                key = (ln.to_endpoint.element_type,
                       ln.to_endpoint.element_id)
                self.lines_by_to_ep[key].append(ln)

        # ── Transformer indices ───────────────────────────────────
        self.tr_by_from_bus: dict[str, list[tuple[int, object]]] = defaultdict(
            list,
        )
        self.tr_by_to_bus: dict[str, list[tuple[int, object]]] = defaultdict(
            list,
        )
        for i, tr in enumerate(state.transformers):
            self.tr_by_from_bus[tr.from_bus].append((i, tr))
            self.tr_by_to_bus[tr.to_bus].append((i, tr))


def _build_bus_adjacency(state) -> dict[str, set[str]]:
    """Build an undirected bus adjacency graph from ALL connection types."""
    adj: dict[str, set[str]] = {bid: set() for bid in state.buses}
    for ln in state.transmission_lines:
        if ln.from_bus in adj and ln.to_bus in adj:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus in adj and tr.to_bus in adj:
            adj[tr.from_bus].add(tr.to_bus)
            adj[tr.to_bus].add(tr.from_bus)
    for c in state.acdc_converters:
        if c.from_bus in adj and c.to_bus in adj:
            adj[c.from_bus].add(c.to_bus)
            adj[c.to_bus].add(c.from_bus)
    for c in state.freq_converters:
        if c.from_bus in adj and c.to_bus in adj:
            adj[c.from_bus].add(c.to_bus)
            adj[c.to_bus].add(c.from_bus)
    return adj


def _find_connected_components(
    adj: dict[str, set[str]],
) -> list[set[str]]:
    """BFS connected components on the bus adjacency graph."""
    visited: set[str] = set()
    components: list[set[str]] = []
    for start in adj:
        if start in visited:
            continue
        comp: set[str] = set()
        queue = deque([start])
        while queue:
            bid = queue.popleft()
            if bid in visited:
                continue
            visited.add(bid)
            comp.add(bid)
            for nb in adj.get(bid, ()):
                if nb not in visited:
                    queue.append(nb)
        if comp:
            components.append(comp)
    return components


def _bus_has_any_equipment(state, bus_id: str) -> bool:
    """Return True if *any* equipment is assigned to this bus.

    Unlike the validation helper, this does NOT check rated_power > 0.
    After grid mapping, many assets have unknown capacity (0 MW) but are
    real infrastructure that must be preserved.

    Checks generators, batteries, electrolyzers, transformers, and
    converters (AC/DC and frequency).
    """
    if any(g.bus == bus_id for g in state.generators.values()):
        return True
    if any(b.bus == bus_id for b in state.batteries.values()):
        return True
    if any(e.bus == bus_id for e in state.electrolyzers.values()):
        return True
    if any(
        tr.from_bus == bus_id or tr.to_bus == bus_id
        for tr in state.transformers
    ):
        return True
    if any(
        c.from_bus == bus_id or c.to_bus == bus_id
        for c in state.acdc_converters
    ):
        return True
    if any(
        c.from_bus == bus_id or c.to_bus == bus_id
        for c in state.freq_converters
    ):
        return True
    return False


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def iterative_auto_connect(
    model, state,
    *,
    max_iterations: int = 20,
    voltage_mismatch_ratio: float = 1.5,
    lv_voltage_kv: float = 0.48,
    max_connection_km: float = 100.0,
) -> tuple[int, list[str]]:
    """Iteratively connect and validate the network until electrically consistent.

    Uses **element-by-element** auditing: every equipment item, transformer,
    and converter is individually checked for a complete connection chain.

    Phase order per iteration (connectivity first, then element chains):

      1-2. Audit + fix voltage mismatches (TR chain replacement)
      3-4. Audit + fix connectivity (bridge isolated components)
      5-6. Audit + fix transformer connection lines
      7-8. Audit + fix converter connection lines (AC/DC + frequency)
     9-10. Audit + fix equipment chains (element-by-element)
       11. Sync transformer voltages with their bus voltages

    Connectivity is fixed BEFORE equipment chains so that the main
    component is as large as possible when equipment targets are chosen.

    After convergence, a **final verification** re-audits all equipment,
    transformers, and converters and reports any remaining failures.

    Returns ``(total_elements_created, full_log)``.
    """
    log: list[str] = []
    total_created = 0

    for iteration in range(1, max_iterations + 1):
        log.append(f"── Iteration {iteration} ──")
        created = 0

        # Build shared indices ONCE per iteration (avoids 3-4x
        # redundant O(L) passes over transmission lines).
        idx = _NetworkIndices(state)

        # ── Phase 1-2: Voltage mismatches ────────────────────────
        volt_issues = _check_voltage_consistency(
            state, voltage_mismatch_ratio,
        )
        if volt_issues:
            log.append(f"  Voltage audit: {len(volt_issues)} mismatches")
            n, fix_log = _fix_voltage_mismatches(model, state, volt_issues)
            created += n
            log.extend(fix_log)

        # ── Phase 3-4: Connectivity ──────────────────────────────
        conn_issues = _check_connectivity(state, idx=idx)
        if conn_issues:
            log.append(
                f"  Connectivity audit: {len(conn_issues)} "
                f"isolated components"
            )
            n, fix_log = _fix_disconnected_components(
                model, state, conn_issues,
                lv_voltage_kv=lv_voltage_kv,
                max_connection_km=max_connection_km,
            )
            created += n
            log.extend(fix_log)

        # ── Phase 5-6: Transformer connection lines ──────────────
        tr_audits = _audit_all_transformers(state, idx=idx)
        failed_tr = [a for a in tr_audits if not a["ok"]]
        if failed_tr:
            n_tr_ok = len(tr_audits) - len(failed_tr)
            log.append(
                f"  Transformer audit: {n_tr_ok}/{len(tr_audits)} OK, "
                f"{len(failed_tr)} missing lines"
            )
            for a in failed_tr:
                log.append(
                    f"    TR[{a['tr_idx']}]: missing {a['missing_sides']}"
                )
            n, fix_log = _fix_transformer_lines(model, state, failed_tr)
            created += n
            log.extend(fix_log)

        # ── Phase 7-8: Converter connection lines ────────────────
        conv_audits = _audit_all_converters(state, idx=idx)
        failed_conv = [a for a in conv_audits if not a["ok"]]
        if failed_conv:
            n_conv_ok = len(conv_audits) - len(failed_conv)
            log.append(
                f"  Converter audit: {n_conv_ok}/{len(conv_audits)} OK, "
                f"{len(failed_conv)} missing lines"
            )
            for a in failed_conv:
                label = a["conv_type"].replace("_", " ").upper()
                log.append(
                    f"    {label}[{a['conv_idx']}]: "
                    f"missing {a['missing_sides']}"
                )
            n, fix_log = _fix_converter_lines(model, state, failed_conv)
            created += n
            log.extend(fix_log)

        # ── Phase 9-10: Equipment chains (element-by-element) ────
        equip_audits = _audit_all_equipment(state, lv_voltage_kv, idx=idx)
        failed_equip = [a for a in equip_audits if not a["chain_complete"]]
        n_equip_ok = len(equip_audits) - len(failed_equip)
        log.append(
            f"  Equipment audit: {n_equip_ok}/{len(equip_audits)} OK, "
            f"{len(failed_equip)} failed"
        )
        if failed_equip:
            for a in failed_equip:
                log.append(
                    f"    {a['etype']} {a['eid']}: {a['failure_reason']}"
                )
            n, fix_log = _fix_unchained_equipment(
                model, state, failed_equip,
                lv_voltage_kv=lv_voltage_kv,
                max_connection_km=max_connection_km,
            )
            created += n
            log.extend(fix_log)

        # ── Phase 11: Sync transformer voltages ──────────────────
        n_sync, sync_log = _sync_transformer_voltages(state)
        if n_sync:
            log.extend(sync_log)

        total_created += created
        log.append(f"  Fixed: {created} element(s) created/modified")

        if created == 0:
            if not failed_equip and not failed_tr and not failed_conv:
                log.append(
                    "  All audits passed, no fixes needed — converged."
                )
            else:
                log.append(
                    "  No fixes applied (remaining failures are "
                    "unfixable) — stopping."
                )
            break
    else:
        log.append(f"Reached max iterations ({max_iterations}).")

    # ── Final verification ────────────────────────────────────────
    log.append("── Final Verification ──")
    final_idx = _NetworkIndices(state)
    final_audits = _audit_all_equipment(state, lv_voltage_kv, idx=final_idx)
    final_failed = [a for a in final_audits if not a["chain_complete"]]
    final_ok = len(final_audits) - len(final_failed)

    final_tr = _audit_all_transformers(state, idx=final_idx)
    final_tr_failed = [a for a in final_tr if not a["ok"]]
    final_tr_ok = len(final_tr) - len(final_tr_failed)

    final_conv = _audit_all_converters(state, idx=final_idx)
    final_conv_failed = [a for a in final_conv if not a["ok"]]
    final_conv_ok = len(final_conv) - len(final_conv_failed)

    log.append(
        f"  Equipment: {final_ok}/{len(final_audits)} fully chained"
    )
    if final_failed:
        for a in final_failed:
            log.append(
                f"    STILL FAILED: {a['etype']} {a['eid']}: "
                f"{a['failure_reason']}"
            )
    log.append(
        f"  Transformers: {final_tr_ok}/{len(final_tr)} fully connected"
    )
    if final_tr_failed:
        for a in final_tr_failed:
            log.append(
                f"    STILL FAILED: TR[{a['tr_idx']}]: "
                f"missing {a['missing_sides']}"
            )
    log.append(
        f"  Converters: {final_conv_ok}/{len(final_conv)} fully connected"
    )
    if final_conv_failed:
        for a in final_conv_failed:
            label = a["conv_type"].replace("_", " ").upper()
            log.append(
                f"    STILL FAILED: {label}[{a['conv_idx']}]: "
                f"missing {a['missing_sides']}"
            )

    log.append(f"Total elements created: {total_created}")
    return total_created, log


# =====================================================================
# Check functions (read-only analysis)
# =====================================================================


def _check_connectivity(
    state, *, idx: _NetworkIndices | None = None,
) -> list[dict]:
    """Find disconnected components via BFS.

    Returns one issue dict per isolated component::

        {"type": "disconnected", "component": set[str],
         "equipment": [(etype, eid, obj), ...]}
    """
    if idx is not None:
        components = idx.components
    else:
        adj = _build_bus_adjacency(state)
        components = _find_connected_components(adj)

    if len(components) <= 1:
        return []

    main_comp = max(components, key=len)
    issues: list[dict] = []

    for comp in components:
        if comp is main_comp:
            continue

        # Collect equipment in this component.
        equip: list[tuple[str, str, object]] = []
        for gid, g in state.generators.items():
            if g.bus in comp:
                equip.append(("generator", gid, g))
        for bid, b in state.batteries.items():
            if b.bus in comp:
                equip.append(("battery", bid, b))
        for eid, e in state.electrolyzers.items():
            if e.bus in comp:
                equip.append(("electrolyzer", eid, e))
        for i, c in enumerate(state.acdc_converters):
            if c.from_bus in comp or c.to_bus in comp:
                equip.append(("acdc_converter", str(i), c))
        for i, c in enumerate(state.freq_converters):
            if c.from_bus in comp or c.to_bus in comp:
                equip.append(("freq_converter", str(i), c))

        issues.append({
            "type": "disconnected",
            "component": comp,
            "equipment": equip,
        })

    return issues


def _check_voltage_consistency(
    state, ratio_threshold: float = 1.5,
) -> list[dict]:
    """Find bus-to-bus lines that cross voltage levels without a transformer.

    Only checks lines where **both** endpoints are ``EndpointRef("bus", ...)``.
    Internal chain lines (equipment↔bus, bus↔transformer) are exempt.

    Returns::

        {"type": "voltage_mismatch", "line_id": str,
         "from_bus": str, "to_bus": str, "v_from": float, "v_to": float}
    """
    issues: list[dict] = []

    for ln in state.transmission_lines:
        # Only check bus-to-bus lines.
        if not ln.from_endpoint or not ln.to_endpoint:
            continue
        if ln.from_endpoint.element_type != "bus":
            continue
        if ln.to_endpoint.element_type != "bus":
            continue
        # Skip if same bus (shouldn't happen for bus-to-bus but be safe).
        if ln.from_bus == ln.to_bus:
            continue

        fb = state.buses.get(ln.from_bus)
        tb = state.buses.get(ln.to_bus)
        if not fb or not tb:
            continue

        v_from = fb.voltage_kv or 110.0
        v_to = tb.voltage_kv or 110.0
        v_high = max(v_from, v_to)
        v_low = min(v_from, v_to)
        ratio = v_high / v_low if v_low > 0 else 1.0

        if ratio >= ratio_threshold:
            issues.append({
                "type": "voltage_mismatch",
                "line_id": ln.line_id,
                "from_bus": ln.from_bus,
                "to_bus": ln.to_bus,
                "v_from": v_from,
                "v_to": v_to,
            })

    return issues


def _audit_all_equipment(
    state, lv_voltage_kv: float = 0.48,
    *, idx: _NetworkIndices | None = None,
) -> list[dict]:
    """Element-by-element audit of every equipment item's connection chain.

    For EACH generator/battery/electrolyzer, verify the full chain::

        equipment ── line ── LV_bus(≤lv_voltage_kv) ── line ── transformer ── line ── HV_bus

    Checks per element:
      1. Connection line exists (line with from_endpoint matching the equipment)
      2. Equipment is on an LV bus (voltage_kv ≤ lv_voltage_kv)
      3. That LV bus appears in a transformer endpoint
      4. Transformer has a line on its LV side (EndpointRef → transformer on from_bus)
      5. Transformer has a line on its HV side (EndpointRef → transformer on to_bus)
      6. HV bus is in the main connected component

    Returns one dict per equipment item with ``chain_complete: bool`` and
    ``failure_reason: str`` (empty when complete).
    """
    # ── Use precomputed indices or build from scratch ──────────────
    if idx is None:
        idx = _NetworkIndices(state)

    lines_by_from_ep = idx.lines_by_from_ep
    tr_by_from_bus = idx.tr_by_from_bus
    bus_to_comp = idx.bus_to_comp

    # ── Collect all equipment ─────────────────────────────────────
    equipment: list[tuple[str, str, object]] = []
    for gid, g in state.generators.items():
        equipment.append(("generator", gid, g))
    for bid, b in state.batteries.items():
        equipment.append(("battery", bid, b))
    for eid, e in state.electrolyzers.items():
        equipment.append(("electrolyzer", eid, e))

    # ── Audit each element ────────────────────────────────────────
    results: list[dict] = []
    for etype, eid, obj in equipment:
        audit = {
            "etype": etype, "eid": eid, "obj": obj,
            "bus_id": obj.bus,
            "chain_complete": False, "failure_reason": "",
        }

        # Check 1: connection line from equipment to a bus
        conn_lines = lines_by_from_ep.get((etype, eid), [])
        bus_lines = [
            ln for ln in conn_lines
            if ln.to_endpoint and ln.to_endpoint.element_type == "bus"
        ]
        if not bus_lines:
            audit["failure_reason"] = "no connection line from equipment to bus"
            results.append(audit)
            continue

        # The LV bus is the bus the equipment connects to via the line
        lv_bus_id = bus_lines[0].to_endpoint.element_id

        # Check 2: equipment is on an LV bus
        lv_bus = state.buses.get(lv_bus_id)
        if not lv_bus:
            audit["failure_reason"] = f"LV bus {lv_bus_id} not found"
            results.append(audit)
            continue
        if lv_bus.voltage_kv > lv_voltage_kv:
            audit["failure_reason"] = (
                f"bus {lv_bus_id} voltage {lv_bus.voltage_kv}kV > "
                f"LV threshold {lv_voltage_kv}kV"
            )
            results.append(audit)
            continue

        # Check 3: LV bus has a transformer
        # The transformer should have from_bus == lv_bus_id
        trs_on_lv = tr_by_from_bus.get(lv_bus_id, [])
        if not trs_on_lv:
            audit["failure_reason"] = (
                f"LV bus {lv_bus_id} has no transformer (from_bus side)"
            )
            results.append(audit)
            continue

        # Take the first transformer on this LV bus
        tr_idx, tr_obj = trs_on_lv[0]
        tr_id_str = str(tr_idx)

        # Check 4: line from LV bus to transformer
        lv_to_tr_lines = [
            ln for ln in lines_by_from_ep.get(("bus", lv_bus_id), [])
            if (ln.to_endpoint
                and ln.to_endpoint.element_type == "transformer"
                and ln.to_endpoint.element_id == tr_id_str)
        ]
        if not lv_to_tr_lines:
            audit["failure_reason"] = (
                f"no line from bus:{lv_bus_id} to transformer:{tr_id_str}"
            )
            results.append(audit)
            continue

        # Check 5: line from transformer to HV bus
        hv_bus_id = tr_obj.to_bus
        tr_to_hv_lines = [
            ln for ln in lines_by_from_ep.get(("transformer", tr_id_str), [])
            if (ln.to_endpoint
                and ln.to_endpoint.element_type == "bus"
                and ln.to_endpoint.element_id == hv_bus_id)
        ]
        if not tr_to_hv_lines:
            audit["failure_reason"] = (
                f"no line from transformer:{tr_id_str} to bus:{hv_bus_id}"
            )
            results.append(audit)
            continue

        # Check 6: HV bus is in the same connected component as the LV bus
        # (validates the chain is internally reachable, whether in main
        # component or a separate island network)
        lv_comp = bus_to_comp.get(lv_bus_id, set())
        if hv_bus_id not in lv_comp:
            audit["failure_reason"] = (
                f"HV bus {hv_bus_id} not reachable from LV bus {lv_bus_id}"
            )
            results.append(audit)
            continue

        # All checks passed
        audit["chain_complete"] = True
        results.append(audit)

    return results


def _audit_all_transformers(
    state, *, idx: _NetworkIndices | None = None,
) -> list[dict]:
    """Element-by-element audit of every transformer's connection lines.

    For EACH transformer, verify:
      1. A line exists with ``EndpointRef("bus", from_bus) → EndpointRef("transformer", str(idx))``
      2. A line exists with ``EndpointRef("transformer", str(idx)) → EndpointRef("bus", to_bus)``

    Returns one dict per transformer with ``ok: bool`` and
    ``missing_sides: list[str]`` (containing "from" and/or "to").
    """
    if idx is None:
        idx = _NetworkIndices(state)

    lines_by_to_ep = idx.lines_by_to_ep
    lines_by_from_ep = idx.lines_by_from_ep

    results: list[dict] = []
    for i, tr in enumerate(state.transformers):
        tr_id_str = str(i)
        missing: list[str] = []

        # Check from-side: line from bus:from_bus → transformer:i
        from_lines = [
            ln for ln in lines_by_to_ep.get(("transformer", tr_id_str), [])
            if (ln.from_endpoint
                and ln.from_endpoint.element_type == "bus"
                and ln.from_endpoint.element_id == tr.from_bus)
        ]
        if not from_lines:
            missing.append("from")

        # Check to-side: line from transformer:i → bus:to_bus
        to_lines = [
            ln for ln in lines_by_from_ep.get(("transformer", tr_id_str), [])
            if (ln.to_endpoint
                and ln.to_endpoint.element_type == "bus"
                and ln.to_endpoint.element_id == tr.to_bus)
        ]
        if not to_lines:
            missing.append("to")

        results.append({
            "tr_idx": i, "tr": tr,
            "ok": len(missing) == 0,
            "missing_sides": missing,
        })

    return results


def _audit_all_converters(
    state, *, idx: _NetworkIndices | None = None,
) -> list[dict]:
    """Element-by-element audit of every converter's connection lines.

    Checks both AC/DC converters and frequency converters.  For EACH
    converter, verify:
      1. A line exists with ``EndpointRef("bus", from_bus) →
         EndpointRef("<conv_type>", str(idx))``
      2. A line exists with ``EndpointRef("<conv_type>", str(idx)) →
         EndpointRef("bus", to_bus)``

    Returns one dict per converter with ``ok: bool`` and
    ``missing_sides: list[str]`` (containing "from" and/or "to").
    """
    if idx is None:
        idx = _NetworkIndices(state)

    lines_by_to_ep = idx.lines_by_to_ep
    lines_by_from_ep = idx.lines_by_from_ep

    results: list[dict] = []

    for conv_type, conv_list in [
        ("acdc_converter", state.acdc_converters),
        ("freq_converter", state.freq_converters),
    ]:
        for i, conv in enumerate(conv_list):
            conv_id_str = str(i)
            missing: list[str] = []

            # Check from-side: line from bus:from_bus → converter:i
            from_lines = [
                ln for ln in lines_by_to_ep.get((conv_type, conv_id_str), [])
                if (ln.from_endpoint
                    and ln.from_endpoint.element_type == "bus"
                    and ln.from_endpoint.element_id == conv.from_bus)
            ]
            if not from_lines:
                missing.append("from")

            # Check to-side: line from converter:i → bus:to_bus
            to_lines = [
                ln for ln in lines_by_from_ep.get((conv_type, conv_id_str), [])
                if (ln.to_endpoint
                    and ln.to_endpoint.element_type == "bus"
                    and ln.to_endpoint.element_id == conv.to_bus)
            ]
            if not to_lines:
                missing.append("to")

            results.append({
                "conv_type": conv_type,
                "conv_idx": i,
                "conv": conv,
                "ok": len(missing) == 0,
                "missing_sides": missing,
            })

    return results


# =====================================================================
# Fix functions (modify model)
# =====================================================================


def _fix_disconnected_components(
    model, state, issues: list[dict],
    *, lv_voltage_kv: float = 0.48,
    max_connection_km: float = float("inf"),
) -> tuple[int, list[str]]:
    """Bridge each disconnected component to the main network.

    For components **with equipment**: creates auto-complete chains
    (equipment → line → LV_bus → line → TR → line → HV_bus).

    For components **without equipment**: creates a bus-to-bus bridge
    line to the nearest same-voltage bus in the main component.

    When *max_connection_km* is set, components beyond that distance
    from the main network are NOT bridged — they form independent
    local networks instead.  Equipment in those components will search
    for the nearest HV bus among ALL buses within range.

    Returns ``(elements_created, log_lines)``.
    """
    from esfex.visualization.data.gui_model import (
        EndpointRef,
        GuiTransmissionLine,
    )

    SAFETY_FACTOR = 1.2
    DEFAULT_LV_KV = lv_voltage_kv
    DEFAULT_CAPACITY_MW = 1.0
    LV_FRACTION = 0.25
    TR_FRACTION = 0.65
    MIN_CHAIN_SPREAD = 0.003

    adj = _build_bus_adjacency(state)
    components = _find_connected_components(adj)
    main_comp = max(components, key=len) if components else set()

    log: list[str] = []
    created = 0

    for issue in issues:
        comp = issue["component"]
        equip = issue["equipment"]

        # Separate single-bus equipment (gen/bat/elec) from two-bus
        # elements (converters).  Only single-bus equipment needs the
        # LV→TR→HV chain; converter connection lines are handled by
        # _fix_converter_lines in a separate phase.
        _TWO_BUS = frozenset({"acdc_converter", "freq_converter"})
        single_bus = [
            (et, eid, obj) for et, eid, obj in equip
            if et not in _TWO_BUS
        ]

        if single_bus:
            # Group single-bus equipment by bus.
            by_bus: dict[str, list[tuple[str, str, object]]] = {}
            for etype, eid, obj in single_bus:
                by_bus.setdefault(obj.bus, []).append((etype, eid, obj))

            for bus_id, group in by_bus.items():
                bus = state.buses.get(bus_id)
                if not bus or (
                    bus.latitude == 0.0 and bus.longitude == 0.0
                ):
                    continue

                eq_lat, eq_lng = bus.latitude, bus.longitude

                # Find nearest HV bus strictly in the main component.
                # If beyond max_connection_km, skip — local connections
                # are handled by _fix_unchained_equipment later.
                best_hv, best_dist = _find_nearest_hv_bus_in(
                    state, eq_lat, eq_lng,
                    candidates=main_comp, min_voltage_kv=DEFAULT_LV_KV,
                )

                if best_hv is None or best_dist > max_connection_km:
                    if best_hv is not None:
                        log.append(
                            f"  {bus_id}: {best_dist:.0f} km > max "
                            f"{max_connection_km:.0f} km — local network"
                        )
                    else:
                        log.append(
                            f"  {bus_id}: no HV target in main, skipped."
                        )
                    continue

                tgt = state.buses[best_hv]

                # Direction from equipment toward HV bus.
                if eq_lat == tgt.latitude and eq_lng == tgt.longitude:
                    uy, ux = 0.0, 1.0
                else:
                    dy = tgt.latitude - eq_lat
                    dx = tgt.longitude - eq_lng
                    norm = math.sqrt(dy * dy + dx * dx) or 1e-9
                    uy, ux = dy / norm, dx / norm

                dist_deg = math.sqrt(
                    (tgt.latitude - eq_lat) ** 2
                    + (tgt.longitude - eq_lng) ** 2
                )

                if dist_deg >= 3 * MIN_CHAIN_SPREAD:
                    lv_lat = eq_lat + (tgt.latitude - eq_lat) * LV_FRACTION
                    lv_lng = eq_lng + (tgt.longitude - eq_lng) * LV_FRACTION
                    tr_lat = eq_lat + (tgt.latitude - eq_lat) * TR_FRACTION
                    tr_lng = eq_lng + (tgt.longitude - eq_lng) * TR_FRACTION
                else:
                    spacing = MIN_CHAIN_SPREAD
                    lv_lat = eq_lat + uy * spacing
                    lv_lng = eq_lng + ux * spacing
                    tr_lat = eq_lat + uy * 2 * spacing
                    tr_lng = eq_lng + ux * 2 * spacing

                total_mw = sum(
                    getattr(o, "rated_power", 0.0) for _, _, o in group
                )
                if total_mw <= 0:
                    total_mw = DEFAULT_CAPACITY_MW
                tr_cap = total_mw * SAFETY_FACTOR
                equip_node = group[0][2].node

                try:
                    n_g = sum(1 for t, _, _ in group if t == "generator")
                    n_b = sum(1 for t, _, _ in group if t == "battery")
                    n_e = sum(1 for t, _, _ in group if t == "electrolyzer")
                    parts = []
                    if n_g:
                        parts.append(f"{n_g}gen")
                    if n_b:
                        parts.append(f"{n_b}bat")
                    if n_e:
                        parts.append(f"{n_e}elec")
                    summary = ",".join(parts) or "equip"

                    new_lv = model.add_bus(
                        parent_node=equip_node,
                        name=f"Auto LV ({summary})",
                        voltage_kv=DEFAULT_LV_KV,
                        latitude=lv_lat, longitude=lv_lng,
                    )
                    created += 1

                    tr_idx = model.add_transformer(
                        name=f"Auto TR {new_lv}→{best_hv}",
                        from_bus=new_lv, to_bus=best_hv,
                        from_voltage_kv=DEFAULT_LV_KV,
                        to_voltage_kv=tgt.voltage_kv,
                        rated_power_mva=tr_cap,
                        latitude=tr_lat, longitude=tr_lng,
                    )
                    created += 1

                    for etype, eid, obj in group:
                        rated = (
                            getattr(obj, "rated_power", 0.0)
                            or DEFAULT_CAPACITY_MW
                        )
                        model.add_line(
                            from_bus=new_lv, to_bus=new_lv,
                            capacity_mw=rated,
                            from_endpoint=EndpointRef(etype, eid),
                            to_endpoint=EndpointRef("bus", new_lv),
                        )
                        created += 1

                    model.add_line(
                        from_bus=new_lv, to_bus=new_lv,
                        capacity_mw=tr_cap,
                        from_endpoint=EndpointRef("bus", new_lv),
                        to_endpoint=EndpointRef(
                            "transformer", str(tr_idx),
                        ),
                    )
                    created += 1

                    model.add_line(
                        from_bus=best_hv, to_bus=best_hv,
                        capacity_mw=tr_cap,
                        from_endpoint=EndpointRef(
                            "transformer", str(tr_idx),
                        ),
                        to_endpoint=EndpointRef("bus", best_hv),
                    )
                    created += 1

                    for _, _, obj in group:
                        obj.bus = new_lv

                    log.append(
                        f"  Chain: {summary} on {bus_id} → LV {new_lv}"
                        f" → TR → {best_hv} ({tgt.voltage_kv:.0f}kV)"
                        f"  [{best_dist:.1f} km]"
                    )

                except Exception as exc:
                    log.append(f"  Error chain {bus_id}: {exc}")
                    logger.exception("Chain error for bus %s", bus_id)

        else:
            # ── Empty component: bus-to-bus bridge ───────────────
            best_iso, best_main_bid, best_dist = _find_closest_bus_pair(
                state, comp, main_comp,
            )
            if best_iso is None or best_main_bid is None:
                log.append(
                    f"  Empty component ({len(comp)} buses): "
                    f"no valid coordinates for bridging."
                )
                continue

            if best_dist > max_connection_km:
                log.append(
                    f"  Empty component ({len(comp)} buses): "
                    f"{best_dist:.0f} km > max {max_connection_km:.0f} km"
                    f" — kept as separate network."
                )
                continue

            iso_bus = state.buses[best_iso]
            main_bus = state.buses[best_main_bid]
            v = max(iso_bus.voltage_kv, main_bus.voltage_kv) or 110.0
            cap = _estimate_bridge_capacity(v)

            lid = f"line_{state._next_line_id}"
            state._next_line_id += 1
            state.transmission_lines.append(GuiTransmissionLine(
                line_id=lid,
                from_bus=best_main_bid, to_bus=best_iso,
                from_node=main_bus.parent_node,
                to_node=iso_bus.parent_node,
                capacity_mw=cap, voltage_kv=v, waypoints=[],
                from_endpoint=EndpointRef("bus", best_main_bid),
                to_endpoint=EndpointRef("bus", best_iso),
            ))
            created += 1

            log.append(
                f"  Bridge: {best_iso} → {best_main_bid}"
                f"  [{best_dist:.1f} km, {cap:.0f} MW]"
            )

        # Merge into main for subsequent issues.
        main_comp.update(comp)

    return created, log


def _fix_voltage_mismatches(
    model, state, issues: list[dict],
) -> tuple[int, list[str]]:
    """Replace voltage-mismatched bus-to-bus lines with transformer chains.

    For each mismatched line::

        bus_high ─── line ─── bus_low          (BEFORE)
        bus_high ─── line ─── TR ─── line ─── bus_low  (AFTER)

    Returns ``(elements_created, log_lines)``.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    log: list[str] = []
    created = 0

    for issue in issues:
        line_id = issue["line_id"]
        from_bus_id = issue["from_bus"]
        to_bus_id = issue["to_bus"]
        v_from = issue["v_from"]
        v_to = issue["v_to"]

        # Verify line still exists (previous fix may have removed it).
        ln = None
        for candidate in state.transmission_lines:
            if candidate.line_id == line_id:
                ln = candidate
                break
        if ln is None:
            continue

        fb = state.buses.get(from_bus_id)
        tb = state.buses.get(to_bus_id)
        if not fb or not tb:
            continue

        # Determine HV / LV sides.
        if v_from >= v_to:
            hv_bus_id, lv_bus_id = from_bus_id, to_bus_id
            hv_bus, lv_bus = fb, tb
            v_hv, v_lv = v_from, v_to
        else:
            hv_bus_id, lv_bus_id = to_bus_id, from_bus_id
            hv_bus, lv_bus = tb, fb
            v_hv, v_lv = v_to, v_from

        cap = ln.capacity_mw or _estimate_bridge_capacity(v_hv)

        # Position transformer midway.
        tr_lat = (hv_bus.latitude + lv_bus.latitude) / 2
        tr_lng = (hv_bus.longitude + lv_bus.longitude) / 2

        try:
            # Remove the direct line.
            model.remove_line(line_id)

            # Create transformer.
            tr_idx = model.add_transformer(
                name=f"Auto TR {v_hv:.1f}/{v_lv:.1f}kV",
                from_bus=hv_bus_id, to_bus=lv_bus_id,
                from_voltage_kv=v_hv, to_voltage_kv=v_lv,
                rated_power_mva=cap,
                latitude=tr_lat, longitude=tr_lng,
            )
            created += 1

            # Line: HV bus → transformer.
            model.add_line(
                from_bus=hv_bus_id, to_bus=hv_bus_id,
                capacity_mw=cap,
                from_endpoint=EndpointRef("bus", hv_bus_id),
                to_endpoint=EndpointRef("transformer", str(tr_idx)),
            )
            created += 1

            # Line: transformer → LV bus.
            model.add_line(
                from_bus=lv_bus_id, to_bus=lv_bus_id,
                capacity_mw=cap,
                from_endpoint=EndpointRef("transformer", str(tr_idx)),
                to_endpoint=EndpointRef("bus", lv_bus_id),
            )
            created += 1

            log.append(
                f"  Voltage fix: {line_id} replaced with "
                f"TR {v_hv:.1f}/{v_lv:.1f}kV "
                f"({hv_bus_id} → {lv_bus_id})"
            )

        except Exception as exc:
            log.append(f"  Error fixing voltage {line_id}: {exc}")
            logger.exception("Voltage fix error for line %s", line_id)

    return created, log


def _fix_unchained_equipment(
    model, state, failed_audits: list[dict],
    *, lv_voltage_kv: float = 0.48,
    max_connection_km: float = float("inf"),
) -> tuple[int, list[str]]:
    """Create auto-complete chains for equipment that failed the audit.

    For each equipment item:
      1. Clean up any existing connection lines.
      2. Find nearest HV bus in the main connected component (within
         *max_connection_km*).  If too far, search ALL buses within range.
      3. Create the complete chain:
         ``equipment → line → LV_bus → line → transformer → line → HV_bus``
      4. No fallback — if no HV bus found within range, log and skip.

    Returns ``(elements_created, log_lines)``.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    SAFETY_FACTOR = 1.2
    DEFAULT_CAPACITY_MW = 1.0
    LV_FRACTION = 0.25
    TR_FRACTION = 0.65
    MIN_CHAIN_SPREAD = 0.003

    log: list[str] = []
    created = 0

    # Compute main connected component ONCE for the whole batch.
    adj = _build_bus_adjacency(state)
    components = _find_connected_components(adj)
    main_comp = max(components, key=len) if components else set()

    for audit in failed_audits:
        etype = audit["etype"]
        eid = audit["eid"]
        obj = audit["obj"]
        reason = audit["failure_reason"]

        # ── 1. Remove existing connection lines for this equipment ─
        # Only remove lines whose from_endpoint matches this equipment
        # to avoid duplicate connection lines. Orphaned LV buses and
        # transformers will be cleaned up by the simplify step.
        lines_to_remove = [
            ln.line_id for ln in state.transmission_lines
            if (ln.from_endpoint
                and ln.from_endpoint.element_type == etype
                and ln.from_endpoint.element_id == eid)
        ]
        for lid in lines_to_remove:
            try:
                model.remove_line(lid)
            except Exception:
                pass

        # ── 2. Get coordinates ────────────────────────────────────
        lat = getattr(obj, "latitude", 0.0)
        lng = getattr(obj, "longitude", 0.0)
        if lat == 0.0 and lng == 0.0:
            bus = state.buses.get(obj.bus)
            if bus:
                lat, lng = bus.latitude, bus.longitude

        if lat == 0.0 and lng == 0.0:
            log.append(
                f"  {etype} {eid}: no coordinates, skipped "
                f"(reason: {reason})"
            )
            continue

        # ── 3. Find nearest HV bus in main component ─────────────
        target_id, dist_km = _find_nearest_hv_bus_in(
            state, lat, lng,
            candidates=main_comp,
            min_voltage_kv=lv_voltage_kv,
        )

        # If beyond max distance, search ALL buses within range
        # (allows connecting to local island networks).
        if (target_id is None
                or dist_km > max_connection_km):
            all_buses = set(state.buses.keys())
            target_id, dist_km = _find_nearest_hv_bus_in(
                state, lat, lng,
                candidates=all_buses,
                min_voltage_kv=lv_voltage_kv,
            )
            if target_id is not None and dist_km > max_connection_km:
                target_id = None

        if target_id is None:
            log.append(
                f"  {etype} {eid}: no HV bus within "
                f"{max_connection_km:.0f} km, skipped"
            )
            continue

        tgt = state.buses[target_id]
        equip_node = obj.node
        rated_mw = getattr(obj, "rated_power", 0.0) or DEFAULT_CAPACITY_MW
        tr_cap = rated_mw * SAFETY_FACTOR

        # ── 4. Position LV bus and transformer along axis ─────────
        if lat == tgt.latitude and lng == tgt.longitude:
            uy, ux = 0.0, 1.0
        else:
            dy = tgt.latitude - lat
            dx = tgt.longitude - lng
            norm = math.sqrt(dy * dy + dx * dx) or 1e-9
            uy, ux = dy / norm, dx / norm

        dist_deg = math.sqrt(
            (tgt.latitude - lat) ** 2 + (tgt.longitude - lng) ** 2
        )
        if dist_deg >= 3 * MIN_CHAIN_SPREAD:
            lv_lat = lat + (tgt.latitude - lat) * LV_FRACTION
            lv_lng = lng + (tgt.longitude - lng) * LV_FRACTION
            tr_lat = lat + (tgt.latitude - lat) * TR_FRACTION
            tr_lng = lng + (tgt.longitude - lng) * TR_FRACTION
        else:
            lv_lat = lat + uy * MIN_CHAIN_SPREAD
            lv_lng = lng + ux * MIN_CHAIN_SPREAD
            tr_lat = lat + uy * 2 * MIN_CHAIN_SPREAD
            tr_lng = lng + ux * 2 * MIN_CHAIN_SPREAD

        # ── 5. Create chain elements ─────────────────────────────
        try:
            new_lv = model.add_bus(
                parent_node=equip_node,
                name=f"LV ({getattr(obj, 'name', eid)[:25]})",
                voltage_kv=lv_voltage_kv,
                latitude=lv_lat, longitude=lv_lng,
            )
            created += 1

            tr_idx = model.add_transformer(
                name=f"TR {new_lv}\u2192{target_id}",
                from_bus=new_lv, to_bus=target_id,
                from_voltage_kv=lv_voltage_kv,
                to_voltage_kv=tgt.voltage_kv,
                rated_power_mva=tr_cap,
                latitude=tr_lat, longitude=tr_lng,
            )
            created += 1

            # Line: equipment → LV bus
            model.add_line(
                from_bus=new_lv, to_bus=new_lv,
                capacity_mw=rated_mw,
                from_endpoint=EndpointRef(etype, eid),
                to_endpoint=EndpointRef("bus", new_lv),
            )
            created += 1

            # Line: LV bus → transformer
            model.add_line(
                from_bus=new_lv, to_bus=new_lv,
                capacity_mw=tr_cap,
                from_endpoint=EndpointRef("bus", new_lv),
                to_endpoint=EndpointRef("transformer", str(tr_idx)),
            )
            created += 1

            # Line: transformer → HV bus
            model.add_line(
                from_bus=target_id, to_bus=target_id,
                capacity_mw=tr_cap,
                from_endpoint=EndpointRef("transformer", str(tr_idx)),
                to_endpoint=EndpointRef("bus", target_id),
            )
            created += 1

            # Move equipment to LV bus
            obj.bus = new_lv

            # Update main_comp with new elements
            main_comp.add(new_lv)

            log.append(
                f"  Chain: {etype} {eid} → LV {new_lv} → TR → "
                f"{target_id} ({tgt.voltage_kv:.0f}kV) "
                f"[{dist_km:.1f} km]"
            )

        except Exception as exc:
            log.append(f"  {etype} {eid}: chain creation error: {exc}")
            logger.exception(
                "Chain creation error for %s %s", etype, eid,
            )

    return created, log


def _fix_transformer_lines(
    model, state, failed_audits: list[dict],
) -> tuple[int, list[str]]:
    """Create missing connection lines for transformers.

    For each transformer with missing sides:
      - Missing from-side: create line ``bus:from_bus → transformer:idx``
      - Missing to-side: create line ``transformer:idx → bus:to_bus``

    Returns ``(lines_created, log_lines)``.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    log: list[str] = []
    created = 0

    for audit in failed_audits:
        tr_idx = audit["tr_idx"]
        tr = audit["tr"]
        missing = audit["missing_sides"]
        tr_id_str = str(tr_idx)

        cap = tr.rated_power_mva or _estimate_bridge_capacity(
            max(tr.from_voltage_kv, tr.to_voltage_kv) or 110.0
        )

        if "from" in missing:
            try:
                model.add_line(
                    from_bus=tr.from_bus, to_bus=tr.from_bus,
                    capacity_mw=cap,
                    from_endpoint=EndpointRef("bus", tr.from_bus),
                    to_endpoint=EndpointRef("transformer", tr_id_str),
                )
                created += 1
                log.append(
                    f"  TR[{tr_idx}]: created from-side line "
                    f"bus:{tr.from_bus} → transformer:{tr_id_str}"
                )
            except Exception as exc:
                log.append(
                    f"  TR[{tr_idx}]: error creating from-side line: {exc}"
                )

        if "to" in missing:
            try:
                model.add_line(
                    from_bus=tr.to_bus, to_bus=tr.to_bus,
                    capacity_mw=cap,
                    from_endpoint=EndpointRef("transformer", tr_id_str),
                    to_endpoint=EndpointRef("bus", tr.to_bus),
                )
                created += 1
                log.append(
                    f"  TR[{tr_idx}]: created to-side line "
                    f"transformer:{tr_id_str} → bus:{tr.to_bus}"
                )
            except Exception as exc:
                log.append(
                    f"  TR[{tr_idx}]: error creating to-side line: {exc}"
                )

    return created, log


def _fix_converter_lines(
    model, state, failed_audits: list[dict],
) -> tuple[int, list[str]]:
    """Create missing connection lines for converters (AC/DC and frequency).

    For each converter with missing sides:
      - Missing from-side: create line ``bus:from_bus → <conv_type>:idx``
      - Missing to-side: create line ``<conv_type>:idx → bus:to_bus``

    Returns ``(lines_created, log_lines)``.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    log: list[str] = []
    created = 0

    for audit in failed_audits:
        conv_type = audit["conv_type"]
        conv_idx = audit["conv_idx"]
        conv = audit["conv"]
        missing = audit["missing_sides"]
        conv_id_str = str(conv_idx)

        cap = conv.rated_power_mva or _estimate_bridge_capacity(
            max(
                getattr(conv, "from_voltage_kv", 110.0),
                getattr(conv, "dc_voltage_kv",
                        getattr(conv, "to_voltage_kv", 110.0)),
            ) or 110.0
        )

        label = conv_type.replace("_", " ").upper()

        if "from" in missing:
            try:
                model.add_line(
                    from_bus=conv.from_bus, to_bus=conv.from_bus,
                    capacity_mw=cap,
                    from_endpoint=EndpointRef("bus", conv.from_bus),
                    to_endpoint=EndpointRef(conv_type, conv_id_str),
                )
                created += 1
                log.append(
                    f"  {label}[{conv_idx}]: created from-side line "
                    f"bus:{conv.from_bus} → {conv_type}:{conv_id_str}"
                )
            except Exception as exc:
                log.append(
                    f"  {label}[{conv_idx}]: error creating "
                    f"from-side line: {exc}"
                )

        if "to" in missing:
            try:
                model.add_line(
                    from_bus=conv.to_bus, to_bus=conv.to_bus,
                    capacity_mw=cap,
                    from_endpoint=EndpointRef(conv_type, conv_id_str),
                    to_endpoint=EndpointRef("bus", conv.to_bus),
                )
                created += 1
                log.append(
                    f"  {label}[{conv_idx}]: created to-side line "
                    f"{conv_type}:{conv_id_str} → bus:{conv.to_bus}"
                )
            except Exception as exc:
                log.append(
                    f"  {label}[{conv_idx}]: error creating "
                    f"to-side line: {exc}"
                )

    return created, log


def _sync_transformer_voltages(state) -> tuple[int, list[str]]:
    """Sync transformer voltage fields with their connected bus voltages.

    Returns ``(corrections_count, log_lines)``.
    """
    log: list[str] = []
    fixed = 0

    for i, tr in enumerate(state.transformers):
        fb = state.buses.get(tr.from_bus)
        tb = state.buses.get(tr.to_bus)

        if fb and fb.voltage_kv > 0 and tr.from_voltage_kv != fb.voltage_kv:
            old_v = tr.from_voltage_kv
            tr.from_voltage_kv = fb.voltage_kv
            fixed += 1
            log.append(
                f"  TR[{i}] from_voltage: {old_v:.0f} → {fb.voltage_kv:.0f} kV"
            )

        if tb and tb.voltage_kv > 0 and tr.to_voltage_kv != tb.voltage_kv:
            old_v = tr.to_voltage_kv
            tr.to_voltage_kv = tb.voltage_kv
            fixed += 1
            log.append(
                f"  TR[{i}] to_voltage: {old_v:.0f} → {tb.voltage_kv:.0f} kV"
            )

    return fixed, log


# =====================================================================
# Shared helpers
# =====================================================================


def _find_nearest_hv_bus_in(
    state, lat: float, lng: float,
    candidates: set[str],
    min_voltage_kv: float = 0.48,
) -> tuple[str | None, float]:
    """Find the nearest bus with voltage > min_voltage_kv within *candidates*."""
    best_id: str | None = None
    best_dist = float("inf")
    for bid in candidates:
        b = state.buses.get(bid)
        if not b:
            continue
        if b.latitude == 0.0 and b.longitude == 0.0:
            continue
        if b.voltage_kv <= min_voltage_kv:
            continue
        d = _haversine_km(lat, lng, b.latitude, b.longitude)
        if d < best_dist:
            best_dist = d
            best_id = bid
    return best_id, best_dist


def _find_closest_bus_pair(
    state, comp_a: set[str], comp_b: set[str],
) -> tuple[str | None, str | None, float]:
    """Find the closest bus pair between two components."""
    best_a: str | None = None
    best_b: str | None = None
    best_dist = float("inf")
    for a_bid in comp_a:
        a_bus = state.buses.get(a_bid)
        if not a_bus or (a_bus.latitude == 0.0 and a_bus.longitude == 0.0):
            continue
        for b_bid in comp_b:
            b_bus = state.buses.get(b_bid)
            if not b_bus or (
                b_bus.latitude == 0.0 and b_bus.longitude == 0.0
            ):
                continue
            d = _haversine_km(
                a_bus.latitude, a_bus.longitude,
                b_bus.latitude, b_bus.longitude,
            )
            if d < best_dist:
                best_dist = d
                best_a = a_bid
                best_b = b_bid
    return best_a, best_b, best_dist


def _estimate_bridge_capacity(voltage_kv: float) -> float:
    """Rough capacity for a bridge line / transformer based on voltage."""
    if voltage_kv >= 500:
        return 2000.0
    if voltage_kv >= 345:
        return 1000.0
    if voltage_kv >= 220:
        return 500.0
    if voltage_kv >= 110:
        return 200.0
    return 50.0


def _remove_empty_isolated_buses(
    model, state,
) -> tuple[int, list[str]]:
    """Remove buses that are completely isolated AND have no equipment.

    This is a safe, **non-cascading** single pass:
      - Build adjacency.
      - Identify buses with degree 0 (no connections at all).
      - Among those, keep any bus that has generators, batteries,
        electrolyzers, transformers, or converters (regardless of
        rated_power).
      - Remove the rest, plus any self-loop lines.

    Returns ``(removed_count, log_lines)``.
    """
    adj = _build_bus_adjacency(state)
    log: list[str] = []
    removed_buses = 0
    removed_lines = 0

    # 1. Remove TRUE self-loop lines.
    # A true self-loop is where BOTH endpoints refer to the same element.
    # Lines with from_bus == to_bus are NOT self-loops — they are valid
    # connections between equipment↔bus or bus↔transformer (the from_bus/
    # to_bus fields indicate bus ownership, not connectivity).
    self_loops = []
    for ln in state.transmission_lines:
        if (ln.from_endpoint and ln.to_endpoint
                and ln.from_endpoint.element_type == ln.to_endpoint.element_type
                and ln.from_endpoint.element_id == ln.to_endpoint.element_id):
            self_loops.append(ln)
        elif (not ln.from_endpoint and not ln.to_endpoint
              and ln.from_bus == ln.to_bus):
            # No endpoint refs at all AND from_bus == to_bus → true self-loop.
            self_loops.append(ln)

    for ln in self_loops:
        model.remove_line(ln.line_id)
        removed_lines += 1
        log.append(f"  Removed self-loop line {ln.line_id}")

    # Re-build adjacency after removing self-loops
    if self_loops:
        adj = _build_bus_adjacency(state)

    # 2. Identify degree-0 buses with no equipment
    candidates = []
    for bid in list(state.buses.keys()):
        degree = len(adj.get(bid, set()))
        if degree == 0 and not _bus_has_any_equipment(state, bid):
            candidates.append(bid)

    for bid in candidates:
        bus = state.buses.get(bid)
        bus_name = bus.name if bus else bid
        model.remove_bus(bid)
        removed_buses += 1
        log.append(f"  Removed isolated empty bus: {bus_name} ({bid})")

    total = removed_buses + removed_lines
    if total == 0:
        log.append("No empty isolated buses or self-loop lines found.")
    else:
        parts: list[str] = []
        if removed_buses:
            parts.append(f"{removed_buses} bus(es)")
        if removed_lines:
            parts.append(f"{removed_lines} self-loop line(s)")
        log.insert(0, f"Removed {total} element(s): " + ", ".join(parts))

    return total, log


# =====================================================================
# Fuel auto-routing via road network (Dijkstra)
# =====================================================================


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _build_road_graph(
    features: list,
) -> tuple[dict[tuple, list[tuple]], dict[tuple, tuple[float, float]]]:
    """Build adjacency list from road GridFeatures.

    Returns ``(adj, coords)`` where:
    - ``adj[node_key]`` -> ``[(neighbor_key, dist_km), ...]``
    - ``coords[node_key]`` -> ``(lat, lng)``
    - ``node_key`` = ``(round(lat, 5), round(lng, 5))``

    Coordinates are rounded to ~1m precision for node deduplication.
    """
    adj: dict[tuple, list[tuple]] = {}
    coords: dict[tuple, tuple[float, float]] = {}

    for feat in features:
        if feat.feature_type != "road" or not feat.line_coords:
            continue
        line = feat.line_coords
        for i in range(len(line) - 1):
            lat1, lng1 = line[i]
            lat2, lng2 = line[i + 1]
            k1 = (round(lat1, 5), round(lng1, 5))
            k2 = (round(lat2, 5), round(lng2, 5))
            if k1 == k2:
                continue
            coords[k1] = (lat1, lng1)
            coords[k2] = (lat2, lng2)
            dist = _haversine_km(lat1, lng1, lat2, lng2)
            if k1 not in adj:
                adj[k1] = []
            if k2 not in adj:
                adj[k2] = []
            adj[k1].append((k2, dist))
            adj[k2].append((k1, dist))

    return adj, coords


def _snap_to_road(
    lat: float, lng: float,
    road_coords: dict[tuple, tuple[float, float]],
) -> tuple | None:
    """Find nearest road node to ``(lat, lng)``. Returns node_key or None."""
    best_key = None
    best_dist = float("inf")
    for key, (rlat, rlng) in road_coords.items():
        d = _haversine_km(lat, lng, rlat, rlng)
        if d < best_dist:
            best_dist = d
            best_key = key
    return best_key


def _shortest_road_path(
    adj: dict[tuple, list[tuple]],
    coords: dict[tuple, tuple[float, float]],
    start_key: tuple,
    end_key: tuple,
    max_km: float,
) -> tuple[list[tuple[float, float]], float] | tuple[None, float]:
    """Dijkstra's shortest path on the road graph.

    Returns ``(path_coords, total_km)`` or ``(None, inf)``.
    ``path_coords`` is a list of ``(lat, lng)`` tuples.
    """
    if start_key is None or end_key is None:
        return None, float("inf")
    if start_key == end_key:
        lat, lng = coords.get(start_key, (0, 0))
        return [(lat, lng)], 0.0
    if start_key not in adj or end_key not in adj:
        return None, float("inf")

    # Dijkstra
    dist: dict[tuple, float] = {start_key: 0.0}
    prev: dict[tuple, tuple | None] = {start_key: None}
    heap: list[tuple[float, tuple]] = [(0.0, start_key)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if u == end_key:
            break
        if d > max_km:
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if end_key not in dist:
        return None, float("inf")

    # Reconstruct path
    path_keys: list[tuple] = []
    cur: tuple | None = end_key
    while cur is not None:
        path_keys.append(cur)
        cur = prev.get(cur)
    path_keys.reverse()

    path_coords = [coords.get(k, k) for k in path_keys]
    return path_coords, dist[end_key]


def fuel_auto_route(
    model,
    state,
    features: list,
    *,
    max_route_km: float = 200.0,
) -> int:
    """Connect fuel infrastructure via road network.

    For each fuel storage, find the nearest fuel entry of matching fuel type
    and connect them via the shortest road-network path (Dijkstra).

    Parameters
    ----------
    model : GuiModel
        The active GUI model.
    state : GuiSystemState
        The current system state.
    features : list[GridFeature]
        All features (including roads with ``include=False``).
    max_route_km : float
        Maximum road-network distance for a route.

    Returns
    -------
    int
        Number of fuel routes created.
    """
    from esfex.visualization.data.gui_model import EndpointRef, GeoPoint

    roads = [f for f in features if f.feature_type == "road"]
    if not roads:
        logger.warning("No road data available for fuel routing")
        return 0

    adj, road_coords = _build_road_graph(features)
    if not adj:
        logger.warning("Road graph is empty")
        return 0

    entries = state.fuel_entry_points
    storages = state.fuel_storages

    if not entries or not storages:
        logger.info("No fuel entries or storages to route")
        return 0

    created = 0

    for sid, storage in storages.items():
        storage_fuel = storage.fuels[0] if storage.fuels else ""
        s_lat = storage.latitude
        s_lng = storage.longitude

        storage_snap = _snap_to_road(s_lat, s_lng, road_coords)

        best_entry_idx: int | None = None
        best_path: list[tuple[float, float]] | None = None
        best_dist = float("inf")

        for idx, entry in enumerate(entries):
            # Prefer matching fuel; skip non-matching if storage has fuel
            entry_fuels = entry.fuels or []
            if storage_fuel and entry_fuels and storage_fuel not in entry_fuels:
                continue

            e_lat = entry.coordinate.lat if entry.coordinate else 0.0
            e_lng = entry.coordinate.lng if entry.coordinate else 0.0
            entry_snap = _snap_to_road(e_lat, e_lng, road_coords)

            path, dist = _shortest_road_path(
                adj, road_coords,
                storage_snap, entry_snap,
                max_route_km,
            )
            if path and dist < best_dist:
                best_dist = dist
                best_path = path
                best_entry_idx = idx

        # Fallback: try ANY entry if no fuel-matched entry found
        if best_path is None and storage_fuel:
            for idx, entry in enumerate(entries):
                e_lat = entry.coordinate.lat if entry.coordinate else 0.0
                e_lng = entry.coordinate.lng if entry.coordinate else 0.0
                entry_snap = _snap_to_road(e_lat, e_lng, road_coords)

                path, dist = _shortest_road_path(
                    adj, road_coords,
                    storage_snap, entry_snap,
                    max_route_km,
                )
                if path and dist < best_dist:
                    best_dist = dist
                    best_path = path
                    best_entry_idx = idx

        if best_path and best_entry_idx is not None:
            waypoints = [GeoPoint(lat, lng) for lat, lng in best_path[1:-1]]
            fuels = [storage_fuel] if storage_fuel else []
            entry = entries[best_entry_idx]

            model.add_fuel_route(
                from_node=entry.node,
                to_node=storage.node,
                fuels=fuels,
                waypoints=waypoints,
                from_endpoint=EndpointRef("fuel_entry", str(best_entry_idx)),
                to_endpoint=EndpointRef("fuel_storage", sid),
            )
            created += 1
            logger.info(
                "Fuel route: %s -> %s (%.1f km, %d waypoints)",
                entry.name, storage.name, best_dist, len(waypoints),
            )

    return created


# =====================================================================
# Step 6: Demand Forecast & Distribution (integrated)
# =====================================================================


class GridMappingDemandStep(QWidget):
    """Forecast demand per node and distribute among busbars.

    Integrates ML-based demand forecasting into the grid-mapping wizard,
    reusing domain bounds from Step 1 and node positions from Step 3.

    Sub-sections:
      1. Demand Forecast — auto-detect country, fetch WB/ERA5, run ML
      2. Forecast Results — per-node peak/GWh/LF table
      3. Bus Distribution — building footprints → demand fractions
    """

    def __init__(self, model=None, all_states=None, map_widget=None,
                 parent=None):
        super().__init__(parent)
        self._model = model
        self._all_states = all_states or {}
        self._map_widget = map_widget
        self._bounds: tuple[float, float, float, float] | None = None
        self._buildings_gdf = None
        self._classified_gdf = None
        self._targets: list[dict] = []
        self._assignments: list[dict] = []
        self._fetcher = None
        self._wb_data: dict = {}
        self._era5_data: dict = {}
        self._forecast_result = None
        self._forecast_worker = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        layout.addWidget(QLabel(
            "<b>Step 6: Demand Forecast & Distribution</b><br>"
            "Generate hourly demand profiles per node using ML models, "
            "then distribute among busbars via building density."
        ))

        # ==============================================================
        # Section 1: Demand Forecast Configuration
        # ==============================================================
        forecast_group = QGroupBox("1. Demand Forecast")
        fg = QVBoxLayout(forecast_group)

        # Country + base config (2 columns)
        config_grid = QGridLayout()

        config_grid.addWidget(QLabel("Country:"), 0, 0)
        self._combo_country = QComboBox()
        self._combo_country.setEditable(True)
        self._combo_country.setMinimumWidth(180)
        config_grid.addWidget(self._combo_country, 0, 1)

        self._btn_detect_country = QPushButton("Auto-detect")
        self._btn_detect_country.setToolTip(
            "Detect country from polygon centroid via Nominatim"
        )
        self._btn_detect_country.clicked.connect(self._detect_country)
        config_grid.addWidget(self._btn_detect_country, 0, 2)

        config_grid.addWidget(QLabel("Base year:"), 1, 0)
        self._spin_base_year = QSpinBox()
        self._spin_base_year.setRange(2000, 2100)
        self._spin_base_year.setValue(2025)
        config_grid.addWidget(self._spin_base_year, 1, 1)

        config_grid.addWidget(QLabel("Horizon (years):"), 2, 0)
        self._spin_horizon = QSpinBox()
        self._spin_horizon.setRange(1, 50)
        self._spin_horizon.setValue(25)
        config_grid.addWidget(self._spin_horizon, 2, 1)

        config_grid.addWidget(QLabel("ML engine:"), 3, 0)
        self._combo_engine = QComboBox()
        # TFT disabled for now \u2014 forward per-node generation uses XGBoost.
        self._combo_engine.addItem("Auto (XGBoost)", "auto")
        self._combo_engine.addItem("XGBoost", "xgboost")
        self._combo_engine.addItem("Archetype (no ML)", "archetype")
        config_grid.addWidget(self._combo_engine, 3, 1)

        config_grid.addWidget(QLabel("National demand (GWh):"), 4, 0)
        self._spin_national_gwh = QDoubleSpinBox()
        self._spin_national_gwh.setRange(0.0, 9_999_999.0)
        self._spin_national_gwh.setDecimals(1)
        self._spin_national_gwh.setValue(0.0)
        self._spin_national_gwh.setSpecialValueText("Auto-estimate")
        self._spin_national_gwh.setToolTip(
            "Override total national demand (0 = auto-estimate from "
            "World Bank kWh/capita \u00d7 population)"
        )
        config_grid.addWidget(self._spin_national_gwh, 4, 1)

        fg.addLayout(config_grid)

        # GDP growth + elasticity (collapsible row)
        growth_row = QHBoxLayout()
        growth_row.addWidget(QLabel("GDP growth:"))
        self._spin_gdp_growth = QDoubleSpinBox()
        self._spin_gdp_growth.setRange(-0.10, 0.20)
        self._spin_gdp_growth.setDecimals(3)
        self._spin_gdp_growth.setSingleStep(0.005)
        self._spin_gdp_growth.setValue(0.030)
        self._spin_gdp_growth.setSuffix(" /yr")
        growth_row.addWidget(self._spin_gdp_growth)

        growth_row.addWidget(QLabel("Elasticity:"))
        self._spin_elasticity = QDoubleSpinBox()
        self._spin_elasticity.setRange(0.0, 2.0)
        self._spin_elasticity.setDecimals(2)
        self._spin_elasticity.setSingleStep(0.05)
        self._spin_elasticity.setValue(0.80)
        growth_row.addWidget(self._spin_elasticity)

        growth_row.addWidget(QLabel("Efficiency:"))
        self._spin_efficiency = QDoubleSpinBox()
        self._spin_efficiency.setRange(0.0, 0.05)
        self._spin_efficiency.setDecimals(3)
        self._spin_efficiency.setSingleStep(0.001)
        self._spin_efficiency.setValue(0.005)
        self._spin_efficiency.setSuffix(" /yr")
        growth_row.addWidget(self._spin_efficiency)
        growth_row.addStretch()
        fg.addLayout(growth_row)

        # Fetch + Run buttons
        btn_row = QHBoxLayout()
        self._btn_fetch_data = QPushButton("Fetch WB + ERA5")
        self._btn_fetch_data.setStyleSheet(
            "font-size: 11px; padding: 4px 8px;"
        )
        self._btn_fetch_data.clicked.connect(self._fetch_wb_era5)
        btn_row.addWidget(self._btn_fetch_data)

        self._btn_forecast = QPushButton("\u26a1 Forecast Demand")
        self._btn_forecast.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 4px 12px;"
        )
        self._btn_forecast.setEnabled(False)
        self._btn_forecast.clicked.connect(self._run_forecast)
        btn_row.addWidget(self._btn_forecast)

        self._forecast_progress = QProgressBar()
        self._forecast_progress.setRange(0, 100)
        btn_row.addWidget(self._forecast_progress, 1)
        fg.addLayout(btn_row)

        self._lbl_forecast_status = QLabel("")
        self._lbl_forecast_status.setWordWrap(True)
        fg.addWidget(self._lbl_forecast_status)

        layout.addWidget(forecast_group)

        # ==============================================================
        # Section 2: Forecast Results
        # ==============================================================
        results_group = QGroupBox("2. Forecast Results")
        rg = QVBoxLayout(results_group)

        self._forecast_table = QTableWidget(0, 4)
        self._forecast_table.setHorizontalHeaderLabels([
            "Node", "Peak (MW)", "Annual (GWh)", "Load Factor",
        ])
        self._forecast_table.horizontalHeader().setStretchLastSection(True)
        self._forecast_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers,
        )
        self._forecast_table.setMinimumHeight(100)
        rg.addWidget(self._forecast_table)

        self._lbl_forecast_summary = QLabel("")
        self._lbl_forecast_summary.setWordWrap(True)
        rg.addWidget(self._lbl_forecast_summary)

        layout.addWidget(results_group)

        # ==============================================================
        # Section 3: Bus Distribution (existing logic preserved)
        # ==============================================================
        dist_group = QGroupBox(
            "3. Bus Distribution (building footprint density)"
        )
        dg = QVBoxLayout(dist_group)
        dg.addWidget(QLabel(
            "Distribute each node\u2019s forecast demand among its busbars "
            "using building footprint density. Only nodes with \u2265 2 buses "
            "need distribution."
        ))

        # Building source
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Building source:"))
        self._combo_bld_source = QComboBox()
        self._combo_bld_source.addItem("Overture Maps", "overture")
        self._combo_bld_source.addItem("Microsoft ML", "microsoft")
        self._combo_bld_source.addItem("Google Open Buildings", "google")
        src_row.addWidget(self._combo_bld_source, 1)
        dg.addLayout(src_row)

        # Classification rules table
        self._rules_table = QTableWidget(0, 4)
        self._rules_table.setHorizontalHeaderLabels([
            "Type Name", "Area Min (m\u00b2)", "Area Max (m\u00b2)",
            "Weight/m\u00b2",
        ])
        self._rules_table.horizontalHeader().setStretchLastSection(True)
        self._rules_table.setMinimumHeight(100)
        dg.addWidget(self._rules_table)

        rules_btn_row = QHBoxLayout()
        self._btn_add_rule = QPushButton("Add Rule")
        self._btn_add_rule.clicked.connect(self._add_empty_rule)
        rules_btn_row.addWidget(self._btn_add_rule)
        self._btn_remove_rule = QPushButton("Remove Rule")
        self._btn_remove_rule.clicked.connect(self._remove_selected_rule)
        rules_btn_row.addWidget(self._btn_remove_rule)
        rules_btn_row.addStretch()
        dg.addLayout(rules_btn_row)

        # Fallback weight
        fallback_row = QHBoxLayout()
        fallback_row.addWidget(QLabel("Fallback weight/m\u00b2:"))
        self._spin_fallback = QDoubleSpinBox()
        self._spin_fallback.setRange(0.0, 1.0)
        self._spin_fallback.setDecimals(4)
        self._spin_fallback.setSingleStep(0.01)
        self._spin_fallback.setValue(0.03)
        fallback_row.addWidget(self._spin_fallback)
        fallback_row.addStretch()
        dg.addLayout(fallback_row)

        # Fetch & Distribute button
        dist_btn_row = QHBoxLayout()
        self._btn_fetch_bld = QPushButton("Fetch & Distribute")
        self._btn_fetch_bld.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 4px 8px;"
        )
        self._btn_fetch_bld.setEnabled(False)
        self._btn_fetch_bld.clicked.connect(self._fetch_buildings)
        dist_btn_row.addWidget(self._btn_fetch_bld)
        self._bld_progress = QProgressBar()
        self._bld_progress.setRange(0, 100)
        dist_btn_row.addWidget(self._bld_progress, 1)
        dg.addLayout(dist_btn_row)

        self._lbl_bld_status = QLabel("")
        self._lbl_bld_status.setWordWrap(True)
        dg.addWidget(self._lbl_bld_status)

        # Results table (bus fractions)
        self._results_table = QTableWidget(0, 5)
        self._results_table.setHorizontalHeaderLabels([
            "Node", "Bus", "Buildings", "Old Fraction", "New Fraction",
        ])
        self._results_table.horizontalHeader().setStretchLastSection(True)
        self._results_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers,
        )
        self._results_table.setMinimumHeight(120)
        dg.addWidget(self._results_table)

        # Apply button
        apply_row = QHBoxLayout()
        self._btn_apply = QPushButton("Apply Demand & Fractions")
        self._btn_apply.setStyleSheet(
            "font-weight: bold; font-size: 11px; padding: 4px 16px;"
        )
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._apply_all)
        apply_row.addWidget(self._btn_apply)
        apply_row.addStretch()
        dg.addLayout(apply_row)

        self._lbl_apply_status = QLabel("")
        self._lbl_apply_status.setWordWrap(True)
        dg.addWidget(self._lbl_apply_status)

        layout.addWidget(dist_group)

        # Aliases for compatibility
        self._progress = self._bld_progress
        self._lbl_status = self._lbl_bld_status
        self._btn_run = self._btn_fetch_bld

        # Populate default rules
        self._load_default_rules()

        scroll.setWidget(scroll_content)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_inputs(self, bounds, model, all_states):
        """Called by wizard when navigating to this step."""
        self._model = model
        self._all_states = all_states
        self._bounds = bounds
        self._buildings_gdf = None
        self._classified_gdf = None
        self._assignments.clear()
        self._forecast_result = None
        self._wb_data.clear()
        self._era5_data.clear()

        # Reset UI
        self._forecast_progress.setValue(0)
        self._lbl_forecast_status.setText("")
        self._forecast_table.setRowCount(0)
        self._lbl_forecast_summary.setText("")
        self._bld_progress.setValue(0)
        self._lbl_bld_status.setText("")
        self._lbl_apply_status.setText("")
        self._results_table.setRowCount(0)
        self._btn_forecast.setEnabled(False)
        self._btn_fetch_bld.setEnabled(False)
        self._btn_apply.setEnabled(False)

        # Auto-detect eligible nodes for bus distribution
        self._detect_eligible_nodes()

        # Auto-detect country from polygon centroid
        if bounds is not None:
            self._detect_country()

    def is_valid(self) -> bool:
        return True  # Step is optional

    # ==================================================================
    # Section 1: Demand Forecast
    # ==================================================================

    def _detect_country(self):
        """Detect country from polygon centroid via Nominatim."""
        if self._bounds is None:
            return

        self._btn_detect_country.setEnabled(False)
        self._lbl_forecast_status.setText("Detecting country...")

        south, west, north, east = self._bounds
        lat = (south + north) / 2.0
        lon = (west + east) / 2.0

        import threading

        def _do_detect():
            try:
                import requests
                url = (
                    f"https://nominatim.openstreetmap.org/reverse"
                    f"?lat={lat}&lon={lon}&format=json&zoom=3"
                )
                resp = requests.get(
                    url, headers={"User-Agent": "ESFEX-Grid/1.0"},
                    timeout=10,
                )
                data = resp.json()
                cc = data.get("address", {}).get("country_code", "").upper()
                name = data.get("address", {}).get("country", cc)
                # Map ISO2 → ISO3
                from esfex.visualization.workflows.demand_estimation_fetchers import (
                    _iso2_to_iso3,
                )
                iso3 = _iso2_to_iso3(cc)
                self._combo_country.blockSignals(True)
                self._combo_country.clear()
                self._combo_country.addItem(
                    f"{name} ({iso3})", {"iso2": cc, "iso3": iso3, "name": name}
                )
                self._combo_country.blockSignals(False)
                self._lbl_forecast_status.setText(
                    f"Country: {name} ({iso3})"
                )
            except Exception as exc:
                self._lbl_forecast_status.setText(
                    f"Country detection failed: {exc}"
                )
            finally:
                self._btn_detect_country.setEnabled(True)

        threading.Thread(target=_do_detect, daemon=True).start()

    def _fetch_wb_era5(self):
        """Fetch World Bank indicators + ERA5 temperature in background."""
        country_data = self._combo_country.currentData()
        if not country_data:
            self._lbl_forecast_status.setText(
                "Select a country first."
            )
            return

        iso2 = country_data.get("iso2", "")
        iso3 = country_data.get("iso3", iso2)

        self._btn_fetch_data.setEnabled(False)
        self._forecast_progress.setValue(5)
        self._lbl_forecast_status.setText("Fetching World Bank data...")

        # Fetch WB + ERA5 in background
        import threading

        def _do_fetch():
            try:
                import requests

                # ── World Bank ──
                wb_indicators = {
                    "gdp_per_capita": "NY.GDP.PCAP.CD",
                    "population": "SP.POP.TOTL",
                    "urbanization_pct": "SP.URB.TOTL.IN.ZS",
                    "electricity_access": "EG.ELC.ACCS.ZS",
                    "electric_consumption_kwh_capita": "EG.USE.ELEC.KH.PC",
                }
                headers = {"User-Agent": "ESFEX-Grid/1.0"}
                wb = {}
                for key, code in wb_indicators.items():
                    url = (
                        f"https://api.worldbank.org/v2/country/{iso2}"
                        f"/indicator/{code}"
                        f"?format=json&per_page=10&date=2015:2025"
                    )
                    try:
                        resp = requests.get(url, headers=headers, timeout=15)
                        payload = resp.json()
                        if isinstance(payload, list) and len(payload) >= 2 and payload[1]:
                            for entry in payload[1]:
                                if entry.get("value") is not None:
                                    wb[key] = entry["value"]
                                    break
                    except Exception as exc:
                        # Don't swallow silently: the UI later reads wb.get(k, 0)
                        # and would render "GDP=$0" as if it were real data.
                        import logging
                        logging.getLogger(__name__).warning(
                            "WorldBank fetch failed for %s (indicator %s): %s",
                            key, code, exc,
                        )

                self._wb_data = wb
                self._forecast_progress.setValue(40)
                self._lbl_forecast_status.setText(
                    f"WB: GDP/cap=${wb.get('gdp_per_capita', 0):,.0f}, "
                    f"Pop={wb.get('population', 0):,.0f}. "
                    f"Fetching ERA5 temperature..."
                )

                # ── ERA5 via Open-Meteo ──
                south, west, north, east = self._bounds
                lat = (south + north) / 2.0
                lon = (west + east) / 2.0
                weather_year = self._spin_base_year.value() - 1
                # Clamp to available ERA5 range
                weather_year = min(weather_year, 2025)

                url = (
                    f"https://archive-api.open-meteo.com/v1/archive"
                    f"?latitude={lat}&longitude={lon}"
                    f"&start_date={weather_year}-01-01"
                    f"&end_date={weather_year}-12-31"
                    f"&hourly=temperature_2m&timezone=UTC"
                )
                resp = requests.get(url, timeout=30)
                data = resp.json()
                temp = data.get("hourly", {}).get("temperature_2m", [])

                self._era5_data = {
                    "temperature_hourly": temp[:8760],
                    "lat": lat,
                    "lon": lon,
                    "year": weather_year,
                }
                self._forecast_progress.setValue(60)
                self._lbl_forecast_status.setText(
                    f"WB: GDP/cap=${wb.get('gdp_per_capita', 0):,.0f}, "
                    f"Pop={wb.get('population', 0):,.0f}, "
                    f"kWh/cap={wb.get('electric_consumption_kwh_capita', 0):,.0f}. "
                    f"ERA5: {len(temp)} hours ({weather_year}). "
                    f"Ready to forecast."
                )
                self._btn_forecast.setEnabled(True)
                self._btn_fetch_data.setEnabled(True)

            except Exception as exc:
                self._lbl_forecast_status.setText(f"Fetch error: {exc}")
                self._btn_fetch_data.setEnabled(True)
                self._forecast_progress.setValue(0)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _run_forecast(self):
        """Run ML demand forecast using collected data."""
        from esfex.visualization.workflows.demand_estimation_analysis import (
            DemandEstimationConfig,
            DemandEstimationResult,
            DemandProfileBuilder,
            MacroData,
            MeteoData,
            ProxyData,
        )

        self._btn_forecast.setEnabled(False)
        self._forecast_progress.setValue(65)
        self._lbl_forecast_status.setText("Running demand forecast...")

        state = self._model.state if self._model else None
        if state is None:
            self._lbl_forecast_status.setText("No active system.")
            self._btn_forecast.setEnabled(True)
            return

        nodes = list(state.nodes)
        num_nodes = len(nodes)
        if num_nodes == 0:
            self._lbl_forecast_status.setText("No nodes in system.")
            self._btn_forecast.setEnabled(True)
            return

        country_data = self._combo_country.currentData() or {}
        iso3 = country_data.get("iso3", "")
        engine_key = self._combo_engine.currentData() or "auto"

        # Build config
        cfg = DemandEstimationConfig(
            base_year=self._spin_base_year.value(),
            simulation_years=self._spin_horizon.value(),
            num_nodes=num_nodes,
            national_demand_gwh=self._spin_national_gwh.value(),
            gdp_growth_rate=self._spin_gdp_growth.value(),
            demand_gdp_elasticity=self._spin_elasticity.value(),
            efficiency_improvement=self._spin_efficiency.value(),
            ml_engine=engine_key,
            force_archetype=(engine_key == "archetype"),
        )

        # Build proxy data from nodes
        proxy = ProxyData(
            building_weights=[1.0 / num_nodes] * num_nodes,
            population_weights=[1.0 / num_nodes] * num_nodes,
            nightlight_weights=[1.0 / num_nodes] * num_nodes,
            landuse_weights=[1.0 / num_nodes] * num_nodes,
            node_residential_fraction=[0.40] * num_nodes,
            node_commercial_fraction=[0.35] * num_nodes,
            node_industrial_fraction=[0.25] * num_nodes,
            node_lats=[n.latitude for n in nodes],
            node_lons=[n.longitude for n in nodes],
            node_names=[n.name for n in nodes],
        )

        # Build macro data from WB fetch
        wb = self._wb_data
        macro = MacroData(
            country_iso=iso3,
            country_name=country_data.get("name", iso3),
            gdp_per_capita=wb.get("gdp_per_capita", 5000.0),
            population=wb.get("population", 1_000_000.0),
            urbanization_pct=wb.get("urbanization_pct", 50.0),
            electricity_access_pct=wb.get("electricity_access", 95.0),
            electric_consumption_kwh_capita=wb.get(
                "electric_consumption_kwh_capita", 2000.0
            ),
            gdp_growth_rate=self._spin_gdp_growth.value(),
        )

        # Build meteo data from ERA5 fetch
        era5 = self._era5_data
        temp_h = era5.get("temperature_hourly", [])
        hdd_base = 18.0
        cdd_base = 24.0
        hdd_h = [max(0.0, hdd_base - t) for t in temp_h] if temp_h else []
        cdd_h = [max(0.0, t - cdd_base) for t in temp_h] if temp_h else []

        meteo = MeteoData(
            temperature_hourly=temp_h,
            hdd_hourly=hdd_h,
            cdd_hourly=cdd_h,
            lat=era5.get("lat", 0.0),
            lon=era5.get("lon", 0.0),
            year=era5.get("year", 2024),
        )

        # Run in thread
        import threading

        def _do_forecast():
            try:
                builder = DemandProfileBuilder(cfg)
                result = builder.build(
                    proxy, macro, meteo,
                    progress_callback=lambda p, m: (
                        self._forecast_progress.setValue(65 + int(p * 0.30)),
                        self._lbl_forecast_status.setText(m),
                    ),
                )
                self._forecast_result = result
                self._forecast_progress.setValue(95)

                # Populate results table
                self._forecast_table.setRowCount(num_nodes)
                for i, node in enumerate(nodes):
                    peak = result.peak_mw[i] if i < len(result.peak_mw) else 0
                    gwh = result.annual_gwh[i] if i < len(result.annual_gwh) else 0
                    lf = result.load_factor[i] if i < len(result.load_factor) else 0
                    self._forecast_table.setItem(
                        i, 0, QTableWidgetItem(node.name))
                    self._forecast_table.setItem(
                        i, 1, QTableWidgetItem(f"{peak:.1f}"))
                    self._forecast_table.setItem(
                        i, 2, QTableWidgetItem(f"{gwh:.1f}"))
                    self._forecast_table.setItem(
                        i, 3, QTableWidgetItem(f"{lf:.3f}"))

                self._lbl_forecast_summary.setText(
                    f"<b>System total:</b> Peak={result.total_peak_mw:.1f} MW, "
                    f"Annual={result.total_annual_gwh:.1f} GWh, "
                    f"LF={result.total_load_factor:.3f} "
                    f"&mdash; Source: {result.demand_source}"
                )
                self._lbl_forecast_summary.setStyleSheet(
                    "color: #27ae60; padding: 4px;"
                )
                self._forecast_progress.setValue(100)
                self._lbl_forecast_status.setText("Forecast complete.")

                # Enable bus distribution
                has_eligible = len(self._targets) > 0
                self._btn_fetch_bld.setEnabled(
                    has_eligible and self._bounds is not None
                )
                self._btn_apply.setEnabled(True)

            except Exception as exc:
                logger.exception("Demand forecast failed")
                self._lbl_forecast_status.setText(
                    f"Forecast error: {exc}"
                )
                self._forecast_progress.setValue(0)

            self._btn_forecast.setEnabled(True)

        threading.Thread(target=_do_forecast, daemon=True).start()

    # ==================================================================
    # Section 2: Eligible nodes detection (for bus distribution)
    # ==================================================================

    def _detect_eligible_nodes(self):
        """Find all nodes with >= 2 demand-carrying buses across all systems.

        Connection buses (role='connection') are excluded — they don't carry
        demand and cannot receive a demand_fraction allocation.
        """
        self._targets.clear()
        self._all_eligible: list[dict] = []

        state = self._model.state if self._model else None
        if state is None:
            return

        for node in state.nodes:
            buses = [
                b for b in state.buses.values()
                if b.parent_node == node.index and b.role in ("load", "mixed")
            ]
            if len(buses) >= 2:
                self._all_eligible.append({
                    "node_index": node.index,
                    "node_name": node.name,
                    "peak_mw": node.demand.peak_mw if node.demand else 0.0,
                    "buses": buses,
                })
                self._targets.append(self._all_eligible[-1])

    # ==================================================================
    # Section 3: Bus Distribution (preserved from original)
    # ==================================================================

    def _fetch_buildings(self):
        if self._bounds is None:
            self._lbl_bld_status.setText("No domain bounds available.")
            return

        from esfex.visualization.workflows.data_fetchers import (
            BuildingFetcher,
        )

        source = self._combo_bld_source.currentData()
        self._btn_fetch_bld.setEnabled(False)
        self._lbl_bld_status.setText("Fetching building footprints...")
        self._bld_progress.setValue(0)

        self._fetcher = BuildingFetcher(source, self._bounds)
        self._fetcher.progress.connect(self._on_bld_progress)
        self._fetcher.finished.connect(self._on_bld_finished)
        self._fetcher.error.connect(self._on_bld_error)
        self._fetcher.start()

    def _on_bld_progress(self, pct, msg):
        self._bld_progress.setValue(pct)
        self._lbl_bld_status.setText(msg)

    def _on_bld_finished(self, gdf):
        self._buildings_gdf = gdf
        n = len(gdf) if gdf is not None else 0
        self._lbl_bld_status.setText(
            f"Loaded {n:,} building footprints. Classifying..."
        )
        self._bld_progress.setValue(50)
        if n > 0:
            self._run_classify_and_distribute()
        else:
            self._btn_fetch_bld.setEnabled(True)

    def _on_bld_error(self, msg):
        self._btn_fetch_bld.setEnabled(True)
        self._lbl_bld_status.setText(f"Error: {msg}")
        self._bld_progress.setValue(0)

    def _load_default_rules(self):
        """Populate rules table with the default classification rules."""
        from esfex.visualization.workflows.demand_analysis import (
            DEFAULT_RULES,
        )
        self._rules_table.setRowCount(len(DEFAULT_RULES))
        for row, rule in enumerate(DEFAULT_RULES):
            self._rules_table.setItem(
                row, 0, QTableWidgetItem(rule.name))
            self._rules_table.setItem(
                row, 1, QTableWidgetItem(str(rule.area_min_m2)))
            area_max_str = (
                "\u221e" if rule.area_max_m2 == math.inf
                else str(rule.area_max_m2)
            )
            self._rules_table.setItem(
                row, 2, QTableWidgetItem(area_max_str))
            self._rules_table.setItem(
                row, 3, QTableWidgetItem(str(rule.weight_per_m2)))

    def _get_rules(self):
        from esfex.visualization.workflows.demand_analysis import (
            BuildingTypeRule,
        )
        rules = []
        for row in range(self._rules_table.rowCount()):
            name = self._rules_table.item(row, 0)
            area_min = self._rules_table.item(row, 1)
            area_max = self._rules_table.item(row, 2)
            weight = self._rules_table.item(row, 3)
            if name is None:
                continue
            area_max_val = math.inf
            if area_max and area_max.text() not in ("\u221e", "inf", ""):
                try:
                    area_max_val = float(area_max.text())
                except ValueError:
                    area_max_val = math.inf
            rules.append(BuildingTypeRule(
                name=name.text(),
                area_min_m2=float(area_min.text()) if area_min else 0.0,
                area_max_m2=area_max_val,
                weight_per_m2=float(weight.text()) if weight else 0.05,
            ))
        return rules

    def _add_empty_rule(self):
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)
        self._rules_table.setItem(row, 0, QTableWidgetItem("New Type"))
        self._rules_table.setItem(row, 1, QTableWidgetItem("0"))
        self._rules_table.setItem(row, 2, QTableWidgetItem("\u221e"))
        self._rules_table.setItem(row, 3, QTableWidgetItem("0.05"))

    def _remove_selected_rule(self):
        row = self._rules_table.currentRow()
        if row >= 0:
            self._rules_table.removeRow(row)

    def _run_classify_and_distribute(self):
        """Classify buildings then assign each to its nearest bus."""
        if self._buildings_gdf is None or self._buildings_gdf.empty:
            self._lbl_status.setText("No buildings loaded.")
            return

        from esfex.visualization.workflows.demand_analysis import (
            classify_buildings,
            compute_classification_summary,
        )

        self._btn_run.setEnabled(False)
        self._btn_apply.setEnabled(False)
        self._results_table.setRowCount(0)
        self._assignments.clear()
        self._progress.setValue(10)
        self._lbl_status.setText("Classifying buildings...")

        rules = self._get_rules()
        fallback = self._spin_fallback.value()
        self._classified_gdf = classify_buildings(
            self._buildings_gdf, rules, fallback,
        )

        summary = compute_classification_summary(self._classified_gdf)
        cls_lines = []
        for _, r in summary.iterrows():
            cls_lines.append(
                f"{r['building_type']}: {int(r['count']):,} bldg, "
                f"{r['total_area_m2']:,.0f} m\u00b2, "
                f"w={r['total_weight']:.1f}"
            )

        self._progress.setValue(40)
        self._lbl_status.setText("Assigning buildings to nearest buses...")

        gdf = self._classified_gdf
        bld_lats = gdf.geometry.centroid.y.values
        bld_lngs = gdf.geometry.centroid.x.values
        bld_weights = gdf["demand_weight"].values

        rows = []
        for target in self._targets:
            node_name = target["node_name"]
            buses = sorted(target["buses"], key=lambda b: b.bus_id)
            n_buses = len(buses)
            if n_buses == 0:
                continue

            bus_lats = np.array([b.latitude for b in buses])
            bus_lngs = np.array([b.longitude for b in buses])

            n_bld = len(bld_lats)
            chunk_size = max(
                1, min(100_000, 500_000_000 // max(n_buses, 1)),
            )
            bus_weights = np.zeros(n_buses)
            nearest = np.empty(n_bld, dtype=np.intp)

            for start in range(0, n_bld, chunk_size):
                end = min(start + chunk_size, n_bld)
                dlat = bld_lats[start:end, None] - bus_lats[None, :]
                dlng = bld_lngs[start:end, None] - bus_lngs[None, :]
                dists = dlat ** 2 + dlng ** 2
                chunk_nearest = np.argmin(dists, axis=1)
                nearest[start:end] = chunk_nearest
                np.add.at(
                    bus_weights, chunk_nearest, bld_weights[start:end],
                )

            total = bus_weights.sum()
            if total > 0:
                bus_fractions = bus_weights / total
            else:
                bus_fractions = np.full(n_buses, 1.0 / n_buses)

            for i, bus in enumerate(buses):
                count = int((nearest == i).sum())
                self._assignments.append({
                    "node_name": node_name,
                    "node_index": target["node_index"],
                    "bus_id": bus.bus_id,
                    "bus_name": bus.name,
                    "old_fraction": bus.demand_fraction,
                    "new_fraction": float(bus_fractions[i]),
                    "building_count": count,
                })
                rows.append(self._assignments[-1])

        self._progress.setValue(80)

        self._results_table.setRowCount(len(rows))
        for row_idx, a in enumerate(rows):
            self._results_table.setItem(
                row_idx, 0, QTableWidgetItem(a["node_name"]))
            self._results_table.setItem(
                row_idx, 1,
                QTableWidgetItem(f"{a['bus_id']} ({a['bus_name']})"))
            self._results_table.setItem(
                row_idx, 2,
                QTableWidgetItem(f"{a['building_count']:,}"))
            self._results_table.setItem(
                row_idx, 3,
                QTableWidgetItem(f"{a['old_fraction']:.4f}"))
            self._results_table.setItem(
                row_idx, 4,
                QTableWidgetItem(f"{a['new_fraction']:.4f}"))

        self._progress.setValue(100)
        total_w = summary["total_weight"].sum()
        self._lbl_status.setText(
            f"Classified {len(gdf):,} buildings "
            f"({'; '.join(cls_lines)}; total weight: {total_w:.1f}). "
            f"Assigned to {len(rows)} buses."
        )
        self._btn_run.setEnabled(True)
        self._btn_apply.setEnabled(len(rows) > 0)

    def _apply_all(self):
        """Apply forecast demand to nodes + bus fractions to model."""
        if not self._model:
            return

        state = self._model.state
        applied_fracs = 0
        applied_demand = 0

        # Apply forecast demand to nodes (if forecast was run)
        if self._forecast_result is not None:
            result = self._forecast_result
            for i, node in enumerate(state.nodes):
                if i < len(result.peak_mw):
                    try:
                        if node.demand is None:
                            from types import SimpleNamespace
                            node.demand = SimpleNamespace(
                                peak_mw=0.0, annual_gwh=0.0)
                        node.demand.peak_mw = result.peak_mw[i]
                        node.demand.annual_gwh = result.annual_gwh[i]
                        applied_demand += 1
                    except Exception:
                        pass

        # Apply bus fractions
        skipped_connection = 0
        for a in self._assignments:
            bus_id = a["bus_id"]
            new_frac = a["new_fraction"]
            target_bus = state.buses.get(bus_id)
            if target_bus is not None and target_bus.role == "connection":
                # Defensive: never write demand to a connection bus
                skipped_connection += 1
                continue
            try:
                self._model.update_bus(bus_id, demand_fraction=new_frac)
                applied_fracs += 1
            except Exception:
                if bus_id in state.buses:
                    state.buses[bus_id].demand_fraction = new_frac
                    applied_fracs += 1
        if skipped_connection:
            logging.getLogger(__name__).warning(
                "Skipped %d connection bus(es) during demand allocation",
                skipped_connection,
            )

        self._btn_apply.setEnabled(False)
        parts = []
        if applied_demand > 0:
            parts.append(f"demand to {applied_demand} node(s)")
        if applied_fracs > 0:
            parts.append(f"fractions to {applied_fracs} bus(es)")
        self._lbl_apply_status.setText(
            f"Applied {' and '.join(parts)}."
            if parts else "Nothing to apply."
        )
        self._lbl_apply_status.setStyleSheet(
            "color: #27ae60; font-weight: bold; padding: 4px;"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_map(self):
        """Remove demand cluster overlays from the map."""
        if self._map_widget:
            try:
                self._map_widget.clear_demand_clusters()
            except Exception:
                pass
