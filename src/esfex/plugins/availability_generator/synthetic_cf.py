"""Synthetic capacity-factor profiles for non-weather generators.

Targets generator types whose availability is not driven by site-specific
meteorology (thermal, geothermal, biomass, hydro fallback).  Profiles are
parametric — calibrated to industry-standard yearly capacity factors with
a planned-maintenance window for thermal units and an analytical seasonal
pattern for hydro.

All functions return a 1-D numpy array of length ``hours`` (default 8760).
Values are bounded in ``[0, 1]`` so the array can be used directly by the
solver as a per-hour derating factor.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

_HOURS_PER_YEAR = 8760


# ── Reference annual availability + planned outage windows ──────────
# Sources: NREL ATB 2024, IEA WEO 2023, EIA EIB-411 forced/planned
# outage statistics. ``annual_cf`` is the long-run mean availability;
# ``maint_weeks`` is the typical planned-outage window per year.
_THERMAL_DEFAULTS: dict[str, dict] = {
    # canonical fuel key (matching grid_mapping_builder._FUEL_ALIASES)
    "naturalgas":  {"annual_cf": 0.92, "maint_weeks": 3},
    "coal":        {"annual_cf": 0.85, "maint_weeks": 4},
    "diesel":      {"annual_cf": 0.90, "maint_weeks": 2},
    "fuel_oil":    {"annual_cf": 0.85, "maint_weeks": 4},
    "nuclear":     {"annual_cf": 0.93, "maint_weeks": 4},
    "geothermal":  {"annual_cf": 0.95, "maint_weeks": 2},
    "biomass":     {"annual_cf": 0.85, "maint_weeks": 3},
    "biogas":      {"annual_cf": 0.85, "maint_weeks": 3},
    "waste":       {"annual_cf": 0.83, "maint_weeks": 3},
}


def _maintenance_window(
    hours: int,
    weeks_offline: float,
    seed: int = 0,
) -> tuple[int, int]:
    """Pick a planned-outage window (start_hour, end_hour).

    Uses ``seed`` to deterministically distribute outages across the
    year so different units in the same fleet don't all stop at once.
    Outages cluster in the historic shoulder seasons (April-May,
    October-November) when both demand and renewable output are
    moderate.
    """
    if weeks_offline <= 0:
        return (0, 0)
    out_hours = int(round(weeks_offline * 7 * 24))
    out_hours = min(out_hours, hours - 1)
    # Deterministic offset across two preferred shoulder windows
    spring_start = int(0.25 * hours)   # ~April 1
    fall_start = int(0.75 * hours)     # ~October 1
    use_fall = (seed % 2) == 1
    base_start = fall_start if use_fall else spring_start
    # Spread within the shoulder window (~6 weeks wide)
    jitter_range = 6 * 7 * 24
    jitter = (seed * 31) % jitter_range
    start = (base_start + jitter) % (hours - out_hours)
    return (start, start + out_hours)


def compute_thermal_cf(
    canonical_fuel: str,
    hours: int = _HOURS_PER_YEAR,
    seed: int = 0,
    annual_cf: float | None = None,
    maint_weeks: float | None = None,
) -> np.ndarray:
    """Capacity-factor profile for a thermal / firm-baseload unit.

    Constant baseline at the unit's annual availability, with a single
    planned-outage window (zero output) sized so the year-mean equals
    the requested ``annual_cf`` exactly.  ``seed`` distributes outage
    timing across units so a fleet doesn't go offline together.
    """
    defaults = _THERMAL_DEFAULTS.get(
        canonical_fuel,
        {"annual_cf": 0.85, "maint_weeks": 3},
    )
    af = annual_cf if annual_cf is not None else defaults["annual_cf"]
    mw = maint_weeks if maint_weeks is not None else defaults["maint_weeks"]
    af = max(0.0, min(1.0, af))
    cf = np.ones(hours, dtype=float)
    if mw > 0:
        start, end = _maintenance_window(hours, mw, seed=seed)
        cf[start:end] = 0.0
    # Calibrate baseline so the year-mean matches requested annual_cf
    current_mean = cf.mean()
    if current_mean > 0 and current_mean != af:
        scale = af / current_mean
        if scale <= 1.0:
            cf *= scale
        # If we'd need to scale > 1 (i.e. requested CF higher than the
        # implicit max from the outage window), clip to 1 instead.
    return np.clip(cf, 0.0, 1.0)


def compute_hydro_cf(
    lat: float,
    hours: int = _HOURS_PER_YEAR,
    annual_cf: float = 0.45,
) -> np.ndarray:
    """Seasonal hydrograph for run-of-river hydro.

    Proxy: northern hemisphere peaks in late spring (snowmelt + spring
    rain), troughs in late summer/early autumn.  Southern hemisphere
    is phase-flipped.  Tropical sites (|lat| < 10°) use a softer
    bimodal pattern (wet/dry seasons).  Final profile is rescaled so
    its annual mean equals ``annual_cf``.
    """
    t = np.arange(hours, dtype=float) / hours  # 0..1 over the year
    if abs(lat) < 10:
        # Two wet seasons (April-May, October-November)
        shape = (
            0.5 + 0.4 * np.sin(2 * math.pi * (t - 0.30))
            + 0.3 * np.sin(4 * math.pi * (t - 0.05))
        )
    else:
        # Single peak in late spring; sign of latitude flips season
        peak_t = 0.40 if lat >= 0 else 0.90
        shape = 0.6 + 0.5 * np.cos(2 * math.pi * (t - peak_t))
    shape = np.clip(shape, 0.05, 1.5)
    # Rescale to match requested annual mean
    if shape.mean() > 0:
        shape *= annual_cf / shape.mean()
    return np.clip(shape, 0.0, 1.0)


def compute_constant_cf(
    annual_cf: float,
    hours: int = _HOURS_PER_YEAR,
) -> np.ndarray:
    """Flat profile (used as ultimate fallback)."""
    return np.full(hours, max(0.0, min(1.0, annual_cf)), dtype=float)


# ── Top-level dispatcher ────────────────────────────────────────────


# Map canonical fuel keys (from grid_mapping_builder._FUEL_ALIASES) to
# the synthetic-profile family they belong to.
SYNTHETIC_FUELS = frozenset({
    "naturalgas", "coal", "diesel", "fuel_oil", "nuclear",
    "geothermal", "biomass", "biogas", "waste",
    "water",  # hydro
    "other",  # last-resort
})


def is_synthetic_fuel(canonical_fuel: str) -> bool:
    return canonical_fuel in SYNTHETIC_FUELS


def compute_synthetic_cf(
    canonical_fuel: str,
    lat: float = 0.0,
    hours: int = _HOURS_PER_YEAR,
    seed: int = 0,
) -> np.ndarray:
    """Dispatch to the right synthetic profile by fuel type."""
    f = (canonical_fuel or "").lower()
    if f == "water":
        return compute_hydro_cf(lat, hours=hours)
    if f in _THERMAL_DEFAULTS:
        return compute_thermal_cf(f, hours=hours, seed=seed)
    if f == "other":
        return compute_constant_cf(0.85, hours=hours)
    # Unknown: conservative flat 0.9
    return compute_constant_cf(0.90, hours=hours)
