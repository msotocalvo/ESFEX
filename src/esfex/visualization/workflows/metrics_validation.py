"""Validation metrics and post-hoc demand correction.

Lets a user *validate* a forecast against their own observed time series and,
optionally, bend the forecast toward the observation without retraining the ML
model (engine-agnostic — it operates only on the output series).

The correction is a multiplicative bias/shape factor per (month-of-year ×
hour-of-day) derived from the overlapping *base year*. Because the same factor
is applied to every simulation year, the year-over-year growth trajectory (the
SSP path baked into ``demand_multi_year``) is preserved exactly.

All functions are pure (no Qt, no I/O) and unit-testable.
"""

from __future__ import annotations

import numpy as np

# Reuse the same clip bounds the climate refinement uses
# (DemandEstimationConfig.monthly_climate_clip_min/max) so a single observed
# year can't blow the forecast up or zero it out.
FACTOR_CLIP_MIN = 0.5
FACTOR_CLIP_MAX = 2.0


def _mh_offsets(base_year: int, n_per_year: int,
                resolution_hours: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Return 0-based (month, hour) index arrays of length ``n_per_year``.

    Built from a calendar starting at ``base_year-01-01`` at the given temporal
    resolution, mirroring the index the demand visualizer uses.
    """
    import pandas as pd

    freq = f"{int(round(resolution_hours * 60))}min"
    idx = pd.date_range(f"{base_year}-01-01", periods=n_per_year, freq=freq)
    return idx.month.to_numpy() - 1, idx.hour.to_numpy()


def forecast_metrics(observed, forecast) -> dict:
    """Error metrics comparing an observed series to a forecast series.

    Both are 1-D, aligned from the start, truncated to the shorter length.
    Returns MAPE/RMSE/MAE plus peak, annual-energy and load-factor errors
    (forecast relative to observed, in %), and the Pearson correlation.
    """
    o = np.asarray(observed, dtype=np.float64).ravel()
    f = np.asarray(forecast, dtype=np.float64).ravel()
    n = min(o.size, f.size)
    if n == 0:
        return {}
    o, f = o[:n], f[:n]

    err = f - o
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    nz = o != 0
    mape = float(np.mean(np.abs(err[nz] / o[nz])) * 100.0) if nz.any() else float("nan")

    o_peak, f_peak = float(o.max()), float(f.max())
    o_sum, f_sum = float(o.sum()), float(f.sum())
    peak_err = (f_peak - o_peak) / o_peak * 100.0 if o_peak else float("nan")
    energy_err = (f_sum - o_sum) / o_sum * 100.0 if o_sum else float("nan")

    o_lf = (o.mean() / o_peak) if o_peak else float("nan")
    f_lf = (f.mean() / f_peak) if f_peak else float("nan")
    lf_err = (f_lf - o_lf) / o_lf * 100.0 if o_lf else float("nan")

    if o.std() > 0 and f.std() > 0:
        corr = float(np.corrcoef(o, f)[0, 1])
    else:
        corr = float("nan")

    return {
        "mape": mape, "rmse": rmse, "mae": mae,
        "peak_err": peak_err, "energy_err": energy_err, "lf_err": lf_err,
        "corr": corr,
        "obs_peak": o_peak, "fc_peak": f_peak,
        "obs_gwh": o_sum / 1000.0, "fc_gwh": f_sum / 1000.0,
        "obs_lf": o_lf, "fc_lf": f_lf,
        "n": n,
    }


def month_hour_factors(observed, forecast_base_year, base_year: int,
                       resolution_hours: float = 1.0) -> np.ndarray:
    """Multiplicative correction factors, shape ``(12, 24)``.

    ``factor[m, h] = Σ observed / Σ forecast`` over all timesteps falling in
    month ``m`` (0-based) and hour ``h``, clipped to
    ``[FACTOR_CLIP_MIN, FACTOR_CLIP_MAX]``. Empty/zero bins → 1.0 (no change).
    """
    o = np.asarray(observed, dtype=np.float64).ravel()
    f = np.asarray(forecast_base_year, dtype=np.float64).ravel()
    n = min(o.size, f.size)
    o, f = o[:n], f[:n]

    months, hours = _mh_offsets(base_year, n, resolution_hours)
    factors = np.ones((12, 24), dtype=np.float64)
    obs_sum = np.zeros((12, 24), dtype=np.float64)
    fc_sum = np.zeros((12, 24), dtype=np.float64)
    np.add.at(obs_sum, (months, hours), o)
    np.add.at(fc_sum, (months, hours), f)
    nz = fc_sum > 0
    factors[nz] = obs_sum[nz] / fc_sum[nz]
    return np.clip(factors, FACTOR_CLIP_MIN, FACTOR_CLIP_MAX)


def apply_factors(column, factors: np.ndarray, base_year: int,
                  n_per_year: int, resolution_hours: float = 1.0) -> np.ndarray:
    """Apply ``(12, 24)`` factors to a full multi-year demand column.

    Row ``r`` is multiplied by ``factors[month, hour]`` of its within-year
    offset ``r % n_per_year``. Identity factors leave the column untouched.
    The same factor across every year preserves the growth trajectory.
    """
    col = np.asarray(column, dtype=np.float64).ravel()
    months, hours = _mh_offsets(base_year, n_per_year, resolution_hours)
    per_offset = factors[months, hours]                # length n_per_year
    offs = np.arange(col.size) % n_per_year
    return col * per_offset[offs]
