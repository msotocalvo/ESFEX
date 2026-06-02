# -*- coding: utf-8 -*-
"""OTEC Studio — economics engine (M3).

GUI-independent wrappers over ``otex.economics`` and ``on_design_analysis`` for
the Economics panel: nominal CAPEX/OPEX breakdown, lifetime power degradation,
and the NPV-based LCOE that the wizard's point-in-time LCOE ignores.

Degradation compounds over a 20-30 year life: a logistic or constant decay
lowers annual energy and therefore *raises* the real LCOE vs the nameplate
value — this engine quantifies that.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from esfex.visualization.workflows.otec_studio.optimize import (
    build_inputs_template,
    transmission_efficiency,
)
from esfex.visualization.workflows.otec_studio.project import StudioConfig

DEGRADATION_MODELS = ("constant", "logistic", "step")


def _scalar(d: dict, key: str, default=None):
    """First scalar from a possibly-vectorised plant/cost value."""
    if key not in d:
        return default
    arr = np.ravel(d[key])
    return float(arr[0]) if arr.size else default


def run_on_design(
    config: StudioConfig, t_ww: float, t_cw: float, dist_shore: float,
) -> dict:
    """Run OTEX ``on_design_analysis`` for one site → nominal plant + costs.

    Returns ``{plant, cost_breakdown, inputs}``. ``on_design_analysis`` is
    vectorised over sites and wants ``dist_shore``/``eff_trans`` arrays in
    ``inputs`` and array temperatures, so we wrap a single site.
    """
    from otex.plant.off_design_analysis import on_design_analysis

    inputs = dict(build_inputs_template(config))
    threshold = float(inputs.get("threshold_AC_DC", 50.0))
    inputs["dist_shore"] = np.array([[float(dist_shore)]])
    inputs["eff_trans"] = np.array(
        [[transmission_efficiency(dist_shore, threshold)]]
    )
    plant, cbd = on_design_analysis(
        np.array([float(t_ww)]), np.array([float(t_cw)]),
        inputs, cost_level=config.cost_level,
    )
    return {"plant": plant, "cost_breakdown": cbd, "inputs": inputs}


def capex_components(cost_breakdown: dict) -> tuple[dict, float]:
    """Per-component CAPEX (the ``*_CAPEX`` keys) and their total."""
    comps = {
        k[:-6]: _scalar(cost_breakdown, k)
        for k in cost_breakdown
        if k.endswith("_CAPEX")
    }
    comps = {k: v for k, v in comps.items() if v is not None}
    total = float(sum(comps.values()))
    return comps, total


def degradation_series(
    model: str,
    rate: float,
    lifetime: int,
    logistic_L: float = 0.3,
    logistic_k: float = 0.3,
    logistic_t0: float = 15.0,
    step_years: list[int] | None = None,
    step_drops: list[float] | None = None,
) -> np.ndarray:
    """Per-year power-retention factor (1.0 = nameplate) over the lifetime."""
    from otex.economics import DegradationConfig, degradation_factor

    cfg = DegradationConfig(
        model=model,
        rate=rate,
        logistic_L=logistic_L,
        logistic_k=logistic_k,
        logistic_t0=logistic_t0,
        step_years=step_years or [],
        step_drops=step_drops or [],
    )
    return degradation_factor(int(lifetime), cfg)


def npv_lcoe(
    plant: dict, inputs: dict, p_net_nom: float, factors: np.ndarray,
) -> float:
    """NPV-based LCOE given per-year degradation factors."""
    from otex.economics import lcoe_npv

    factors = np.asarray(factors)
    years = np.arange(len(factors))
    p_by_year = (p_net_nom * factors).reshape(-1, 1)  # (years, 1 site)
    lc = lcoe_npv(plant, inputs, p_by_year, years)
    return float(np.ravel(lc)[0])


def analyze(
    config: StudioConfig,
    t_ww: float,
    t_cw: float,
    dist_shore: float,
    deg_model: str = "constant",
    deg_rate: float = 0.005,
    **deg_kw: Any,
) -> dict:
    """End-to-end economics for one site: on-design + degradation + NPV-LCOE."""
    od = run_on_design(config, t_ww, t_cw, dist_shore)
    plant, cbd, inputs = od["plant"], od["cost_breakdown"], od["inputs"]
    comps, capex_total = capex_components(cbd)
    lifetime = int(np.ravel(inputs.get("lifetime_years", config.plant_lifetime))[0])
    p_net_nom = _scalar(plant, "p_net_nom")
    lcoe_nom = _scalar(plant, "LCOE_nom") or _scalar(cbd, "LCOE")
    factors = degradation_series(deg_model, deg_rate, lifetime, **deg_kw)
    lcoe_npv_val = npv_lcoe(plant, inputs, p_net_nom, factors)
    return {
        "capex_components": comps,
        "capex_total": capex_total,
        "opex": _scalar(cbd, "OPEX"),
        "lcoe_nominal": lcoe_nom,
        "lcoe_npv": lcoe_npv_val,
        "p_net_nom": p_net_nom,
        "lifetime": lifetime,
        "degradation": factors,
        "p_net_by_year": np.abs(p_net_nom * factors) / 1000.0,  # MW magnitude
    }
