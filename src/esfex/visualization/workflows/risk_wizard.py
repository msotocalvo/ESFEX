# -*- coding: utf-8 -*-
"""Risk & Resilience Analysis workbench.

Professional analytical tool for multi-hazard risk assessment, fragility
analysis, climate scenario generation, and composite risk mapping.

Architecture:
  - 3-step wizard with Back/Next navigation
  - Shared RiskWorkbenchState with signals for cross-panel communication

Steps:
  1. Risk Analysis       — auto-fetch hazard data, composite risk assessment,
                           fragility library, IM exceedance, EAL, sensitivity
  2. Scenarios           — climate & demand projections, hazard scenario tree
  3. Results & Export    — summary, CSV/JSON/YAML export, apply
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiModel
    from esfex.visualization.map_widget import MapWidget

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Shared State
# ──────────────────────────────────────────────────────────────────


class RiskWorkbenchState(QObject):
    """Shared mutable state across all panels.

    Emits signals when data changes so panels can update reactively.
    """

    hazard_data_changed = Signal()
    fragility_changed = Signal()
    risk_assessed = Signal()
    scenarios_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Site data — per-element coordinates for spatial analysis
        self.node_coordinates: list[tuple[float, float]] = []
        self.node_enabled: dict[int, bool] = {}  # coord_idx → enabled
        self.node_names: dict[int, str] = {}      # coord_idx → display name

        # System node aggregation — maps coord_idx back to system node
        # for aggregated visualization (charts show per-node, not per-element)
        self.coord_to_system_node: dict[int, int] = {}  # coord_idx → sys_node_idx
        self.system_node_names: dict[int, str] = {}       # sys_node_idx → name

        # Layer 1: Hazard intensity maps from fetchers
        self.hazard_maps: list = []  # list[HazardIntensityMap]

        # Layer 2: Fragility library (editable)
        from esfex.models.hazard_assessment import FragilityLibrary
        self.fragility_library: FragilityLibrary = FragilityLibrary()

        # Layer 3: Risk assessment results
        self.risk_profiles: list = []  # list[NodeRiskProfile]

        # Layer 4: Scenarios
        self.climate_scenarios: list[dict] = []
        self.hazard_scenarios: list[dict] = []

        # Risk parameters
        self.combination_method: str = "independent"
        self.risk_measure: str = "expected"
        self.cvar_alpha: float = 0.95
        self.cvar_lambda: float = 0.5

        # System info extracted from all_states (real equipment data)
        self.node_components: dict[int, list[str]] = {}
        self.component_values: dict[int, dict[str, float]] = {}
        self.generator_map: dict[str, tuple[int, str]] = {}
        self.battery_map: dict[str, tuple[int, str]] = {}
        self.total_capacity_mw: float = 0.0
        self.total_demand_mwh: float = 0.0
        self.n_generator_types: int = 0

        # ISO compliance extensions
        self.risk_evaluations: list = []        # list[RiskEvaluation]
        self.resilience_metrics: object = None  # ResilienceMetrics or None
        self.mc_result: object = None           # MonteCarloRiskResult or None


# ──────────────────────────────────────────────────────────────────
# Phase and Tab Configuration
# ──────────────────────────────────────────────────────────────────

# Fuel string → fragility component type mapping
_FUEL_TO_COMPONENT: dict[str, str] = {
    "Sun": "solar_pv", "Solar": "solar_pv",
    "Wind": "wind_turbine",
    "Diesel": "diesel_gen", "Gas": "gas_turbine",
    "Hydro": "hydro", "Nuclear": "nuclear",
    "Biomass": "biomass", "Geothermal": "geothermal",
    "Coal": "coal_plant", "Oil": "diesel_gen",
    "LNG": "gas_turbine", "Hydrogen": "gas_turbine",
}


_WIZARD_STEPS = [
    "Risk Analysis",
    "Scenarios",
    "Results & Export",
]

# ──────────────────────────────────────────────────────────────────
# Workbench Dialog
# ──────────────────────────────────────────────────────────────────


class RiskWorkbench(QDialog):
    """Professional risk & resilience analysis workbench.

    Tab-based dialog for non-linear exploration of hazard data,
    fragility curves, risk assessment, and scenario generation.
    """

    def __init__(
        self,
        map_widget: MapWidget | None = None,
        model: GuiModel | None = None,
        all_states: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Risk & Resilience Analysis")
        self.setMinimumSize(1000, 700)
        self.resize(1250, 900)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._map_widget = map_widget
        self._model = model
        self._all_states = all_states if all_states is not None else {}

        # Shared state
        self._state = RiskWorkbenchState(self)
        self._extract_system_info()

        self._build_ui()

    # ------------------------------------------------------------------
    # System info extraction from all_states
    # ------------------------------------------------------------------

    def _extract_system_info(self):
        """Extract system nodes and per-element equipment data.

        System nodes come directly from sys_state.nodes — these are the
        real nodes of the power system (typically 1-20).  Each element
        (generator, battery) gets its own query coordinate for hazard
        intensity, but maps back to its parent system node for charts.
        """
        # node_coordinates: one entry per UNIQUE element location for API queries
        coords: list[tuple[float, float]] = []
        coord_to_idx: dict[tuple[float, float], int] = {}
        # Maps coord_idx → global system node index (for chart aggregation)
        coord_to_sys_node: dict[int, int] = {}
        # Per coord_idx: component types and replacement values
        node_components: dict[int, list[str]] = {}
        component_values: dict[int, dict[str, float]] = {}
        generator_map: dict[str, tuple[int, str]] = {}
        battery_map: dict[str, tuple[int, str]] = {}
        total_cap = 0.0
        total_demand = 0.0
        fuel_types: set[str] = set()
        sys_node_names: dict[int, str] = {}
        sys_node_centroids: dict[int, tuple[float, float]] = {}

        def _get_coord_idx(lat: float, lon: float) -> int:
            key = (round(lat, 6), round(lon, 6))
            if key not in coord_to_idx:
                coord_to_idx[key] = len(coords)
                coords.append(key)
            return coord_to_idx[key]

        global_offset = 0

        for sys_name, sys_state in self._all_states.items():
            # ── Read REAL system nodes ──
            node_centroids: dict[int, tuple[float, float]] = {}
            local_to_global: dict[int, int] = {}

            for node in sys_state.nodes:
                li = getattr(node, "index", 0)
                gi = li + global_offset
                local_to_global[li] = gi
                lat = getattr(node, "centroid_lat", 0.0)
                lng = getattr(node, "centroid_lng", 0.0)
                if (lat, lng) != (0.0, 0.0):
                    node_centroids[li] = (lat, lng)
                    sys_node_centroids[gi] = (lat, lng)
                name = getattr(node, "name", "")
                sys_node_names[gi] = name or f"{sys_name} N{li}"

                demand_obj = getattr(node, "demand", None)
                if demand_obj:
                    total_demand += getattr(demand_obj, "total_mwh", 0.0) or 0.0

            # ── Bus → global node mapping ──
            bus_to_global: dict[str, int] = {}
            for bus_id, bus in getattr(sys_state, "buses", {}).items():
                lp = getattr(bus, "parent_node", 0)
                bus_to_global[bus_id] = local_to_global.get(lp, lp + global_offset)

            def _element_global_node(elem) -> int:
                bus = getattr(elem, "bus", "")
                if bus and bus in bus_to_global:
                    return bus_to_global[bus]
                ln = getattr(elem, "node", 0)
                return local_to_global.get(ln, ln + global_offset)

            def _element_coords(elem) -> tuple[float, float]:
                lat = getattr(elem, "latitude", 0.0) or 0.0
                lon = getattr(elem, "longitude", 0.0) or 0.0
                if (lat, lon) != (0.0, 0.0):
                    return (lat, lon)
                # Fallback to parent node centroid
                ln = getattr(elem, "node", 0)
                return node_centroids.get(ln, (0.0, 0.0))

            # ── Generators ──
            for inst_id, gen in getattr(sys_state, "generators", {}).items():
                gn = _element_global_node(gen)
                lat, lon = _element_coords(gen)
                if (lat, lon) == (0.0, 0.0):
                    continue

                ci = _get_coord_idx(lat, lon)
                coord_to_sys_node.setdefault(ci, gn)

                fuel = getattr(gen, "fuel", "")
                comp_type = _FUEL_TO_COMPONENT.get(fuel, "diesel_gen")
                fuel_types.add(fuel)
                rated = getattr(gen, "rated_power", 0.0) or 0.0
                total_cap += rated

                node_components.setdefault(ci, [])
                if comp_type not in node_components[ci]:
                    node_components[ci].append(comp_type)

                fixed = getattr(gen, "fixed_cost", 0.0) or 0.0
                lt = getattr(gen, "life_time", 25) or 25
                repl = rated * fixed * lt if fixed > 0 else rated * 1000.0
                component_values.setdefault(ci, {})
                component_values[ci][comp_type] = max(
                    component_values[ci].get(comp_type, 0.0), repl)

                generator_map[inst_id] = (ci, comp_type)

            # ── Batteries ──
            for inst_id, bat in getattr(sys_state, "batteries", {}).items():
                bn = _element_global_node(bat)
                lat, lon = _element_coords(bat)
                if (lat, lon) == (0.0, 0.0):
                    continue

                ci = _get_coord_idx(lat, lon)
                coord_to_sys_node.setdefault(ci, bn)

                rated = getattr(bat, "rated_power", 0.0) or 0.0
                total_cap += rated

                node_components.setdefault(ci, [])
                if "battery" not in node_components[ci]:
                    node_components[ci].append("battery")

                fixed = getattr(bat, "fixed_cost", 0.0) or 0.0
                lt = getattr(bat, "life_time", 15) or 15
                repl = rated * fixed * lt if fixed > 0 else rated * 500.0
                component_values.setdefault(ci, {})
                component_values[ci]["battery"] = max(
                    component_values[ci].get("battery", 0.0), repl)

                battery_map[inst_id] = (ci, "battery")

            # ── Transmission lines ──
            for line in getattr(sys_state, "transmission_lines", []):
                for attr in ("from_node", "to_node"):
                    ln = getattr(line, attr, -1)
                    c = node_centroids.get(ln)
                    if c:
                        ci = _get_coord_idx(*c)
                        coord_to_sys_node.setdefault(
                            ci, local_to_global.get(ln, ln + global_offset))
                        node_components.setdefault(ci, [])
                        if "transmission_line" not in node_components[ci]:
                            node_components[ci].append("transmission_line")

            global_offset += len(sys_state.nodes)

        # Ensure substation at every location with equipment
        for comps in node_components.values():
            if comps and "substation" not in comps:
                comps.append("substation")

        # Store in shared state
        self._state.node_coordinates = coords
        self._state.node_components = node_components
        self._state.component_values = component_values
        self._state.generator_map = generator_map
        self._state.battery_map = battery_map
        self._state.total_capacity_mw = total_cap
        self._state.total_demand_mwh = total_demand
        self._state.n_generator_types = len(fuel_types - {""})
        self._state.coord_to_system_node = coord_to_sys_node
        self._state.system_node_names = sys_node_names

        # Node selection by system node
        for gi, name in sys_node_names.items():
            self._state.node_enabled[gi] = True
            n_el = sum(1 for sn in coord_to_sys_node.values() if sn == gi)
            c = sys_node_centroids.get(gi)
            loc = f"({c[0]:.2f}, {c[1]:.2f})" if c else ""
            self._state.node_names[gi] = f"{name} {loc} [{n_el} el.]"

        logger.info(
            "Risk workbench: %d system nodes, %d element locations, "
            "%.1f MW, %d generators, %d batteries",
            len(sys_node_names), len(coords), total_cap,
            len(generator_map), len(battery_map),
        )

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        from esfex.visualization.theme import current_theme
        c = current_theme().colors

        layout = QVBoxLayout(self)

        # Step indicator bar
        top_bar = QHBoxLayout()
        self._lbl_step = QLabel("")
        self._lbl_step.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {c.text_primary};"
        )
        top_bar.addWidget(self._lbl_step)
        top_bar.addStretch()
        self._lbl_nodes = QLabel(
            f"{len(self._state.node_coordinates)} nodes detected"
        )
        self._lbl_nodes.setStyleSheet(
            f"font-style: italic; color: {c.text_secondary};"
        )
        top_bar.addWidget(self._lbl_nodes)
        layout.addLayout(top_bar)

        # Tab widget (tabs hidden — navigation via Back/Next)
        self._tabs = QTabWidget()
        self._tabs.tabBar().setVisible(False)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._panels = self._create_panels()

        self._step_names = _WIZARD_STEPS
        for name, panel in zip(self._step_names, self._panels):
            self._tabs.addTab(panel, name)

        layout.addWidget(self._tabs, 1)

        # Bottom navigation bar
        bottom = QHBoxLayout()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)

        bottom.addStretch()

        self._status_label = QLabel("")
        bottom.addWidget(self._status_label)

        bottom.addStretch()

        self._btn_back = QPushButton("\u25c0 Back")
        self._btn_back.clicked.connect(self._go_back)
        bottom.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next \u25b6")
        self._btn_next.setStyleSheet(
            f"background-color: {c.accent_primary}; color: white; "
            f"font-weight: bold; padding: 6px 16px; border-radius: 3px;"
        )
        self._btn_next.clicked.connect(self._go_next)
        bottom.addWidget(self._btn_next)

        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setStyleSheet(
            f"background-color: {c.accent_secondary}; color: white; "
            f"font-weight: bold; padding: 6px 16px; border-radius: 3px;"
        )
        self._btn_apply.clicked.connect(self._apply_results)
        self._btn_apply.setVisible(False)
        bottom.addWidget(self._btn_apply)

        layout.addLayout(bottom)

        # Initialize navigation state
        self._update_navigation()

    def _create_panels(self) -> list:
        """Create the 3 wizard step panels."""
        from esfex.visualization.workflows.risk_panels import (
            RiskAnalysisPanel,
            ScenariosPanel,
            ResultsExportPanel,
        )

        risk = RiskAnalysisPanel()
        scenarios = ScenariosPanel()
        results = ResultsExportPanel()

        panels = [risk, scenarios, results]

        for panel in panels:
            panel.set_state(self._state)

        results.set_context(self._map_widget, self._model, self._all_states)

        return panels

    # ------------------------------------------------------------------
    # Wizard Navigation
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int):
        """Notify panel when it becomes active and update navigation."""
        if 0 <= index < len(self._panels):
            panel = self._panels[index]
            if hasattr(panel, "on_enter"):
                panel.on_enter()
        self._update_navigation()

    def _update_navigation(self):
        """Update Back/Next/Apply button states and step indicator."""
        if not hasattr(self, "_btn_back"):
            return  # Called during _build_ui before buttons exist
        idx = self._tabs.currentIndex()
        total = self._tabs.count()
        is_last = idx == total - 1

        self._btn_back.setEnabled(idx > 0)
        self._btn_next.setVisible(not is_last)
        self._btn_apply.setVisible(is_last)

        # Step indicator: "Step 3 of 7 — Fragility Library"
        if 0 <= idx < len(self._step_names):
            self._lbl_step.setText(
                f"Step {idx + 1} of {total} \u2014 {self._step_names[idx]}"
            )

    def _go_back(self):
        idx = self._tabs.currentIndex()
        if idx > 0:
            self._tabs.setCurrentIndex(idx - 1)

    def _go_next(self):
        idx = self._tabs.currentIndex()
        if idx < self._tabs.count() - 1:
            self._tabs.setCurrentIndex(idx + 1)

    def _apply_results(self):
        """Apply risk analysis results to the system configuration.

        Only available on the last step. Validates that the assessment
        pipeline has been completed before applying.
        """
        if not self._state:
            return

        # Validate pipeline completeness
        missing = []
        if not self._state.risk_profiles:
            missing.append("Risk assessment (Step 1 — click 'Assess Risk')")

        if missing:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Incomplete Analysis",
                "The following steps must be completed before applying:\n\n"
                + "\n".join(f"  \u2022 {m}" for m in missing)
                + "\n\nPlease go back and complete these steps.",
            )
            return

        export_panel = self._panels[-1]
        if hasattr(export_panel, "_apply"):
            export_panel._apply()

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Accessors (backward compatibility)
    # ------------------------------------------------------------------

    @property
    def map_widget(self):
        return self._map_widget

    @property
    def model(self):
        return self._model

    @property
    def all_states(self) -> dict:
        return self._all_states

    @property
    def state(self) -> RiskWorkbenchState:
        return self._state


