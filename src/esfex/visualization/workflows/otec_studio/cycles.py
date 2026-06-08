# -*- coding: utf-8 -*-
"""OTEC Studio — thermodynamic cycle engine (M2).

GUI-independent wrappers over ``otex.core`` for the Cycle & Design panel: build
any of the five cycles, compute their thermodynamic states, and produce the
data for live T-s / P-h diagrams.

Honest about the API surface: ``ammonia_concentration`` is a real Kalina/Uehara
constructor knob and IS exposed; the internal ``split_ratio`` / hybrid
``power_split`` are NOT public constructor/method arguments, so they are not
surfaced as controls. The dome + closed-loop diagram is built for the
closed Rankine state structure; every cycle gets a numeric state table.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from esfex.visualization.workflows.otec_studio.optimize import (
    build_inputs_template,
)
from esfex.visualization.workflows.otec_studio.project import StudioConfig

MIXTURE_CYCLES = ("kalina", "uehara")
# Cycles whose state structure supports the 4-point dome+loop diagram.
LOOP_CYCLES = ("rankine_closed",)


def _to_scalar(v: Any) -> Any:
    """Coerce a scalar-like thermodynamic value to a plain Python float.

    ``otex``/CoolProp may return a state value as a NumPy scalar or a 0-d /
    single-element array depending on the installed NumPy and CoolProp
    versions. Downstream code then either builds a *ragged* ``np.array`` (when
    only some states are arrays) or fails ``isinstance(x, float)`` checks. We
    normalise every scalar-like value to a Python ``float`` so behaviour is
    version-independent; non-numeric values (e.g. labels) pass through.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    try:
        arr = np.asarray(v)
    except (TypeError, ValueError):
        return v
    if arr.ndim == 0 or arr.size == 1:
        try:
            return float(arr.reshape(-1)[0])
        except (TypeError, ValueError):
            return v
    return v


def build_cycle(config: StudioConfig) -> tuple[Any, Any]:
    """Instantiate the configured cycle and its working fluid.

    Mixture cycles (Kalina/Uehara) build their own NH3-H2O mixture and take
    ``ammonia_concentration``; closed/hybrid take a working-fluid object.
    """
    from otex.core import get_thermodynamic_cycle, get_working_fluid

    fluid = get_working_fluid(config.fluid_type)
    kwargs = {}
    if config.cycle_type in MIXTURE_CYCLES:
        kwargs["ammonia_concentration"] = config.ammonia_concentration
    cycle = get_thermodynamic_cycle(config.cycle_type, fluid, **kwargs)
    return cycle, fluid


def compute_states(
    config: StudioConfig, t_evap: float, t_cond: float,
) -> dict:
    """Compute a cycle's thermodynamic states at an operating point.

    Returns ``{states, p_evap, p_cond, fluid, cycle, mass_flow}`` where
    ``mass_flow`` is a float (single-fluid cycles) or dict (mixture cycles).
    """
    cycle, fluid = build_cycle(config)
    p_evap = float(fluid.saturation_pressure(t_evap))
    p_cond = float(fluid.saturation_pressure(t_cond))
    inputs = build_inputs_template(config)
    states = cycle.calculate_cycle_states(t_evap, t_cond, p_evap, p_cond, inputs)
    # Normalise scalar state values to plain floats (see _to_scalar): keeps the
    # T-s / P-h loop arrays homogeneous and ``mass_flow`` a real float across
    # NumPy/CoolProp versions.
    if isinstance(states, dict):
        states = {k: _to_scalar(v) for k, v in states.items()}
    try:
        mass_flow = cycle.calculate_mass_flow(config.gross_power, states)
        if not isinstance(mass_flow, dict):
            mass_flow = _to_scalar(mass_flow)
    except Exception:
        mass_flow = None
    return {
        "states": states, "p_evap": p_evap, "p_cond": p_cond,
        "fluid": fluid, "cycle": cycle, "mass_flow": mass_flow,
    }


def saturation_dome(
    fluid: Any, t_min: float, t_max: float, n: int = 60,
) -> dict:
    """Two-phase envelope for the diagrams.

    Returns saturated-liquid/vapor entropy (T-s) and enthalpy (P-h) over a
    temperature range, plus the saturation pressure at each T.
    """
    temps = np.linspace(t_min, t_max, n)
    s_liq, s_vap, h_liq, h_vap, pres = [], [], [], [], []
    for t in temps:
        s_liq.append(float(fluid.entropy_liquid(t)))
        s_vap.append(float(fluid.entropy_vapor(t)))
        h_liq.append(float(fluid.enthalpy_liquid(t)))
        h_vap.append(float(fluid.enthalpy_vapor(t)))
        pres.append(float(fluid.saturation_pressure(t)))
    return {
        "T": temps,
        "s_liq": np.array(s_liq), "s_vap": np.array(s_vap),
        "h_liq": np.array(h_liq), "h_vap": np.array(h_vap),
        "p": np.array(pres),
    }


def closed_loop_ts(
    states: dict, t_evap: float, t_cond: float, fluid: Any, n_heat: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """Closed-Rankine cycle path in (entropy, temperature) coordinates.

    1→2 pump (≈T_cond), 2→3 liquid heating along the saturated-liquid line to
    T_evap then evaporation, 3→4 turbine expansion to T_cond, 4→1 condensation.
    """
    s1, s2 = _to_scalar(states["s_1"]), _to_scalar(states["s_2"])
    s3, s4 = _to_scalar(states["s_3"]), _to_scalar(states["s_4"])
    heat_T = np.linspace(t_cond, t_evap, n_heat)
    heat_s = [float(fluid.entropy_liquid(t)) for t in heat_T]
    s_pts = [s1, s2, *heat_s, s3, s4, s1]
    T_pts = [t_cond, t_cond, *heat_T.tolist(), t_evap, t_cond, t_cond]
    return np.array(s_pts), np.array(T_pts)


def closed_loop_ph(
    states: dict, p_evap: float, p_cond: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Closed-Rankine cycle path in (enthalpy, pressure) coordinates."""
    h = [_to_scalar(states[k]) for k in ("h_1", "h_2", "h_3", "h_4")]
    h_pts = [h[0], h[1], h[2], h[3], h[0]]
    p_pts = [p_cond, p_evap, p_evap, p_cond, p_cond]
    return np.array(h_pts), np.array(p_pts)


def format_states(states: dict) -> list[tuple[str, str]]:
    """Flatten a cycle's state dict into ordered (key, formatted-value) rows."""
    rows = []
    for k, v in states.items():
        if isinstance(v, (int, float, np.floating)):
            rows.append((k, f"{float(v):.4g}"))
        else:
            rows.append((k, str(v)))
    return rows
