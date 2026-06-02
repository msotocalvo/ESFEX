"""
Tests for helper functions in esfex.bridge.adapters that do NOT require Julia.

Covers:
- _haversine_km: haversine distance calculation
- _pwl_segments_from_config: loss model mapping for operational problems
- _pwl_segments_from_config_master: loss model mapping for master problem
- _RENEWABLE_FUELS: renewable fuel type set
- _compute_geographic_fuel_adjustments: distance-based fuel cost adjustments
"""

import math
import types

import numpy as np
import pytest

from esfex.bridge.adapters import (
    _RENEWABLE_FUELS,
    _compute_geographic_fuel_adjustments,
    _haversine_km,
    _pwl_segments_from_config,
    _pwl_segments_from_config_master,
)


# ──────────────────────────────────────────────────────────────────────
# _haversine_km
# ──────────────────────────────────────────────────────────────────────


class TestHaversineKm:
    """Tests for _haversine_km(lat1, lng1, lat2, lng2)."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself is zero."""
        assert _haversine_km(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_same_point_origin(self):
        """Distance from origin (0,0) to itself is zero."""
        assert _haversine_km(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_new_york_to_london(self):
        """Known distance: New York (40.7128, -74.0060) to London (51.5074, -0.1278).

        Accepted reference: ~5570 km.
        """
        dist = _haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert dist == pytest.approx(5570, rel=0.02)

    def test_paris_to_berlin(self):
        """Known distance: Paris (48.8566, 2.3522) to Berlin (52.5200, 13.4050).

        Accepted reference: ~878 km.
        """
        dist = _haversine_km(48.8566, 2.3522, 52.5200, 13.4050)
        assert dist == pytest.approx(878, rel=0.02)

    def test_equator_one_degree_longitude(self):
        """One degree of longitude at the equator is ~111.2 km."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        assert dist == pytest.approx(111.2, rel=0.01)

    def test_one_degree_latitude(self):
        """One degree of latitude is ~111.2 km regardless of longitude."""
        dist = _haversine_km(0.0, 0.0, 1.0, 0.0)
        assert dist == pytest.approx(111.2, rel=0.01)

    def test_symmetry(self):
        """Distance(A, B) == Distance(B, A)."""
        d1 = _haversine_km(35.0, 139.0, -33.0, 151.0)
        d2 = _haversine_km(-33.0, 151.0, 35.0, 139.0)
        assert d1 == pytest.approx(d2)

    def test_antipodal_points(self):
        """Antipodal points (opposite sides of the Earth) are ~20015 km apart."""
        dist = _haversine_km(0.0, 0.0, 0.0, 180.0)
        assert dist == pytest.approx(20015, rel=0.01)

    def test_north_pole_to_south_pole(self):
        """North Pole to South Pole is ~20015 km (half circumference)."""
        dist = _haversine_km(90.0, 0.0, -90.0, 0.0)
        assert dist == pytest.approx(20015, rel=0.01)

    def test_short_distance(self):
        """Two nearby points should give a small distance."""
        # ~1 meter apart at equator (0.00001 degrees of longitude)
        dist = _haversine_km(0.0, 0.0, 0.0, 0.00001)
        assert dist < 0.01  # less than 10 meters

    def test_return_type_is_float(self):
        """Return type is always float."""
        result = _haversine_km(10.0, 20.0, 30.0, 40.0)
        assert isinstance(result, float)

    def test_uses_earth_radius_6371(self):
        """Verify the function uses Earth radius of 6371 km.

        Quarter-circle (0,0) to (90,0) should be pi/2 * 6371 ~ 10007.5 km.
        """
        dist = _haversine_km(0.0, 0.0, 90.0, 0.0)
        expected = math.pi / 2 * 6371.0
        assert dist == pytest.approx(expected, rel=1e-6)

    def test_negative_latitudes(self):
        """Southern hemisphere coordinates work correctly."""
        dist = _haversine_km(-33.8688, 151.2093, -37.8136, 144.9631)
        # Sydney to Melbourne: ~714 km
        assert dist == pytest.approx(714, rel=0.03)


# ──────────────────────────────────────────────────────────────────────
# _pwl_segments_from_config
# ──────────────────────────────────────────────────────────────────────


class TestPwlSegmentsFromConfig:
    """Tests for _pwl_segments_from_config(dc_config)."""

    def test_pwl_mode_default_segments(self):
        """PWL mode with no pwl_loss_segments returns default 3."""
        cfg = types.SimpleNamespace(loss_model="pwl")
        assert _pwl_segments_from_config(cfg) == 3

    def test_pwl_mode_custom_segments(self):
        """PWL mode with explicit pwl_loss_segments returns that value."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments=5)
        assert _pwl_segments_from_config(cfg) == 5

    def test_pwl_mode_segments_as_float(self):
        """PWL segments specified as float are converted to int."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments=4.0)
        result = _pwl_segments_from_config(cfg)
        assert result == 4
        assert isinstance(result, int)

    def test_linear_mode(self):
        """Linear loss model returns -1."""
        cfg = types.SimpleNamespace(loss_model="linear")
        assert _pwl_segments_from_config(cfg) == -1

    def test_none_mode(self):
        """None (lossless) mode returns 0."""
        cfg = types.SimpleNamespace(loss_model="none")
        assert _pwl_segments_from_config(cfg) == 0

    def test_missing_loss_model_defaults_to_pwl(self):
        """Missing loss_model attribute defaults to 'pwl' (returns 3)."""
        cfg = types.SimpleNamespace()
        assert _pwl_segments_from_config(cfg) == 3

    def test_missing_both_attrs_defaults(self):
        """Missing both loss_model and pwl_loss_segments returns default 3."""
        cfg = object()  # no attributes at all
        assert _pwl_segments_from_config(cfg) == 3

    def test_unknown_mode_returns_zero(self):
        """Any unrecognized loss_model string falls to else branch (0)."""
        cfg = types.SimpleNamespace(loss_model="quadratic")
        assert _pwl_segments_from_config(cfg) == 0

    def test_pwl_mode_segments_one(self):
        """Single segment PWL."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments=1)
        assert _pwl_segments_from_config(cfg) == 1

    def test_pwl_mode_segments_ten(self):
        """Ten segment PWL."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments=10)
        assert _pwl_segments_from_config(cfg) == 10


# ──────────────────────────────────────────────────────────────────────
# _pwl_segments_from_config_master
# ──────────────────────────────────────────────────────────────────────


class TestPwlSegmentsFromConfigMaster:
    """Tests for _pwl_segments_from_config_master(dc_config)."""

    def test_pwl_mode_default_segments_master(self):
        """PWL mode with no pwl_loss_segments_master returns default 2."""
        cfg = types.SimpleNamespace(loss_model="pwl")
        assert _pwl_segments_from_config_master(cfg) == 2

    def test_pwl_mode_custom_segments_master(self):
        """PWL mode with explicit pwl_loss_segments_master returns that value."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments_master=4)
        assert _pwl_segments_from_config_master(cfg) == 4

    def test_linear_mode_master(self):
        """Linear loss model returns -1 for master."""
        cfg = types.SimpleNamespace(loss_model="linear")
        assert _pwl_segments_from_config_master(cfg) == -1

    def test_none_mode_master(self):
        """None (lossless) mode returns 0 for master."""
        cfg = types.SimpleNamespace(loss_model="none")
        assert _pwl_segments_from_config_master(cfg) == 0

    def test_missing_loss_model_master(self):
        """Missing loss_model defaults to 'pwl' with master default (2)."""
        cfg = types.SimpleNamespace()
        assert _pwl_segments_from_config_master(cfg) == 2

    def test_master_default_differs_from_operational(self):
        """Master default (2) differs from operational default (3)."""
        cfg = types.SimpleNamespace(loss_model="pwl")
        assert _pwl_segments_from_config_master(cfg) != _pwl_segments_from_config(cfg)
        assert _pwl_segments_from_config_master(cfg) == 2
        assert _pwl_segments_from_config(cfg) == 3

    def test_unknown_mode_returns_zero_master(self):
        """Unrecognized loss_model returns 0 for master too."""
        cfg = types.SimpleNamespace(loss_model="fancy")
        assert _pwl_segments_from_config_master(cfg) == 0

    def test_pwl_mode_segments_master_as_float(self):
        """Master segments specified as float are converted to int."""
        cfg = types.SimpleNamespace(loss_model="pwl", pwl_loss_segments_master=6.0)
        result = _pwl_segments_from_config_master(cfg)
        assert result == 6
        assert isinstance(result, int)


# ──────────────────────────────────────────────────────────────────────
# _RENEWABLE_FUELS
# ──────────────────────────────────────────────────────────────────────


class TestRenewableFuels:
    """Tests for the _RENEWABLE_FUELS set."""

    def test_contains_sun(self):
        assert "Sun" in _RENEWABLE_FUELS

    def test_contains_wind(self):
        assert "Wind" in _RENEWABLE_FUELS

    def test_contains_water(self):
        assert "Water" in _RENEWABLE_FUELS

    def test_contains_otec(self):
        assert "OTEC" in _RENEWABLE_FUELS

    def test_contains_none_string(self):
        assert "None" in _RENEWABLE_FUELS

    def test_does_not_contain_coal(self):
        assert "Coal" not in _RENEWABLE_FUELS

    def test_does_not_contain_natural_gas(self):
        assert "Natural Gas" not in _RENEWABLE_FUELS

    def test_does_not_contain_hydrogen(self):
        assert "Hydrogen" not in _RENEWABLE_FUELS

    def test_does_not_contain_lowercase_sun(self):
        """Membership is case-sensitive."""
        assert "sun" not in _RENEWABLE_FUELS

    def test_is_a_set(self):
        assert isinstance(_RENEWABLE_FUELS, set)

    def test_has_five_members(self):
        assert len(_RENEWABLE_FUELS) == 5


# ──────────────────────────────────────────────────────────────────────
# _compute_geographic_fuel_adjustments  --  helpers
# ──────────────────────────────────────────────────────────────────────


def _make_gen_cfg(fuel, fuel_cost, eff_at_rated=None, name="TestGen"):
    """Create a minimal generator config SimpleNamespace."""
    return types.SimpleNamespace(
        fuel=fuel,
        fuel_cost=fuel_cost,
        eff_at_rated=eff_at_rated or [0.4],
        name=name,
    )


def _make_fuel_def(energy_content):
    """Create a minimal fuel definition SimpleNamespace."""
    return types.SimpleNamespace(energy_content=energy_content)


def _make_sys_config(
    gui_layout=None,
    generators=None,
    fuels=None,
    storage_facilities=None,
    transport_pipelines=None,
    num_nodes=1,
):
    """Build a minimal SystemConfig mock for _compute_geographic_fuel_adjustments."""
    fuel_infra = types.SimpleNamespace(
        storage_facilities=storage_facilities or {},
        transport_pipelines=transport_pipelines or {},
    )
    nodes = types.SimpleNamespace(num_nodes=num_nodes)
    return types.SimpleNamespace(
        gui_layout=gui_layout,
        fuel_infrastructure=fuel_infra,
        generators=generators or {},
        fuels=fuels or {},
        nodes=nodes,
    )


# ──────────────────────────────────────────────────────────────────────
# _compute_geographic_fuel_adjustments
# ──────────────────────────────────────────────────────────────────────


class TestComputeGeographicFuelAdjustments:
    """Tests for _compute_geographic_fuel_adjustments(sys_config)."""

    # --- early-exit cases ---

    def test_no_layout_returns_empty(self):
        """When gui_layout is None, return empty dict."""
        cfg = _make_sys_config(gui_layout=None)
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_empty_layout_returns_empty(self):
        """When gui_layout is empty dict, return empty dict."""
        cfg = _make_sys_config(gui_layout={})
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_no_generators_in_layout_returns_empty(self):
        """When layout has fuel_storages but no generators, return empty."""
        cfg = _make_sys_config(
            gui_layout={"fuel_storages": {"s0": (0.0, 0.0)}},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_no_storages_in_layout_returns_empty(self):
        """When layout has generators but no fuel_storages, return empty."""
        cfg = _make_sys_config(
            gui_layout={"generators": {"gen0_n0": (0.0, 0.0)}},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_no_generators_in_config_returns_empty(self):
        """Layout present but no generators in sys_config.generators."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (1.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_renewable_fuel_skipped(self):
        """Generators with renewable fuels are not adjusted."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"solar_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (1.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Sun"]}},
            generators={"solar": _make_gen_cfg("Sun", [0.0])},
            fuels={"Sun": _make_fuel_def(1.0)},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_all_renewable_fuels_skipped(self):
        """Every member of _RENEWABLE_FUELS is skipped."""
        for fuel_name in _RENEWABLE_FUELS:
            cfg = _make_sys_config(
                gui_layout={
                    "generators": {"gen_n0": (10.0, 20.0)},
                    "fuel_storages": {"s0": (10.1, 20.1)},
                },
                storage_facilities={"s0": {"fuels": [fuel_name]}},
                generators={"gen": _make_gen_cfg(fuel_name, [5.0])},
                fuels={fuel_name: _make_fuel_def(1.0)},
            )
            result = _compute_geographic_fuel_adjustments(cfg)
            assert result == {}, f"Expected empty for renewable fuel '{fuel_name}'"

    def test_no_fuel_def_returns_empty(self):
        """Generator whose fuel is not in sys_config.fuels is skipped."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (1.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={},  # Coal not defined
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_fuel_def_zero_energy_content_returns_empty(self):
        """Generator whose fuel has energy_content=0 is skipped."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (1.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(0.0)},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_no_matching_fuel_in_supply(self):
        """Generator fuel has no matching storage facility."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (1.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["LNG"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    def test_storage_not_in_positions_skipped(self):
        """Storage facility ID not in layout positions is ignored."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {},  # s0 not here
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
        )
        assert _compute_geographic_fuel_adjustments(cfg) == {}

    # --- successful adjustment cases ---

    def test_basic_adjustment_increases_cost(self):
        """A thermal generator near a fuel storage gets an increased fuel cost."""
        # Generator at (0, 0), storage at (0, 1) => ~111 km apart
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=[0.4])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert "gen0" in result
        assert result["gen0"][0] > 50.0  # cost increased

    def test_adjustment_formula_with_defaults(self):
        """Verify adjustment formula using default cost_per_km and loss_per_km.

        DEFAULT_COST_PER_KM = 0.5
        DEFAULT_LOSS_PER_KM = 0.001
        transport_cost_mwhe = cpk * dist / (energy_content * efficiency)
        loss_factor = min(lpk * dist, 0.5)
        loss_multiplier = 1 / (1 - loss_factor)
        adjusted = original * loss_multiplier + transport_cost_mwhe
        """
        # Use ~111 km distance (0,0) to (0,1)
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        original_cost = 50.0
        energy_content = 8.14
        efficiency = 0.4
        cpk = 0.5  # default
        lpk = 0.001  # default

        transport_cost_mwhe = cpk * dist / (energy_content * efficiency)
        loss_factor = min(lpk * dist, 0.5)
        loss_multiplier = 1.0 / (1.0 - loss_factor)
        expected = original_cost * loss_multiplier + transport_cost_mwhe

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [original_cost], eff_at_rated=[efficiency])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_very_close_generator_not_adjusted(self):
        """Generator closer than 0.01 km to storage is NOT adjusted."""
        # Same location (within 0.01 km)
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 0.000001)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result == {}

    def test_multiple_generators(self):
        """Multiple generators get independent adjustments."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {
                    "coal_plant_n0": (0.0, 0.0),
                    "gas_plant_n0": (0.0, 2.0),
                },
                "fuel_storages": {
                    "coal_depot": (0.0, 1.0),
                    "gas_depot": (0.0, 3.0),
                },
            },
            storage_facilities={
                "coal_depot": {"fuels": ["Coal"]},
                "gas_depot": {"fuels": ["NaturalGas"]},
            },
            generators={
                "coal_plant": _make_gen_cfg("Coal", [40.0], name="Coal Plant"),
                "gas_plant": _make_gen_cfg("NaturalGas", [60.0], name="Gas Plant"),
            },
            fuels={
                "Coal": _make_fuel_def(8.14),
                "NaturalGas": _make_fuel_def(13.1),
            },
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert "coal_plant" in result
        assert "gas_plant" in result
        assert result["coal_plant"][0] > 40.0
        assert result["gas_plant"][0] > 60.0

    def test_nearest_storage_selected(self):
        """Among multiple storages with the same fuel, the nearest is used."""
        # gen at (0,0), near storage at (0,1) ~111km, far storage at (0,5) ~556km
        dist_near = _haversine_km(0.0, 0.0, 0.0, 1.0)
        dist_far = _haversine_km(0.0, 0.0, 0.0, 5.0)
        original_cost = 50.0
        energy_content = 8.14
        efficiency = 0.4
        cpk = 0.5
        lpk = 0.001

        # Expected cost using the nearer storage
        transport_near = cpk * dist_near / (energy_content * efficiency)
        loss_near = min(lpk * dist_near, 0.5)
        mult_near = 1.0 / (1.0 - loss_near)
        expected_near = original_cost * mult_near + transport_near

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {
                    "near": (0.0, 1.0),
                    "far": (0.0, 5.0),
                },
            },
            storage_facilities={
                "near": {"fuels": ["Coal"]},
                "far": {"fuels": ["Coal"]},
            },
            generators={"gen0": _make_gen_cfg("Coal", [original_cost], eff_at_rated=[efficiency])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected_near, rel=1e-6)

    def test_multinode_generator(self):
        """Generator with multiple nodes, only nodes with layout keys get adjusted."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {
                    "gen0_n0": (0.0, 0.0),
                    # node 1 has no layout entry
                },
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0, 55.0], eff_at_rated=[0.4, 0.4])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=2,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert "gen0" in result
        assert result["gen0"][0] > 50.0   # node 0 adjusted
        assert result["gen0"][1] == 55.0  # node 1 unchanged

    def test_legacy_single_fuel_format(self):
        """Storage with 'fuel' key instead of 'fuels' list works (legacy)."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuel": "Coal"}},  # single-fuel legacy
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert "gen0" in result
        assert result["gen0"][0] > 50.0

    def test_transport_pipeline_overrides_default_rates(self):
        """When transport_pipelines exist, derived rates replace defaults."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        original_cost = 50.0
        energy_content = 8.14
        efficiency = 0.4

        # Pipeline: 100 km, transport_cost=200 $/unit, losses=0.05
        # Per-km: cost=2.0 $/unit/km, loss=0.0005 /km
        pipeline_cpk = 200.0 / 100.0  # 2.0
        pipeline_lpk = 0.05 / 100.0   # 0.0005

        transport_cost_mwhe = pipeline_cpk * dist / (energy_content * efficiency)
        loss_factor = min(pipeline_lpk * dist, 0.5)
        loss_multiplier = 1.0 / (1.0 - loss_factor)
        expected = original_cost * loss_multiplier + transport_cost_mwhe

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [original_cost], eff_at_rated=[efficiency])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            transport_pipelines={
                "route0": {
                    "length_km": 100.0,
                    "fuels": ["Coal"],
                    "transport_cost": 200.0,
                    "losses_fraction": 0.05,
                },
            },
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_short_pipeline_skipped(self):
        """Pipelines shorter than 0.5 km are ignored for rate derivation."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
            transport_pipelines={
                "short_route": {
                    "length_km": 0.3,  # too short
                    "fuels": ["Coal"],
                    "transport_cost": 9999.0,  # should be ignored
                    "losses_fraction": 0.99,
                },
            },
            num_nodes=1,
        )
        # With the short pipeline ignored, default rates are used
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        cpk_default = 0.5
        lpk_default = 0.001
        transport = cpk_default * dist / (8.14 * 0.4)
        loss_f = min(lpk_default * dist, 0.5)
        loss_m = 1.0 / (1.0 - loss_f)
        expected = 50.0 * loss_m + transport

        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_zero_efficiency_uses_fallback(self):
        """Generator with eff_at_rated=[0.0] uses fallback efficiency=0.35."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        energy_content = 8.14
        cpk = 0.5
        lpk = 0.001
        fallback_eff = 0.35

        transport = cpk * dist / (energy_content * fallback_eff)
        loss_f = min(lpk * dist, 0.5)
        loss_m = 1.0 / (1.0 - loss_f)
        expected = 50.0 * loss_m + transport

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=[0.0])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_no_eff_at_rated_uses_fallback(self):
        """Generator with eff_at_rated=None uses fallback efficiency=0.35."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        energy_content = 8.14
        fallback_eff = 0.35
        cpk = 0.5
        lpk = 0.001

        transport = cpk * dist / (energy_content * fallback_eff)
        loss_f = min(lpk * dist, 0.5)
        loss_m = 1.0 / (1.0 - loss_f)
        expected = 50.0 * loss_m + transport

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=None)},
            fuels={"Coal": _make_fuel_def(energy_content)},
            num_nodes=1,
        )
        # Fix: eff_at_rated=None makes the condition falsy, uses 0.35
        gen = cfg.generators["gen0"]
        gen.eff_at_rated = None
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_loss_factor_capped_at_50_percent(self):
        """Loss factor is capped at 0.5 even for very long distances."""
        # Use a huge distance: 10000 km, lpk_default=0.001 => raw loss=10.0
        # Should be capped to 0.5
        # Generator at (0,0), storage at (0,90) ~ ~10000 km
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 90.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=[0.4])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        dist = _haversine_km(0.0, 0.0, 0.0, 90.0)
        cpk = 0.5
        lpk = 0.001
        transport = cpk * dist / (8.14 * 0.4)
        # loss_factor capped at 0.5
        loss_m = 1.0 / (1.0 - 0.5)  # = 2.0
        expected = 50.0 * loss_m + transport
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_pipeline_fuel_params_override_route_level(self):
        """Per-fuel fuel_params in pipelines override route-level defaults."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        energy_content = 8.14
        efficiency = 0.4

        # Route-level: transport_cost=100, losses_fraction=0.1
        # Fuel-specific: transport_cost=300, losses_fraction=0.02
        # Pipeline length: 50 km
        # Per-km rates should use fuel-specific: 300/50=6.0, 0.02/50=0.0004
        pipeline_cpk = 300.0 / 50.0
        pipeline_lpk = 0.02 / 50.0

        transport = pipeline_cpk * dist / (energy_content * efficiency)
        loss_f = min(pipeline_lpk * dist, 0.5)
        loss_m = 1.0 / (1.0 - loss_f)
        expected = 50.0 * loss_m + transport

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=[efficiency])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            transport_pipelines={
                "route0": {
                    "length_km": 50.0,
                    "fuels": ["Coal"],
                    "transport_cost": 100.0,
                    "losses_fraction": 0.1,
                    "fuel_params": {
                        "Coal": {
                            "transport_cost": 300.0,
                            "losses_fraction": 0.02,
                        },
                    },
                },
            },
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)

    def test_return_type(self):
        """Return value is a dict mapping str to list of float."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert isinstance(result, dict)
        for key, val in result.items():
            assert isinstance(key, str)
            assert isinstance(val, list)

    def test_original_fuel_cost_not_mutated(self):
        """The original gen_cfg.fuel_cost list is not modified."""
        gen = _make_gen_cfg("Coal", [50.0])
        original_cost = gen.fuel_cost[0]
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": gen},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        _compute_geographic_fuel_adjustments(cfg)
        assert gen.fuel_cost[0] == original_cost

    def test_generator_with_no_layout_position_unchanged(self):
        """Generator in config but without layout position is not adjusted."""
        cfg = _make_sys_config(
            gui_layout={
                "generators": {"other_n0": (0.0, 0.0)},  # different key
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0])},
            fuels={"Coal": _make_fuel_def(8.14)},
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result == {}

    def test_multiple_pipelines_average_rates(self):
        """Multiple pipelines for the same fuel average their per-km rates."""
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        energy_content = 8.14
        efficiency = 0.4

        # Pipeline 1: 100 km, cost=100 => per-km=1.0
        # Pipeline 2: 200 km, cost=600 => per-km=3.0
        # Average per-km = (1.0 + 3.0) / 2 = 2.0
        avg_cpk = (100.0 / 100.0 + 600.0 / 200.0) / 2.0
        # Pipeline 1: loss=0.1/100=0.001, Pipeline 2: loss=0.06/200=0.0003
        avg_lpk = (0.1 / 100.0 + 0.06 / 200.0) / 2.0

        transport = avg_cpk * dist / (energy_content * efficiency)
        loss_f = min(avg_lpk * dist, 0.5)
        loss_m = 1.0 / (1.0 - loss_f)
        expected = 50.0 * loss_m + transport

        cfg = _make_sys_config(
            gui_layout={
                "generators": {"gen0_n0": (0.0, 0.0)},
                "fuel_storages": {"s0": (0.0, 1.0)},
            },
            storage_facilities={"s0": {"fuels": ["Coal"]}},
            generators={"gen0": _make_gen_cfg("Coal", [50.0], eff_at_rated=[efficiency])},
            fuels={"Coal": _make_fuel_def(energy_content)},
            transport_pipelines={
                "route_a": {
                    "length_km": 100.0,
                    "fuels": ["Coal"],
                    "transport_cost": 100.0,
                    "losses_fraction": 0.1,
                },
                "route_b": {
                    "length_km": 200.0,
                    "fuels": ["Coal"],
                    "transport_cost": 600.0,
                    "losses_fraction": 0.06,
                },
            },
            num_nodes=1,
        )
        result = _compute_geographic_fuel_adjustments(cfg)
        assert result["gen0"][0] == pytest.approx(expected, rel=1e-6)
