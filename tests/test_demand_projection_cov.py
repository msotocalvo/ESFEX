"""Coverage tests for esfex.models.demand_projection.

These tests exercise the pure helper functions, the CMIP6/ERA5 network
fetchers (with `requests` stubbed via sys.modules), and the
GlobalDemandProjector temperature loading / projection / run pipeline
(with a fake ML model and the legacy XGBoost feature builder patched).

No live network, Julia, or large data files are required.
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

# pandas + pyarrow are needed for the `run` pipeline; skip those tests if absent.
pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

import esfex.models.demand_projection as dp
import esfex.models.demand_ml as demand_ml


# ── fixtures / helpers ──────────────────────────────────────────────────


def _make_requests(json_payload, raise_exc=None):
    """Build a fake `requests` module whose .get(...).json() returns payload."""
    mod = types.ModuleType("requests")

    def get(url, timeout=None):  # noqa: ARG001
        resp = mock.MagicMock()
        if raise_exc is not None:
            resp.raise_for_status.side_effect = raise_exc
        else:
            resp.raise_for_status.return_value = None
        resp.json.return_value = json_payload
        return resp

    mod.get = get
    return mod


def _country_record(**overrides):
    base = dict(
        latitude=1.0,
        longitude=2.0,
        iso3="AAA",
        gdp_per_capita=20000.0,
        population=1_000_000.0,
        urbanization_pct=60.0,
        electricity_access_pct=99.0,
        annual_gwh=100.0,
        pop_projections={},
        data_quality="high",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _FakeModel:
    """Stand-in for the XGBoost model: predict_raw returns a flat shape."""

    def __init__(self, value=2.0):
        self.value = value

    def predict_raw(self, features):  # noqa: ARG002
        return np.ones(8760) * self.value


@pytest.fixture
def patch_build_features():
    """Replace build_hourly_features so project_country's legacy path runs."""
    orig = demand_ml.build_hourly_features
    demand_ml.build_hourly_features = lambda **kw: np.zeros((8760, 1))
    try:
        yield
    finally:
        demand_ml.build_hourly_features = orig


# ── SSP / interpolation helpers ─────────────────────────────────────────


def test_interpolate_5yr_to_annual_linear():
    r = dp._interpolate_5yr_to_annual({2020: 1.0, 2025: 2.0})
    assert r[2020] == 1.0
    assert r[2025] == 2.0
    # midpoint year 2022 is two fifths of the way: 1.0 + 0.4*(1.0)
    assert r[2022] == pytest.approx(1.4)
    # endpoints inclusive, contiguous range
    assert set(r.keys()) == set(range(2020, 2026))


def test_levels_to_growth_rates():
    rates = dp._levels_to_growth_rates({1: 1.0, 2: 1.1, 3: 1.21})
    assert rates[2] == pytest.approx(0.1)
    assert rates[3] == pytest.approx(0.1)
    # first year has no predecessor → not present
    assert 1 not in rates


def test_ssp_growth_rates_gdp_and_pop():
    gdp = dp._ssp_growth_rates("SSP2", "gdp")
    pop = dp._ssp_growth_rates("SSP2", "pop")
    # base year (2025) has no predecessor in the multiplier table → starts 2026
    assert min(gdp) == 2026
    assert max(gdp) == 2050
    assert all(isinstance(v, float) for v in gdp.values())
    # pop rates use the population multiplier table (different from gdp)
    assert pop[2026] != gdp[2026]


def test_ssp_growth_rates_pop_kind_uses_pop_table():
    # Any kind that is not "gdp" selects the population multipliers.
    pop = dp._ssp_growth_rates("SSP1", "population")
    pop2 = dp._ssp_growth_rates("SSP1", "pop")
    assert pop == pop2


def test_ssp_growth_rates_unknown_raises():
    with pytest.raises(ValueError) as exc:
        dp._ssp_growth_rates("NOPE")
    assert "Unknown SSP" in str(exc.value)


# ── annual trajectory ───────────────────────────────────────────────────


def test_compute_annual_trajectory_basic():
    traj = dp.compute_annual_trajectory(
        100.0, 2025, 2030, {2026: 0.03, 2027: 0.03}, elasticity=0.8
    )
    assert set(traj.keys()) == set(range(2025, 2031))
    assert traj[2025] == 100.0
    # first-year factor: (1 + 0.03*0.8)*(1 - eff_r)*(1 + elec_r)
    # eff_r=0.005, elec_r=0.01 on the first step
    expected_2026 = 100.0 * (1 + 0.03 * 0.8) * (1 - 0.005) * (1 + 0.01)
    assert traj[2026] == pytest.approx(expected_2026)
    # all positive and increasing-ish
    assert all(v > 0 for v in traj.values())


def test_compute_annual_trajectory_default_gdp_growth():
    # Years missing from the dict default to 0.02 GDP growth.
    traj = dp.compute_annual_trajectory(50.0, 2025, 2027, {}, elasticity=1.0)
    assert traj[2027] > traj[2025] > 0


def test_compute_annual_trajectory_single_year():
    traj = dp.compute_annual_trajectory(10.0, 2025, 2025, {})
    assert traj == {2025: 10.0}


# ── gdp elasticity ──────────────────────────────────────────────────────


def test_gdp_elasticity_nonpositive_default():
    assert dp._gdp_elasticity(0) == 0.8
    assert dp._gdp_elasticity(-100) == 0.8


def test_gdp_elasticity_clamping_and_monotone():
    # Very low positive GDP/cap → clamped at upper bound 1.2
    assert dp._gdp_elasticity(1.0) == pytest.approx(1.2)
    # Higher GDP/cap → lower elasticity
    low = dp._gdp_elasticity(50000.0)
    high_income = dp._gdp_elasticity(1e9)
    assert 0.2 <= high_income <= 1.2
    assert high_income < low < 1.2


def test_gdp_elasticity_formula_midrange():
    import math
    g = 50000.0
    expected = max(0.2, min(1.2, 1.4 - 0.12 * math.log10(g)))
    assert dp._gdp_elasticity(g) == pytest.approx(expected)


# ── daily→hourly temperature ────────────────────────────────────────────


def test_daily_to_hourly_temperature_shape_and_peaks():
    tmin = np.array([10.0, 12.0])
    tmax = np.array([20.0, 22.0])
    h = dp._daily_to_hourly_temperature(tmin, tmax, 2)
    assert h.shape == (48,)
    # cos(0) at h=14 → equals tmax for day 0
    assert h[14] == pytest.approx(20.0)
    # day 1 peak at hour 24+14=38
    assert h[38] == pytest.approx(22.0)
    # all values within [tmin, tmax] for each day
    assert h[:24].min() >= 10.0 - 1e-9
    assert h[:24].max() <= 20.0 + 1e-9


# ── CMIP6 fetch ─────────────────────────────────────────────────────────


def test_fetch_cmip6_cache_hit():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        cf = cd / "cmip6_CMCC_CM2_VHR4_1.00_2.00_2025_2025.npz"
        np.savez_compressed(cf, **{"2025": np.full(8760, 7.0)})
        r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
    assert r is not None
    assert sorted(r.keys()) == [2025]
    assert r[2025][0] == 7.0


def test_fetch_cmip6_success_full_year():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        d0 = datetime.date(2025, 1, 1)
        times = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(365)]
        payload = {
            "daily": {
                "time": times,
                "temperature_2m_max": [25.0] * 365,
                "temperature_2m_min": [15.0] * 365,
            }
        }
        with mock.patch.dict(sys.modules, {"requests": _make_requests(payload)}):
            r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
        assert r is not None
        assert 2025 in r
        assert r[2025].shape == (8760,)
        # cache file written
        assert (cd / "cmip6_CMCC_CM2_VHR4_1.00_2.00_2025_2025.npz").exists()


def test_fetch_cmip6_handles_none_values():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        d0 = datetime.date(2025, 1, 1)
        times = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(365)]
        tmax = [25.0] * 365
        tmin = [15.0] * 365
        tmax[0] = None
        tmin[0] = None
        payload = {
            "daily": {
                "time": times,
                "temperature_2m_max": tmax,
                "temperature_2m_min": tmin,
            }
        }
        with mock.patch.dict(sys.modules, {"requests": _make_requests(payload)}):
            r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
        assert 2025 in r
        assert not np.isnan(r[2025]).any()


def test_fetch_cmip6_short_year_skipped():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        d0 = datetime.date(2025, 1, 1)
        times = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(100)]
        payload = {
            "daily": {
                "time": times,
                "temperature_2m_max": [25.0] * 100,
                "temperature_2m_min": [15.0] * 100,
            }
        }
        with mock.patch.dict(sys.modules, {"requests": _make_requests(payload)}):
            r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
        # year had < 360 days → skipped → empty result dict
        assert r == {}


def test_fetch_cmip6_empty_times_returns_none():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        payload = {"daily": {"time": []}}
        with mock.patch.dict(sys.modules, {"requests": _make_requests(payload)}):
            r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
        assert r is None


def test_fetch_cmip6_request_failure_returns_none():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        fake = _make_requests({}, raise_exc=RuntimeError("boom"))
        with mock.patch.dict(sys.modules, {"requests": fake}):
            r = dp.fetch_cmip6_temperature(1.0, 2.0, 2025, 2025, cd)
        assert r is None


# ── ERA5 fetch ──────────────────────────────────────────────────────────


def test_fetch_era5_cache_hit():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        arr = np.arange(8760, dtype=np.float64)
        np.save(cd / "era5_10.00_20.00_2023.npy", arr)
        r = dp._fetch_era5_temperature(10.0, 20.0, 2023, cd)
    assert r is not None
    assert r.shape == (8760,)
    assert r[5] == 5.0


def test_fetch_era5_success_pads_short_array():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        payload = {"hourly": {"temperature_2m": [5.0] * 8000}}
        with mock.patch.dict(sys.modules, {"requests": _make_requests(payload)}):
            r = dp._fetch_era5_temperature(1.0, 2.0, 2023, cd)
        assert r.shape == (8760,)
        # padded with edge value
        assert r[-1] == 5.0
        assert (cd / "era5_1.00_2.00_2023.npy").exists()


def test_fetch_era5_failure_returns_none():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        fake = _make_requests({}, raise_exc=RuntimeError("net down"))
        with mock.patch.dict(sys.modules, {"requests": fake}):
            r = dp._fetch_era5_temperature(1.0, 2.0, 2023, cd)
        assert r is None


# ── GlobalDemandProjector ───────────────────────────────────────────────


def test_projector_init_defaults():
    p = dp.GlobalDemandProjector(ssp="SSP2", base_year=2025, end_year=2030)
    assert p.n_years == 6
    assert p.cmip6_model == "CMCC_CM2_VHR4"
    assert p.ml_engine == "tft"
    assert p._model is None
    assert 2026 in p.gdp_growth
    assert 2026 in p.pop_growth


def test_get_temperature_fallback_20c():
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        p = dp.GlobalDemandProjector(base_year=2025, end_year=2027, data_dir=Path(td))
        t = p._get_temperature(rec)
    assert sorted(t.keys()) == [2025, 2026, 2027]
    assert all((v == 20.0).all() for v in t.values())
    assert t[2025].shape == (8760,)


def test_get_temperature_cmip6_full():
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(base_year=2025, end_year=2027, data_dir=cd)
        (cd / "cmip6").mkdir(parents=True)
        cf = cd / "cmip6" / "cmip6_CMCC_CM2_VHR4_1.00_2.00_2025_2027.npz"
        # n_years - 1 == 2 years suffices to short-circuit
        np.savez_compressed(
            cf, **{"2025": np.full(8760, 5.0), "2026": np.full(8760, 6.0)}
        )
        t = p._get_temperature(rec)
    assert sorted(t.keys()) == [2025, 2026]
    assert t[2025][0] == 5.0
    assert t[2026][0] == 6.0


def test_get_temperature_cmip6_partial_filled_with_era5():
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(base_year=2025, end_year=2027, data_dir=cd)
        (cd / "cmip6").mkdir(parents=True)
        (cd / "era5").mkdir(parents=True)
        cf = cd / "cmip6" / "cmip6_CMCC_CM2_VHR4_1.00_2.00_2025_2027.npz"
        np.savez_compressed(cf, **{"2025": np.full(8760, 5.0)})  # only 1 year
        np.save(cd / "era5" / "era5_1.00_2.00_2023.npy", np.full(8760, 9.0))
        t = p._get_temperature(rec)
    assert sorted(t.keys()) == [2025, 2026, 2027]
    assert t[2025][0] == 5.0  # from cmip6
    assert t[2026][0] == 9.0  # filled with era5
    assert t[2027][0] == 9.0


def test_download_temperatures_all_cached_noop():
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(base_year=2025, end_year=2025, data_dir=cd)
        (cd / "cmip6").mkdir(parents=True)
        (cd / "era5").mkdir(parents=True)
        np.savez_compressed(
            cd / "cmip6" / "cmip6_CMCC_CM2_VHR4_1.00_2.00_2025_2025.npz",
            **{"2025": np.ones(8760)},
        )
        np.save(cd / "era5" / "era5_1.00_2.00_2023.npy", np.ones(8760))
        # Should not attempt any network; returns None cleanly.
        assert p.download_temperatures({"AAA": rec}) is None


def test_download_temperatures_invokes_fetchers(monkeypatch):
    rec = _country_record()
    calls = {"cmip6": 0, "era5": 0}

    monkeypatch.setattr(
        dp,
        "fetch_cmip6_temperature",
        lambda *a, **k: calls.__setitem__("cmip6", calls["cmip6"] + 1),
    )
    monkeypatch.setattr(
        dp,
        "_fetch_era5_temperature",
        lambda *a, **k: calls.__setitem__("era5", calls["era5"] + 1),
    )
    # avoid real sleeps
    monkeypatch.setattr(dp.time, "sleep", lambda *a, **k: None)

    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(base_year=2025, end_year=2025, data_dir=cd)
        p.download_temperatures({"AAA": rec})

    assert calls["cmip6"] == 1
    assert calls["era5"] == 1


def test_project_country_legacy_path(patch_build_features):
    p = dp.GlobalDemandProjector(base_year=2025, end_year=2026, ml_engine="xgboost")
    p._model = _FakeModel(value=2.0)
    rec = _country_record()
    temps = {2025: np.full(8760, 20.0), 2026: np.full(8760, 21.0)}
    res = p.project_country(rec, temps)

    assert set(res.keys()) == {"hourly", "annual_gwh", "peak_mw"}
    assert sorted(res["hourly"].keys()) == [2025, 2026]
    assert res["hourly"][2025].dtype == np.float32
    # raw_mean = 2.0, base annual_gwh = 100 → corrected = 200 for base year
    assert res["annual_gwh"][2025] == pytest.approx(200.0)
    # flat shape → peak == average MW
    avg_mw = res["annual_gwh"][2025] * 1000.0 / 8760.0
    assert res["peak_mw"][2025] == pytest.approx(avg_mw, rel=1e-5)


def test_project_country_uses_pop_projections(patch_build_features):
    # With pop_projections set, pop_growth is derived from them (no crash);
    # verify it still produces a valid projection.
    p = dp.GlobalDemandProjector(base_year=2025, end_year=2026, ml_engine="xgboost")
    p._model = _FakeModel()
    rec = _country_record(pop_projections={2025: 1000.0, 2030: 1100.0})
    temps = {2025: np.full(8760, 20.0), 2026: np.full(8760, 20.0)}
    res = p.project_country(rec, temps)
    assert res["annual_gwh"][2026] > 0


def test_project_country_zero_raw_mean_keeps_shape(patch_build_features):
    # raw_mean == 0 → shape_factors fall back to raw_sf, annual_gwh becomes 0.
    p = dp.GlobalDemandProjector(base_year=2025, end_year=2025, ml_engine="xgboost")
    p._model = _FakeModel(value=0.0)
    rec = _country_record()
    temps = {2025: np.zeros(8760)}
    res = p.project_country(rec, temps)
    assert res["annual_gwh"][2025] == pytest.approx(0.0)
    assert res["peak_mw"][2025] == pytest.approx(0.0)


def test_run_pipeline_writes_outputs(patch_build_features):
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(
            base_year=2025, end_year=2026, ml_engine="xgboost", data_dir=cd
        )
        p._model = _FakeModel()
        out = cd / "out"
        res_path = p.run({"AAA": rec}, output_dir=out)

        assert res_path == out
        assert (out / "AAA" / "AAA_2025.parquet").exists()
        assert (out / "AAA" / "AAA_2026.parquet").exists()
        assert (out / "summary_annual.parquet").exists()

        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["n_countries"] == 1
        assert manifest["ssp"] == "SSP2"
        assert manifest["ml_engine"] == "xgboost"
        assert manifest["total_countries_attempted"] == 1

        sdf = pd.read_parquet(out / "summary_annual.parquet")
        assert len(sdf) == 2  # two years
        assert {"iso3", "year", "annual_gwh", "peak_mw", "load_factor"} <= set(
            sdf.columns
        )

        # hourly parquet content
        df = pd.read_parquet(out / "AAA" / "AAA_2025.parquet")
        assert list(df.columns) == ["timestamp", "demand_mw"]
        assert len(df) == 8760


def test_run_filters_zero_annual_when_no_country_filter(patch_build_features):
    rec_a = _country_record(iso3="AAA", annual_gwh=100.0)
    rec_b = _country_record(iso3="BBB", annual_gwh=0.0)
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(
            base_year=2025, end_year=2025, ml_engine="xgboost", data_dir=cd
        )
        p._model = _FakeModel()
        out = cd / "out"
        p.run({"AAA": rec_a, "BBB": rec_b}, output_dir=out)
        manifest = json.loads((out / "manifest.json").read_text())
        # BBB has annual_gwh == 0 → excluded from targets
        assert manifest["total_countries_attempted"] == 1
        assert (out / "AAA").exists()
        assert not (out / "BBB").exists()


def test_run_with_explicit_country_filter(patch_build_features):
    rec_a = _country_record(iso3="AAA", annual_gwh=0.0)  # zero, but explicitly named
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(
            base_year=2025, end_year=2025, ml_engine="xgboost", data_dir=cd
        )
        p._model = _FakeModel()
        out = cd / "out"
        # explicit filter bypasses the annual_gwh > 0 gate
        p.run({"AAA": rec_a, "BBB": _country_record(iso3="BBB")},
              output_dir=out, countries=["AAA"])
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["total_countries_attempted"] == 1
        assert (out / "AAA").exists()


def test_run_skips_failing_country(patch_build_features, monkeypatch):
    # Force project_country to raise so the except branch is exercised.
    rec = _country_record()
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td)
        p = dp.GlobalDemandProjector(
            base_year=2025, end_year=2025, ml_engine="xgboost", data_dir=cd
        )
        p._model = _FakeModel()

        def boom(*a, **k):
            raise RuntimeError("explode")

        monkeypatch.setattr(p, "project_country", boom)
        out = cd / "out"
        p.run({"AAA": rec}, output_dir=out)
        manifest = json.loads((out / "manifest.json").read_text())
        # country failed → n_countries 0, but pipeline still completes
        assert manifest["n_countries"] == 0
        assert (out / "summary_annual.parquet").exists()
