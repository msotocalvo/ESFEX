# -*- coding: utf-8 -*-
"""OTEC Studio — inverse-design optimization engine (M1).

Thin, GUI-independent wrappers over ``otex.optimization`` so the Optimization
panel's logic is unit-testable headless. This is the capability the wizard never
exposed: instead of a forward on-design sweep, drive OTEX's ``optimize_site``
(minimize LCOE within design bounds, subject to optional ``UserConstraints``)
and the ``evaluate`` design-point evaluator that draws the LCOE surface.

Conventions (OTEX): ``p_gross`` is in **kW** with the negative-=-output sign
convention; bounds default to p_gross∈[-500000,-1000] kW, dT∈[1,6] K,
depth_CW∈[600,3000] m.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from esfex.visualization.workflows.otec_studio.project import StudioConfig


# Design variables OTEX optimizes / evaluates over.
DESIGN_VARS = ("p_gross", "dT_WW", "dT_CW", "depth_CW")


def transmission_efficiency(dist_shore_km: float,
                            threshold_ac_dc: float = 50.0) -> float:
    """Cable transmission efficiency vs distance to shore.

    Mirrors ``otex.data.cmems.data_processing`` (and the wizard's
    ``OTECAnalyzer``): an AC fit below the AC/DC threshold, a DC fit above,
    floored at 0.01. Reimplemented here (not imported) to stay Qt-free.
    """
    d = float(dist_shore_km)
    if d <= threshold_ac_dc:
        eff = 0.979 - 1e-6 * d ** 2 - 9e-5 * d
    else:
        eff = 0.964 - 8e-5 * d
    return max(eff, 0.01)


def build_inputs_template(config: StudioConfig) -> dict:
    """OTEX legacy inputs dict from a StudioConfig (same mapping as the wizard)."""
    from otex.config import get_default_config

    cfg = get_default_config(
        gross_power=config.gross_power,
        cycle_type=config.cycle_type,
        fluid_type=config.fluid_type,
        cost_level=config.cost_level,
        year=config.year,
    )
    cfg.plant.installation_type = config.installation
    cfg.depth_limits.min_depth = config.min_depth
    cfg.depth_limits.max_depth = config.max_depth
    cfg.economics.discount_rate = config.discount_rate
    cfg.economics.lifetime_years = config.plant_lifetime
    cfg.economics.availability = config.availability
    return cfg.to_legacy_dict()


def build_site_context(
    config: StudioConfig,
    t_ww: float,
    t_cw: float,
    dist_shore: float,
    latitude: float,
    longitude: float,
    site_id: int = 0,
) -> Any:
    """Assemble an ``otex.optimization.SiteContext`` from config + site data."""
    from otex.optimization import SiteContext

    inputs = build_inputs_template(config)
    threshold = float(inputs.get("threshold_AC_DC", 50.0))
    eff = transmission_efficiency(dist_shore, threshold)
    return SiteContext(
        site_id=site_id,
        longitude=longitude,
        latitude=latitude,
        T_WW_in=t_ww,
        T_CW_in=t_cw,
        dist_shore=dist_shore,
        eff_trans=eff,
        inputs_template=inputs,
        cost_level=config.cost_level,
    )


def make_bounds(
    p_gross: tuple[float, float] = (-500000.0, -1000.0),
    dT_WW: tuple[float, float] = (1.0, 6.0),
    dT_CW: tuple[float, float] = (1.0, 6.0),
    depth_CW: tuple[float, float] = (600.0, 3000.0),
) -> Any:
    from otex.optimization import Bounds
    return Bounds(p_gross=p_gross, dT_WW=dT_WW, dT_CW=dT_CW, depth_CW=depth_CW)


def make_constraints(
    max_capex_MUSD: Optional[float] = None,
    max_p_net_MW: Optional[float] = None,
    max_aep_MWh: Optional[float] = None,
    max_p_gross_MW: Optional[float] = None,
    max_parasitic_ratio: Optional[float] = None,
) -> Optional[Any]:
    """Build UserConstraints, or None if no cap is set (no interior optimum)."""
    if all(v is None for v in (max_capex_MUSD, max_p_net_MW, max_aep_MWh,
                               max_p_gross_MW, max_parasitic_ratio)):
        return None
    from otex.optimization import UserConstraints
    return UserConstraints(
        max_aep_MWh=max_aep_MWh,
        max_p_net_MW=max_p_net_MW,
        max_capex_MUSD=max_capex_MUSD,
        max_p_gross_MW=max_p_gross_MW,
        max_parasitic_ratio=max_parasitic_ratio,
    )


def run_optimization(
    site: Any,
    bounds: Optional[Any] = None,
    constraints: Optional[Any] = None,
    options: Optional[dict] = None,
) -> Any:
    """Run ``optimize_site`` → OptimizationResult (success, x, lcoe, …)."""
    from otex.optimization import optimize_site

    kw: dict[str, Any] = {}
    if bounds is not None:
        kw["bounds"] = bounds
    if constraints is not None:
        kw["user_constraints"] = constraints
    if options is not None:
        kw["options"] = options
    return optimize_site(site, **kw)


def evaluate_design(
    site: Any, p_gross: float, dT_WW: float, dT_CW: float, depth_CW: float,
) -> Any:
    """Evaluate a single design point → DesignResult (lcoe, p_net, capex, …)."""
    from otex.optimization import DesignVector, evaluate
    return evaluate(DesignVector(p_gross, dT_WW, dT_CW, depth_CW), site)


def lcoe_surface(
    site: Any,
    base: dict,
    var_x: str,
    var_y: str,
    x_vals,
    y_vals,
) -> dict:
    """Sweep two design variables (others held at ``base``) → LCOE surface.

    Returns ``{var_x, var_y, x_vals, y_vals, lcoe}`` where ``lcoe`` is a
    ``(len(y_vals), len(x_vals))`` array (NaN where a point fails to evaluate).
    """
    if var_x not in DESIGN_VARS or var_y not in DESIGN_VARS:
        raise ValueError(f"design vars must be in {DESIGN_VARS}")
    x_vals = list(x_vals)
    y_vals = list(y_vals)
    grid = np.full((len(y_vals), len(x_vals)), np.nan)
    for j, yv in enumerate(y_vals):
        for i, xv in enumerate(x_vals):
            d = dict(base)
            d[var_x] = xv
            d[var_y] = yv
            try:
                res = evaluate_design(
                    site, d["p_gross"], d["dT_WW"], d["dT_CW"], d["depth_CW"],
                )
                grid[j, i] = float(res.lcoe)
            except Exception:
                pass  # leave NaN; the surface tolerates infeasible points
    return {
        "var_x": var_x, "var_y": var_y,
        "x_vals": x_vals, "y_vals": y_vals, "lcoe": grid,
    }
