# -*- coding: utf-8 -*-
"""OTEC Studio — uncertainty & sensitivity engine (M5).

GUI-independent wrappers over ``otex.analysis`` for Monte Carlo, Tornado and
Sobol studies. Unlike the wizard (which hardcodes ``output='lcoe'`` and a fixed
parameter set), this exposes a selectable output metric and editable parameter
distributions, and is reusable across scenarios.

Confirmed in OTEX 0.3.1: MC reports samples/stats for lcoe / net_power / capex /
opex; Tornado and Sobol accept ``output='lcoe'`` or ``'capex'``.
"""

from __future__ import annotations

from typing import Any

from esfex.visualization.workflows.otec_studio.project import StudioConfig

# Output metrics each analysis can target.
MC_METRICS = ("lcoe", "net_power", "capex", "opex")
SENS_OUTPUTS = ("lcoe", "capex")


def default_parameters() -> list[dict]:
    """OTEX default uncertain parameters as editable plain dicts."""
    from otex.analysis import get_default_parameters

    rows = []
    for p in get_default_parameters():
        b = getattr(p, "bounds", (None, None))
        rows.append({
            "name": p.name,
            "distribution": p.distribution,
            "nominal": float(p.nominal),
            "p1": float(b[0]),
            "p2": float(b[1]),
            "category": getattr(p, "category", ""),
        })
    return rows


def _uncertainty_config(params: list[dict], n_samples: int, seed: int) -> Any:
    from otex.analysis import UncertainParameter, UncertaintyConfig

    ups = [
        UncertainParameter(
            name=p["name"], nominal=p["nominal"], distribution=p["distribution"],
            bounds=(p["p1"], p["p2"]), category=p.get("category", ""),
        )
        for p in params
    ]
    # parallel=False: the worker thread already offloads from the GUI; avoid
    # spawning a process pool from within a QThread.
    return UncertaintyConfig(
        parameters=ups, n_samples=n_samples, seed=seed, parallel=False,
    )


def _analysis_kwargs(cfg: StudioConfig) -> dict:
    return {
        "p_gross": cfg.gross_power,
        "cost_level": cfg.cost_level,
        "cycle_type": cfg.cycle_type,
        "fluid_type": cfg.fluid_type,
        "installation_type": cfg.installation,
    }


def run_monte_carlo(
    cfg: StudioConfig, t_ww: float, t_cw: float,
    params: list[dict], n_samples: int = 500, seed: int = 42,
) -> dict:
    """Monte Carlo → {stats (per metric), df (samples per metric)}."""
    from otex.analysis import MonteCarloAnalysis

    uc = _uncertainty_config(params, n_samples, seed)
    res = MonteCarloAnalysis(t_ww, t_cw, config=uc, **_analysis_kwargs(cfg)).run(
        show_progress=False
    )
    return {"stats": res.compute_statistics(), "df": res.to_dataframe()}


def run_tornado(
    cfg: StudioConfig, t_ww: float, t_cw: float, params: list[dict],
    variation_pct: float = 10.0, output: str = "lcoe",
    n_samples: int = 64, seed: int = 42,
) -> dict:
    """Tornado one-at-a-time sensitivity → {ranking, output, baseline}."""
    from otex.analysis import TornadoAnalysis

    uc = _uncertainty_config(params, n_samples, seed)
    res = TornadoAnalysis(
        t_ww, t_cw, variation_pct=variation_pct, config=uc,
        **_analysis_kwargs(cfg),
    ).run(output=output, show_progress=False)
    return {
        "ranking": list(res.get_ranking()),
        "output": res.output_name,
        "baseline": getattr(res, "baseline", None),
    }


def run_sobol(
    cfg: StudioConfig, t_ww: float, t_cw: float, params: list[dict],
    n_samples: int = 256, output: str = "lcoe", seed: int = 42,
) -> dict:
    """Sobol global sensitivity → {ranking, S1, ST, output}."""
    from otex.analysis import SobolAnalysis

    uc = _uncertainty_config(params, n_samples, seed)
    res = SobolAnalysis(
        t_ww, t_cw, n_samples=n_samples, config=uc, **_analysis_kwargs(cfg),
    ).run(output=output, show_progress=False)
    d = res.to_dict()
    return {
        "ranking": list(res.get_ranking()),
        "S1": d.get("S1"), "ST": d.get("ST"),
        "output": res.output_name,
    }
