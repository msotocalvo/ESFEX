"""
Tests for esfex.zones module.

Covers all public and private functions:
- _haversine (great-circle distance)
- _polygon_centroid (mean-of-vertices centroid)
- _find_nearest_bus (closest bus by haversine)
- _match_generators (technology/fuel matching)
- _match_batteries (storage technology matching)
- expand_config_with_zones (full zone expansion pipeline)
- ZoneMapping dataclass
- _GEN_PER_NODE_FIELDS, _BAT_PER_NODE_FIELDS constants
"""

import math
from copy import deepcopy
from unittest.mock import MagicMock

import numpy as np
import pytest

from esfex.config.schema import (
    BatteryConfig,
    BatteryTechnologyConfig,
    BusConfig,
    DevelopmentZoneConfig,
    GeoCoordinate,
    GeneratorConfig,
    NodeConfig,
    SystemConfig,
    TechnologyConfig,
)
from esfex.zones import (
    ZoneMapping,
    _BAT_PER_NODE_FIELDS,
    _BAT_TECH_PER_NODE_FIELDS,
    _GEN_PER_NODE_FIELDS,
    _TECH_PER_NODE_FIELDS,
    _find_nearest_bus,
    _haversine,
    _match_batteries,
    _match_battery_technologies,
    _match_generators,
    _match_technologies,
    _polygon_centroid,
    expand_config_with_zones,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node_config(num_nodes: int, coords: list[tuple[float, float]] | None = None):
    """Build a minimal NodeConfig with NxN zero adjacency matrix."""
    n = num_nodes
    connections = [0.0] * (n * n)
    node_coordinates = None
    if coords:
        node_coordinates = [
            GeoCoordinate(latitude=lat, longitude=lon) for lat, lon in coords
        ]
    return NodeConfig(
        num_nodes=n,
        nodes_connections=connections,
        node_coordinates=node_coordinates,
    )


def _make_generator(
    name: str,
    fuel: str,
    num_nodes: int,
    *,
    technology: str | None = None,
    gen_type: str = "Renewable",
    rated_power: float = 100.0,
    invest_max_power: float = 0.0,
) -> GeneratorConfig:
    """Build a minimal GeneratorConfig for testing."""
    return GeneratorConfig(
        name=name,
        type=gen_type,
        fuel=fuel,
        technology=technology,
        life_time=[25] * num_nodes,
        initial_age=[0] * num_nodes,
        degradation_rate=[0.005] * num_nodes,
        decommissioning_cost=[1000.0] * num_nodes,
        rated_power=[rated_power] * num_nodes,
        min_power=[0.0] * num_nodes,
        min_up=[1] * num_nodes,
        min_down=[1] * num_nodes,
        ramp_up=[1.0] * num_nodes,
        ramp_down=[1.0] * num_nodes,
        eff_at_rated=[1.0] * num_nodes,
        eff_at_min=[0.8] * num_nodes,
        inertia=[0.0] * num_nodes,
        start_up_cost=[0.0] * num_nodes,
        fuel_cost=[0.0] * num_nodes,
        fixed_cost=[10.0] * num_nodes,
        maintenance_cost=[5.0] * num_nodes,
        invest_cost=[1500.0] * num_nodes,
        invest_max_power=[invest_max_power] * num_nodes,
    )


def _make_battery(name: str, num_nodes: int) -> BatteryConfig:
    """Build a minimal BatteryConfig for testing."""
    return BatteryConfig(
        name=name,
        life_time=[15] * num_nodes,
        initial_age=[0] * num_nodes,
        degradation_rate=[0.01] * num_nodes,
        decommissioning_cost=[500.0] * num_nodes,
        rated_power=[50.0] * num_nodes,
        min_power=[0.0] * num_nodes,
        min_up=[1] * num_nodes,
        min_down=[1] * num_nodes,
        ramp_up=[1.0] * num_nodes,
        ramp_down=[1.0] * num_nodes,
        eff_at_rated=[0.95] * num_nodes,
        eff_at_min=[0.9] * num_nodes,
        inertia=[0.0] * num_nodes,
        start_up_cost=[0.0] * num_nodes,
        fuel_cost=[0.0] * num_nodes,
        fixed_cost=[5.0] * num_nodes,
        maintenance_cost=[2.0] * num_nodes,
        invest_cost=[800.0] * num_nodes,
        invest_cost_energy=[200.0] * num_nodes,
        invest_max_power=[100.0] * num_nodes,
        invest_max_capacity=[400.0] * num_nodes,
        efficiency_charge=[0.95] * num_nodes,
        efficiency_discharge=[0.95] * num_nodes,
        soc_initial=[0.5] * num_nodes,
        max_DoD=[0.8] * num_nodes,
        capacity=[200.0] * num_nodes,
        MaxChargePower=[50.0] * num_nodes,
        MaxDischargePower=[50.0] * num_nodes,
    )


def _make_zone(
    name: str = "Zone_A",
    technology: str = "Solar",
    polygon_coords: list[tuple[float, float]] | None = None,
    max_capacity_mw: float | None = 500.0,
    line_cost_per_mw_km: float = 1500.0,
    transformer_cost_per_mw: float = 50000.0,
    target_bus: int | None = None,
    allowed_generators: list[str] | None = None,
    allowed_technologies: dict[str, float] | None = None,
    exclusive: bool = False,
) -> DevelopmentZoneConfig:
    """Build a DevelopmentZoneConfig for testing."""
    if polygon_coords is None:
        polygon_coords = [(22.0, -80.0), (22.5, -80.0), (22.5, -79.5), (22.0, -79.5)]
    polygon = [
        GeoCoordinate(latitude=lat, longitude=lon) for lat, lon in polygon_coords
    ]
    return DevelopmentZoneConfig(
        name=name,
        technology=technology,
        polygon=polygon,
        max_capacity_mw=max_capacity_mw,
        line_cost_per_mw_km=line_cost_per_mw_km,
        transformer_cost_per_mw=transformer_cost_per_mw,
        target_bus=target_bus,
        allowed_generators=allowed_generators,
        allowed_technologies=allowed_technologies,
        exclusive=exclusive,
    )


def _make_technology(
    name: str,
    fuel: str,
    num_nodes: int,
    *,
    tech_type: str = "Renewable",
    invest_cost: float = 900000.0,
    invest_max_power: float = 500.0,
) -> TechnologyConfig:
    """Build a minimal TechnologyConfig for testing."""
    return TechnologyConfig(
        name=name,
        type=tech_type,
        fuel=fuel,
        invest_cost=[invest_cost] * num_nodes,
        invest_max_power=[invest_max_power] * num_nodes,
        eff_at_rated=[1.0] * num_nodes,
        degradation_rate=[0.005] * num_nodes,
        lifetime=25,
        min_output=[0.0] * num_nodes,
        ramp_up=[1.0] * num_nodes,
        ramp_down=[1.0] * num_nodes,
        fuel_cost=[0.0] * num_nodes,
        fixed_cost=[10.0] * num_nodes,
        maintenance_cost=[5.0] * num_nodes,
        inertia=[0.0] * num_nodes,
        start_up_cost=[0.0] * num_nodes,
        eff_at_min=[0.0] * num_nodes,
        decommissioning_cost=[1000.0] * num_nodes,
    )


def _make_battery_technology(
    name: str,
    num_nodes: int,
    *,
    invest_max_power: float = 200.0,
    invest_max_capacity: float = 800.0,
) -> BatteryTechnologyConfig:
    """Build a minimal BatteryTechnologyConfig for testing."""
    return BatteryTechnologyConfig(
        name=name,
        invest_cost_power=[600000.0] * num_nodes,
        invest_cost_energy=[240000.0] * num_nodes,
        invest_max_power=[invest_max_power] * num_nodes,
        invest_max_capacity=[invest_max_capacity] * num_nodes,
        efficiency_charge=[0.95] * num_nodes,
        efficiency_discharge=[0.95] * num_nodes,
        degradation_rate=[0.01] * num_nodes,
        lifetime=15,
        soc_initial=[0.5] * num_nodes,
        max_DoD=[0.9] * num_nodes,
        maintenance_cost=[0.0] * num_nodes,
        inertia=[0.0] * num_nodes,
        throughput_degradation_cost=[0.0] * num_nodes,
        decommissioning_cost=[500.0] * num_nodes,
    )


def _make_system_config(
    num_nodes: int = 2,
    coords: list[tuple[float, float]] | None = None,
    generators: dict[str, GeneratorConfig] | None = None,
    batteries: dict[str, BatteryConfig] | None = None,
    technologies: dict[str, TechnologyConfig] | None = None,
    battery_technologies: dict[str, BatteryTechnologyConfig] | None = None,
    zones: list[DevelopmentZoneConfig] | None = None,
    buses: list[BusConfig] | None = None,
) -> SystemConfig:
    """Build a minimal SystemConfig for zone expansion testing."""
    if coords is None:
        coords = [(23.0, -82.0), (22.0, -79.0)]
    nodes = _make_node_config(num_nodes, coords)
    if generators is None:
        generators = {}
    if batteries is None:
        batteries = {}
    if zones is None:
        zones = []

    kwargs = dict(
        name="TestSystem",
        nodes=nodes,
        generators=generators,
        batteries=batteries,
        development_zones=zones,
    )
    if technologies is not None:
        kwargs["technologies"] = technologies
    if battery_technologies is not None:
        kwargs["battery_technologies"] = battery_technologies
    if buses is not None:
        kwargs["buses"] = buses

    return SystemConfig(**kwargs)


# ---------------------------------------------------------------------------
# _haversine
# ---------------------------------------------------------------------------


class TestHaversine:
    """Tests for _haversine distance calculation."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself is zero."""
        d = _haversine(51.5074, -0.1278, 51.5074, -0.1278)
        assert d == 0.0

    def test_london_to_paris(self):
        """London (51.5074, -0.1278) to Paris (48.8566, 2.3522) is ~344 km."""
        d = _haversine(51.5074, -0.1278, 48.8566, 2.3522)
        assert abs(d - 344.0) < 5.0  # within 5 km tolerance

    def test_new_york_to_los_angeles(self):
        """New York (40.7128, -74.0060) to LA (34.0522, -118.2437) is ~3944 km."""
        d = _haversine(40.7128, -74.0060, 34.0522, -118.2437)
        assert abs(d - 3944.0) < 30.0

    def test_antipodal_points(self):
        """Antipodal points should be ~20015 km apart (half circumference)."""
        d = _haversine(0.0, 0.0, 0.0, 180.0)
        expected = math.pi * 6371.0  # half circumference
        np.testing.assert_allclose(d, expected, rtol=1e-6)

    def test_north_pole_to_south_pole(self):
        """North pole to south pole is ~20015 km."""
        d = _haversine(90.0, 0.0, -90.0, 0.0)
        expected = math.pi * 6371.0
        np.testing.assert_allclose(d, expected, rtol=1e-6)

    def test_symmetry(self):
        """Distance A->B equals distance B->A."""
        d_ab = _haversine(51.5074, -0.1278, 48.8566, 2.3522)
        d_ba = _haversine(48.8566, 2.3522, 51.5074, -0.1278)
        np.testing.assert_allclose(d_ab, d_ba, rtol=1e-10)

    def test_equator_one_degree(self):
        """1 degree longitude on equator is ~111.19 km."""
        d = _haversine(0.0, 0.0, 0.0, 1.0)
        assert abs(d - 111.19) < 0.5

    def test_small_distance(self):
        """Short distance (within a city) is non-negative and small."""
        d = _haversine(51.5074, -0.1278, 51.5080, -0.1270)
        assert 0 < d < 1.0  # less than 1 km

    def test_returns_float(self):
        """Return type is float."""
        d = _haversine(0.0, 0.0, 1.0, 1.0)
        assert isinstance(d, float)


# ---------------------------------------------------------------------------
# _polygon_centroid
# ---------------------------------------------------------------------------


class TestPolygonCentroid:
    """Tests for _polygon_centroid (mean-of-vertices)."""

    def test_empty_polygon_returns_origin(self):
        """Empty polygon returns (0.0, 0.0)."""
        lat, lon = _polygon_centroid([])
        assert lat == 0.0
        assert lon == 0.0

    def test_single_point(self):
        """Single vertex returns that point itself."""
        pt = GeoCoordinate(latitude=22.5, longitude=-80.0)
        lat, lon = _polygon_centroid([pt])
        assert lat == 22.5
        assert lon == -80.0

    def test_simple_square(self):
        """Centroid of a unit square on the equator."""
        polygon = [
            GeoCoordinate(latitude=0.0, longitude=0.0),
            GeoCoordinate(latitude=1.0, longitude=0.0),
            GeoCoordinate(latitude=1.0, longitude=1.0),
            GeoCoordinate(latitude=0.0, longitude=1.0),
        ]
        lat, lon = _polygon_centroid(polygon)
        np.testing.assert_allclose(lat, 0.5)
        np.testing.assert_allclose(lon, 0.5)

    def test_triangle(self):
        """Centroid of a triangle is the mean of the three vertices."""
        polygon = [
            GeoCoordinate(latitude=0.0, longitude=0.0),
            GeoCoordinate(latitude=3.0, longitude=0.0),
            GeoCoordinate(latitude=0.0, longitude=3.0),
        ]
        lat, lon = _polygon_centroid(polygon)
        np.testing.assert_allclose(lat, 1.0)
        np.testing.assert_allclose(lon, 1.0)

    def test_two_points(self):
        """Centroid of two points is their midpoint."""
        polygon = [
            GeoCoordinate(latitude=10.0, longitude=20.0),
            GeoCoordinate(latitude=30.0, longitude=40.0),
        ]
        lat, lon = _polygon_centroid(polygon)
        np.testing.assert_allclose(lat, 20.0)
        np.testing.assert_allclose(lon, 30.0)

    def test_returns_tuple_of_floats(self):
        """Return type is a tuple of two floats."""
        pt = GeoCoordinate(latitude=5.0, longitude=10.0)
        result = _polygon_centroid([pt])
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)


# ---------------------------------------------------------------------------
# _find_nearest_bus
# ---------------------------------------------------------------------------


class TestFindNearestBus:
    """Tests for _find_nearest_bus."""

    def test_single_bus(self):
        """Single bus is always the nearest."""
        buses = [BusConfig(bus_id="bus_0", parent_node=0)]
        node_coords = [GeoCoordinate(latitude=23.0, longitude=-82.0)]
        centroid = (23.1, -82.1)

        bus_idx, parent_node, dist = _find_nearest_bus(centroid, buses, node_coords)
        assert bus_idx == 0
        assert parent_node == 0
        assert dist > 0

    def test_two_buses_returns_closest(self):
        """With two buses, the nearest one is returned."""
        buses = [
            BusConfig(bus_id="bus_0", parent_node=0),
            BusConfig(bus_id="bus_1", parent_node=1),
        ]
        node_coords = [
            GeoCoordinate(latitude=23.0, longitude=-82.0),  # node 0: Havana-ish
            GeoCoordinate(latitude=22.0, longitude=-79.0),  # node 1: Santiago-ish
        ]
        # Centroid close to node 1
        centroid = (22.1, -79.1)

        bus_idx, parent_node, dist = _find_nearest_bus(centroid, buses, node_coords)
        assert bus_idx == 1
        assert parent_node == 1

    def test_three_buses_different_nodes(self):
        """With three buses on different nodes, nearest wins."""
        buses = [
            BusConfig(bus_id="bus_0", parent_node=0),
            BusConfig(bus_id="bus_1", parent_node=1),
            BusConfig(bus_id="bus_2", parent_node=2),
        ]
        node_coords = [
            GeoCoordinate(latitude=0.0, longitude=0.0),
            GeoCoordinate(latitude=10.0, longitude=10.0),
            GeoCoordinate(latitude=1.0, longitude=1.0),  # nearest to (0.5, 0.5)
        ]
        centroid = (0.5, 0.5)

        bus_idx, parent_node, dist = _find_nearest_bus(centroid, buses, node_coords)
        assert bus_idx == 2
        assert parent_node == 2

    def test_multiple_buses_same_node(self):
        """Multiple buses on the same node all have same distance."""
        buses = [
            BusConfig(bus_id="bus_0", parent_node=0),
            BusConfig(bus_id="bus_1", parent_node=0),
        ]
        node_coords = [GeoCoordinate(latitude=10.0, longitude=20.0)]
        centroid = (10.5, 20.5)

        bus_idx, parent_node, dist = _find_nearest_bus(centroid, buses, node_coords)
        # First bus wins since they have the same distance
        assert bus_idx == 0
        assert parent_node == 0

    def test_centroid_on_bus_returns_zero_distance(self):
        """When centroid exactly matches bus location, distance is 0."""
        buses = [BusConfig(bus_id="bus_0", parent_node=0)]
        node_coords = [GeoCoordinate(latitude=22.0, longitude=-80.0)]
        centroid = (22.0, -80.0)

        _, _, dist = _find_nearest_bus(centroid, buses, node_coords)
        np.testing.assert_allclose(dist, 0.0, atol=1e-10)

    def test_returns_correct_tuple_format(self):
        """Return value is (bus_index, parent_node_index, distance_km)."""
        buses = [BusConfig(bus_id="bus_0", parent_node=0)]
        node_coords = [GeoCoordinate(latitude=0.0, longitude=0.0)]
        centroid = (1.0, 1.0)

        result = _find_nearest_bus(centroid, buses, node_coords)
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)
        assert isinstance(result[2], float)

    def test_parent_node_out_of_range_skipped(self):
        """Bus with parent_node >= len(node_coords) is effectively skipped."""
        buses = [
            BusConfig(bus_id="bus_0", parent_node=5),  # out of range
            BusConfig(bus_id="bus_1", parent_node=0),  # valid
        ]
        node_coords = [GeoCoordinate(latitude=10.0, longitude=20.0)]
        centroid = (10.5, 20.5)

        bus_idx, parent_node, dist = _find_nearest_bus(centroid, buses, node_coords)
        assert bus_idx == 1
        assert parent_node == 0


# ---------------------------------------------------------------------------
# _match_generators
# ---------------------------------------------------------------------------


class TestMatchGenerators:
    """Tests for _match_generators."""

    def test_explicit_allowed_generators(self):
        """When allowed_generators is set, only those keys are returned."""
        zone = _make_zone(allowed_generators=["solar_1", "wind_1"])
        generators = {
            "solar_1": _make_generator("Solar1", "Sun", 1),
            "solar_2": _make_generator("Solar2", "Sun", 1),
            "wind_1": _make_generator("Wind1", "Wind", 1, technology="Wind"),
        }
        result = _match_generators(zone, generators)
        assert sorted(result) == ["solar_1", "wind_1"]

    def test_allowed_generators_filters_nonexistent(self):
        """Allowed generators that do not exist in the dict are excluded."""
        zone = _make_zone(allowed_generators=["solar_1", "nonexistent"])
        generators = {
            "solar_1": _make_generator("Solar1", "Sun", 1),
        }
        result = _match_generators(zone, generators)
        assert result == ["solar_1"]

    def test_match_by_key_name(self):
        """Zone technology matched against generator key (case-insensitive)."""
        zone = _make_zone(technology="Solar")
        generators = {
            "solar_pv_1": _make_generator("SolarPV", "Sun", 1),
            "gas_turbine": _make_generator("Gas", "NaturalGas", 1, gen_type="Non-renewable"),
        }
        result = _match_generators(zone, generators)
        assert result == ["solar_pv_1"]

    def test_match_by_fuel(self):
        """Zone technology matched against generator fuel (case-insensitive)."""
        zone = _make_zone(technology="wind")
        generators = {
            "gen_0": _make_generator("Turbine", "Wind", 1, technology="Onshore"),
        }
        result = _match_generators(zone, generators)
        assert result == ["gen_0"]

    def test_match_by_technology_field(self):
        """Zone technology matched against generator.technology field."""
        zone = _make_zone(technology="onshore")
        generators = {
            "wind_farm": _make_generator("Farm", "Air", 1, technology="Onshore Wind"),
        }
        result = _match_generators(zone, generators)
        assert result == ["wind_farm"]

    def test_no_match_returns_empty(self):
        """No matching generator returns empty list."""
        zone = _make_zone(technology="Geothermal")
        generators = {
            "solar_1": _make_generator("Solar", "Sun", 1),
            "gas_1": _make_generator("Gas", "NaturalGas", 1, gen_type="Non-renewable"),
        }
        result = _match_generators(zone, generators)
        assert result == []

    def test_case_insensitive_matching(self):
        """Matching is case-insensitive."""
        zone = _make_zone(technology="SOLAR")
        generators = {
            "my_solar": _make_generator("PV", "sun", 1),
        }
        result = _match_generators(zone, generators)
        # "solar" in "my_solar" (key match)
        assert result == ["my_solar"]

    def test_multiple_matches(self):
        """Multiple generators can match a zone technology."""
        zone = _make_zone(technology="solar")
        generators = {
            "solar_1": _make_generator("Solar1", "Sun", 1),
            "solar_2": _make_generator("Solar2", "Sun", 1),
            "wind_1": _make_generator("Wind1", "Wind", 1),
        }
        result = _match_generators(zone, generators)
        assert sorted(result) == ["solar_1", "solar_2"]

    def test_empty_generators_dict(self):
        """Empty generators dict returns empty list."""
        zone = _make_zone(technology="Solar")
        result = _match_generators(zone, {})
        assert result == []


# ---------------------------------------------------------------------------
# _match_batteries
# ---------------------------------------------------------------------------


class TestMatchBatteries:
    """Tests for _match_batteries."""

    def test_battery_technology_matches_all(self):
        """Zone with technology='Battery' returns all battery keys."""
        zone = _make_zone(technology="Battery")
        batteries = {
            "bat_0": _make_battery("Bat0", 1),
            "bat_1": _make_battery("Bat1", 1),
        }
        result = _match_batteries(zone, batteries)
        assert sorted(result) == ["bat_0", "bat_1"]

    def test_storage_technology_matches_all(self):
        """Zone with technology='Storage' returns all battery keys."""
        zone = _make_zone(technology="Storage")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == ["bat_0"]

    def test_bess_technology_matches_all(self):
        """Zone with technology='BESS' (case-insensitive) returns all batteries."""
        zone = _make_zone(technology="BESS")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == ["bat_0"]

    def test_ess_technology_matches_all(self):
        """Zone with technology='ESS' returns all batteries."""
        zone = _make_zone(technology="ESS")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == ["bat_0"]

    def test_non_storage_technology_returns_empty(self):
        """Zone with non-storage technology returns empty list."""
        zone = _make_zone(technology="Solar")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == []

    def test_wind_technology_returns_empty(self):
        """Wind zone does not match batteries."""
        zone = _make_zone(technology="Wind")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == []

    def test_empty_batteries_dict(self):
        """Empty batteries dict returns empty list even for storage zone."""
        zone = _make_zone(technology="Battery")
        result = _match_batteries(zone, {})
        assert result == []

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        zone = _make_zone(technology="battery")
        batteries = {"bat_0": _make_battery("Bat0", 1)}
        result = _match_batteries(zone, batteries)
        assert result == ["bat_0"]


# ---------------------------------------------------------------------------
# ZoneMapping dataclass
# ---------------------------------------------------------------------------


class TestZoneMapping:
    """Tests for ZoneMapping dataclass."""

    def test_defaults(self):
        """Default factory fields initialize to empty lists."""
        zm = ZoneMapping(
            zone_name="Z1",
            technology="Solar",
            virtual_node_idx=2,
            virtual_bus_idx=3,
            nearest_bus_idx=0,
            nearest_bus_parent_node=0,
            distance_km=50.0,
            interconnection_cost_per_mw=125000.0,
        )
        assert zm.matched_generators == []
        assert zm.matched_batteries == []

    def test_with_matches(self):
        """Matched generators and batteries are stored correctly."""
        zm = ZoneMapping(
            zone_name="Z1",
            technology="Solar",
            virtual_node_idx=2,
            virtual_bus_idx=3,
            nearest_bus_idx=0,
            nearest_bus_parent_node=0,
            distance_km=50.0,
            interconnection_cost_per_mw=125000.0,
            matched_generators=["solar_1", "solar_2"],
            matched_batteries=["bat_0"],
        )
        assert zm.matched_generators == ["solar_1", "solar_2"]
        assert zm.matched_batteries == ["bat_0"]

    def test_fields_accessible(self):
        """All fields are accessible as attributes."""
        zm = ZoneMapping(
            zone_name="TestZone",
            technology="Wind",
            virtual_node_idx=5,
            virtual_bus_idx=6,
            nearest_bus_idx=1,
            nearest_bus_parent_node=1,
            distance_km=120.5,
            interconnection_cost_per_mw=230750.0,
        )
        assert zm.zone_name == "TestZone"
        assert zm.technology == "Wind"
        assert zm.virtual_node_idx == 5
        assert zm.virtual_bus_idx == 6
        assert zm.nearest_bus_idx == 1
        assert zm.nearest_bus_parent_node == 1
        np.testing.assert_allclose(zm.distance_km, 120.5)
        np.testing.assert_allclose(zm.interconnection_cost_per_mw, 230750.0)


# ---------------------------------------------------------------------------
# Field name lists
# ---------------------------------------------------------------------------


class TestFieldNameConstants:
    """Tests for _GEN_PER_NODE_FIELDS and _BAT_PER_NODE_FIELDS."""

    def test_gen_fields_is_list(self):
        assert isinstance(_GEN_PER_NODE_FIELDS, list)

    def test_bat_fields_is_list(self):
        assert isinstance(_BAT_PER_NODE_FIELDS, list)

    def test_gen_fields_contain_key_entries(self):
        """Generator fields include critical per-node array names."""
        for expected in ("rated_power", "invest_cost", "invest_max_power", "life_time"):
            assert expected in _GEN_PER_NODE_FIELDS

    def test_bat_fields_contain_key_entries(self):
        """Battery fields include storage-specific per-node array names."""
        for expected in (
            "rated_power", "invest_cost", "invest_cost_energy",
            "invest_max_power", "invest_max_capacity",
            "efficiency_charge", "efficiency_discharge",
            "soc_initial", "max_DoD", "capacity",
            "MaxChargePower", "MaxDischargePower",
        ):
            assert expected in _BAT_PER_NODE_FIELDS

    def test_bat_fields_superset_of_gen_common(self):
        """Battery fields include all the fields in gen fields that are common."""
        common = [
            "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
            "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
            "eff_at_rated", "eff_at_min", "inertia", "start_up_cost", "fuel_cost",
            "fixed_cost", "maintenance_cost", "invest_cost", "invest_max_power",
        ]
        for f in common:
            assert f in _GEN_PER_NODE_FIELDS, f"{f} missing from gen fields"
            assert f in _BAT_PER_NODE_FIELDS, f"{f} missing from bat fields"


# ---------------------------------------------------------------------------
# expand_config_with_zones
# ---------------------------------------------------------------------------


class TestExpandConfigWithZonesNoZones:
    """Tests for expand_config_with_zones when no zones are defined."""

    def test_no_zones_returns_original(self):
        """With no development zones, returns the original config unchanged."""
        cfg = _make_system_config(num_nodes=2)
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert result_cfg is cfg  # same object, not deepcopy
        assert mappings == []

    def test_empty_zones_list(self):
        """Explicit empty zones list returns unchanged config."""
        cfg = _make_system_config(num_nodes=2, zones=[])
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert result_cfg is cfg
        assert mappings == []

    def test_no_zones_preserves_num_nodes(self):
        """Node count unchanged when no zones."""
        cfg = _make_system_config(num_nodes=3)
        result_cfg, _ = expand_config_with_zones(cfg)
        assert result_cfg.nodes.num_nodes == 3


class TestExpandConfigWithZonesSingleZone:
    """Tests for expand_config_with_zones with a single development zone."""

    def test_num_nodes_increased_by_one(self):
        """One zone adds one virtual node."""
        zone = _make_zone(technology="Solar")
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": _make_generator("Solar", "Sun", 2)},
            zones=[zone],
        )
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert result_cfg.nodes.num_nodes == 3

    def test_one_mapping_returned(self):
        """One zone produces exactly one ZoneMapping."""
        zone = _make_zone(technology="Solar")
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": _make_generator("Solar", "Sun", 2)},
            zones=[zone],
        )
        _, mappings = expand_config_with_zones(cfg)
        assert len(mappings) == 1
        assert mappings[0].zone_name == "Zone_A"

    def test_virtual_node_idx(self):
        """Virtual node index = original num_nodes."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        _, mappings = expand_config_with_zones(cfg)
        assert mappings[0].virtual_node_idx == 2

    def test_virtual_bus_created(self):
        """A virtual bus is appended for the zone."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)
        # Original had 2 auto-created buses + 1 zone bus
        assert len(result_cfg.buses) == 3
        zone_bus = result_cfg.buses[-1]
        assert zone_bus.bus_id == "zone_bus_0"
        assert zone_bus.parent_node == mappings[0].virtual_node_idx
        assert zone_bus.demand_fraction == 0.0

    def test_adjacency_matrix_expanded(self):
        """Adjacency matrix grows from NxN to (N+1)x(N+1) with epsilon connections."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)

        new_n = result_cfg.nodes.num_nodes
        assert new_n == 3
        conn = np.array(result_cfg.nodes.nodes_connections).reshape(new_n, new_n)

        parent = mappings[0].nearest_bus_parent_node
        virt = mappings[0].virtual_node_idx
        # Epsilon connections set
        np.testing.assert_allclose(conn[virt, parent], 0.001)
        np.testing.assert_allclose(conn[parent, virt], 0.001)

    def test_node_coordinates_expanded(self):
        """Zone centroid added to node_coordinates."""
        zone = _make_zone(
            polygon_coords=[(10.0, 20.0), (12.0, 20.0), (12.0, 22.0), (10.0, 22.0)],
        )
        cfg = _make_system_config(
            num_nodes=1,
            coords=[(11.0, 21.0)],
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        assert len(result_cfg.nodes.node_coordinates) == 2
        zone_coord = result_cfg.nodes.node_coordinates[-1]
        np.testing.assert_allclose(zone_coord.latitude, 11.0)
        np.testing.assert_allclose(zone_coord.longitude, 21.0)

    def test_node_names_expanded(self):
        """Zone node name added as 'zone_{zone_name}'."""
        zone = _make_zone(name="MyZone")
        cfg = _make_system_config(num_nodes=1, coords=[(0.0, 0.0)], zones=[zone])
        result_cfg, _ = expand_config_with_zones(cfg)
        assert result_cfg.nodes.node_names[-1] == "zone_MyZone"

    def test_generator_arrays_expanded(self):
        """Generator per-node arrays grow by 1 per zone."""
        zone = _make_zone(technology="Solar")
        gen = _make_generator("Solar", "Sun", 2)
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": gen},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        gen_out = result_cfg.generators["solar_1"]
        for field_name in _GEN_PER_NODE_FIELDS:
            arr = getattr(gen_out, field_name)
            assert len(arr) == 3, f"Field {field_name} has length {len(arr)}, expected 3"

    def test_matched_generator_invest_max_power(self):
        """Matched generator gets zone max_capacity_mw as invest_max_power."""
        zone = _make_zone(technology="Solar", max_capacity_mw=500.0)
        gen = _make_generator("Solar", "Sun", 2)
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": gen},
            zones=[zone],
        )
        result_cfg, mappings = expand_config_with_zones(cfg)
        gen_out = result_cfg.generators["solar_1"]
        # Virtual node is index 2 (0-based)
        assert gen_out.invest_max_power[2] == 500.0

    def test_matched_generator_rated_power_zero(self):
        """Matched generator has zero rated_power on virtual node (nothing built yet)."""
        zone = _make_zone(technology="Solar", max_capacity_mw=500.0)
        gen = _make_generator("Solar", "Sun", 2, rated_power=100.0)
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": gen},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        gen_out = result_cfg.generators["solar_1"]
        assert gen_out.rated_power[2] == 0.0

    def test_unmatched_generator_invest_max_zero(self):
        """Unmatched generator gets zero invest_max_power on virtual node."""
        zone = _make_zone(technology="Solar")
        gas_gen = _make_generator("Gas", "NaturalGas", 2, gen_type="Non-renewable")
        cfg = _make_system_config(
            num_nodes=2,
            generators={"gas_1": gas_gen},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        gen_out = result_cfg.generators["gas_1"]
        assert gen_out.invest_max_power[2] == 0.0

    def test_battery_arrays_expanded(self):
        """Battery per-node arrays grow by 1 per zone."""
        zone = _make_zone(technology="Battery")
        bat = _make_battery("Bat0", 2)
        cfg = _make_system_config(
            num_nodes=2,
            batteries={"bat_0": bat},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        bat_out = result_cfg.batteries["bat_0"]
        for field_name in _BAT_PER_NODE_FIELDS:
            arr = getattr(bat_out, field_name)
            assert len(arr) == 3, f"Battery field {field_name} has length {len(arr)}, expected 3"

    def test_matched_battery_invest_max_power(self):
        """Matched battery gets zone max_capacity_mw as invest_max_power."""
        zone = _make_zone(technology="Storage", max_capacity_mw=200.0)
        bat = _make_battery("Bat0", 2)
        cfg = _make_system_config(
            num_nodes=2,
            batteries={"bat_0": bat},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        bat_out = result_cfg.batteries["bat_0"]
        assert bat_out.invest_max_power[2] == 200.0

    def test_interconnection_cost_formula(self):
        """Interconnection cost = line_cost * distance + transformer_cost."""
        zone = _make_zone(
            line_cost_per_mw_km=1000.0,
            transformer_cost_per_mw=30000.0,
            polygon_coords=[(23.5, -82.5), (24.0, -82.5), (24.0, -82.0), (23.5, -82.0)],
        )
        cfg = _make_system_config(
            num_nodes=1,
            coords=[(23.0, -82.0)],
            zones=[zone],
        )
        _, mappings = expand_config_with_zones(cfg)
        m = mappings[0]
        expected_cost = 1000.0 * m.distance_km + 30000.0
        np.testing.assert_allclose(m.interconnection_cost_per_mw, expected_cost)

    def test_reserve_arrays_expanded(self):
        """Per-node reserve arrays grow by the number of zones."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        result_cfg, _ = expand_config_with_zones(cfg)
        assert len(result_cfg.nodes.reserve_static) == 3
        assert len(result_cfg.nodes.reserve_dynamic) == 3
        assert len(result_cfg.nodes.reserve_duration) == 3
        assert len(result_cfg.nodes.losses) == 3
        # Virtual node reserves are zero
        assert result_cfg.nodes.reserve_static[-1] == 0.0
        assert result_cfg.nodes.reserve_dynamic[-1] == 0.0
        assert result_cfg.nodes.losses[-1] == 0.0

    def test_does_not_mutate_original(self):
        """expand_config_with_zones deepcopies the config."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        original_num_nodes = cfg.nodes.num_nodes
        result_cfg, _ = expand_config_with_zones(cfg)
        # Original unchanged
        assert cfg.nodes.num_nodes == original_num_nodes
        assert result_cfg.nodes.num_nodes == original_num_nodes + 1


class TestExpandConfigWithZonesMultipleZones:
    """Tests for expand_config_with_zones with multiple zones."""

    def test_two_zones_add_two_nodes(self):
        """Two zones add two virtual nodes."""
        zone_solar = _make_zone(name="Solar_Zone", technology="Solar")
        zone_wind = _make_zone(
            name="Wind_Zone",
            technology="Wind",
            polygon_coords=[(21.0, -78.0), (21.5, -78.0), (21.5, -77.5), (21.0, -77.5)],
        )
        gen_solar = _make_generator("Solar", "Sun", 2)
        gen_wind = _make_generator("Wind", "Wind", 2, technology="Wind")
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": gen_solar, "wind_1": gen_wind},
            zones=[zone_solar, zone_wind],
        )
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert result_cfg.nodes.num_nodes == 4
        assert len(mappings) == 2

    def test_two_zones_virtual_indices_sequential(self):
        """Virtual node indices are sequential from original num_nodes."""
        zone1 = _make_zone(name="Z1", technology="Solar")
        zone2 = _make_zone(
            name="Z2",
            technology="Wind",
            polygon_coords=[(21.0, -78.0), (21.5, -78.0), (21.5, -77.5), (21.0, -77.5)],
        )
        cfg = _make_system_config(num_nodes=2, zones=[zone1, zone2])
        _, mappings = expand_config_with_zones(cfg)
        assert mappings[0].virtual_node_idx == 2
        assert mappings[1].virtual_node_idx == 3

    def test_two_zones_two_virtual_buses(self):
        """Each zone gets its own virtual bus."""
        zone1 = _make_zone(name="Z1")
        zone2 = _make_zone(
            name="Z2",
            polygon_coords=[(21.0, -78.0), (21.5, -78.0), (21.5, -77.5), (21.0, -77.5)],
        )
        cfg = _make_system_config(num_nodes=2, zones=[zone1, zone2])
        result_cfg, _ = expand_config_with_zones(cfg)
        # 2 original auto-buses + 2 zone buses
        assert len(result_cfg.buses) == 4
        assert result_cfg.buses[-2].bus_id == "zone_bus_0"
        assert result_cfg.buses[-1].bus_id == "zone_bus_1"

    def test_generator_arrays_expanded_for_multiple_zones(self):
        """Generator arrays grow by number of zones."""
        zone1 = _make_zone(name="Z1", technology="Solar")
        zone2 = _make_zone(
            name="Z2",
            technology="Wind",
            polygon_coords=[(21.0, -78.0), (21.5, -78.0), (21.5, -77.5), (21.0, -77.5)],
        )
        gen = _make_generator("Solar", "Sun", 2)
        cfg = _make_system_config(
            num_nodes=2,
            generators={"solar_1": gen},
            zones=[zone1, zone2],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        gen_out = result_cfg.generators["solar_1"]
        # 2 original + 2 zones = 4
        for field_name in _GEN_PER_NODE_FIELDS:
            arr = getattr(gen_out, field_name)
            assert len(arr) == 4, f"Field {field_name} should have length 4, got {len(arr)}"


class TestExpandConfigWithZonesTargetBus:
    """Tests for expand_config_with_zones with explicit target_bus."""

    def test_target_bus_overrides_nearest(self):
        """When target_bus is specified, that bus is used instead of nearest."""
        zone = _make_zone(
            target_bus=1,
            polygon_coords=[(23.5, -82.5), (24.0, -82.5), (24.0, -82.0), (23.5, -82.0)],
        )
        cfg = _make_system_config(
            num_nodes=2,
            coords=[(23.0, -82.0), (22.0, -79.0)],
            zones=[zone],
        )
        _, mappings = expand_config_with_zones(cfg)
        assert mappings[0].nearest_bus_idx == 1
        assert mappings[0].nearest_bus_parent_node == 1

    def test_target_bus_distance_computed_correctly(self):
        """Distance is still computed from centroid to target bus node."""
        zone = _make_zone(
            target_bus=0,
            polygon_coords=[(23.0, -82.0), (23.0, -82.0)],  # centroid = node 0
        )
        cfg = _make_system_config(
            num_nodes=1,
            coords=[(23.0, -82.0)],
            zones=[zone],
        )
        _, mappings = expand_config_with_zones(cfg)
        np.testing.assert_allclose(mappings[0].distance_km, 0.0, atol=1e-10)


class TestExpandConfigWithZonesNoCoords:
    """Tests when node coordinates are missing."""

    def test_no_coords_defaults_to_bus_0(self):
        """Without node_coordinates, falls back to bus 0 with distance 0."""
        zone = _make_zone()
        cfg = _make_system_config(num_nodes=2, coords=None, zones=[zone])
        # Manually clear coordinates set by helper
        cfg.nodes.node_coordinates = None
        _, mappings = expand_config_with_zones(cfg)
        assert mappings[0].nearest_bus_idx == 0
        assert mappings[0].nearest_bus_parent_node == 0
        np.testing.assert_allclose(mappings[0].distance_km, 0.0)

    def test_no_coords_interconnection_cost_is_transformer_only(self):
        """With distance=0, interconnection cost = transformer cost only."""
        zone = _make_zone(
            line_cost_per_mw_km=1500.0,
            transformer_cost_per_mw=50000.0,
        )
        cfg = _make_system_config(num_nodes=1, coords=None, zones=[zone])
        cfg.nodes.node_coordinates = None
        _, mappings = expand_config_with_zones(cfg)
        np.testing.assert_allclose(mappings[0].interconnection_cost_per_mw, 50000.0)


class TestExpandConfigWithZonesTransferenceArrays:
    """Tests for transference_invest_cost and transference_invest_max expansion."""

    def test_transference_invest_cost_set_for_zone(self):
        """Virtual node gets interconnection_cost_per_mw in transference_invest_cost."""
        zone = _make_zone(
            line_cost_per_mw_km=1000.0,
            transformer_cost_per_mw=20000.0,
        )
        cfg = _make_system_config(num_nodes=1, coords=[(0.0, 0.0)], zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)
        virt_idx = mappings[0].virtual_node_idx
        expected_cost = mappings[0].interconnection_cost_per_mw
        np.testing.assert_allclose(
            result_cfg.nodes.transference_invest_cost[virt_idx], expected_cost,
        )

    def test_transference_invest_max_set_to_zone_capacity(self):
        """Virtual node gets max_capacity_mw in transference_invest_max."""
        zone = _make_zone(max_capacity_mw=750.0)
        cfg = _make_system_config(num_nodes=1, coords=[(0.0, 0.0)], zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)
        virt_idx = mappings[0].virtual_node_idx
        assert result_cfg.nodes.transference_invest_max[virt_idx] == 750.0

    def test_transference_invest_max_none_defaults_to_1e6(self):
        """When max_capacity_mw is None, defaults to 1e6."""
        zone = _make_zone(max_capacity_mw=None)
        cfg = _make_system_config(num_nodes=1, coords=[(0.0, 0.0)], zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)
        virt_idx = mappings[0].virtual_node_idx
        assert result_cfg.nodes.transference_invest_max[virt_idx] == 1e6


# ---------------------------------------------------------------------------
# _match_technologies
# ---------------------------------------------------------------------------


class TestMatchTechnologies:
    """Tests for _match_technologies function."""

    def test_explicit_allowed_technologies(self):
        """Matches only keys listed in allowed_technologies."""
        zone = _make_zone(technology="Solar", allowed_technologies={"tech_solar": 0.0})
        techs = {
            "tech_solar": _make_technology("Solar PV", "Solar", 1),
            "tech_wind": _make_technology("Wind", "Wind", 1),
        }
        assert _match_technologies(zone, techs) == ["tech_solar"]

    def test_fuzzy_match_by_key(self):
        """Fuzzy matches zone.technology against tech key."""
        zone = _make_zone(technology="Solar")
        techs = {
            "tech_solar": _make_technology("PV", "Photovoltaic", 1),
            "tech_wind": _make_technology("Wind Turbine", "Wind", 1),
        }
        assert _match_technologies(zone, techs) == ["tech_solar"]

    def test_fuzzy_match_by_name(self):
        """Fuzzy matches zone.technology against tech.name."""
        zone = _make_zone(technology="Solar")
        techs = {
            "pv_tech": _make_technology("Solar PV", "Photovoltaic", 1),
        }
        assert _match_technologies(zone, techs) == ["pv_tech"]

    def test_fuzzy_match_by_fuel(self):
        """Fuzzy matches zone.technology against tech.fuel."""
        zone = _make_zone(technology="Solar")
        techs = {
            "pv_tech": _make_technology("PV", "Solar", 1),
        }
        assert _match_technologies(zone, techs) == ["pv_tech"]

    def test_no_match_returns_empty(self):
        """Returns empty list when nothing matches."""
        zone = _make_zone(technology="Geothermal")
        techs = {
            "tech_solar": _make_technology("Solar PV", "Solar", 1),
        }
        assert _match_technologies(zone, techs) == []

    def test_empty_technologies_dict(self):
        """Handles empty technologies dict gracefully."""
        zone = _make_zone(technology="Solar")
        assert _match_technologies(zone, {}) == []


class TestMatchBatteryTechnologies:
    """Tests for _match_battery_technologies function."""

    def test_battery_zone_matches_all(self):
        """Battery zone matches all battery technologies."""
        zone = _make_zone(technology="Battery")
        bat_techs = {
            "bt_lion": _make_battery_technology("Li-ion", 1),
            "bt_flow": _make_battery_technology("Flow", 1),
        }
        result = _match_battery_technologies(zone, bat_techs)
        assert set(result) == {"bt_lion", "bt_flow"}

    def test_non_battery_zone_returns_empty(self):
        """Non-battery zone returns empty list."""
        zone = _make_zone(technology="Solar")
        bat_techs = {"bt_lion": _make_battery_technology("Li-ion", 1)}
        assert _match_battery_technologies(zone, bat_techs) == []

    def test_storage_alias(self):
        """'Storage' is recognized as battery-like."""
        zone = _make_zone(technology="Storage")
        bat_techs = {"bt_lion": _make_battery_technology("Li-ion", 1)}
        assert _match_battery_technologies(zone, bat_techs) == ["bt_lion"]

    def test_explicit_allowed_technologies(self):
        """Uses allowed_technologies when set on battery zone."""
        zone = _make_zone(
            technology="Battery",
            allowed_technologies={"bt_lion": 0.0},
        )
        bat_techs = {
            "bt_lion": _make_battery_technology("Li-ion", 1),
            "bt_flow": _make_battery_technology("Flow", 1),
        }
        assert _match_battery_technologies(zone, bat_techs) == ["bt_lion"]


# ---------------------------------------------------------------------------
# Technology zone expansion
# ---------------------------------------------------------------------------


class TestTechnologyZoneExpansion:
    """Tests for technology per-node array expansion in expand_config_with_zones."""

    def test_technology_arrays_expanded(self):
        """Technology per-node arrays grow by 1 per zone."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0)
        tech = _make_technology("Solar PV", "Solar", 2, invest_cost=900000.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, mappings = expand_config_with_zones(cfg)
        # Original 2 nodes + 1 zone = 3
        result_tech = result_cfg.technologies["tech_solar"]
        assert len(result_tech.invest_max_power) == 3
        assert len(result_tech.invest_cost) == 3

    def test_matched_tech_gets_zone_capacity(self):
        """Matched technology gets zone max_capacity_mw at zone node."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0)
        tech = _make_technology("Solar PV", "Solar", 2)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # Zone node (index 2) should get 300.0
        assert result_tech.invest_max_power[2] == 300.0

    def test_unmatched_tech_gets_zero(self):
        """Unmatched technology gets 0.0 invest_max_power at zone node."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0)
        tech = _make_technology("Wind Turbine", "Wind", 2)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_wind": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_wind"]
        assert result_tech.invest_max_power[2] == 0.0
        assert result_tech.invest_cost[2] == 0.0

    def test_matched_tech_copies_invest_cost(self):
        """Matched tech copies invest_cost from reference node."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0)
        tech = _make_technology("Solar PV", "Solar", 2, invest_cost=900000.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        assert result_tech.invest_cost[2] == 900000.0

    def test_original_nodes_unchanged_non_exclusive(self):
        """Non-exclusive zone does not modify original nodes."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0, exclusive=False)
        tech = _make_technology("Solar PV", "Solar", 2, invest_max_power=500.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # Original nodes unchanged
        assert result_tech.invest_max_power[0] == 500.0
        assert result_tech.invest_max_power[1] == 500.0

    def test_zone_mapping_includes_matched_technologies(self):
        """ZoneMapping records matched_technologies."""
        zone = _make_zone(technology="Solar")
        tech = _make_technology("Solar PV", "Solar", 2)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        _, mappings = expand_config_with_zones(cfg)
        assert mappings[0].matched_technologies == ["tech_solar"]

    def test_per_tech_max_invest_overrides_zone_capacity(self):
        """Per-technology max invest from allowed_technologies dict overrides zone max_capacity_mw."""
        zone = _make_zone(
            technology="Solar",
            max_capacity_mw=500.0,
            allowed_technologies={"tech_solar": 150.0},
        )
        tech = _make_technology("Solar PV", "Solar", 2, invest_max_power=1000.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # Per-tech limit (150) overrides zone capacity (500)
        assert result_tech.invest_max_power[2] == 150.0

    def test_per_tech_zero_means_unlimited(self):
        """Per-technology max invest 0.0 means unlimited (falls back to zone capacity)."""
        zone = _make_zone(
            technology="Solar",
            max_capacity_mw=500.0,
            allowed_technologies={"tech_solar": 0.0},
        )
        tech = _make_technology("Solar PV", "Solar", 2)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # 0.0 = unlimited → falls back to zone max_capacity_mw
        assert result_tech.invest_max_power[2] == 500.0

    def test_no_technologies_no_error(self):
        """Expansion works when technologies dict is empty."""
        zone = _make_zone(technology="Solar")
        cfg = _make_system_config(num_nodes=2, zones=[zone])
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert result_cfg.nodes.num_nodes == 3
        assert len(mappings) == 1


class TestBatteryTechnologyZoneExpansion:
    """Tests for battery technology per-node array expansion."""

    def test_battery_tech_arrays_expanded(self):
        """Battery technology per-node arrays grow by 1 per zone."""
        zone = _make_zone(technology="Battery", max_capacity_mw=200.0)
        bt = _make_battery_technology("Li-ion", 2)
        cfg = _make_system_config(
            num_nodes=2,
            battery_technologies={"bt_lion": bt},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_bt = result_cfg.battery_technologies["bt_lion"]
        assert len(result_bt.invest_max_power) == 3
        assert result_bt.invest_max_power[2] == 200.0

    def test_non_battery_zone_gives_zero(self):
        """Solar zone gives 0 invest_max_power to battery technologies."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0)
        bt = _make_battery_technology("Li-ion", 2)
        cfg = _make_system_config(
            num_nodes=2,
            battery_technologies={"bt_lion": bt},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_bt = result_cfg.battery_technologies["bt_lion"]
        assert result_bt.invest_max_power[2] == 0.0
        assert result_bt.invest_max_capacity[2] == 0.0


# ---------------------------------------------------------------------------
# Exclusive mode
# ---------------------------------------------------------------------------


class TestExclusiveMode:
    """Tests for exclusive zone mode (invest_max zeroed on original nodes)."""

    def test_exclusive_zeros_original_nodes(self):
        """Exclusive zone sets invest_max_power to 0 on all original nodes."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0, exclusive=True)
        tech = _make_technology("Solar PV", "Solar", 2, invest_max_power=500.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # Original nodes zeroed
        assert result_tech.invest_max_power[0] == 0.0
        assert result_tech.invest_max_power[1] == 0.0
        # Zone node has capacity
        assert result_tech.invest_max_power[2] == 300.0

    def test_exclusive_does_not_affect_unmatched_tech(self):
        """Exclusive Solar zone does not zero Wind technology."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0, exclusive=True)
        solar = _make_technology("Solar PV", "Solar", 2, invest_max_power=500.0)
        wind = _make_technology("Wind", "Wind", 2, invest_max_power=200.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": solar, "tech_wind": wind},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        # Solar zeroed on original nodes
        assert result_cfg.technologies["tech_solar"].invest_max_power[0] == 0.0
        # Wind unchanged on original nodes
        assert result_cfg.technologies["tech_wind"].invest_max_power[0] == 200.0
        assert result_cfg.technologies["tech_wind"].invest_max_power[1] == 200.0

    def test_non_exclusive_preserves_original_nodes(self):
        """Non-exclusive zone preserves original invest_max_power."""
        zone = _make_zone(technology="Solar", max_capacity_mw=300.0, exclusive=False)
        tech = _make_technology("Solar PV", "Solar", 2, invest_max_power=500.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        assert result_tech.invest_max_power[0] == 500.0
        assert result_tech.invest_max_power[1] == 500.0
        assert result_tech.invest_max_power[2] == 300.0

    def test_exclusive_battery_technology(self):
        """Exclusive Battery zone zeros battery tech on original nodes."""
        zone = _make_zone(technology="Battery", max_capacity_mw=100.0, exclusive=True)
        bt = _make_battery_technology("Li-ion", 2, invest_max_power=200.0, invest_max_capacity=800.0)
        cfg = _make_system_config(
            num_nodes=2,
            battery_technologies={"bt_lion": bt},
            zones=[zone],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_bt = result_cfg.battery_technologies["bt_lion"]
        # Original nodes zeroed
        assert result_bt.invest_max_power[0] == 0.0
        assert result_bt.invest_max_power[1] == 0.0
        assert result_bt.invest_max_capacity[0] == 0.0
        assert result_bt.invest_max_capacity[1] == 0.0
        # Zone node has capacity
        assert result_bt.invest_max_power[2] == 100.0

    def test_two_exclusive_zones_same_tech(self):
        """Two exclusive zones for same tech: both zone nodes get capacity."""
        zone_a = _make_zone(
            name="Zone_A", technology="Solar",
            max_capacity_mw=300.0, exclusive=True,
            polygon_coords=[(22.0, -80.0), (22.5, -80.0), (22.5, -79.5), (22.0, -79.5)],
        )
        zone_b = _make_zone(
            name="Zone_B", technology="Solar",
            max_capacity_mw=200.0, exclusive=True,
            polygon_coords=[(23.0, -81.0), (23.5, -81.0), (23.5, -80.5), (23.0, -80.5)],
        )
        tech = _make_technology("Solar PV", "Solar", 2, invest_max_power=500.0)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[zone_a, zone_b],
        )
        result_cfg, _ = expand_config_with_zones(cfg)
        result_tech = result_cfg.technologies["tech_solar"]
        # 2 original + 2 zones = 4 entries
        assert len(result_tech.invest_max_power) == 4
        # Original nodes zeroed
        assert result_tech.invest_max_power[0] == 0.0
        assert result_tech.invest_max_power[1] == 0.0
        # Zone nodes have their capacities
        assert result_tech.invest_max_power[2] == 300.0
        assert result_tech.invest_max_power[3] == 200.0


# ---------------------------------------------------------------------------
# Fallback behavior (no zones)
# ---------------------------------------------------------------------------


class TestNoZonesFallback:
    """Verify that no zones = no changes."""

    def test_no_zones_returns_original_config(self):
        """Empty development_zones returns original config unchanged."""
        tech = _make_technology("Solar PV", "Solar", 2)
        cfg = _make_system_config(
            num_nodes=2,
            technologies={"tech_solar": tech},
            zones=[],
        )
        result_cfg, mappings = expand_config_with_zones(cfg)
        assert mappings == []
        assert result_cfg.nodes.num_nodes == 2
        assert len(result_cfg.technologies["tech_solar"].invest_max_power) == 2
