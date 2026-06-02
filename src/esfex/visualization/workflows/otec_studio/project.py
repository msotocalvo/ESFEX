# -*- coding: utf-8 -*-
"""OTEC Studio shared project model (the "spine").

GUI-independent and dependency-light on purpose: it must import without Qt or
OTEX so the model is unit-testable headless (the OTEX result objects are held
opaquely as ``Any``). The Qt shell and panels read/write this model.

A :class:`OtexProject` owns N :class:`OtexScenario` objects (each a config plus
its cached OTEX results) and one shared :class:`ResourceData` (site/ocean data),
so branching a scenario does not re-download CMEMS. This is what enables the
non-linear, A/B-comparison workflow the wizard cannot do.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Optional


# =====================================================================
# Configuration
# =====================================================================


@dataclass
class StudioConfig:
    """All OTEX design knobs for one scenario.

    The first block mirrors the wizard's ``OTECConfig`` (same names/defaults) so
    a scenario can drive the existing analysis layer unchanged; the second block
    adds the knobs the wizard hides (cycle composition, climate, degradation…).
    Kept standalone (no import of the Qt-bound ``otec_analysis.OTECConfig``) to
    stay headless-importable.
    """

    # --- mirrors OTECConfig ---
    cycle_type: str = "rankine_closed"   # rankine_closed|rankine_open|rankine_hybrid|kalina|uehara
    fluid_type: str = "ammonia"
    gross_power: int = -136000           # kW (negative = net output convention)
    cost_level: str = "low_cost"         # low_cost|high_cost
    year: int = 2020
    installation: str = "offshore"       # offshore|onshore
    min_depth: float = 600.0
    max_depth: float = 3000.0
    lcoe_threshold: float = 0.15         # $/kWh (zone selection)
    zone_buffer_km: float = 10.0
    discount_rate: float = 0.10
    plant_lifetime: int = 30
    availability: float = 0.914
    grid_resolution: float = 0.25        # degrees

    # --- Studio extensions (knobs the wizard does not expose) ---
    # Mixture cycles (Kalina / Uehara)
    ammonia_concentration: float = 0.70
    split_ratio: float = 0.30
    # Hybrid cycle power split (closed-cycle fraction)
    power_split: float = 0.88
    # Climate scenario (CMIP6 / SSP delta downscaling); None = historical
    ssp: Optional[str] = None            # e.g. "ssp245", "ssp585"
    horizon_year: Optional[int] = None
    # Cold-water intake depth optimization
    optimize_depth: bool = False
    # Performance degradation over lifetime
    degradation_model: str = "constant"  # constant|logistic|step
    degradation_rate: float = 0.005
    # Cost scheme selector
    cost_scheme: str = "default"


# =====================================================================
# Shared resource data (site / ocean)
# =====================================================================


@dataclass
class ResourceData:
    """Site/ocean data shared by all scenarios in a project.

    Held at the project level so branching a scenario reuses the (expensive to
    download) CMEMS/HYCOM data instead of re-fetching. Fields are intentionally
    permissive — populated by the Site & Resource panel later.
    """

    name: str = ""
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    t_ww: Optional[float] = None         # representative warm-water temp (°C)
    t_cw: Optional[float] = None         # representative cold-water temp (°C)
    depth_profiles: Any = None           # multi-depth temperature profiles
    siting_layers: Any = None            # hazard enrichment (MPA/AIS/seismic/…)
    daily_data: Any = None               # per-cell daily series (for operation)
    bounds: Optional[tuple] = None       # (south, west, north, east) if regional

    @property
    def has_design_point(self) -> bool:
        return self.t_ww is not None and self.t_cw is not None


# =====================================================================
# Scenario
# =====================================================================


@dataclass
class OtexScenario:
    """One config plus its cached OTEX results.

    Result fields hold opaque OTEX objects (``Any``) so this module never has to
    import OTEX; panels fill them in as the user runs each stage.
    """

    name: str
    config: StudioConfig = field(default_factory=StudioConfig)
    site: Any = None                     # otex.optimization.SiteContext
    design: Any = None                   # DesignResult (optimize_site / evaluate)
    plant: Any = None                    # on_design_analysis plant dict
    cost_breakdown: Any = None           # CAPEX/OPEX dict
    operation: Any = None                # otec_operation time-series
    uncertainty: Any = None              # UncertaintyResults
    sensitivity: dict = field(default_factory=dict)   # {"tornado":.., "sobol":..}
    inputs: dict = field(default_factory=dict)        # to_legacy_dict() cache

    def clear_results(self) -> None:
        """Drop cached results (e.g. after a config edit) keeping the config."""
        self.site = None
        self.design = None
        self.plant = None
        self.cost_breakdown = None
        self.operation = None
        self.uncertainty = None
        self.sensitivity = {}
        self.inputs = {}


# =====================================================================
# Project
# =====================================================================


@dataclass
class OtexProject:
    """A workbench session: scenarios + shared resource + active selection."""

    scenarios: list[OtexScenario] = field(default_factory=list)
    active_index: int = 0
    resource: Optional[ResourceData] = None

    def __post_init__(self) -> None:
        if not self.scenarios:
            self.scenarios = [OtexScenario(name="Scenario 1")]
        self.active_index = max(0, min(self.active_index, len(self.scenarios) - 1))

    # -- access --
    @property
    def active(self) -> OtexScenario:
        return self.scenarios[self.active_index]

    def set_active(self, index: int) -> None:
        if not 0 <= index < len(self.scenarios):
            raise IndexError(f"scenario index {index} out of range")
        self.active_index = index

    def _unique_name(self, base: str) -> str:
        existing = {s.name for s in self.scenarios}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    # -- mutate --
    def add_scenario(
        self, name: Optional[str] = None,
        config: Optional[StudioConfig] = None,
    ) -> OtexScenario:
        name = self._unique_name(name or f"Scenario {len(self.scenarios) + 1}")
        sc = OtexScenario(name=name, config=config or StudioConfig())
        self.scenarios.append(sc)
        self.active_index = len(self.scenarios) - 1
        return sc

    def branch(self, name: Optional[str] = None) -> OtexScenario:
        """Clone the active scenario's CONFIG into a new scenario (no results).

        The shared project resource is reused (not copied), so the branch does
        not re-download data — the whole point of the scenario model.
        """
        src = self.active
        new = OtexScenario(
            name=self._unique_name(name or f"{src.name} (branch)"),
            config=copy.deepcopy(src.config),
        )
        self.scenarios.append(new)
        self.active_index = len(self.scenarios) - 1
        return new

    def remove_scenario(self, index: int) -> None:
        if len(self.scenarios) <= 1:
            raise ValueError("cannot remove the last scenario")
        if not 0 <= index < len(self.scenarios):
            raise IndexError(f"scenario index {index} out of range")
        self.scenarios.pop(index)
        self.active_index = min(self.active_index, len(self.scenarios) - 1)

    def update_active_config(self, **changes: Any) -> StudioConfig:
        """Apply field changes to the active config and invalidate its results."""
        sc = self.active
        sc.config = replace(sc.config, **changes)
        sc.clear_results()
        return sc.config

    # -- compare --
    def compare(self) -> list[dict]:
        """Key metrics across all scenarios (for the compare dock / A-B)."""
        return [scenario_metrics(s) for s in self.scenarios]


# =====================================================================
# Metric extraction (defensive — OTEX result shapes vary by call)
# =====================================================================


def _first(obj: Any, keys: tuple[str, ...]) -> Optional[float]:
    """Return the first present attribute/key from obj, coerced to float."""
    if obj is None:
        return None
    for k in keys:
        val = None
        if isinstance(obj, dict) and k in obj:
            val = obj[k]
        elif hasattr(obj, k):
            val = getattr(obj, k)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        return f
    return None


def scenario_metrics(sc: OtexScenario) -> dict:
    """Extract a comparable metric row from a scenario's cached results.

    Pulls LCOE / net power / CAPEX / feasibility from whichever result object is
    populated (``design`` from optimization, else ``plant``/``cost_breakdown``
    from on-design), returning None where unavailable.
    """
    lcoe = _first(sc.design, ("lcoe", "LCOE", "lcoe_usd_per_kwh")) \
        or _first(sc.cost_breakdown, ("LCOE", "lcoe")) \
        or _first(sc.plant, ("LCOE_nom", "lcoe_nom", "LCOE"))
    # OTEX reports net power in kW (negative = output); expose MW magnitude.
    p_net_kw = _first(sc.design, ("p_net", "p_net_MW", "net_power_MW")) \
        or _first(sc.plant, ("p_net_nom", "p_net"))
    p_net = abs(p_net_kw) / 1000.0 if p_net_kw is not None else None
    capex = _first(sc.design, ("capex_MUSD", "capex_total", "capex", "CAPEX_total")) \
        or _first(sc.cost_breakdown, ("CAPEX_total", "capex"))
    # Feasibility: prefer an explicit flag, else derive from an optimizer's
    # success + max_violation (OptimizationResult).
    feasible = None
    if sc.design is not None:
        if (isinstance(sc.design, dict) and "feasible" in sc.design) \
                or hasattr(sc.design, "feasible"):
            feasible = bool(_first(sc.design, ("feasible",)) or 0)
        elif hasattr(sc.design, "success"):
            mv = _first(sc.design, ("max_violation",))
            feasible = bool(getattr(sc.design, "success")) and (mv is None or mv <= 1e-3)
    return {
        "name": sc.name,
        "cycle": sc.config.cycle_type,
        "fluid": sc.config.fluid_type,
        "lcoe": lcoe,
        "p_net_mw": p_net,
        "capex": capex,
        "feasible": feasible,
        "has_results": any(
            x is not None for x in (sc.design, sc.plant, sc.operation)
        ),
    }
