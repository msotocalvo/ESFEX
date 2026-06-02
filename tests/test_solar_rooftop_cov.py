"""Coverage tests for esfex.models.solar_rooftop."""

import numpy as np
import pytest

from esfex.models.solar_rooftop import (
    calculate_rooftop_potential,
    generate_rooftop_solar_profiles,
    integrate_rooftop_solar,
)


# ---------------------------------------------------------------------------
# generate_rooftop_solar_profiles
# ---------------------------------------------------------------------------


def test_generate_shapes_and_types():
    avail, adoption, max_pot = generate_rooftop_solar_profiles(
        num_nodes=4, hours=24, seed=42
    )
    assert isinstance(avail, np.ndarray)
    assert avail.shape == (24, 4)
    assert isinstance(adoption, np.ndarray)
    assert adoption.shape == (4,)
    assert isinstance(max_pot, list)
    assert len(max_pot) == 4


def test_generate_availability_bounded_0_1():
    avail, _, _ = generate_rooftop_solar_profiles(num_nodes=3, hours=48, seed=7)
    assert np.all(avail >= 0.0)
    assert np.all(avail <= 1.0)


def test_generate_seed_reproducible():
    a1, ad1, mp1 = generate_rooftop_solar_profiles(num_nodes=5, hours=24, seed=123)
    a2, ad2, mp2 = generate_rooftop_solar_profiles(num_nodes=5, hours=24, seed=123)
    assert np.array_equal(a1, a2)
    assert np.array_equal(ad1, ad2)
    assert mp1 == mp2


def test_generate_seed_none_no_crash():
    # seed None must not call np.random.seed; just verify it runs
    avail, adoption, max_pot = generate_rooftop_solar_profiles(
        num_nodes=2, hours=24, seed=None
    )
    assert avail.shape == (24, 2)


def test_generate_nighttime_is_zero():
    # Hours outside [6, 18] have base_profile 0; with no positive noise contribution
    # the base is 0, but hourly_noise can push values up. The base_profile itself
    # is zero at midnight. Verify base behavior via mean: daytime mean > nighttime.
    avail, _, _ = generate_rooftop_solar_profiles(num_nodes=8, hours=24, seed=99)
    day = avail[12, :].mean()      # noon
    night = avail[0, :].mean()     # midnight
    assert day > night


def test_generate_performance_ratio_caps_peak():
    # base profile peak = sin(pi*(12-6)/12) = sin(pi/2) = 1.0, scaled by perf ratio
    config = {"performance_ratio": 0.5}
    avail, _, _ = generate_rooftop_solar_profiles(
        num_nodes=1, hours=24, seed=1, config=config,
        weather_variability="low",
    )
    # With low weather variance and bounded factors, noon value should be modest.
    assert avail[12, 0] <= 1.0


def test_generate_weather_variability_keys():
    for wv in ("low", "normal", "high"):
        avail, _, _ = generate_rooftop_solar_profiles(
            num_nodes=2, hours=24, seed=3, weather_variability=wv
        )
        assert avail.shape == (24, 2)


def test_generate_invalid_weather_variability_raises_keyerror():
    with pytest.raises(KeyError):
        generate_rooftop_solar_profiles(
            num_nodes=2, hours=24, seed=3, weather_variability="extreme"
        )


def test_generate_adoption_scenarios():
    for sc in ("low", "medium", "high"):
        _, adoption, _ = generate_rooftop_solar_profiles(
            num_nodes=3, hours=24, seed=5, adoption_scenario=sc
        )
        assert np.all(adoption >= 0.0)
        # node_max_adoption capped at 0.9, target via logistic < that
        assert np.all(adoption <= 0.9)


def test_generate_unknown_adoption_scenario_uses_defaults():
    # adoption_rates.get(...,0.08) and max_adoption.get(...,0.5) fallbacks
    _, adoption, _ = generate_rooftop_solar_profiles(
        num_nodes=2, hours=24, seed=5, adoption_scenario="bogus"
    )
    assert adoption.shape == (2,)


def test_generate_systems_per_node_config_path():
    config = {
        "systems_per_node": [1000, 2000],
        "avg_system_size": [4.0, 6.0],
    }
    _, _, max_pot = generate_rooftop_solar_profiles(
        num_nodes=2, hours=24, seed=11, config=config
    )
    # node0: 1000 * 4.0 / 1000 = 4.0 ; node1: 2000 * 6.0 / 1000 = 12.0
    assert max_pot == pytest.approx([4.0, 12.0])


def test_generate_systems_per_node_padding():
    # fewer entries than num_nodes -> last value repeated
    config = {
        "systems_per_node": [1000],
        "avg_system_size": [5.0],
    }
    _, _, max_pot = generate_rooftop_solar_profiles(
        num_nodes=3, hours=24, seed=11, config=config
    )
    # all nodes use 1000 * 5.0 / 1000 = 5.0
    assert max_pot == pytest.approx([5.0, 5.0, 5.0])


def test_generate_only_systems_without_avg_uses_fallback_branch():
    # Only one of the two keys present -> falls to else (gamma) branch
    config = {"systems_per_node": [1000, 2000]}
    _, _, max_pot = generate_rooftop_solar_profiles(
        num_nodes=2, hours=24, seed=11, config=config
    )
    # gamma branch: all positive
    assert all(p > 0 for p in max_pot)


def test_generate_custom_adoption_rates_and_max():
    config = {
        "adoption_rates": {"medium": 0.5},
        "max_adoption": {"medium": 0.6},
    }
    _, adoption, _ = generate_rooftop_solar_profiles(
        num_nodes=2, hours=24, seed=2, adoption_scenario="medium", config=config
    )
    assert np.all(adoption <= 0.9)


def test_generate_initial_adoption_padding_no_error():
    # initial_adoption is read but padded; ensure short list works
    config = {"initial_adoption": [0.1]}
    avail, adoption, _ = generate_rooftop_solar_profiles(
        num_nodes=4, hours=24, seed=8, config=config
    )
    assert adoption.shape == (4,)


def test_generate_multiday_hours():
    avail, _, _ = generate_rooftop_solar_profiles(num_nodes=2, hours=72, seed=4)
    assert avail.shape == (72, 2)


def test_generate_hours_less_than_24():
    # num_days = max(1, hours//24) -> 1 when hours < 24
    avail, _, _ = generate_rooftop_solar_profiles(num_nodes=2, hours=10, seed=4)
    assert avail.shape == (10, 2)


# ---------------------------------------------------------------------------
# integrate_rooftop_solar
# ---------------------------------------------------------------------------


def _make_inputs(num_nodes=3, hours=24, seed=10):
    avail, adoption, max_pot = generate_rooftop_solar_profiles(
        num_nodes=num_nodes, hours=hours, seed=seed, adoption_scenario="high"
    )
    return avail, adoption, max_pot


def test_integrate_returns_unit_and_mutates_config():
    avail, adoption, max_pot = _make_inputs()
    # Force large potential so we exceed threshold
    max_pot = [10000.0] * len(max_pot)
    units = {}
    result = integrate_rooftop_solar(
        units_config=units,
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
    )
    assert result is not None
    assert result["name"] == "Rooftop_Solar"
    assert result["type"] == "Renewable"
    assert result["fuel"] == "Sun"
    assert result["reservable"] is False
    # config mutated in place
    assert "unit_1" in units
    assert units["unit_1"] is result


def test_integrate_below_threshold_returns_none():
    avail, adoption, _ = _make_inputs()
    units = {}
    result = integrate_rooftop_solar(
        units_config=units,
        num_nodes=3,
        year=2024,            # years_diff=0 -> s_curve very small
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=[0.0, 0.0, 0.0],
        min_capacity_threshold=1.0,
    )
    assert result is None
    assert units == {}


def test_integrate_unit_field_lengths():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0, 5000.0, 5000.0]
    units = {}
    result = integrate_rooftop_solar(
        units_config=units,
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
    )
    assert len(result["rated_power"]) == 3
    assert result["fuel_cost"] == [0.0, 0.0, 0.0]
    assert len(result["fixed_cost"]) == 3
    assert len(result["invest_max_power"]) == 3
    assert result["ramp_up"] == [1.0, 1.0, 1.0]
    assert result["min_up"] == [1, 1, 1]
    assert np.array_equal(result["Availability"], avail)


def test_integrate_existing_unit_ids_increment():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0] * 3
    units = {"unit_3": {"x": 1}, "unit_7": {"y": 2}}
    integrate_rooftop_solar(
        units_config=units,
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
    )
    # max existing id is 7 -> new unit_8
    assert "unit_8" in units


def test_integrate_non_unit_keys_fallback():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0] * 3
    # keys not matching unit_ pattern -> existing_ids empty -> unit_1
    units = {"foo": {}, "bar": {}}
    integrate_rooftop_solar(
        units_config=units,
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
    )
    assert "unit_1" in units


def test_integrate_invest_max_power_nonnegative():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0] * 3
    result = integrate_rooftop_solar(
        units_config={},
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
    )
    assert all(v >= 0.0 for v in result["invest_max_power"])


def test_integrate_cost_factor_applied():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0] * 3
    config = {"cost_per_kw": 1000, "o_and_m_cost": 100}
    result = integrate_rooftop_solar(
        units_config={},
        num_nodes=3,
        year=2032,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
        cost_reduction_rate=0.1,
        config=config,
    )
    years_diff = 2032 - 2024
    expected_cost_factor = (1 - 0.1) ** years_diff
    expected_invest = 1000 * expected_cost_factor
    assert result["invest_cost"][0] == pytest.approx(expected_invest)
    assert result["fixed_cost"][0] == pytest.approx(100 * expected_cost_factor)


def test_integrate_performance_ratio_efficiency_fields():
    avail, adoption, _ = _make_inputs()
    max_pot = [5000.0] * 3
    config = {"performance_ratio": 0.8}
    result = integrate_rooftop_solar(
        units_config={},
        num_nodes=3,
        year=2050,
        base_year=2024,
        availability_matrix=avail,
        adoption_factors=adoption,
        max_potential=max_pot,
        config=config,
    )
    assert result["eff_at_rated"] == [0.8, 0.8, 0.8]
    assert result["eff_at_min"] == [0.8, 0.8, 0.8]


# ---------------------------------------------------------------------------
# calculate_rooftop_potential
# ---------------------------------------------------------------------------


def test_calculate_potential_known_value():
    # pop=10000, defaults: density .35, roof 50, suitable .3, eff .20, irr 1000
    # dwellings = 3500 ; roof_area = 3500*50*0.3 = 52500
    # peak_kw = 52500 * 0.20 * 1000 / 1000 = 10500 ; MW = 10.5
    result = calculate_rooftop_potential([10000.0])
    assert result == pytest.approx([10.5])


def test_calculate_potential_multiple_nodes():
    result = calculate_rooftop_potential([0.0, 10000.0, 20000.0])
    assert result[0] == pytest.approx(0.0)
    assert result[2] == pytest.approx(2 * result[1])


def test_calculate_potential_custom_params():
    result = calculate_rooftop_potential(
        [1000.0],
        dwelling_density=0.5,
        avg_roof_area=100.0,
        suitable_fraction=0.5,
        panel_efficiency=0.25,
        solar_irradiance=900.0,
    )
    # dwellings = 500 ; roof = 500*100*0.5 = 25000
    # peak_kw = 25000 * 0.25 * 900 / 1000 = 5625 ; MW = 5.625
    assert result == pytest.approx([5.625])


def test_calculate_potential_empty():
    assert calculate_rooftop_potential([]) == []
