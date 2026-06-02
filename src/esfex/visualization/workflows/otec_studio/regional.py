# -*- coding: utf-8 -*-
"""OTEC Studio — regional optimization engine (M7).

Batch inverse-design across every feasible site in a named region via OTEX's
``run_regional_optimization``: one optimal design per site. Unlike the wizard's
forward on-design regional sweep, this *optimizes* each site (and accepts
``UserConstraints`` to explore what-if caps across a whole region).

Network boundary: the regional run downloads CMEMS/HYCOM data and builds the
site catalogue, so it runs off-thread with graceful failure. The result-frame
post-processing (feasibility filter, summary, ranking) is pure and unit-tested
against a synthetic frame with the real column schema.
"""

from __future__ import annotations

from typing import Any, Optional

# Result-frame columns produced by run_regional_optimization (0.3.1).
RESULT_COLUMNS = (
    "id", "longitude", "latitude", "T_WW_design", "T_CW_design",
    "lcoe_min", "p_net_kW", "p_gross_opt_MW", "dT_WW_opt", "dT_CW_opt",
    "depth_CW_opt", "capex_total_MUSD", "opex_MUSDyr",
    "feasible", "success", "max_violation", "n_evaluations",
)


def list_regions() -> list[str]:
    """All region names OTEX knows (242 countries/territories)."""
    from otex.data import list_regions as _lr

    return list(_lr())


def run_regional(
    region: str,
    *,
    cost_level: str = "low_cost",
    cycle_type: str = "rankine_closed",
    fluid_type: str = "ammonia",
    bounds: Optional[Any] = None,
    user_constraints: Optional[Any] = None,
    output_dir: Optional[str] = None,
):
    """Optimize every feasible site in ``region`` (NETWORK) → DataFrame."""
    from otex.optimization import run_regional_optimization

    kw: dict[str, Any] = {
        "cost_level": cost_level,
        "cycle_type": cycle_type,
        "fluid_type": fluid_type,
        "verbose": False,
    }
    if bounds is not None:
        kw["bounds"] = bounds
    if user_constraints is not None:
        kw["user_constraints"] = user_constraints
    if output_dir is not None:
        kw["output_dir"] = output_dir
    return run_regional_optimization(region, **kw)


# ---------------------------------------------------------------------------
# Pure post-processing (testable)
# ---------------------------------------------------------------------------


def _feasible_mask(df):
    """Boolean mask of feasible, successfully-solved sites."""
    import numpy as np

    mask = np.ones(len(df), dtype=bool)
    if "feasible" in df.columns:
        mask &= df["feasible"].astype(bool).to_numpy()
    if "success" in df.columns:
        mask &= df["success"].astype(bool).to_numpy()
    if "lcoe_min" in df.columns:
        mask &= np.isfinite(df["lcoe_min"].to_numpy())
    return mask


def filter_feasible(df):
    """Subset of the result frame that is feasible and solved."""
    return df[_feasible_mask(df)].copy()


def summarize_regional(df) -> dict:
    """Portfolio-level summary across a regional result frame."""
    import numpy as np

    n_total = len(df)
    feas = filter_feasible(df)
    n_feasible = len(feas)
    out = {
        "n_total": n_total,
        "n_feasible": n_feasible,
        "feasible_fraction": (n_feasible / n_total) if n_total else 0.0,
        "lcoe_min": None,
        "lcoe_median": None,
        "lcoe_max": None,
        "total_capacity_MW": None,
        "best_site": None,
    }
    if n_feasible == 0:
        return out
    lcoe = feas["lcoe_min"].to_numpy(dtype=float)
    out["lcoe_min"] = float(np.min(lcoe))
    out["lcoe_median"] = float(np.median(lcoe))
    out["lcoe_max"] = float(np.max(lcoe))
    if "p_net_kW" in feas.columns:
        out["total_capacity_MW"] = float(
            np.sum(np.abs(feas["p_net_kW"].to_numpy(dtype=float))) / 1000.0
        )
    best_idx = int(np.argmin(lcoe))
    best = feas.iloc[best_idx]
    out["best_site"] = {
        "id": best.get("id"),
        "longitude": float(best.get("longitude", float("nan"))),
        "latitude": float(best.get("latitude", float("nan"))),
        "lcoe": float(best["lcoe_min"]),
    }
    return out
