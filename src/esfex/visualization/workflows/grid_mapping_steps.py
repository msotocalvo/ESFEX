"""Step widgets for the Grid Builder wizard.

Each step is a QWidget displayed in the wizard's QStackedWidget.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import time
from collections import deque
from pathlib import Path

import numpy as np
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
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

    def __init__(self, map_widget=None, parent=None, geo_assets_provider=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._geo_assets_provider = geo_assets_provider
        self._features: list = []
        self._fetchers: list = []
        self._finalize_worker = None
        self._fetch_t0: float = 0.0
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
            "Define the extraction area: draw a polygon on the map or apply an "
            "imported GeoAsset."
        ))
        # Standard two-column domain selector: draw a polygon OR apply a GeoAsset.
        from esfex.visualization.workflows._domain_definition import (
            DomainDefinitionWidget,
        )
        self._domain = DomainDefinitionWidget(
            self._map_widget, self._geo_assets_provider
        )
        self._domain.domainChanged.connect(self._on_domain_changed)
        region_lay.addWidget(self._domain)

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

        # GridFinder (predicted line routes) is intentionally not offered:
        # it carries only ML-predicted geometry — no voltage, no capacity —
        # so mixing it with the real OSM topology adds more noise than signal.

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

    # ------------------------------------------------------------------
    # Region (domain) — driven by the shared DomainDefinitionWidget
    # ------------------------------------------------------------------

    def _on_domain_changed(self):
        """The shared widget set a new domain (drawn polygon or GeoAsset)."""
        self._polygon = self._domain.get_polygon()
        self._bounds = self._domain.get_bounds()
        self._btn_fetch.setEnabled(self._bounds is not None)

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
            },
            "min_voltage_kv": self._spin_min_voltage.value(),
            "min_capacity_mw": self._spin_min_capacity.value(),
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
        # Stop *and wait* — a fetcher QThread destroyed while still
        # running aborts the process. stop_thread cancels cooperatively,
        # then waits (terminating only as a last resort on teardown).
        from esfex.visualization.workflows._wizard_utils import stop_thread
        for f in self._fetchers:
            stop_thread(f)
        if self._finalize_worker is not None:
            stop_thread(self._finalize_worker)
            self._finalize_worker = None

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

        import time
        self._fetch_t0 = time.perf_counter()
        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("Fetching...")
        self._progress_group.setVisible(True)
        self._summary_label.setText("")
        self._error_text.setVisible(False)
        # Stop any fetchers still running from a previous click before we
        # drop our references to them (reassigning the list would orphan a
        # running QThread → "Destroyed while thread is still running").
        self.cancel_all()
        self._features = []
        self._errors = []
        self._pending = 0
        self._fetchers = []

        self._start_fetch(self._bounds, config, self._polygon)

    def _start_fetch(self, bounds, config, polygon):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            GEMGridFetcher,
            OSMGridFetcher,
            WRIGridFetcher,
        )

        sources = config["sources"]
        min_v = config["min_voltage_kv"]
        min_cap = config["min_capacity_mw"]
        etypes = config["element_types"]

        # Reset progress bars
        for bar in (self._bar_osm, self._bar_wri, self._bar_gem):
            bar.setValue(0)
        for lbl in (self._status_osm, self._status_wri, self._status_gem):
            lbl.setText("")

        # Hide unused sources
        osm_on = sources.get("osm", False)
        wri_on = sources.get("wri", False)
        gem_on = sources.get("gem", False)

        self._lbl_osm.setVisible(osm_on)
        self._bar_osm.setVisible(osm_on)
        self._status_osm.setVisible(osm_on)

        self._lbl_wri.setVisible(wri_on)
        self._bar_wri.setVisible(wri_on)
        self._status_wri.setVisible(wri_on)

        self._lbl_gem.setVisible(gem_on)
        self._bar_gem.setVisible(gem_on)
        self._status_gem.setVisible(gem_on)

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
        return None, None

    def _finalize(self):
        # Polygon clip + dedup over country-scale feature sets would freeze the
        # GUI thread, so run them in a worker (the fetchers already are). The UI
        # is only touched back in _on_finalize_done.
        import time
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            FetchFinalizeWorker,
        )

        self._fetch_elapsed = time.perf_counter() - (self._fetch_t0 or
                                                      time.perf_counter())
        self._summary_label.setText("Processing results…")
        worker = FetchFinalizeWorker(self._features, self._polygon, self)
        worker.progress.connect(self._on_finalize_progress)
        worker.finished.connect(self._on_finalize_done)
        self._finalize_worker = worker
        worker.start()

    def _on_finalize_progress(self, message: str):
        self._summary_label.setText(message)

    def _on_finalize_done(self, features: list, counts: dict, timings: dict):
        self._features = features
        self._finalize_worker = None

        parts = []
        for ftype in ["substation", "generator", "battery", "line",
                       "transformer", "converter", "fuel_entry",
                       "fuel_storage", "road"]:
            c = counts.get(ftype, 0)
            if c:
                parts.append(f"{c} {ftype}(s)")

        elapsed = getattr(self, "_fetch_elapsed", 0.0)
        logger.info(
            "Grid fetch timing: fetch+parse=%.1fs, polygon_filter=%.1fs, "
            "deduplicate=%.1fs", elapsed,
            timings.get("polygon_filter", 0.0), timings.get("deduplicate", 0.0))

        if parts:
            self._summary_label.setText(
                f"Found {len(self._features)} features: " + ", ".join(parts)
                + f"  (fetch {elapsed:.0f}s)"
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
        map_widget=None,
    ):
        super().__init__(parent)
        self._model = model
        self._all_states = all_states if all_states is not None else {}
        self._switch_system_fn = switch_system_fn
        self._create_system_fn = create_system_fn
        self._map_widget = map_widget
        self._built = False
        self._connected = False
        self._clustering_worker = None
        # Manual node definition (map-click centroid picking + editable table)
        self._awaiting_centroid = False   # True while a map click should drop a node
        self._table_updating = False      # guard against itemChanged feedback loops
        self._polygon = []
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
            "<b>Step 2: Build & Connect</b><br>"
            "Select the target system, create nodes (auto-cluster or define "
            "them manually on the map), build the network, then auto-connect "
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
        sys_lay.addWidget(self._combo_system)

        self._btn_new_system = QPushButton("New System...")
        self._btn_new_system.clicked.connect(self._on_new_system)
        sys_lay.addWidget(self._btn_new_system)

        # Keep the combo + button left-justified instead of stretching the
        # combo across the whole window.
        sys_lay.addStretch(1)

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

        # Narrow the left ("Cluster size") column from an even 50/50 split to
        # ~40/60 so the criteria column sits closer — roughly halving the gap
        # between the two columns while leaving the right column enough width
        # for the word-wrapped criterion description.
        node_cols.addLayout(node_left, 2)
        node_cols.addLayout(node_right, 3)
        node_lay.addWidget(self._node_options_widget)

        # ── Manual node definition (shown when auto-create is OFF) ────
        # A node is a POINT (centroid); its territory is the Voronoi cell
        # drawn live on the map. Buses snap to the nearest node centroid,
        # so the visible cells match where elements actually land.
        self._manual_nodes_widget = QWidget()
        man_lay = QVBoxLayout(self._manual_nodes_widget)
        man_lay.setContentsMargins(0, 0, 0, 0)
        man_lay.addWidget(QLabel(
            "Define nodes by clicking the map or editing the table. Each "
            "node's Voronoi territory is drawn on the map."
        ))
        self._node_table = QTableWidget(0, 3)
        self._node_table.setHorizontalHeaderLabels(["Name", "Lat", "Lng"])
        self._node_table.horizontalHeader().setStretchLastSection(True)
        self._node_table.setMaximumHeight(180)
        self._node_table.itemChanged.connect(self._on_node_table_changed)
        man_lay.addWidget(self._node_table)
        man_btns = QHBoxLayout()
        self._btn_add_node_map = QPushButton("Add node (click map)")
        self._btn_add_node_map.clicked.connect(self._add_node_by_map)
        self._btn_add_node_map.setEnabled(self._map_widget is not None)
        man_btns.addWidget(self._btn_add_node_map)
        self._btn_add_node_row = QPushButton("Add node (row)")
        self._btn_add_node_row.clicked.connect(self._add_node_by_row)
        man_btns.addWidget(self._btn_add_node_row)
        self._btn_del_node = QPushButton("Delete selected")
        self._btn_del_node.clicked.connect(self._delete_selected_node)
        man_btns.addWidget(self._btn_del_node)
        man_btns.addStretch()
        man_lay.addLayout(man_btns)
        self._manual_nodes_widget.setVisible(False)
        node_lay.addWidget(self._manual_nodes_widget)

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
            "from the fetched features."
        ))

        # Single-column body: build / simplify options. The old left column of
        # auto-connect numeric parameters (max iterations, voltage mismatch
        # ratio, LV bus voltage, max interconnection distance) was retired with
        # the fabricating pipeline — faithful import does not use any of them.
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
            "thermal / hydro / geothermal / biomass; real weather-based "
            "capacity factors for wind/solar (Open-Meteo; adds ~30 s per "
            "wind/solar generator)."
        )
        avail_box.addWidget(self._chk_gen_availability)
        avail_box.addStretch()
        build_right.addLayout(avail_box)

        # Faithful import is now the ONLY build mode (#16): the built network
        # is always the real OSM topology — substations and the actual line
        # traces — with no fabricated connectivity (no bus-role inference, no
        # node star-coupling, no generator step-up chains, no long bridges).
        # The legacy fabricating pipeline produced geographically distorted
        # networks and has been retired, so there is no longer a toggle.

        # Station merge radius (#16): the ONE meaningful tolerance in faithful
        # mode. Buses of the same voltage within this distance are the same
        # physical station and are clustered into one. It is a "same station"
        # radius, NOT a reach distance — the default connects each line endpoint
        # to its substation without inventing links. Widen it if large
        # substations still fragment; tighten it if two distinct stations merge.
        radius_row = QHBoxLayout()
        radius_row.setContentsMargins(0, 0, 0, 0)
        radius_row.addWidget(QLabel("Station merge radius:"))
        self._spin_station_radius = QDoubleSpinBox()
        self._spin_station_radius.setRange(0.05, 5.0)
        self._spin_station_radius.setValue(1.0)
        self._spin_station_radius.setSingleStep(0.25)
        self._spin_station_radius.setDecimals(2)
        self._spin_station_radius.setSuffix(" km")
        self._spin_station_radius.setToolTip(
            "Faithful import only. Buses of the same voltage within this radius\n"
            "are treated as one physical station and merged — this is what\n"
            "connects each line endpoint to its substation. It is a 'same\n"
            "station' tolerance, not a reach: lines are never snapped to a far\n"
            "bus. Increase it if large substations still split into separate\n"
            "components; decrease it if two distinct stations get merged."
        )
        radius_row.addWidget(self._spin_station_radius)
        radius_row.addStretch(1)
        build_right.addLayout(radius_row)

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

        build_lay.addLayout(build_right)

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

        # Receive map clicks for manual centroid placement. The same
        # ``elementPlaced`` signal MainWindow listens to is shared; our handler
        # is gated by ``_awaiting_centroid`` so it only fires when the user
        # pressed "Add node (click map)" here, and MainWindow's own handler
        # no-ops because its ``_pick_centroid_node`` is None.
        if self._map_widget is not None:
            self._map_widget.bridge.elementPlaced.connect(
                self._on_map_centroid_picked
            )
            self._map_widget.bridge.modeReset.connect(
                self._on_centroid_pick_cancelled
            )

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
        if not self._chk_auto_nodes.isChecked():
            self._refresh_node_table()
            self._redraw_voronoi()

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
        # Manual node definition is the inverse: the table + map-click tools
        # appear only when auto-create is OFF (this IS the "nodes pre-exist"
        # path that _do_build builds on).
        self._manual_nodes_widget.setVisible(not checked)
        if not checked:
            self._refresh_node_table()
            self._redraw_voronoi()
        else:
            self._clear_voronoi()

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
    # Manual node definition (point centroid + live Voronoi territory)
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._chk_auto_nodes.isChecked():
            self._refresh_node_table()
            self._redraw_voronoi()

    def _refresh_node_table(self):
        """Rebuild the node table from the current system state."""
        if self._model is None:
            return
        self._table_updating = True
        try:
            nodes = list(self._model.state.nodes)
            self._node_table.setRowCount(len(nodes))
            for i, node in enumerate(nodes):
                self._node_table.setItem(i, 0, QTableWidgetItem(node.name))
                self._node_table.setItem(
                    i, 1, QTableWidgetItem(f"{node.centroid_lat:.5f}"))
                self._node_table.setItem(
                    i, 2, QTableWidgetItem(f"{node.centroid_lng:.5f}"))
        finally:
            self._table_updating = False

    def _on_node_table_changed(self, item):
        if self._table_updating or self._model is None:
            return
        row = item.row()
        if row >= len(self._model.state.nodes):
            return
        col = item.column()
        text = item.text().strip()
        if col == 0:
            self._model.update_node(row, name=text or f"Node {row}")
        else:
            try:
                val = float(text)
            except ValueError:
                self._refresh_node_table()   # revert to stored value
                return
            key = "centroid_lat" if col == 1 else "centroid_lng"
            self._model.update_node(row, **{key: val})
        self._redraw_voronoi()

    def _add_node_by_row(self):
        if self._model is None:
            return
        # Seed the centroid at the domain centre so it's immediately valid;
        # the user refines lat/lng in the table or by dragging on the map.
        if self._polygon:
            lat = sum(p[0] for p in self._polygon) / len(self._polygon)
            lng = sum(p[1] for p in self._polygon) / len(self._polygon)
        else:
            lat, lng = 0.0, 0.0
        idx = self._model.add_node()
        self._model.update_node(idx, centroid_lat=lat, centroid_lng=lng)
        self._refresh_node_table()
        self._redraw_voronoi()

    def _delete_selected_node(self):
        if self._model is None:
            return
        row = self._node_table.currentRow()
        if row < 0 or row >= len(self._model.state.nodes):
            return
        self._model.remove_node(row)
        self._refresh_node_table()
        self._redraw_voronoi()

    def _add_node_by_map(self):
        if self._map_widget is None:
            return
        self._awaiting_centroid = True
        self._lbl_cluster_status.setText(
            "Click on the map to place the node centroid (ESC to cancel)."
        )
        self._map_widget.set_mode("pick_centroid")

    def _on_map_centroid_picked(self, mode: str, lat: float, lng: float):
        # Only our own "Add node (click map)" requests act; MainWindow's
        # handler ignores these because its _pick_centroid_node is None.
        if mode != "pick_centroid" or not self._awaiting_centroid:
            return
        self._awaiting_centroid = False
        if self._model is not None:
            idx = self._model.add_node()
            self._model.update_node(idx, centroid_lat=lat, centroid_lng=lng)
            self._refresh_node_table()
            self._redraw_voronoi()
        self._lbl_cluster_status.setText("")
        # Back to navigation so later clicks don't keep dropping nodes.
        try:
            self._map_widget.set_mode("select")
        except Exception:
            pass

    def _on_centroid_pick_cancelled(self):
        if self._awaiting_centroid:
            self._awaiting_centroid = False
            self._lbl_cluster_status.setText("")

    def _redraw_voronoi(self):
        """Draw each node's Voronoi territory clipped to the domain polygon."""
        if self._map_widget is None or self._chk_auto_nodes.isChecked():
            return
        import json

        from esfex.visualization.workflows.voronoi_cells import (
            compute_voronoi_cells,
        )

        nodes = list(self._model.state.nodes) if self._model else []
        centroids = [(n.centroid_lat, n.centroid_lng) for n in nodes]
        if not centroids or len(self._polygon) < 3:
            self._clear_voronoi()
            return
        cells = compute_voronoi_cells(centroids, self._polygon)
        features = []
        for node, ring in zip(nodes, cells):
            if len(ring) < 3:
                continue
            coords = [[lng, lat] for lat, lng in ring]  # GeoJSON is [lng, lat]
            coords.append(coords[0])                     # close the ring
            features.append({
                "type": "Feature",
                "properties": {"name": node.name},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            })
        if not features:
            self._clear_voronoi()
            return
        fc = {"type": "FeatureCollection", "features": features}
        self._map_widget.add_geo_asset(
            "voronoi_cells", json.dumps(fc), "Node territories", "#3498db"
        )

    def _clear_voronoi(self):
        if self._map_widget is not None:
            try:
                self._map_widget.remove_geo_asset("voronoi_cells")
            except Exception:
                pass

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
            # Drop the territory overlay before drawing the real network.
            self._clear_voronoi()
            self._start_build(None)  # manual nodes — keep existing nodes

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

        # Stop a previous clustering run before replacing the reference —
        # otherwise the old QThread is dropped while still running.
        from esfex.visualization.workflows._wizard_utils import stop_thread
        stop_thread(getattr(self, "_clustering_worker", None))

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

    def cancel_all(self):
        """Stop the clustering worker (called on wizard close/cancel).

        Without this the ``NodeClusteringWorker`` QThread can be destroyed
        while still running — the crash seen on large regions (e.g. a
        whole country) where clustering is still busy at teardown.
        """
        from esfex.visualization.workflows._wizard_utils import stop_thread
        stop_thread(getattr(self, "_clustering_worker", None))

    def _on_clustering_progress(self, pct: int, msg: str):
        self._cluster_progress.setValue(pct)
        self._lbl_cluster_status.setText(msg)

    def _on_clustering_done(self, result):
        # Naming finished. Hand the heavy node-creation + build off to a worker
        # thread so the GUI stays responsive (large systems used to freeze the
        # Studio for tens of minutes here).
        self._cluster_progress.setValue(100)
        self._lbl_cluster_status.setText(
            f"Created {result.n_clusters} nodes via "
            f"{result.criterion_used} clustering."
        )
        self._start_build(result.node_positions)

    def _on_clustering_error(self, error_msg: str):
        self._cluster_progress.setVisible(False)
        self._lbl_cluster_status.setText(f"Clustering error: {error_msg}")
        self._btn_build.setEnabled(True)

    def _start_build(self, node_positions):
        """Start the build pipeline on a background worker.

        ``node_positions`` is the auto-clustering result, or ``None`` to build
        on the user's existing (manual) nodes. Widget values are captured here
        on the main thread; the worker never touches a Qt widget.
        """
        from esfex.visualization.workflows.grid_mapping_build_worker import (
            BuildParams,
            GridBuildWorker,
        )

        self._lbl_build_status.setText("Building network\u2026")
        main_window = self.window()
        params = BuildParams(
            node_positions=node_positions,
            n_clusters=len(node_positions) if node_positions else 0,
            criterion_used="",
            features=self._features,
            config=self._config,
            station_radius_km=self._spin_station_radius.value(),
            simplify_level=self._combo_simplify.currentData() or 0,
            min_component=self._spin_min_component.value(),
            gen_availability=self._chk_gen_availability.isChecked(),
            use_weather=True,  # weather-based CF is now the default behaviour
            cfg_path=getattr(main_window, "_config_path", None),
        )

        # Block per-element signals so the thousands of created elements don't
        # each render synchronously; the worker mutates state and we repaint
        # once via stateLoaded when it finishes.
        self._model.blockSignals(True)

        # Indeterminate progress + repurpose the Build button as Cancel.
        self._cluster_progress.setRange(0, 0)
        self._cluster_progress.setVisible(True)
        self._btn_build.setText("Cancel")
        try:
            self._btn_build.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._btn_build.clicked.connect(self._on_build_cancel)
        self._btn_build.setEnabled(True)

        self._build_worker = GridBuildWorker(self._model, params, self)
        self._build_worker.progress.connect(self._on_build_progress)
        self._build_worker.finished.connect(self._on_build_done)
        self._build_worker.error.connect(self._on_build_error)
        self._build_worker.start()

    def _on_build_progress(self, text: str):
        self._lbl_build_status.setText(text)

    def _on_build_cancel(self):
        worker = getattr(self, "_build_worker", None)
        if worker is not None and worker.isRunning():
            self._lbl_build_status.setText("Cancelling\u2026")
            self._btn_build.setEnabled(False)
            worker.cancel()

    def _restore_build_button(self):
        self._cluster_progress.setRange(0, 100)
        self._cluster_progress.setVisible(False)
        self._btn_build.setText("Build Network")
        try:
            self._btn_build.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._btn_build.clicked.connect(self._do_build)
        self._btn_build.setEnabled(True)

    def _on_build_done(self, res: dict):
        # Back on the main thread: unblock signals and repaint once.
        self._model.blockSignals(False)
        self._build_worker = None
        self._phase_timings = res.get("phase_timings", [])
        self._model.stateLoaded.emit()

        # Assemble the result text.
        sections = []
        if res.get("build_summary"):
            sections.append(res["build_summary"])
        if res.get("simplify_summary"):
            sections.append(
                "\u2500\u2500 Simplification \u2500\u2500\n" + res["simplify_summary"])
        if res.get("island_summary"):
            sections.append(
                "\u2500\u2500 Isolated Cleanup \u2500\u2500\n" + res["island_summary"])
        if self._phase_timings:
            total_s = sum(dt for _, dt in self._phase_timings)
            timing_lines = [f"  {label}: {dt:.1f}s" for label, dt in self._phase_timings]
            timing_lines.append(f"  Total: {total_s:.1f}s")
            sections.append(
                "\u2500\u2500 Timing \u2500\u2500\n" + "\n".join(timing_lines))
        self._result_text.setPlainText("\n\n".join(sections))

        if res.get("cancelled"):
            self._lbl_build_status.setText("Build cancelled (partial network shown).")
        else:
            level = res.get("simplify_level", 0)
            head = "Network built"
            head += (f"; simplified at level {level}." if level > 0
                     else "; cleanup pass applied.")
            self._lbl_build_status.setText(head)

        self._built = True
        self._connected = True
        self._restore_build_button()
        self.buildFinished.emit()

    def _on_build_error(self, msg: str):
        self._model.blockSignals(False)
        self._build_worker = None
        # Show whatever partial network was built before the failure.
        try:
            self._model.stateLoaded.emit()
        except Exception:
            pass
        self._lbl_build_status.setText(f"Error: {msg}")
        self._restore_build_button()


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
    bridge_disconnected: bool = True,
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

    # When fabrication is disabled (the real-topology method, #16) equipment
    # may only be chained to a *nearby* HV bus; equipment whose nearest bus is
    # farther than this is left unconnected (its island is dropped) rather than
    # reconnected with a long artificial line.
    _NO_FABRICATE_MAX_KM = 5.0
    equip_max_km = (
        max_connection_km if bridge_disconnected
        else min(max_connection_km, _NO_FABRICATE_MAX_KM)
    )

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
        # Real-topology method (#16): do NOT fabricate bridges between
        # disconnected components. Connectivity comes from the real geometry
        # (lines split at the substations they cross); whatever remains
        # disconnected is the genuine topology, kept (largest component) or
        # dropped by drop_isolated_components — never glued with invented
        # straight lines.
        if bridge_disconnected:
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
        elif iteration == 1:
            log.append(
                "  Connectivity: real-topology mode — not fabricating bridges "
                "(largest component kept, remote islands dropped)."
            )

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
                max_connection_km=equip_max_km,
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

    # Spatial index over the main component so each bridge/equipment search
    # is O(log n) instead of scanning every main-component bus. Kept hot
    # across all issues; grows as components merge in.
    nn_main = _BusNN(state, main_comp)

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
                best_hv, best_dist = nn_main.nearest(
                    eq_lat, eq_lng, min_voltage_kv=DEFAULT_LV_KV,
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
            # Query the main-component index with each (small) isolated-
            # component bus; keep the closest pair. O(|comp|·log n) vs the
            # old O(|comp|·|main|) all-pairs scan.
            best_iso = best_main_bid = None
            best_dist = float("inf")
            for bid in comp:
                b = state.buses.get(bid)
                if not b or (b.latitude == 0.0 and b.longitude == 0.0):
                    continue
                cand, d = nn_main.nearest(b.latitude, b.longitude)
                if cand is not None and d < best_dist:
                    best_dist = d
                    best_iso = bid
                    best_main_bid = cand
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

        # Merge into main for subsequent issues (set + spatial index).
        for bid in comp:
            nn_main.add(bid)
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

    # O(1) line lookup + deferred batch removal: removing each fixed line via
    # model.remove_line (an O(L) list rebuild) per issue is O(L²) on large
    # networks. Index once, collect removals, drop them in a single pass.
    lines_by_id = {ln.line_id: ln for ln in state.transmission_lines}
    to_remove: set[str] = set()

    for issue in issues:
        line_id = issue["line_id"]
        from_bus_id = issue["from_bus"]
        to_bus_id = issue["to_bus"]
        v_from = issue["v_from"]
        v_to = issue["v_to"]

        # Verify line still exists (previous fix may have removed it).
        if line_id in to_remove:
            continue
        ln = lines_by_id.get(line_id)
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
            # Schedule the direct line for removal (batched after the loop).
            to_remove.add(line_id)

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

    # Drop all replaced direct lines in one O(L) pass.
    model.remove_lines(to_remove)

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

    # Spatial index over the main component (O(log n) HV-bus search per
    # equipment item instead of scanning every main-component bus). The
    # all-buses fallback index is built lazily — only HV buses present at
    # the start matter as targets (this loop creates only LV buses).
    nn_main = _BusNN(state, main_comp)
    nn_all = None

    # Index existing connection lines by their from_endpoint ONCE, and defer
    # removals to a single batch pass. Scanning every line per equipment and
    # removing one-by-one (each an O(L) rebuild) was O(E·L) — quadratic. New
    # chain lines get fresh ids absent from the removal set, so deferring is
    # safe.
    lines_by_from_ep: dict[tuple, list[str]] = {}
    for ln in state.transmission_lines:
        if ln.from_endpoint:
            lines_by_from_ep.setdefault(
                (ln.from_endpoint.element_type, ln.from_endpoint.element_id),
                [],
            ).append(ln.line_id)
    to_remove_all: set[str] = set()

    for audit in failed_audits:
        etype = audit["etype"]
        eid = audit["eid"]
        obj = audit["obj"]
        reason = audit["failure_reason"]

        # ── 1. Remove existing connection lines for this equipment ─
        # Only remove lines whose from_endpoint matches this equipment
        # to avoid duplicate connection lines. Orphaned LV buses and
        # transformers will be cleaned up by the simplify step.
        to_remove_all.update(lines_by_from_ep.get((etype, eid), ()))

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
        target_id, dist_km = nn_main.nearest(
            lat, lng, min_voltage_kv=lv_voltage_kv,
        )

        # If beyond max distance, search ALL buses within range
        # (allows connecting to local island networks).
        if (target_id is None
                or dist_km > max_connection_km):
            if nn_all is None:
                nn_all = _BusNN(state, set(state.buses.keys()))
            target_id, dist_km = nn_all.nearest(
                lat, lng, min_voltage_kv=lv_voltage_kv,
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

    # Drop all superseded equipment connection lines in one O(L) pass.
    model.remove_lines(to_remove_all)

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


class _BusNN:
    """Spatial nearest-bus index over a candidate set of bus IDs.

    Replaces the O(n) linear scans in :func:`_find_closest_bus_pair` and
    :func:`_find_nearest_hv_bus_in` — which made disconnected-component
    bridging and equipment chaining O(n²) and hung the build on
    country-scale networks (e.g. Japan, ~20k buses → 12+ min) — with KD-tree
    queries on an equirectangular projection.  Buses can be added as
    components merge; the tree is rebuilt once enough have accumulated.
    Falls back to an exact linear scan when SciPy is unavailable.
    """

    _REBUILD_SLACK = 256

    def __init__(self, state, candidates):
        self._state = state
        try:
            from scipy.spatial import cKDTree
            self._cKDTree = cKDTree
        except Exception:
            self._cKDTree = None
        self._build(list(candidates))

    def _build(self, bids):
        st = self._state
        ids: list[str] = []
        lats: list[float] = []
        lngs: list[float] = []
        for bid in bids:
            b = st.buses.get(bid)
            if not b or (b.latitude == 0.0 and b.longitude == 0.0):
                continue
            ids.append(bid)
            lats.append(b.latitude)
            lngs.append(b.longitude)
        self._ids = ids
        self._extra: list[str] = []  # added since the last (re)build
        self._tree = None
        if self._cKDTree is not None and ids:
            lat_arr = np.asarray(lats, dtype=float)
            lng_arr = np.asarray(lngs, dtype=float)
            # Equirectangular projection: scale lng by cos(mean lat) so plain
            # Euclidean distance tracks great-circle distance closely over a
            # country-sized region (the exact winner is refined by haversine).
            self._coslat = math.cos(math.radians(float(lat_arr.mean())))
            proj = np.column_stack((lat_arr, lng_arr * self._coslat))
            self._tree = self._cKDTree(proj)

    def add(self, bid: str) -> None:
        b = self._state.buses.get(bid)
        if not b or (b.latitude == 0.0 and b.longitude == 0.0):
            return
        self._extra.append(bid)
        if len(self._extra) > self._REBUILD_SLACK:
            self._build(self._ids + self._extra)

    def _scan(self, ids, lat, lng, min_voltage_kv):
        st = self._state
        best_id = None
        best_dist = float("inf")
        for bid in ids:
            b = st.buses.get(bid)
            if not b or (b.latitude == 0.0 and b.longitude == 0.0):
                continue
            if min_voltage_kv is not None and b.voltage_kv <= min_voltage_kv:
                continue
            d = _haversine_km(lat, lng, b.latitude, b.longitude)
            if d < best_dist:
                best_dist = d
                best_id = bid
        return best_id, best_dist

    def nearest(self, lat, lng, min_voltage_kv=None):
        """Nearest candidate bus to ``(lat, lng)`` → ``(bus_id, dist_km)``."""
        best_id = None
        best_dist = float("inf")
        if self._tree is not None and self._ids:
            n = len(self._ids)
            # No voltage filter → the projected nearest IS the answer (k=1).
            # With a filter, widen k so a filtered-out nearest neighbour does
            # not mask a valid bus just behind it.
            k = 1 if min_voltage_kv is None else min(32, n)
            q = np.array([lat, lng * self._coslat])
            _dd, ii = self._tree.query(q, k=k)
            idxs = [int(ii)] if k == 1 else [int(x) for x in np.atleast_1d(ii)]
            for i in idxs:
                if i < 0 or i >= n:
                    continue
                bid = self._ids[i]
                b = self._state.buses.get(bid)
                if not b:
                    continue
                if (min_voltage_kv is not None
                        and b.voltage_kv <= min_voltage_kv):
                    continue
                d = _haversine_km(lat, lng, b.latitude, b.longitude)
                if d < best_dist:
                    best_dist = d
                    best_id = bid
            # Voltage filter eliminated all k nearest → exact fallback scan
            # over the full tree set (rare on real networks).
            if best_id is None and min_voltage_kv is not None:
                best_id, best_dist = self._scan(
                    self._ids, lat, lng, min_voltage_kv)
        else:
            best_id, best_dist = self._scan(
                self._ids, lat, lng, min_voltage_kv)
        # Recently-added buses not yet folded into the tree (small list).
        if self._extra:
            e_id, e_dist = self._scan(self._extra, lat, lng, min_voltage_kv)
            if e_dist < best_dist:
                best_id, best_dist = e_id, e_dist
        return best_id, best_dist


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


def write_forecast_demand_csvs(nodes, result, out_dir: Path) -> int:
    """Persist a demand forecast onto the model's nodes.

    Writes each node's hourly series (the full multi-year forecast when
    available, otherwise the single representative year — the same choice the
    standalone demand wizard's export makes) to ``out_dir/demand_<node>.csv``
    and stores a full :class:`GuiNodeDemand` (``csv_path`` + ``data`` + stats)
    on the node. That is what lets the serializer emit the system's
    ``demand_paths`` and the runner load per-node demand. Returns the number of
    nodes that received demand. The series stays in memory even if a file write
    fails.
    """
    import re

    from esfex.visualization.data.gui_model import GuiNodeDemand

    series = getattr(result, "demand_multi_year", None)
    if series is None:
        series = getattr(result, "demand", None)
    if series is None:
        return 0

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    peak_list = list(getattr(result, "peak_mw", []) or [])
    ncols = series.shape[1] if series.ndim > 1 else 1
    used: set[str] = set()
    applied = 0
    for i, node in enumerate(nodes):
        if i >= ncols:
            break
        col = series[:, i] if series.ndim > 1 else series
        safe = re.sub(
            r"[^0-9A-Za-z._-]+", "_",
            node.name or f"node_{i}").strip("_") or f"node_{i}"
        base, k = safe, 1
        while safe in used:
            safe, k = f"{base}_{k}", k + 1
        used.add(safe)
        data_list = [float(x) for x in col]
        csv_path = None
        try:
            path = out_dir / f"demand_{safe}.csv"
            np.savetxt(str(path), col, fmt="%.4f")
            csv_path = str(path)
        except Exception:
            pass  # keep the series in memory even if the write fails
        node.demand = GuiNodeDemand(
            csv_path=csv_path,
            data=data_list,
            num_hours=len(data_list),
            peak_mw=(peak_list[i] if i < len(peak_list)
                     else float(max(data_list, default=0.0))),
            total_mwh=float(sum(data_list)),
        )
        applied += 1
    return applied


class GridMappingDemandStep(QWidget):
    """Forecast demand per node and distribute among busbars.

    Integrates ML-based demand forecasting into the grid-mapping wizard,
    reusing domain bounds from Step 1 and node positions from Step 3.

    Sub-sections:
      1. Demand Forecast — auto-detect country, fetch WB/ERA5, run ML
      2. Forecast Results — per-node peak/GWh/LF table
      3. Bus Distribution — building footprints → demand fractions
    """

    # Carries a no-arg callable from a worker thread to the GUI thread. Worker
    # threads (country detection, World Bank fetch, demand forecast) must never
    # touch Qt widgets directly; they emit their UI updates through this signal,
    # which Qt delivers as a queued call on the main thread.
    _ui_call = Signal(object)

    def __init__(self, model=None, all_states=None, map_widget=None,
                 parent=None):
        super().__init__(parent)
        self._ui_call.connect(self._on_ui_call)
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
        self._forecast_result_raw = None   # uncorrected demand_multi_year copy
        self._forecast_nodes: list = []
        self._forecast_worker = None
        # User-supplied observed hourly demand per node index (validation).
        self._observed: dict[int, "GuiNodeDemand"] = {}
        # When set (single-node mode, launched from the node panel) the whole
        # step — forecast, validation, distribution — is scoped to this node.
        self._target_node: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        _hdr = QLabel(
            "<b>Step 6: Demand Forecast & Distribution</b><br>"
            "Generate hourly demand profiles per node using ML models, "
            "then distribute among busbars via building density."
        )
        _hdr.setWordWrap(True)
        layout.addWidget(_hdr)

        # ==============================================================
        # Section 1: Demand Forecast Configuration
        # ==============================================================
        forecast_group = QGroupBox("1. Demand Forecast")
        fg = QVBoxLayout(forecast_group)

        # Country + base config (2 columns). Tighter columns: half the default
        # 6 px horizontal gap between columns.
        config_grid = QGridLayout()
        config_grid.setHorizontalSpacing(3)

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

        # GDP scenario + elasticity (collapsible row). GDP growth is no longer
        # a fixed user rate — it follows the gridded SSP GDP trajectory (real,
        # non-linear, per node). The user picks the SSP scenario instead.
        growth_row = QHBoxLayout()
        # Tighter inter-column gap so the three label+widget pairs (GDP /
        # Elasticity / Efficiency) fit within the default window width even on
        # HiDPI screens where the proportional fonts make each pair wider.
        growth_row.setSpacing(3)
        growth_row.addWidget(QLabel("GDP scenario:"))
        self._combo_gdp_ssp = QComboBox()
        for ssp in ["SSP1", "SSP2", "SSP3", "SSP4", "SSP5"]:
            self._combo_gdp_ssp.addItem(ssp, ssp)
        self._combo_gdp_ssp.setCurrentIndex(1)   # SSP2
        self._combo_gdp_ssp.setToolTip(
            "Shared Socioeconomic Pathway for the GDP trajectory. The demand\n"
            "forecast follows the gridded SSP GDP path (per node, year by year),\n"
            "replacing a fixed growth rate. SSP2 = middle-of-the-road."
        )
        growth_row.addWidget(self._combo_gdp_ssp)

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

        self._btn_view_demand = QPushButton(tr("grid_builder.view_demand"))
        self._btn_view_demand.clicked.connect(self._on_view_demand)
        self._btn_view_demand.setEnabled(False)
        rg.addWidget(self._btn_view_demand)

        layout.addWidget(results_group)

        # ==============================================================
        # Section 2b: Validate against observed demand (optional)
        # ==============================================================
        val_group = QGroupBox("2b. Validate against observed demand (optional)")
        vg = QVBoxLayout(val_group)
        _val_intro = QLabel(
            "Import your own observed hourly demand for a node to validate the "
            "forecast and, optionally, bend it toward the observation. The "
            "correction scales the forecast by a month×hour factor derived "
            "from the base year and applies it to every year, so the future "
            "growth trajectory is preserved."
        )
        _val_intro.setWordWrap(True)
        vg.addWidget(_val_intro)

        imp_row = QHBoxLayout()
        imp_row.addWidget(QLabel("Node:"))
        self._combo_obs_node = QComboBox()
        imp_row.addWidget(self._combo_obs_node, 1)
        self._btn_load_observed = QPushButton("Load observed CSV…")
        self._btn_load_observed.clicked.connect(self._load_observed_csv)
        self._btn_load_observed.setEnabled(False)
        imp_row.addWidget(self._btn_load_observed)
        self._btn_clear_observed = QPushButton("Clear all")
        self._btn_clear_observed.clicked.connect(self._clear_observed)
        imp_row.addWidget(self._btn_clear_observed)
        vg.addLayout(imp_row)

        self._obs_metrics_table = QTableWidget(0, 6)
        self._obs_metrics_table.setHorizontalHeaderLabels([
            "Node", "MAPE %", "RMSE (MW)", "Peak err %", "Energy err %", "Corr",
        ])
        self._obs_metrics_table.horizontalHeader().setStretchLastSection(True)
        self._obs_metrics_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._obs_metrics_table.setMinimumHeight(90)
        vg.addWidget(self._obs_metrics_table)

        val_btns = QHBoxLayout()
        self._btn_overlay_observed = QPushButton("Overlay")
        self._btn_overlay_observed.setToolTip(
            "Open the chart with the observed series overlaid on the forecast.")
        self._btn_overlay_observed.clicked.connect(self._on_view_demand)
        self._btn_overlay_observed.setEnabled(False)
        val_btns.addWidget(self._btn_overlay_observed)
        self._chk_apply_correction = QCheckBox("Apply month×hour correction")
        self._chk_apply_correction.setToolTip(
            "Scale the forecast so the base year matches your observed series "
            "in each (month, hour) bin; the same factor is applied to all "
            "years so growth is preserved."
        )
        self._chk_apply_correction.toggled.connect(
            self._on_apply_correction_toggled)
        self._chk_apply_correction.setEnabled(False)
        val_btns.addWidget(self._chk_apply_correction)
        val_btns.addStretch()
        vg.addLayout(val_btns)

        self._lbl_validation_status = QLabel("")
        self._lbl_validation_status.setWordWrap(True)
        self._lbl_validation_status.setStyleSheet("color: #888; font-size: 11px;")
        vg.addWidget(self._lbl_validation_status)

        layout.addWidget(val_group)

        # ==============================================================
        # Section 3: Bus Distribution (existing logic preserved)
        # ==============================================================
        dist_group = QGroupBox(
            "3. Bus Distribution (spatial demand or building footprints)"
        )
        self._dist_group = dist_group   # toggled in single-node mode
        dg = QVBoxLayout(dist_group)
        _dist_intro = QLabel(
            "Distribute each node\u2019s forecast demand among its busbars. "
            "Prefer \u201cDistribute by spatial demand\u201d (uses the model\u2019s "
            "per-cell demand directly); building footprints remain as an "
            "alternative. Only nodes with \u2265 2 buses need distribution."
        )
        # Without word-wrap this long line demands ~1233 px on a single row,
        # which forced the whole scroll panel (and every section above it) to
        # that width and produced a permanent horizontal scrollbar.
        _dist_intro.setWordWrap(True)
        dg.addWidget(_dist_intro)

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
        # Alternative: distribute by the density model's own per-cell demand
        # (the actual forecast demand, not a building-footprint proxy).
        self._btn_spatial_dist = QPushButton("Distribute by spatial demand")
        self._btn_spatial_dist.setToolTip(
            "Assign each 0.25° demand-density cell from the forecast to its\n"
            "nearest bus and split each node's demand by the cells' demand.\n"
            "Uses the model's actual spatial demand instead of building\n"
            "footprints. Requires the density-engine forecast to be run first."
        )
        self._btn_spatial_dist.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 4px 8px;"
        )
        self._btn_spatial_dist.clicked.connect(self._distribute_by_spatial_demand)
        dist_btn_row.addWidget(self._btn_spatial_dist)
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

    def _nodes_in_scope(self):
        """Nodes this step operates on: the single target node when scoped
        (node-panel mode), otherwise every node in the active system."""
        state = self._model.state if self._model else None
        if state is None:
            return []
        if self._target_node is not None:
            node = self._model.get_node(self._target_node)
            return [node] if node is not None else []
        return list(state.nodes)

    def set_single_node(self, model, node_index: int):
        """Scope the whole step to one node (launched from its attributes panel).

        Derives a study region from the node's own electrical infrastructure:
        the bounding box of its load/mixed buses (padded), or — when the node
        has no buses yet — a box around its centroid. Hides the bus-distribution
        section when the node has fewer than two demand-carrying buses.
        """
        self._target_node = node_index
        node = model.get_node(node_index)
        buses = [
            b for b in model.state.buses.values()
            if b.parent_node == node_index and b.role in ("load", "mixed")
            and (b.latitude or b.longitude)
        ] if node is not None else []
        if buses:
            lats = [b.latitude for b in buses]
            lons = [b.longitude for b in buses]
            s, w, n, e = min(lats), min(lons), max(lats), max(lons)
            # Pad to a minimum span so the density model has ≥1 grid cell.
            pad_lat = max(0.05, (0.3 - (n - s)) / 2.0)
            pad_lon = max(0.05, (0.3 - (e - w)) / 2.0)
            bounds = (s - pad_lat, w - pad_lon, n + pad_lat, e + pad_lon)
        elif node is not None:
            r = 0.25
            bounds = (node.centroid_lat - r, node.centroid_lng - r,
                      node.centroid_lat + r, node.centroid_lng + r)
        else:
            bounds = None
        # Adaptive infrastructure filter: distribution only makes sense with ≥2
        # demand-carrying buses to split between.
        load_buses = [
            b for b in model.state.buses.values()
            if b.parent_node == node_index and b.role in ("load", "mixed")
        ] if node is not None else []
        self._dist_group.setVisible(len(load_buses) >= 2)
        self.set_inputs(bounds, model, self._all_states)

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
        """Detect the region's countries offline, with a Nominatim fallback.

        Tests the grid node coordinates (or a sample of the region's bounding
        box) against the bundled country polygons, so a region spanning several
        countries surfaces all of them and territories (Puerto Rico, ...) are
        not folded into their sovereign state. Nominatim is only used when the
        offline lookup finds nothing.
        """
        if self._bounds is None:
            return

        self._btn_detect_country.setEnabled(False)
        self._lbl_forecast_status.setText("Detecting country...")

        # Prefer the actual node coordinates; fall back to a bbox sample.
        points: list[tuple[float, float]] = []
        if self._model is not None:
            for nd in self._nodes_in_scope():
                lat = getattr(nd, "centroid_lat", None)
                lng = getattr(nd, "centroid_lng", None)
                if lat is not None and lng is not None and (lat or lng):
                    points.append((lat, lng))
        bounds = self._bounds

        import threading

        def _populate(countries: list[dict]):
            self._combo_country.blockSignals(True)
            self._combo_country.clear()
            for c in countries:
                self._combo_country.addItem(
                    f"{c['name']} ({c['iso3']})", c)
            self._combo_country.blockSignals(False)
            if len(countries) == 1:
                c = countries[0]
                self._lbl_forecast_status.setText(
                    f"Country: {c['name']} ({c['iso3']})")
            else:
                self._lbl_forecast_status.setText(
                    f"{len(countries)} countries detected — select one")
            self._btn_detect_country.setEnabled(True)

        def _do_detect():
            try:
                from esfex.visualization.workflows.country_detection import (
                    detect_countries, sample_bbox,
                )
                pts = points or sample_bbox(bounds, n=8)
                countries = detect_countries(pts)
                if countries:
                    self._ui_call.emit(
                        lambda c=countries: _populate(c))
                    return
                # Offline lookup empty (e.g. an entirely offshore region) —
                # fall back to Nominatim on the centroid, in English.
                import requests
                south, west, north, east = bounds
                lat = (south + north) / 2.0
                lon = (west + east) / 2.0
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"lat": lat, "lon": lon, "format": "json",
                            "zoom": 3, "accept-language": "en"},
                    headers={"User-Agent": "ESFEX-Grid/1.0"}, timeout=10,
                )
                addr = resp.json().get("address", {})
                cc = addr.get("country_code", "").upper()
                name = addr.get("country", cc)
                from esfex.visualization.workflows.country_detection import (
                    _iso2_to_iso3,
                )
                iso3 = _iso2_to_iso3(cc)
                self._ui_call.emit(lambda c=[{"iso2": cc, "iso3": iso3,
                                              "name": name}]: _populate(c))
            except Exception as exc:
                self._ui_call.emit(lambda e=exc: (
                    self._lbl_forecast_status.setText(
                        f"Country detection failed: {e}"),
                    self._btn_detect_country.setEnabled(True),
                ))

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
                self._ui_call.emit(lambda wb=wb: (
                    self._forecast_progress.setValue(40),
                    self._lbl_forecast_status.setText(
                        f"WB: GDP/cap=${wb.get('gdp_per_capita', 0):,.0f}, "
                        f"Pop={wb.get('population', 0):,.0f}. "
                        f"Fetching ERA5 temperature..."
                    ),
                ))

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
                self._ui_call.emit(
                    lambda wb=wb, temp=temp, weather_year=weather_year: (
                        self._forecast_progress.setValue(60),
                        self._lbl_forecast_status.setText(
                            f"WB: GDP/cap=${wb.get('gdp_per_capita', 0):,.0f}, "
                            f"Pop={wb.get('population', 0):,.0f}, "
                            "kWh/cap="
                            f"{wb.get('electric_consumption_kwh_capita', 0):,.0f}. "
                            f"ERA5: {len(temp)} hours ({weather_year}). "
                            f"Ready to forecast."
                        ),
                        self._btn_forecast.setEnabled(True),
                        self._btn_fetch_data.setEnabled(True),
                    )
                )

            except Exception as exc:
                self._ui_call.emit(lambda e=exc: (
                    self._lbl_forecast_status.setText(f"Fetch error: {e}"),
                    self._btn_fetch_data.setEnabled(True),
                    self._forecast_progress.setValue(0),
                ))

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_ui_call(self, fn):
        """Run a worker-thread-supplied callable on the GUI thread.

        Connected to ``_ui_call``; because the widget lives on the main thread,
        emits from a worker thread are delivered here as a queued call, so the
        wrapped widget updates execute safely on the GUI thread.
        """
        try:
            fn()
        except Exception:
            logger.exception("Deferred GUI update failed")

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

        nodes = self._nodes_in_scope()
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
            demand_gdp_elasticity=self._spin_elasticity.value(),
            efficiency_improvement=self._spin_efficiency.value(),
            ml_engine=engine_key,
            force_archetype=(engine_key == "archetype"),
            # GDP trajectory follows the gridded SSP rasters (not a fixed rate).
            ssp_scenario=self._combo_gdp_ssp.currentData() or "SSP2",
        )

        # Build proxy data from nodes. The drawn-area bounds give the density
        # model the study region for the per-node area partition and the SSP
        # GDP / population raster sampling.
        proxy = ProxyData(
            building_weights=[1.0 / num_nodes] * num_nodes,
            population_weights=[1.0 / num_nodes] * num_nodes,
            nightlight_weights=[1.0 / num_nodes] * num_nodes,
            landuse_weights=[1.0 / num_nodes] * num_nodes,
            node_residential_fraction=[0.40] * num_nodes,
            node_commercial_fraction=[0.35] * num_nodes,
            node_industrial_fraction=[0.25] * num_nodes,
            node_lats=[n.centroid_lat for n in nodes],
            node_lons=[n.centroid_lng for n in nodes],
            node_names=[n.name for n in nodes],
            bounds=self._bounds,
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
        )

        # Wire the per-year SSP growth rates into the macro so the demand
        # trajectory is non-linear. Without these the density engine sees a
        # FLAT population, and — if it falls back to the shape-ML engine —
        # ``_compute_annual_trajectory`` uses the constant scalar GDP rate,
        # producing an almost-linear curve. The standalone demand wizard
        # already wires these; the grid-mapping step was missing them.
        ssp_key = self._combo_gdp_ssp.currentData() or "SSP2"
        try:
            from esfex.models.demand_projection import _ssp_growth_rates
            macro.gdp_growth_by_year = _ssp_growth_rates(ssp_key, "gdp")
            macro.pop_growth_by_year = _ssp_growth_rates(ssp_key, "pop")
        except Exception as exc:  # bundled multipliers should always resolve
            logger.warning("SSP growth-rate wiring failed (%s); "
                           "trajectory falls back to scalar rates.", exc)

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
            # Heavy compute runs here; every widget update is marshalled to the
            # GUI thread via _ui_call (touching widgets here would crash Qt).
            try:
                builder = DemandProfileBuilder(cfg)
                result = builder.build(
                    proxy, macro, meteo,
                    progress_callback=lambda p, m: self._ui_call.emit(
                        lambda p=p, m=m: (
                            self._forecast_progress.setValue(65 + int(p * 0.30)),
                            self._lbl_forecast_status.setText(m),
                        )
                    ),
                )
                self._ui_call.emit(
                    lambda: self._populate_forecast_results(
                        result, nodes, num_nodes)
                )
            except Exception as exc:
                logger.exception("Demand forecast failed")
                self._ui_call.emit(lambda e=exc: (
                    self._lbl_forecast_status.setText(f"Forecast error: {e}"),
                    self._forecast_progress.setValue(0),
                ))
            self._ui_call.emit(lambda: self._btn_forecast.setEnabled(True))

        threading.Thread(target=_do_forecast, daemon=True).start()

    def _populate_forecast_results(self, result, nodes, num_nodes):
        """Render the forecast table + summary on the GUI thread."""
        import numpy as np
        self._forecast_result = result
        # Cache the uncorrected multi-year series so the observed-data
        # correction can be toggled on/off reversibly.
        _my = getattr(result, "demand_multi_year", None)
        self._forecast_result_raw = (
            np.array(_my, dtype=float).copy() if _my is not None else None
        )
        self._forecast_progress.setValue(95)

        self._forecast_table.setRowCount(num_nodes)
        for i, node in enumerate(nodes):
            peak = result.peak_mw[i] if i < len(result.peak_mw) else 0
            gwh = result.annual_gwh[i] if i < len(result.annual_gwh) else 0
            lf = result.load_factor[i] if i < len(result.load_factor) else 0
            self._forecast_table.setItem(i, 0, QTableWidgetItem(node.name))
            self._forecast_table.setItem(i, 1, QTableWidgetItem(f"{peak:.1f}"))
            self._forecast_table.setItem(i, 2, QTableWidgetItem(f"{gwh:.1f}"))
            self._forecast_table.setItem(i, 3, QTableWidgetItem(f"{lf:.3f}"))

        self._lbl_forecast_summary.setText(
            f"<b>System total:</b> Peak={result.total_peak_mw:.1f} MW, "
            f"Annual={result.total_annual_gwh:.1f} GWh, "
            f"LF={result.total_load_factor:.3f} "
            f"&mdash; Source: {result.demand_source}"
        )
        self._lbl_forecast_summary.setStyleSheet("color: #27ae60; padding: 4px;")
        self._forecast_progress.setValue(100)
        self._lbl_forecast_status.setText("Forecast complete.")

        # Remember the forecast nodes so the demand visualizer can read the
        # per-node hourly series straight from the result.
        self._forecast_nodes = list(nodes)
        self._btn_view_demand.setEnabled(True)

        # Observed-demand validation: refresh the node picker, reset the
        # correction toggle (a fresh forecast), and re-score any observed
        # series the user already loaded.
        self._refresh_obs_node_combo()
        self._btn_load_observed.setEnabled(True)
        self._chk_apply_correction.blockSignals(True)
        self._chk_apply_correction.setChecked(False)
        self._chk_apply_correction.blockSignals(False)
        self._refresh_validation()

        # Enable bus distribution
        has_eligible = len(self._targets) > 0
        self._btn_fetch_bld.setEnabled(has_eligible and self._bounds is not None)
        self._btn_apply.setEnabled(True)

    def _on_view_demand(self):
        """Open the demand visualizer with every forecasted node's series."""
        result = self._forecast_result
        if result is None:
            return
        import numpy as np

        from esfex.visualization.data.gui_model import GuiNodeDemand
        from esfex.visualization.panels.demand_visualizer import (
            DemandVisualizerDialog,
        )

        series = getattr(result, "demand_multi_year", None)
        if series is None:
            series = getattr(result, "demand", None)
        if series is None:
            return
        series = np.asarray(series)
        ncols = series.shape[1] if series.ndim > 1 else 1

        entries: list = []
        for i, node in enumerate(self._forecast_nodes):
            if i >= ncols:
                break
            col = series[:, i] if series.ndim > 1 else series
            data = [float(x) for x in col]
            fc = GuiNodeDemand(
                data=data, num_hours=len(data),
                peak_mw=float(max(data, default=0.0)),
                total_mwh=float(sum(data)))
            obs = self._observed.get(i)
            # 3-tuple (name, forecast, observed) when an observed series exists
            # for this node so the visualizer can overlay it.
            entries.append((node.name, fc, obs) if obs is not None
                           else (node.name, fc))
        if entries:
            try:
                start_year = int(self._spin_base_year.value())
            except Exception:
                start_year = 2025
            DemandVisualizerDialog(
                entries, self, start_year=start_year).exec()

    # ==================================================================
    # Observed-demand validation & post-hoc correction
    # ==================================================================

    def _refresh_obs_node_combo(self):
        self._combo_obs_node.blockSignals(True)
        self._combo_obs_node.clear()
        for i, node in enumerate(self._forecast_nodes):
            tag = " ✓" if i in self._observed else ""
            self._combo_obs_node.addItem(f"{node.name}{tag}", i)
        self._combo_obs_node.blockSignals(False)

    def _load_observed_csv(self):
        if not self._forecast_nodes:
            return
        idx = self._combo_obs_node.currentData()
        if idx is None:
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Load observed demand CSV", "",
            "Demand files (*.csv *.xlsx *.xls);;All files (*)")
        if not path:
            return
        try:
            import pandas as pd
            if path.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(path, header=None)
            else:
                df = pd.read_csv(path, header=None)
            series = df.iloc[:, 0].astype(float)   # one series = selected node
            data = [float(x) for x in series.tolist()]
        except Exception as exc:
            QMessageBox.warning(self, "Load failed",
                                f"Could not read the file:\n{exc}")
            return
        from esfex.visualization.data.gui_model import GuiNodeDemand
        self._observed[idx] = GuiNodeDemand(
            csv_path=path, data=data, num_hours=len(data),
            peak_mw=float(max(data, default=0.0)),
            total_mwh=float(sum(data)))
        self._refresh_obs_node_combo()
        pos = self._combo_obs_node.findData(idx)
        if pos >= 0:
            self._combo_obs_node.setCurrentIndex(pos)
        self._refresh_validation()

    def _clear_observed(self):
        self._observed.clear()
        if self._chk_apply_correction.isChecked():
            self._chk_apply_correction.setChecked(False)  # restores raw
        self._refresh_obs_node_combo()
        self._refresh_validation()

    def _forecast_dims(self):
        """Return (raw_multi_year, n_per_year, base_year, resolution_hours)."""
        my = self._forecast_result_raw
        if my is None:
            return None, 0, 2025, 1.0
        cfg = getattr(self._forecast_result, "config", None)
        years = getattr(cfg, "simulation_years", 0) or 1
        try:
            base_year = int(self._spin_base_year.value())
        except Exception:
            base_year = getattr(cfg, "base_year", 2025)
        res = getattr(self._forecast_result, "resolution_hours", 1.0) or 1.0
        n = my.shape[0] // years if my.ndim > 1 else len(my) // years
        return my, max(int(n), 1), base_year, float(res)

    def _refresh_validation(self):
        """Recompute per-node metrics and enable/disable the controls."""
        from esfex.visualization.workflows.metrics_validation import (
            forecast_metrics,
        )
        has_obs = bool(self._observed) and self._forecast_result_raw is not None
        self._btn_overlay_observed.setEnabled(has_obs)
        self._chk_apply_correction.setEnabled(has_obs)
        rows = sorted(self._observed.keys())
        self._obs_metrics_table.setRowCount(len(rows))
        if not has_obs:
            self._lbl_validation_status.setText("")
            return
        raw, n_per_year, base_year, res = self._forecast_dims()
        for r, idx in enumerate(rows):
            name = (self._forecast_nodes[idx].name
                    if idx < len(self._forecast_nodes) else f"node_{idx}")
            obs = self._observed[idx].data or []
            fc = raw[:n_per_year, idx] if raw.ndim > 1 else raw[:n_per_year]
            m = forecast_metrics(obs, fc)
            vals = [name,
                    f"{m.get('mape', float('nan')):.1f}",
                    f"{m.get('rmse', float('nan')):.1f}",
                    f"{m.get('peak_err', float('nan')):+.1f}",
                    f"{m.get('energy_err', float('nan')):+.1f}",
                    f"{m.get('corr', float('nan')):.2f}"]
            for c, v in enumerate(vals):
                self._obs_metrics_table.setItem(r, c, QTableWidgetItem(str(v)))
        self._lbl_validation_status.setText(
            f"{len(rows)} node(s) with observed data — metrics compare the "
            "base year against your series.")

    def _on_apply_correction_toggled(self, checked: bool):
        if self._forecast_result is None or self._forecast_result_raw is None:
            return
        raw = self._forecast_result_raw
        if not checked:
            self._forecast_result.demand_multi_year = raw.copy()
            self._recompute_node_stats(raw)
            self._lbl_validation_status.setText("Correction off (raw forecast).")
            return
        from esfex.visualization.workflows.metrics_validation import (
            apply_factors, month_hour_factors,
        )
        _, n_per_year, base_year, res = self._forecast_dims()
        corrected = raw.copy()
        ncols = raw.shape[1] if raw.ndim > 1 else 1
        n_applied = 0
        for idx, obs_demand in self._observed.items():
            if idx >= ncols:
                continue
            obs = obs_demand.data or []
            fc_base = raw[:n_per_year, idx] if raw.ndim > 1 else raw[:n_per_year]
            factors = month_hour_factors(obs, fc_base, base_year, res)
            col = raw[:, idx] if raw.ndim > 1 else raw
            new_col = apply_factors(col, factors, base_year, n_per_year, res)
            if raw.ndim > 1:
                corrected[:, idx] = new_col
            else:
                corrected = new_col
            n_applied += 1
        self._forecast_result.demand_multi_year = corrected
        self._recompute_node_stats(corrected)
        self._lbl_validation_status.setText(
            f"Correction applied to {n_applied} node(s); growth preserved.")

    def _recompute_node_stats(self, multi_year):
        """Refresh per-node peak/GWh/LF + the results table + system total."""
        import numpy as np
        result = self._forecast_result
        if result is None or multi_year is None:
            return
        my = np.asarray(multi_year)
        _, n_per_year, _, _ = self._forecast_dims()
        ncols = my.shape[1] if my.ndim > 1 else 1
        peaks, gwhs, lfs = [], [], []
        for i in range(ncols):
            base = my[:n_per_year, i] if my.ndim > 1 else my[:n_per_year]
            peak = float(base.max()) if base.size else 0.0
            gwh = float(base.sum()) / 1000.0
            lf = float(base.mean() / peak) if peak > 0 else 0.0
            peaks.append(peak)
            gwhs.append(gwh)
            lfs.append(lf)
            if i < self._forecast_table.rowCount():
                self._forecast_table.setItem(i, 1, QTableWidgetItem(f"{peak:.1f}"))
                self._forecast_table.setItem(i, 2, QTableWidgetItem(f"{gwh:.1f}"))
                self._forecast_table.setItem(i, 3, QTableWidgetItem(f"{lf:.3f}"))
        result.peak_mw = peaks
        result.annual_gwh = gwhs
        result.load_factor = lfs
        result.total_peak_mw = float(sum(peaks))
        result.total_annual_gwh = float(sum(gwhs))
        if my.ndim > 1 and n_per_year > 0:
            sys_base = my[:n_per_year, :].sum(axis=1)
            sp = float(sys_base.max()) if sys_base.size else 0.0
            result.total_load_factor = (
                float(sys_base.mean() / sp) if sp > 0 else 0.0)
        self._lbl_forecast_summary.setText(
            f"<b>System total:</b> Peak={result.total_peak_mw:.1f} MW, "
            f"Annual={result.total_annual_gwh:.1f} GWh, "
            f"LF={result.total_load_factor:.3f} "
            f"&mdash; Source: {result.demand_source}")

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

        for node in self._nodes_in_scope():
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

        from esfex.visualization.workflows._wizard_utils import stop_thread
        stop_thread(getattr(self, "_fetcher", None))
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
        """Classify buildings and assign each to its nearest bus.

        The work runs in a background ``ClassifyDistributeWorker`` so the window
        stays responsive even for whole-country footprint sets (the previous
        synchronous version froze the UI on hundreds of thousands of buildings).
        """
        if self._buildings_gdf is None or self._buildings_gdf.empty:
            self._lbl_status.setText("No buildings loaded.")
            return

        from esfex.visualization.workflows.demand_analysis import (
            ClassifyDistributeWorker,
        )

        self._btn_run.setEnabled(False)
        self._btn_apply.setEnabled(False)
        self._results_table.setRowCount(0)
        self._assignments.clear()
        self._progress.setValue(10)
        self._lbl_status.setText("Classifying buildings...")

        rules = self._get_rules()
        fallback = self._spin_fallback.value()
        from esfex.visualization.workflows._wizard_utils import stop_thread
        stop_thread(getattr(self, "_classify_worker", None))
        self._classify_worker = ClassifyDistributeWorker(
            self._buildings_gdf, rules, fallback, list(self._targets),
        )
        self._classify_worker.progress.connect(self._on_classify_dist_progress)
        self._classify_worker.finished.connect(self._on_classify_dist_done)
        self._classify_worker.error.connect(self._on_classify_dist_error)
        self._classify_worker.start()

    def _on_classify_dist_progress(self, pct, msg):
        self._progress.setValue(pct)
        if msg:
            self._lbl_status.setText(msg)

    def _on_classify_dist_error(self, msg):
        self._lbl_status.setText(f"Classification failed: {msg}")
        self._progress.setValue(0)
        self._btn_run.setEnabled(True)

    def _on_classify_dist_done(self, assignments, summary):
        """Render assignments + classification summary on the GUI thread."""
        self._assignments = list(assignments)

        cls_lines = []
        for _, r in summary.iterrows():
            cls_lines.append(
                f"{r['building_type']}: {int(r['count']):,} bldg, "
                f"{r['total_area_m2']:,.0f} m\u00b2, "
                f"w={r['total_weight']:.1f}"
            )

        self._render_assignments_table()

        self._progress.setValue(100)
        total_bld = int(summary["count"].sum()) if not summary.empty else 0
        total_w = summary["total_weight"].sum() if not summary.empty else 0.0
        self._lbl_status.setText(
            f"Classified {total_bld:,} buildings "
            f"({'; '.join(cls_lines)}; total weight: {total_w:.1f}). "
            f"Assigned to {len(self._assignments)} buses."
        )
        self._btn_run.setEnabled(True)
        self._btn_apply.setEnabled(len(self._assignments) > 0)

    def _render_assignments_table(self):
        """Render ``self._assignments`` into the bus-fraction results table.
        The 3rd column shows the per-bus building count or demand cell count."""
        rows = self._assignments
        self._results_table.setRowCount(len(rows))
        for row_idx, a in enumerate(rows):
            self._results_table.setItem(row_idx, 0, QTableWidgetItem(a["node_name"]))
            self._results_table.setItem(
                row_idx, 1, QTableWidgetItem(f"{a['bus_id']} ({a['bus_name']})"))
            self._results_table.setItem(
                row_idx, 2, QTableWidgetItem(f"{a.get('building_count', 0):,}"))
            self._results_table.setItem(
                row_idx, 3, QTableWidgetItem(f"{a['old_fraction']:.4f}"))
            self._results_table.setItem(
                row_idx, 4, QTableWidgetItem(f"{a['new_fraction']:.4f}"))

    def _distribute_by_spatial_demand(self):
        """Distribute each node's demand among its buses using the density
        model's per-cell spatial demand, solved as a *capacitated transport*
        problem rather than a nearest/Voronoi assignment.

        A substation does not only carry the demand of its own cell: it serves a
        distribution territory whose size is bounded by its capacity. So per node
        we solve a min-cost flow — cells (sources, weighted by their forecast
        demand) → load buses (sinks, capacity = transformer MVA, with a
        voltage-scaled fallback) — minimising total feeder distance subject to
        every cell being served and no bus exceeding its capacity. Demand spills
        to the next substation once the nearest one saturates, which a hard
        Voronoi split cannot represent. Produces the same ``self._assignments``
        the Apply step consumes."""
        import numpy as np, math
        from esfex.models.demand_density_ml import allocate_demand_capacitated
        res = getattr(self, "_forecast_result", None)
        cells = getattr(res, "cell_annual_mwh", None) if res is not None else None
        if not cells:
            self._lbl_bld_status.setText(
                "Run the demand forecast (Auto/XGBoost engine) first — no "
                "spatial-demand cells available.")
            return
        state = self._model.state if self._model else None
        nodes = self._nodes_in_scope() if state else []
        if not nodes:
            self._lbl_bld_status.setText("No nodes in the active system.")
            return

        clat = np.asarray(res.cell_lats, dtype=float)
        clon = np.asarray(res.cell_lons, dtype=float)
        cann = np.asarray(res.cell_annual_mwh, dtype=float)

        # Assign each cell to its nearest node (projected) — preserves the
        # density model's validated per-node totals; the transport only splits
        # within a node.
        nlat = np.array([nd.centroid_lat for nd in nodes], dtype=float)
        nlon = np.array([nd.centroid_lng for nd in nodes], dtype=float)
        mlng0 = 111.320 * math.cos(math.radians(float(np.mean(clat))))
        cx = clon * mlng0; cy = clat * 110.540
        nx = nlon * mlng0; ny = nlat * 110.540
        cell_node = np.array([
            int(np.argmin((nx - cx[i]) ** 2 + (ny - cy[i]) ** 2))
            for i in range(len(clat))
        ])

        # Per-bus capacity: sum of MVA of transformers touching the bus, with a
        # voltage-kV fallback scaled into the MVA range (so a node mixing
        # MVA-rated and fallback buses stays consistent).
        mva: dict[str, float] = {}
        for tr in state.transformers:
            m = float(getattr(tr, "rated_power_mva", 0.0) or 0.0)
            for bid in (tr.from_bus, tr.to_bus):
                if bid:
                    mva[bid] = mva.get(bid, 0.0) + m
        load_buses_all = [b for b in state.buses.values()
                          if b.role in ("load", "mixed")]
        have = [b for b in load_buses_all if mva.get(b.bus_id, 0.0) > 0]
        if have:
            mean_v = float(np.mean([max(float(b.voltage_kv), 1.0) for b in have]))
            sc = (sum(mva[b.bus_id] for b in have) / len(have)) / max(mean_v, 1.0)
        else:
            sc = 1.0

        def cap_of(b):
            m = mva.get(b.bus_id, 0.0)
            return m if m > 0 else max(float(b.voltage_kv), 1.0) * sc

        self._assignments = []
        n_nodes = 0
        for ni, nd in enumerate(nodes):
            bs = [b for b in state.buses.values()
                  if b.parent_node == nd.index and b.role in ("load", "mixed")]
            if len(bs) < 2:
                continue          # single-bus nodes need no split
            n_nodes += 1
            sel = np.where(cell_node == ni)[0]
            if sel.size:
                served = allocate_demand_capacitated(
                    clat[sel], clon[sel], cann[sel],
                    [b.latitude for b in bs], [b.longitude for b in bs],
                    [cap_of(b) for b in bs])
                tot = float(np.sum(served))
            else:
                served = None
                tot = 0.0
            for j, b in enumerate(bs):
                d = float(served[j]) if served is not None else 0.0
                new_frac = (d / tot) if tot > 0 else (1.0 / len(bs))
                self._assignments.append({
                    "node_name": nd.name, "bus_id": b.bus_id, "bus_name": b.name,
                    "building_count": int(round(d)),   # MWh/yr served by the bus
                    "old_fraction": b.demand_fraction, "new_fraction": new_frac,
                })

        self._render_assignments_table()
        self._progress.setValue(100)
        self._btn_apply.setEnabled(len(self._assignments) > 0)
        self._lbl_bld_status.setText(
            f"Capacitated-transport distribution: {len(self._assignments)} bus "
            f"fraction(s) across {n_nodes} multi-bus node(s) from "
            f"{len(res.cell_lats)} demand cells.")

    def _apply_all(self):
        """Apply forecast demand to nodes + bus fractions to model."""
        if not self._model:
            return

        state = self._model.state
        applied_fracs = 0
        applied_demand = 0

        # Apply forecast demand to nodes (if forecast was run). Each node's
        # hourly series is written to a CSV and a full GuiNodeDemand is stored
        # (csv_path + data + stats), so the serializer emits ``demand_paths``
        # and the runner finds the files. (Previously only the summary stats
        # were stashed on a SimpleNamespace, leaving demand_paths empty.)
        if self._forecast_result is not None:
            # Output dir: alongside the loaded YAML if known (same convention as
            # availability profiles), else the working directory.
            cfg_path = getattr(self.window(), "_config_path", None)
            out_dir = (Path(cfg_path).parent if cfg_path else Path.cwd()) / "demand"
            applied_demand += write_forecast_demand_csvs(
                self._nodes_in_scope(), self._forecast_result, out_dir)

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

    def cancel_all(self):
        """Stop the building-fetch and classify/distribute workers.

        Invoked by ``cleanup_wizard`` on close so neither QThread is
        destroyed while still running.
        """
        from esfex.visualization.workflows._wizard_utils import stop_thread
        stop_thread(getattr(self, "_fetcher", None))
        stop_thread(getattr(self, "_classify_worker", None))
