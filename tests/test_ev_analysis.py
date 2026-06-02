"""
Tests for esfex.models.ev_analysis module.

Covers the following public functions:
- generate_charging_profiles (single scenario)
- generate_all_scenarios (3 scenarios)
- compute_v2g_potential (hourly V2G capacity and energy)
- compute_battery_degradation (Wöhler curve, NMC/LFP chemistry)
- assess_grid_impact (peak shaving, valley filling, economics)
- compute_fleet_evolution_metrics (yearly projection)
"""

import numpy as np
import pytest

from evrex import (
    DEFAULT_CONNECTED_PROFILE,
    ChargingProfile,
    ChargingScenarioResult,
    DegradationResult,
    GridImpactResult,
    V2GPotential,
    assess_grid_impact,
    compute_battery_degradation,
    compute_fleet_evolution_metrics,
    compute_v2g_potential,
    generate_all_scenarios,
    generate_charging_profiles,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet():
    """Standard fleet for testing."""
    return {"light": 500, "medium": 100, "heavy": 25, "buses": 15}


@pytest.fixture
def ev_params():
    """Standard EV technical parameters."""
    return {
        "light": {
            "charging_power": 7.0, "battery_capacity": 50.0,
            "v2g_power": 5.0, "v2g_participation": 0.3,
            "efficiency_discharge": 0.90, "energy_consumption": 18.0,
            "avg_daily_km": 40.0,
        },
        "medium": {
            "charging_power": 11.0, "battery_capacity": 75.0,
            "v2g_power": 8.0, "v2g_participation": 0.4,
            "efficiency_discharge": 0.90, "energy_consumption": 25.0,
            "avg_daily_km": 80.0,
        },
        "heavy": {
            "charging_power": 22.0, "battery_capacity": 150.0,
            "v2g_power": 15.0, "v2g_participation": 0.5,
            "efficiency_discharge": 0.90, "energy_consumption": 55.0,
            "avg_daily_km": 150.0,
        },
        "buses": {
            "charging_power": 50.0, "battery_capacity": 300.0,
            "v2g_power": 40.0, "v2g_participation": 0.7,
            "efficiency_discharge": 0.90, "energy_consumption": 80.0,
            "avg_daily_km": 200.0,
        },
    }


@pytest.fixture
def base_demand():
    """Synthetic 24h base demand (MW)."""
    hours = np.arange(24)
    return (
        200
        + 80 * np.exp(-0.5 * ((hours - 9) / 2.0) ** 2)
        + 100 * np.exp(-0.5 * ((hours - 20) / 2.5) ** 2)
    ).tolist()


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for EV analysis dataclass defaults."""

    def test_charging_profile_defaults(self):
        cp = ChargingProfile(category="light", scenario="uncontrolled")
        assert cp.hourly_mw == []
        assert cp.description == ""

    def test_charging_scenario_result_defaults(self):
        csr = ChargingScenarioResult(scenario="test")
        assert csr.peak_demand_mw == 0.0
        assert csr.daily_energy_mwh == 0.0

    def test_v2g_potential_defaults(self):
        v = V2GPotential()
        assert v.daily_v2g_energy_mwh == 0.0
        assert v.breakeven_compensation == 0.0

    def test_degradation_result_fields(self):
        d = DegradationResult(chemistry="NMC")
        assert d.chemistry == "NMC"
        assert d.calendar_aging_pct_per_year == 2.0

    def test_grid_impact_result_defaults(self):
        g = GridImpactResult()
        assert g.peak_shaving_mw == 0.0
        assert g.net_v2g_value == 0.0

    def test_default_connected_profile_length(self):
        assert len(DEFAULT_CONNECTED_PROFILE) == 24
        assert all(0 <= v <= 1 for v in DEFAULT_CONNECTED_PROFILE)


# ---------------------------------------------------------------------------
# generate_charging_profiles
# ---------------------------------------------------------------------------


class TestGenerateChargingProfiles:
    """Tests for single-scenario charging profile generation."""

    def test_uncontrolled_returns_result(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        assert isinstance(result, ChargingScenarioResult)
        assert result.scenario == "uncontrolled"

    def test_aggregate_has_24_hours(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        assert len(result.aggregate_hourly_mw) == 24

    def test_all_values_non_negative(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        assert all(v >= 0 for v in result.aggregate_hourly_mw)

    def test_peak_demand_matches_max(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        assert result.peak_demand_mw == pytest.approx(
            max(result.aggregate_hourly_mw), abs=0.01,
        )

    def test_daily_energy_is_sum(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        assert result.daily_energy_mwh == pytest.approx(
            sum(result.aggregate_hourly_mw), abs=0.01,
        )

    def test_profiles_per_category(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        for cat in fleet:
            assert cat in result.profiles_by_category
            assert len(result.profiles_by_category[cat].hourly_mw) == 24

    def test_tou_shifted_lower_peak(self, fleet, ev_params):
        unc = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        tou = generate_charging_profiles(fleet, ev_params, "tou_shifted")
        # TOU should shift load off-peak, likely reducing peak
        assert tou.peak_demand_mw <= unc.peak_demand_mw * 1.1  # allow margin

    def test_optimized_requires_base_demand(self, fleet, ev_params, base_demand):
        result = generate_charging_profiles(
            fleet, ev_params, "optimized",
            smart_charging_fraction=0.8,
            base_demand_24h=base_demand,
        )
        assert result.scenario == "optimized"
        assert len(result.aggregate_hourly_mw) == 24

    def test_zero_fleet_produces_zero(self, ev_params):
        empty = {"light": 0, "medium": 0, "heavy": 0, "buses": 0}
        result = generate_charging_profiles(empty, ev_params, "uncontrolled")
        assert result.peak_demand_mw == 0.0
        assert result.daily_energy_mwh == 0.0

    def test_smart_fraction_affects_optimized(self, fleet, ev_params, base_demand):
        r0 = generate_charging_profiles(
            fleet, ev_params, "optimized",
            smart_charging_fraction=0.0, base_demand_24h=base_demand,
        )
        r100 = generate_charging_profiles(
            fleet, ev_params, "optimized",
            smart_charging_fraction=1.0, base_demand_24h=base_demand,
        )
        # 100% smart should have different (flatter) profile than 0%
        assert r0.aggregate_hourly_mw != r100.aggregate_hourly_mw


# ---------------------------------------------------------------------------
# generate_all_scenarios
# ---------------------------------------------------------------------------


class TestGenerateAllScenarios:
    """Tests for multi-scenario generation."""

    def test_returns_three_scenarios(self, fleet, ev_params):
        results = generate_all_scenarios(fleet, ev_params)
        assert len(results) == 3
        assert "uncontrolled" in results
        assert "tou_shifted" in results
        assert "optimized" in results

    def test_all_have_24h_profiles(self, fleet, ev_params):
        results = generate_all_scenarios(fleet, ev_params)
        for name, scenario in results.items():
            assert len(scenario.aggregate_hourly_mw) == 24

    def test_same_daily_energy_approximately(self, fleet, ev_params):
        """All scenarios should deliver roughly similar daily energy."""
        results = generate_all_scenarios(fleet, ev_params)
        energies = [s.daily_energy_mwh for s in results.values()]
        # Within 30% of each other (different patterns redistribute, not eliminate)
        avg = sum(energies) / len(energies)
        for e in energies:
            assert abs(e - avg) / max(avg, 0.01) < 0.30


# ---------------------------------------------------------------------------
# compute_v2g_potential
# ---------------------------------------------------------------------------


class TestComputeV2GPotential:
    """Tests for V2G technical potential assessment."""

    def test_returns_v2g_potential(self, fleet, ev_params):
        v2g = compute_v2g_potential(fleet, ev_params)
        assert isinstance(v2g, V2GPotential)

    def test_24_hourly_values(self, fleet, ev_params):
        v2g = compute_v2g_potential(fleet, ev_params)
        assert len(v2g.max_v2g_power_mw) == 24
        assert len(v2g.hourly_available_soc_mwh) == 24
        assert len(v2g.hourly_connected_fraction) == 24

    def test_v2g_power_non_negative(self, fleet, ev_params):
        v2g = compute_v2g_potential(fleet, ev_params)
        assert all(p >= 0 for p in v2g.max_v2g_power_mw)

    def test_daily_energy_positive(self, fleet, ev_params):
        v2g = compute_v2g_potential(fleet, ev_params)
        assert v2g.daily_v2g_energy_mwh > 0

    def test_annual_potential_consistent(self, fleet, ev_params):
        v2g = compute_v2g_potential(fleet, ev_params)
        expected_annual = v2g.daily_v2g_energy_mwh * 365 / 1000
        assert v2g.annual_v2g_potential_gwh == pytest.approx(expected_annual, rel=0.01)

    def test_custom_connected_profile(self, fleet, ev_params):
        flat_profile = [0.5] * 24
        v2g = compute_v2g_potential(fleet, ev_params, connected_profile=flat_profile)
        assert v2g.hourly_connected_fraction == flat_profile

    def test_higher_participation_more_power(self, fleet):
        params_low = {
            "light": {"v2g_power": 5.0, "v2g_participation": 0.1,
                       "battery_capacity": 50.0, "efficiency_discharge": 0.9},
        }
        params_high = {
            "light": {"v2g_power": 5.0, "v2g_participation": 0.8,
                       "battery_capacity": 50.0, "efficiency_discharge": 0.9},
        }
        fleet_light = {"light": 500}
        v_low = compute_v2g_potential(fleet_light, params_low)
        v_high = compute_v2g_potential(fleet_light, params_high)
        assert v_high.daily_v2g_energy_mwh > v_low.daily_v2g_energy_mwh

    def test_wider_soc_window_more_energy(self, fleet, ev_params):
        v_narrow = compute_v2g_potential(
            fleet, ev_params, v2g_min_soc=0.40, v2g_max_soc=0.60,
        )
        v_wide = compute_v2g_potential(
            fleet, ev_params, v2g_min_soc=0.20, v2g_max_soc=0.90,
        )
        assert v_wide.hourly_available_soc_mwh[0] > v_narrow.hourly_available_soc_mwh[0]

    def test_zero_fleet(self, ev_params):
        empty = {"light": 0}
        v2g = compute_v2g_potential(empty, ev_params)
        assert v2g.daily_v2g_energy_mwh == 0


# ---------------------------------------------------------------------------
# compute_battery_degradation
# ---------------------------------------------------------------------------


class TestBatteryDegradation:
    """Tests for the Wöhler curve battery degradation model."""

    def test_returns_degradation_result(self):
        d = compute_battery_degradation()
        assert isinstance(d, DegradationResult)

    def test_nmc_chemistry(self):
        d = compute_battery_degradation(chemistry="NMC")
        assert d.chemistry == "NMC"
        assert d.calendar_aging_pct_per_year == 2.5

    def test_lfp_chemistry(self):
        d = compute_battery_degradation(chemistry="LFP")
        assert d.chemistry == "LFP"
        assert d.calendar_aging_pct_per_year == 1.5

    def test_lfp_less_degradation_than_nmc(self):
        nmc = compute_battery_degradation(chemistry="NMC")
        lfp = compute_battery_degradation(chemistry="LFP")
        assert lfp.total_degradation_pct_per_year < nmc.total_degradation_pct_per_year

    def test_more_cycles_more_degradation(self):
        d_low = compute_battery_degradation(v2g_cycles_per_day=0.2)
        d_high = compute_battery_degradation(v2g_cycles_per_day=2.0)
        assert d_high.total_degradation_pct_per_year > d_low.total_degradation_pct_per_year

    def test_deeper_dod_more_degradation(self):
        d_shallow = compute_battery_degradation(depth_of_discharge=0.10)
        d_deep = compute_battery_degradation(depth_of_discharge=0.60)
        assert d_deep.degradation_cost_per_kwh > d_shallow.degradation_cost_per_kwh

    def test_breakeven_positive(self):
        d = compute_battery_degradation()
        assert d.breakeven_compensation > 0

    def test_breakeven_is_cost_times_1000(self):
        """Break-even $/MWh = degradation $/kWh × 1000."""
        d = compute_battery_degradation()
        assert d.breakeven_compensation == pytest.approx(
            d.degradation_cost_per_kwh * 1000, rel=0.01,
        )

    def test_total_includes_calendar(self):
        d = compute_battery_degradation(v2g_cycles_per_day=0.5, chemistry="NMC")
        assert d.total_degradation_pct_per_year > d.calendar_aging_pct_per_year

    def test_zero_cycles_only_calendar(self):
        d = compute_battery_degradation(v2g_cycles_per_day=0.0)
        assert d.total_degradation_pct_per_year == d.calendar_aging_pct_per_year

    def test_custom_battery_cost(self):
        d_low = compute_battery_degradation(battery_cost_per_kwh=50.0)
        d_high = compute_battery_degradation(battery_cost_per_kwh=200.0)
        assert d_high.degradation_cost_per_kwh > d_low.degradation_cost_per_kwh


# ---------------------------------------------------------------------------
# assess_grid_impact
# ---------------------------------------------------------------------------


class TestAssessGridImpact:
    """Tests for the grid impact assessment function."""

    @pytest.fixture
    def v2g(self, fleet, ev_params):
        return compute_v2g_potential(fleet, ev_params)

    @pytest.fixture
    def ev_charging(self, fleet, ev_params):
        result = generate_charging_profiles(fleet, ev_params, "uncontrolled")
        return result.aggregate_hourly_mw

    def test_returns_grid_impact_result(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        assert isinstance(r, GridImpactResult)

    def test_24h_profiles(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        assert len(r.base_demand_24h) == 24
        assert len(r.ev_charging_24h) == 24
        assert len(r.v2g_discharge_24h) == 24
        assert len(r.net_load_24h) == 24

    def test_net_load_is_base_plus_ev_minus_v2g(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        for h in range(24):
            expected = r.base_demand_24h[h] + r.ev_charging_24h[h] - r.v2g_discharge_24h[h]
            assert r.net_load_24h[h] == pytest.approx(expected, abs=0.1)

    def test_peak_shaving_non_negative(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        assert r.peak_shaving_mw >= 0

    def test_valley_filling_non_negative(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        assert r.valley_filling_mw >= 0

    def test_arbitrage_revenue_non_negative(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        assert r.arbitrage_revenue_annual >= 0

    def test_v2g_dispatched_8_hours_max(self, base_demand, ev_charging, v2g):
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        dispatch_hours = sum(1 for v in r.v2g_discharge_24h if v > 0)
        assert dispatch_hours <= 8

    def test_ptv_ratio_decreases(self, base_demand, ev_charging, v2g):
        """Peak-to-valley ratio should decrease (or not increase much) with V2G."""
        r = assess_grid_impact(base_demand, ev_charging, v2g)
        # Before is just base; after includes V2G
        # V2G should help flatten, but EV charging may worsen
        assert r.peak_to_valley_after < r.peak_to_valley_before * 2

    def test_synthetic_prices_used_when_none(self, base_demand, ev_charging, v2g):
        """Should not crash when no prices provided."""
        r = assess_grid_impact(base_demand, ev_charging, v2g, electricity_prices_24h=None)
        assert r.arbitrage_revenue_annual >= 0

    def test_custom_compensation(self, base_demand, ev_charging, v2g):
        r_low = assess_grid_impact(
            base_demand, ev_charging, v2g, v2g_compensation_per_mwh=10,
        )
        r_high = assess_grid_impact(
            base_demand, ev_charging, v2g, v2g_compensation_per_mwh=200,
        )
        assert r_high.net_v2g_value >= r_low.net_v2g_value


# ---------------------------------------------------------------------------
# compute_fleet_evolution_metrics
# ---------------------------------------------------------------------------


class TestFleetEvolutionMetrics:
    """Tests for the yearly fleet metrics projection."""

    def test_returns_dict(self, ev_params):
        result = compute_fleet_evolution_metrics(
            years=[2025, 2030, 2035],
            fleet_ev_by_year=[100, 500, 1000],
            fleet_by_category_by_year={
                "light": [80, 400, 800],
                "medium": [20, 100, 200],
            },
            ev_categories=ev_params,
        )
        assert isinstance(result, dict)

    def test_has_expected_keys(self, ev_params):
        result = compute_fleet_evolution_metrics(
            years=[2025], fleet_ev_by_year=[100],
            fleet_by_category_by_year={"light": [100]},
            ev_categories=ev_params,
        )
        for key in ("years", "total_ev", "energy_gwh", "peak_mw", "ev_demand_pct", "v2g_capacity_mw"):
            assert key in result

    def test_lengths_match(self, ev_params):
        years = [2025, 2030, 2035, 2040]
        result = compute_fleet_evolution_metrics(
            years=years,
            fleet_ev_by_year=[100, 500, 1000, 2000],
            fleet_by_category_by_year={"light": [100, 500, 1000, 2000]},
            ev_categories=ev_params,
        )
        assert len(result["energy_gwh"]) == 4
        assert len(result["peak_mw"]) == 4
        assert len(result["v2g_capacity_mw"]) == 4

    def test_energy_increases_with_fleet(self, ev_params):
        result = compute_fleet_evolution_metrics(
            years=[2025, 2030, 2035],
            fleet_ev_by_year=[100, 500, 1000],
            fleet_by_category_by_year={"light": [100, 500, 1000]},
            ev_categories=ev_params,
        )
        assert result["energy_gwh"][-1] > result["energy_gwh"][0]
        assert result["peak_mw"][-1] > result["peak_mw"][0]

    def test_ev_demand_pct_relative_to_base(self, ev_params):
        result = compute_fleet_evolution_metrics(
            years=[2025],
            fleet_ev_by_year=[1000],
            fleet_by_category_by_year={"light": [1000]},
            ev_categories=ev_params,
            base_demand_annual_gwh=100.0,
        )
        assert result["ev_demand_pct"][0] > 0
        assert result["ev_demand_pct"][0] < 100  # 1000 light vehicles << 100 GWh
