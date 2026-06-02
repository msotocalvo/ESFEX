# -*- coding: utf-8 -*-
"""Coverage tests for esfex.models.financial_analysis.

Covers the pure financial helpers directly and exercises the HDF5-backed
high-level functions (compute_system_financials, compute_technology_financials,
load_price_series, run_sensitivity_analysis, run_monte_carlo) against small
synthetic HDF5 fixtures built with h5py.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

# h5py is required by the HDF5-backed functions; guard so the file still
# collects if it is absent.
h5py = pytest.importorskip("h5py")

from esfex.models import financial_analysis as fa  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_crf_zero_rate():
    # rate <= 0 -> 1/years
    assert fa._crf(0.0, 10) == pytest.approx(0.1)
    assert fa._crf(-0.05, 10) == pytest.approx(0.1)
    # years guarded by max(years, 1)
    assert fa._crf(0.0, 0) == pytest.approx(1.0)


def test_crf_positive_rate():
    # Known annuity formula
    r, n = 0.05, 10
    expected = r * (1 + r) ** n / ((1 + r) ** n - 1)
    assert fa._crf(r, n) == pytest.approx(expected)


def test_compute_irr_empty_and_allzero():
    assert fa._compute_irr([]) == 0.0
    assert fa._compute_irr([0.0, 0.0, 0.0]) == 0.0


def test_compute_irr_all_nonnegative_is_nan():
    # No investment outflow -> NaN
    res = fa._compute_irr([0.0, 100.0, 200.0])
    assert math.isnan(res)


def test_compute_irr_conventional():
    # -100 then +110 -> IRR = 10%
    irr = fa._compute_irr([-100.0, 110.0])
    assert irr == pytest.approx(0.10, abs=1e-4)


def test_compute_irr_recovers_known_rate():
    # Build a stream with a known IRR of 12%
    rate = 0.12
    cfs = [-1000.0, 400.0, 400.0, 400.0]
    irr = fa._compute_irr(cfs)
    npv = sum(cf / (1 + irr) ** t for t, cf in enumerate(cfs))
    assert abs(npv) < 1e-3


def test_compute_mirr_degenerate():
    assert fa._compute_mirr([], 0.05, 0.10) == 0.0
    assert fa._compute_mirr([-100.0], 0.05, 0.10) == 0.0
    # no positive flows -> 0
    assert fa._compute_mirr([-100.0, -50.0], 0.05, 0.10) == 0.0


def test_compute_mirr_value():
    cfs = [-1000.0, 500.0, 600.0]
    fin, rein = 0.05, 0.10
    n = len(cfs) - 1
    neg_pv = -1000.0
    pos_fv = 500.0 * (1 + rein) ** (n - 1) + 600.0 * (1 + rein) ** (n - 2)
    expected = (pos_fv / abs(neg_pv)) ** (1.0 / n) - 1.0
    assert fa._compute_mirr(cfs, fin, rein) == pytest.approx(expected)


def test_compute_npv():
    cfs = [-100.0, 50.0, 60.0]
    rate = 0.10
    expected = -100.0 + 50.0 / 1.1 + 60.0 / 1.1 ** 2
    assert fa._compute_npv(cfs, rate) == pytest.approx(expected)


def test_payback_never_recovers():
    assert fa._payback([-100.0, 10.0, 10.0]) == float("inf")


def test_payback_simple():
    # -100, +60, +60 -> crosses zero in year 2
    pb = fa._payback([-100.0, 60.0, 60.0])
    # at t=2, prev cumulative = -40, dcf=60 -> frac = 40/60
    assert pb == pytest.approx(1 + 40.0 / 60.0)


def test_payback_discounted():
    cfs = [-100.0, 60.0, 80.0]
    pb = fa._payback(cfs, discounted=True, rate=0.10)
    assert pb > 0 and math.isfinite(pb)


def test_depreciation_straight_line():
    sched = fa._depreciation_schedule(1000.0, "straight_line", 5, 10)
    assert sched.shape == (10,)
    assert sched[:5] == pytest.approx([200.0] * 5)
    assert sched[5:] == pytest.approx([0.0] * 5)


def test_depreciation_straight_line_years_capped_by_lifetime():
    sched = fa._depreciation_schedule(1000.0, "straight_line", 20, 4)
    # dep_years = min(20, 4) = 4 -> 250 each
    assert sched == pytest.approx([250.0, 250.0, 250.0, 250.0])


def test_depreciation_macrs():
    sched = fa._depreciation_schedule(1000.0, "macrs", 5, 10)
    expected = [200.0, 320.0, 192.0, 115.2, 115.2, 57.6]
    assert sched[:6] == pytest.approx(expected)
    assert sched[6:] == pytest.approx([0.0] * 4)


def test_depreciation_macrs_truncated_lifetime():
    sched = fa._depreciation_schedule(1000.0, "macrs", 5, 3)
    assert sched.shape == (3,)
    assert sched == pytest.approx([200.0, 320.0, 192.0])


def test_debt_service_no_debt():
    assert np.all(fa._debt_service(0.0, 0.05, 10, 10) == 0.0)
    assert np.all(fa._debt_service(1000.0, 0.05, 0, 10) == 0.0)


def test_debt_service_zero_rate():
    svc = fa._debt_service(1000.0, 0.0, 5, 10)
    assert svc[:5] == pytest.approx([200.0] * 5)
    assert svc[5:] == pytest.approx([0.0] * 5)


def test_debt_service_positive_rate():
    svc = fa._debt_service(1000.0, 0.05, 5, 10)
    annuity = 1000.0 * fa._crf(0.05, 5)
    assert svc[:5] == pytest.approx([annuity] * 5)
    assert svc[5:] == pytest.approx([0.0] * 5)


def test_vintage_depreciation():
    capex = np.array([1000.0, 0.0, 500.0])
    sched = fa._vintage_depreciation(capex, "straight_line", 2, 3)
    # vintage 0: 1000 over 2 yrs -> [500,500,0]
    # vintage 2: 500 over min(2, 3-2=1) -> [.., .., 500]
    assert sched == pytest.approx([500.0, 500.0, 500.0])


def test_vintage_debt_service():
    capex = np.array([1000.0, 0.0])
    ds = fa._vintage_debt_service(capex, 0.5, 0.0, 1, 2)
    # principal vintage 0 = 500, zero rate, tenor 1 -> 500 in year 0
    assert ds[0] == pytest.approx(500.0)
    assert ds[1] == pytest.approx(0.0)


def test_as_array_scalar():
    arr = fa._as_array(3.5, 4)
    assert arr.tolist() == [3.5, 3.5, 3.5, 3.5]


def test_as_array_list_string():
    arr = fa._as_array("[1.0, 2.0, 3.0]", 3)
    assert arr.tolist() == [1.0, 2.0, 3.0]


def test_as_array_scalar_string():
    arr = fa._as_array("4.0", 2)
    assert arr.tolist() == [4.0, 4.0]


def test_as_array_bytes():
    arr = fa._as_array(b"[5.0, 6.0]", 2)
    assert arr.tolist() == [5.0, 6.0]


def test_as_array_pad_and_truncate():
    # shorter than size -> padded with last value
    padded = fa._as_array([1.0, 2.0], 4)
    assert padded.tolist() == [1.0, 2.0, 2.0, 2.0]
    # longer than size -> truncated
    trunc = fa._as_array([1.0, 2.0, 3.0, 4.0], 2)
    assert trunc.tolist() == [1.0, 2.0]
    # single-element array broadcast
    single = fa._as_array([7.0], 3)
    assert single.tolist() == [7.0, 7.0, 7.0]


def test_h5safe():
    assert fa._h5safe("a/b/c") == "a - b - c"
    assert fa._h5safe("plain") == "plain"


def test_index_configs_by_name():
    configs = [{"name": "Gen/A"}, {"name": "GenB"}, {"name": ""}]
    out = fa._index_configs_by_name(configs)
    assert "Gen - A" in out
    assert "GenB" in out
    # empty name skipped
    assert "" not in out


def test_lookup_config_by_name():
    configs = [{"name": "Foo"}, {"name": "Bar"}]
    by_name = fa._index_configs_by_name(configs)
    assert fa._lookup_config("Foo", by_name, configs) == {"name": "Foo"}


def test_lookup_config_dedup_suffix():
    configs = [{"name": "Foo"}]
    by_name = fa._index_configs_by_name(configs)
    # "Foo (2)" strips the suffix back to "Foo"
    assert fa._lookup_config("Foo (2)", by_name, configs) == {"name": "Foo"}


def test_lookup_config_positional_fallback():
    configs = [{"x": 1}, {"x": 2}]
    by_name = {}  # no names
    assert fa._lookup_config("missing", by_name, configs, idx=1) == {"x": 2}


def test_lookup_config_empty():
    assert fa._lookup_config("missing", {}, [], idx=-1) == {}


def test_resolve_invest_cost_via_technology_link():
    cfg = {"technology": "TechX"}
    techs = [{"name": "TechX", "invest_cost": 1234.0}]
    assert fa._resolve_invest_cost(cfg, techs, "invest_cost") == pytest.approx(1234.0)


def test_resolve_invest_cost_own_attr():
    cfg = {"technology": "none", "invest_cost": 999.0}
    assert fa._resolve_invest_cost(cfg, [], "invest_cost") == pytest.approx(999.0)


def test_resolve_invest_cost_legacy_fallback():
    cfg = {"technology": "", "invest_cost": 0.0}
    techs = [{"name": "First", "invest_cost": 55.0}]
    assert fa._resolve_invest_cost(cfg, techs, "invest_cost") == pytest.approx(55.0)


def test_resolve_invest_cost_zero():
    cfg = {"technology": "", "invest_cost": 0.0}
    assert fa._resolve_invest_cost(cfg, [], "invest_cost") == 0.0


def test_fuel_opex_map():
    gens = [
        {"fuel": "gas", "fuel_cost": [10.0, 5.0], "fixed_cost": 2.0,
         "maintenance_cost": 1.0},
        {"fuel": "gas", "fuel_cost": 99.0},  # duplicate fuel ignored
        {"fuel_type": "coal", "fuel_cost": 7.0},
        {"fuel": ""},  # blank ignored
    ]
    out = fa._fuel_opex_map(gens, 2)
    assert out["gas"] == pytest.approx((10.0, 2.0, 1.0))
    assert out["coal"][0] == pytest.approx(7.0)
    assert "" not in out


def test_battery_opex_rate():
    bats = [{"maintenance_cost": 1.0}, {"maintenance_cost": [3.0, 2.0]}]
    assert fa._battery_opex_rate(bats, 2) == pytest.approx(3.0)
    assert fa._battery_opex_rate([], 2) == 0.0


def test_clamp_assumption():
    assert fa._clamp_assumption("discount_rate", -1.0) == pytest.approx(1e-3)
    assert fa._clamp_assumption("discount_rate", 99.0) == pytest.approx(0.40)
    assert fa._clamp_assumption("discount_rate", 0.10) == pytest.approx(0.10)
    # unknown var passes through
    assert fa._clamp_assumption("not_a_var", 12345.0) == 12345.0


def test_investment_cashflow_metrics_empty():
    z = np.zeros(3)
    m = fa._investment_cashflow_metrics(
        z, z, z, z, z, fa.FinancialAssumptions(), 0.08, 3,
    )
    # No generation -> LCOE inf; no capex -> all-zero cash flows -> IRR 0
    assert m["lcoe"] == float("inf")
    assert m["capex"] == 0.0
    assert m["irr"] == 0.0


def test_investment_cashflow_metrics_with_flows():
    n = 3
    inv_revenue = np.array([0.0, 100.0, 100.0])
    inv_fuel = np.array([0.0, 10.0, 10.0])
    inv_om = np.array([0.0, 5.0, 5.0])
    inv_capex = np.array([200.0, 0.0, 0.0])
    inv_gen = np.array([0.0, 50.0, 50.0])
    m = fa._investment_cashflow_metrics(
        inv_revenue, inv_fuel, inv_om, inv_capex, inv_gen,
        fa.FinancialAssumptions(itc_rate=0.0, tax_rate=0.0,
                                insurance_rate=0.0, salvage_fraction=0.0),
        0.08, n,
    )
    assert m["capex"] == pytest.approx(200.0)
    assert math.isfinite(m["lcoe"])
    assert m["npv"] == pytest.approx(
        sum(cf / 1.08 ** t for t, cf in enumerate(
            [-200.0, 85.0, 85.0]))
    )


def test_dataclass_defaults():
    fin = fa.SystemFinancials()
    assert fin.npv_total == 0.0
    assert fin.payback_simple == float("inf")
    assert isinstance(fin.lcoe_by_tech, dict)
    tf = fa.TechnologyFinancials()
    assert math.isnan(tf.lcos)
    sa = fa.FinancialAssumptions()
    assert sa.discount_rate == 0.08
    sens = fa.SensitivityResult()
    assert sens.sweeps == {}
    mc = fa.MonteCarloResult()
    assert mc.n_samples == 0


# ---------------------------------------------------------------------------
# HDF5-backed integration tests
# ---------------------------------------------------------------------------


def _build_h5(path, *, with_cost_breakdown=False, years=(2030, 2031),
              with_investment_gen=False, temporal_res=1, num_nodes=1):
    """Create a minimal synthetic ESFEX results HDF5 file."""
    n_nodes = num_nodes
    n_hours = 24
    rng = np.random.default_rng(0)

    with h5py.File(path, "w") as f:
        f.attrs["temporal_resolution_hours"] = temporal_res
        f.attrs["num_nodes"] = n_nodes

        # summary_results/year
        sr = f.create_group("summary_results")
        sr.create_dataset("year", data=np.array(list(years), dtype=int))

        # system_configuration/generators
        sc = f.create_group("system_configuration")
        gens = sc.create_group("generators")
        gens.attrs["num_generators"] = 1
        g0 = gens.create_group("generator_0")
        g0.attrs["name"] = "GasUnit"
        g0.attrs["fuel"] = "gas"
        g0.attrs["fuel_type"] = "gas"
        g0.attrs["fuel_cost"] = 20.0
        g0.attrs["fixed_cost"] = 2.0
        g0.attrs["maintenance_cost"] = 1.0
        g0.attrs["start_up_cost"] = 100.0
        g0.attrs["rated_power"] = 100.0
        g0.attrs["invest_cost"] = 0.0
        g0.attrs["technology"] = "ExpTech"

        # batteries
        bats = sc.create_group("batteries")
        bats.attrs["num_batteries"] = 1
        b0 = bats.create_group("battery_0")
        b0.attrs["name"] = "Bat1"
        b0.attrs["maintenance_cost"] = 0.5

        # technologies (investment tech)
        techs = sc.create_group("technologies")
        techs.attrs["num_technologies"] = 1
        t0 = techs.create_group("technology_0")
        t0.attrs["name"] = "ExpTech"
        t0.attrs["fuel"] = "gas"
        t0.attrs["invest_cost"] = 500.0

        # battery technologies
        bt = sc.create_group("battery_technologies")
        bt.attrs["num_battery_technologies"] = 1
        bt0 = bt.create_group("battery_technology_0")
        bt0.attrs["name"] = "ExpBatTech"
        bt0.attrs["invest_cost_power"] = 300.0
        bt0.attrs["invest_cost_energy"] = 100.0

        # detailed_results per year
        dr = f.create_group("detailed_results")
        for yi, year in enumerate(years):
            key = f"scenario_{year}"
            grp = dr.create_group(key)
            grp.attrs["year"] = year
            grp.attrs["total_cost"] = 1.0e6

            gen_grp = grp.create_group("generation")
            gen_data = np.abs(rng.normal(50.0, 5.0, (n_nodes, n_hours)))
            gen_grp.create_dataset("GasUnit", data=gen_data)

            if with_investment_gen:
                inv_data = np.abs(rng.normal(30.0, 3.0, (n_nodes, n_hours)))
                gen_grp.create_dataset("Investment ExpTech", data=inv_data)

            grp.create_dataset(
                "nodal_electricity_prices",
                data=np.abs(rng.normal(40.0, 8.0, (n_nodes, n_hours))),
            )
            grp.create_dataset("demand", data=np.abs(
                rng.normal(60.0, 5.0, (n_nodes, n_hours))))
            grp.create_dataset(
                "CO2_emissions",
                data=np.abs(rng.normal(10.0, 1.0, (n_nodes, n_hours))),
            )

            # startup
            su = grp.create_group("gen_startup")
            su.create_dataset(
                "GasUnit", data=(rng.random((n_nodes, n_hours)) > 0.8).astype(float))

            # batteries (discharge/charge/soc)
            for grpname in ("battery_discharge", "battery_charge",
                            "battery_soc"):
                bg = grp.create_group(grpname)
                bg.create_dataset(
                    "Bat1", data=np.abs(rng.normal(5.0, 1.0, (n_nodes, n_hours))))

        if with_cost_breakdown:
            cbd = f.create_group("cost_breakdown")
            for year in years:
                yg = cbd.create_group(f"year_{year}")
                yg.attrs["fuel_cost"] = 100000.0
                yg.attrs["fixed_om_cost"] = 5000.0
                yg.attrs["maintenance_cost"] = 3000.0
                yg.attrs["battery_maintenance_cost"] = 1000.0
                yg.attrs["battery_degradation_cost"] = 500.0
                yg.attrs["startup_cost"] = 2000.0
                yg.attrs["co2_emission_cost"] = 8000.0
                yg.attrs["load_shedding_cost"] = 1500.0
                yg.attrs["curtailment_cost"] = 750.0


@pytest.fixture()
def h5_basic(tmp_path):
    p = tmp_path / "basic.h5"
    _build_h5(p)
    return p


@pytest.fixture()
def h5_cbd(tmp_path):
    p = tmp_path / "cbd.h5"
    _build_h5(p, with_cost_breakdown=True)
    return p


@pytest.fixture()
def h5_invest(tmp_path):
    p = tmp_path / "invest.h5"
    _build_h5(p, with_investment_gen=True)
    return p


def test_load_system_from_h5(h5_basic):
    info = fa._load_system_from_h5(h5_basic)
    assert info["years"] == [2030, 2031]
    assert info["num_nodes"] == 1
    assert info["temporal_res"] == 1
    assert len(info["gen_configs"]) == 1
    assert len(info["bat_configs"]) == 1
    assert len(info["tech_configs"]) == 1
    assert len(info["bat_tech_configs"]) == 1
    assert set(info["scenarios"].keys()) == {2030, 2031}


def test_load_year_data(h5_basic):
    info = fa._load_system_from_h5(h5_basic)
    key = info["scenarios"][2030]
    data = fa._load_year_data(h5_basic, key)
    assert "GasUnit" in data["generation"]
    assert "nodal_prices" in data
    assert "co2_emissions" in data
    assert "Bat1" in data["battery_discharge"]
    assert data["total_cost"] == pytest.approx(1.0e6)


def test_try_load_cost_breakdown_present(h5_cbd):
    cbd = fa._try_load_cost_breakdown(h5_cbd, 2030)
    assert cbd is not None
    assert cbd["fuel_cost"] == pytest.approx(100000.0)


def test_try_load_cost_breakdown_absent(h5_basic):
    assert fa._try_load_cost_breakdown(h5_basic, 2030) is None


def test_try_load_cost_breakdown_missing_year(h5_cbd):
    assert fa._try_load_cost_breakdown(h5_cbd, 1999) is None


def test_recalculate_costs(h5_basic):
    info = fa._load_system_from_h5(h5_basic)
    key = info["scenarios"][2030]
    data = fa._load_year_data(h5_basic, key)
    costs = fa._recalculate_costs(
        data, info["gen_configs"], info["bat_configs"], info["temporal_res"],
    )
    assert costs["fuel_cost"] > 0
    assert costs["om_cost"] > 0
    assert costs["revenue"] > 0
    assert costs["battery_cost"] > 0
    assert costs["startup_cost"] >= 0


def test_compute_system_financials_basic(h5_basic):
    fin = fa.compute_system_financials(h5_basic)
    assert isinstance(fin, fa.SystemFinancials)
    assert fin.wacc > 0
    assert not fin.cash_flows.empty
    assert len(fin.cash_flows) == 2
    assert math.isfinite(fin.npv_revenue)
    # revenue present, generation present
    assert fin.npv_revenue > 0
    assert fin.lcoe_system >= 0


def test_compute_system_financials_no_years(tmp_path):
    p = tmp_path / "empty.h5"
    with h5py.File(p, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1
    fin = fa.compute_system_financials(p)
    # returns default SystemFinancials
    assert fin.npv_total == 0.0
    assert fin.cash_flows.empty


def test_compute_system_financials_with_cost_breakdown(h5_cbd):
    fin = fa.compute_system_financials(h5_cbd)
    # fuel cost comes from cost breakdown -> npv_fuel positive
    assert fin.npv_fuel > 0
    assert fin.npv_penalties > 0  # load shedding + curtailment + co2


def test_compute_system_financials_with_investment(h5_invest):
    fin = fa.compute_system_financials(h5_invest)
    # Investment ExpTech booked CAPEX
    assert fin.investment_capex > 0
    assert fin.npv_capex > 0


def test_compute_system_financials_ppa_price(h5_basic):
    assumptions = fa.FinancialAssumptions(ppa_price=75.0, ppa_escalation=0.0)
    fin = fa.compute_system_financials(h5_basic, assumptions)
    assert fin.npv_revenue > 0


def test_compute_system_financials_carbon_and_rec(h5_basic):
    assumptions = fa.FinancialAssumptions(
        carbon_price=50.0, rec_price=10.0, capacity_payment=1000.0,
    )
    fin = fa.compute_system_financials(h5_basic, assumptions)
    # carbon cost should appear in penalties decomposition
    assert fin.npv_penalties > 0


def test_compute_technology_financials(h5_basic):
    res = fa.compute_technology_financials(h5_basic)
    assert "GasUnit" in res
    assert "Bat1" in res
    gas = res["GasUnit"]
    assert gas.tech_type == "generator"
    assert gas.generation_mwh > 0
    assert gas.revenue_total > 0
    assert gas.installed_mw >= 100.0  # includes rated_power
    bat = res["Bat1"]
    assert bat.tech_type == "battery"
    assert math.isfinite(bat.arbitrage_revenue)


def test_compute_technology_financials_no_years(tmp_path):
    p = tmp_path / "empty2.h5"
    with h5py.File(p, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1
    assert fa.compute_technology_financials(p) == {}


def test_load_price_series(h5_basic):
    series = fa.load_price_series(h5_basic)
    assert series.ndim == 1
    assert series.size == 24  # 1 node x 24 hours
    assert np.all(series >= 0)


def test_load_price_series_specific_year(h5_basic):
    series = fa.load_price_series(h5_basic, year=2031)
    assert series.size == 24


def test_load_price_series_no_years(tmp_path):
    p = tmp_path / "noyears.h5"
    with h5py.File(p, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1
    assert fa.load_price_series(p).size == 0


def test_run_sensitivity_analysis(h5_basic):
    assumptions = fa.FinancialAssumptions(ppa_price=60.0)
    res = fa.run_sensitivity_analysis(
        h5_basic, assumptions,
        variables=["discount_rate", "ppa_price"],
        n_points=5,
    )
    assert isinstance(res, fa.SensitivityResult)
    assert "discount_rate" in res.sweeps
    assert "ppa_price" in res.sweeps
    assert len(res.sweeps["ppa_price"]) == 5
    assert "discount_rate" in res.tornado


def test_run_sensitivity_analysis_skips_unknown_var(h5_basic):
    res = fa.run_sensitivity_analysis(
        h5_basic, fa.FinancialAssumptions(),
        variables=["not_a_field"], n_points=3,
    )
    # unknown var -> skipped, no sweep recorded
    assert "not_a_field" not in res.sweeps


def test_run_sensitivity_analysis_zero_base_val(h5_basic):
    # carbon_price defaults to 0 -> uses absolute ±range_pct branch
    res = fa.run_sensitivity_analysis(
        h5_basic, fa.FinancialAssumptions(),
        variables=["carbon_price"], n_points=3,
    )
    assert "carbon_price" in res.sweeps


def test_run_monte_carlo(h5_basic):
    res = fa.run_monte_carlo(h5_basic, fa.FinancialAssumptions(), n_samples=8,
                             seed=1)
    assert isinstance(res, fa.MonteCarloResult)
    assert res.n_samples == 8
    assert res.npv_samples.size == 8
    assert math.isfinite(res.npv_mean)
    assert math.isfinite(res.npv_std)
    assert res.npv_p5 <= res.npv_p50 <= res.npv_p95


def test_run_monte_carlo_custom_distributions(h5_basic):
    dists = {
        "discount_rate": ("uniform", 0.05, 0.12),
        "tax_rate": ("triangular", 0.10, 0.40),
        "ppa_price": ("normal", 60.0, 5.0),
        "ignored": ("weird_dist", 1.0, 2.0),  # unknown dist -> skipped
    }
    res = fa.run_monte_carlo(h5_basic, fa.FinancialAssumptions(),
                             distributions=dists, n_samples=6, seed=3)
    assert res.n_samples == 6
    assert res.npv_samples.size == 6
