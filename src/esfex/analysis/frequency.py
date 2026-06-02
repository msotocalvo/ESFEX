"""Simplified frequency response analysis using center-of-inertia model.

This module computes post-contingency frequency metrics (ROCOF, nadir,
steady-state frequency) from the algebraic swing equation — no time-domain
simulation is required.  All physical parameters (inertia constants,
rated powers, droop characteristics) are read from the HDF5 results file
and optional configuration.

Mathematical basis
------------------
The center-of-inertia (COI) model aggregates all synchronous machines
into a single equivalent machine:

    2 H_sys  df/dt  =  ΔP  -  D_total × Δf

where:

- ``H_sys = Σ(H_i × P_i)`` — aggregate system inertia (MW·s)
- ``ΔP`` — power imbalance from contingency (MW)
- ``D_total`` — aggregate damping + primary frequency response (MW/Hz)
- ``Δf = f - f_nom`` — frequency deviation (Hz)

Key metrics derived from this model:

1. **ROCOF** = ΔP / (2 × H_sys)  (Hz/s)
2. **Nadir** = f_nom - ΔP / (2 × √(H_sys × D_total))  (Hz)
3. **Steady-state** = f_nom - ΔP / D_total  (Hz)
4. **Time to nadir** = π × √(H_sys / D_total)  (s)

References
----------
- Kundur, P. (1994). *Power System Stability and Control*. McGraw-Hill.
- ENTSO-E (2017). *Frequency Stability Evaluation Criteria*.
- Ela, E. et al. (2012). *Effective Inertia Constant*, NREL/TP-5500-55503.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ── Default physical parameters ──
_DEFAULT_DROOP = 0.05        # 5% droop (pu)
_DEFAULT_GOV_TIME = 5.0      # Governor time constant (s)
_DEFAULT_LOAD_DAMPING = 0.01  # 1% load damping (pu)
_DEFAULT_F_NOM = 50.0        # Nominal frequency (Hz)
_DEFAULT_ROCOF_LIMIT = 2.0   # Max allowable ROCOF (Hz/s)
_DEFAULT_NADIR_LIMIT = 49.0  # Min allowable frequency (Hz)


@dataclass
class GeneratorFreqParams:
    """Frequency-relevant parameters for a single generator."""

    element_id: str
    rated_power_mw: float
    inertia_h: float          # Inertia constant H (seconds)
    droop: float              # Governor droop R (pu), e.g. 0.05
    governor_time_const: float  # Governor time constant T_g (seconds)
    is_renewable: bool = False  # Renewables typically don't provide inertia


@dataclass
class FrequencyResponse:
    """Result of frequency stability analysis for a contingency event.

    All frequency values are in Hz.  A positive ``delta_p_mw`` means
    generation was *lost* (under-frequency event).
    """

    delta_p_mw: float          # Power imbalance (MW, positive = gen loss)
    h_total_mws: float         # Aggregate system inertia (MW·s)
    rocof_hz_per_s: float      # Initial Rate of Change of Frequency (Hz/s)
    nadir_hz: float            # Frequency nadir — lowest point (Hz)
    steady_state_hz: float     # Post-primary-response steady-state (Hz)
    t_nadir_s: float           # Time to reach nadir (seconds)
    d_total_mw_per_hz: float   # Aggregate system damping (MW/Hz)
    is_stable: bool            # True if nadir >= nadir limit
    rocof_ok: bool             # True if |ROCOF| <= ROCOF limit


class FrequencyAnalyzer:
    """Simplified frequency response model using center-of-inertia.

    Computes ROCOF, frequency nadir, and steady-state frequency
    after a generation loss event using algebraic equations — no
    differential equation solving is required.

    Parameters
    ----------
    gen_params : list[GeneratorFreqParams]
        Frequency-relevant parameters per generator.
    load_damping : float
        Load damping coefficient D_load (pu).  Fraction of total load
        that self-regulates per unit frequency deviation.
    f_nom : float
        Nominal system frequency (Hz).
    rocof_limit : float
        Maximum allowable ROCOF before protection trips (Hz/s).
    nadir_limit : float
        Minimum allowable frequency before UFLS acts (Hz).
    """

    def __init__(
        self,
        gen_params: list[GeneratorFreqParams],
        load_damping: float = _DEFAULT_LOAD_DAMPING,
        f_nom: float = _DEFAULT_F_NOM,
        rocof_limit: float = _DEFAULT_ROCOF_LIMIT,
        nadir_limit: float = _DEFAULT_NADIR_LIMIT,
    ) -> None:
        self.gen_params = list(gen_params)
        self.load_damping = load_damping
        self.f_nom = f_nom
        self.rocof_limit = rocof_limit
        self.nadir_limit = nadir_limit

    # ── Main API ──

    def analyze(
        self,
        snapshot: dict[str, Any],
        delta_p_mw: float,
    ) -> FrequencyResponse:
        """Compute frequency response for a given power imbalance.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
            Used to determine which generators are online and their output.
        delta_p_mw : float
            Power loss in MW (positive = generation lost).

        Returns
        -------
        FrequencyResponse
            Computed frequency metrics.
        """
        if delta_p_mw <= 0:
            return FrequencyResponse(
                delta_p_mw=0.0,
                h_total_mws=self._compute_h_total(snapshot),
                rocof_hz_per_s=0.0,
                nadir_hz=self.f_nom,
                steady_state_hz=self.f_nom,
                t_nadir_s=0.0,
                d_total_mw_per_hz=0.0,
                is_stable=True,
                rocof_ok=True,
            )

        gens_data = snapshot.get("generators", {})
        total_demand = sum(
            v.get("demand_mw", 0) for v in snapshot.get("loads", {}).values()
        )

        # ── Aggregate inertia H_sys (MW·s) ──
        h_total = self._compute_h_total(snapshot)

        # ── Aggregate damping D_total (MW/Hz) ──
        # D_total = D_load × P_demand / f_nom  +  Σ(P_rated_i / (R_i × f_nom))
        d_load = self.load_damping * total_demand / self.f_nom if self.f_nom > 0 else 0
        d_droop = 0.0
        for gp in self.gen_params:
            gdata = gens_data.get(gp.element_id, {})
            is_online = gdata.get("status", 1) > 0
            if not is_online or gp.droop <= 0 or gp.is_renewable:
                continue
            d_droop += gp.rated_power_mw / (gp.droop * self.f_nom)

        d_total = d_load + d_droop

        # ── ROCOF = ΔP × f_nom / (2 × H_sys) ──
        if h_total > 0:
            rocof = delta_p_mw * self.f_nom / (2.0 * h_total)
        else:
            rocof = float("inf")

        # ── Frequency nadir ──
        if h_total > 0 and d_total > 0:
            # Analytical nadir from linearized swing equation:
            # Δf_nadir = ΔP / (2 × √(H_sys × D_total))
            nadir_deviation = delta_p_mw / (2.0 * math.sqrt(h_total * d_total))
            nadir = self.f_nom - nadir_deviation
        elif d_total > 0:
            # No inertia — frequency drops to steady-state instantly
            nadir = self.f_nom - delta_p_mw / d_total
        else:
            nadir = 0.0  # No damping, no inertia — frequency collapse

        # ── Time to nadir = π × √(H_sys / D_total) ──
        if h_total > 0 and d_total > 0:
            t_nadir = math.pi * math.sqrt(h_total / d_total)
        else:
            t_nadir = 0.0

        # ── Steady-state frequency after primary response ──
        if d_total > 0:
            ss_deviation = delta_p_mw / d_total
            steady_state = self.f_nom - ss_deviation
        else:
            steady_state = 0.0

        return FrequencyResponse(
            delta_p_mw=delta_p_mw,
            h_total_mws=h_total,
            rocof_hz_per_s=rocof,
            nadir_hz=nadir,
            steady_state_hz=steady_state,
            t_nadir_s=t_nadir,
            d_total_mw_per_hz=d_total,
            is_stable=nadir >= self.nadir_limit,
            rocof_ok=abs(rocof) <= self.rocof_limit,
        )

    def analyze_all_n1(
        self, snapshot: dict[str, Any],
    ) -> list[tuple[str, FrequencyResponse]]:
        """Run N-1 frequency analysis for loss of each online generator.

        Returns a list of ``(gen_element_id, FrequencyResponse)`` tuples
        sorted by severity (lowest nadir first).
        """
        gens_data = snapshot.get("generators", {})
        results: list[tuple[str, FrequencyResponse]] = []

        for gp in self.gen_params:
            gdata = gens_data.get(gp.element_id, {})
            is_online = gdata.get("status", 1) > 0
            output_mw = gdata.get("output_mw", 0)
            if not is_online or output_mw < 0.1:
                continue
            resp = self.analyze(snapshot, output_mw)
            results.append((gp.element_id, resp))

        results.sort(key=lambda x: x[1].nadir_hz)
        return results

    # ── Private helpers ──

    def _compute_h_total(self, snapshot: dict[str, Any]) -> float:
        """Compute aggregate system inertia H_sys = Σ(H_i × P_i) for online gens."""
        gens_data = snapshot.get("generators", {})
        h_total = 0.0
        for gp in self.gen_params:
            gdata = gens_data.get(gp.element_id, {})
            is_online = gdata.get("status", 1) > 0
            output_mw = gdata.get("output_mw", 0)
            if is_online and output_mw > 0 and gp.inertia_h > 0:
                h_total += gp.inertia_h * output_mw
        return h_total


def build_gen_freq_params_from_hdf5(
    h5_path: str | Path,
    gen_map: dict[str, tuple[int, int]],
) -> list[GeneratorFreqParams]:
    """Build generator frequency parameters from HDF5 system configuration.

    Parameters
    ----------
    h5_path : str | Path
        Path to HDF5 results file.
    gen_map : dict[str, tuple[int, int]]
        Mapping from GUI element_id to (gen_index, node_index).

    Returns
    -------
    list[GeneratorFreqParams]
    """
    import h5py

    params: list[GeneratorFreqParams] = []
    h5_path = Path(h5_path)

    with h5py.File(h5_path, "r") as f:
        sysconf = f.get("system_configuration", {})
        gen_conf = sysconf.get("generators", {})

        # Build gen_index → config mapping
        gen_keys = sorted(
            [k for k in gen_conf.keys() if k.startswith("generator_")],
            key=lambda x: int(x.split("_")[1]),
        )

        for elem_id, (g_idx, n_idx) in gen_map.items():
            if g_idx >= len(gen_keys):
                continue
            gk = gen_keys[g_idx]
            g = gen_conf[gk]

            attrs = dict(g.attrs)

            def _decode(val):
                return val.decode() if isinstance(val, bytes) else val

            rated_arr = attrs.get("rated_power", [0.0])
            if hasattr(rated_arr, "__iter__"):
                rated = float(rated_arr[n_idx]) if n_idx < len(rated_arr) else 0.0
            else:
                rated = float(rated_arr)

            inertia_arr = attrs.get("inertia", [0.0])
            if hasattr(inertia_arr, "__iter__"):
                inertia = float(inertia_arr[n_idx]) if n_idx < len(inertia_arr) else 0.0
            else:
                inertia = float(inertia_arr)

            droop_arr = attrs.get("droop", [_DEFAULT_DROOP])
            if hasattr(droop_arr, "__iter__"):
                droop = float(droop_arr[n_idx]) if n_idx < len(droop_arr) else _DEFAULT_DROOP
            else:
                droop = float(droop_arr)

            gov_arr = attrs.get("governor_time_const", [_DEFAULT_GOV_TIME])
            if hasattr(gov_arr, "__iter__"):
                gov_tc = float(gov_arr[n_idx]) if n_idx < len(gov_arr) else _DEFAULT_GOV_TIME
            else:
                gov_tc = float(gov_arr)

            gen_type = _decode(attrs.get("type", ""))
            is_re = "renewable" in gen_type.lower() if gen_type else False

            params.append(GeneratorFreqParams(
                element_id=elem_id,
                rated_power_mw=rated,
                inertia_h=inertia,
                droop=droop,
                governor_time_const=gov_tc,
                is_renewable=is_re,
            ))

    return params


def build_gen_freq_params_from_state(state) -> list[GeneratorFreqParams]:
    """Build generator frequency parameters directly from GuiSystemState.

    This enables frequency analysis without HDF5 results — the editor's
    live generator data is used instead.

    Parameters
    ----------
    state : GuiSystemState
        The current editor state with generator instances.

    Returns
    -------
    list[GeneratorFreqParams]
    """
    params: list[GeneratorFreqParams] = []
    for gen_id, gen in state.generators.items():
        is_re = gen.gen_type.lower() == "renewable"
        params.append(GeneratorFreqParams(
            element_id=gen_id,
            rated_power_mw=gen.rated_power,
            inertia_h=gen.inertia,
            droop=getattr(gen, "droop", _DEFAULT_DROOP),
            governor_time_const=getattr(gen, "governor_time_const", _DEFAULT_GOV_TIME),
            is_renewable=is_re,
        ))
    return params
