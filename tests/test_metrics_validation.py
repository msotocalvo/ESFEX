"""Unit tests for validation metrics and post-hoc demand correction."""

import numpy as np
import pytest

from esfex.visualization.workflows.metrics_validation import (
    FACTOR_CLIP_MAX,
    apply_factors,
    forecast_metrics,
    month_hour_factors,
)

H = 8760  # hours per year
BASE_YEAR = 2025


def _hours_index(base_year=BASE_YEAR, n=H):
    import pandas as pd
    idx = pd.date_range(f"{base_year}-01-01", periods=n, freq="60min")
    return idx.month.to_numpy() - 1, idx.hour.to_numpy()


def test_forecast_metrics_hand_values():
    o = np.array([100.0, 200.0, 300.0, 400.0])
    f = np.array([110.0, 190.0, 330.0, 360.0])
    m = forecast_metrics(o, f)
    assert m["mae"] == pytest.approx(np.mean([10, 10, 30, 40]))
    assert m["rmse"] == pytest.approx(np.sqrt(np.mean([100, 100, 900, 1600])))
    assert m["peak_err"] == pytest.approx((360 - 400) / 400 * 100)
    assert m["energy_err"] == pytest.approx((990 - 1000) / 1000 * 100)
    assert -100 <= m["corr"] <= 100


def test_metrics_perfect_match():
    o = np.linspace(50, 150, 500)
    m = forecast_metrics(o, o.copy())
    assert m["mae"] == pytest.approx(0.0)
    assert m["rmse"] == pytest.approx(0.0)
    assert m["mape"] == pytest.approx(0.0)
    assert m["corr"] == pytest.approx(1.0)


def test_july_afternoon_bump_factor():
    # Flat forecast of 100 MW all year.
    f = np.full(H, 100.0)
    months, hours = _hours_index()
    # Observed = forecast except July (month index 6), hour 14, which is 1.5x.
    o = f.copy()
    bump = (months == 6) & (hours == 14)
    o[bump] = 150.0

    factors = month_hour_factors(o, f, BASE_YEAR)
    assert factors.shape == (12, 24)
    assert factors[6, 14] == pytest.approx(1.5, rel=1e-6)
    # All other bins unchanged.
    others = np.ones((12, 24), dtype=bool)
    others[6, 14] = False
    assert np.allclose(factors[others], 1.0)


def test_factor_clip():
    f = np.full(H, 100.0)
    o = np.full(H, 1000.0)  # 10x → must clip to FACTOR_CLIP_MAX
    factors = month_hour_factors(o, f, BASE_YEAR)
    assert np.all(factors <= FACTOR_CLIP_MAX + 1e-9)
    assert factors.max() == pytest.approx(FACTOR_CLIP_MAX)


def test_apply_preserves_growth_trajectory():
    # 3-year column with a clean per-year growth factor of 1.05.
    base = np.random.default_rng(0).uniform(50, 150, H)
    col = np.concatenate([base, base * 1.05, base * 1.05 ** 2])

    months, hours = _hours_index()
    o = base.copy()
    bump = (months == 6) & (hours == 14)
    o[bump] = base[bump] * 1.3
    factors = month_hour_factors(o, base, BASE_YEAR)

    corrected = apply_factors(col, factors, BASE_YEAR, H)
    y0 = corrected[:H]
    y1 = corrected[H:2 * H]
    y2 = corrected[2 * H:]
    # Year-over-year ratio preserved everywhere (growth untouched).
    assert np.allclose(y1, y0 * 1.05, rtol=1e-9)
    assert np.allclose(y2, y0 * 1.05 ** 2, rtol=1e-9)


def test_apply_matches_observed_bin_energy():
    # After correction, the base-year energy in each (m,h) bin equals observed.
    f = np.full(H, 100.0)
    months, hours = _hours_index()
    o = f.copy()
    o[(months == 2) & (hours == 8)] = 130.0
    o[(months == 11) & (hours == 20)] = 70.0
    factors = month_hour_factors(o, f, BASE_YEAR)
    corrected = apply_factors(f, factors, BASE_YEAR, H)

    obs_bin = np.zeros((12, 24))
    cor_bin = np.zeros((12, 24))
    np.add.at(obs_bin, (months, hours), o)
    np.add.at(cor_bin, (months, hours), corrected)
    assert np.allclose(obs_bin, cor_bin, rtol=1e-9)
