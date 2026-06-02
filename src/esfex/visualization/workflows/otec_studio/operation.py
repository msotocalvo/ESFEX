# -*- coding: utf-8 -*-
"""OTEC Studio — time-series operation engine (M4).

GUI-independent wrapper over ``otex.plant.operation.otec_operation`` for the
Operation panel: simulate a nominal plant against a time-varying seawater
temperature profile (per-site, not averaged like the wizard), then *diagnose*
why net power drops below nameplate.

``otec_operation`` returns the regulated operating state per timestep but does
not label the binding limit. We attribute each timestep's net-power deficit to
its two physical drivers — a smaller temperature lift (lower gross power) versus
higher seawater-pump parasitics — which is the actionable "why".
"""

from __future__ import annotations

import numpy as np

from esfex.visualization.workflows.otec_studio.economics import (
    _scalar,
    run_on_design,
)
from esfex.visualization.workflows.otec_studio.project import StudioConfig


def seasonal_profile(
    mean: float, amplitude: float, n: int = 12, phase: float = 0.0,
) -> np.ndarray:
    """Synthetic seasonal temperature profile (sinusoid) over ``n`` steps."""
    t = np.arange(n)
    return mean + amplitude * np.sin(2 * np.pi * (t - phase) / max(n, 1))


def run_operation(
    config: StudioConfig,
    t_ww_design: float,
    t_cw_design: float,
    dist_shore: float,
    t_ww_profile,
    t_cw_profile,
) -> dict:
    """Nominal on-design plant + time-series operation over the profiles.

    Returns ``{result, plant, inputs}`` where ``result`` is the OTEX operation
    dict of ``(1, n)`` time-series.
    """
    from otex.plant.operation import otec_operation

    od = run_on_design(config, t_ww_design, t_cw_design, dist_shore)
    plant, inputs = od["plant"], od["inputs"]
    t_ww = np.asarray(t_ww_profile, dtype=float).reshape(1, -1)
    t_cw = np.asarray(t_cw_profile, dtype=float).reshape(1, -1)
    result = otec_operation(plant, t_ww, t_cw, inputs)
    return {"result": result, "plant": plant, "inputs": inputs}


def _series(result: dict, key: str) -> np.ndarray:
    return np.ravel(np.asarray(result[key], dtype=float))


def diagnose(result: dict, plant: dict) -> dict:
    """Capacity factor + attribution of net-power deficit to its drivers.

    Net output magnitude = gross magnitude − parasitic pumping. A deficit vs
    nameplate therefore splits into a *gross* component (smaller ΔT lift) and a
    *parasitic* component (more pumping), energy-weighted across the horizon.
    """
    p_net = _series(result, "p_net")
    p_gross = _series(result, "p_gross")
    p_par = _series(result, "p_pump_total")

    p_net_nom = _scalar(plant, "p_net_nom") or 0.0
    p_gross_nom = _scalar(plant, "p_gross_nom") or 0.0
    p_par_nom = _scalar(plant, "p_pump_total_nom") or 0.0

    cf = float(np.mean(np.abs(p_net)) / abs(p_net_nom)) if p_net_nom else 0.0
    gross_deficit = np.clip(abs(p_gross_nom) - np.abs(p_gross), 0, None)
    parasitic_excess = np.clip(p_par - p_par_nom, 0, None)
    gd = float(gross_deficit.sum())
    pe = float(parasitic_excess.sum())
    total = gd + pe

    return {
        "cf": cf,
        "p_net_mw": np.abs(p_net) / 1000.0,
        "p_gross_mw": np.abs(p_gross) / 1000.0,
        "parasitic_mw": p_par / 1000.0,
        "pump_ww_mw": _series(result, "p_pump_WW") / 1000.0,
        "pump_cw_mw": _series(result, "p_pump_CW") / 1000.0,
        "pump_nh3_mw": _series(result, "p_pump_NH3") / 1000.0,
        "t_evap": _series(result, "T_evap"),
        "t_cond": _series(result, "T_cond"),
        "eff_net": _series(result, "eff_net"),
        "lcoe": _series(result, "LCOE"),
        "p_net_min_mw": float(np.min(np.abs(p_net))) / 1000.0,
        "p_net_max_mw": float(np.max(np.abs(p_net))) / 1000.0,
        "loss_gross_frac": gd / total if total else 0.0,
        "loss_parasitic_frac": pe / total if total else 0.0,
        "dominant": "ΔT / gross power" if gd >= pe else "parasitic (pumping)",
    }
