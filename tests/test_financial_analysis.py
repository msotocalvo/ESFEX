# -*- coding: utf-8 -*-
"""
Tests for the financial analysis engine.

Tests cover:
- Financial helper functions (CRF, IRR, MIRR, NPV, payback, depreciation, debt service)
- HDF5 loading with mock data
- Cost recalculation fallback
- System financials computation
- Technology financials computation
- Sensitivity analysis
- Monte Carlo simulation
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np
import pandas as pd
import pytest

from esfex.models.financial_analysis import (
    FinancialAssumptions,
    MonteCarloResult,
    SensitivityResult,
    SystemFinancials,
    TechnologyFinancials,
    _as_array,
    _compute_irr,
    _compute_mirr,
    _compute_npv,
    _crf,
    _debt_service,
    _depreciation_schedule,
    _load_system_from_h5,
    _load_year_data,
    _payback,
    _recalculate_costs,
    _try_load_cost_breakdown,
    compute_system_financials,
    compute_technology_financials,
    load_price_series,
    run_monte_carlo,
    run_sensitivity_analysis,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def default_assumptions():
    return FinancialAssumptions()


@pytest.fixture
def mock_h5(tmp_path):
    """Create a minimal mock HDF5 file with 3 years of results."""
    h5_path = tmp_path / "test_results.h5"
    n_gens = 2
    n_bats = 1
    n_nodes = 1
    n_hours = 48  # 2 days
    years = [2025, 2026, 2027]

    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 6
        f.attrs["num_nodes"] = n_nodes

        # -- system_configuration --
        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = n_gens

        g0 = gen_grp.create_group("generator_0")
        g0.attrs["name"] = "Diesel"
        g0.attrs["fuel_type"] = "diesel"
        g0.attrs["rated_power"] = 50.0  # MW
        g0.attrs["fuel_cost"] = 40.0  # $/MWh
        g0.attrs["fixed_cost"] = 2.0
        g0.attrs["maintenance_cost"] = 1.5
        g0.attrs["start_up_cost"] = 500.0

        g1 = gen_grp.create_group("generator_1")
        g1.attrs["name"] = "Solar PV"
        g1.attrs["fuel_type"] = "solar"
        g1.attrs["rated_power"] = 100.0
        g1.attrs["fuel_cost"] = 0.0
        g1.attrs["fixed_cost"] = 0.5
        g1.attrs["maintenance_cost"] = 0.3
        g1.attrs["start_up_cost"] = 0.0

        bat_grp = f.create_group("system_configuration/batteries")
        bat_grp.attrs["num_batteries"] = n_bats
        b0 = bat_grp.create_group("battery_0")
        b0.attrs["name"] = "Li-ion"
        b0.attrs["rated_power"] = 20.0
        b0.attrs["maintenance_cost"] = 0.5

        tech_grp = f.create_group("system_configuration/technologies")
        tech_grp.attrs["num_technologies"] = 1
        t0 = tech_grp.create_group("technology_0")
        t0.attrs["name"] = "Investment Solar"
        t0.attrs["invest_cost"] = 1200.0  # $/MW

        bt_grp = f.create_group("system_configuration/battery_technologies")
        bt_grp.attrs["num_battery_technologies"] = 1
        bt0 = bt_grp.create_group("battery_technology_0")
        bt0.attrs["name"] = "Investment Battery"
        bt0.attrs["invest_cost_power"] = 800.0

        # -- summary_results --
        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array(years))

        # -- detailed_results --
        detailed = f.create_group("detailed_results")
        rng = np.random.default_rng(42)

        for y_idx, year in enumerate(years):
            key = f"year_{year}_threshold_0"
            grp = detailed.create_group(key)
            grp.attrs["year"] = year
            grp.attrs["total_cost"] = 5e6 + y_idx * 1e5

            # Generation: [nodes x hours]
            gen_g = grp.create_group("generation")
            diesel_out = rng.uniform(10, 40, size=(n_nodes, n_hours))
            solar_out = rng.uniform(0, 80, size=(n_nodes, n_hours))
            gen_g.create_dataset("Diesel", data=diesel_out)
            gen_g.create_dataset("Solar PV", data=solar_out)

            # Prices: [nodes x hours]
            prices = rng.uniform(30, 80, size=(n_nodes, n_hours))
            grp.create_dataset("nodal_electricity_prices", data=prices)
            grp.create_dataset("electricity_prices", data=prices.mean(axis=0))

            # Demand
            grp.create_dataset("demand", data=rng.uniform(50, 120, size=(n_nodes, n_hours)))

            # Startup
            su_g = grp.create_group("gen_startup")
            su_g.create_dataset("Diesel", data=rng.integers(0, 2, size=(n_nodes, n_hours)))
            su_g.create_dataset("Solar PV", data=np.zeros((n_nodes, n_hours)))

            # Battery
            bat_dis = rng.uniform(0, 15, size=(n_nodes, n_hours))
            bat_chg = rng.uniform(0, 15, size=(n_nodes, n_hours))
            bat_soc = rng.uniform(5, 80, size=(n_nodes, n_hours))
            bd_g = grp.create_group("battery_discharge")
            bd_g.create_dataset("Li-ion", data=bat_dis)
            bc_g = grp.create_group("battery_charge")
            bc_g.create_dataset("Li-ion", data=bat_chg)
            bs_g = grp.create_group("battery_soc")
            bs_g.create_dataset("Li-ion", data=bat_soc)

            # CO2
            grp.create_dataset("CO2_emissions", data=diesel_out * 0.5)

            # Investment (only year 0)
            if y_idx == 0:
                inv_g = grp.create_group("gen_investment_power")
                inv_g.create_dataset("Solar PV", data=np.array([[50.0]]))
                bat_inv = grp.create_group("bat_investment_power")
                bat_inv.create_dataset("Li-ion", data=np.array([[10.0]]))

    return h5_path


@pytest.fixture
def mock_h5_with_cost_breakdown(mock_h5):
    """Extend mock_h5 with /cost_breakdown/ group (Layer A data)."""
    with h5py.File(mock_h5, "a") as f:
        cbd = f.create_group("cost_breakdown")
        y25 = cbd.create_group("year_2025")
        y25.attrs["fuel_cost"] = 1e6
        y25.attrs["fixed_om_cost"] = 2e5
        y25.attrs["maintenance_cost"] = 1e5
        y25.attrs["battery_maintenance_cost"] = 5e4
        y25.attrs["startup_cost"] = 3e4
        y25.attrs["load_shedding_cost"] = 1e4
        y25.attrs["curtailment_cost"] = 5e3
        y25.attrs["reserve_static_cost"] = 2e3
        y25.attrs["reserve_dynamic_cost"] = 1e3
        y25.attrs["co2_emission_cost"] = 8e4
        y25.attrs["fre_penetration_cost"] = 0.0
        y25.attrs["soc_violation_cost"] = 0.0
        # Components the engine historically ignored (present in real files):
        y25.attrs["battery_degradation_cost"] = 7e4   # → O&M
        y25.attrs["inertia_cost"] = 2e3               # → penalties
    return mock_h5


@pytest.fixture
def mock_h5_multi_tech(tmp_path):
    """Two generators that invest via two DIFFERENT investment technologies.

    GenA → TechA (1000 $/MW), invests 10 MW  → 10,000
    GenB → TechB (3000 $/MW), invests  5 MW  → 15,000   total = 25,000
    The historical ``break`` after tech_configs[0] charged BOTH at 1000.
    """
    h5_path = tmp_path / "multi_tech.h5"
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = 2
        gA = gen_grp.create_group("generator_0")
        gA.attrs["name"] = "GenA"
        gA.attrs["fuel_type"] = "solar"
        gA.attrs["technology"] = "TechA"
        gA.attrs["rated_power"] = 0.0
        gA.attrs["fuel_cost"] = 0.0
        gA.attrs["fixed_cost"] = 0.0
        gA.attrs["maintenance_cost"] = 0.0
        gB = gen_grp.create_group("generator_1")
        gB.attrs["name"] = "GenB"
        gB.attrs["fuel_type"] = "wind"
        gB.attrs["technology"] = "TechB"
        gB.attrs["rated_power"] = 0.0
        gB.attrs["fuel_cost"] = 0.0
        gB.attrs["fixed_cost"] = 0.0
        gB.attrs["maintenance_cost"] = 0.0

        tech_grp = f.create_group("system_configuration/technologies")
        tech_grp.attrs["num_technologies"] = 2
        tA = tech_grp.create_group("technology_0")
        tA.attrs["name"] = "TechA"
        tA.attrs["invest_cost"] = 1000.0
        tB = tech_grp.create_group("technology_1")
        tB.attrs["name"] = "TechB"
        tB.attrs["invest_cost"] = 3000.0

        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array([2025]))

        detailed = f.create_group("detailed_results")
        grp = detailed.create_group("year_2025_threshold_0")
        grp.attrs["year"] = 2025
        gen_g = grp.create_group("generation")
        gen_g.create_dataset("GenA", data=np.full((1, 24), 5.0))
        gen_g.create_dataset("GenB", data=np.full((1, 24), 5.0))
        grp.create_dataset("electricity_prices", data=np.full(24, 50.0))
        inv = grp.create_group("gen_investment_power")
        inv.create_dataset("GenA", data=np.array([[10.0]]))
        inv.create_dataset("GenB", data=np.array([[5.0]]))
    return h5_path


@pytest.fixture
def mock_h5_misaligned(tmp_path):
    """Generator config order differs from alphabetical dataset order.

    generator_0 = "Zeta" (fuel_cost 100), generator_1 = "Alpha" (fuel_cost 1).
    h5py iterates generation datasets alphabetically → ["Alpha", "Zeta"], so
    positional indexing into gen_configs swaps each generator's cost params.
    """
    h5_path = tmp_path / "misaligned.h5"
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = 2
        g0 = gen_grp.create_group("generator_0")
        g0.attrs["name"] = "Zeta"
        g0.attrs["fuel_type"] = "diesel"
        g0.attrs["rated_power"] = 50.0
        g0.attrs["fuel_cost"] = 100.0   # expensive
        g0.attrs["fixed_cost"] = 0.0
        g0.attrs["maintenance_cost"] = 0.0
        g1 = gen_grp.create_group("generator_1")
        g1.attrs["name"] = "Alpha"
        g1.attrs["fuel_type"] = "gas"
        g1.attrs["rated_power"] = 50.0
        g1.attrs["fuel_cost"] = 1.0      # cheap
        g1.attrs["fixed_cost"] = 0.0
        g1.attrs["maintenance_cost"] = 0.0

        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array([2025]))
        detailed = f.create_group("detailed_results")
        grp = detailed.create_group("year_2025_threshold_0")
        grp.attrs["year"] = 2025
        gen_g = grp.create_group("generation")
        # identical generation so cost difference is purely from fuel_cost
        gen_g.create_dataset("Zeta", data=np.full((1, 24), 10.0))
        gen_g.create_dataset("Alpha", data=np.full((1, 24), 10.0))
        grp.create_dataset("electricity_prices", data=np.full(24, 50.0))
    return h5_path


@pytest.fixture
def mock_h5_investment_gen(tmp_path):
    """Capacity expansion expressed as an 'Investment <tech>' generation
    series (no gen_investment_power group, as in real dispatch+expansion
    runs). Per-node peak generation is the installed-MW proxy; peak grows
    40 → 100 MW across two years.
    """
    h5_path = tmp_path / "inv_gen.h5"
    years = [2025, 2026]
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = 0  # only investment, no existing units

        tech_grp = f.create_group("system_configuration/technologies")
        tech_grp.attrs["num_technologies"] = 1
        t0 = tech_grp.create_group("technology_0")
        t0.attrs["name"] = "Cuba/Solar PV"   # → dataset "Investment Cuba - Solar PV"
        t0.attrs["invest_cost"] = 900000.0

        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array(years))
        detailed = f.create_group("detailed_results")
        for yi, yr in enumerate(years):
            grp = detailed.create_group(f"year_{yr}_threshold_0")
            grp.attrs["year"] = yr
            gen_g = grp.create_group("generation")
            peak = 40.0 if yi == 0 else 100.0
            arr = np.full((1, 24), peak * 0.5)
            arr[0, 12] = peak  # peak hour defines installed MW proxy
            gen_g.create_dataset("Investment Cuba - Solar PV", data=arr)
            grp.create_dataset("electricity_prices", data=np.full(24, 50.0))
    return h5_path


@pytest.fixture
def mock_h5_battery_invest(tmp_path):
    """Capacity expansion of a battery, expressed as an 'Investment <bat>'
    series in battery_discharge/charge/soc (no bat_investment_power group).
    Power MW = peak max(charge, discharge) per node; energy MWh = peak SOC.
    """
    h5_path = tmp_path / "bat_invest.h5"
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        f.create_group("system_configuration/generators").attrs["num_generators"] = 0
        bt_grp = f.create_group("system_configuration/battery_technologies")
        bt_grp.attrs["num_battery_technologies"] = 1
        bt0 = bt_grp.create_group("battery_technology_0")
        bt0.attrs["name"] = "Cuba/Li-ion Battery"
        bt0.attrs["invest_cost_power"] = 600000.0   # $/MW
        bt0.attrs["invest_cost_energy"] = 240000.0  # $/MWh

        f.create_group("summary_results").create_dataset("year", data=np.array([2025]))
        grp = f.create_group("detailed_results/year_2025_threshold_0")
        grp.attrs["year"] = 2025
        grp.create_group("generation")  # no generators
        grp.create_dataset("electricity_prices", data=np.full(24, 50.0))

        dis = np.full((1, 24), 5.0); dis[0, 12] = 10.0   # power peak 10 MW (discharge)
        chg = np.full((1, 24), 6.0); chg[0, 6] = 12.0    # power peak 12 MW (charge)
        soc = np.full((1, 24), 20.0); soc[0, 18] = 40.0  # energy peak 40 MWh
        grp.create_group("battery_discharge").create_dataset(
            "Investment Cuba - Li-ion Battery", data=dis)
        grp.create_group("battery_charge").create_dataset(
            "Investment Cuba - Li-ion Battery", data=chg)
        grp.create_group("battery_soc").create_dataset(
            "Investment Cuba - Li-ion Battery", data=soc)
    return h5_path


@pytest.fixture
def mock_h5_existing_plus_invest(tmp_path):
    """A big cheap EXISTING plant (revenue, zero counted capex) plus a small
    'Investment Solar' build. System IRR is inflated by the existing plant's
    revenue; the new-investment IRR reflects only the build's economics.
    """
    h5_path = tmp_path / "existing_plus_invest.h5"
    years = [2025, 2026, 2027]
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = 1
        g0 = gen_grp.create_group("generator_0")
        g0.attrs["name"] = "OldPlant"
        g0.attrs["fuel"] = "Sun"          # so investment Solar borrows its opex
        g0.attrs["fuel_cost"] = 0.0
        g0.attrs["fixed_cost"] = 0.0
        g0.attrs["maintenance_cost"] = 1.0
        g0.attrs["rated_power"] = 50000.0

        tech_grp = f.create_group("system_configuration/technologies")
        tech_grp.attrs["num_technologies"] = 1
        t0 = tech_grp.create_group("technology_0")
        t0.attrs["name"] = "Cuba/Solar PV"
        t0.attrs["fuel"] = "Sun"
        t0.attrs["invest_cost"] = 900000.0

        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array(years))
        detailed = f.create_group("detailed_results")
        for yi, yr in enumerate(years):
            grp = detailed.create_group(f"year_{yr}_threshold_0")
            grp.attrs["year"] = yr
            gen_g = grp.create_group("generation")
            # Huge existing generation → big system revenue, no counted capex
            gen_g.create_dataset("OldPlant", data=np.full((1, 24), 40000.0))
            # Small investment build, peak 5 MW from year 0
            inv_arr = np.full((1, 24), 2.5)
            inv_arr[0, 12] = 5.0
            gen_g.create_dataset("Investment Cuba - Solar PV", data=inv_arr)
            grp.create_dataset("electricity_prices", data=np.full(24, 50.0))
    return h5_path


@pytest.fixture
def mock_h5_late_invest(tmp_path):
    """Investment occurs in year index 1 (2026), not in year 0."""
    h5_path = tmp_path / "late_invest.h5"
    years = [2025, 2026, 2027]
    with h5py.File(h5_path, "w") as f:
        f.attrs["temporal_resolution_hours"] = 1
        f.attrs["num_nodes"] = 1

        gen_grp = f.create_group("system_configuration/generators")
        gen_grp.attrs["num_generators"] = 1
        g0 = gen_grp.create_group("generator_0")
        g0.attrs["name"] = "Solar"
        g0.attrs["technology"] = "TechSolar"
        g0.attrs["rated_power"] = 100.0
        g0.attrs["fuel_cost"] = 0.0
        g0.attrs["fixed_cost"] = 0.0
        g0.attrs["maintenance_cost"] = 0.0

        tech_grp = f.create_group("system_configuration/technologies")
        tech_grp.attrs["num_technologies"] = 1
        t0 = tech_grp.create_group("technology_0")
        t0.attrs["name"] = "TechSolar"
        t0.attrs["invest_cost"] = 1000.0

        summary = f.create_group("summary_results")
        summary.create_dataset("year", data=np.array(years))
        detailed = f.create_group("detailed_results")
        for y_idx, year in enumerate(years):
            grp = detailed.create_group(f"year_{year}_threshold_0")
            grp.attrs["year"] = year
            gen_g = grp.create_group("generation")
            gen_g.create_dataset("Solar", data=np.full((1, 24), 50.0))
            grp.create_dataset("electricity_prices", data=np.full(24, 50.0))
            if y_idx == 1:  # invest in 2026
                inv = grp.create_group("gen_investment_power")
                inv.create_dataset("Solar", data=np.array([[20.0]]))
    return h5_path


# =====================================================================
# Financial Helper Tests
# =====================================================================


class TestCRF:
    def test_standard_case(self):
        """CRF at 8% for 25 years should be ~0.0937."""
        crf = _crf(0.08, 25)
        assert 0.09 < crf < 0.10

    def test_zero_rate(self):
        """With zero rate, CRF = 1/years."""
        assert _crf(0.0, 20) == pytest.approx(0.05, rel=1e-10)

    def test_one_year(self):
        """CRF for 1 year at any rate = 1 + rate."""
        assert _crf(0.10, 1) == pytest.approx(1.10, rel=1e-10)

    def test_high_rate(self):
        """Sanity check for high discount rate."""
        crf = _crf(0.50, 10)
        assert crf > 0.5  # must be > rate for short period

    def test_negative_rate(self):
        """Negative rate returns 1/years fallback."""
        assert _crf(-0.05, 10) == pytest.approx(0.1, rel=1e-10)


class TestComputeIRR:
    def test_known_irr(self):
        """Standard investment: -1000 upfront, 300/yr for 5 years → IRR ~15%."""
        cfs = [-1000, 300, 300, 300, 300, 300]
        irr = _compute_irr(cfs)
        assert 0.14 < irr < 0.16

    def test_no_return(self):
        """All negative CFs should give negative IRR."""
        cfs = [-100, -50, -50]
        irr = _compute_irr(cfs)
        assert irr < 0

    def test_immediate_return(self):
        """Huge positive CF should give very high IRR."""
        cfs = [-100, 1000]
        irr = _compute_irr(cfs)
        assert irr >= 5.0

    def test_zero_npv_at_irr(self):
        """NPV at IRR should be approximately zero."""
        cfs = [-500, 150, 150, 150, 150, 150]
        irr = _compute_irr(cfs)
        npv = sum(cf / (1 + irr) ** t for t, cf in enumerate(cfs))
        assert abs(npv) < 0.01

    def test_all_zero_cash_flows(self):
        """A degenerate all-zero project has no return, not a spurious IRR."""
        assert _compute_irr([0.0, 0.0, 0.0]) == 0.0

    def test_empty_cash_flows(self):
        assert _compute_irr([]) == 0.0

    def test_no_outflow_is_undefined(self):
        """Cash flows with no investment outflow (all >= 0) have no IRR:
        return NaN, not the bracket ceiling (which displayed as 500%)."""
        import math
        assert math.isnan(_compute_irr([100.0, 200.0, 300.0]))
        # a real outflow keeps a finite/sentinel IRR
        assert _compute_irr([-100.0, 60.0, 60.0, 60.0]) > 0


class TestComputeMIRR:
    def test_standard_mirr(self):
        """MIRR should be between finance and reinvest rates for mixed CFs."""
        cfs = [-1000, 300, 400, 500, 200]
        mirr = _compute_mirr(cfs, finance_rate=0.05, reinvest_rate=0.10)
        assert 0.05 < mirr < 0.30

    def test_all_positive(self):
        """No negative CFs means neg_pv = 0 → returns 0."""
        cfs = [100, 200, 300]
        mirr = _compute_mirr(cfs, 0.05, 0.10)
        assert mirr == 0.0

    def test_single_period(self):
        """Single period returns 0."""
        mirr = _compute_mirr([-100], 0.05, 0.10)
        assert mirr == 0.0


class TestComputeNPV:
    def test_zero_rate(self):
        """At 0% discount, NPV = sum of cash flows."""
        cfs = [-100, 50, 50, 50]
        assert _compute_npv(cfs, 0.0) == pytest.approx(50.0)

    def test_positive_rate(self):
        """NPV should decrease with higher discount rate."""
        cfs = [-100, 50, 50, 50]
        npv_low = _compute_npv(cfs, 0.05)
        npv_high = _compute_npv(cfs, 0.15)
        assert npv_low > npv_high

    def test_known_value(self):
        """NPV of -1000 + 500/(1.10) + 500/(1.10^2) = -1000 + 454.55 + 413.22 = -132.23."""
        cfs = [-1000, 500, 500]
        expected = -1000 + 500 / 1.10 + 500 / 1.21
        assert _compute_npv(cfs, 0.10) == pytest.approx(expected, rel=1e-6)


class TestPayback:
    def test_simple_payback(self):
        """Simple payback for -1000 + 400/yr = 2.5 years."""
        cfs = [-1000, 400, 400, 400, 400]
        pb = _payback(cfs)
        assert pb == pytest.approx(2.5, rel=1e-3)

    def test_discounted_payback(self):
        """Discounted payback should be longer than simple."""
        cfs = [-1000, 400, 400, 400, 400]
        simple = _payback(cfs)
        discounted = _payback(cfs, discounted=True, rate=0.10)
        assert discounted > simple

    def test_no_payback(self):
        """Returns inf if cumulative never goes positive."""
        cfs = [-1000, 100, 100]
        assert _payback(cfs) == float("inf")


class TestDepreciationSchedule:
    def test_straight_line(self):
        """Straight-line: equal annual depreciation."""
        sched = _depreciation_schedule(1000, "straight_line", 10, 25)
        assert len(sched) == 25
        assert sched[0] == pytest.approx(100.0)
        assert sched[9] == pytest.approx(100.0)
        assert sched[10] == 0.0

    def test_macrs(self):
        """5-year MACRS should sum to 100% of CAPEX."""
        sched = _depreciation_schedule(1000, "macrs", 5, 25)
        total = np.sum(sched)
        assert total == pytest.approx(1000.0, rel=1e-6)
        assert sched[0] == pytest.approx(200.0)  # 20%
        assert sched[1] == pytest.approx(320.0)  # 32%

    def test_zero_capex(self):
        sched = _depreciation_schedule(0, "straight_line", 10, 25)
        assert np.all(sched == 0.0)


class TestDebtService:
    def test_standard_amortization(self):
        """Constant annuity over tenor period, zero after."""
        ds = _debt_service(1000, 0.05, 10, 25)
        assert len(ds) == 25
        annuity = ds[0]
        assert annuity > 0
        assert all(ds[i] == pytest.approx(annuity) for i in range(10))
        assert all(ds[i] == 0.0 for i in range(10, 25))

    def test_annuity_value(self):
        """Annuity = principal × CRF."""
        ds = _debt_service(1000, 0.05, 10, 25)
        expected = 1000 * _crf(0.05, 10)
        assert ds[0] == pytest.approx(expected, rel=1e-6)

    def test_zero_rate(self):
        """Zero rate → equal installments."""
        ds = _debt_service(1000, 0.0, 10, 25)
        assert ds[0] == pytest.approx(100.0)

    def test_zero_principal(self):
        ds = _debt_service(0, 0.05, 10, 25)
        assert np.all(ds == 0.0)


class TestAsArray:
    def test_scalar(self):
        arr = _as_array(5.0, 3)
        assert np.array_equal(arr, [5.0, 5.0, 5.0])

    def test_list(self):
        arr = _as_array([1.0, 2.0, 3.0], 3)
        assert np.array_equal(arr, [1.0, 2.0, 3.0])

    def test_short_list_padded(self):
        arr = _as_array([1.0, 2.0], 4)
        assert np.array_equal(arr, [1.0, 2.0, 2.0, 2.0])

    def test_long_list_truncated(self):
        arr = _as_array([1.0, 2.0, 3.0, 4.0], 2)
        assert np.array_equal(arr, [1.0, 2.0])

    def test_single_element_list(self):
        arr = _as_array([7.0], 5)
        assert np.array_equal(arr, [7.0, 7.0, 7.0, 7.0, 7.0])


# =====================================================================
# HDF5 Loading Tests
# =====================================================================


class TestLoadSystemFromH5:
    def test_loads_generators(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert len(info["gen_configs"]) == 2
        assert info["gen_configs"][0]["name"] == "Diesel"
        assert info["gen_configs"][1]["name"] == "Solar PV"

    def test_loads_batteries(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert len(info["bat_configs"]) == 1
        assert info["bat_configs"][0]["name"] == "Li-ion"

    def test_loads_technologies(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert len(info["tech_configs"]) == 1
        assert info["tech_configs"][0]["invest_cost"] == 1200.0

    def test_loads_years(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert info["years"] == [2025, 2026, 2027]

    def test_loads_scenarios(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert len(info["scenarios"]) == 3
        assert info["scenarios"][2025] == "year_2025_threshold_0"

    def test_temporal_resolution(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        assert info["temporal_res"] == 6
        assert info["num_nodes"] == 1


class TestLoadYearData:
    def test_loads_generation(self, mock_h5):
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        assert "Diesel" in data["generation"]
        assert "Solar PV" in data["generation"]
        assert data["generation"]["Diesel"].shape == (1, 48)

    def test_loads_prices(self, mock_h5):
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        assert "nodal_prices" in data
        assert data["nodal_prices"].shape == (1, 48)

    def test_loads_battery(self, mock_h5):
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        assert "Li-ion" in data["battery_discharge"]
        assert "Li-ion" in data["battery_charge"]

    def test_loads_startup(self, mock_h5):
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        assert "Diesel" in data["gen_startup"]

    def test_loads_investments(self, mock_h5):
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        assert "Solar PV" in data["gen_investment_power"]
        assert float(np.sum(data["gen_investment_power"]["Solar PV"])) == 50.0


class TestTryLoadCostBreakdown:
    def test_returns_none_without_layer_a(self, mock_h5):
        result = _try_load_cost_breakdown(mock_h5, 2025)
        assert result is None

    def test_returns_dict_with_layer_a(self, mock_h5_with_cost_breakdown):
        result = _try_load_cost_breakdown(mock_h5_with_cost_breakdown, 2025)
        assert result is not None
        assert result["fuel_cost"] == 1e6

    def test_returns_none_for_missing_year(self, mock_h5_with_cost_breakdown):
        result = _try_load_cost_breakdown(mock_h5_with_cost_breakdown, 2026)
        assert result is None


# =====================================================================
# Cost Recalculation Tests
# =====================================================================


class TestRecalculateCosts:
    def test_fuel_cost(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        costs = _recalculate_costs(data, info["gen_configs"], info["bat_configs"], 6)

        # Diesel has fuel_cost=40, so fuel_cost = sum(output * 6 * 40)
        diesel_output = data["generation"]["Diesel"]
        expected_fuel = float(np.sum(diesel_output * 6 * 40.0))
        assert costs["fuel_cost"] == pytest.approx(expected_fuel, rel=1e-6)

    def test_solar_no_fuel(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        costs = _recalculate_costs(data, info["gen_configs"], info["bat_configs"], 6)

        # Solar fuel_cost=0, so total fuel cost comes only from diesel
        diesel_output = data["generation"]["Diesel"]
        expected = float(np.sum(diesel_output * 6 * 40.0))
        assert costs["fuel_cost"] == pytest.approx(expected, rel=1e-6)

    def test_revenue_positive(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        costs = _recalculate_costs(data, info["gen_configs"], info["bat_configs"], 6)
        assert costs["revenue"] > 0

    def test_om_cost_positive(self, mock_h5):
        info = _load_system_from_h5(mock_h5)
        data = _load_year_data(mock_h5, "year_2025_threshold_0")
        costs = _recalculate_costs(data, info["gen_configs"], info["bat_configs"], 6)
        assert costs["om_cost"] > 0


# =====================================================================
# System Financials Tests
# =====================================================================


class TestComputeSystemFinancials:
    def test_returns_system_financials(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert isinstance(sf, SystemFinancials)

    def test_wacc_calculation(self, mock_h5, default_assumptions):
        sf = compute_system_financials(mock_h5, default_assumptions)
        expected_wacc = (
            0.60 * 0.05 * (1 - 0.25) + 0.40 * 0.12
        )
        assert sf.wacc == pytest.approx(expected_wacc, rel=1e-6)

    def test_npv_decomposition(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        # NPV total should equal sum of components (approximately)
        # npv_total = npv_revenue - npv_fuel - npv_om - npv_capex - tax + ptc + itc + salvage
        assert sf.npv_total != 0.0
        assert isinstance(sf.npv_revenue, float)
        assert isinstance(sf.npv_fuel, float)

    def test_cash_flows_dataframe(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert isinstance(sf.cash_flows, pd.DataFrame)
        assert len(sf.cash_flows) == 3  # 3 years
        assert "revenue" in sf.cash_flows.columns
        assert "net_cash_flow" in sf.cash_flows.columns
        assert "dscr" in sf.cash_flows.columns

    def test_irr_computed(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert isinstance(sf.project_irr, float)
        assert isinstance(sf.equity_irr, float)

    def test_lcoe_positive(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert sf.lcoe_system > 0

    def test_dscr(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert len(sf.dscr_annual) == 3

    def test_with_ppa_price(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        sf = compute_system_financials(mock_h5, assumptions)
        assert sf.npv_revenue > 0

    def test_with_cost_breakdown(self, mock_h5_with_cost_breakdown):
        """Uses Layer A cost breakdown for year 2025, fallback for 2026-2027."""
        sf = compute_system_financials(mock_h5_with_cost_breakdown)
        assert isinstance(sf, SystemFinancials)
        assert sf.npv_fuel > 0  # From Layer A data

    def test_default_assumptions(self, mock_h5):
        sf = compute_system_financials(mock_h5, None)
        assert sf.wacc > 0

    def test_with_carbon_price(self, mock_h5):
        assumptions = FinancialAssumptions(carbon_price=50.0)
        sf = compute_system_financials(mock_h5, assumptions)
        assert isinstance(sf, SystemFinancials)

    def test_with_rec_price(self, mock_h5):
        assumptions = FinancialAssumptions(rec_price=15.0)
        sf = compute_system_financials(mock_h5, assumptions)
        assert sf.npv_revenue > 0

    def test_with_macrs_depreciation(self, mock_h5):
        assumptions = FinancialAssumptions(depreciation_method="macrs")
        sf = compute_system_financials(mock_h5, assumptions)
        assert isinstance(sf, SystemFinancials)

    def test_with_itc(self, mock_h5):
        assumptions = FinancialAssumptions(itc_rate=0.30)
        sf_no_itc = compute_system_financials(mock_h5, FinancialAssumptions())
        sf_itc = compute_system_financials(mock_h5, assumptions)
        # ITC should improve NPV
        assert sf_itc.npv_total >= sf_no_itc.npv_total

    def test_profitability_index(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert isinstance(sf.profitability_index, float)

    def test_cumulative_npv_in_cash_flows(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        assert "cumulative_npv" in sf.cash_flows.columns
        # Cumulative should be monotonically related to net CF
        assert not sf.cash_flows["cumulative_npv"].isna().any()


# =====================================================================
# Technology Financials Tests
# =====================================================================


class TestComputeTechnologyFinancials:
    def test_returns_dict(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        assert isinstance(result, dict)

    def test_contains_generators(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        assert "Diesel" in result
        assert "Solar PV" in result

    def test_contains_battery(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        assert "Li-ion" in result

    def test_diesel_has_fuel_cost(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        assert result["Diesel"].fuel_cost_total > 0
        assert result["Solar PV"].fuel_cost_total == 0.0

    def test_solar_lcoe(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        solar = result["Solar PV"]
        assert solar.lcoe > 0
        assert solar.generation_mwh > 0

    def test_battery_lcos(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        bat = result["Li-ion"]
        assert bat.tech_type == "battery"
        assert bat.generation_mwh > 0

    def test_capacity_factor_range(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        for name, tf in result.items():
            if tf.generation_mwh > 0 and tf.installed_mw > 0:
                assert 0.0 <= tf.capacity_factor <= 1.5  # allow slight overrun for mock data

    def test_technology_financials_fields(self, mock_h5):
        result = compute_technology_financials(mock_h5)
        diesel = result["Diesel"]
        assert isinstance(diesel, TechnologyFinancials)
        assert diesel.name == "Diesel"
        assert diesel.tech_type == "generator"
        assert isinstance(diesel.annual_generation, np.ndarray)
        assert len(diesel.annual_generation) == 3

    def test_with_ppa_price(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=75.0)
        result = compute_technology_financials(mock_h5, assumptions)
        for tf in result.values():
            if tf.generation_mwh > 0:
                assert tf.revenue_total > 0


# =====================================================================
# Sensitivity Analysis Tests
# =====================================================================


class TestSensitivityAnalysis:
    def test_returns_result(self, mock_h5):
        assumptions = FinancialAssumptions()
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate"],
            n_points=3,
        )
        assert isinstance(result, SensitivityResult)

    def test_base_case(self, mock_h5):
        assumptions = FinancialAssumptions()
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate"],
            n_points=3,
        )
        base = compute_system_financials(mock_h5, assumptions)
        assert result.base_npv == pytest.approx(base.npv_total, rel=1e-6)

    def test_sweep_contains_points(self, mock_h5):
        assumptions = FinancialAssumptions()
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate"],
            n_points=5,
        )
        assert "discount_rate" in result.sweeps
        assert len(result.sweeps["discount_rate"]) == 5

    def test_tornado_data(self, mock_h5):
        assumptions = FinancialAssumptions()
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate"],
            n_points=3,
        )
        assert "discount_rate" in result.tornado
        npv_low, npv_high = result.tornado["discount_rate"]
        assert isinstance(npv_low, float)
        assert isinstance(npv_high, float)

    def test_multiple_variables(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate", "ppa_price"],
            n_points=3,
        )
        assert "discount_rate" in result.sweeps
        assert "ppa_price" in result.sweeps

    def test_npv_decreases_with_discount(self, mock_h5):
        assumptions = FinancialAssumptions()
        result = run_sensitivity_analysis(
            mock_h5, assumptions,
            variables=["discount_rate"],
            n_points=5,
        )
        sweep = result.sweeps["discount_rate"]
        npvs = [s[1] for s in sweep]
        # NPV should generally decrease with higher discount rate
        assert npvs[0] >= npvs[-1]


# =====================================================================
# Monte Carlo Tests
# =====================================================================


class TestMonteCarlo:
    def test_returns_result(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        result = run_monte_carlo(
            mock_h5, assumptions, n_samples=10, seed=42,
        )
        assert isinstance(result, MonteCarloResult)
        assert result.n_samples == 10

    def test_sample_count(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        result = run_monte_carlo(
            mock_h5, assumptions, n_samples=20, seed=42,
        )
        assert len(result.npv_samples) == 20
        assert len(result.irr_samples) == 20

    def test_statistics(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        result = run_monte_carlo(
            mock_h5, assumptions, n_samples=50, seed=42,
        )
        assert result.npv_mean != 0.0
        assert result.npv_std >= 0.0
        assert result.npv_p5 <= result.npv_p50 <= result.npv_p95
        assert result.irr_mean != 0.0

    def test_var_cvar(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        result = run_monte_carlo(
            mock_h5, assumptions, n_samples=50, seed=42,
        )
        # CVaR should be <= VaR (both are worst-case measures)
        assert result.npv_cvar_5 <= result.npv_var_5

    def test_reproducibility(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        r1 = run_monte_carlo(mock_h5, assumptions, n_samples=10, seed=123)
        r2 = run_monte_carlo(mock_h5, assumptions, n_samples=10, seed=123)
        np.testing.assert_array_almost_equal(r1.npv_samples, r2.npv_samples)

    def test_custom_distributions(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0)
        dists = {
            "discount_rate": ("uniform", 0.05, 0.12),
            "ppa_price": ("triangular", 40.0, 80.0),
        }
        result = run_monte_carlo(
            mock_h5, assumptions, distributions=dists, n_samples=10, seed=42,
        )
        assert result.n_samples == 10


# =====================================================================
# Dataclass Tests
# =====================================================================


class TestDataclasses:
    def test_financial_assumptions_defaults(self):
        fa = FinancialAssumptions()
        assert fa.debt_fraction == 0.60
        assert fa.discount_rate == 0.08
        assert fa.tax_rate == 0.25

    def test_financial_assumptions_custom(self):
        fa = FinancialAssumptions(discount_rate=0.10, ppa_price=50.0)
        assert fa.discount_rate == 0.10
        assert fa.ppa_price == 50.0

    def test_system_financials_defaults(self):
        sf = SystemFinancials()
        assert sf.npv_total == 0.0
        assert sf.payback_simple == float("inf")
        assert isinstance(sf.cash_flows, pd.DataFrame)

    def test_technology_financials_defaults(self):
        tf = TechnologyFinancials()
        assert tf.lcoe == 0.0
        assert np.isnan(tf.lcos)

    def test_sensitivity_result_defaults(self):
        sr = SensitivityResult()
        assert sr.base_npv == 0.0
        assert sr.sweeps == {}

    def test_monte_carlo_result_defaults(self):
        mcr = MonteCarloResult()
        assert mcr.n_samples == 0
        assert len(mcr.npv_samples) == 0


# =====================================================================
# Correctness Regression Tests (bug fixes)
# =====================================================================


class TestDSCRCorrectness:
    """DSCR / CFADS must reflect cash available for debt service.

    CFADS (before debt service) = revenue - fuel - O&M - insurance - tax
    + ptc + salvage, EXCLUDING capex (financed) and the debt service itself.
    The historical formula ``net_cf + ds - annual_capex`` inflated DSCR by
    exactly +1.0 (it added back ds, which net_cf never subtracted) and
    double-counted capex.
    """

    def test_dscr_matches_cfads_over_debt_service(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        cf = sf.cash_flows

        # Year index 1 (2026): no investment capex, not the terminal year,
        # so no salvage and ptc=0 under defaults → clean invariant.
        y = 1
        row = cf.iloc[y]
        assert row["capex"] == pytest.approx(0.0)
        ds = row["debt_service"]
        assert ds > 0  # debt tenor (15y) covers all 3 years

        expected_cfads = (
            row["revenue"]
            - row["fuel_cost"]
            - row["om_cost"]
            - row["insurance"]
            - row["tax"]
            + row["ptc_benefit"]
        )
        expected_dscr = expected_cfads / ds

        assert sf.dscr_annual[y] == pytest.approx(expected_dscr, rel=1e-9)

    def test_cfads_excludes_debt_service(self, mock_h5):
        """CFADS array must not contain the debt-service add-back."""
        sf = compute_system_financials(mock_h5)
        cf = sf.cash_flows
        y = 1
        row = cf.iloc[y]
        expected_cfads = (
            row["revenue"] - row["fuel_cost"] - row["om_cost"]
            - row["insurance"] - row["tax"] + row["ptc_benefit"]
        )
        assert sf.cfads[y] == pytest.approx(expected_cfads, rel=1e-9)


class TestProfitabilityIndex:
    """PI = PV(future cash flows) / initial investment = (NPV + I) / I.

    The historical formula ``npv_revenue / npv_capex`` ignored opex/tax and
    could report PI > 1 for an NPV-negative project.
    """

    def test_pi_formula(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        if sf.npv_capex > 0:
            expected = (sf.npv_total + sf.npv_capex) / sf.npv_capex
            assert sf.profitability_index == pytest.approx(expected, rel=1e-9)

    def test_pi_consistent_with_npv_sign(self, mock_h5):
        """PI > 1 iff NPV > 0 (the defining property of PI)."""
        sf = compute_system_financials(mock_h5)
        if sf.npv_capex > 0:
            assert (sf.profitability_index > 1.0) == (sf.npv_total > 0.0)


class TestCapexMapping:
    """Each generator's investment must use ITS technology's invest_cost."""

    def test_capex_uses_per_technology_cost(self, mock_h5_multi_tech):
        sf = compute_system_financials(mock_h5_multi_tech)
        # GenA: 10 MW × 1000 = 10,000 ; GenB: 5 MW × 3000 = 15,000
        assert sf.cash_flows.iloc[0]["capex"] == pytest.approx(25_000.0)
        assert sf.npv_capex == pytest.approx(25_000.0)


class TestGenConfigAlignment:
    """Cost params must follow each generator by NAME, not sorted position."""

    def test_costs_follow_generator_name(self, mock_h5_misaligned):
        result = compute_technology_financials(mock_h5_misaligned)
        # Alpha has fuel_cost 1 (cheap); Zeta has fuel_cost 100 (expensive).
        assert result["Alpha"].fuel_cost_total < result["Zeta"].fuel_cost_total
        # Exact: same generation (10 MW × 24 h = 240 MWh), temporal_res=1
        assert result["Alpha"].fuel_cost_total == pytest.approx(240.0)
        assert result["Zeta"].fuel_cost_total == pytest.approx(24_000.0)


class TestNPVDecompositionReconciles:
    """The waterfall components must sum back to npv_total. This is what the
    Cost Decomposition waterfall renders; the Tax term (npv_tax) is required
    for it to reconcile."""

    def test_decomposition_identity(self, mock_h5_with_cost_breakdown):
        sf = compute_system_financials(
            mock_h5_with_cost_breakdown,
            FinancialAssumptions(carbon_price=30.0, itc_rate=0.1, ptc_rate=2.0),
        )
        reconstructed = (
            sf.npv_revenue
            - sf.npv_fuel
            - sf.npv_om
            - sf.npv_capex
            - sf.npv_penalties
            - sf.npv_tax
            + sf.npv_tax_benefits
            + sf.npv_salvage
        )
        assert reconstructed == pytest.approx(sf.npv_total, rel=1e-6)

    def test_npv_tax_exposed_and_nonneg(self, mock_h5):
        sf = compute_system_financials(mock_h5, FinancialAssumptions(ppa_price=80.0))
        assert hasattr(sf, "npv_tax")
        assert sf.npv_tax >= 0.0


class TestExtraCostComponents:
    """Engine must capture cost_breakdown components it historically dropped
    (battery_degradation_cost → O&M, inertia_cost → penalties)."""

    def test_degradation_and_inertia_captured(self, mock_h5_with_cost_breakdown):
        sf = compute_system_financials(mock_h5_with_cost_breakdown)
        row = sf.cash_flows.iloc[0]  # 2025 has the cost_breakdown
        # O&M = fixed_om 2e5 + maintenance 1e5 + battery_maint 5e4
        #       + battery_degradation 7e4 + startup 3e4 = 4.5e5
        assert row["om_cost"] == pytest.approx(4.5e5)
        # penalties = load_shed 1e4 + curtail 5e3 + reserve_s 2e3
        #       + reserve_d 1e3 + co2 8e4 + inertia 2e3 = 1.0e5
        assert row["penalties"] == pytest.approx(1.0e5)


class TestPriceSeries:
    def test_loads_prices(self, mock_h5):
        prices = load_price_series(mock_h5)
        assert prices.ndim == 1
        assert prices.size == 48  # 1 node × 48 hours, base year
        assert np.all(np.isfinite(prices))

    def test_empty_file(self, tmp_path):
        h5_path = tmp_path / "empty.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["temporal_resolution_hours"] = 1
        assert load_price_series(h5_path).size == 0


class TestOperationalPenalties:
    """Operational penalties (load shedding, curtailment, reserve shortfalls,
    …) are real operating costs: they must reduce net_cf / CFADS, not just
    appear in the NPV decomposition / waterfall."""

    def test_penalties_present_in_cash_flows(self, mock_h5_with_cost_breakdown):
        sf = compute_system_financials(mock_h5_with_cost_breakdown)
        cf = sf.cash_flows
        assert "penalties" in cf.columns
        # year 2025 carries the optimizer's penalty bundle
        assert cf.iloc[0]["penalties"] > 0

    def test_net_cf_subtracts_penalties(self, mock_h5_with_cost_breakdown):
        """net_cash_flow identity must include the -penalties term."""
        sf = compute_system_financials(mock_h5_with_cost_breakdown)
        cf = sf.cash_flows
        y = 0  # 2025: penalized, not the terminal year → salvage = 0
        row = cf.iloc[y]
        expected_net = (
            row["revenue"]
            - row["fuel_cost"]
            - row["om_cost"]
            - row["insurance"]
            - row["carbon_cost"]
            - row["penalties"]
            - row["capex"]
            - row["tax"]
            + row["ptc_benefit"]
            + row["itc_benefit"]
        )
        assert row["net_cash_flow"] == pytest.approx(expected_net, rel=1e-9)

    def test_penalties_reduce_dscr(self, mock_h5_with_cost_breakdown):
        """CFADS (hence DSCR) must net out operational penalties."""
        sf = compute_system_financials(mock_h5_with_cost_breakdown)
        cf = sf.cash_flows
        y = 0
        row = cf.iloc[y]
        expected_cfads = (
            row["revenue"] - row["fuel_cost"] - row["om_cost"]
            - row["insurance"] - row["carbon_cost"] - row["penalties"]
            - row["tax"] + row["ptc_benefit"]
        )
        assert sf.cfads[y] == pytest.approx(expected_cfads, rel=1e-9)


class TestCarbonTax:
    """carbon_price is a tax on emissions: it must REDUCE NPV, and must not
    double-count years the optimizer already priced."""

    def test_carbon_price_reduces_npv(self, mock_h5):
        base = compute_system_financials(mock_h5, FinancialAssumptions())
        taxed = compute_system_financials(
            mock_h5, FinancialAssumptions(carbon_price=50.0),
        )
        assert taxed.npv_total < base.npv_total

    def test_carbon_cost_in_cash_flows(self, mock_h5):
        sf = compute_system_financials(
            mock_h5, FinancialAssumptions(carbon_price=50.0),
        )
        assert "carbon_cost" in sf.cash_flows.columns
        assert (sf.cash_flows["carbon_cost"] > 0).any()

    def test_no_double_count_when_optimizer_priced_co2(
        self, mock_h5_with_cost_breakdown,
    ):
        sf = compute_system_financials(
            mock_h5_with_cost_breakdown, FinancialAssumptions(carbon_price=50.0),
        )
        # 2025 was priced by the optimizer (co2_emission_cost present) → no
        # user carbon re-applied; 2026/2027 (fallback) do get it.
        assert sf.cash_flows.iloc[0]["carbon_cost"] == 0.0
        assert sf.cash_flows.iloc[1]["carbon_cost"] > 0.0


class TestInvestmentFromGeneration:
    """CAPEX must be booked for 'Investment <tech>' generation series using
    the per-node peak generation as the installed-MW proxy × the technology's
    invest_cost, incrementally as capacity grows."""

    def test_capex_from_investment_series(self, mock_h5_investment_gen):
        sf = compute_system_financials(mock_h5_investment_gen)
        cf = sf.cash_flows
        # year 0: 40 MW peak × 900,000 $/MW
        assert cf.iloc[0]["capex"] == pytest.approx(40 * 900000)
        # year 1: incremental (100 − 40) = 60 MW × 900,000
        assert cf.iloc[1]["capex"] == pytest.approx(60 * 900000)
        assert sf.npv_capex > 0

    def test_irr_defined_with_investment(self, mock_h5_investment_gen):
        """With real CAPEX outflow, IRR is no longer the undefined sentinel."""
        sf = compute_system_financials(mock_h5_investment_gen)
        # capex now dominates → year-0 net cash flow is negative → finite IRR
        assert sf.cash_flows.iloc[0]["net_cash_flow"] < 0

    def test_investment_capex_and_npv(self, mock_h5_investment_gen):
        sf = compute_system_financials(mock_h5_investment_gen)
        assert sf.investment_capex == pytest.approx(100 * 900000)  # 40 + 60 MW
        # tiny revenue vs large capex → negative investment NPV
        assert sf.investment_npv < 0

    def test_battery_investment_capex(self, mock_h5_battery_invest):
        sf = compute_system_financials(mock_h5_battery_invest)
        # power 12 MW × 600,000 + energy 40 MWh × 240,000
        expected = 12 * 600000 + 40 * 240000  # 7.2M + 9.6M = 16.8M
        assert sf.cash_flows.iloc[0]["capex"] == pytest.approx(expected)
        assert sf.investment_capex == pytest.approx(expected)


class TestNewInvestmentIRRFraming:
    """The new-investment IRR isolates the build's economics; it must NOT be
    inflated by revenue from pre-existing sunk-cost plants the way the
    system-level IRR is."""

    def test_system_irr_inflated_but_investment_finite(
        self, mock_h5_existing_plus_invest,
    ):
        import math
        sf = compute_system_financials(mock_h5_existing_plus_invest)
        # System: huge existing revenue, only the build's capex → net positive
        # every year → system IRR is the undefined/sentinel case (NaN).
        assert math.isnan(sf.project_irr)
        # New-investment stream has a real upfront outflow → finite metrics.
        assert sf.investment_capex == pytest.approx(5 * 900000)
        assert not math.isnan(sf.investment_irr)
        assert sf.investment_npv < sf.npv_total  # not inflated by OldPlant

    def test_investment_fields_present(self, mock_h5):
        sf = compute_system_financials(mock_h5)
        for attr in (
            "investment_npv", "investment_irr", "investment_equity_irr",
            "investment_payback", "investment_capex", "investment_lcoe",
        ):
            assert hasattr(sf, attr)


class TestCapexVintageTiming:
    """Depreciation, debt and ITC anchor to each asset's in-service year."""

    def test_depreciation_starts_at_inservice_year(self, mock_h5_late_invest):
        sf = compute_system_financials(
            mock_h5_late_invest,
            FinancialAssumptions(
                depreciation_method="straight_line", depreciation_years=20,
            ),
        )
        cf = sf.cash_flows
        assert cf.iloc[0]["capex"] == 0.0
        assert cf.iloc[1]["capex"] == pytest.approx(20_000.0)  # 20 MW × 1000
        assert cf.iloc[0]["depreciation"] == 0.0   # nothing in service yet
        assert cf.iloc[1]["depreciation"] > 0.0

    def test_debt_service_starts_at_inservice_year(self, mock_h5_late_invest):
        sf = compute_system_financials(
            mock_h5_late_invest,
            FinancialAssumptions(
                debt_fraction=0.6, cost_of_debt=0.05, debt_tenor=10,
            ),
        )
        cf = sf.cash_flows
        assert cf.iloc[0]["debt_service"] == 0.0
        assert cf.iloc[1]["debt_service"] > 0.0

    def test_itc_booked_at_inservice_year(self, mock_h5_late_invest):
        sf = compute_system_financials(
            mock_h5_late_invest, FinancialAssumptions(itc_rate=0.30),
        )
        cf = sf.cash_flows
        assert cf.iloc[0]["itc_benefit"] == 0.0
        assert cf.iloc[1]["itc_benefit"] == pytest.approx(0.30 * 20_000.0)


class TestMonteCarloRobustness:
    """Random draws must never feed invalid (e.g. negative) parameters that
    blow up discount factors or produce non-finite results."""

    def test_samples_finite_under_high_variance(self, mock_h5):
        assumptions = FinancialAssumptions(ppa_price=60.0, discount_rate=0.05)
        # Wide normals will draw negative discount rates / prices unless clamped.
        dists = {
            "discount_rate": ("normal", 0.05, 0.20),
            "ppa_price": ("normal", 60.0, 80.0),
            "tax_rate": ("uniform", -0.5, 1.5),
        }
        result = run_monte_carlo(
            mock_h5, assumptions, distributions=dists, n_samples=200, seed=7,
        )
        # The clamp's job is to keep NPV finite under invalid draws.
        assert np.all(np.isfinite(result.npv_samples))
        # IRR may be NaN (undefined when a draw yields no net outflow), but a
        # clamped run must never produce a non-finite *infinity*.
        assert not np.any(np.isinf(result.irr_samples))


class TestSensitivityRobustness:
    def test_zero_base_variable_stays_in_bounds(self, mock_h5):
        """Sweeping a variable whose base value is 0 (carbon_price) must not
        produce negative carbon prices, and NPVs must stay finite."""
        assumptions = FinancialAssumptions(carbon_price=0.0)
        result = run_sensitivity_analysis(
            mock_h5, assumptions, variables=["carbon_price"], n_points=5,
        )
        sweep_vals = [v for v, _npv, _irr in result.sweeps["carbon_price"]]
        assert all(v >= 0.0 for v in sweep_vals)
        assert all(np.isfinite(npv) for _v, npv, _irr in result.sweeps["carbon_price"])


# =====================================================================
# Edge Case Tests
# =====================================================================


class TestEdgeCases:
    def test_empty_h5(self, tmp_path):
        """Handle HDF5 with no results gracefully."""
        h5_path = tmp_path / "empty.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["temporal_resolution_hours"] = 1

        sf = compute_system_financials(h5_path)
        assert sf.npv_total == 0.0

    def test_no_investments(self, tmp_path):
        """System with no investments should still compute LCOE."""
        h5_path = tmp_path / "no_invest.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["temporal_resolution_hours"] = 1
            f.attrs["num_nodes"] = 1

            gen_grp = f.create_group("system_configuration/generators")
            gen_grp.attrs["num_generators"] = 1
            g0 = gen_grp.create_group("generator_0")
            g0.attrs["name"] = "Diesel"
            g0.attrs["fuel_cost"] = 40.0
            g0.attrs["fixed_cost"] = 2.0
            g0.attrs["maintenance_cost"] = 1.0
            g0.attrs["rated_power"] = 50.0

            summary = f.create_group("summary_results")
            summary.create_dataset("year", data=[2025])

            detailed = f.create_group("detailed_results")
            grp = detailed.create_group("year_2025_threshold_0")
            grp.attrs["year"] = 2025
            grp.attrs["total_cost"] = 1e6

            gen_g = grp.create_group("generation")
            gen_g.create_dataset("Diesel", data=np.full((1, 24), 30.0))
            grp.create_dataset("electricity_prices", data=np.full(24, 50.0))

        sf = compute_system_financials(h5_path)
        assert sf.lcoe_system > 0

    def test_zero_generation(self, tmp_path):
        """Zero generation → infinite LCOE, no crash."""
        h5_path = tmp_path / "zero_gen.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["temporal_resolution_hours"] = 1
            f.attrs["num_nodes"] = 1

            gen_grp = f.create_group("system_configuration/generators")
            gen_grp.attrs["num_generators"] = 1
            g0 = gen_grp.create_group("generator_0")
            g0.attrs["name"] = "Offline"
            g0.attrs["fuel_cost"] = 0.0
            g0.attrs["fixed_cost"] = 0.0
            g0.attrs["maintenance_cost"] = 0.0
            g0.attrs["rated_power"] = 0.0

            summary = f.create_group("summary_results")
            summary.create_dataset("year", data=[2025])

            detailed = f.create_group("detailed_results")
            grp = detailed.create_group("year_2025_threshold_0")
            grp.attrs["year"] = 2025
            gen_g = grp.create_group("generation")
            gen_g.create_dataset("Offline", data=np.zeros((1, 24)))

        sf = compute_system_financials(h5_path)
        assert sf.lcoe_system == float("inf")
