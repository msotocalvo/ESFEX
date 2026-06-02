# -*- coding: utf-8 -*-
"""Analytical panels for the Risk & Resilience workbench.

Seven independent panels, each a QWidget that receives a shared
``RiskWorkbenchState`` and can be visited in any order.

Panels:
  1. SiteScreeningPanel    — node detection + ThinkHazard! screening
  2. HazardDataPanel       — unified hazard data acquisition (all types)
  3. FragilityLibraryPanel  — editable curve browser with live CDF preview
  4. RiskDashboardPanel     — composite risk: CVaR tuning, EAL, sensitivity
  5. ClimateDemandsPanel    — SSP pathways, demand HDD/CDD adjustment
  6. ScenarioTreePanel      — browse/edit scenario tree, probability management
  7. ExportApplyPanel       — summary dashboard, export (CSV/JSON/YAML), apply
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import TYPE_CHECKING, Any

import numpy as np
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from esfex.visualization.workflows.risk_wizard import RiskWorkbenchState

logger = logging.getLogger(__name__)


# =====================================================================
# Theme-aware style helpers
# =====================================================================


def _action_btn_style() -> str:
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    return (
        f"background-color: {c.accent_primary}; color: white; "
        "font-weight: bold; padding: 6px 12px; border-radius: 3px;"
    )


def _success_btn_style() -> str:
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    return (
        f"background-color: {c.accent_secondary}; color: white; "
        "font-weight: bold; padding: 6px 12px; border-radius: 3px;"
    )


def _danger_btn_style() -> str:
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    return (
        f"background-color: {c.danger}; color: white; "
        "font-weight: bold; padding: 6px 12px; border-radius: 3px;"
    )


def _warning_btn_style() -> str:
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    return (
        f"background-color: {c.status_warning}; color: white; "
        "font-weight: bold; padding: 6px 12px; border-radius: 3px;"
    )


def _mono_text_style() -> str:
    from esfex.visualization.theme import current_theme
    t = current_theme().typography
    return f"font-family: {t.family_mono}; font-size: {t.size_code}pt;"


def _secondary_text_style() -> str:
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    t = current_theme().typography
    return f"color: {c.text_secondary}; font-size: {t.size_small}px;"


# Hazard types supported by the fetcher registry
_HAZARD_TYPES = [
    "earthquake", "cyclone", "flood", "tsunami",
    "wildfire", "volcanic", "sea_level_rise",
]

# Default return period options
_RETURN_PERIODS = [50, 100, 250, 475, 500, 1000, 2500]


# =====================================================================
# Background Workers
# =====================================================================


class _HazardFetchWorker(QThread):
    """Run hazard fetching in a background thread."""

    progress = Signal(int, str)
    finished = Signal(object)  # HazardIntensityMap or None
    error = Signal(str)

    def __init__(self, fetcher, coordinates, return_periods, parent=None):
        super().__init__(parent)
        self._fetcher = fetcher
        self._coordinates = coordinates
        self._return_periods = return_periods

    def run(self):
        try:
            result = self._fetcher.fetch(
                self._coordinates,
                self._return_periods,
                on_progress=lambda pct, msg: self.progress.emit(pct, msg),
            )
            self.finished.emit(result)
        except Exception as e:
            logger.exception("Hazard fetch failed")
            self.error.emit(str(e))


class _RiskAssessmentWorker(QThread):
    """Run composite risk assessment in background."""

    finished = Signal(list)  # list[NodeRiskProfile]
    error = Signal(str)

    def __init__(self, assessment, hazard_maps, node_components,
                 component_values, node_coordinates=None, parent=None):
        super().__init__(parent)
        self._assessment = assessment
        self._hazard_maps = hazard_maps
        self._node_components = node_components
        self._component_values = component_values
        self._node_coordinates = node_coordinates

    def run(self):
        try:
            profiles = self._assessment.assess(
                self._hazard_maps,
                self._node_components,
                self._component_values,
                self._node_coordinates,
            )
            self.finished.emit(profiles)
        except Exception as e:
            logger.exception("Risk assessment failed")
            self.error.emit(str(e))


class _ScenarioWorker(QThread):
    """Generate hazard scenarios in background."""

    finished = Signal(list)  # list[dict]
    error = Signal(str)

    def __init__(self, generator, risk_profiles, generator_map,
                 battery_map, n_scenarios, method, parent=None):
        super().__init__(parent)
        self._generator = generator
        self._risk_profiles = risk_profiles
        self._generator_map = generator_map
        self._battery_map = battery_map
        self._n_scenarios = n_scenarios
        self._method = method

    def run(self):
        try:
            scenarios = self._generator.generate_hazard_scenarios(
                self._risk_profiles,
                self._generator_map,
                self._battery_map,
                self._n_scenarios,
                self._method,
            )
            self.finished.emit(scenarios)
        except Exception as e:
            logger.exception("Scenario generation failed")
            self.error.emit(str(e))


# =====================================================================
# Panel 1: Site & Screening
# =====================================================================


class SiteScreeningPanel(QWidget):
    """Node detection and ThinkHazard! rapid multi-hazard screening."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._worker: _HazardFetchWorker | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state
        # Auto-refresh screening when hazard data changes
        state.hazard_data_changed.connect(self._on_hazard_data_changed)

    def _on_hazard_data_changed(self):
        """Auto-update screening view when hazard maps change."""
        if not self._state:
            return
        real_maps = [h for h in self._state.hazard_maps
                     if h.hazard_type != "screening"]
        if real_maps:
            self._update_screening_from_hazard_maps(real_maps)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<h3>Site Overview & Hazard Screening</h3>"
            "<p>Shows detected system nodes and a categorical hazard screening "
            "derived from the fetched intensity data. The screening levels "
            "update automatically when hazard data is fetched.</p>"
        ))

        # Node list
        grp_nodes = QGroupBox("Detected Nodes")
        nl = QVBoxLayout(grp_nodes)
        self._node_list = QListWidget()
        nl.addWidget(self._node_list)
        layout.addWidget(grp_nodes)

        # Screening controls
        row = QHBoxLayout()
        self._btn_screen = QPushButton("Run Hazard Screening")
        self._btn_screen.setStyleSheet(_action_btn_style())
        self._btn_screen.clicked.connect(self._run_screening)
        row.addWidget(self._btn_screen)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        row.addWidget(self._progress)
        row.addStretch()
        layout.addLayout(row)

        # Results chart
        from esfex.visualization.workflows.risk_charts import HazardScreeningChart
        self._chart = HazardScreeningChart()
        layout.addWidget(self._chart, 1)

    def on_enter(self):
        """Refresh node list from shared state."""
        self._node_list.clear()
        if not self._state:
            return
        for i, (lat, lon) in enumerate(self._state.node_coordinates):
            self._node_list.addItem(f"Node {i}: ({lat:.4f}, {lon:.4f})")

    def _run_screening(self):
        """Derive screening view from existing hazard_maps in shared state.

        If no hazard data has been fetched yet, prompts the user to
        go to the Hazard Data tab first.  This ensures the screening
        view is always consistent with the data used in risk assessment.
        """
        if not self._state or not self._state.node_coordinates:
            QMessageBox.warning(self, "No Nodes",
                                "No node coordinates detected. Load a system first.")
            return

        # Use hazard maps already fetched (single source of truth)
        real_maps = [h for h in self._state.hazard_maps
                     if h.hazard_type != "screening"]
        if not real_maps:
            QMessageBox.information(
                self, "No Hazard Data",
                "No hazard intensity data available yet.\n\n"
                "Switch to the Hazard Data tab and click "
                "'Fetch Selected Hazards' to download data from "
                "USGS, IBTrACS, NOAA, NASA FIRMS, and other sources.",
            )
            return

        self._update_screening_from_hazard_maps(real_maps)

    def _update_screening_from_hazard_maps(
        self, hazard_maps: list,
    ):
        """Show real IM values from fetched hazard data.

        Displays the same raw intensity measures used by the risk
        assessment — no intermediate classification or scoring.
        Each column is normalized independently for color mapping
        since hazard types have different physical units.
        """
        _IM_UNITS = {
            "earthquake": "g", "cyclone": "m/s", "flood": "m",
            "tsunami": "m", "wildfire": "FWI", "volcanic": "mm",
            "sea_level_rise": "m",
        }

        n_nodes = len(self._state.node_coordinates)
        node_labels = [f"Node {i}" for i in range(n_nodes)]

        hazard_levels: dict[str, list[float]] = {}
        for haz_type in _HAZARD_TYPES:
            hmap = next(
                (h for h in hazard_maps if h.hazard_type == haz_type), None
            )
            unit = _IM_UNITS.get(haz_type, "")
            label = f"{haz_type.replace('_', ' ').title()} ({unit})"
            values = []
            for idx in range(n_nodes):
                if hmap is None or idx not in hmap.node_intensities:
                    values.append(0.0)
                    continue
                rp_ims = hmap.node_intensities[idx]
                values.append(max(rp_ims.values()) if rp_ims else 0.0)
            hazard_levels[label] = values

        self._chart.update_chart(
            node_labels, hazard_levels,
            title="Hazard Intensity Measures (max return period)",
            normalize_columns=True,
            colorbar_label="Relative Intensity (per hazard type)",
        )

    def _on_worker_error(self, msg):
        self._progress.setVisible(False)
        self._btn_screen.setEnabled(True)
        QMessageBox.critical(self, "Screening Error", msg)


# =====================================================================
# Panel 2: Hazard Data Manager
# =====================================================================


class HazardDataPanel(QWidget):
    """Unified hazard data acquisition for all hazard types.

    Merges the previous separate seismic/cyclone/flood steps into a single
    panel with hazard type selector, source picker, return period config,
    results table, and chart sub-tabs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._worker: _HazardFetchWorker | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Hazard Data Manager</h3>"
            "<p>Fetch and manage hazard intensity data from multiple sources. "
            "Supports seismic (USGS/GEM), cyclone (IBTrACS/STORM), "
            "flood (WRI Aqueduct), tsunami (NOAA), wildfire (NASA FIRMS), "
            "volcanic (Smithsonian GVP), and sea level rise (NASA AR6).</p>"
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Controls ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # Hazard types (multi-select)
        grp_hazards = QGroupBox("Hazard Types")
        haz_lay = QVBoxLayout(grp_hazards)
        self._hazard_checks: dict[str, QCheckBox] = {}
        for haz in _HAZARD_TYPES:
            cb = QCheckBox(haz.replace("_", " ").title())
            cb.setChecked(True)  # All hazards enabled by default
            self._hazard_checks[haz] = cb
            haz_lay.addWidget(cb)
        sel_row = QHBoxLayout()
        btn_sel_all = QPushButton("All")
        btn_sel_all.clicked.connect(
            lambda: [cb.setChecked(True) for cb in self._hazard_checks.values()]
        )
        sel_row.addWidget(btn_sel_all)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(
            lambda: [cb.setChecked(False) for cb in self._hazard_checks.values()]
        )
        sel_row.addWidget(btn_clear)
        sel_row.addStretch()
        haz_lay.addLayout(sel_row)
        left_lay.addWidget(grp_hazards)

        # Return periods
        grp_rp = QGroupBox("Return Periods (years)")
        rp_lay = QVBoxLayout(grp_rp)
        self._rp_checks: dict[int, QCheckBox] = {}
        for rp in _RETURN_PERIODS:
            cb = QCheckBox(str(rp))
            cb.setChecked(rp in (100, 500))
            self._rp_checks[rp] = cb
            rp_lay.addWidget(cb)
        left_lay.addWidget(grp_rp)

        # Fetch button + progress
        self._btn_fetch = QPushButton("Fetch Selected Hazards")
        self._btn_fetch.setStyleSheet(_action_btn_style())
        self._btn_fetch.clicked.connect(self._run_fetch)
        left_lay.addWidget(self._btn_fetch)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        left_lay.addWidget(self._progress)

        self._lbl_status = QLabel("")
        left_lay.addWidget(self._lbl_status)

        # Results table
        grp_results = QGroupBox("Fetched Hazard Data")
        res_lay = QVBoxLayout(grp_results)
        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels(
            ["Hazard", "Source", "IM", "Nodes"]
        )
        self._results_table.horizontalHeader().setStretchLastSection(True)
        res_lay.addWidget(self._results_table)
        left_lay.addWidget(grp_results, 1)

        splitter.addWidget(left)

        # ── Right: Charts ──
        self._chart_tabs = QTabWidget()

        from esfex.visualization.workflows.risk_charts import (
            IMExceedanceChart,
            HazardScreeningChart,
        )

        # Tab 1: IM Exceedance with hazard selector
        exc_widget = QWidget()
        exc_lay = QVBoxLayout(exc_widget)
        exc_lay.setContentsMargins(0, 0, 0, 0)
        self._combo_exc_hazard = QComboBox()
        self._combo_exc_hazard.addItem("(no data)")
        self._combo_exc_hazard.currentIndexChanged.connect(
            self._on_exceedance_hazard_changed
        )
        exc_lay.addWidget(self._combo_exc_hazard)
        self._exceedance_chart = IMExceedanceChart()
        exc_lay.addWidget(self._exceedance_chart, 1)
        self._chart_tabs.addTab(exc_widget, "IM Exceedance")

        # Tab 2: Node Comparison (all-hazard heatmap)
        self._node_chart = HazardScreeningChart()
        self._chart_tabs.addTab(self._node_chart, "Node Comparison")

        splitter.addWidget(self._chart_tabs)
        splitter.setSizes([400, 600])
        layout.addWidget(splitter, 1)

    def on_enter(self):
        """Refresh results table and charts from shared state."""
        self._refresh_results_table()
        self._sync_exceedance_combo()
        self._update_node_comparison()

    def _get_selected_hazard_types(self) -> list[str]:
        """Return list of checked hazard types."""
        return [h for h, cb in self._hazard_checks.items() if cb.isChecked()]

    def _get_selected_return_periods(self) -> list[int]:
        return [rp for rp, cb in self._rp_checks.items() if cb.isChecked()]

    def _run_fetch(self):
        """Start batch fetch for all selected hazard types."""
        if not self._state or not self._state.node_coordinates:
            QMessageBox.warning(self, "No Nodes",
                                "No node coordinates available. Check Site & Screening tab.")
            return

        selected = self._get_selected_hazard_types()
        if not selected:
            QMessageBox.warning(self, "No Hazards",
                                "Select at least one hazard type.")
            return

        return_periods = self._get_selected_return_periods()
        if not return_periods:
            QMessageBox.warning(self, "No Return Periods",
                                "Select at least one return period.")
            return

        self._fetch_queue = list(selected)
        self._fetch_rps = return_periods
        self._fetch_total = len(selected)
        self._fetch_done_count = 0

        self._progress.setRange(0, self._fetch_total * 100)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._btn_fetch.setEnabled(False)

        self._fetch_next()

    def _fetch_next(self):
        """Pop next hazard from queue and start worker."""
        if not self._fetch_queue:
            # All done
            self._progress.setVisible(False)
            self._btn_fetch.setEnabled(True)
            self._lbl_status.setText(
                f"Completed: {self._fetch_done_count}/{self._fetch_total} hazards fetched."
            )
            self._sync_exceedance_combo()
            self._update_node_comparison()
            return

        hazard_type = self._fetch_queue.pop(0)
        self._lbl_status.setText(
            f"Fetching {hazard_type.replace('_', ' ')} "
            f"({self._fetch_done_count + 1}/{self._fetch_total})..."
        )

        from esfex.models.hazard_assessment import create_fetcher
        fetcher = create_fetcher(hazard_type, source="")

        self._worker = _HazardFetchWorker(
            fetcher, self._state.node_coordinates, self._fetch_rps
        )
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.finished.connect(self._on_batch_fetch_done)
        self._worker.error.connect(self._on_batch_error)
        self._worker.start()

    def _on_batch_progress(self, pct, msg):
        offset = self._fetch_done_count * 100
        self._progress.setValue(offset + pct)
        self._lbl_status.setText(msg)

    def _on_batch_fetch_done(self, result):
        self._fetch_done_count += 1

        if result and self._state:
            # Replace existing map of same type, or append
            new_maps = [h for h in self._state.hazard_maps
                        if h.hazard_type != result.hazard_type]
            new_maps.append(result)
            self._state.hazard_maps = new_maps
            self._state.hazard_data_changed.emit()
            self._refresh_results_table()

        self._fetch_next()

    def _on_batch_error(self, msg):
        self._fetch_done_count += 1
        logger.warning("Hazard fetch failed (continuing batch): %s", msg)
        self._lbl_status.setText(f"Warning: {msg} — continuing...")
        self._fetch_next()

    def _refresh_results_table(self):
        if not self._state:
            return
        maps = self._state.hazard_maps
        self._results_table.setRowCount(len(maps))
        for r, hmap in enumerate(maps):
            self._results_table.setItem(r, 0, QTableWidgetItem(
                hmap.hazard_type.replace("_", " ").title()))
            self._results_table.setItem(r, 1, QTableWidgetItem(hmap.source))
            self._results_table.setItem(r, 2, QTableWidgetItem(
                f"{hmap.intensity_measure} ({hmap.units})"))
            self._results_table.setItem(r, 3, QTableWidgetItem(
                str(len(hmap.node_intensities))))

    # ------------------------------------------------------------------
    # Chart updates
    # ------------------------------------------------------------------

    def _sync_exceedance_combo(self):
        """Rebuild the hazard-type combo box for the exceedance chart."""
        self._combo_exc_hazard.blockSignals(True)
        self._combo_exc_hazard.clear()
        if not self._state:
            self._combo_exc_hazard.addItem("(no data)")
            self._combo_exc_hazard.blockSignals(False)
            return
        added = False
        for hmap in self._state.hazard_maps:
            if hmap.hazard_type == "screening":
                continue
            label = hmap.hazard_type.replace("_", " ").title()
            self._combo_exc_hazard.addItem(label, hmap.hazard_type)
            added = True
        if not added:
            self._combo_exc_hazard.addItem("(no data)")
        self._combo_exc_hazard.blockSignals(False)
        # Draw the first entry
        self._on_exceedance_hazard_changed(0)

    def _on_exceedance_hazard_changed(self, _index: int):
        """Redraw exceedance chart for the selected hazard type."""
        haz_type = self._combo_exc_hazard.currentData()
        if not haz_type or not self._state:
            self._exceedance_chart.update_chart({})
            return
        hmap = next(
            (h for h in self._state.hazard_maps if h.hazard_type == haz_type),
            None,
        )
        if not hmap:
            self._exceedance_chart.update_chart({})
            return
        node_curves: dict[str, dict[int, float]] = {}
        for idx, rp_im in hmap.node_intensities.items():
            node_curves[f"Node {idx}"] = rp_im
        units = getattr(hmap, "units", "")
        self._exceedance_chart.update_chart(node_curves, hmap.hazard_type, units)

    def _update_node_comparison(self):
        """Rebuild Node Comparison heatmap from ALL fetched hazard maps.

        Rows = nodes, columns = hazard types.
        Shows raw intensity measure values with their physical units.
        Each column is independently normalized for color mapping since
        hazard types have incomparable units (g, m/s, m, mm, index).
        Cell annotations show the actual IM values.
        """
        if not self._state:
            return
        maps = [h for h in self._state.hazard_maps if h.hazard_type != "screening"]
        if not maps:
            return

        _IM_UNITS = {
            "earthquake": "g", "cyclone": "m/s", "flood": "m",
            "tsunami": "m", "wildfire": "FWI", "volcanic": "mm",
            "sea_level_rise": "m",
        }

        all_nodes: set[int] = set()
        for hmap in maps:
            all_nodes.update(hmap.node_intensities.keys())
        sorted_nodes = sorted(all_nodes)
        node_labels = [f"Node {idx}" for idx in sorted_nodes]

        hazard_levels: dict[str, list[float]] = {}
        for hmap in maps:
            rps = getattr(hmap, "return_periods", [])
            ref_rp = max(rps) if rps else 0
            unit = _IM_UNITS.get(hmap.hazard_type, hmap.units)
            label = f"{hmap.hazard_type.replace('_', ' ').title()} ({unit})"
            values = []
            for ni in sorted_nodes:
                ni_data = hmap.node_intensities.get(ni, {})
                values.append(ni_data.get(ref_rp, 0.0))
            hazard_levels[label] = values

        self._node_chart.update_chart(
            node_labels, hazard_levels,
            title="Hazard Intensity Measures by Node (max return period)",
            normalize_columns=True,
            colorbar_label="Relative Intensity (per hazard type)",
        )


# =====================================================================
# Panel 3: Fragility Library Editor
# =====================================================================


class FragilityLibraryPanel(QWidget):
    """Editable fragility curve browser with live CDF preview.

    Exposes the full FragilityLibrary with parameter editing, curve
    overlay, add/remove/clone, and import/export CSV.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Fragility Library Editor</h3>"
            "<p>Browse and edit lognormal fragility curves. "
            "49 built-in curves from NHESS-2024 and PNNL-33587. "
            "Edit parameters to see live CDF updates.</p>"
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Filters + Table + Buttons ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Component:"))
        self._combo_comp = QComboBox()
        self._combo_comp.addItem("All")
        self._combo_comp.currentIndexChanged.connect(self._refresh_table)
        filter_row.addWidget(self._combo_comp)

        filter_row.addWidget(QLabel("Hazard:"))
        self._combo_haz = QComboBox()
        self._combo_haz.addItem("All")
        self._combo_haz.currentIndexChanged.connect(self._refresh_table)
        filter_row.addWidget(self._combo_haz)
        filter_row.addStretch()
        left_lay.addLayout(filter_row)

        # Curve table
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Component", "Hazard", "Damage State", "\u03b8 (median)", "\u03b2", "Source"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.currentCellChanged.connect(self._on_selection_changed)
        self._table.cellChanged.connect(self._on_cell_edited)
        left_lay.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Curve")
        btn_add.clicked.connect(self._add_curve)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove_curve)
        btn_row.addWidget(btn_remove)

        btn_clone = QPushButton("Clone")
        btn_clone.clicked.connect(self._clone_curve)
        btn_row.addWidget(btn_clone)

        btn_reset = QPushButton("Reset Defaults")
        btn_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(btn_reset)

        btn_row.addStretch()

        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export_csv)
        btn_row.addWidget(btn_export)

        btn_import = QPushButton("Import CSV")
        btn_import.clicked.connect(self._import_csv)
        btn_row.addWidget(btn_import)

        left_lay.addLayout(btn_row)
        splitter.addWidget(left)

        # ── Right: Chart + IM evaluator ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)

        from esfex.visualization.workflows.risk_charts import (
            FragilityCurveChart, HazardScreeningChart,
        )
        self._chart_stack = QTabWidget()
        self._overview_chart = HazardScreeningChart()
        self._chart_stack.addTab(self._overview_chart, "Overview")
        self._chart = FragilityCurveChart()
        self._chart_stack.addTab(self._chart, "Curves")
        right_lay.addWidget(self._chart_stack, 1)

        # IM value evaluator
        eval_row = QHBoxLayout()
        eval_row.addWidget(QLabel("Evaluate at IM ="))
        self._spin_im = QDoubleSpinBox()
        self._spin_im.setRange(0.01, 100.0)
        self._spin_im.setValue(1.0)
        self._spin_im.setSingleStep(0.1)
        self._spin_im.setDecimals(2)
        self._spin_im.valueChanged.connect(self._evaluate_im)
        eval_row.addWidget(self._spin_im)
        self._lbl_prob = QLabel("P(damage) = —")
        self._lbl_prob.setStyleSheet("font-weight: bold;")
        eval_row.addWidget(self._lbl_prob)
        eval_row.addStretch()
        right_lay.addLayout(eval_row)

        splitter.addWidget(right)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

    def on_enter(self):
        """Populate filters and table from fragility library."""
        if not self._state:
            return
        lib = self._state.fragility_library

        # Populate filter combos
        self._combo_comp.blockSignals(True)
        self._combo_comp.clear()
        self._combo_comp.addItem("All")
        for ct in sorted(lib.component_types):
            self._combo_comp.addItem(ct.replace("_", " ").title(), ct)
        self._combo_comp.blockSignals(False)

        self._combo_haz.blockSignals(True)
        self._combo_haz.clear()
        self._combo_haz.addItem("All")
        for ht in sorted(lib.hazard_types):
            self._combo_haz.addItem(ht.replace("_", " ").title(), ht)
        self._combo_haz.blockSignals(False)

        self._refresh_table()

    def _get_filter_comp(self) -> str:
        data = self._combo_comp.currentData()
        return data if data else ""

    def _get_filter_haz(self) -> str:
        data = self._combo_haz.currentData()
        return data if data else ""

    def _refresh_table(self):
        if not self._state:
            return
        lib = self._state.fragility_library
        all_curves = lib.get_all_curves()

        comp_filter = self._get_filter_comp()
        haz_filter = self._get_filter_haz()

        filtered = all_curves
        if comp_filter:
            filtered = [c for c in filtered if c.component_type == comp_filter]
        if haz_filter:
            filtered = [c for c in filtered if c.hazard_type == haz_filter]

        self._table.blockSignals(True)
        self._table.setRowCount(len(filtered))
        for r, curve in enumerate(filtered):
            self._table.setItem(r, 0, QTableWidgetItem(
                curve.component_type.replace("_", " ").title()))
            self._table.setItem(r, 1, QTableWidgetItem(
                curve.hazard_type.replace("_", " ").title()))
            self._table.setItem(r, 2, QTableWidgetItem(curve.damage_state))

            # Editable median
            item_med = QTableWidgetItem(f"{curve.im_median:.3f}")
            item_med.setFlags(item_med.flags() | Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 3, item_med)

            # Editable beta
            item_beta = QTableWidgetItem(f"{curve.beta:.3f}")
            item_beta.setFlags(item_beta.flags() | Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 4, item_beta)

            self._table.setItem(r, 5, QTableWidgetItem(curve.source))

        self._table.blockSignals(False)
        self._filtered_curves = filtered
        # Clear selection so the overview chart is shown
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._show_overview_chart()

    def _on_selection_changed(self, row, col, prev_row, prev_col):
        self._update_chart_for_selection()

    def _on_cell_edited(self, row, col):
        """Apply in-place edits of theta/beta back to the library."""
        if not self._state or row < 0 or row >= len(self._filtered_curves):
            return
        curve = self._filtered_curves[row]
        try:
            if col == 3:  # theta
                new_val = float(self._table.item(row, col).text())
                if new_val > 0:
                    curve.im_median = new_val
            elif col == 4:  # beta
                new_val = float(self._table.item(row, col).text())
                if 0 < new_val <= 2.0:
                    curve.beta = new_val
        except (ValueError, AttributeError):
            return

        self._state.fragility_changed.emit()
        self._update_chart_for_selection()

    def _update_chart_for_selection(self):
        """Update chart based on current filters and selection.

        If no row is selected (or first enter), shows an overview of all
        'complete' damage curves for the current filter, giving a
        comparative view across components and hazards.

        If a specific row is selected, shows all damage states for
        that component/hazard combination.
        """
        if not self._state:
            self._chart.update_chart([])
            return

        lib = self._state.fragility_library
        row = self._table.currentRow()

        # Specific row selected → show all damage states for that combination
        if row >= 0 and row < len(self._filtered_curves):
            curve = self._filtered_curves[row]
            related = lib.get_curves(curve.component_type, curve.hazard_type)
            ds_colors = {
                "slight": "#27ae60", "moderate": "#f1c40f",
                "extensive": "#e67e22", "complete": "#e74c3c",
            }
            chart_data = []
            for c in related:
                chart_data.append({
                    "label": f"{c.damage_state} (\u03b8={c.im_median:.2f}, \u03b2={c.beta:.2f})",
                    "im_median": c.im_median,
                    "beta": c.beta,
                    "beta_epistemic": c.beta_epistemic,
                    "color": ds_colors.get(c.damage_state, "#7f8c8d"),
                })
            title = (
                f"{curve.component_type.replace('_', ' ').title()} — "
                f"{curve.hazard_type.replace('_', ' ').title()}"
            )
            self._chart.update_chart(chart_data, title=title)
            self._chart_stack.setCurrentWidget(self._chart)
            self._evaluate_im()
            return

        # No selection → overview heatmap
        self._show_overview_chart()

    def _show_overview_chart(self):
        """Show heatmap of median IM for complete damage (component × hazard).

        Each cell shows the im_median value at which P(complete)=50%.
        Lower values mean more fragile. Cells are normalized per column
        (per hazard) since IM units differ across hazard types.
        """
        if not self._state:
            return
        lib = self._state.fragility_library

        comp_filter = self._get_filter_comp()
        haz_filter = self._get_filter_haz()

        comp_types = sorted(lib.component_types)
        haz_types = sorted(lib.hazard_types)
        if comp_filter:
            comp_types = [comp_filter]
        if haz_filter:
            haz_types = [haz_filter]

        comp_labels = [c.replace("_", " ").title() for c in comp_types]

        _IM_UNITS = {
            "earthquake": "g", "cyclone": "m/s", "flood": "m",
            "tsunami": "m", "wildfire": "FWI", "volcanic": "mm",
            "sea_level_rise": "m",
        }

        hazard_values: dict[str, list[float]] = {}
        for haz in haz_types:
            unit = _IM_UNITS.get(haz, "")
            label = f"{haz.replace('_', ' ').title()} ({unit})"
            values = []
            for comp in comp_types:
                p = lib.get_complete_damage_probability
                curves = lib.get_curves(comp, haz)
                complete = [c for c in curves if c.damage_state == "complete"]
                if complete:
                    values.append(complete[0].im_median)
                else:
                    values.append(0.0)
            hazard_values[label] = values

        # Invert values: fragility_score = 1 - (im / max_im_in_column)
        # so that more fragile components (lower im_median) get higher scores
        # and appear as more intense colors.
        inverted: dict[str, list[float]] = {}
        for label, values in hazard_values.items():
            col_max = max(values) if values else 1.0
            if col_max > 0:
                inverted[label] = [
                    round(1.0 - v / col_max, 3) if v > 0 else 0.0
                    for v in values
                ]
            else:
                inverted[label] = values

        self._overview_chart.update_chart(
            comp_labels, inverted,
            title="Component Fragility (higher = more vulnerable)",
            colorbar_label="Fragility (0 = least vulnerable, 1 = most vulnerable)",
        )
        self._chart_stack.setCurrentWidget(self._overview_chart)

    def _evaluate_im(self):
        """Evaluate P(damage) at the current IM spinbox value."""
        if not self._filtered_curves:
            self._lbl_prob.setText("P(damage) = —")
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._filtered_curves):
            return
        curve = self._filtered_curves[row]
        im = self._spin_im.value()
        prob = curve.evaluate(im)
        self._lbl_prob.setText(
            f"P({curve.damage_state} | IM={im:.2f}) = {prob:.4f}"
        )

    def _add_curve(self):
        """Add a new default curve to the library."""
        if not self._state:
            return
        from esfex.models.hazard_assessment import FragilityCurve
        new_curve = FragilityCurve(
            component_type="solar_pv",
            hazard_type="earthquake",
            damage_state="complete",
            im_median=1.0,
            beta=0.5,
            source="user",
        )
        lib = self._state.fragility_library
        key = (new_curve.component_type, new_curve.hazard_type)
        if key not in lib._curves:
            lib._curves[key] = []
        lib._curves[key].append(new_curve)
        self._state.fragility_changed.emit()
        self._refresh_table()

    def _remove_curve(self):
        """Remove selected curve from library."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._filtered_curves) or not self._state:
            return
        curve = self._filtered_curves[row]
        lib = self._state.fragility_library
        key = (curve.component_type, curve.hazard_type)
        curves_list = lib._curves.get(key, [])
        if curve in curves_list:
            curves_list.remove(curve)
        self._state.fragility_changed.emit()
        self._refresh_table()

    def _clone_curve(self):
        """Clone selected curve with editable parameters."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._filtered_curves) or not self._state:
            return
        from esfex.models.hazard_assessment import FragilityCurve
        src = self._filtered_curves[row]
        clone = FragilityCurve(
            component_type=src.component_type,
            hazard_type=src.hazard_type,
            damage_state=src.damage_state,
            im_median=src.im_median * 1.1,
            beta=src.beta,
            source="user (cloned)",
        )
        lib = self._state.fragility_library
        key = (clone.component_type, clone.hazard_type)
        if key not in lib._curves:
            lib._curves[key] = []
        lib._curves[key].append(clone)
        self._state.fragility_changed.emit()
        self._refresh_table()

    def _reset_defaults(self):
        """Reset library to built-in defaults."""
        if not self._state:
            return
        reply = QMessageBox.question(
            self, "Reset Fragility Library",
            "Reset all curves to built-in defaults? Custom curves will be lost.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from esfex.models.hazard_assessment import FragilityLibrary
        self._state.fragility_library = FragilityLibrary()
        self._state.fragility_changed.emit()
        self._refresh_table()

    def _export_csv(self):
        if not self._state:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Fragility Curves", "fragility_library.csv",
            "CSV Files (*.csv)")
        if not path:
            return
        lib = self._state.fragility_library
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["component_type", "hazard_type", "damage_state",
                        "im_median", "beta", "source"])
            for curve in lib.get_all_curves():
                w.writerow([curve.component_type, curve.hazard_type,
                            curve.damage_state, curve.im_median,
                            curve.beta, curve.source])

    def _import_csv(self):
        if not self._state:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Fragility Curves", "",
            "CSV Files (*.csv)")
        if not path:
            return
        from esfex.models.hazard_assessment import FragilityCurve
        lib = self._state.fragility_library
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    curve = FragilityCurve(
                        component_type=row["component_type"],
                        hazard_type=row["hazard_type"],
                        damage_state=row["damage_state"],
                        im_median=float(row["im_median"]),
                        beta=float(row["beta"]),
                        source=row.get("source", "imported"),
                    )
                    key = (curve.component_type, curve.hazard_type)
                    if key not in lib._curves:
                        lib._curves[key] = []
                    lib._curves[key].append(curve)
            self._state.fragility_changed.emit()
            self._refresh_table()
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))


# =====================================================================
# Panel 4: Risk Dashboard (analytical core)
# =====================================================================


class RiskDashboardPanel(QWidget):
    """Composite risk assessment with CVaR tuning, EAL table, sensitivity.

    This is the analytical core of the workbench: combines hazard data
    with fragility curves to compute per-node risk profiles, then offers
    parameter tuning (combination method, CVaR α/λ) with live feedback.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._worker: _RiskAssessmentWorker | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Risk Dashboard</h3>"
            "<p>Run composite risk assessment combining all fetched hazard data "
            "with the fragility library. Tune CVaR parameters and combination "
            "method to explore risk sensitivity.</p>"
        ))

        # ── Top controls row ──
        controls = QHBoxLayout()

        controls.addWidget(QLabel("Combination:"))
        self._combo_method = QComboBox()
        self._combo_method.addItems(["Independent", "Copula", "MCDA"])
        controls.addWidget(self._combo_method)

        controls.addWidget(QLabel("Risk Measure:"))
        self._combo_measure = QComboBox()
        self._combo_measure.addItems(["Expected", "CVaR", "Minimax Regret"])
        controls.addWidget(self._combo_measure)

        controls.addWidget(QLabel("CVaR \u03b1:"))
        self._spin_alpha = QDoubleSpinBox()
        self._spin_alpha.setRange(0.80, 0.99)
        self._spin_alpha.setValue(0.95)
        self._spin_alpha.setSingleStep(0.01)
        self._spin_alpha.setDecimals(2)
        controls.addWidget(self._spin_alpha)

        controls.addWidget(QLabel("CVaR \u03bb:"))
        self._spin_lambda = QDoubleSpinBox()
        self._spin_lambda.setRange(0.0, 1.0)
        self._spin_lambda.setValue(0.5)
        self._spin_lambda.setSingleStep(0.05)
        self._spin_lambda.setDecimals(2)
        controls.addWidget(self._spin_lambda)

        self._btn_assess = QPushButton("Assess Risk")
        self._btn_assess.setStyleSheet(_danger_btn_style())
        self._btn_assess.clicked.connect(self._run_assessment)
        controls.addWidget(self._btn_assess)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        controls.addWidget(self._progress)

        controls.addStretch()
        layout.addLayout(controls)

        # ── Main area: splitter ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Node selection + risk table
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # Node selection checkboxes
        grp_nodes = QGroupBox("Nodes to Analyze")
        node_lay = QVBoxLayout(grp_nodes)
        node_btn_row = QHBoxLayout()
        btn_all = QPushButton("All")
        btn_all.clicked.connect(self._select_all_nodes)
        node_btn_row.addWidget(btn_all)
        btn_none = QPushButton("None")
        btn_none.clicked.connect(self._select_no_nodes)
        node_btn_row.addWidget(btn_none)
        node_btn_row.addStretch()
        node_lay.addLayout(node_btn_row)

        self._node_scroll = QScrollArea()
        self._node_scroll.setWidgetResizable(True)
        self._node_scroll.setMaximumHeight(150)
        self._node_check_container = QWidget()
        self._node_check_layout = QVBoxLayout(self._node_check_container)
        self._node_check_layout.setContentsMargins(2, 2, 2, 2)
        self._node_check_layout.setSpacing(2)
        self._node_checks: dict[int, QCheckBox] = {}
        self._node_scroll.setWidget(self._node_check_container)
        node_lay.addWidget(self._node_scroll)
        left_lay.addWidget(grp_nodes)

        # Risk results table
        self._risk_table = QTableWidget()
        self._risk_table.setColumnCount(5)
        self._risk_table.setHorizontalHeaderLabels(
            ["Node", "Composite Risk", "EAL ($/yr)", "Dominant Hazard", "ALARP Class"]
        )
        self._risk_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._risk_table.currentCellChanged.connect(self._on_node_selected)
        left_lay.addWidget(self._risk_table, 1)

        splitter.addWidget(left_widget)

        # Right: Chart tabs
        self._chart_tabs = QTabWidget()

        from esfex.visualization.workflows.risk_charts import (
            RiskHeatmapChart,
            EALBarChart,
            SensitivityTornadoChart,
            ResiliencePerformanceChart,
            IMExceedanceChart,
            HazardScreeningChart,
        )
        self._heatmap = RiskHeatmapChart()
        self._chart_tabs.addTab(self._heatmap, "Failure Probability")

        self._eal_chart = EALBarChart()
        self._chart_tabs.addTab(self._eal_chart, "EAL by Node")

        # IM Exceedance with hazard selector
        exc_widget = QWidget()
        exc_lay = QVBoxLayout(exc_widget)
        exc_lay.setContentsMargins(0, 0, 0, 0)
        self._combo_exc_hazard = QComboBox()
        self._combo_exc_hazard.addItem("(no data)")
        self._combo_exc_hazard.currentIndexChanged.connect(
            self._on_exceedance_hazard_changed
        )
        exc_lay.addWidget(self._combo_exc_hazard)
        self._exceedance_chart = IMExceedanceChart()
        exc_lay.addWidget(self._exceedance_chart, 1)
        self._chart_tabs.addTab(exc_widget, "IM Exceedance")

        # Hazard IM comparison heatmap
        self._im_heatmap = HazardScreeningChart()
        self._chart_tabs.addTab(self._im_heatmap, "Hazard IMs")

        self._tornado = SensitivityTornadoChart()
        self._chart_tabs.addTab(self._tornado, "Sensitivity")

        self._resilience_chart = ResiliencePerformanceChart()
        self._chart_tabs.addTab(self._resilience_chart, "Resilience Curve")


        # Node detail text
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setStyleSheet(_mono_text_style())
        self._chart_tabs.addTab(self._detail_text, "Node Detail")
        self._chart_tabs.currentChanged.connect(self._on_chart_tab_changed)

        splitter.addWidget(self._chart_tabs)
        splitter.setSizes([400, 600])
        layout.addWidget(splitter, 1)

    def _on_chart_tab_changed(self, index: int):
        """Force redraw of matplotlib charts when their tab becomes visible."""
        widget = self._chart_tabs.widget(index)
        if widget and hasattr(widget, "draw"):
            widget.draw()

    def _populate_node_checks(self):
        """Build node checkboxes from shared state."""
        # Clear existing
        for cb in self._node_checks.values():
            self._node_check_layout.removeWidget(cb)
            cb.deleteLater()
        self._node_checks.clear()

        if not self._state:
            return
        for idx in range(len(self._state.node_coordinates)):
            name = self._state.node_names.get(idx, f"Node {idx}")
            enabled = self._state.node_enabled.get(idx, True)
            cb = QCheckBox(name)
            cb.setChecked(enabled)
            cb.stateChanged.connect(
                lambda state, i=idx: self._on_node_check_changed(i, state)
            )
            self._node_check_layout.addWidget(cb)
            self._node_checks[idx] = cb

    def _on_node_check_changed(self, node_idx: int, state: int):
        if self._state:
            self._state.node_enabled[node_idx] = bool(state)

    def _select_all_nodes(self):
        for cb in self._node_checks.values():
            cb.setChecked(True)

    def _select_no_nodes(self):
        for cb in self._node_checks.values():
            cb.setChecked(False)

    def _get_enabled_node_indices(self) -> set[int]:
        """Return set of coord indices whose system node is enabled."""
        if not self._state:
            return set()
        enabled_sys_nodes = {
            sn for sn, enabled in self._state.node_enabled.items()
            if enabled
        }
        c2n = getattr(self._state, "coord_to_system_node", {})
        return {
            ci for ci, sn in c2n.items() if sn in enabled_sys_nodes
        }

    def on_enter(self):
        """Refresh node checkboxes and risk profiles if they exist."""
        self._populate_node_checks()
        if self._state and self._state.risk_profiles:
            self._populate_results(self._state.risk_profiles)

    def _run_assessment(self):
        if not self._state:
            return
        if not self._state.node_coordinates:
            QMessageBox.warning(self, "No Nodes",
                                "No node coordinates detected. Check Site & Screening tab.")
            return

        # Update state parameters from controls
        method_map = {"Independent": "independent", "Copula": "copula", "MCDA": "mcda"}
        self._state.combination_method = method_map.get(
            self._combo_method.currentText(), "independent"
        )
        measure_map = {"Expected": "expected", "CVaR": "cvar", "Minimax Regret": "minimax_regret"}
        self._state.risk_measure = measure_map.get(
            self._combo_measure.currentText(), "expected"
        )
        self._state.cvar_alpha = self._spin_alpha.value()
        self._state.cvar_lambda = self._spin_lambda.value()

        # Auto-fetch any missing hazard data before running assessment
        fetched_types = {
            h.hazard_type for h in self._state.hazard_maps
            if h.hazard_type != "screening"
        }
        missing = set(_HAZARD_TYPES) - fetched_types

        if missing:
            logger.info(
                "Auto-fetching %d missing hazard types: %s",
                len(missing), ", ".join(sorted(missing)),
            )
            self._autofetch_queue = sorted(missing)
            self._autofetch_rps = [100, 475, 500]
            self._autofetch_done = 0
            self._autofetch_total = len(missing)
            self._progress.setRange(0, self._autofetch_total * 100)
            self._progress.setValue(0)
            self._progress.setVisible(True)
            self._btn_assess.setEnabled(False)
            self._autofetch_next()
            return

        self._execute_assessment()

    def _autofetch_next(self):
        """Fetch next missing hazard type, then continue to assessment."""
        if not self._autofetch_queue:
            self._execute_assessment()
            return

        hazard_type = self._autofetch_queue.pop(0)

        from esfex.models.hazard_assessment import create_fetcher
        fetcher = create_fetcher(hazard_type, source="")

        self._worker = _HazardFetchWorker(
            fetcher, self._state.node_coordinates, self._autofetch_rps,
        )
        self._worker.progress.connect(
            lambda pct, msg: (
                self._progress.setValue(self._autofetch_done * 100 + pct),
            )
        )
        self._worker.finished.connect(self._on_autofetch_done)
        self._worker.error.connect(self._on_autofetch_error)
        self._worker.start()

    def _on_autofetch_done(self, result):
        self._autofetch_done += 1
        if result and self._state:
            new_maps = [h for h in self._state.hazard_maps
                        if h.hazard_type != result.hazard_type]
            new_maps.append(result)
            self._state.hazard_maps = new_maps
            self._state.hazard_data_changed.emit()
        self._autofetch_next()

    def _on_autofetch_error(self, msg):
        self._autofetch_done += 1
        logger.warning("Auto-fetch failed (continuing): %s", msg)
        self._autofetch_next()

    def _execute_assessment(self):
        """Run the composite risk assessment with all available hazard data."""
        if not self._state:
            return

        # Filter to enabled nodes only
        enabled = self._get_enabled_node_indices()
        if not enabled:
            QMessageBox.warning(self, "No Nodes Selected",
                                "Select at least one node to analyze.")
            self._progress.setVisible(False)
            self._btn_assess.setEnabled(True)
            return

        # Build node_components from real system data, filtered
        all_components = self._state.node_components
        if not all_components:
            n_nodes = len(self._state.node_coordinates)
            all_components = {i: ["substation"] for i in range(n_nodes)}
        node_components = {
            idx: comps for idx, comps in all_components.items()
            if idx in enabled
        }

        # Build node_coordinates dict for assess(), filtered
        node_coords = {
            i: coord for i, coord in enumerate(self._state.node_coordinates)
            if i in enabled
        }

        from esfex.models.hazard_assessment import CompositeRiskAssessment
        assessment = CompositeRiskAssessment(
            fragility_library=self._state.fragility_library,
            combination_method=self._state.combination_method,
            risk_measure=self._state.risk_measure,
            cvar_alpha=self._state.cvar_alpha,
            cvar_lambda=self._state.cvar_lambda,
        )
        self._assessment = assessment
        self._node_components = node_components

        self._progress.setRange(0, 100)
        self._progress.setVisible(True)
        self._btn_assess.setEnabled(False)

        comp_values = self._state.component_values or {}
        comp_values_filtered = {
            idx: v for idx, v in comp_values.items() if idx in enabled
        } or None

        self._worker = _RiskAssessmentWorker(
            assessment,
            self._state.hazard_maps,
            node_components,
            comp_values_filtered,
            node_coords,
        )
        self._worker.finished.connect(self._on_assessment_done)
        self._worker.error.connect(self._on_assessment_error)
        self._worker.start()

    def _on_assessment_done(self, profiles):
        self._progress.setVisible(False)
        self._btn_assess.setEnabled(True)
        if not self._state:
            return

        self._state.risk_profiles = profiles
        self._state.risk_assessed.emit()
        self._populate_results(profiles)

        # Sensitivity sweep → tornado chart
        try:
            if hasattr(self, "_assessment") and self._assessment and self._state.hazard_maps:
                sweep = self._assessment.sensitivity_sweep(
                    self._state.hazard_maps,
                    getattr(self, "_node_components", {}),
                    self._state.component_values or None,
                )
                self._tornado.update_chart(
                    sweep["param_names"],
                    sweep["low_values"],
                    sweep["high_values"],
                    sweep["base_value"],
                )
        except Exception:
            logger.debug("Sensitivity sweep failed", exc_info=True)

        # Auto-generate hazard scenarios if none exist (for resilience curve)
        if not self._state.hazard_scenarios and profiles:
            try:
                from esfex.models.hazard_assessment import ScenarioGenerator
                gen = ScenarioGenerator(self._state.fragility_library)
                auto_scenarios = gen.generate_hazard_scenarios(
                    profiles,
                    self._state.generator_map,
                    self._state.battery_map,
                    n_scenarios=10,
                    method="enumeration",
                )
                self._state.hazard_scenarios = auto_scenarios
                self._state.scenarios_changed.emit()
                logger.info(
                    "Auto-generated %d hazard scenarios for resilience",
                    len(auto_scenarios),
                )
            except Exception:
                logger.debug("Auto scenario generation failed", exc_info=True)

        # Resilience metrics (with real system parameters)
        try:
            from esfex.models.hazard_assessment import ResilienceAnalyzer
            analyzer = ResilienceAnalyzer()
            metrics = analyzer.compute_metrics(
                profiles,
                self._state.hazard_scenarios,
                total_demand_mwh=self._state.total_demand_mwh or 8760.0,
                total_capacity_mw=self._state.total_capacity_mw or 100.0,
                n_generators=self._state.n_generator_types or 6,
            )
            self._state.resilience_metrics = metrics

            if metrics.time_steps is not None and metrics.performance_curve is not None:
                self._resilience_chart.update_chart(
                    metrics.time_steps, metrics.performance_curve,
                    metrics.resilience_index,
                )

            capacities = {
                "Anticipatory": metrics.anticipatory_capacity,
                "Absorptive": metrics.absorptive_capacity,
                "Adaptive": metrics.adaptive_capacity,
                "Restorative": metrics.restorative_capacity,
            }
            logger.info(
                "Resilience: R=%.3f, LOLP=%.4f, EENS=%.1f MWh, "
                "capacities=%s",
                metrics.resilience_index, metrics.lolp, metrics.eens_mwh,
                capacities,
            )
            # Resilience capacities logged but not charted — no standard
            # prescribes quantitative formulas for these indicators.
        except Exception:
            logger.warning("Resilience metrics failed", exc_info=True)

        # Update IM exceedance and hazard IM heatmap from fetched data
        self._update_im_charts()

    def _on_exceedance_hazard_changed(self, _index: int):
        """Redraw exceedance chart, aggregated to system nodes (max IM per RP)."""
        haz_type = self._combo_exc_hazard.currentData()
        if not haz_type or not self._state:
            self._exceedance_chart.update_chart({})
            return
        hmap = next(
            (h for h in self._state.hazard_maps if h.hazard_type == haz_type),
            None,
        )
        if not hmap:
            self._exceedance_chart.update_chart({})
            return

        c2n = getattr(self._state, "coord_to_system_node", {})
        sys_names = getattr(self._state, "system_node_names", {})

        from collections import defaultdict
        sys_rp_ims: dict[int, dict[int, float]] = defaultdict(dict)
        for coord_idx, rp_im in hmap.node_intensities.items():
            sn = c2n.get(coord_idx, coord_idx)
            for rp, im in rp_im.items():
                sys_rp_ims[sn][rp] = max(sys_rp_ims[sn].get(rp, 0.0), im)

        node_curves: dict[str, dict[int, float]] = {}
        for sn in sorted(sys_rp_ims.keys()):
            label = sys_names.get(sn, f"Node {sn}")
            node_curves[label] = sys_rp_ims[sn]

        units = getattr(hmap, "units", "")
        self._exceedance_chart.update_chart(node_curves, hmap.hazard_type, units)

    def _update_im_charts(self):
        """Refresh IM exceedance combo and hazard IM heatmap."""
        if not self._state:
            return

        # Rebuild exceedance hazard combo
        maps = [h for h in self._state.hazard_maps if h.hazard_type != "screening"]
        self._combo_exc_hazard.blockSignals(True)
        self._combo_exc_hazard.clear()
        for hmap in maps:
            label = hmap.hazard_type.replace("_", " ").title()
            self._combo_exc_hazard.addItem(label, hmap.hazard_type)
        if not maps:
            self._combo_exc_hazard.addItem("(no data)")
        self._combo_exc_hazard.blockSignals(False)
        if maps:
            self._on_exceedance_hazard_changed(0)

        # Hazard IM heatmap — aggregated to system nodes
        if not maps:
            return
        _IM_UNITS = {
            "earthquake": "g", "cyclone": "m/s", "flood": "m",
            "tsunami": "m", "wildfire": "FWI", "volcanic": "mm",
            "sea_level_rise": "m",
        }

        c2n = getattr(self._state, "coord_to_system_node", {})
        sys_names = getattr(self._state, "system_node_names", {})

        from collections import defaultdict
        sys_node_coords: dict[int, list[int]] = defaultdict(list)
        all_coord_idxs: set[int] = set()
        for hmap in maps:
            all_coord_idxs.update(hmap.node_intensities.keys())
        for ci in all_coord_idxs:
            sn = c2n.get(ci, ci)
            sys_node_coords[sn].append(ci)

        sorted_sys_nodes = sorted(sys_node_coords.keys())
        node_labels = [sys_names.get(n, f"Node {n}") for n in sorted_sys_nodes]

        hazard_levels: dict[str, list[float]] = {}
        for hmap in maps:
            rps = getattr(hmap, "return_periods", [])
            ref_rp = max(rps) if rps else 0
            unit = _IM_UNITS.get(hmap.hazard_type, hmap.units)
            label = f"{hmap.hazard_type.replace('_', ' ').title()} ({unit})"
            values = []
            for sn in sorted_sys_nodes:
                max_im = 0.0
                for ci in sys_node_coords[sn]:
                    ni_data = hmap.node_intensities.get(ci, {})
                    max_im = max(max_im, ni_data.get(ref_rp, 0.0))
                values.append(max_im)
            hazard_levels[label] = values

        self._im_heatmap.update_chart(
            node_labels, hazard_levels,
            title="Hazard Intensity Measures (max return period)",
            normalize_columns=True,
            colorbar_label="Relative Intensity (per hazard type)",
        )

    def _on_assessment_error(self, msg):
        self._progress.setVisible(False)
        self._btn_assess.setEnabled(True)
        QMessageBox.critical(self, "Assessment Error", msg)

    def _populate_results(self, profiles):
        """Fill risk table and charts from per-element risk profiles.

        Charts are aggregated to system nodes (not per-element) for
        readability.  The table shows per-system-node aggregated values:
        max composite risk, sum EAL, dominant hazard.
        """
        from PySide6.QtGui import QBrush, QColor
        from collections import defaultdict

        # Evaluate risk criteria (ISO 31000 §6.5 ALARP)
        from esfex.models.hazard_assessment import evaluate_risk_criteria
        evaluations = evaluate_risk_criteria(profiles)
        if self._state:
            self._state.risk_evaluations = evaluations
        eval_map = {e.node_index: e for e in evaluations}

        _EVAL_COLORS = {
            "negligible": "#27ae60",
            "tolerable_low": "#f1c40f",
            "tolerable_high": "#e67e22",
            "intolerable": "#e74c3c",
        }

        # Aggregate per-element profiles to system nodes
        c2n = getattr(self._state, "coord_to_system_node", {})
        sys_names = getattr(self._state, "system_node_names", {})

        node_profiles: dict[int, list] = defaultdict(list)
        for p in profiles:
            sys_node = c2n.get(p.node_index, p.node_index)
            node_profiles[sys_node].append(p)

        sorted_sys_nodes = sorted(node_profiles.keys())
        self._aggregated_node_profiles = []  # for Node Detail drill-down

        self._risk_table.setRowCount(len(sorted_sys_nodes))
        for r, sys_node in enumerate(sorted_sys_nodes):
            plist = node_profiles[sys_node]
            # Aggregated metrics
            max_risk = max(p.composite_risk for p in plist)
            total_eal = sum(p.expected_annual_loss for p in plist)
            # Dominant hazard: from the highest-risk element
            dominant_profile = max(plist, key=lambda p: p.composite_risk)
            dominant_haz = dominant_profile.dominant_hazard
            # ALARP: worst classification across elements at this node
            worst_ev = None
            _ALARP_ORDER = ["negligible", "tolerable_low", "tolerable_high", "intolerable"]
            for p in plist:
                ev = eval_map.get(p.node_index)
                if ev and (worst_ev is None or
                           _ALARP_ORDER.index(ev.classification) >
                           _ALARP_ORDER.index(worst_ev.classification)):
                    worst_ev = ev

            node_name = sys_names.get(sys_node, f"Node {sys_node}")
            self._risk_table.setItem(r, 0, QTableWidgetItem(node_name))
            self._risk_table.setItem(r, 1, QTableWidgetItem(f"{max_risk:.4f}"))
            self._risk_table.setItem(r, 2, QTableWidgetItem(f"${total_eal:,.0f}"))
            self._risk_table.setItem(r, 3, QTableWidgetItem(
                dominant_haz.replace("_", " ").title()))
            cls_label = worst_ev.classification.replace("_", " ").title() if worst_ev else "—"
            self._risk_table.setItem(r, 4, QTableWidgetItem(cls_label))

            if worst_ev:
                color = QColor(_EVAL_COLORS.get(worst_ev.classification, "#7f8c8d"))
                color.setAlpha(60)
                brush = QBrush(color)
                for c in range(self._risk_table.columnCount()):
                    item = self._risk_table.item(r, c)
                    if item:
                        item.setBackground(brush)

            self._aggregated_node_profiles.append((sys_node, plist))

        # Charts use system node labels
        node_labels = [sys_names.get(n, f"Node {n}") for n in sorted_sys_nodes]

        # Heatmap: max failure probability per hazard per system node
        all_hazards = set()
        for p in profiles:
            for comp_probs in p.component_failure_probs.values():
                all_hazards.update(comp_probs.keys())
        if not all_hazards:
            all_hazards = set(_HAZARD_TYPES[:4])
        hazard_list = sorted(all_hazards)

        matrix = np.zeros((len(sorted_sys_nodes), len(hazard_list)))
        for i, sys_node in enumerate(sorted_sys_nodes):
            for p in node_profiles[sys_node]:
                for comp_probs in p.component_failure_probs.values():
                    for j, haz in enumerate(hazard_list):
                        matrix[i, j] = max(matrix[i, j], comp_probs.get(haz, 0.0))

        self._heatmap.update_chart(node_labels, hazard_list, matrix)

        # EAL chart: sum EAL per system node, split by hazard
        eal_by_hazard: dict[str, list[float]] = {h: [] for h in hazard_list}
        for sys_node in sorted_sys_nodes:
            plist = node_profiles[sys_node]
            node_eal = sum(p.expected_annual_loss for p in plist)
            # Distribute proportionally to max hazard prob
            total_prob = 0.0
            haz_probs: dict[str, float] = {}
            for haz in hazard_list:
                hp = 0.0
                for p in plist:
                    for comp_probs in p.component_failure_probs.values():
                        hp = max(hp, comp_probs.get(haz, 0.0))
                haz_probs[haz] = hp
                total_prob += hp
            for haz in hazard_list:
                share = (haz_probs[haz] / total_prob * node_eal) if total_prob > 0 else 0
                eal_by_hazard[haz].append(share)

        self._eal_chart.update_chart(node_labels, eal_by_hazard)

        # Auto-select first row
        if sorted_sys_nodes:
            self._risk_table.selectRow(0)
            self._on_node_selected(0, 0, -1, -1)

    def _on_node_selected(self, row, col, prev_row, prev_col):
        """Show detailed per-element breakdown for the selected system node."""
        if not hasattr(self, "_aggregated_node_profiles"):
            return
        if row < 0 or row >= len(self._aggregated_node_profiles):
            return

        sys_node, plist = self._aggregated_node_profiles[row]
        sys_name = ""
        if self._state:
            sys_name = self._state.system_node_names.get(sys_node, f"Node {sys_node}")

        lines = [
            f"{sys_name} — Risk Profile ({len(plist)} element(s))",
            "=" * 60,
        ]

        # Aggregated summary
        max_risk = max(p.composite_risk for p in plist)
        total_eal = sum(p.expected_annual_loss for p in plist)
        lines.append(f"Max Composite Risk:   {max_risk:.4f}")
        lines.append(f"Total EAL:            ${total_eal:,.0f}/yr")
        lines.append("")

        # Per-element detail
        for p in plist:
            lat, lon = p.coordinates
            lines.append(f"--- Location ({lat:.4f}, {lon:.4f}) ---")
            lines.append(f"  Composite Risk:     {p.composite_risk:.4f}")
            lines.append(f"  EAL:                ${p.expected_annual_loss:,.0f}/yr")
            lines.append(f"  Dominant Hazard:    {p.dominant_hazard}")

            lines.append("  Hazard Intensities:")
            for haz, rp_im in p.hazard_intensities.items():
                for rp, im in sorted(rp_im.items()):
                    lines.append(f"    {haz}: RP={rp}yr → IM={im:.3f}")

            lines.append("  Failure Probabilities:")
            for comp, probs in p.component_failure_probs.items():
                for haz, prob in probs.items():
                    lines.append(f"    {comp}/{haz}: P={prob:.4f}")
            lines.append("")

        self._detail_text.setText("\n".join(lines))


# =====================================================================
# Panel 5: Climate & Demand
# =====================================================================


class ClimateDemandsPanel(QWidget):
    """SSP pathway configuration and temperature-dependent demand adjustment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Climate Scenarios & Demand Adjustment</h3>"
            "<p>Configure SSP climate pathways and preview their impact on "
            "renewable availability and electricity demand via HDD/CDD.</p>"
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Controls ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # SSP pathway checkboxes
        grp_ssp = QGroupBox("SSP Pathways")
        ssp_lay = QVBoxLayout(grp_ssp)
        self._ssp_checks: dict[str, QCheckBox] = {}
        for ssp in ["SSP1-2.6", "SSP2-4.5", "SSP3-7.0", "SSP5-8.5"]:
            cb = QCheckBox(ssp)
            cb.setChecked(ssp in ("SSP2-4.5", "SSP5-8.5"))
            self._ssp_checks[ssp] = cb
            ssp_lay.addWidget(cb)
        left_lay.addWidget(grp_ssp)

        # Year horizons
        grp_year = QGroupBox("Year Horizons")
        year_lay = QVBoxLayout(grp_year)
        self._year_checks: dict[int, QCheckBox] = {}
        for yr in [2030, 2040, 2050, 2060, 2080, 2100]:
            cb = QCheckBox(str(yr))
            cb.setChecked(yr in (2030, 2050))
            self._year_checks[yr] = cb
            year_lay.addWidget(cb)
        left_lay.addWidget(grp_year)

        # Demand parameters
        grp_demand = QGroupBox("Demand Parameters")
        demand_form = QFormLayout(grp_demand)

        self._spin_base_temp = QDoubleSpinBox()
        self._spin_base_temp.setRange(10.0, 30.0)
        self._spin_base_temp.setValue(18.0)
        self._spin_base_temp.setSuffix(" \u00b0C")
        demand_form.addRow("Base Temperature:", self._spin_base_temp)

        self._spin_heat = QDoubleSpinBox()
        self._spin_heat.setRange(0.0, 20.0)
        self._spin_heat.setValue(1.5)
        self._spin_heat.setDecimals(1)
        self._spin_heat.setSuffix(" %/\u00b0C")
        demand_form.addRow("Heating Sensitivity:", self._spin_heat)

        self._spin_cool = QDoubleSpinBox()
        self._spin_cool.setRange(0.0, 20.0)
        self._spin_cool.setValue(2.5)
        self._spin_cool.setDecimals(1)
        self._spin_cool.setSuffix(" %/\u00b0C")
        demand_form.addRow("Cooling Sensitivity:", self._spin_cool)

        left_lay.addWidget(grp_demand)

        lbl_info = QLabel(
            "Demand increase per 1\u00b0C warming (cooling) or "
            "cooling (heating). Typical: 2\u20134 %/\u00b0C cooling, "
            "1\u20132 %/\u00b0C heating."
        )
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet(_secondary_text_style())
        left_lay.addWidget(lbl_info)

        # Generate button
        self._btn_generate = QPushButton("Generate Climate Scenarios")
        self._btn_generate.setStyleSheet(_success_btn_style())
        self._btn_generate.clicked.connect(self._generate_scenarios)
        left_lay.addWidget(self._btn_generate)

        self._lbl_status = QLabel("")
        left_lay.addWidget(self._lbl_status)
        left_lay.addStretch()

        splitter.addWidget(left)

        # ── Right: Charts ──
        self._chart_tabs = QTabWidget()

        from esfex.visualization.workflows.risk_charts import (
            ClimateScenarioChart,
            DemandAdjustmentChart,
        )
        self._climate_chart = ClimateScenarioChart()
        self._chart_tabs.addTab(self._climate_chart, "\u0394T / GHI / Wind")

        self._demand_chart = DemandAdjustmentChart()
        self._chart_tabs.addTab(self._demand_chart, "Demand Adjustment")

        splitter.addWidget(self._chart_tabs)
        splitter.setSizes([350, 650])
        layout.addWidget(splitter, 1)

    def on_enter(self):
        """Load existing climate scenarios if available."""
        pass

    def _generate_scenarios(self):
        if not self._state:
            return

        ssp_selected = [ssp for ssp, cb in self._ssp_checks.items() if cb.isChecked()]
        if not ssp_selected:
            QMessageBox.warning(self, "No SSP Selected",
                                "Select at least one SSP pathway.")
            return

        year_selected = [yr for yr, cb in self._year_checks.items() if cb.isChecked()]

        from esfex.models.hazard_assessment import ScenarioGenerator
        gen = ScenarioGenerator(self._state.fragility_library)
        scenarios = gen.generate_climate_scenarios(
            ssp_pathways=ssp_selected,
            year_horizons=year_selected,
        )

        self._state.climate_scenarios = scenarios
        self._state.scenarios_changed.emit()

        self._lbl_status.setText(f"Generated {len(scenarios)} climate scenarios.")

        # Update climate chart
        self._climate_chart.update_chart(scenarios)

        # Update demand chart (compute demand multipliers from temperature deltas)
        base_temp = self._spin_base_temp.value()
        alpha_heat = self._spin_heat.value()
        alpha_cool = self._spin_cool.value()

        ssp_demands: dict[str, dict[int, float]] = {}
        for sc in scenarios:
            temp_delta = sc.get("temperature_delta", {})
            if temp_delta:
                demand_mult: dict[int, float] = {}
                for yr, dt in temp_delta.items():
                    # dt > 0 → warming → more cooling demand
                    # dt < 0 → cooling → more heating demand (rare in projections)
                    cooling_increase = (alpha_cool / 100.0) * max(0.0, dt)
                    heating_increase = (alpha_heat / 100.0) * max(0.0, -dt)
                    mult = 1.0 + cooling_increase + heating_increase
                    demand_mult[yr] = round(mult, 4)
                # Store in scenario dict (previously lost)
                sc["demand_scale"] = demand_mult
                ssp_demands[sc.get("name", "")] = demand_mult

        self._demand_chart.update_chart(ssp_demands)


# =====================================================================
# Panel 6: Scenario Tree
# =====================================================================


class ScenarioTreePanel(QWidget):
    """Browse, edit, and generate hazard + climate scenarios."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._worker: _ScenarioWorker | None = None
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Scenario Tree</h3>"
            "<p>Generate and manage discrete hazard scenarios for the stochastic "
            "optimizer. Edit probabilities, add manual scenarios, or reduce "
            "the tree using forward reduction.</p>"
        ))

        # ── Top controls ──
        controls = QHBoxLayout()

        controls.addWidget(QLabel("Sampling:"))
        self._combo_method = QComboBox()
        self._combo_method.addItems(["Importance", "LHS", "Enumeration"])
        controls.addWidget(self._combo_method)

        controls.addWidget(QLabel("N scenarios:"))
        self._spin_n = QSpinBox()
        self._spin_n.setRange(3, 50)
        self._spin_n.setValue(10)
        controls.addWidget(self._spin_n)

        controls.addWidget(QLabel("Max total:"))
        self._spin_max = QSpinBox()
        self._spin_max.setRange(5, 100)
        self._spin_max.setValue(20)
        controls.addWidget(self._spin_max)

        self._btn_generate = QPushButton("Generate Scenarios")
        self._btn_generate.setStyleSheet(_warning_btn_style())
        self._btn_generate.clicked.connect(self._generate_scenarios)
        controls.addWidget(self._btn_generate)

        self._btn_reduce = QPushButton("Reduce Tree")
        self._btn_reduce.clicked.connect(self._reduce_tree)
        controls.addWidget(self._btn_reduce)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        controls.addWidget(self._progress)

        controls.addStretch()
        layout.addLayout(controls)

        # ── Main: Splitter (table + charts) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Scenario table
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self._scenario_table = QTableWidget()
        self._scenario_table.setColumnCount(7)
        self._scenario_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Probability", "Hazard", "Nodes", "Max Damage", "Recovery (h)"]
        )
        self._scenario_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._scenario_table.cellChanged.connect(self._on_prob_edited)
        left_lay.addWidget(self._scenario_table, 1)

        # Table buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Scenario")
        btn_add.clicked.connect(self._add_manual_scenario)
        btn_row.addWidget(btn_add)
        btn_del = QPushButton("Delete Selected")
        btn_del.clicked.connect(self._delete_scenario)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        self._lbl_prob_sum = QLabel("Sum P = 0.0000")
        self._lbl_prob_sum.setStyleSheet("font-weight: bold;")
        btn_row.addWidget(self._lbl_prob_sum)
        left_lay.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: Charts
        self._chart_tabs = QTabWidget()

        from esfex.visualization.workflows.risk_charts import (
            ScenarioTreeChart,
            ProbabilityPieChart,
        )
        self._tree_chart = ScenarioTreeChart()
        self._chart_tabs.addTab(self._tree_chart, "Scenario Tree")

        self._pie_chart = ProbabilityPieChart()
        self._chart_tabs.addTab(self._pie_chart, "Probabilities")

        splitter.addWidget(self._chart_tabs)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

    def on_enter(self):
        """Refresh scenario table from state."""
        self._refresh_table()

    def _generate_scenarios(self):
        if not self._state:
            return
        if not self._state.risk_profiles:
            QMessageBox.warning(self, "No Risk Profiles",
                                "Run risk assessment first in the Risk Dashboard tab.")
            return

        method_map = {"Importance": "importance", "LHS": "lhs", "Enumeration": "enumeration"}
        method = method_map.get(self._combo_method.currentText(), "importance")

        # Use real generator/battery maps from system extraction
        generator_map = self._state.generator_map
        battery_map = self._state.battery_map
        if not generator_map:
            # Fallback: one generic generator per node
            n_nodes = len(self._state.node_coordinates)
            generator_map = {f"gen_{i}": (i, "solar_pv") for i in range(n_nodes)}
            battery_map = {}

        from esfex.models.hazard_assessment import ScenarioGenerator
        gen = ScenarioGenerator(self._state.fragility_library)

        self._progress.setVisible(True)
        self._btn_generate.setEnabled(False)

        self._worker = _ScenarioWorker(
            gen, self._state.risk_profiles,
            generator_map, battery_map,
            self._spin_n.value(), method,
        )
        self._worker.finished.connect(self._on_scenarios_done)
        self._worker.error.connect(self._on_scenario_error)
        self._worker.start()

    def _on_scenarios_done(self, scenarios):
        self._progress.setVisible(False)
        self._btn_generate.setEnabled(True)
        if not self._state:
            return

        self._state.hazard_scenarios = scenarios
        self._state.scenarios_changed.emit()
        self._refresh_table()
        self._update_charts()

    def _on_scenario_error(self, msg):
        self._progress.setVisible(False)
        self._btn_generate.setEnabled(True)
        QMessageBox.critical(self, "Scenario Error", msg)

    def _reduce_tree(self):
        """Apply forward reduction to scenario tree."""
        if not self._state:
            return
        from esfex.models.hazard_assessment import ScenarioGenerator
        gen = ScenarioGenerator(self._state.fragility_library)
        climate, hazard = gen.build_scenario_tree(
            self._state.climate_scenarios,
            self._state.hazard_scenarios,
            max_scenarios=self._spin_max.value(),
        )
        self._state.climate_scenarios = climate
        self._state.hazard_scenarios = hazard
        self._state.scenarios_changed.emit()
        self._refresh_table()
        self._update_charts()

    def _refresh_table(self):
        if not self._state:
            return

        all_scenarios = []
        for sc in self._state.climate_scenarios:
            all_scenarios.append(("climate", sc))
        for sc in self._state.hazard_scenarios:
            all_scenarios.append(("hazard", sc))

        self._scenario_table.blockSignals(True)
        self._scenario_table.setRowCount(len(all_scenarios))

        for r, (stype, sc) in enumerate(all_scenarios):
            self._scenario_table.setItem(r, 0, QTableWidgetItem(sc.get("name", "")))
            self._scenario_table.setItem(r, 1, QTableWidgetItem(stype))

            # Editable probability
            prob_item = QTableWidgetItem(f"{sc.get('probability', 0):.6f}")
            prob_item.setFlags(prob_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._scenario_table.setItem(r, 2, prob_item)

            self._scenario_table.setItem(r, 3, QTableWidgetItem(
                sc.get("hazard_type", sc.get("ssp_pathway", ""))))
            nodes = sc.get("affected_nodes", [])
            self._scenario_table.setItem(r, 4, QTableWidgetItem(
                str(len(nodes)) if nodes else "all"))
            dmg = sc.get("damage_fraction", {})
            max_dmg = max(dmg.values()) if dmg else 0.0
            self._scenario_table.setItem(r, 5, QTableWidgetItem(f"{max_dmg:.2%}"))
            self._scenario_table.setItem(r, 6, QTableWidgetItem(
                str(sc.get("recovery_hours", ""))))

        self._scenario_table.blockSignals(False)
        self._all_scenarios = all_scenarios
        self._update_prob_sum()

    def _on_prob_edited(self, row, col):
        """Allow editing probability column."""
        if col != 2 or not self._state:
            return
        if row >= len(self._all_scenarios):
            return
        try:
            new_prob = float(self._scenario_table.item(row, col).text())
            stype, sc = self._all_scenarios[row]
            sc["probability"] = max(0.0, min(1.0, new_prob))
            self._update_prob_sum()
        except (ValueError, AttributeError):
            pass

    def _update_prob_sum(self):
        if not self._state:
            return
        total = sum(sc.get("probability", 0)
                    for sc in self._state.hazard_scenarios)
        from esfex.visualization.theme import current_theme
        c = current_theme().colors
        color = c.status_success if abs(total - 1.0) < 0.01 else c.status_error
        self._lbl_prob_sum.setText(f"Hazard \u03a3P = {total:.4f}")
        self._lbl_prob_sum.setStyleSheet(f"font-weight: bold; color: {color};")

    def _add_manual_scenario(self):
        """Add a manually-defined scenario."""
        if not self._state:
            return
        n = len(self._state.hazard_scenarios)
        self._state.hazard_scenarios.append({
            "name": f"manual_scenario_{n}",
            "probability": 0.01,
            "hazard_type": "earthquake",
            "affected_nodes": [],
            "damage_fraction": {},
            "recovery_hours": 8760,
            "intensity_measure": 0.0,
            "description": "Manually created scenario",
        })
        self._state.scenarios_changed.emit()
        self._refresh_table()

    def _delete_scenario(self):
        """Delete selected scenario."""
        row = self._scenario_table.currentRow()
        if row < 0 or row >= len(self._all_scenarios) or not self._state:
            return
        stype, sc = self._all_scenarios[row]
        if stype == "hazard":
            if sc in self._state.hazard_scenarios:
                self._state.hazard_scenarios.remove(sc)
        else:
            if sc in self._state.climate_scenarios:
                self._state.climate_scenarios.remove(sc)
        self._state.scenarios_changed.emit()
        self._refresh_table()
        self._update_charts()

    def _update_charts(self):
        if not self._state:
            return
        self._tree_chart.update_chart(
            self._state.climate_scenarios,
            self._state.hazard_scenarios,
        )
        # Pie chart
        all_sc = self._state.climate_scenarios + self._state.hazard_scenarios
        names = [sc.get("name", "?") for sc in all_sc]
        probs = [sc.get("probability", 0) for sc in all_sc]
        self._pie_chart.update_chart(names, probs)


# =====================================================================
# Panel 7: Export & Apply
# =====================================================================


class ExportApplyPanel(QWidget):
    """Summary dashboard, configuration export, and apply to model."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: RiskWorkbenchState | None = None
        self._map_widget = None
        self._model = None
        self._all_states: dict = {}
        self._build_ui()

    def set_state(self, state: RiskWorkbenchState) -> None:
        self._state = state

    def set_context(self, map_widget, model, all_states):
        self._map_widget = map_widget
        self._model = model
        self._all_states = all_states

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Export & Apply</h3>"
            "<p>Review the complete risk analysis configuration and export "
            "results or apply to the system configuration.</p>"
        ))

        # Summary text
        self._summary = QTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setStyleSheet(_mono_text_style())
        layout.addWidget(self._summary, 1)

        # Buttons
        btn_row = QHBoxLayout()

        btn_csv = QPushButton("Export EAL Table (CSV)")
        btn_csv.clicked.connect(self._export_csv)
        btn_row.addWidget(btn_csv)

        btn_json = QPushButton("Export Full State (JSON)")
        btn_json.clicked.connect(self._export_json)
        btn_row.addWidget(btn_json)

        btn_yaml = QPushButton("Export Config (YAML)")
        btn_yaml.clicked.connect(self._export_yaml)
        btn_row.addWidget(btn_yaml)

        btn_report = QPushButton("ISO Report (HTML)")
        btn_report.setStyleSheet(_action_btn_style())
        btn_report.clicked.connect(self._export_iso_report)
        btn_row.addWidget(btn_report)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def on_enter(self):
        """Refresh summary."""
        self._generate_summary()

    def _generate_summary(self):
        if not self._state:
            return
        s = self._state
        lines = [
            "=" * 60,
            "  RISK & RESILIENCE ANALYSIS — SUMMARY",
            "=" * 60,
            "",
            "--- SITE ---",
            f"  Nodes detected:        {len(s.node_coordinates)}",
            f"  Hazard maps loaded:    {len(s.hazard_maps)}",
            "",
            "--- RISK PARAMETERS ---",
            f"  Combination method:    {s.combination_method}",
            f"  Risk measure:          {s.risk_measure}",
            f"  CVaR \u03b1:                {s.cvar_alpha:.2f}",
            f"  CVaR \u03bb:                {s.cvar_lambda:.2f}",
            "",
            "--- FRAGILITY LIBRARY ---",
            f"  Total curves:          {len(s.fragility_library.get_all_curves())}",
            f"  Component types:       {len(s.fragility_library.component_types)}",
            f"  Hazard types covered:  {len(s.fragility_library.hazard_types)}",
            "",
            "--- RISK PROFILES ---",
            f"  Assessed nodes:        {len(s.risk_profiles)}",
        ]

        if s.risk_profiles:
            risks = [p.composite_risk for p in s.risk_profiles]
            eals = [p.expected_annual_loss for p in s.risk_profiles]
            lines.extend([
                f"  Composite risk range:  {min(risks):.4f} — {max(risks):.4f}",
                f"  Total EAL:             ${sum(eals):,.0f}/yr",
                f"  Mean EAL per node:     ${np.mean(eals):,.0f}/yr",
            ])

        lines.extend([
            "",
            "--- SCENARIOS ---",
            f"  Climate scenarios:     {len(s.climate_scenarios)}",
            f"  Hazard scenarios:      {len(s.hazard_scenarios)}",
        ])

        if s.climate_scenarios:
            names = [sc.get("name", "") for sc in s.climate_scenarios]
            lines.append(f"  SSP pathways:          {', '.join(names)}")

        if s.hazard_scenarios:
            prob_sum = sum(sc.get("probability", 0) for sc in s.hazard_scenarios)
            lines.append(f"  Hazard prob sum:       {prob_sum:.4f}")

        # Risk evaluation
        evaluations = getattr(s, "risk_evaluations", [])
        if evaluations:
            intolerable = sum(1 for e in evaluations if getattr(e, "action_required", False))
            lines.extend([
                "",
                "--- RISK EVALUATION (ISO 31000 §6.5) ---",
                f"  Nodes evaluated:       {len(evaluations)}",
                f"  Intolerable nodes:     {intolerable}",
            ])

        # Resilience metrics
        rm = getattr(s, "resilience_metrics", None)
        if rm:
            lines.extend([
                "",
                "--- RESILIENCE (ISO 22372) ---",
                f"  LOLP:                  {rm.lolp:.4f}",
                f"  EENS:                  {rm.eens_mwh:,.1f} MWh/yr",
                f"  Resilience Index (R):  {rm.resilience_index:.3f}",
                f"  SART:                  {rm.sart_hours:.0f} hours",
                f"  Anticipatory:          {rm.anticipatory_capacity:.2f}",
                f"  Absorptive:            {rm.absorptive_capacity:.2f}",
                f"  Adaptive:              {rm.adaptive_capacity:.2f}",
                f"  Restorative:           {rm.restorative_capacity:.2f}",
            ])

        self._summary.setText("\n".join(lines))

    def _export_csv(self):
        if not self._state or not self._state.risk_profiles:
            QMessageBox.warning(self, "No Data", "Run risk assessment first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export EAL Table", "risk_eal.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["node", "composite_risk", "eal_usd_yr",
                         "dominant_hazard", "lat", "lon"])
            for p in self._state.risk_profiles:
                lat, lon = p.coordinates
                w.writerow([p.node_index, f"{p.composite_risk:.4f}",
                            f"{p.expected_annual_loss:.0f}",
                            p.dominant_hazard, lat, lon])

    def _export_json(self):
        if not self._state:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Full State", "risk_state.json", "JSON Files (*.json)")
        if not path:
            return
        s = self._state
        data = {
            "node_coordinates": s.node_coordinates,
            "combination_method": s.combination_method,
            "risk_measure": s.risk_measure,
            "cvar_alpha": s.cvar_alpha,
            "cvar_lambda": s.cvar_lambda,
            "climate_scenarios": s.climate_scenarios,
            "hazard_scenarios": s.hazard_scenarios,
            "risk_profiles": [
                {
                    "node_index": p.node_index,
                    "composite_risk": p.composite_risk,
                    "expected_annual_loss": p.expected_annual_loss,
                    "dominant_hazard": p.dominant_hazard,
                }
                for p in s.risk_profiles
            ],
            "fragility_curves": [
                {
                    "component_type": c.component_type,
                    "hazard_type": c.hazard_type,
                    "damage_state": c.damage_state,
                    "im_median": c.im_median,
                    "beta": c.beta,
                    "source": c.source,
                }
                for c in s.fragility_library.get_all_curves()
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _export_yaml(self):
        if not self._state:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Config YAML", "risk_config.yaml", "YAML Files (*.yaml)")
        if not path:
            return
        s = self._state
        # Build YAML-compatible dict
        config = {
            "risk": {
                "enabled": True,
                "risk_measure": s.risk_measure,
                "cvar_alpha": s.cvar_alpha,
                "cvar_lambda": s.cvar_lambda,
                "combination_method": s.combination_method,
                "climate_scenarios": s.climate_scenarios,
                "hazard_scenarios": s.hazard_scenarios,
            }
        }
        try:
            import yaml
            with open(path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        except ImportError:
            # Fallback to JSON
            with open(path, "w") as f:
                json.dump(config, f, indent=2, default=str)

    def _apply(self):
        """Apply risk configuration to the GUI model."""
        logger.info(
            "Apply called: state=%s, model=%s, all_states=%d",
            self._state is not None, self._model is not None,
            len(self._all_states),
        )
        if not self._state:
            QMessageBox.warning(self, "Apply",
                                "No risk analysis state. Run the assessment first.")
            return
        if not self._model:
            QMessageBox.warning(self, "Apply",
                                "No GUI model available. The workbench must be "
                                "opened from the main editor window.")
            return

        s = self._state
        gs = self._model.global_settings

        gs.risk_enabled = True
        gs.risk_measure = s.risk_measure
        gs.risk_cvar_alpha = s.cvar_alpha
        gs.risk_cvar_lambda = s.cvar_lambda
        gs.risk_combination_method = s.combination_method

        # Persist climate and hazard scenarios
        gs.risk_climate_scenarios = list(s.climate_scenarios)
        gs.risk_hazard_scenarios = list(s.hazard_scenarios)

        # Compute and persist per-element risk coefficients
        if s.risk_profiles and (s.generator_map or s.battery_map):
            from esfex.models.hazard_assessment import CompositeRiskAssessment
            assessment = CompositeRiskAssessment(
                s.fragility_library, s.combination_method,
                s.risk_measure, s.cvar_alpha, s.cvar_lambda,
            )
            gen_coeffs, bat_coeffs = assessment.compute_risk_coefficients(
                s.risk_profiles, s.generator_map, s.battery_map,
            )
            gs.risk_coefficients = {**gen_coeffs, **bat_coeffs}

            # Write coefficients to individual generator/battery instances
            for sys_state in self._all_states.values():
                for inst_id, gen_inst in getattr(sys_state, "generators", {}).items():
                    if inst_id in gen_coeffs:
                        gen_inst.risk_coefficient = gen_coeffs[inst_id]
                for inst_id, bat_inst in getattr(sys_state, "batteries", {}).items():
                    if inst_id in bat_coeffs:
                        bat_inst.risk_coefficient = bat_coeffs[inst_id]

            # Compute per-node risk coefficients for investment technologies
            from esfex.visualization.workflows.risk_wizard import _FUEL_TO_COMPONENT
            n_nodes = len(s.node_coordinates)
            tech_risk_map: dict[str, list[float]] = {}
            for sys_state in self._all_states.values():
                for tid, tech in getattr(sys_state, "technologies", {}).items():
                    fuel = getattr(tech, "fuel", "")
                    comp_type = _FUEL_TO_COMPONENT.get(fuel, "solar_pv")
                    if tech.category == "Storage":
                        comp_type = "battery"
                    rc = assessment.compute_technology_risk_coefficients(
                        s.risk_profiles, comp_type, n_nodes,
                    )
                    tech_risk_map[tid] = rc

            gs.risk_technology_coefficients = tech_risk_map

            logger.info(
                "Applied risk coefficients: %d generators, %d batteries, "
                "%d technologies",
                len(gen_coeffs), len(bat_coeffs), len(tech_risk_map),
            )
        else:
            gs.risk_coefficients = {}
            gs.risk_technology_coefficients = {}

        self._model.globalSettingsUpdated.emit()

        # Pass risk data to the ResultsPanel for map rendering
        if self._map_widget and s.risk_profiles:
            all_coeffs = gs.risk_coefficients or {}
            risk_data = []
            for p in s.risk_profiles:
                lat, lon = p.coordinates
                if lat == 0 and lon == 0:
                    continue
                elements_here = []
                for gk, (ni, ct) in s.generator_map.items():
                    if ni == p.node_index:
                        elements_here.append(gk)
                for bk, (ni, ct) in s.battery_map.items():
                    if ni == p.node_index:
                        elements_here.append(bk)
                coeffs = [all_coeffs.get(e, 1.0) for e in elements_here]
                avg_coeff = sum(coeffs) / len(coeffs) if coeffs else 1.0
                risk_data.append({
                    "lat": lat, "lng": lon,
                    "risk_coefficient": avg_coeff,
                    "eal": p.expected_annual_loss,
                    "composite_risk": p.composite_risk,
                    "dominant_hazard": p.dominant_hazard,
                    "label": f"({lat:.3f}, {lon:.3f})",
                    "elements": ", ".join(elements_here[:4]),
                })

            # Find ResultsPanel via map_widget parent chain
            widget = self._map_widget
            while widget:
                results_panel = getattr(widget, "_results_panel", None)
                if results_panel and hasattr(results_panel, "set_risk_data"):
                    results_panel.set_risk_data(risk_data)
                    logger.info("Risk data sent to ResultsPanel (%d locations)", len(risk_data))
                    break
                widget = widget.parent() if hasattr(widget, "parent") else None

        n_coeffs = len(gs.risk_coefficients) if hasattr(gs, "risk_coefficients") else 0
        QMessageBox.information(
            self, "Applied",
            f"Risk configuration applied.\n\n"
            f"  Risk coefficients: {n_coeffs} elements\n"
            f"  Climate scenarios: {len(s.climate_scenarios)}\n"
            f"  Hazard scenarios: {len(s.hazard_scenarios)}\n"
            f"  Risk measure: {s.risk_measure}\n\n"
            f"Risk results are now visible on the map.",
        )

    def _export_iso_report(self):
        """Generate ISO 31000-compliant HTML report."""
        if not self._state:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ISO Report", "risk_report.html", "HTML Files (*.html)")
        if not path:
            return

        s = self._state
        state_dict = {
            "node_coordinates": s.node_coordinates,
            "hazard_maps": s.hazard_maps,
            "combination_method": s.combination_method,
            "risk_measure": s.risk_measure,
            "cvar_alpha": s.cvar_alpha,
            "cvar_lambda": s.cvar_lambda,
            "risk_profiles": [
                {
                    "node_index": p.node_index,
                    "composite_risk": p.composite_risk,
                    "expected_annual_loss": p.expected_annual_loss,
                    "dominant_hazard": p.dominant_hazard,
                }
                for p in s.risk_profiles
            ],
            "climate_scenarios": s.climate_scenarios,
            "hazard_scenarios": s.hazard_scenarios,
            "fragility_library": s.fragility_library,
        }

        from esfex.models.hazard_assessment import ISOReportGenerator
        gen = ISOReportGenerator()
        html = gen.generate_html(
            state_dict=state_dict,
            risk_evaluations=getattr(s, "risk_evaluations", None),
            resilience_metrics=getattr(s, "resilience_metrics", None),
            mc_result=getattr(s, "mc_result", None),
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        QMessageBox.information(
            self, "Report Generated",
            f"ISO 31000 report saved to:\n{path}",
        )


# =====================================================================
# WIZARD STEP 1: Hazard Assessment (combines Site & HazardData)
# =====================================================================


class HazardAssessmentPanel(QWidget):
    """Step 1: Fetch multi-hazard intensity data for all system nodes.

    Single unified panel for hazard data acquisition. Shows detected
    nodes, fetches all 7 hazard types from public APIs, and displays
    results (IM exceedance curves and node comparison heatmap).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._build_ui()

    def set_state(self, state):
        self._state = state
        self._hazard_panel.set_state(state)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._hazard_panel = HazardDataPanel()
        layout.addWidget(self._hazard_panel, 1)

    def on_enter(self):
        if hasattr(self._hazard_panel, "on_enter"):
            self._hazard_panel.on_enter()


# =====================================================================
# WIZARD STEP 2: Risk Analysis (combines Dashboard + Fragility)
# =====================================================================


class RiskAnalysisPanel(QWidget):
    """Step 2: Run composite risk assessment and compute risk coefficients.

    The Risk Dashboard is the main view. The Fragility Library editor
    is available as a secondary tab for advanced users who need to
    review or modify fragility curve parameters.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._build_ui()

    def set_state(self, state):
        self._state = state
        self._dashboard.set_state(state)
        self._fragility.set_state(state)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Step 2: Risk Analysis</h3>"
            "<p>Compute composite risk indices, expected annual loss (EAL), "
            "and per-element risk coefficients. The Fragility Library tab "
            "allows reviewing the damage curves used in the assessment.</p>"
        ))

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_inner_tab_changed)

        self._dashboard = RiskDashboardPanel()
        self._tabs.addTab(self._dashboard, "Risk Dashboard")

        self._fragility = FragilityLibraryPanel()
        self._tabs.addTab(self._fragility, "Fragility Library")

        layout.addWidget(self._tabs, 1)

    def _on_inner_tab_changed(self, index: int):
        """Call on_enter for the inner panel that just became active."""
        widget = self._tabs.widget(index)
        if widget and hasattr(widget, "on_enter"):
            widget.on_enter()

    def on_enter(self):
        if hasattr(self._dashboard, "on_enter"):
            self._dashboard.on_enter()


# =====================================================================
# WIZARD STEP 3: Scenarios (combines Climate & ScenarioTree)
# =====================================================================


class ScenariosPanel(QWidget):
    """Step 3: Configure climate and hazard scenarios for the optimizer.

    Climate scenarios define SSP pathways with temperature, GHI, and
    wind speed deltas. Hazard scenarios define discrete disaster events
    with per-element damage fractions. Both feed into the stochastic
    master problem.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._build_ui()

    def set_state(self, state):
        self._state = state
        self._climate.set_state(state)
        self._scenarios.set_state(state)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Step 3: Scenarios</h3>"
            "<p>Configure climate projections (SSP pathways, demand adjustment) "
            "and disaster scenarios (per-hazard damage fractions). These "
            "scenarios drive the stochastic optimization.</p>"
        ))

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_inner_tab_changed)

        self._climate = ClimateDemandsPanel()
        self._tabs.addTab(self._climate, "Climate & Demand")

        self._scenarios = ScenarioTreePanel()
        self._tabs.addTab(self._scenarios, "Hazard Scenarios")

        layout.addWidget(self._tabs, 1)

    def _on_inner_tab_changed(self, index: int):
        widget = self._tabs.widget(index)
        if widget and hasattr(widget, "on_enter"):
            widget.on_enter()

    def on_enter(self):
        if hasattr(self._climate, "on_enter"):
            self._climate.on_enter()


# =====================================================================
# WIZARD STEP 4: Results & Export (wraps ExportApply)
# =====================================================================


class ResultsExportPanel(QWidget):
    """Step 4: Review results summary and export in multiple formats.

    Provides a complete summary of the risk analysis, export options
    (CSV, JSON, YAML, ISO 31000 HTML report), and the Apply action
    that writes risk coefficients and scenarios to the system config.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._build_ui()

    def set_state(self, state):
        self._state = state
        self._export_panel.set_state(state)

    def set_context(self, map_widget, model, all_states):
        self._export_panel.set_context(map_widget, model, all_states)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<h3>Step 4: Results & Export</h3>"
            "<p>Review the complete risk analysis summary. Export results "
            "as CSV, JSON, YAML, or ISO 31000 HTML report. Click Apply "
            "in the bottom bar to write risk coefficients and scenarios "
            "to the system configuration.</p>"
        ))

        self._export_panel = ExportApplyPanel()
        layout.addWidget(self._export_panel, 1)

    def on_enter(self):
        if hasattr(self._export_panel, "on_enter"):
            self._export_panel.on_enter()

    def _apply(self):
        """Delegate to the inner export panel."""
        if hasattr(self._export_panel, "_apply"):
            self._export_panel._apply()
