"""
Comprehensive tests for esfex.utils.helpers module.

Covers all public functions and the BoundaryConditions dataclass.
"""

import numpy as np
import pandas as pd
import pytest

from esfex.utils.helpers import (
    BoundaryConditions,
    adjust_investment_limits,
    adjust_transmission_parameters,
    calculate_co2_emissions,
    calculate_renewable_penetration,
    extract_boundary_conditions,
    extract_ev_profiles,
    extract_inertia_limit,
    extract_sectoral_demand,
    initialize_battery_soc,
    initialize_ev_soc,
    initialize_generator_status,
)


# ──────────────────────────────────────────────────────────────────────
# BoundaryConditions
# ──────────────────────────────────────────────────────────────────────


class TestBoundaryConditions:
    """Tests for the BoundaryConditions dataclass."""

    def test_creation_defaults(self):
        """Default construction gives empty dicts."""
        bc = BoundaryConditions()
        assert bc.battery_soc == {}
        assert bc.generator_status == {}
        assert bc.ev_soc == {}

    def test_creation_with_values(self):
        """Construction with explicit values stores them correctly."""
        bat = {0: {0: 0.8, 1: 0.6}}
        gen = {0: {0: 1}}
        ev = {0: 0.4}
        bc = BoundaryConditions(battery_soc=bat, generator_status=gen, ev_soc=ev)
        assert bc.battery_soc == bat
        assert bc.generator_status == gen
        assert bc.ev_soc == ev

    def test_to_dict(self):
        """to_dict() returns a plain dict with the three keys."""
        bc = BoundaryConditions(
            battery_soc={0: {0: 0.5}},
            generator_status={0: {0: 1}},
            ev_soc={0: 0.3},
        )
        d = bc.to_dict()
        assert isinstance(d, dict)
        assert set(d.keys()) == {"battery_soc", "generator_status", "ev_soc"}
        assert d["battery_soc"] == {0: {0: 0.5}}
        assert d["ev_soc"] == {0: 0.3}

    def test_from_dict_roundtrip(self):
        """from_dict(to_dict()) reproduces the original object."""
        bc = BoundaryConditions(
            battery_soc={0: {0: 0.9, 1: 0.1}},
            generator_status={1: {0: 0}},
            ev_soc={0: 0.7, 1: 0.2},
        )
        bc2 = BoundaryConditions.from_dict(bc.to_dict())
        assert bc2.battery_soc == bc.battery_soc
        assert bc2.generator_status == bc.generator_status
        assert bc2.ev_soc == bc.ev_soc

    def test_from_dict_missing_keys(self):
        """from_dict() falls back to empty dicts for missing keys."""
        bc = BoundaryConditions.from_dict({})
        assert bc.battery_soc == {}
        assert bc.generator_status == {}
        assert bc.ev_soc == {}

    def test_default_factory_independence(self):
        """Each instance gets its own default dict (no shared mutable state)."""
        bc1 = BoundaryConditions()
        bc2 = BoundaryConditions()
        bc1.battery_soc[0] = {0: 0.5}
        assert bc2.battery_soc == {}


# ──────────────────────────────────────────────────────────────────────
# initialize_battery_soc
# ──────────────────────────────────────────────────────────────────────


class TestInitializeBatterySoc:
    """Tests for initialize_battery_soc()."""

    def test_two_batteries_three_nodes(self):
        """2 batteries x 3 nodes with explicit soc_initial."""
        batteries = [
            {"soc_initial": [0.3, 0.4, 0.5]},
            {"soc_initial": [0.6, 0.7, 0.8]},
        ]
        result = initialize_battery_soc(batteries, num_nodes=3)
        assert result == {
            0: {0: 0.3, 1: 0.4, 2: 0.5},
            1: {0: 0.6, 1: 0.7, 2: 0.8},
        }

    def test_default_soc_when_key_missing(self):
        """Default 0.5 for every node when soc_initial is absent."""
        batteries = [{}]
        result = initialize_battery_soc(batteries, num_nodes=2)
        assert result == {0: {0: 0.5, 1: 0.5}}

    def test_short_soc_initial_list(self):
        """Nodes beyond the soc_initial list get default 0.5."""
        batteries = [{"soc_initial": [0.9]}]
        result = initialize_battery_soc(batteries, num_nodes=3)
        assert result[0][0] == 0.9
        assert result[0][1] == 0.5
        assert result[0][2] == 0.5

    def test_empty_battery_list(self):
        """No batteries returns empty dict."""
        assert initialize_battery_soc([], num_nodes=2) == {}

    def test_zero_nodes(self):
        """Zero nodes means inner dicts are empty."""
        batteries = [{"soc_initial": [0.5]}]
        result = initialize_battery_soc(batteries, num_nodes=0)
        assert result == {0: {}}


# ──────────────────────────────────────────────────────────────────────
# initialize_generator_status
# ──────────────────────────────────────────────────────────────────────


class TestInitializeGeneratorStatus:
    """Tests for initialize_generator_status()."""

    def test_generators_with_rated_power(self):
        """Generators with rated_power > 0 start ON (1)."""
        generators = [
            {"rated_power": [100, 0]},
            {"rated_power": [0, 200]},
        ]
        result = initialize_generator_status(generators, num_nodes=2)
        assert result == {
            0: {0: 1, 1: 0},
            1: {0: 0, 1: 1},
        }

    def test_generators_no_rated_power(self):
        """Missing rated_power defaults to all-zero (OFF)."""
        generators = [{}]
        result = initialize_generator_status(generators, num_nodes=2)
        assert result == {0: {0: 0, 1: 0}}

    def test_short_rated_power_list(self):
        """Nodes beyond rated_power list are OFF."""
        generators = [{"rated_power": [50]}]
        result = initialize_generator_status(generators, num_nodes=3)
        assert result[0] == {0: 1, 1: 0, 2: 0}

    def test_empty_generator_list(self):
        """No generators returns empty dict."""
        assert initialize_generator_status([], num_nodes=2) == {}

    def test_all_positive_rated_power(self):
        """All nodes with power start ON."""
        generators = [{"rated_power": [10, 20, 30]}]
        result = initialize_generator_status(generators, num_nodes=3)
        assert all(v == 1 for v in result[0].values())


# ──────────────────────────────────────────────────────────────────────
# initialize_ev_soc
# ──────────────────────────────────────────────────────────────────────


class TestInitializeEvSoc:
    """Tests for initialize_ev_soc()."""

    def test_default_soc(self):
        """Without ev_initial_soc, every node gets 0.5."""
        result = initialize_ev_soc(num_nodes=3)
        assert result == {0: 0.5, 1: 0.5, 2: 0.5}

    def test_custom_values(self):
        """Explicit ev_initial_soc list is respected."""
        result = initialize_ev_soc(num_nodes=2, ev_initial_soc=[0.3, 0.7])
        assert result == {0: 0.3, 1: 0.7}

    def test_short_ev_initial_soc_list(self):
        """Nodes beyond the list fall back to 0.5."""
        result = initialize_ev_soc(num_nodes=3, ev_initial_soc=[0.9])
        assert result == {0: 0.9, 1: 0.5, 2: 0.5}

    def test_zero_nodes(self):
        """Zero nodes returns empty dict."""
        assert initialize_ev_soc(num_nodes=0) == {}


# ──────────────────────────────────────────────────────────────────────
# extract_inertia_limit
# ──────────────────────────────────────────────────────────────────────


class TestExtractInertiaLimit:
    """Tests for extract_inertia_limit()."""

    def test_dict_input_exact_keys(self):
        """Dict with matching hour keys returns correct window."""
        inertia = {0: 10.0, 1: 20.0, 2: 30.0, 3: 40.0}
        result = extract_inertia_limit(inertia, start_hour=1, window_hours=2)
        assert result == {0: 20.0, 1: 30.0}

    def test_dict_input_missing_keys_falls_back_to_key_zero(self):
        """Missing hour keys fall back to value at key 0."""
        inertia = {0: 5.0}
        result = extract_inertia_limit(inertia, start_hour=10, window_hours=3)
        assert result == {0: 5.0, 1: 5.0, 2: 5.0}

    def test_dict_input_missing_keys_and_no_zero(self):
        """Missing keys and no key-0 fall back to 0."""
        inertia = {5: 99.0}
        result = extract_inertia_limit(inertia, start_hour=0, window_hours=2)
        # t=0: inertia.get(0, inertia.get(0,0)) -> key 0 missing, default 0
        # t=1: inertia.get(1, inertia.get(0,0)) -> key 1 missing, default 0
        assert result == {0: 0, 1: 0}

    def test_non_dict_input_returns_zeros(self):
        """Non-dict input (e.g. None, list) yields all zeros."""
        result = extract_inertia_limit(None, start_hour=0, window_hours=3)
        assert result == {0: 0, 1: 0, 2: 0}

    def test_non_dict_input_list(self):
        """A list is not a dict, so zeros are returned."""
        result = extract_inertia_limit([10, 20], start_hour=0, window_hours=2)
        assert result == {0: 0, 1: 0}

    def test_zero_window_hours(self):
        """Window of zero hours returns empty dict."""
        result = extract_inertia_limit({0: 1.0}, start_hour=0, window_hours=0)
        assert result == {}


# ──────────────────────────────────────────────────────────────────────
# extract_sectoral_demand
# ──────────────────────────────────────────────────────────────────────


class TestExtractSectoralDemand:
    """Tests for extract_sectoral_demand()."""

    def test_none_input(self):
        """None input returns None."""
        assert extract_sectoral_demand(None, 0, 24) is None

    def test_normal_extraction(self):
        """Slices the demand arrays by [start_hour:end_hour, :]."""
        arr = np.arange(48).reshape(24, 2).astype(float)
        sectoral = {"residential": arr, "industrial": arr * 2}
        result = extract_sectoral_demand(sectoral, start_hour=2, end_hour=5)
        np.testing.assert_array_equal(result["residential"], arr[2:5, :])
        np.testing.assert_array_equal(result["industrial"], arr[2:5, :] * 2)

    def test_ndarray_slicing_shape(self):
        """Output shape matches (end_hour - start_hour, num_nodes)."""
        arr = np.ones((100, 3))
        result = extract_sectoral_demand({"s": arr}, start_hour=10, end_hour=30)
        assert result["s"].shape == (20, 3)

    def test_non_ndarray_values_are_skipped(self):
        """Non-ndarray values in the dict are silently skipped."""
        sectoral = {"bad": [1, 2, 3], "good": np.ones((10, 2))}
        result = extract_sectoral_demand(sectoral, 0, 5)
        assert "bad" not in result
        assert "good" in result

    def test_empty_dict(self):
        """Empty sectoral dict returns empty dict (not None)."""
        result = extract_sectoral_demand({}, 0, 5)
        assert result == {}


# ──────────────────────────────────────────────────────────────────────
# extract_ev_profiles
# ──────────────────────────────────────────────────────────────────────


class TestExtractEvProfiles:
    """Tests for extract_ev_profiles()."""

    def test_dataframe_charging(self):
        """DataFrame ev_charging is sliced via iloc."""
        ev_profiles = {"standard_charging": {"charging_profile": None}}
        df = pd.DataFrame(np.arange(48).reshape(24, 2), columns=["n0", "n1"])
        result = extract_ev_profiles(ev_profiles, df, None, start_hour=2, end_hour=5)
        expected = df.iloc[2:5, :].values
        np.testing.assert_array_equal(
            result["standard_charging"]["charging_profile"], expected
        )

    def test_ndarray_charging(self):
        """ndarray ev_charging is sliced directly."""
        ev_profiles = {"standard_charging": {"charging_profile": None}}
        arr = np.ones((24, 2))
        result = extract_ev_profiles(ev_profiles, arr, None, start_hour=0, end_hour=10)
        np.testing.assert_array_equal(
            result["standard_charging"]["charging_profile"], arr[0:10]
        )

    def test_dataframe_v2g(self):
        """DataFrame v2g_availability is sliced via iloc."""
        ev_profiles = {"V2G": {"availability_profile": None}}
        df = pd.DataFrame(np.ones((24, 2)))
        result = extract_ev_profiles(ev_profiles, None, df, start_hour=5, end_hour=10)
        np.testing.assert_array_equal(
            result["V2G"]["availability_profile"], df.iloc[5:10, :].values
        )

    def test_ndarray_v2g(self):
        """ndarray v2g_availability is sliced directly."""
        ev_profiles = {"V2G": {"availability_profile": None}}
        arr = np.zeros((24, 2))
        result = extract_ev_profiles(ev_profiles, None, arr, start_hour=1, end_hour=4)
        np.testing.assert_array_equal(
            result["V2G"]["availability_profile"], arr[1:4]
        )

    def test_both_charging_and_v2g(self):
        """Both standard_charging and V2G are extracted together."""
        ev_profiles = {
            "standard_charging": {"charging_profile": None},
            "V2G": {"availability_profile": None},
        }
        charge_arr = np.ones((24, 2))
        v2g_arr = np.zeros((24, 2))
        result = extract_ev_profiles(
            ev_profiles, charge_arr, v2g_arr, start_hour=0, end_hour=6
        )
        assert result["standard_charging"]["charging_profile"].shape == (6, 2)
        assert result["V2G"]["availability_profile"].shape == (6, 2)

    def test_no_matching_keys(self):
        """Profiles without standard_charging or V2G are returned unchanged."""
        ev_profiles = {"other_mode": {"some_key": 42}}
        result = extract_ev_profiles(ev_profiles, None, None, 0, 10)
        assert result["other_mode"]["some_key"] == 42

    def test_does_not_mutate_original(self):
        """Original ev_profiles dict is not mutated (deepcopy is used)."""
        ev_profiles = {"standard_charging": {"charging_profile": "original"}}
        arr = np.ones((24, 2))
        extract_ev_profiles(ev_profiles, arr, None, 0, 5)
        assert ev_profiles["standard_charging"]["charging_profile"] == "original"


# ──────────────────────────────────────────────────────────────────────
# extract_boundary_conditions
# ──────────────────────────────────────────────────────────────────────


class TestExtractBoundaryConditions:
    """Tests for extract_boundary_conditions()."""

    def test_full_solution(self):
        """Extracts last-timestep values from a complete solution dict."""
        solution = {
            "bat_soc": [
                [[0.3, 0.4, 0.5], [0.6, 0.7, 0.8]],  # bat 0: 2 nodes x 3 hours
            ],
            "gen_status": [
                [[0, 0, 1], [1, 1, 0]],  # gen 0: 2 nodes x 3 hours
            ],
            "EV_soc": [
                [0.2, 0.3, 0.4],  # node 0: 3 hours
                [0.5, 0.6, 0.7],  # node 1: 3 hours
            ],
        }
        bc = extract_boundary_conditions(
            solution, num_batteries=1, num_generators=1, num_nodes=2
        )
        assert bc.battery_soc[0][0] == 0.5
        assert bc.battery_soc[0][1] == 0.8
        assert bc.generator_status[0][0] == 1
        assert bc.generator_status[0][1] == 0
        assert bc.ev_soc[0] == 0.4
        assert bc.ev_soc[1] == 0.7

    def test_empty_solution(self):
        """Empty solution dict falls back to defaults."""
        bc = extract_boundary_conditions(
            {}, num_batteries=1, num_generators=1, num_nodes=2
        )
        assert bc.battery_soc == {0: {0: 0.5, 1: 0.5}}
        assert bc.generator_status == {0: {0: 0, 1: 0}}
        assert bc.ev_soc == {0: 0.5, 1: 0.5}

    def test_partial_bat_soc_with_defaults(self):
        """Falls back to default_battery_soc when solution data is empty."""
        solution = {"bat_soc": [
            [[], [0.1, 0.2, 0.3]],  # bat 0: node 0 empty, node 1 has data
        ]}
        default_bat = [{"soc_initial": [0.9, 0.8]}]
        bc = extract_boundary_conditions(
            solution,
            num_batteries=1,
            num_generators=0,
            num_nodes=2,
            default_battery_soc=default_bat,
        )
        # node 0: empty list -> truthy check fails -> use default_battery_soc
        assert bc.battery_soc[0][0] == 0.9
        # node 1: has data -> last value
        assert bc.battery_soc[0][1] == 0.3

    def test_default_ev_soc_fallback(self):
        """Uses default_ev_soc when EV_soc is absent from solution."""
        bc = extract_boundary_conditions(
            {},
            num_batteries=0,
            num_generators=0,
            num_nodes=2,
            default_ev_soc=[0.6, 0.7],
        )
        assert bc.ev_soc == {0: 0.6, 1: 0.7}

    def test_returns_boundary_conditions_instance(self):
        """Return type is BoundaryConditions."""
        bc = extract_boundary_conditions(
            {}, num_batteries=0, num_generators=0, num_nodes=0
        )
        assert isinstance(bc, BoundaryConditions)

    def test_zero_everything(self):
        """Zero batteries, generators, and nodes yields empty inner dicts."""
        bc = extract_boundary_conditions(
            {}, num_batteries=0, num_generators=0, num_nodes=0
        )
        assert bc.battery_soc == {}
        assert bc.generator_status == {}
        assert bc.ev_soc == {}


# ──────────────────────────────────────────────────────────────────────
# adjust_investment_limits
# ──────────────────────────────────────────────────────────────────────


class TestAdjustInvestmentLimits:
    """Tests for adjust_investment_limits()."""

    def test_renewable_type_scales_invest_max_power(self):
        """Renewable invest_max_power grows by (1+rate)^years."""
        unit = {"type": "Renewable", "invest_max_power": [100.0, 200.0]}
        adjust_investment_limits(unit, year=2027, base_year=2025, growth_rate=0.5)
        factor = 1.5 ** 2
        assert unit["invest_max_power"] == pytest.approx([100 * factor, 200 * factor])

    def test_storage_type_scales_both_power_and_capacity(self):
        """Storage scales invest_max_power AND invest_max_capacity."""
        unit = {
            "type": "Storage",
            "invest_max_power": [50.0],
            "invest_max_capacity": [400.0],
        }
        adjust_investment_limits(unit, year=2026, base_year=2025, growth_rate=0.5)
        factor = 1.5
        assert unit["invest_max_power"] == pytest.approx([50 * factor])
        assert unit["invest_max_capacity"] == pytest.approx([400 * factor])

    def test_other_type_unchanged(self):
        """Non-Renewable/Storage types are not modified."""
        unit = {"type": "Thermal", "invest_max_power": [100.0]}
        original = unit["invest_max_power"].copy()
        adjust_investment_limits(unit, year=2030, base_year=2025)
        assert unit["invest_max_power"] == original

    def test_base_year_equals_current_year(self):
        """No change when year == base_year (growth factor = 1)."""
        unit = {"type": "Renewable", "invest_max_power": [100.0]}
        adjust_investment_limits(unit, year=2025, base_year=2025)
        assert unit["invest_max_power"] == [100.0]

    def test_missing_invest_max_power_key(self):
        """No error if invest_max_power key is absent."""
        unit = {"type": "Renewable"}
        adjust_investment_limits(unit, year=2030, base_year=2025)
        assert "invest_max_power" not in unit

    def test_storage_without_invest_max_capacity(self):
        """Storage without invest_max_capacity only scales power."""
        unit = {"type": "Storage", "invest_max_power": [10.0]}
        adjust_investment_limits(unit, year=2026, base_year=2025, growth_rate=1.0)
        assert unit["invest_max_power"] == pytest.approx([20.0])
        assert "invest_max_capacity" not in unit


# ──────────────────────────────────────────────────────────────────────
# adjust_transmission_parameters
# ──────────────────────────────────────────────────────────────────────


class TestAdjustTransmissionParameters:
    """Tests for adjust_transmission_parameters()."""

    def test_cost_reduction(self):
        """Investment cost decreases by (1 - rate)^years."""
        nodes = {"transference_invest_cost": [1000.0, 2000.0]}
        adjust_transmission_parameters(
            nodes, year=2027, base_year=2025, cost_reduction_rate=0.03
        )
        factor = 0.97 ** 2
        assert nodes["transference_invest_cost"] == pytest.approx(
            [1000 * factor, 2000 * factor]
        )

    def test_capacity_growth(self):
        """Max investment capacity grows by (1 + rate)^years."""
        nodes = {"transference_invest_max": [500.0]}
        adjust_transmission_parameters(
            nodes, year=2026, base_year=2025, capacity_growth_rate=0.5
        )
        assert nodes["transference_invest_max"] == pytest.approx([750.0])

    def test_both_keys_present(self):
        """Both cost and capacity keys are adjusted simultaneously."""
        nodes = {
            "transference_invest_cost": [100.0],
            "transference_invest_max": [100.0],
        }
        adjust_transmission_parameters(nodes, year=2030, base_year=2025)
        # cost: 100 * 0.97^5
        # capacity: 100 * 1.5^5
        assert nodes["transference_invest_cost"][0] < 100.0
        assert nodes["transference_invest_max"][0] > 100.0

    def test_no_matching_keys(self):
        """No error when neither key is present."""
        nodes = {"some_other_key": 42}
        adjust_transmission_parameters(nodes, year=2030, base_year=2025)
        assert nodes == {"some_other_key": 42}

    def test_same_year_no_change(self):
        """No change when year == base_year."""
        nodes = {
            "transference_invest_cost": [100.0],
            "transference_invest_max": [200.0],
        }
        adjust_transmission_parameters(nodes, year=2025, base_year=2025)
        assert nodes["transference_invest_cost"] == [100.0]
        assert nodes["transference_invest_max"] == [200.0]


# ──────────────────────────────────────────────────────────────────────
# calculate_renewable_penetration
# ──────────────────────────────────────────────────────────────────────


class TestCalculateRenewablePenetration:
    """Tests for calculate_renewable_penetration()."""

    def test_mixed_generators(self):
        """Mixed renewable and thermal generators yield correct penetration."""
        # gen_output shape: [gen_idx, node, hour]
        gen_output = np.ones((2, 1, 24))  # 2 gens, 1 node, 24 hours
        gen_output[0, :, :] = 60.0  # gen 0: renewable
        gen_output[1, :, :] = 40.0  # gen 1: thermal
        generators = [
            {"type": "Renewable"},
            {"type": "Thermal"},
        ]
        total, renewable, penetration = calculate_renewable_penetration(
            gen_output, generators
        )
        assert total == pytest.approx(60 * 24 + 40 * 24)
        assert renewable == pytest.approx(60 * 24)
        assert penetration == pytest.approx(0.6)

    def test_all_renewable(self):
        """100% renewable penetration."""
        gen_output = np.ones((1, 2, 10)) * 50
        generators = [{"type": "Renewable"}]
        total, renewable, penetration = calculate_renewable_penetration(
            gen_output, generators
        )
        assert total == renewable
        assert penetration == pytest.approx(1.0)

    def test_no_generation(self):
        """Zero generation returns zero penetration (no division by zero)."""
        gen_output = np.zeros((1, 1, 24))
        generators = [{"type": "Thermal"}]
        total, renewable, penetration = calculate_renewable_penetration(
            gen_output, generators
        )
        assert total == 0.0
        assert renewable == 0.0
        assert penetration == 0.0

    def test_no_renewable(self):
        """All thermal generators yield 0% penetration."""
        gen_output = np.ones((2, 1, 24)) * 100
        generators = [{"type": "Thermal"}, {"type": "Nuclear"}]
        total, renewable, penetration = calculate_renewable_penetration(
            gen_output, generators
        )
        assert renewable == 0.0
        assert penetration == 0.0

    def test_multiple_nodes(self):
        """Renewable penetration is calculated across all nodes."""
        gen_output = np.zeros((2, 3, 10))
        gen_output[0, :, :] = 30.0  # renewable across 3 nodes
        gen_output[1, :, :] = 70.0  # thermal across 3 nodes
        generators = [{"type": "Renewable"}, {"type": "Thermal"}]
        total, renewable, penetration = calculate_renewable_penetration(
            gen_output, generators
        )
        assert total == pytest.approx((30 + 70) * 3 * 10)
        assert renewable == pytest.approx(30 * 3 * 10)
        assert penetration == pytest.approx(0.3)


# ──────────────────────────────────────────────────────────────────────
# calculate_co2_emissions
# ──────────────────────────────────────────────────────────────────────


class TestCalculateCo2Emissions:
    """Tests for calculate_co2_emissions()."""

    def test_with_fuel_co2_factors(self):
        """Thermal generators emit CO2 based on fuel type and output."""
        gen_output = np.zeros((2, 1, 24))
        gen_output[0, :, :] = 100.0  # coal
        gen_output[1, :, :] = 50.0   # gas
        generators = [
            {"type": "Thermal", "fuel": "Coal"},
            {"type": "Thermal", "fuel": "Natural Gas"},
        ]
        fuel_co2 = {"Coal": 0.9, "Natural Gas": 0.4}
        emissions = calculate_co2_emissions(gen_output, generators, fuel_co2)
        expected = 100 * 24 * 0.9 + 50 * 24 * 0.4
        assert emissions == pytest.approx(expected)

    def test_renewable_generators_zero_emissions(self):
        """Renewable generators contribute zero emissions."""
        gen_output = np.ones((2, 1, 24)) * 100
        generators = [
            {"type": "Renewable"},
            {"type": "Renewable"},
        ]
        fuel_co2 = {"Coal": 0.9}
        emissions = calculate_co2_emissions(gen_output, generators, fuel_co2)
        assert emissions == 0.0

    def test_unknown_fuel_type_defaults_to_zero(self):
        """Unknown fuel not in fuel_co2 dict contributes zero emissions."""
        gen_output = np.ones((1, 1, 10)) * 100
        generators = [{"type": "Thermal", "fuel": "Hydrogen"}]
        fuel_co2 = {"Coal": 0.9}
        emissions = calculate_co2_emissions(gen_output, generators, fuel_co2)
        assert emissions == 0.0

    def test_missing_fuel_key_defaults_to_natural_gas(self):
        """Generator without 'fuel' key defaults to 'Natural Gas'."""
        gen_output = np.ones((1, 1, 10)) * 100
        generators = [{"type": "Thermal"}]  # no 'fuel' key
        fuel_co2 = {"Natural Gas": 0.5}
        emissions = calculate_co2_emissions(gen_output, generators, fuel_co2)
        expected = 100 * 10 * 0.5
        assert emissions == pytest.approx(expected)

    def test_mixed_renewable_and_thermal(self):
        """Only thermal generators contribute to emissions."""
        gen_output = np.ones((3, 2, 12))
        gen_output[0, :, :] = 80.0   # renewable
        gen_output[1, :, :] = 60.0   # thermal coal
        gen_output[2, :, :] = 40.0   # thermal gas
        generators = [
            {"type": "Renewable"},
            {"type": "Thermal", "fuel": "Coal"},
            {"type": "Thermal", "fuel": "Natural Gas"},
        ]
        fuel_co2 = {"Coal": 1.0, "Natural Gas": 0.5}
        emissions = calculate_co2_emissions(gen_output, generators, fuel_co2)
        expected = 60 * 2 * 12 * 1.0 + 40 * 2 * 12 * 0.5
        assert emissions == pytest.approx(expected)

    def test_empty_fuel_co2_dict(self):
        """Empty fuel_co2 dict yields zero emissions."""
        gen_output = np.ones((1, 1, 10)) * 100
        generators = [{"type": "Thermal", "fuel": "Coal"}]
        emissions = calculate_co2_emissions(gen_output, generators, {})
        assert emissions == 0.0
