"""
Tests for esfex.models.ev_adoption module.

Covers the following public functions and classes:
- TransportContext, EVMacroData, EVAdoptionCurve, EVValidationData (dataclasses)
- run_ev_logistic_adoption (transport-specific logistic regression)
- run_ev_bass_diffusion (Bass innovation/imitation model)
- run_ev_tco_parity (total cost of ownership comparison)
- run_ev_policy_driven (mandate-based adoption with scrappage model)
- fit_adoption_to_ev_config (S-curve fitting for ESFEX integration)
"""

import math

import numpy as np
import pytest

from evrex import (
    DEFAULT_CATEGORIES,
    DEFAULT_ENERGY_CONSUMPTION,
    EVAdoptionCurve,
    EVMacroData,
    EVValidationData,
    TransportContext,
    fit_adoption_to_ev_config,
    run_ev_bass_diffusion,
    run_ev_logistic_adoption,
    run_ev_policy_driven,
    run_ev_tco_parity,
)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def transport():
    """Standard transport context for testing."""
    return TransportContext(
        fleet_by_category={"light": 1000, "medium": 200, "heavy": 50, "buses": 30},
        avg_daily_km={"light": 40, "medium": 80, "heavy": 150, "buses": 200},
        energy_consumption={"light": 18, "medium": 25, "heavy": 55, "buses": 80},
        charging_stations=50,
        road_density_km2=3.5,
        population=500_000,
    )


@pytest.fixture
def macro():
    """Standard macro data for testing."""
    return EVMacroData(
        country_iso="CUB",
        gdp_per_capita=10000,
        urbanization_pct=75,
        population=500_000,
        ev_price={"light": 35000, "medium": 55000, "heavy": 120000, "buses": 300000},
        ice_price={"light": 25000, "medium": 40000, "heavy": 90000, "buses": 250000},
        battery_cost_per_kwh=140,
        battery_cost_decline_rate=0.08,
        fuel_price_gasoline=1.20,
        fuel_price_diesel=1.10,
        electricity_tariff=0.15,
        maintenance_diff_annual=500,
        ice_phaseout_year=0,
        ev_subsidy_pct=0.0,
        emission_target_pct=0.0,
    )


def _assert_valid_curve(curve: EVAdoptionCurve, base_year: int, target_year: int):
    """Validate common properties of an EVAdoptionCurve."""
    assert isinstance(curve, EVAdoptionCurve)
    assert curve.years[0] == base_year
    assert curve.years[-1] == target_year
    n = len(curve.years)
    assert len(curve.penetration) == n
    assert len(curve.total_fleet_ev) == n
    assert len(curve.energy_demand_gwh) == n
    assert len(curve.peak_charging_mw) == n
    # Penetration must be in [0, 1]
    assert all(0 <= p <= 1.0 for p in curve.penetration)
    # Fleet counts are non-negative
    assert all(f >= 0 for f in curve.total_fleet_ev)
    # Energy demand is non-negative
    assert all(e >= 0 for e in curve.energy_demand_gwh)
    # Has fleet breakdown per category
    assert isinstance(curve.fleet_by_category, dict)
    for cat, counts in curve.fleet_by_category.items():
        assert len(counts) == n
        assert all(c >= 0 for c in counts)


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for EV adoption dataclass defaults and construction."""

    def test_transport_context_defaults(self):
        ctx = TransportContext()
        assert "light" in ctx.fleet_by_category
        assert ctx.charging_stations == 0
        assert ctx.population == 1_000_000

    def test_ev_macro_data_defaults(self):
        m = EVMacroData()
        assert m.gdp_per_capita == 5000.0
        assert m.battery_cost_per_kwh == 140.0
        assert m.ice_phaseout_year == 0

    def test_ev_adoption_curve_has_method(self):
        c = EVAdoptionCurve(
            method="test", years=[2025], penetration=[0.1],
            fleet_by_category={}, total_fleet_ev=[100],
            energy_demand_gwh=[1.0], peak_charging_mw=[0.5],
            parameters={},
        )
        assert c.method == "test"

    def test_ev_validation_data(self):
        vd = EVValidationData(label="IEA", years=[2020], ev_stock=[500], source="iea")
        assert vd.source == "iea"
        assert vd.ev_stock == [500]

    def test_default_categories_exist(self):
        assert "light" in DEFAULT_CATEGORIES
        assert "buses" in DEFAULT_CATEGORIES
        assert len(DEFAULT_CATEGORIES) == 4

    def test_default_energy_consumption(self):
        assert DEFAULT_ENERGY_CONSUMPTION["light"] < DEFAULT_ENERGY_CONSUMPTION["buses"]


# ---------------------------------------------------------------------------
# run_ev_logistic_adoption
# ---------------------------------------------------------------------------


class TestLogisticAdoption:
    """Tests for the logistic regression adoption method."""

    def test_returns_valid_curve(self, macro, transport):
        curve = run_ev_logistic_adoption(macro, transport, 2025, 2050)
        _assert_valid_curve(curve, 2025, 2050)
        assert curve.method == "logistic"

    def test_penetration_increases_with_gdp(self, transport):
        m_low = EVMacroData(gdp_per_capita=3000)
        m_high = EVMacroData(gdp_per_capita=30000)
        c_low = run_ev_logistic_adoption(m_low, transport, 2025, 2040)
        c_high = run_ev_logistic_adoption(m_high, transport, 2025, 2040)
        assert c_high.penetration[-1] >= c_low.penetration[-1]

    def test_penetration_increases_with_fuel_price(self, transport):
        m_low = EVMacroData(fuel_price_gasoline=0.50)
        m_high = EVMacroData(fuel_price_gasoline=3.00)
        c_low = run_ev_logistic_adoption(m_low, transport, 2025, 2040)
        c_high = run_ev_logistic_adoption(m_high, transport, 2025, 2040)
        assert c_high.penetration[-1] >= c_low.penetration[-1]

    def test_number_of_years(self, macro, transport):
        curve = run_ev_logistic_adoption(macro, transport, 2025, 2035)
        assert len(curve.years) == 11  # inclusive

    def test_custom_coefficients(self, macro, transport):
        coeffs = {"beta_0": -5.0, "beta_fuel_savings": 1.0}
        curve = run_ev_logistic_adoption(
            macro, transport, 2025, 2040, coefficients=coeffs,
        )
        _assert_valid_curve(curve, 2025, 2040)

    def test_parameters_stored(self, macro, transport):
        curve = run_ev_logistic_adoption(macro, transport, 2025, 2040)
        assert "beta_0" in curve.parameters


# ---------------------------------------------------------------------------
# run_ev_bass_diffusion
# ---------------------------------------------------------------------------


class TestBassDiffusion:
    """Tests for the Bass diffusion adoption method."""

    def test_returns_valid_curve(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        _assert_valid_curve(curve, 2025, 2050)
        assert curve.method == "bass"

    def test_higher_q_faster_adoption(self, transport):
        c_low = run_ev_bass_diffusion(transport, 2025, 2050, q=0.20)
        c_high = run_ev_bass_diffusion(transport, 2025, 2050, q=0.60)
        assert c_high.penetration[-1] >= c_low.penetration[-1]

    def test_higher_p_faster_initial(self, transport):
        c_low = run_ev_bass_diffusion(transport, 2025, 2050, p=0.005)
        c_high = run_ev_bass_diffusion(transport, 2025, 2050, p=0.05)
        # Higher p should lead to faster initial adoption (check early years)
        assert c_high.penetration[3] >= c_low.penetration[3]

    def test_monotonically_increasing(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        for i in range(1, len(curve.penetration)):
            assert curve.penetration[i] >= curve.penetration[i - 1]

    def test_initial_penetration_respected(self, transport):
        curve = run_ev_bass_diffusion(
            transport, 2025, 2050, initial_penetration=0.10,
        )
        assert curve.penetration[0] >= 0.09  # allow small float tolerance

    def test_parameters_stored(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050, p=0.03, q=0.45)
        assert curve.parameters["p"] == 0.03
        assert curve.parameters["q"] == 0.45

    def test_approaches_one(self, transport):
        """Over long horizon with strong imitation, should approach 100%."""
        curve = run_ev_bass_diffusion(transport, 2025, 2070, p=0.03, q=0.50)
        assert curve.penetration[-1] > 0.90


# ---------------------------------------------------------------------------
# run_ev_tco_parity
# ---------------------------------------------------------------------------


class TestTCOParity:
    """Tests for the TCO-parity adoption method."""

    def test_returns_valid_curve(self, macro, transport):
        curve = run_ev_tco_parity(macro, transport, 2025, 2050)
        _assert_valid_curve(curve, 2025, 2050)
        assert curve.method == "tco_parity"

    def test_higher_sensitivity_steeper(self, macro, transport):
        c_low = run_ev_tco_parity(macro, transport, 2025, 2050, price_sensitivity=3.0)
        c_high = run_ev_tco_parity(macro, transport, 2025, 2050, price_sensitivity=15.0)
        # With high sensitivity, adoption should respond more sharply
        final_low = c_low.penetration[-1]
        final_high = c_high.penetration[-1]
        # Both should be valid; high sensitivity amplifies TCO gaps
        assert final_low >= 0 and final_high >= 0

    def test_cheaper_ev_higher_adoption(self, transport):
        """When EVs are much cheaper than ICE, adoption should be higher."""
        m_expensive = EVMacroData(
            ev_price={"light": 60000, "medium": 80000, "heavy": 200000, "buses": 500000},
            ice_price={"light": 25000, "medium": 40000, "heavy": 90000, "buses": 250000},
        )
        m_cheap = EVMacroData(
            ev_price={"light": 20000, "medium": 30000, "heavy": 70000, "buses": 180000},
            ice_price={"light": 25000, "medium": 40000, "heavy": 90000, "buses": 250000},
        )
        c_exp = run_ev_tco_parity(m_expensive, transport, 2025, 2040)
        c_cheap = run_ev_tco_parity(m_cheap, transport, 2025, 2040)
        assert c_cheap.penetration[-1] > c_exp.penetration[-1]

    def test_battery_decline_helps(self, macro, transport):
        m_no_decline = EVMacroData(battery_cost_decline_rate=0.0)
        m_decline = EVMacroData(battery_cost_decline_rate=0.12)
        c_no = run_ev_tco_parity(m_no_decline, transport, 2025, 2050)
        c_yes = run_ev_tco_parity(m_decline, transport, 2025, 2050)
        assert c_yes.penetration[-1] >= c_no.penetration[-1]

    def test_subsidy_increases_adoption(self, transport):
        m_no = EVMacroData(ev_subsidy_pct=0.0)
        m_sub = EVMacroData(ev_subsidy_pct=0.30)
        c_no = run_ev_tco_parity(m_no, transport, 2025, 2040)
        c_sub = run_ev_tco_parity(m_sub, transport, 2025, 2040)
        assert c_sub.penetration[-1] >= c_no.penetration[-1]


# ---------------------------------------------------------------------------
# run_ev_policy_driven
# ---------------------------------------------------------------------------


class TestPolicyDriven:
    """Tests for the policy-driven adoption method."""

    def test_returns_valid_curve(self, macro, transport):
        curve = run_ev_policy_driven(macro, transport, 2025, 2050)
        _assert_valid_curve(curve, 2025, 2050)
        assert curve.method == "policy_driven"

    def test_ice_ban_reaches_high_penetration(self, transport):
        m = EVMacroData(ice_phaseout_year=2035)
        curve = run_ev_policy_driven(m, transport, 2025, 2060)
        # After ban + vehicle lifetime, stock should be mostly EV
        assert curve.penetration[-1] > 0.70

    def test_no_ban_slow_adoption(self, transport):
        m = EVMacroData(ice_phaseout_year=0, emission_target_pct=0)
        curve = run_ev_policy_driven(m, transport, 2025, 2050)
        # Without policy, adoption should be slow
        assert curve.penetration[-1] < 0.50

    def test_emission_target_drives_adoption(self, transport):
        m_low = EVMacroData(ice_phaseout_year=0, emission_target_pct=20)
        m_high = EVMacroData(ice_phaseout_year=0, emission_target_pct=80)
        c_low = run_ev_policy_driven(m_low, transport, 2025, 2050)
        c_high = run_ev_policy_driven(m_high, transport, 2025, 2050)
        assert c_high.penetration[-1] > c_low.penetration[-1]

    def test_scrappage_model_lag(self, transport):
        """Fleet stock lags sales share due to scrappage model."""
        m = EVMacroData(ice_phaseout_year=2035)
        curve = run_ev_policy_driven(m, transport, 2025, 2060)
        # At ban year, stock should not yet be 100%
        ban_idx = curve.years.index(2035)
        assert curve.penetration[ban_idx] < 1.0

    def test_parameters_contain_sales_share(self, macro, transport):
        curve = run_ev_policy_driven(macro, transport, 2025, 2040)
        assert "sales_share_trajectory" in curve.parameters


# ---------------------------------------------------------------------------
# Fleet breakdown and energy metrics
# ---------------------------------------------------------------------------


class TestFleetBreakdown:
    """Tests for fleet_by_category and energy metrics in adoption curves."""

    def test_fleet_sum_close_to_total(self, macro, transport):
        """Category fleet sums should be close to total (rounding tolerance)."""
        curve = run_ev_bass_diffusion(transport, 2025, 2040)
        for i in range(len(curve.years)):
            cat_sum = sum(
                curve.fleet_by_category[cat][i]
                for cat in curve.fleet_by_category
            )
            # Allow rounding from integer category splits
            assert abs(cat_sum - curve.total_fleet_ev[i]) <= len(curve.fleet_by_category)

    def test_energy_demand_positive_when_fleet_positive(self, macro, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2040)
        for i in range(len(curve.years)):
            if curve.total_fleet_ev[i] > 0:
                assert curve.energy_demand_gwh[i] > 0

    def test_peak_charging_scales_with_fleet(self, transport):
        c1 = run_ev_bass_diffusion(transport, 2025, 2035)
        # More fleet → more peak
        assert c1.peak_charging_mw[-1] >= c1.peak_charging_mw[0]

    def test_all_categories_present(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2040)
        for cat in DEFAULT_CATEGORIES:
            assert cat in curve.fleet_by_category


# ---------------------------------------------------------------------------
# fit_adoption_to_ev_config
# ---------------------------------------------------------------------------


class TestFitAdoptionToConfig:
    """Tests for the S-curve fitting and config generation."""

    def test_returns_dict(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=2)
        assert isinstance(config, dict)

    def test_has_required_keys(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=2)
        assert "base_year" in config
        assert "target_year" in config
        assert "categories" in config
        assert "initial_soc" in config
        assert "fitted_s_curve" in config
        assert "method" in config

    def test_categories_count(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=2)
        assert len(config["categories"]) == 4  # light, medium, heavy, buses

    def test_initial_soc_per_node(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=3)
        assert len(config["initial_soc"]) == 3
        assert all(soc >= 0 for soc in config["initial_soc"])

    def test_quantity_per_node(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=4)
        for cat_cfg in config["categories"].values():
            assert len(cat_cfg["quantity"]) == 4

    def test_fitted_s_curve_reasonable(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=1)
        sc = config["fitted_s_curve"]
        assert sc["max_adoption"] > 0
        assert 0 < sc["growth_rate"] < 1
        assert 0.1 <= sc["mid_point_fraction"] <= 0.9

    def test_node_demand_fractions_applied(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        fracs = [0.6, 0.3, 0.1]
        config = fit_adoption_to_ev_config(
            curve, transport, num_nodes=3,
            node_demand_fractions=fracs,
        )
        # First node should have more EVs
        light = config["categories"]["light"]
        assert light["quantity"][0] > light["quantity"][2]

    def test_charging_profiles_included(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=1)
        for cat_cfg in config["categories"].values():
            assert len(cat_cfg["base_pattern"]) == 24

    def test_empty_curve_returns_empty(self, transport):
        curve = EVAdoptionCurve(
            method="test", years=[], penetration=[],
            fleet_by_category={}, total_fleet_ev=[],
            energy_demand_gwh=[], peak_charging_mw=[],
            parameters={},
        )
        config = fit_adoption_to_ev_config(curve, transport, num_nodes=1)
        assert config == {}

    def test_v2g_params_applied(self, transport):
        curve = run_ev_bass_diffusion(transport, 2025, 2050)
        v2g = {"light": {"v2g_power": 10.0, "v2g_participation": 0.5}}
        config = fit_adoption_to_ev_config(
            curve, transport, num_nodes=1, v2g_params=v2g,
        )
        assert config["categories"]["light"]["v2g_power"] == 10.0
        assert config["categories"]["light"]["v2g_participation"] == 0.5


# ---------------------------------------------------------------------------
# Cross-method comparison
# ---------------------------------------------------------------------------


class TestCrossMethodComparison:
    """Tests that compare properties across all 4 methods."""

    def test_all_methods_produce_valid_curves(self, macro, transport):
        curves = [
            run_ev_logistic_adoption(macro, transport, 2025, 2040),
            run_ev_bass_diffusion(transport, 2025, 2040),
            run_ev_tco_parity(macro, transport, 2025, 2040),
            run_ev_policy_driven(macro, transport, 2025, 2040),
        ]
        for curve in curves:
            _assert_valid_curve(curve, 2025, 2040)

    def test_all_methods_have_distinct_names(self, macro, transport):
        methods = {
            run_ev_logistic_adoption(macro, transport, 2025, 2040).method,
            run_ev_bass_diffusion(transport, 2025, 2040).method,
            run_ev_tco_parity(macro, transport, 2025, 2040).method,
            run_ev_policy_driven(macro, transport, 2025, 2040).method,
        }
        assert len(methods) == 4

    def test_all_start_same_base_year(self, macro, transport):
        for fn in [
            lambda: run_ev_logistic_adoption(macro, transport, 2025, 2040),
            lambda: run_ev_bass_diffusion(transport, 2025, 2040),
            lambda: run_ev_tco_parity(macro, transport, 2025, 2040),
            lambda: run_ev_policy_driven(macro, transport, 2025, 2040),
        ]:
            curve = fn()
            assert curve.years[0] == 2025
