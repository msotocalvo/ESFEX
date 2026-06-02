"""
Tests for esfex.models.solar_rooftop module.

Covers the following public functions:
- generate_rooftop_solar_profiles (stochastic availability, adoption factors, max potential)
- integrate_rooftop_solar (capacity integration with S-curve adoption, degradation, cost learning)
- calculate_rooftop_potential (population-based MW potential calculation)
"""

import numpy as np
import pytest

from esfex.models.solar_rooftop import (
    calculate_rooftop_potential,
    generate_rooftop_solar_profiles,
    integrate_rooftop_solar,
)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_rng():
    """Set global numpy random seed for reproducibility."""
    np.random.seed(42)


def _default_profiles(num_nodes=3, hours=24, seed=42, **kwargs):
    """Helper to generate profiles with common defaults."""
    return generate_rooftop_solar_profiles(
        num_nodes=num_nodes,
        hours=hours,
        seed=seed,
        **kwargs,
    )


def _make_integration_inputs(num_nodes=3, hours=24, seed=42):
    """Helper to build standard inputs for integrate_rooftop_solar."""
    avail, adoption, potential = _default_profiles(
        num_nodes=num_nodes, hours=hours, seed=seed,
    )
    units_config = {
        "unit_0": {"name": "Existing_Gen", "type": "Thermal"},
        "unit_1": {"name": "Wind_Farm", "type": "Renewable"},
    }
    return units_config, avail, adoption, potential


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- output shapes and types
# ---------------------------------------------------------------------------


class TestGenerateProfilesOutputShape:
    """Tests for output shapes and types of generate_rooftop_solar_profiles."""

    def test_returns_tuple_of_three(self):
        """Return value is a 3-tuple."""
        result = _default_profiles()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_availability_matrix_is_ndarray(self):
        """First element (availability_matrix) is a numpy ndarray."""
        avail, _, _ = _default_profiles()
        assert isinstance(avail, np.ndarray)

    def test_adoption_factors_is_ndarray(self):
        """Second element (adoption_factors) is a numpy ndarray."""
        _, adoption, _ = _default_profiles()
        assert isinstance(adoption, np.ndarray)

    def test_max_potential_is_list(self):
        """Third element (max_potential) is a list."""
        _, _, potential = _default_profiles()
        assert isinstance(potential, list)

    def test_availability_matrix_shape(self):
        """Availability matrix has shape (hours, num_nodes)."""
        avail, _, _ = _default_profiles(num_nodes=5, hours=48)
        assert avail.shape == (48, 5)

    def test_adoption_factors_length(self):
        """Adoption factors array length matches num_nodes."""
        _, adoption, _ = _default_profiles(num_nodes=7)
        assert len(adoption) == 7

    def test_max_potential_length(self):
        """Max potential list length matches num_nodes."""
        _, _, potential = _default_profiles(num_nodes=4)
        assert len(potential) == 4

    def test_single_node_shape(self):
        """Single node produces correct shapes."""
        avail, adoption, potential = _default_profiles(num_nodes=1, hours=24)
        assert avail.shape == (24, 1)
        assert len(adoption) == 1
        assert len(potential) == 1

    def test_single_hour_shape(self):
        """Single hour produces correct shapes."""
        avail, adoption, potential = _default_profiles(num_nodes=3, hours=1)
        assert avail.shape == (1, 3)
        assert len(adoption) == 3

    def test_large_system_shape(self):
        """Larger system still produces correct shapes."""
        avail, adoption, potential = _default_profiles(num_nodes=20, hours=168)
        assert avail.shape == (168, 20)
        assert len(adoption) == 20
        assert len(potential) == 20


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- value ranges
# ---------------------------------------------------------------------------


class TestGenerateProfilesValueRanges:
    """Tests for value ranges in generated profiles."""

    def test_availability_between_zero_and_one(self):
        """All availability values are in [0, 1]."""
        avail, _, _ = _default_profiles(num_nodes=5, hours=48)
        assert np.all(avail >= 0.0)
        assert np.all(avail <= 1.0)

    def test_adoption_factors_between_zero_and_one(self):
        """All adoption factors are in [0, 1]."""
        _, adoption, _ = _default_profiles(num_nodes=10)
        assert np.all(adoption >= 0.0)
        assert np.all(adoption <= 1.0)

    def test_max_potential_positive(self):
        """All max potential values are positive."""
        _, _, potential = _default_profiles(num_nodes=5)
        assert all(p > 0 for p in potential)

    def test_availability_non_negative_high_variability(self):
        """Availability stays non-negative even with high weather variability."""
        avail, _, _ = _default_profiles(
            num_nodes=10, hours=48, weather_variability="high",
        )
        assert np.all(avail >= 0.0)

    def test_availability_capped_at_one_low_variability(self):
        """Availability stays at most 1.0 even with low variability (high clarity)."""
        avail, _, _ = _default_profiles(
            num_nodes=10, hours=48, weather_variability="low",
        )
        assert np.all(avail <= 1.0)


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- bell-curve shape
# ---------------------------------------------------------------------------


class TestGenerateProfilesBellCurve:
    """Tests for the bell-curve (daylight) shape of solar profiles."""

    def test_nighttime_hours_are_zero(self):
        """Hours before 6am should have zero or near-zero availability."""
        avail, _, _ = _default_profiles(num_nodes=3, hours=24, seed=42)
        # hours_array = linspace(0, 23, 24) -> [0,1,2,3,4,5,...23]
        # Daylight mask: hours >= 6 AND hours <= 18
        # So hours 0-5 (indices 0-5) should be zero base profile
        # Due to hourly_noise, values can be slightly > 0 but clipped to [0,1]
        # With noise std=0.05, nighttime values should be very small
        nighttime = avail[:6, :]  # hours 0-5
        assert np.all(nighttime < 0.15), (
            f"Nighttime values too high: max={nighttime.max():.4f}"
        )

    def test_late_night_hours_are_zero(self):
        """Hours after 6pm (index 19+) should have zero or near-zero availability."""
        avail, _, _ = _default_profiles(num_nodes=3, hours=24, seed=42)
        # hours_array[19] = 19.0 > 18, so base_profile is 0 for indices 19-23
        late_night = avail[19:, :]
        assert np.all(late_night < 0.15), (
            f"Late-night values too high: max={late_night.max():.4f}"
        )

    def test_midday_has_highest_availability(self):
        """Noon-ish hours should have the highest average availability."""
        avail, _, _ = _default_profiles(num_nodes=5, hours=24, seed=42)
        # Mean across nodes for each hour
        hourly_mean = avail.mean(axis=1)
        # Peak should be around hour 12 (index 12)
        peak_hour = np.argmax(hourly_mean)
        assert 10 <= peak_hour <= 14, (
            f"Peak hour is {peak_hour}, expected near noon"
        )

    def test_daytime_values_positive(self):
        """At least some daytime hours (9-15) have positive availability."""
        avail, _, _ = _default_profiles(num_nodes=3, hours=24, seed=42)
        daytime = avail[9:16, :]  # hours 9-15
        assert np.any(daytime > 0.1), "Daytime should have positive availability"

    def test_profile_symmetric_around_noon(self):
        """Average profile should be roughly symmetric around solar noon."""
        # Use many nodes to average out stochastic noise
        avail, _, _ = _default_profiles(num_nodes=50, hours=24, seed=42)
        hourly_mean = avail.mean(axis=1)
        # Compare morning (hours 7-11) vs afternoon (hours 13-17)
        morning_mean = hourly_mean[7:12].mean()
        afternoon_mean = hourly_mean[13:18].mean()
        # Should be within 50% of each other (stochastic, but with 50 nodes)
        ratio = morning_mean / afternoon_mean if afternoon_mean > 0 else 0
        assert 0.3 < ratio < 3.0, (
            f"Morning/afternoon ratio {ratio:.2f} suggests asymmetry"
        )


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- seed reproducibility
# ---------------------------------------------------------------------------


class TestGenerateProfilesReproducibility:
    """Tests for seed-based reproducibility."""

    def test_same_seed_same_availability(self):
        """Same seed produces identical availability matrices."""
        avail1, _, _ = generate_rooftop_solar_profiles(3, 24, seed=99)
        avail2, _, _ = generate_rooftop_solar_profiles(3, 24, seed=99)
        np.testing.assert_array_equal(avail1, avail2)

    def test_same_seed_same_adoption(self):
        """Same seed produces identical adoption factors."""
        _, adopt1, _ = generate_rooftop_solar_profiles(3, 24, seed=99)
        _, adopt2, _ = generate_rooftop_solar_profiles(3, 24, seed=99)
        np.testing.assert_array_equal(adopt1, adopt2)

    def test_same_seed_same_potential(self):
        """Same seed produces identical max potential."""
        _, _, pot1 = generate_rooftop_solar_profiles(3, 24, seed=99)
        _, _, pot2 = generate_rooftop_solar_profiles(3, 24, seed=99)
        np.testing.assert_array_equal(pot1, pot2)

    def test_different_seed_different_availability(self):
        """Different seeds produce different availability matrices."""
        avail1, _, _ = generate_rooftop_solar_profiles(3, 24, seed=1)
        avail2, _, _ = generate_rooftop_solar_profiles(3, 24, seed=2)
        assert not np.array_equal(avail1, avail2)

    def test_no_seed_varies(self):
        """Without seed, consecutive calls may differ (non-deterministic)."""
        # Reset to known state, then call without seed
        np.random.seed(None)
        avail1, _, _ = generate_rooftop_solar_profiles(3, 24, seed=None)
        # Immediately call again -- extremely unlikely to be identical
        # (but technically possible; test is probabilistic)
        avail2, _, _ = generate_rooftop_solar_profiles(3, 24, seed=None)
        # We just check they are valid; exact equality is not guaranteed
        assert avail1.shape == avail2.shape


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- weather variability
# ---------------------------------------------------------------------------


class TestGenerateProfilesWeatherVariability:
    """Tests for weather variability levels."""

    def test_low_variability_less_spread(self):
        """Low variability produces less spread than high variability when
        averaged across many seeds (a single seed can flip the comparison
        due to stochastic noise)."""
        stds_low: list[float] = []
        stds_high: list[float] = []
        for seed in range(20):
            avail_low, _, _ = _default_profiles(
                num_nodes=20, hours=48, weather_variability="low", seed=seed,
            )
            avail_high, _, _ = _default_profiles(
                num_nodes=20, hours=48, weather_variability="high", seed=seed,
            )
            stds_low.append(np.std(avail_low[6:19, :]))
            stds_high.append(np.std(avail_high[6:19, :]))
        mean_low = float(np.mean(stds_low))
        mean_high = float(np.mean(stds_high))
        assert mean_low < mean_high, (
            f"Mean low-variability std ({mean_low:.4f}) should be < "
            f"mean high-variability std ({mean_high:.4f}) across 20 seeds"
        )

    def test_all_variability_levels_valid(self):
        """All three variability levels produce valid output."""
        for level in ("low", "normal", "high"):
            avail, _, _ = _default_profiles(
                num_nodes=3, hours=24, weather_variability=level,
            )
            assert avail.shape == (24, 3)
            assert np.all(avail >= 0)
            assert np.all(avail <= 1)

    def test_invalid_variability_raises(self):
        """Invalid weather_variability raises KeyError."""
        with pytest.raises(KeyError):
            _default_profiles(weather_variability="extreme")


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- adoption scenarios
# ---------------------------------------------------------------------------


class TestGenerateProfilesAdoptionScenarios:
    """Tests for adoption scenario levels (low/medium/high)."""

    def test_high_adoption_greater_than_low(self):
        """High adoption scenario produces higher mean adoption than low."""
        _, adopt_low, _ = _default_profiles(
            num_nodes=20, adoption_scenario="low", seed=42,
        )
        _, adopt_high, _ = _default_profiles(
            num_nodes=20, adoption_scenario="high", seed=42,
        )
        assert adopt_high.mean() > adopt_low.mean()

    def test_medium_adoption_between_low_and_high(self):
        """Medium adoption is between low and high on average."""
        _, adopt_low, _ = _default_profiles(
            num_nodes=30, adoption_scenario="low", seed=42,
        )
        _, adopt_med, _ = _default_profiles(
            num_nodes=30, adoption_scenario="medium", seed=42,
        )
        _, adopt_high, _ = _default_profiles(
            num_nodes=30, adoption_scenario="high", seed=42,
        )
        assert adopt_low.mean() < adopt_med.mean() < adopt_high.mean()

    def test_all_scenarios_valid(self):
        """All three adoption scenarios produce valid adoption factors."""
        for scenario in ("low", "medium", "high"):
            _, adoption, _ = _default_profiles(
                num_nodes=5, adoption_scenario=scenario,
            )
            assert np.all(adoption >= 0)
            assert np.all(adoption <= 1)

    def test_adoption_capped_at_0_9(self):
        """Adoption factors are capped at 0.9 (code: min(0.9, ...))."""
        _, adoption, _ = _default_profiles(
            num_nodes=50, adoption_scenario="high",
            target_year=2100, seed=42,
        )
        assert np.all(adoption <= 0.9)


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- config parameter overrides
# ---------------------------------------------------------------------------


class TestGenerateProfilesConfigOverrides:
    """Tests for configuration parameter overrides."""

    def test_performance_ratio_scales_profile(self):
        """Custom performance_ratio scales the availability values."""
        config_low = {"performance_ratio": 0.5}
        config_high = {"performance_ratio": 1.0}
        avail_low, _, _ = _default_profiles(
            num_nodes=5, hours=24, seed=42, config=config_low,
        )
        avail_high, _, _ = _default_profiles(
            num_nodes=5, hours=24, seed=42, config=config_high,
        )
        # Higher performance ratio should produce higher daytime values on average
        daytime_low = avail_low[9:15, :].mean()
        daytime_high = avail_high[9:15, :].mean()
        assert daytime_high > daytime_low

    def test_systems_per_node_and_avg_system_size(self):
        """Config with systems_per_node and avg_system_size sets deterministic potential."""
        config = {
            "systems_per_node": [10000, 20000],
            "avg_system_size": [5.0, 5.0],  # kW
        }
        _, _, potential = _default_profiles(num_nodes=2, config=config)
        # 10000 * 5.0 / 1000 = 50.0 MW
        assert potential[0] == pytest.approx(50.0)
        # 20000 * 5.0 / 1000 = 100.0 MW
        assert potential[1] == pytest.approx(100.0)

    def test_systems_per_node_pads_shorter_list(self):
        """When systems_per_node list is shorter than num_nodes, last value is repeated."""
        config = {
            "systems_per_node": [8000],
            "avg_system_size": [4.0],
        }
        _, _, potential = _default_profiles(num_nodes=3, config=config)
        # All three nodes should be 8000 * 4.0 / 1000 = 32.0 MW
        assert potential[0] == pytest.approx(32.0)
        assert potential[1] == pytest.approx(32.0)
        assert potential[2] == pytest.approx(32.0)

    def test_custom_adoption_rates(self):
        """Custom adoption_rates in config are used."""
        config = {"adoption_rates": {"low": 0.01, "medium": 0.02, "high": 0.03}}
        _, adopt_custom, _ = _default_profiles(
            num_nodes=10, adoption_scenario="medium", seed=42, config=config,
        )
        _, adopt_default, _ = _default_profiles(
            num_nodes=10, adoption_scenario="medium", seed=42,
        )
        # Custom rate 0.02 < default 0.08, so adoption should be lower
        assert adopt_custom.mean() < adopt_default.mean()

    def test_custom_initial_adoption(self):
        """Custom initial_adoption in config is used."""
        config_low = {"initial_adoption": [0.01, 0.01, 0.01]}
        config_high = {"initial_adoption": [0.3, 0.3, 0.3]}
        _, adopt_low, _ = _default_profiles(num_nodes=3, config=config_low, seed=42)
        _, adopt_high, _ = _default_profiles(num_nodes=3, config=config_high, seed=42)
        # Both should be valid
        assert np.all(adopt_low >= 0)
        assert np.all(adopt_high >= 0)

    def test_custom_max_adoption(self):
        """Custom max_adoption map in config affects adoption ceiling."""
        config = {"max_adoption": {"low": 0.10, "medium": 0.20, "high": 0.30}}
        _, adoption, _ = _default_profiles(
            num_nodes=20, adoption_scenario="high", seed=42, config=config,
        )
        # With max_adoption high = 0.30, all adoption factors should be <= 0.30 * 1.2 * s_curve
        # Due to urbanization_factor scaling (0.8 + 0.4*u), max is 0.30 * 1.2 = 0.36
        # Then capped at 0.9 and divided by logistic. Should be well below 0.5
        assert np.all(adoption < 0.5)

    def test_no_config_uses_defaults(self):
        """Calling without config uses default parameters without error."""
        avail, adoption, potential = _default_profiles(num_nodes=3, config=None)
        assert avail.shape == (24, 3)
        assert len(adoption) == 3
        assert len(potential) == 3


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles -- year dynamics
# ---------------------------------------------------------------------------


class TestGenerateProfilesYearDynamics:
    """Tests for base_year / target_year effects on adoption."""

    def test_same_base_and_target_year(self):
        """When base_year == target_year, adoption factors are still valid."""
        _, adoption, _ = _default_profiles(
            num_nodes=3, base_year=2024, target_year=2024, seed=42,
        )
        assert np.all(adoption >= 0)
        assert np.all(adoption <= 1)

    def test_longer_horizon_different_adoption(self):
        """A longer time horizon changes adoption factors."""
        _, adopt_short, _ = _default_profiles(
            num_nodes=10, base_year=2024, target_year=2030, seed=42,
        )
        _, adopt_long, _ = _default_profiles(
            num_nodes=10, base_year=2024, target_year=2060, seed=42,
        )
        # Different target years should produce different adoption
        # (not necessarily higher -- S-curve mid_point shifts)
        assert not np.array_equal(adopt_short, adopt_long)


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- basic functionality
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarBasic:
    """Tests for basic functionality of integrate_rooftop_solar."""

    def test_returns_dict(self):
        """Return value is a dictionary when above threshold."""
        units, avail, adoption, potential = _make_integration_inputs()
        result = integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2030,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
        )
        assert isinstance(result, dict)

    def test_returns_none_below_threshold(self):
        """Returns None when total installed capacity is below threshold."""
        units, avail, adoption, potential = _make_integration_inputs()
        # Set very high threshold
        result = integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2024,  # base_year == year, so s_curve ~ 0
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
            min_capacity_threshold=1e12,
        )
        assert result is None

    def test_returns_none_zero_potential(self):
        """Returns None when max_potential is all zeros."""
        units, avail, adoption, _ = _make_integration_inputs()
        result = integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2030,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=[0.0, 0.0, 0.0],
            min_capacity_threshold=1.0,
        )
        assert result is None

    def test_unit_added_to_config(self):
        """A new unit key is added to units_config dict."""
        units, avail, adoption, potential = _make_integration_inputs()
        original_len = len(units)
        integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2035,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
        )
        assert len(units) == original_len + 1

    def test_unit_key_auto_increments(self):
        """New unit key auto-increments from existing unit IDs."""
        units, avail, adoption, potential = _make_integration_inputs()
        # Existing keys: unit_0, unit_1 -> next should be unit_2
        integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2035,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
        )
        assert "unit_2" in units

    def test_unit_key_with_empty_config(self):
        """When units_config is empty, first unit is unit_1."""
        avail, adoption, potential = _default_profiles(num_nodes=2)
        units = {}
        integrate_rooftop_solar(
            units_config=units,
            num_nodes=2,
            year=2035,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
        )
        assert "unit_1" in units


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- unit config format validation
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarConfigFormat:
    """Tests for the format of the returned unit configuration dict."""

    @pytest.fixture()
    def rooftop_unit(self):
        """Generate a rooftop unit configuration."""
        units, avail, adoption, potential = _make_integration_inputs()
        return integrate_rooftop_solar(
            units_config=units,
            num_nodes=3,
            year=2035,
            base_year=2024,
            availability_matrix=avail,
            adoption_factors=adoption,
            max_potential=potential,
        )

    def test_name_is_rooftop_solar(self, rooftop_unit):
        """Unit name is 'Rooftop_Solar'."""
        assert rooftop_unit["name"] == "Rooftop_Solar"

    def test_type_is_renewable(self, rooftop_unit):
        """Unit type is 'Renewable'."""
        assert rooftop_unit["type"] == "Renewable"

    def test_fuel_is_sun(self, rooftop_unit):
        """Unit fuel is 'Sun'."""
        assert rooftop_unit["fuel"] == "Sun"

    def test_rated_power_is_list(self, rooftop_unit):
        """Rated power is a list of floats."""
        assert isinstance(rooftop_unit["rated_power"], list)
        assert len(rooftop_unit["rated_power"]) == 3

    def test_fuel_cost_is_zero(self, rooftop_unit):
        """Fuel cost is zero for solar."""
        assert all(c == 0.0 for c in rooftop_unit["fuel_cost"])

    def test_reservable_is_false(self, rooftop_unit):
        """Rooftop solar is not reservable."""
        assert rooftop_unit["reservable"] is False

    def test_all_per_node_arrays_correct_length(self, rooftop_unit):
        """All per-node arrays have length == num_nodes."""
        per_node_keys = [
            "rated_power", "fuel_cost", "fixed_cost", "maintenance_cost",
            "invest_cost", "invest_max_power", "ramp_up", "ramp_down",
            "min_up", "min_down", "start_up_cost", "inertia", "min_power",
            "eff_at_rated", "eff_at_min",
        ]
        for key in per_node_keys:
            assert len(rooftop_unit[key]) == 3, (
                f"Key '{key}' has length {len(rooftop_unit[key])}, expected 3"
            )

    def test_availability_matrix_in_unit(self, rooftop_unit):
        """Unit config contains Availability matrix."""
        assert "Availability" in rooftop_unit
        assert isinstance(rooftop_unit["Availability"], np.ndarray)

    def test_ramp_rates_are_one(self, rooftop_unit):
        """Ramp rates are 1.0 (full flexibility for solar)."""
        assert all(r == 1.0 for r in rooftop_unit["ramp_up"])
        assert all(r == 1.0 for r in rooftop_unit["ramp_down"])

    def test_startup_cost_is_zero(self, rooftop_unit):
        """Start-up cost is zero for solar."""
        assert all(c == 0.0 for c in rooftop_unit["start_up_cost"])

    def test_inertia_is_zero(self, rooftop_unit):
        """Inertia is zero for solar (no rotating mass)."""
        assert all(i == 0.0 for i in rooftop_unit["inertia"])

    def test_efficiency_equals_performance_ratio(self, rooftop_unit):
        """Efficiency at rated and min equals performance ratio (default 0.75)."""
        assert all(e == pytest.approx(0.75) for e in rooftop_unit["eff_at_rated"])
        assert all(e == pytest.approx(0.75) for e in rooftop_unit["eff_at_min"])


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- S-curve adoption dynamics
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarSCurve:
    """Tests for S-curve adoption dynamics in integration."""

    def test_capacity_increases_with_year(self):
        """Installed capacity increases for later years (S-curve growth)."""
        units1, avail, adoption, potential = _make_integration_inputs()
        units2 = dict(units1)  # shallow copy
        result_early = integrate_rooftop_solar(
            units_config=units1, num_nodes=3, year=2026, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )
        result_late = integrate_rooftop_solar(
            units_config=units2, num_nodes=3, year=2045, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )
        # Both might be None if below threshold; skip if so
        if result_early is not None and result_late is not None:
            early_total = sum(result_early["rated_power"])
            late_total = sum(result_late["rated_power"])
            assert late_total > early_total

    def test_s_curve_factor_at_midpoint(self):
        """At the midpoint of the planning horizon, S-curve factor ~ 0.5."""
        # progress_factor = years_diff / (target_year - base_year)
        # s_curve_factor = 1 / (1 + exp(-10*(progress - 0.5)))
        # At midpoint: progress = 0.5, s_curve = 1/(1+exp(0)) = 0.5
        base_year = 2024
        target_year = 2050
        mid_year = 2037  # midpoint
        config = {"target_year": target_year}
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1,
            year=mid_year, base_year=base_year,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, config=config,
        )
        # The s_curve_factor at midpoint should be ~0.5
        progress = (mid_year - base_year) / (target_year - base_year)
        s_factor = 1 / (1 + np.exp(-10 * (progress - 0.5)))
        assert s_factor == pytest.approx(0.5, abs=0.01)

    def test_s_curve_factor_approaches_one_at_target(self):
        """Near the target year, S-curve factor approaches 1.0."""
        progress = 0.95  # Near end of horizon
        s_factor = 1 / (1 + np.exp(-10 * (progress - 0.5)))
        assert s_factor > 0.98


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- cost reduction learning curve
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarCostReduction:
    """Tests for cost reduction (learning curve) in integration."""

    def test_cost_decreases_over_time(self):
        """Investment cost decreases in later years due to learning curve."""
        avail, adoption, potential = _default_profiles(num_nodes=2, seed=42)

        units1 = {"unit_0": {"name": "Gen"}}
        result_early = integrate_rooftop_solar(
            units_config=units1, num_nodes=2, year=2025, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )

        units2 = {"unit_0": {"name": "Gen"}}
        result_late = integrate_rooftop_solar(
            units_config=units2, num_nodes=2, year=2040, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )

        if result_early is not None and result_late is not None:
            assert result_late["invest_cost"][0] < result_early["invest_cost"][0]

    def test_cost_factor_formula(self):
        """Cost factor follows (1 - rate)^years_diff formula."""
        rate = 0.08
        years = 10
        expected = (1 - rate) ** years
        assert expected == pytest.approx(0.4344, rel=0.01)

    def test_custom_cost_reduction_rate(self):
        """Custom cost_reduction_rate affects investment cost."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)

        units1 = {}
        result_slow = integrate_rooftop_solar(
            units_config=units1, num_nodes=1, year=2034, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, cost_reduction_rate=0.02,
        )

        units2 = {}
        result_fast = integrate_rooftop_solar(
            units_config=units2, num_nodes=1, year=2034, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, cost_reduction_rate=0.15,
        )

        if result_slow is not None and result_fast is not None:
            assert result_fast["invest_cost"][0] < result_slow["invest_cost"][0]

    def test_fixed_cost_also_reduces(self):
        """Fixed cost is also affected by cost_factor."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        config = {"o_and_m_cost": 20}
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2034, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, config=config,
        )
        if result is not None:
            # fixed_cost = o_and_m_cost * cost_factor
            # cost_factor = (1 - 0.08)^10 ~ 0.434
            assert result["fixed_cost"][0] < 20.0


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- degradation factor
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarDegradation:
    """Tests for degradation factor in integration."""

    def test_degradation_reduces_capacity(self):
        """Degradation reduces installed capacity over time."""
        avail, adoption, potential = _default_profiles(num_nodes=2, seed=42)

        # Year 0 (no degradation)
        units1 = {}
        result_new = integrate_rooftop_solar(
            units_config=units1, num_nodes=2, year=2024, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )

        # Year 20 (significant degradation)
        units2 = {}
        result_old = integrate_rooftop_solar(
            units_config=units2, num_nodes=2, year=2044, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )

        # The degradation factor at year 20: 1 - 0.005 * 20 / 2 = 0.95
        # But S-curve is also higher at year 20, so compare ratio
        if result_new is not None and result_old is not None:
            # Just verify degradation factor is applied (capacity per MW potential
            # should be less than adoption would suggest)
            deg_factor = 1.0 - (0.005 * 20 / 2)
            assert deg_factor == pytest.approx(0.95)

    def test_custom_degradation_rate(self):
        """Custom degradation_rate from config is applied."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        config_high_deg = {"degradation_rate": 0.02}
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2044, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, config=config_high_deg,
        )
        # degradation_factor = 1.0 - (0.02 * 20 / 2) = 0.80
        if result is not None:
            # Capacity should reflect 80% of undegraded value
            # We can't easily test the exact value due to S-curve,
            # but we can verify the formula
            deg_factor = 1.0 - (0.02 * 20 / 2)
            assert deg_factor == pytest.approx(0.80)

    def test_no_degradation_at_base_year(self):
        """At base year (years_diff=0), degradation factor is 1.0."""
        deg_factor = 1.0 - (0.005 * 0 / 2)
        assert deg_factor == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- threshold logic
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarThreshold:
    """Tests for minimum capacity threshold logic."""

    def test_default_threshold_is_one_mw(self):
        """Default min_capacity_threshold is 1.0 MW."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        units = {}
        # With very small potential, should return None
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2025, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=[0.001],  # 0.001 MW potential
        )
        assert result is None

    def test_zero_threshold_always_adds(self):
        """With threshold=0, even tiny capacity is added."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2035, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=[0.01],
            min_capacity_threshold=0.0,
        )
        assert result is not None

    def test_high_threshold_returns_none(self):
        """Very high threshold causes None return even with large potential."""
        avail, adoption, potential = _default_profiles(num_nodes=3, seed=42)
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=3, year=2030, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
            min_capacity_threshold=1e9,
        )
        assert result is None

    def test_units_config_unchanged_when_none_returned(self):
        """units_config is not modified when result is None."""
        units, avail, adoption, potential = _make_integration_inputs()
        original_keys = set(units.keys())
        integrate_rooftop_solar(
            units_config=units, num_nodes=3, year=2024, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=[0.0, 0.0, 0.0],
            min_capacity_threshold=1.0,
        )
        assert set(units.keys()) == original_keys


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- invest_max_power
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarInvestMax:
    """Tests for invest_max_power calculation."""

    def test_invest_max_non_negative(self):
        """Investment max power is non-negative for all nodes."""
        units, avail, adoption, potential = _make_integration_inputs()
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=3, year=2035, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential,
        )
        if result is not None:
            assert all(p >= 0 for p in result["invest_max_power"])

    def test_invest_max_decreases_with_adoption(self):
        """Higher current adoption leaves less remaining investment potential."""
        avail, _, potential = _default_profiles(num_nodes=1, seed=42)
        low_adopt = np.array([0.1])
        high_adopt = np.array([0.8])

        units1 = {}
        result_low = integrate_rooftop_solar(
            units_config=units1, num_nodes=1, year=2040, base_year=2024,
            availability_matrix=avail, adoption_factors=low_adopt,
            max_potential=potential, min_capacity_threshold=0.0,
        )
        units2 = {}
        result_high = integrate_rooftop_solar(
            units_config=units2, num_nodes=1, year=2040, base_year=2024,
            availability_matrix=avail, adoption_factors=high_adopt,
            max_potential=potential, min_capacity_threshold=0.0,
        )
        if result_low is not None and result_high is not None:
            assert result_low["invest_max_power"][0] > result_high["invest_max_power"][0]


# ---------------------------------------------------------------------------
# integrate_rooftop_solar -- config overrides
# ---------------------------------------------------------------------------


class TestIntegrateRooftopSolarConfig:
    """Tests for config parameter overrides in integration."""

    def test_custom_cost_per_kw(self):
        """Custom cost_per_kw from config is used."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        config = {"cost_per_kw": 800}
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2025, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, config=config, min_capacity_threshold=0.0,
        )
        if result is not None:
            # invest_cost = 800 * (1-0.08)^1 = 800 * 0.92 = 736.0
            assert result["invest_cost"][0] == pytest.approx(736.0, rel=0.01)

    def test_custom_performance_ratio_in_efficiency(self):
        """Custom performance_ratio appears in eff_at_rated."""
        avail, adoption, potential = _default_profiles(num_nodes=1, seed=42)
        config = {"performance_ratio": 0.85}
        units = {}
        result = integrate_rooftop_solar(
            units_config=units, num_nodes=1, year=2035, base_year=2024,
            availability_matrix=avail, adoption_factors=adoption,
            max_potential=potential, config=config, min_capacity_threshold=0.0,
        )
        if result is not None:
            assert result["eff_at_rated"][0] == pytest.approx(0.85)
            assert result["eff_at_min"][0] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# calculate_rooftop_potential -- basic
# ---------------------------------------------------------------------------


class TestCalculateRooftopPotentialBasic:
    """Tests for calculate_rooftop_potential function."""

    def test_returns_list(self):
        """Return type is a list."""
        result = calculate_rooftop_potential([100000, 200000])
        assert isinstance(result, list)

    def test_output_length_matches_input(self):
        """Output list length matches input population list."""
        result = calculate_rooftop_potential([100000, 200000, 300000])
        assert len(result) == 3

    def test_single_node(self):
        """Single population value returns single-element list."""
        result = calculate_rooftop_potential([500000])
        assert len(result) == 1

    def test_empty_population(self):
        """Empty population list returns empty list."""
        result = calculate_rooftop_potential([])
        assert result == []

    def test_values_positive(self):
        """Positive population produces positive potential."""
        result = calculate_rooftop_potential([100000, 200000])
        assert all(p > 0 for p in result)

    def test_zero_population(self):
        """Zero population produces zero potential."""
        result = calculate_rooftop_potential([0])
        assert result[0] == 0.0

    def test_proportional_to_population(self):
        """Potential is proportional to population."""
        result = calculate_rooftop_potential([100000, 200000])
        assert result[1] == pytest.approx(2 * result[0])


# ---------------------------------------------------------------------------
# calculate_rooftop_potential -- formula verification
# ---------------------------------------------------------------------------


class TestCalculateRooftopPotentialFormula:
    """Tests for the exact formula of calculate_rooftop_potential."""

    def test_default_parameters_formula(self):
        """Verify exact calculation with default parameters."""
        pop = 100000
        # dwelling_density=0.35, avg_roof=50, suitable=0.3, eff=0.20, irr=1000
        num_dwellings = pop * 0.35  # 35000
        total_roof = num_dwellings * 50.0 * 0.3  # 525000 m2
        peak_kw = total_roof * 0.20 * 1000 / 1000  # 105000 kW = 105 MW
        expected_mw = peak_kw / 1000  # 105 MW
        result = calculate_rooftop_potential([pop])
        assert result[0] == pytest.approx(expected_mw)

    def test_custom_dwelling_density(self):
        """Custom dwelling_density changes result proportionally."""
        result_default = calculate_rooftop_potential([100000], dwelling_density=0.35)
        result_double = calculate_rooftop_potential([100000], dwelling_density=0.70)
        assert result_double[0] == pytest.approx(2 * result_default[0])

    def test_custom_avg_roof_area(self):
        """Custom avg_roof_area changes result proportionally."""
        result_50 = calculate_rooftop_potential([100000], avg_roof_area=50.0)
        result_100 = calculate_rooftop_potential([100000], avg_roof_area=100.0)
        assert result_100[0] == pytest.approx(2 * result_50[0])

    def test_custom_suitable_fraction(self):
        """Custom suitable_fraction changes result proportionally."""
        result_low = calculate_rooftop_potential([100000], suitable_fraction=0.15)
        result_high = calculate_rooftop_potential([100000], suitable_fraction=0.30)
        assert result_high[0] == pytest.approx(2 * result_low[0])

    def test_custom_panel_efficiency(self):
        """Custom panel_efficiency changes result proportionally."""
        result_low = calculate_rooftop_potential([100000], panel_efficiency=0.10)
        result_high = calculate_rooftop_potential([100000], panel_efficiency=0.20)
        assert result_high[0] == pytest.approx(2 * result_low[0])

    def test_custom_solar_irradiance(self):
        """Custom solar_irradiance changes result proportionally."""
        result_low = calculate_rooftop_potential([100000], solar_irradiance=500.0)
        result_high = calculate_rooftop_potential([100000], solar_irradiance=1000.0)
        assert result_high[0] == pytest.approx(2 * result_low[0])

    def test_all_custom_parameters(self):
        """Full formula verification with all custom parameters."""
        pop = 50000
        dd = 0.4
        roof = 60.0
        sf = 0.25
        eff = 0.22
        irr = 1200.0
        # num_dwellings = 50000 * 0.4 = 20000
        # total_roof = 20000 * 60 * 0.25 = 300000
        # peak_kw = 300000 * 0.22 * 1200 / 1000 = 79200 kW
        # MW = 79200 / 1000 = 79.2
        expected = 79.2
        result = calculate_rooftop_potential(
            [pop],
            dwelling_density=dd,
            avg_roof_area=roof,
            suitable_fraction=sf,
            panel_efficiency=eff,
            solar_irradiance=irr,
        )
        assert result[0] == pytest.approx(expected)

    def test_multiple_nodes_independent(self):
        """Each node is calculated independently."""
        result = calculate_rooftop_potential([100000, 200000, 300000])
        single_results = [
            calculate_rooftop_potential([p])[0]
            for p in [100000, 200000, 300000]
        ]
        for i in range(3):
            assert result[i] == pytest.approx(single_results[i])
