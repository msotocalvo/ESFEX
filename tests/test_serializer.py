"""Comprehensive tests for the GUI serializer module.

Tests bidirectional conversion between:
  ESFEXConfig (Pydantic) <-> GuiSystemState (dataclasses) <-> YAML strings.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
import yaml


@pytest.fixture(autouse=True)
def _no_user_pref_overrides(monkeypatch):
    """GuiGlobalSettings.__post_init__ otherwise loads ~/.config preferences
    (with DEFAULT_PREFERENCES fallback for the solver) that overwrite fields
    the test set via the constructor, masking the serializer's actual
    behavior. Stub __post_init__ to a no-op so the test's constructor
    arguments survive."""
    from esfex.visualization.data import gui_model as _gm
    monkeypatch.setattr(_gm.GuiGlobalSettings, "__post_init__", lambda self: None)

from esfex.config.schema import (
    BatteryConfig,
    CO2BudgetConfig,
    CostCurveBlock,
    CostCurveConfig,
    FuelConfig,
    GeneratorConfig,
    MetaNetworkConfig,
    NodeConfig,
    PenaltiesConfig,
    ESFEXConfig,
    SystemConfig,
)
from esfex.visualization.data.gui_model import (
    EndpointRef,
    FuelRouteParams,
    GeoPoint,
    GuiACPowerFlow,
    GuiBatteryInstance,
    GuiDCPowerFlow,
    GuiDemandSector,
    GuiFuel,
    GuiFuelTransportRoute,
    GuiGeneratorInstance,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiGlobalSettings,
    GuiNode,
    GuiNonElectricDemand,
    GuiPenalties,
    GuiStochasticScenario,
    GuiSystemSettings,
    GuiSystemState,
    GuiTransmissionLine,
    GuiVisualScaling,
)
from esfex.visualization.data.serializer import (
    _build_fuel_transport_distances,
    _build_fuel_transport_routes,
    _cost_curve_to_gui_data,
    _gui_data_to_cost_curve_config,
    _haversine_km,
    _route_length_from_waypoints,
    _to_native,
    config_to_global_settings,
    config_to_gui_states,
    config_to_stochastic_scenarios,
    global_settings_to_config_dict,
    gui_state_to_yaml,
    stochastic_scenarios_to_config_dict,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# =====================================================================
# Helpers: Programmatic fixture construction
# =====================================================================

_2N = 2  # number of nodes for test fixtures


def _make_generator(
    name: str, gen_type: str, fuel: str,
    rated_power: list[float] | None = None,
    invest_max_power: list[float] | None = None,
    technology: str | None = None,
) -> GeneratorConfig:
    n = _2N
    rp = rated_power or [0.0] * n
    return GeneratorConfig(
        name=name,
        type=gen_type,
        fuel=fuel,
        technology=technology,
        reservable=True,
        life_time=[25] * n,
        initial_age=[10] * n,
        degradation_rate=[0.04] * n,
        decommissioning_cost=[1000.0] * n,
        rated_power=rp,
        min_power=[0.3] * n,
        min_up=[4] * n,
        min_down=[2] * n,
        ramp_up=[0.04] * n,
        ramp_down=[0.04] * n,
        eff_at_rated=[0.45] * n,
        eff_at_min=[0.40] * n,
        inertia=[6.0] * n,
        start_up_cost=[500.0] * n,
        fuel_cost=[94.0] * n,
        fixed_cost=[6.6] * n,
        maintenance_cost=[28.8] * n,
        invest_cost=[3_900_000.0] * n,
        invest_max_power=invest_max_power or [0.0] * n,
    )


def _make_battery(
    name: str = "Li-ion Battery",
    rated_power: list[float] | None = None,
    capacity: list[float] | None = None,
    invest_max_power: list[float] | None = None,
    invest_max_capacity: list[float] | None = None,
) -> BatteryConfig:
    n = _2N
    rp = rated_power or [25.0, 40.0]
    cap = capacity or [50.0, 80.0]
    return BatteryConfig(
        name=name,
        fuel="None",
        life_time=[15] * n,
        initial_age=[0] * n,
        degradation_rate=[0.005] * n,
        decommissioning_cost=[300.0] * n,
        rated_power=rp,
        min_power=[0.0] * n,
        min_up=[0] * n,
        min_down=[0] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.9] * n,
        eff_at_min=[0.9] * n,
        inertia=[0.0] * n,
        start_up_cost=[0.0] * n,
        fuel_cost=[0.0] * n,
        fixed_cost=[5.0] * n,
        maintenance_cost=[5.0] * n,
        invest_cost=[200_000.0] * n,
        invest_cost_energy=[150_000.0] * n,
        invest_max_power=invest_max_power or [50.0] * n,
        invest_max_capacity=invest_max_capacity or [100.0] * n,
        efficiency_charge=[0.95] * n,
        efficiency_discharge=[0.95] * n,
        soc_initial=[0.5] * n,
        max_DoD=[0.9] * n,
        capacity=cap,
        MaxChargePower=rp,
        MaxDischargePower=rp,
    )


def _make_system_config(sys_name: str = "TestSystem") -> SystemConfig:
    """Build a minimal but complete 2-node system config."""
    return SystemConfig(
        name=sys_name,
        demand_scale=1.0,
        discount_rate=0.05,
        target_re_penetration=0.5,
        base_lcoe=93.0,
        nodes=NodeConfig(
            num_nodes=2,
            nodes_connections=[0.0, 200.0, 200.0, 0.0],
            reserve_static=[10.0, 10.0],
            reserve_dynamic=[20.0, 20.0],
            reserve_duration=[2, 2],
            losses=[0.001, 0.001],
            transference_invest_cost=[13_000.0, 13_000.0],
            transference_invest_max=[100.0, 100.0],
        ),
        fuels={
            "Gas": FuelConfig(
                name="Gas", unit="ton", emission_factor=0.20,
                energy_content=12.28, price_base=110.0, price_growth_rate=0.015,
            ),
            "Sun": FuelConfig(
                name="Sun", emission_factor=0.0, price_base=0.0,
            ),
            "Wind": FuelConfig(
                name="Wind", emission_factor=0.0, price_base=0.0,
            ),
        },
        penalties=PenaltiesConfig(
            loss_of_load=10_000_000, curtailment=100.0, co2_cost=10.0,
        ),
        co2_budget=CO2BudgetConfig(enabled=True, annual_budget=100_000),
        generators={
            "gas_turbine": _make_generator(
                "Gas turbine", "Non-renewable", "Gas",
                rated_power=[100.0, 50.0],
            ),
            "solar": _make_generator(
                "Solar", "Renewable", "Sun",
                rated_power=[50.0, 80.0],
                invest_max_power=[200.0, 200.0],
                technology="Solar PV",
            ),
            "wind": _make_generator(
                "Wind", "Renewable", "Wind",
                rated_power=[30.0, 60.0],
                invest_max_power=[100.0, 100.0],
                technology="Wind turbine",
            ),
        },
        batteries={
            "li_ion": _make_battery(),
        },
    )


def _make_esfex_config(
    sys_config: SystemConfig | None = None,
    sys_name: str = "TestSystem",
) -> ESFEXConfig:
    """Wrap a SystemConfig in a minimal ESFEXConfig."""
    if sys_config is None:
        sys_config = _make_system_config(sys_name)
    return ESFEXConfig(
        meta_network=MetaNetworkConfig(systems=[sys_name]),
        systems={sys_name: sys_config},
    )


def _build_minimal_gui_state() -> GuiSystemState:
    """Build a minimal GuiSystemState programmatically for export tests."""
    return GuiSystemState(
        name="MinimalSystem",
        nodes=[
            GuiNode(index=0, name="Node A", reserve_static=5.0, reserve_dynamic=10.0),
            GuiNode(index=1, name="Node B", reserve_static=8.0, reserve_dynamic=12.0),
        ],
        generators={
            "solar_n0": GuiGeneratorInstance(
                instance_id="solar_n0",
                unit_key="solar",
                name="Solar",
                gen_type="Renewable",
                fuel="Sun",
                node=0,
                rated_power=50.0,
                life_time=25,
                eff_at_rated=0.98,
                eff_at_min=0.98,
            ),
        },
        batteries={
            "li_ion_n0": GuiBatteryInstance(
                instance_id="li_ion_n0",
                unit_key="li_ion",
                name="Li-ion Battery",
                node=0,
                rated_power=25.0,
                capacity=50.0,
                life_time=15,
            ),
        },
        transmission_lines=[
            GuiTransmissionLine(
                line_id="line_0",
                from_node=0,
                to_node=1,
                capacity_mw=100.0,
                from_endpoint=EndpointRef("node", "0"),
                to_endpoint=EndpointRef("node", "1"),
            ),
        ],
        settings=GuiSystemSettings(
            demand_scale=1.0,
            discount_rate=0.05,
            target_re_penetration=0.5,
        ),
        penalties=GuiPenalties(
            loss_of_load=10e6,
            curtailment=100.0,
        ),
    )


# =====================================================================
# 1. _to_native
# =====================================================================


class TestToNative:
    """Tests for the _to_native helper that converts numpy types."""

    def test_plain_dict_passes_through(self):
        d = {"a": 1, "b": "hello", "c": [1, 2, 3]}
        result = _to_native(d)
        assert result == d

    def test_nested_dict(self):
        d = {"outer": {"inner": 42}}
        result = _to_native(d)
        assert result == {"outer": {"inner": 42}}

    def test_numpy_integer(self):
        val = np.int64(42)
        result = _to_native(val)
        assert result == 42
        assert isinstance(result, int)

    def test_numpy_float(self):
        val = np.float64(3.14)
        result = _to_native(val)
        assert result == pytest.approx(3.14)
        assert isinstance(result, float)

    def test_numpy_bool(self):
        val = np.bool_(True)
        result = _to_native(val)
        assert result is True
        assert isinstance(result, bool)

    def test_numpy_array(self):
        arr = np.array([1, 2, 3])
        result = _to_native(arr)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_list_of_numpy(self):
        lst = [np.int64(1), np.float64(2.5), np.bool_(False)]
        result = _to_native(lst)
        assert result == [1, 2.5, False]
        assert isinstance(result[0], int)
        assert isinstance(result[1], float)
        assert isinstance(result[2], bool)

    def test_dict_with_numpy_values(self):
        d = {"x": np.float64(1.5), "y": np.int32(10)}
        result = _to_native(d)
        assert result == {"x": 1.5, "y": 10}
        assert isinstance(result["x"], float)
        assert isinstance(result["y"], int)

    def test_dict_with_numpy_keys(self):
        d = {np.int64(0): "a", np.int64(1): "b"}
        result = _to_native(d)
        assert result == {0: "a", 1: "b"}

    def test_string_passes_through(self):
        assert _to_native("hello") == "hello"

    def test_none_passes_through(self):
        assert _to_native(None) is None

    def test_int_passes_through(self):
        assert _to_native(42) == 42

    def test_float_passes_through(self):
        assert _to_native(3.14) == pytest.approx(3.14)

    def test_deeply_nested(self):
        d = {
            "level1": {
                "level2": [np.float64(1.0), {"level3": np.int64(99)}]
            }
        }
        result = _to_native(d)
        assert result == {"level1": {"level2": [1.0, {"level3": 99}]}}
        assert isinstance(result["level1"]["level2"][0], float)
        assert isinstance(result["level1"]["level2"][1]["level3"], int)

    def test_tuple_converted_to_list(self):
        t = (np.int64(1), np.int64(2))
        result = _to_native(t)
        assert result == [1, 2]
        assert isinstance(result, list)

    def test_empty_dict(self):
        assert _to_native({}) == {}

    def test_empty_list(self):
        assert _to_native([]) == []

    def test_mixed_numpy_native(self):
        d = {"native_int": 5, "np_int": np.int64(10), "native_str": "foo"}
        result = _to_native(d)
        assert result["native_int"] == 5
        assert result["np_int"] == 10
        assert result["native_str"] == "foo"

    def test_numpy_2d_array(self):
        arr = np.array([[1, 2], [3, 4]])
        result = _to_native(arr)
        assert result == [[1, 2], [3, 4]]

    def test_numpy_float32(self):
        val = np.float32(1.5)
        result = _to_native(val)
        assert isinstance(result, float)
        assert result == pytest.approx(1.5, abs=0.01)

    def test_numpy_int8(self):
        val = np.int8(7)
        result = _to_native(val)
        assert isinstance(result, int)
        assert result == 7


# =====================================================================
# 2. _haversine_km
# =====================================================================


class TestHaversineKm:
    """Tests for the haversine distance function."""

    def test_same_point_returns_zero(self):
        assert _haversine_km(0, 0, 0, 0) == 0.0

    def test_same_point_nonzero_coords(self):
        assert _haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == 0.0

    def test_one_degree_latitude_at_equator(self):
        d = _haversine_km(0, 0, 1, 0)
        assert d == pytest.approx(111.19, abs=1.0)

    def test_one_degree_longitude_at_equator(self):
        d = _haversine_km(0, 0, 0, 1)
        assert d == pytest.approx(111.19, abs=1.0)

    def test_known_distance_paris_london(self):
        d = _haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
        assert d == pytest.approx(343.0, abs=5.0)

    def test_antipodal_points(self):
        d = _haversine_km(90, 0, -90, 0)
        assert d == pytest.approx(20015.0, abs=100.0)

    def test_symmetry(self):
        d1 = _haversine_km(10, 20, 30, 40)
        d2 = _haversine_km(30, 40, 10, 20)
        assert d1 == pytest.approx(d2, abs=1e-6)

    def test_small_distance(self):
        d = _haversine_km(0, 0, 0.001, 0)
        assert d == pytest.approx(0.111, abs=0.01)

    def test_negative_longitude(self):
        d = _haversine_km(0, -1, 0, 1)
        assert d == pytest.approx(222.4, abs=2.0)

    def test_large_longitude_diff(self):
        d = _haversine_km(40.7128, -74.0060, 35.6762, 139.6503)
        assert d == pytest.approx(10838.0, abs=100.0)


# =====================================================================
# 3. _route_length_from_waypoints
# =====================================================================


class TestRouteLengthFromWaypoints:
    """Tests for route length calculation from waypoints."""

    def _make_route(self, waypoints: list[GeoPoint]):
        from esfex.visualization.data.gui_model import GuiFuelTransportRoute
        return GuiFuelTransportRoute(
            route_id="test_route",
            waypoints=waypoints,
        )

    def test_empty_waypoints(self):
        state = GuiSystemState()
        rt = self._make_route([])
        assert _route_length_from_waypoints(state, rt) == 0.0

    def test_single_waypoint(self):
        state = GuiSystemState()
        rt = self._make_route([GeoPoint(0, 0)])
        assert _route_length_from_waypoints(state, rt) == 0.0

    def test_two_waypoints_same_point(self):
        state = GuiSystemState()
        rt = self._make_route([GeoPoint(10, 20), GeoPoint(10, 20)])
        assert _route_length_from_waypoints(state, rt) == 0.0

    def test_two_waypoints_known_distance(self):
        state = GuiSystemState()
        rt = self._make_route([GeoPoint(0, 0), GeoPoint(1, 0)])
        length = _route_length_from_waypoints(state, rt)
        assert length == pytest.approx(111.19, abs=1.0)

    def test_multiple_waypoints_sum_of_segments(self):
        state = GuiSystemState()
        p1 = GeoPoint(0, 0)
        p2 = GeoPoint(1, 0)
        p3 = GeoPoint(2, 0)
        rt = self._make_route([p1, p2, p3])
        length = _route_length_from_waypoints(state, rt)
        expected = _haversine_km(0, 0, 1, 0) + _haversine_km(1, 0, 2, 0)
        assert length == pytest.approx(expected, abs=0.1)

    def test_three_waypoints_triangle(self):
        state = GuiSystemState()
        p1 = GeoPoint(0, 0)
        p2 = GeoPoint(0, 1)
        p3 = GeoPoint(1, 0)
        rt = self._make_route([p1, p2, p3])
        length = _route_length_from_waypoints(state, rt)
        seg1 = _haversine_km(0, 0, 0, 1)
        seg2 = _haversine_km(0, 1, 1, 0)
        assert length == pytest.approx(seg1 + seg2, abs=0.1)


# =====================================================================
# 3b. _build_fuel_transport_distances (haversine NxN matrix)
# =====================================================================


class TestBuildFuelTransportDistances:
    """Tests for automatic haversine distance matrix computation."""

    def test_empty_state(self):
        state = GuiSystemState()
        dist = _build_fuel_transport_distances(state)
        assert dist == []

    def test_single_node_zero_matrix(self):
        state = GuiSystemState(nodes=[GuiNode(name="N0", index=0)])
        dist = _build_fuel_transport_distances(state)
        assert dist == [[0.0]]

    def test_two_nodes_with_fuel_entries(self):
        """Two nodes with fuel entries at known coordinates."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=0, centroid_lng=0),
                GuiNode(name="N1", index=1, centroid_lat=0, centroid_lng=0),
            ],
            fuel_entry_points=[
                GuiFuelEntryPoint(
                    name="Port A", node=0,
                    coordinate=GeoPoint(10.0, 20.0),
                ),
                GuiFuelEntryPoint(
                    name="Port B", node=1,
                    coordinate=GeoPoint(11.0, 20.0),
                ),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        assert len(dist) == 2
        assert dist[0][0] == 0.0
        assert dist[1][1] == 0.0
        # ~111.19 km per degree of latitude
        expected = _haversine_km(10.0, 20.0, 11.0, 20.0)
        assert dist[0][1] == pytest.approx(expected, abs=1.0)
        assert dist[1][0] == pytest.approx(expected, abs=1.0)

    def test_symmetric_matrix(self):
        """Distance matrix must be symmetric."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10, centroid_lng=20),
                GuiNode(name="N1", index=1, centroid_lat=11, centroid_lng=21),
                GuiNode(name="N2", index=2, centroid_lat=12, centroid_lng=22),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        for i in range(3):
            for j in range(3):
                assert dist[i][j] == dist[j][i]

    def test_fallback_to_node_centroid(self):
        """Nodes without fuel infrastructure use their geographic centroid."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        expected = _haversine_km(10.0, 20.0, 11.0, 20.0)
        assert dist[0][1] == pytest.approx(expected, abs=1.0)

    def test_fuel_infrastructure_centroid_used(self):
        """Centroid of fuel entries + storages is used, not node centroid."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=50.0, centroid_lng=50.0),
                GuiNode(name="N1", index=1, centroid_lat=50.0, centroid_lng=50.0),
            ],
            fuel_entry_points=[
                GuiFuelEntryPoint(name="FE0", node=0, coordinate=GeoPoint(10.0, 20.0)),
                GuiFuelEntryPoint(name="FE1", node=1, coordinate=GeoPoint(11.0, 20.0)),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        # Should use fuel entry coords, NOT node centroids (50,50)
        expected = _haversine_km(10.0, 20.0, 11.0, 20.0)
        assert dist[0][1] == pytest.approx(expected, abs=1.0)

    def test_mixed_fuel_entry_and_storage(self):
        """Centroid averages positions of both fuel entries and storages."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=5.0, centroid_lng=10.0),
                GuiNode(name="N1", index=1, centroid_lat=15.0, centroid_lng=10.0),
            ],
            fuel_entry_points=[
                GuiFuelEntryPoint(name="FE0", node=0, coordinate=GeoPoint(10.0, 20.0)),
            ],
            fuel_storages={
                "fs_0": GuiFuelStorage(
                    storage_id="fs_0", name="FS0", node=0,
                    latitude=12.0, longitude=20.0,
                ),
            },
        )
        dist = _build_fuel_transport_distances(state)
        # Node 0 centroid = avg of (10,20) and (12,20) = (11,20)
        # Node 1 has no fuel infra -> uses node centroid (15,10)
        expected = _haversine_km(11.0, 20.0, 15.0, 10.0)
        assert dist[0][1] == pytest.approx(expected, abs=1.0)

    def test_zero_coords_skipped(self):
        """Nodes at (0,0) with no fuel infrastructure produce 0 distance."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=0.0, centroid_lng=0.0),
                GuiNode(name="N1", index=1, centroid_lat=1.0, centroid_lng=0.0),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        # Node 0 is at (0,0) -> skipped
        assert dist[0][1] == 0.0

    def test_routes_override_haversine(self):
        """Explicit routes take priority over haversine fallback."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=0, to_node=1,
                    length_km=500.0,  # Much larger than haversine (~111 km)
                ),
            ],
        )
        dist = _build_fuel_transport_distances(state)
        # Route distance (500 km) should override haversine (~111 km)
        assert dist[0][1] == 500.0
        assert dist[1][0] == 500.0


# =====================================================================
# 3b. _build_fuel_transport_routes (route-based model)
# =====================================================================


class TestBuildFuelTransportRoutes:
    """Tests for route-based fuel transport model."""

    def test_empty_state_returns_empty(self):
        state = GuiSystemState()
        routes = _build_fuel_transport_routes(state)
        assert routes == []

    def test_single_node_no_routes(self):
        state = GuiSystemState(nodes=[GuiNode(name="N0", index=0)])
        routes = _build_fuel_transport_routes(state)
        assert routes == []

    def test_explicit_route_generates_forward_and_reverse(self):
        """Each GUI route becomes two unidirectional routes."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=0, to_node=1,
                    length_km=500.0,
                    fuel_params={
                        "Diesel": FuelRouteParams(
                            capacity=100.0, transport_cost=0.5, losses_fraction=0.02,
                        ),
                    },
                ),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        assert len(routes) == 2
        fwd = routes[0]
        rev = routes[1]
        assert fwd["route_id"] == "route_0_fwd"
        assert fwd["from_node"] == 0
        assert fwd["to_node"] == 1
        assert fwd["distance_km"] == 500.0
        assert fwd["fuel_params"]["Diesel"]["capacity"] == 100.0
        assert fwd["fuel_params"]["Diesel"]["transport_cost"] == 0.5
        assert fwd["fuel_params"]["Diesel"]["losses_fraction"] == 0.02
        assert rev["route_id"] == "route_0_rev"
        assert rev["from_node"] == 1
        assert rev["to_node"] == 0
        assert rev["distance_km"] == 500.0

    def test_haversine_fallback_for_unconnected_pairs(self):
        """Node pairs without explicit routes get haversine auto-routes."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        # 1 pair → 2 unidirectional auto-routes
        assert len(routes) == 2
        assert routes[0]["route_id"].startswith("auto_")
        expected_km = _haversine_km(10.0, 20.0, 11.0, 20.0)
        assert routes[0]["distance_km"] == pytest.approx(expected_km, abs=1.0)
        assert routes[0]["fuel_params"] == {}  # empty = use global defaults
        # Forward and reverse
        assert routes[0]["from_node"] == 0
        assert routes[0]["to_node"] == 1
        assert routes[1]["from_node"] == 1
        assert routes[1]["to_node"] == 0

    def test_explicit_route_suppresses_haversine_for_pair(self):
        """Haversine is skipped for node pairs that have an explicit route."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
                GuiNode(name="N2", index=2, centroid_lat=12.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=0, to_node=1,
                    length_km=500.0,
                ),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        # route_0 → 2 unidirectional (0→1, 1→0)
        # pair (0,2) → 2 haversine auto
        # pair (1,2) → 2 haversine auto
        # total = 6
        assert len(routes) == 6
        route_ids = [r["route_id"] for r in routes]
        assert "route_0_fwd" in route_ids
        assert "route_0_rev" in route_ids
        # No auto route for pair (0,1)
        assert "auto_0_1" not in route_ids
        assert "auto_1_0" not in route_ids

    def test_per_fuel_params_carried_through(self):
        """Multiple fuels on one route carry distinct parameters."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=0, to_node=1,
                    length_km=300.0,
                    fuel_params={
                        "Diesel": FuelRouteParams(
                            capacity=100.0, transport_cost=0.5, losses_fraction=0.02,
                        ),
                        "Gas": FuelRouteParams(
                            capacity=500.0, transport_cost=0.1, losses_fraction=0.01,
                        ),
                    },
                ),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        fwd = routes[0]
        assert len(fwd["fuel_params"]) == 2
        assert fwd["fuel_params"]["Diesel"]["capacity"] == 100.0
        assert fwd["fuel_params"]["Gas"]["capacity"] == 500.0
        assert fwd["fuel_params"]["Diesel"]["transport_cost"] == 0.5
        assert fwd["fuel_params"]["Gas"]["transport_cost"] == 0.1

    def test_intra_node_route_preserved(self):
        """Routes with from_node == to_node (intra-node) are kept."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="intra_0", from_node=0, to_node=0,
                    length_km=15.0,  # port → depot within region
                    fuel_params={
                        "Diesel": FuelRouteParams(
                            capacity=50.0, transport_cost=0.3, losses_fraction=0.01,
                        ),
                    },
                ),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        # Intra-node: 2 (fwd+rev, both 0→0)
        # Haversine: pair (0,1) → 2 auto routes
        # Total = 4
        assert len(routes) == 4
        intra = [r for r in routes if r["route_id"].startswith("intra_")]
        assert len(intra) == 2
        assert all(r["from_node"] == 0 and r["to_node"] == 0 for r in intra)
        assert intra[0]["distance_km"] == 15.0

    def test_zero_distance_route_skipped(self):
        """Routes with zero or negative length are excluded."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=20.0),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=0, to_node=1,
                    length_km=0.0,  # zero length → skip
                ),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        # Explicit route skipped → only haversine fallback
        route_ids = [r["route_id"] for r in routes]
        assert "route_0_fwd" not in route_ids
        assert "auto_0_1" in route_ids

    def test_three_nodes_full_mesh(self):
        """Three nodes with no explicit routes → 6 auto-routes (3 pairs × 2)."""
        state = GuiSystemState(
            nodes=[
                GuiNode(name="N0", index=0, centroid_lat=10.0, centroid_lng=20.0),
                GuiNode(name="N1", index=1, centroid_lat=11.0, centroid_lng=21.0),
                GuiNode(name="N2", index=2, centroid_lat=12.0, centroid_lng=22.0),
            ],
        )
        routes = _build_fuel_transport_routes(state)
        # 3 pairs → 6 unidirectional routes
        assert len(routes) == 6
        assert all(r["route_id"].startswith("auto_") for r in routes)
        # All have empty fuel_params (use global defaults)
        assert all(r["fuel_params"] == {} for r in routes)


# =====================================================================
# 4. config_to_gui_states
# =====================================================================


class TestConfigToGuiStates:
    """Tests for converting ESFEXConfig to GUI states."""

    def test_returns_dict(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        assert isinstance(states, dict)

    def test_system_name_as_key(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        assert "TestSystem" in states

    def test_value_is_gui_system_state(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert isinstance(state, GuiSystemState)

    def test_nodes_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert len(state.nodes) == 2

    def test_node_reserve_values(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert state.nodes[0].reserve_static == 10.0
        assert state.nodes[0].reserve_dynamic == 20.0
        assert state.nodes[0].reserve_duration == 2

    def test_generators_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        # 3 generators x 2 nodes = 6 instances
        assert len(state.generators) == 6

    def test_generator_instance_keys(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert "gas_turbine_n0" in state.generators
        assert "gas_turbine_n1" in state.generators
        assert "solar_n0" in state.generators
        assert "solar_n1" in state.generators
        assert "wind_n0" in state.generators
        assert "wind_n1" in state.generators

    def test_generator_properties(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        gas0 = state.generators["gas_turbine_n0"]
        assert gas0.name == "Gas turbine"
        assert gas0.gen_type == "Non-renewable"
        assert gas0.fuel == "Gas"
        assert gas0.rated_power == 100.0
        assert gas0.life_time == 25
        assert gas0.initial_age == 10
        assert gas0.eff_at_rated == pytest.approx(0.45)

    def test_generator_node1_different_rated_power(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        gas1 = state.generators["gas_turbine_n1"]
        assert gas1.rated_power == 50.0

    def test_batteries_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        # li_ion at 2 nodes
        assert len(state.batteries) == 2

    def test_battery_instance_key(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert "li_ion_n0" in state.batteries
        assert "li_ion_n1" in state.batteries

    def test_battery_properties(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        bat0 = state.batteries["li_ion_n0"]
        assert bat0.name == "Li-ion Battery"
        assert bat0.rated_power == 25.0
        assert bat0.capacity == 50.0
        assert bat0.life_time == 15

    def test_transmission_lines(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        # Exclude decorative lines (generator/battery → bus visual stubs).
        real_lines = [ln for ln in state.transmission_lines if not ln.decorative]
        assert len(real_lines) == 1
        line = real_lines[0]
        assert line.capacity_mw == 200.0
        assert line.from_node == 0
        assert line.to_node == 1

    def test_fuels_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert "Gas" in state.fuels
        gas_fuel = state.fuels["Gas"]
        assert gas_fuel.emission_factor == pytest.approx(0.20)
        assert gas_fuel.energy_content == pytest.approx(12.28)

    def test_settings_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert state.settings.discount_rate == pytest.approx(0.05)
        assert state.settings.target_re_penetration == pytest.approx(0.5)
        assert state.settings.base_lcoe == pytest.approx(93.0)

    def test_penalties_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert state.penalties.loss_of_load == 10_000_000
        assert state.penalties.curtailment == 100.0
        assert state.penalties.co2_cost == 10.0

    def test_co2_budget_in_settings(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert state.settings.co2_budget_enabled is True
        assert state.settings.co2_annual_budget == 100_000

    def test_investment_portfolio_populated(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        gen_investments = [
            e for e in state.investment_portfolio.values()
            if e.technology_type == "generator"
        ]
        # solar and wind have invest_max_power > 0
        assert len(gen_investments) >= 2

    def test_battery_investment_in_portfolio(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        bat_investments = [
            e for e in state.investment_portfolio.values()
            if e.technology_type == "battery"
        ]
        assert len(bat_investments) >= 1

    def test_system_state_name(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        assert state.name == "TestSystem"

    def test_buses_auto_created(self):
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        # One bus per node auto-created
        assert len(state.buses) >= 2


# =====================================================================
# 5. config_to_global_settings
# =====================================================================


class TestConfigToGlobalSettings:
    """Tests for extracting global settings from config."""

    def test_returns_gui_global_settings(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert isinstance(gs, GuiGlobalSettings)

    def test_simulation_mode_default(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.simulation_mode == "development"

    def test_unit_commitment_hours(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.unit_commitment_hours == 24

    def test_solver_defaults(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.solver_name == "highs"
        assert gs.solver_gap == pytest.approx(0.01)

    def test_temporal_defaults(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.resolution_hours == 1
        assert gs.use_rolling_horizon is True

    def test_n1_security_defaults(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.n1_enabled is False

    def test_master_problem_defaults(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert gs.mp_stochastic is False
        assert gs.mp_representative_days == 5

    def test_visual_scaling_default(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        assert isinstance(gs.visual_scaling, GuiVisualScaling)
        assert gs.visual_scaling.marker_min_px == pytest.approx(6.0)

    def test_visual_scaling_from_raw_dict(self):
        config = _make_esfex_config()
        raw_dict = {
            "visual_scaling": {
                "marker_min_px": 10.0,
                "electrical_marker_scale": 0.05,
            }
        }
        gs = config_to_global_settings(config, raw_dict=raw_dict)
        assert gs.visual_scaling.marker_min_px == pytest.approx(10.0)
        assert gs.visual_scaling.electrical_marker_scale == pytest.approx(0.05)


# =====================================================================
# 6. config_to_stochastic_scenarios
# =====================================================================


class TestConfigToStochasticScenarios:
    """Tests for extracting stochastic scenarios."""

    def test_no_scenarios_returns_empty_list(self):
        config = _make_esfex_config()
        scenarios = config_to_stochastic_scenarios(config)
        assert isinstance(scenarios, list)
        assert len(scenarios) == 0

    def test_returns_list_type(self):
        config = _make_esfex_config()
        scenarios = config_to_stochastic_scenarios(config)
        assert isinstance(scenarios, list)


# =====================================================================
# 7. gui_state_to_yaml
# =====================================================================


class TestGuiStateToYaml:
    """Tests for exporting GUI state to YAML."""

    def _export_and_parse(self, states, config, **kwargs) -> dict:
        """Export states to YAML and return the parsed dict."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states,
                base_config=config,
                output_path=output_path,
                **kwargs,
            )
            with open(output_path) as fh:
                return yaml.safe_load(fh)
        finally:
            output_path.unlink(missing_ok=True)

    def test_output_file_created(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states={"MinimalSystem": state},
                base_config=config,
                output_path=output_path,
            )
            assert output_path.exists()
            assert output_path.stat().st_size > 0
        finally:
            output_path.unlink(missing_ok=True)

    def test_output_is_valid_yaml(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        assert isinstance(data, dict)

    def test_output_contains_system(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        assert "systems" in data
        assert "MinimalSystem" in data["systems"]

    def test_output_contains_generators(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        assert "generators" in sys_data
        assert "solar" in sys_data["generators"]

    def test_output_contains_batteries(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        assert "batteries" in sys_data
        assert "li_ion" in sys_data["batteries"]

    def test_output_nodes_connections(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        conns = sys_data["nodes"]["nodes_connections"]
        # 2x2 matrix: 100 MW line between node 0 and 1
        assert conns[0 * 2 + 1] == 100.0
        assert conns[1 * 2 + 0] == 100.0

    def test_output_with_global_settings(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        gs = GuiGlobalSettings(simulation_mode="unit_commitment", solver_name="gurobi")
        data = self._export_and_parse(
            {"MinimalSystem": state}, config, global_settings=gs,
        )
        assert data["simulation_mode"] == "unit_commitment"
        assert data["solver"]["name"] == "gurobi"

    def test_output_with_stochastic_scenarios(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        scenarios = [
            GuiStochasticScenario(
                name="High demand", probability=0.3,
                multipliers={"demand": 1.2},
            ),
            GuiStochasticScenario(
                name="Low demand", probability=0.7,
                multipliers={"demand": 0.8},
            ),
        ]
        data = self._export_and_parse(
            {"MinimalSystem": state}, config,
            stochastic_scenarios=scenarios,
        )
        sys_data = data["systems"]["MinimalSystem"]
        assert "stochastic_scenarios" in sys_data
        assert len(sys_data["stochastic_scenarios"]) == 2
        assert sys_data["stochastic_scenarios"][0]["name"] == "High demand"

    def test_generator_per_node_arrays(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        solar = data["systems"]["MinimalSystem"]["generators"]["solar"]
        # 2 nodes, solar at node 0 with rated_power=50
        assert solar["rated_power"][0] == 50.0
        assert solar["rated_power"][1] == 0.0

    def test_system_settings_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        assert sys_data["discount_rate"] == pytest.approx(0.05)
        assert sys_data["target_re_penetration"] == pytest.approx(0.5)

    def test_penalties_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        assert "penalties" in sys_data
        assert sys_data["penalties"]["LOSS_OF_LOAD"] == 10e6
        assert sys_data["penalties"]["Curtailment"] == 100.0

    def test_node_names_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        names = data["systems"]["MinimalSystem"]["nodes"]["node_names"]
        assert names == ["Node A", "Node B"]

    def test_co2_budget_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        co2 = data["systems"]["MinimalSystem"]["co2_budget"]
        assert "enabled" in co2
        assert "annual_budget" in co2

    def test_dc_power_flow_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        dc = data["systems"]["MinimalSystem"]["dc_power_flow"]
        # enable_angle_limits was removed from DCPowerFlowConfig — DC angle
        # limits are not load-bearing in the current formulation. Only
        # max_angle_diff_deg (used by ACOPF) and slack_bus remain.
        assert "max_angle_diff_deg" in dc
        assert "slack_bus" in dc

    def test_ac_power_flow_exported(self):
        state = _build_minimal_gui_state()
        state.power_flow_mode = "acopf_soc"
        state.ac_power_flow.voltage_min_pu = 0.95
        state.ac_power_flow.q_slack_penalty = 50.0
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        sys_data = data["systems"]["MinimalSystem"]
        assert sys_data["power_flow_mode"] == "acopf_soc"
        ac = sys_data["ac_power_flow"]
        assert ac["voltage_min_pu"] == 0.95
        assert ac["q_slack_penalty"] == 50.0
        assert "base_mva" in ac
        assert "load_power_factor" in ac
        assert "min_reactance_pu" in ac
        assert "tap_ratio_min" in ac
        assert "tap_ratio_max" in ac
        assert "q_min_ratio" in ac


# =====================================================================
# 8. global_settings_to_config_dict
# =====================================================================


class TestGlobalSettingsToConfigDict:
    """Tests for applying global settings to a config dict."""

    def test_simulation_mode_written(self):
        gs = GuiGlobalSettings(simulation_mode="unit_commitment")
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["simulation_mode"] == "unit_commitment"

    def test_unit_commitment_hours_written(self):
        gs = GuiGlobalSettings(unit_commitment_hours=48)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["unit_commitment_hours"] == 48

    def test_date_start_written(self):
        gs = GuiGlobalSettings(date_start="01/06/2030 00:00")
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["date_start"] == "01/06/2030 00:00"

    def test_enable_primary_energy_written(self):
        gs = GuiGlobalSettings(enable_primary_energy=False)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["enable_primary_energy"] is False

    def test_temporal_section_created(self):
        gs = GuiGlobalSettings(resolution_hours=2, rolling_horizon_hours=72)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert "temporal" in d
        assert d["temporal"]["resolution_hours"] == 2
        assert d["temporal"]["rolling_horizon_hours"] == 72

    def test_solver_section_created(self):
        gs = GuiGlobalSettings(solver_name="gurobi", solver_threads=8, solver_gap=0.001)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert "solver" in d
        assert d["solver"]["name"] == "gurobi"
        assert d["solver"]["threads"] == 8
        assert d["solver"]["gap"] == pytest.approx(0.001)

    def test_solver_verbose_flag(self):
        gs = GuiGlobalSettings(solver_verbose=True)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["solver"]["verbose"] is True

    def test_solver_scale_constraints(self):
        gs = GuiGlobalSettings(solver_scale_constraints=False)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["solver"]["scale_constraints"] is False

    def test_solver_specific_options(self):
        gs = GuiGlobalSettings(solver_specific_options={"mip_gap": 0.005})
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert d["solver"]["options"] == {"mip_gap": 0.005}

    def test_solver_no_options_if_empty(self):
        gs = GuiGlobalSettings(solver_specific_options={})
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert "options" not in d["solver"]

    def test_n1_security_section_created(self):
        gs = GuiGlobalSettings(n1_enabled=True, n1_transmission_reserve_factor=0.8)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert "n1_security" in d
        assert d["n1_security"]["enabled"] is True
        assert d["n1_security"]["transmission_reserve_factor"] == pytest.approx(0.8)

    def test_master_problem_section(self):
        gs = GuiGlobalSettings(mp_stochastic=True, mp_representative_days=10)
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        assert "master_problem" in d
        assert d["master_problem"]["stochastic"] is True
        assert d["master_problem"]["representative_days"] == 10

    def test_mga_subsection(self):
        gs = GuiGlobalSettings(
            mp_mga_enabled=True,
            mp_mga_num_alternatives=20,
            mp_mga_slack_fraction=0.10,
        )
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        mga = d["master_problem"]["mga"]
        assert mga["enabled"] is True
        assert mga["num_alternatives"] == 20
        assert mga["slack_fraction"] == pytest.approx(0.10)

    def test_visual_scaling_section(self):
        gs = GuiGlobalSettings(
            visual_scaling=GuiVisualScaling(
                marker_min_px=10.0,
                electrical_marker_scale=0.05,
            )
        )
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        vs = d["visual_scaling"]
        assert vs["marker_min_px"] == pytest.approx(10.0)
        assert vs["electrical_marker_scale"] == pytest.approx(0.05)

    def test_preserves_existing_keys(self):
        gs = GuiGlobalSettings()
        d: dict[str, Any] = {"my_custom_key": "should_remain"}
        global_settings_to_config_dict(gs, d)
        assert d["my_custom_key"] == "should_remain"

    def test_tsam_settings(self):
        gs = GuiGlobalSettings(
            mp_use_tsam=True,
            mp_tsam_num_periods=15,
            mp_tsam_method="hierarchical",
            mp_tsam_inter_period_linking=False,
        )
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        mp = d["master_problem"]
        assert mp["use_tsam"] is True
        assert mp["tsam_num_periods"] == 15
        assert mp["tsam_method"] == "hierarchical"
        assert mp["tsam_inter_period_linking"] is False

    def test_all_temporal_keys(self):
        gs = GuiGlobalSettings(
            resolution_hours=2,
            rolling_horizon_hours=72,
            overlap_hours=12,
            investment_resolution=4380,
            primary_energy_resolution=48,
            use_rolling_horizon=False,
        )
        d: dict[str, Any] = {}
        global_settings_to_config_dict(gs, d)
        t = d["temporal"]
        assert t["resolution_hours"] == 2
        assert t["rolling_horizon_hours"] == 72
        assert t["overlap_hours"] == 12
        assert t["investment_resolution"] == 4380
        assert t["primary_energy_resolution"] == 48
        assert t["use_rolling_horizon"] is False


# =====================================================================
# 9. stochastic_scenarios_to_config_dict
# =====================================================================


class TestStochasticScenarioToConfigDict:
    """Tests for applying stochastic scenarios to a system dict."""

    def test_empty_scenarios_no_change(self):
        sys_dict: dict[str, Any] = {}
        stochastic_scenarios_to_config_dict([], sys_dict)
        assert "stochastic_scenarios" not in sys_dict

    def test_single_scenario(self):
        scenarios = [
            GuiStochasticScenario(
                name="Base", probability=1.0,
                description="Base case",
                multipliers={"fuel_cost": 1.0},
            ),
        ]
        sys_dict: dict[str, Any] = {}
        stochastic_scenarios_to_config_dict(scenarios, sys_dict)
        assert "stochastic_scenarios" in sys_dict
        assert len(sys_dict["stochastic_scenarios"]) == 1
        sc = sys_dict["stochastic_scenarios"][0]
        assert sc["name"] == "Base"
        assert sc["probability"] == 1.0
        assert sc["description"] == "Base case"
        assert sc["multipliers"]["fuel_cost"] == 1.0

    def test_multiple_scenarios(self):
        scenarios = [
            GuiStochasticScenario(name="Low", probability=0.3, multipliers={"demand": 0.8}),
            GuiStochasticScenario(name="High", probability=0.7, multipliers={"demand": 1.2}),
        ]
        sys_dict: dict[str, Any] = {}
        stochastic_scenarios_to_config_dict(scenarios, sys_dict)
        assert len(sys_dict["stochastic_scenarios"]) == 2

    def test_scenario_multipliers_preserved(self):
        scenarios = [
            GuiStochasticScenario(
                name="Multi", probability=0.5,
                multipliers={"demand": 1.1, "fuel_cost": 1.3, "invest": 0.9},
            ),
        ]
        sys_dict: dict[str, Any] = {}
        stochastic_scenarios_to_config_dict(scenarios, sys_dict)
        m = sys_dict["stochastic_scenarios"][0]["multipliers"]
        assert m["demand"] == pytest.approx(1.1)
        assert m["fuel_cost"] == pytest.approx(1.3)
        assert m["invest"] == pytest.approx(0.9)

    def test_empty_multipliers(self):
        scenarios = [
            GuiStochasticScenario(name="NoMult", probability=1.0, multipliers={}),
        ]
        sys_dict: dict[str, Any] = {}
        stochastic_scenarios_to_config_dict(scenarios, sys_dict)
        assert sys_dict["stochastic_scenarios"][0]["multipliers"] == {}

    def test_preserves_existing_keys(self):
        sys_dict: dict[str, Any] = {"name": "TestSystem", "generators": {}}
        scenarios = [GuiStochasticScenario(name="S1", probability=1.0)]
        stochastic_scenarios_to_config_dict(scenarios, sys_dict)
        assert sys_dict["name"] == "TestSystem"
        assert sys_dict["generators"] == {}


# =====================================================================
# 10. Round-trip test
# =====================================================================


class TestRoundTrip:
    """Tests for config -> gui_states -> yaml -> parse -> verify."""

    def _round_trip(self, config: ESFEXConfig, **kwargs) -> dict:
        """Run the full round-trip and return parsed YAML."""
        states = config_to_gui_states(config)
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states,
                base_config=config,
                output_path=output_path,
                **kwargs,
            )
            with open(output_path) as fh:
                return yaml.safe_load(fh)
        finally:
            output_path.unlink(missing_ok=True)

    def test_round_trip_preserves_system_name(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        assert "TestSystem" in data["systems"]

    def test_round_trip_preserves_node_count(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        sys_data = data["systems"]["TestSystem"]
        assert sys_data["nodes"]["num_nodes"] == 2

    def test_round_trip_preserves_generator_names(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        gens = data["systems"]["TestSystem"]["generators"]
        assert "gas_turbine" in gens
        assert "solar" in gens
        assert "wind" in gens

    def test_round_trip_preserves_rated_power(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        gas = data["systems"]["TestSystem"]["generators"]["gas_turbine"]
        assert gas["rated_power"][0] == 100.0
        assert gas["rated_power"][1] == 50.0

    def test_round_trip_preserves_battery_keys(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        bats = data["systems"]["TestSystem"]["batteries"]
        assert "li_ion" in bats

    def test_round_trip_preserves_battery_capacity(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        bat = data["systems"]["TestSystem"]["batteries"]["li_ion"]
        assert bat["capacity"][0] == 50.0
        assert bat["capacity"][1] == 80.0

    def test_round_trip_preserves_transmission_capacity(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        conns = data["systems"]["TestSystem"]["nodes"]["nodes_connections"]
        assert conns[0 * 2 + 1] == 200.0
        assert conns[1 * 2 + 0] == 200.0

    def test_round_trip_preserves_penalties(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        pen = data["systems"]["TestSystem"]["penalties"]
        assert pen["LOSS_OF_LOAD"] == 10_000_000
        assert pen["Curtailment"] == 100.0
        assert pen["CO2_cost"] == 10.0

    def test_round_trip_preserves_settings(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        sys_data = data["systems"]["TestSystem"]
        assert sys_data["discount_rate"] == pytest.approx(0.05)
        assert sys_data["target_re_penetration"] == pytest.approx(0.5)

    def test_round_trip_preserves_fuel_properties(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        fuels = data["systems"]["TestSystem"].get("fuels", {})
        assert "Gas" in fuels
        assert fuels["Gas"]["emission_factor"] == pytest.approx(0.20)

    def test_round_trip_preserves_co2_budget(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        co2 = data["systems"]["TestSystem"]["co2_budget"]
        assert co2["enabled"] is True
        assert co2["annual_budget"] == 100_000

    def test_round_trip_preserves_investment_max(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        solar = data["systems"]["TestSystem"]["generators"]["solar"]
        assert solar["invest_max_power"][0] == 200.0
        assert solar["invest_max_power"][1] == 200.0

    def test_round_trip_with_global_settings(self):
        config = _make_esfex_config()
        gs = config_to_global_settings(config)
        data = self._round_trip(config, global_settings=gs)
        assert data["simulation_mode"] == "development"
        assert "temporal" in data
        assert "solver" in data

    def test_round_trip_generator_fuel_cost(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        gas = data["systems"]["TestSystem"]["generators"]["gas_turbine"]
        assert gas["fuel_cost"][0] == 94.0
        assert gas["fuel_cost"][1] == 94.0

    def test_round_trip_node_names(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        names = data["systems"]["TestSystem"]["nodes"]["node_names"]
        assert len(names) == 2

    def test_round_trip_dc_power_flow(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        dc = data["systems"]["TestSystem"]["dc_power_flow"]
        # enable_angle_limits removed; max_angle_diff_deg + slack_bus survive.
        assert "max_angle_diff_deg" in dc
        assert "slack_bus" in dc

    def test_round_trip_ac_power_flow(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        sys_data = data["systems"]["TestSystem"]
        assert "power_flow_mode" in sys_data
        ac = sys_data["ac_power_flow"]
        assert "base_mva" in ac
        assert "voltage_min_pu" in ac
        assert "voltage_max_pu" in ac
        assert "default_power_factor" in ac
        assert "load_power_factor" in ac
        assert "q_slack_penalty" in ac
        assert "min_reactance_pu" in ac
        assert "tap_ratio_min" in ac
        assert "tap_ratio_max" in ac
        assert "q_min_ratio" in ac

    def test_round_trip_transmission_lines_geo(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        sys_data = data["systems"]["TestSystem"]
        geo = sys_data.get("transmission_lines_geo", [])
        assert len(geo) == 1
        assert "line_id" in geo[0]
        assert geo[0]["capacity_mw"] == 200.0

    def test_round_trip_reserve_arrays(self):
        config = _make_esfex_config()
        data = self._round_trip(config)
        nodes = data["systems"]["TestSystem"]["nodes"]
        assert nodes["reserve_static"] == [10.0, 10.0]
        assert nodes["reserve_dynamic"] == [20.0, 20.0]
        assert nodes["reserve_duration"] == [2, 2]


# =====================================================================
# Additional edge-case tests
# =====================================================================


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def _export_and_parse(self, states, config, **kwargs) -> dict:
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states,
                base_config=config,
                output_path=output_path,
                **kwargs,
            )
            with open(output_path) as fh:
                return yaml.safe_load(fh)
        finally:
            output_path.unlink(missing_ok=True)

    def test_multiple_systems_in_gui_state_to_yaml(self):
        state1 = _build_minimal_gui_state()
        state1.name = "System1"
        state2 = GuiSystemState(
            name="System2",
            nodes=[GuiNode(index=0, name="Node X")],
        )
        config = _make_esfex_config()
        data = self._export_and_parse(
            {"System1": state1, "System2": state2}, config
        )
        assert "System1" in data["systems"]
        assert "System2" in data["systems"]

    def test_empty_generators_dict(self):
        state = GuiSystemState(
            name="Empty",
            nodes=[GuiNode(index=0, name="N0")],
        )
        config = _make_esfex_config()
        data = self._export_and_parse({"Empty": state}, config)
        sys_data = data["systems"]["Empty"]
        assert sys_data["generators"] == {}

    def test_gui_state_demand_sectors(self):
        state = _build_minimal_gui_state()
        state.demand_sectors = {
            "residential": GuiDemandSector(
                sector_id="residential",
                is_flexible=True,
                flexibility_ratio=0.15,
                criticality="high",
            ),
        }
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        ed = data["systems"]["MinimalSystem"]["electric_demand"]
        assert "residential" in ed
        assert ed["residential"]["is_flexible"] is True
        assert ed["residential"]["flexibility_ratio"] == pytest.approx(0.15)
        assert ed["residential"]["criticality"] == "high"

    def test_gui_state_non_electric_demand(self):
        state = _build_minimal_gui_state()
        state.non_electric_demand = {
            "heat": GuiNonElectricDemand(
                demand_id="heat",
                fuel="Gas",
                unit="ton",
                demand=[100, 200],
            ),
        }
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        ned = data["systems"]["MinimalSystem"]["non_electric_demand"]
        assert "heat" in ned
        assert ned["heat"]["fuel"] == "Gas"
        assert ned["heat"]["demand"] == [100, 200]

    def test_gui_state_fuels_renewable_skipped(self):
        state = _build_minimal_gui_state()
        state.fuels = {
            "Sun": GuiFuel(fuel_id="Sun", name="Sun"),
            "Wind": GuiFuel(fuel_id="Wind", name="Wind"),
            "Gas": GuiFuel(fuel_id="Gas", name="Gas", emission_factor=0.2,
                           energy_content=12.28, price_base=110.0),
        }
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        fuels = data["systems"]["MinimalSystem"].get("fuels", {})
        assert "Gas" in fuels
        assert "Sun" not in fuels
        assert "Wind" not in fuels

    def test_criticality_penalties_exported(self):
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        crit = data["systems"]["MinimalSystem"]["criticality_penalties"]
        assert "critical" in crit
        assert "high" in crit
        assert "medium" in crit
        assert "low" in crit

    def test_node_losses_exported(self):
        state = _build_minimal_gui_state()
        state.nodes[0].losses = 0.02
        state.nodes[1].losses = 0.03
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        losses = data["systems"]["MinimalSystem"]["nodes"]["losses"]
        assert losses[0] == pytest.approx(0.02)
        assert losses[1] == pytest.approx(0.03)

    def test_battery_scalar_fields_per_node(self):
        """Verify battery per-node arrays include all scalar fields."""
        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states, base_config=config, output_path=output_path,
            )
            with open(output_path) as fh:
                data = yaml.safe_load(fh)
            bat = data["systems"]["TestSystem"]["batteries"]["li_ion"]
            # Check that essential per-node fields are present as arrays
            assert isinstance(bat["life_time"], list)
            assert isinstance(bat["rated_power"], list)
            assert isinstance(bat["capacity"], list)
            assert isinstance(bat["efficiency_charge"], list)
            assert isinstance(bat["efficiency_discharge"], list)
            assert isinstance(bat["soc_initial"], list)
        finally:
            output_path.unlink(missing_ok=True)

    def test_generator_invest_cost_round_trip(self):
        """Verify invest_cost arrays survive round-trip."""
        config = _make_esfex_config()
        states = config_to_gui_states(config)

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states, base_config=config, output_path=output_path,
            )
            with open(output_path) as fh:
                data = yaml.safe_load(fh)
            solar = data["systems"]["TestSystem"]["generators"]["solar"]
            assert solar["invest_cost"][0] == 3_900_000.0
            assert solar["invest_cost"][1] == 3_900_000.0
        finally:
            output_path.unlink(missing_ok=True)

    def test_meta_network_systems_updated(self):
        """meta_network.systems should list all GUI states."""
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        assert data["meta_network"]["systems"] == ["MinimalSystem"]

    def test_fuel_transport_distances_exported(self):
        """Fuel transport distances matrix should be present."""
        state = _build_minimal_gui_state()
        config = _make_esfex_config()
        data = self._export_and_parse({"MinimalSystem": state}, config)
        dist = data["systems"]["MinimalSystem"]["fuel_transport_distances"]
        assert isinstance(dist, list)
        # 2x2 matrix of zeros (no fuel routes)
        assert len(dist) == 2
        assert len(dist[0]) == 2


# =====================================================================
# Reservoir fields: round-trip
# =====================================================================


class TestReservoirRoundTrip:
    """Tests for reservoir fields in config<->GUI<->YAML round-trip."""

    def _round_trip(self, config):
        """Config → GuiState → YAML → dict."""
        states = config_to_gui_states(config)
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)
        try:
            gui_state_to_yaml(
                states=states, base_config=config, output_path=output_path,
            )
            with open(output_path) as fh:
                return yaml.safe_load(fh)
        finally:
            output_path.unlink(missing_ok=True)

    def test_reservoir_defaults_empty_in_output(self):
        """Generators without reservoir should not have reservoir arrays in output."""
        config = _make_esfex_config()
        data = self._round_trip(config)
        gas = data["systems"]["TestSystem"]["generators"]["gas_turbine"]
        # Empty lists become empty or zero-filled
        assert gas.get("reservoir_capacity", []) == [0.0, 0.0] or gas.get("reservoir_capacity") is None or gas.get("reservoir_capacity") == [0, 0]

    def test_reservoir_values_survive_round_trip(self):
        """Reservoir values should survive config → GUI → YAML → dict."""
        n = _2N
        gen = _make_generator(
            "Hydro_Dam", "Renewable", "Hydro",
            rated_power=[200.0, 100.0],
        )
        # Set reservoir fields
        gen.reservoir_capacity = [500.0, 300.0]
        gen.reservoir_initial_level = [0.8, 0.6]
        gen.reservoir_min_level = [0.1, 0.15]
        gen.reservoir_max_level = [0.95, 0.9]
        gen.reservoir_turbine_efficiency = [0.92, 0.88]
        gen.reservoir_evaporation_rate = [0.001, 0.002]
        gen.reservoir_pump_capacity = [50.0, 30.0]
        gen.reservoir_pump_efficiency = [0.87, 0.85]
        gen.reservoir_spillage_allowed = False
        gen.reservoir_invest_cost = [100000.0, 80000.0]
        gen.reservoir_invest_max = [200.0, 150.0]
        gen.reservoir_head_min_factor = [0.45, 0.6]

        config = _make_esfex_config()
        # Inject reservoir generator
        config.systems["TestSystem"].generators["hydro_dam"] = gen

        data = self._round_trip(config)
        hydro = data["systems"]["TestSystem"]["generators"]["hydro_dam"]
        assert hydro["reservoir_head_min_factor"] == [0.45, 0.6]
        assert hydro["reservoir_capacity"] == [500.0, 300.0]
        assert hydro["reservoir_initial_level"] == [0.8, 0.6]
        assert hydro["reservoir_min_level"] == [0.1, 0.15]
        assert hydro["reservoir_max_level"] == [0.95, 0.9]
        assert hydro["reservoir_turbine_efficiency"] == [0.92, 0.88]
        assert hydro["reservoir_evaporation_rate"] == [0.001, 0.002]
        assert hydro["reservoir_pump_capacity"] == [50.0, 30.0]
        assert hydro["reservoir_pump_efficiency"] == [0.87, 0.85]
        assert hydro["reservoir_spillage_allowed"] is False
        assert hydro["reservoir_invest_cost"] == [100000.0, 80000.0]
        assert hydro["reservoir_invest_max"] == [200.0, 150.0]

    def test_cascade_fields_survive_round_trip(self):
        """Hydraulic cascade (downstream name + delay) survives the round-trip."""
        gen = _make_generator(
            "Hydro_Lower", "Renewable", "Hydro",
            rated_power=[200.0, 100.0],
        )
        gen.reservoir_capacity = [500.0, 300.0]
        gen.cascade_downstream = "Hydro_Outlet"
        gen.cascade_delay_hours = 4

        config = _make_esfex_config()
        config.systems["TestSystem"].generators["hydro_lower"] = gen

        data = self._round_trip(config)
        gens = data["systems"]["TestSystem"]["generators"]
        hydro = gens["hydro_lower"]
        assert hydro["cascade_downstream"] == "Hydro_Outlet"
        assert hydro["cascade_delay_hours"] == 4
        # A non-cascade generator stays clean (no downstream key emitted).
        assert "cascade_downstream" not in gens["gas_turbine"]

    def test_gui_generator_instance_has_reservoir_fields(self):
        """GuiGeneratorInstance should have all reservoir attributes."""
        inst = GuiGeneratorInstance(
            instance_id="hydro_dam_bus_0",
            unit_key="hydro_dam",
            name="Hydro_Dam",
            gen_type="Renewable",
            fuel="Hydro",
            reservoir_capacity=500.0,
            reservoir_initial_level=0.8,
            reservoir_pump_capacity=50.0,
        )
        assert inst.reservoir_capacity == 500.0
        assert inst.reservoir_initial_level == 0.8
        assert inst.reservoir_pump_capacity == 50.0
        # Defaults
        assert inst.reservoir_min_level == 0.1
        assert inst.reservoir_max_level == 1.0
        assert inst.reservoir_turbine_efficiency == 0.9
        assert inst.reservoir_evaporation_rate == 0.0
        assert inst.reservoir_pump_efficiency == 0.85
        assert inst.reservoir_spillage_allowed is True
        assert inst.reservoir_invest_cost == 0.0
        assert inst.reservoir_invest_max == 0.0


class TestForecastDemandSerialization:
    """Grid Builder forecast demand must reach the config as demand_paths (#7)."""

    def test_demand_paths_emitted_from_forecast(self, tmp_path):
        from types import SimpleNamespace

        import numpy as np

        from esfex.visualization.workflows.grid_mapping_steps import (
            write_forecast_demand_csvs,
        )

        config = _make_esfex_config()
        states = config_to_gui_states(config)
        state = states["TestSystem"]
        n = len(state.nodes)
        series = np.tile(np.array([10.0, 20.0, 15.0]).reshape(-1, 1), (1, n))
        result = SimpleNamespace(
            demand_multi_year=series, demand=None, peak_mw=[20.0] * n)

        assert write_forecast_demand_csvs(
            state.nodes, result, tmp_path / "demand") == n

        out = tmp_path / "out.yaml"
        gui_state_to_yaml(states=states, base_config=config, output_path=out)
        data = yaml.safe_load(out.read_text())

        dp = data["systems"]["TestSystem"].get("demand_paths")
        assert dp is not None and len(dp) == n
        assert all(p.endswith(".csv") and Path(p).exists() for p in dp)


class TestDeletedSystemPruning:
    """A deleted system must not survive the save and reappear on reload."""

    def test_deleted_system_is_pruned_from_yaml(self, tmp_path):
        import copy

        config = _make_esfex_config()
        config.systems["SecondSystem"] = copy.deepcopy(
            config.systems["TestSystem"])
        config.meta_network.systems = ["TestSystem", "SecondSystem"]

        states = config_to_gui_states(config)
        assert set(states) == {"TestSystem", "SecondSystem"}
        # Simulate deleting "SecondSystem" in the GUI.
        del states["SecondSystem"]

        out = tmp_path / "out.yaml"
        gui_state_to_yaml(states=states, base_config=config, output_path=out)
        data = yaml.safe_load(out.read_text())

        # The deleted system is gone from both the systems map and the list...
        assert "SecondSystem" not in data["systems"]
        assert "SecondSystem" not in data["meta_network"]["systems"]
        # ...so reloading does not resurrect it.
        reloaded = config_to_gui_states(ESFEXConfig(**data))
        assert set(reloaded) == {"TestSystem"}


class TestFuelSupplyStressRoundTrip:
    """The fuel-supply-stress params (#13) live on the Fuel Source (entry point):
    they survive config<->GUI<->YAML and reach the solver via the runner's
    entry-point -> internal-source builder."""

    def test_entry_point_stress_params_round_trip(self, tmp_path):
        from esfex.config.schema import FuelEntryPointConfig, GeoCoordinate
        from esfex.runner import Orchestrator

        sys_cfg = _make_system_config()
        sys_cfg.fuel_entry_points = [
            FuelEntryPointConfig(
                name="Port", fuels=["Gas"], node=0,
                coordinate=GeoCoordinate(latitude=10.0, longitude=20.0),
                fuel_params={
                    "Gas": {
                        "max_import_rate": 1000.0, "import_cost": 5.0,
                        "transport_transit_days_per_100km": 2.0,
                        "disruption_start_hour": 100, "disruption_end_hour": 200,
                        "disruption_availability": 0.3,
                    }
                },
            )
        ]
        config = _make_esfex_config(sys_cfg)
        sysname = next(iter(config.systems))

        # config -> GUI: the entry point's per-fuel params carry every field.
        states = config_to_gui_states(config)
        fp = states[sysname].fuel_entry_points[0].fuel_params["Gas"]
        assert fp.transport_transit_days_per_100km == 2.0
        assert fp.disruption_start_hour == 100
        assert fp.disruption_end_hour == 200
        assert fp.disruption_availability == 0.3

        # GUI -> YAML: the serialized fuel_params (what the runner reads) has them.
        out = tmp_path / "out.yaml"
        gui_state_to_yaml(states, config, out)
        data = yaml.safe_load(out.read_text())
        sd = data["systems"][sysname]["fuel_entry_points"][0]["fuel_params"]["Gas"]
        assert sd["transport_transit_days_per_100km"] == 2.0
        assert sd["disruption_start_hour"] == 100
        assert sd["disruption_end_hour"] == 200
        assert sd["disruption_availability"] == 0.3

        # ...survive a reload...
        reloaded = ESFEXConfig(**data)
        fp2 = config_to_gui_states(reloaded)[sysname].fuel_entry_points[0].fuel_params["Gas"]
        assert fp2.transport_transit_days_per_100km == 2.0
        assert fp2.disruption_availability == 0.3

        # ...and the runner propagates them into the internal solver source.
        rsys = reloaded.systems[sysname]
        pe = Orchestrator._build_pe_sources_from_entries(None, rsys, rsys.num_nodes)
        gas = pe["Gas"]
        assert gas["transport_transit_days_per_100km"] == 2.0
        assert gas["disruption_start_hour"] == 100
        assert gas["disruption_end_hour"] == 200
        assert gas["disruption_availability"] == 0.3


# =====================================================================
# Cost curve serialization helpers
# =====================================================================


class TestCostCurveToGuiData:
    """Tests for _cost_curve_to_gui_data()."""

    def test_flat_returns_none(self):
        curve = CostCurveConfig(curve_type="flat")
        result = _cost_curve_to_gui_data(curve)
        assert result is None

    def test_linear_returns_dict(self):
        curve = CostCurveConfig(
            curve_type="linear",
            price_at_zero=10.0,
            price_at_max=50.0,
            num_segments=5,
        )
        result = _cost_curve_to_gui_data(curve)
        assert result == {
            "price_at_zero": 10.0,
            "price_at_max": 50.0,
            "num_segments": 5,
        }

    def test_stepwise_returns_blocks(self):
        blocks = [
            CostCurveBlock(fraction=0.5, price=100.0),
            CostCurveBlock(fraction=1.0, price=200.0),
        ]
        curve = CostCurveConfig(curve_type="stepwise", blocks=blocks)
        result = _cost_curve_to_gui_data(curve)
        assert result == {
            "blocks": [
                {"fraction": 0.5, "price": 100.0},
                {"fraction": 1.0, "price": 200.0},
            ]
        }

    def test_exponential_returns_dict(self):
        curve = CostCurveConfig(
            curve_type="exponential",
            base_price=20.0,
            scale_factor=0.5,
            num_segments=8,
        )
        result = _cost_curve_to_gui_data(curve)
        assert result == {
            "base_price": 20.0,
            "scale_factor": 0.5,
            "num_segments": 8,
        }

    def test_linear_none_values_default(self):
        curve = CostCurveConfig(curve_type="linear")
        result = _cost_curve_to_gui_data(curve)
        assert result == {
            "price_at_zero": 0.0,
            "price_at_max": 0.0,
            "num_segments": 5,
        }


class TestGuiDataToCostCurveConfig:
    """Tests for _gui_data_to_cost_curve_config()."""

    def test_flat_returns_none(self):
        result = _gui_data_to_cost_curve_config(
            "flat", {"price_at_zero": 10.0}
        )
        assert result is None

    def test_none_data_returns_none(self):
        result = _gui_data_to_cost_curve_config("linear", None)
        assert result is None

    def test_linear_roundtrip(self):
        data = {"price_at_zero": 5.0, "price_at_max": 30.0, "num_segments": 7}
        result = _gui_data_to_cost_curve_config("linear", data)
        assert result == {
            "curve_type": "linear",
            "price_at_zero": 5.0,
            "price_at_max": 30.0,
            "num_segments": 7,
        }

    def test_stepwise_roundtrip(self):
        data = {
            "blocks": [
                {"fraction": 0.3, "price": 80.0},
                {"fraction": 0.7, "price": 120.0},
                {"fraction": 1.0, "price": 200.0},
            ]
        }
        result = _gui_data_to_cost_curve_config("stepwise", data)
        assert result == {
            "curve_type": "stepwise",
            "blocks": [
                {"fraction": 0.3, "price": 80.0},
                {"fraction": 0.7, "price": 120.0},
                {"fraction": 1.0, "price": 200.0},
            ],
        }

    def test_exponential_roundtrip(self):
        data = {"base_price": 15.0, "scale_factor": 2.0, "num_segments": 10}
        result = _gui_data_to_cost_curve_config("exponential", data)
        assert result == {
            "curve_type": "exponential",
            "base_price": 15.0,
            "scale_factor": 2.0,
            "num_segments": 10,
        }


class TestCostCurveRoundTrip:
    """End-to-end: CostCurveConfig -> GUI data -> config dict."""

    def test_linear_roundtrip(self):
        original = CostCurveConfig(
            curve_type="linear",
            price_at_zero=10.0,
            price_at_max=50.0,
            num_segments=6,
        )
        gui_data = _cost_curve_to_gui_data(original)
        cfg = _gui_data_to_cost_curve_config("linear", gui_data)
        assert cfg is not None
        assert cfg["curve_type"] == "linear"
        assert cfg["price_at_zero"] == 10.0
        assert cfg["price_at_max"] == 50.0
        assert cfg["num_segments"] == 6

    def test_stepwise_roundtrip(self):
        blocks = [
            CostCurveBlock(fraction=0.5, price=100.0),
            CostCurveBlock(fraction=1.0, price=200.0),
        ]
        original = CostCurveConfig(curve_type="stepwise", blocks=blocks)
        gui_data = _cost_curve_to_gui_data(original)
        cfg = _gui_data_to_cost_curve_config("stepwise", gui_data)
        assert cfg is not None
        assert cfg["curve_type"] == "stepwise"
        assert len(cfg["blocks"]) == 2
        assert cfg["blocks"][0] == {"fraction": 0.5, "price": 100.0}
        assert cfg["blocks"][1] == {"fraction": 1.0, "price": 200.0}

    def test_exponential_roundtrip(self):
        original = CostCurveConfig(
            curve_type="exponential",
            base_price=20.0,
            scale_factor=0.5,
            num_segments=8,
        )
        gui_data = _cost_curve_to_gui_data(original)
        cfg = _gui_data_to_cost_curve_config("exponential", gui_data)
        assert cfg is not None
        assert cfg["curve_type"] == "exponential"
        assert cfg["base_price"] == 20.0
        assert cfg["scale_factor"] == 0.5
        assert cfg["num_segments"] == 8


class TestGuiGeneratorInstanceCostCurve:
    """GuiGeneratorInstance cost-curve field defaults and assignment."""

    def test_defaults(self):
        inst = GuiGeneratorInstance(
            instance_id="gen_0_bus_0",
            unit_key="gen_0",
            name="Gen0",
            gen_type="Thermal",
            fuel="Diesel",
        )
        assert inst.fuel_cost_curve_type == "flat"
        assert inst.fuel_cost_curve_data is None

    def test_set_linear(self):
        inst = GuiGeneratorInstance(
            instance_id="gen_1_bus_0",
            unit_key="gen_1",
            name="Gen1",
            gen_type="Thermal",
            fuel="Gas",
            fuel_cost_curve_type="linear",
            fuel_cost_curve_data={
                "price_at_zero": 10.0,
                "price_at_max": 40.0,
                "num_segments": 5,
            },
        )
        assert inst.fuel_cost_curve_type == "linear"
        assert inst.fuel_cost_curve_data == {
            "price_at_zero": 10.0,
            "price_at_max": 40.0,
            "num_segments": 5,
        }


class TestGuiBatteryInstanceCostCurve:
    """GuiBatteryInstance cost-curve field defaults and assignment."""

    def test_defaults(self):
        inst = GuiBatteryInstance(
            instance_id="bat_0_bus_0",
            unit_key="bat_0",
            name="Bat0",
        )
        assert inst.discharge_cost_curve_type == "flat"
        assert inst.discharge_cost_curve_data is None

    def test_set_stepwise(self):
        inst = GuiBatteryInstance(
            instance_id="bat_1_bus_0",
            unit_key="bat_1",
            name="Bat1",
            discharge_cost_curve_type="stepwise",
            discharge_cost_curve_data={
                "blocks": [
                    {"fraction": 0.5, "price": 50.0},
                    {"fraction": 1.0, "price": 100.0},
                ]
            },
        )
        assert inst.discharge_cost_curve_type == "stepwise"
        assert inst.discharge_cost_curve_data == {
            "blocks": [
                {"fraction": 0.5, "price": 50.0},
                {"fraction": 1.0, "price": 100.0},
            ]
        }
