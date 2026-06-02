"""Coverage tests for esfex.zones (development zone preprocessor).

These tests construct minimal pydantic configs and exercise the helper
functions plus the full ``expand_config_with_zones`` pipeline, asserting on
behavior observed by reading src/esfex/zones.py.
"""

from __future__ import annotations

import math

import pytest

from esfex import zones
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


# ---------------------------------------------------------------------------
# Builders for minimal valid configs
# ---------------------------------------------------------------------------

def make_generator(name="g", fuel="Solar", technology=None, n=2):
    return GeneratorConfig(
        name=name,
        type="Renewable",
        fuel=fuel,
        technology=technology,
        life_time=[25] * n,
        initial_age=[0] * n,
        degradation_rate=[0.01] * n,
        decommissioning_cost=[100.0] * n,
        rated_power=[10.0] * n,
        min_power=[0.0] * n,
        min_up=[1] * n,
        min_down=[1] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.9] * n,
        eff_at_min=[0.8] * n,
        inertia=[2.0] * n,
        start_up_cost=[50.0] * n,
        fuel_cost=[5.0] * n,
        fixed_cost=[1.0] * n,
        maintenance_cost=[2.0] * n,
        invest_cost=[1000.0] * n,
        invest_max_power=[500.0] * n,
    )


def make_battery(name="b", n=2):
    return BatteryConfig(
        name=name,
        life_time=[15] * n,
        initial_age=[0] * n,
        degradation_rate=[0.01] * n,
        decommissioning_cost=[100.0] * n,
        rated_power=[5.0] * n,
        min_power=[0.0] * n,
        min_up=[1] * n,
        min_down=[1] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.95] * n,
        eff_at_min=[0.9] * n,
        inertia=[0.0] * n,
        start_up_cost=[0.0] * n,
        fuel_cost=[0.0] * n,
        fixed_cost=[1.0] * n,
        maintenance_cost=[1.0] * n,
        invest_cost=[800.0] * n,
        invest_cost_energy=[200.0] * n,
        invest_max_power=[300.0] * n,
        invest_max_capacity=[600.0] * n,
        efficiency_charge=[0.95] * n,
        efficiency_discharge=[0.95] * n,
        soc_initial=[0.5] * n,
        max_DoD=[0.9] * n,
        capacity=[20.0] * n,
        MaxChargePower=[5.0] * n,
        MaxDischargePower=[5.0] * n,
    )


def make_technology(name="t", fuel="Solar", n=2):
    return TechnologyConfig(
        name=name,
        type="Renewable",
        fuel=fuel,
        invest_cost=[1200.0] * n,
        invest_max_power=[1000.0] * n,
        eff_at_rated=[0.9] * n,
        degradation_rate=[0.01] * n,
        lifetime=25,
        min_output=[0.0] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        fuel_cost=[0.0] * n,
        fixed_cost=[1.0] * n,
        maintenance_cost=[1.0] * n,
        inertia=[0.0] * n,
        start_up_cost=[0.0] * n,
        eff_at_min=[0.0] * n,
        decommissioning_cost=[50.0] * n,
    )


def make_battery_technology(name="bt", n=2):
    return BatteryTechnologyConfig(
        name=name,
        invest_cost_power=[700.0] * n,
        invest_cost_energy=[150.0] * n,
        invest_max_power=[900.0] * n,
        invest_max_capacity=[1800.0] * n,
        efficiency_charge=[0.95] * n,
        efficiency_discharge=[0.95] * n,
        degradation_rate=[0.01] * n,
        lifetime=15,
        soc_initial=[0.5] * n,
        max_DoD=[0.9] * n,
        maintenance_cost=[1.0] * n,
        inertia=[0.0] * n,
        throughput_degradation_cost=[1.0] * n,
        decommissioning_cost=[40.0] * n,
    )


def make_nodes(n=2, coords=True):
    # simple chain connection matrix
    conn = [0.0] * (n * n)
    for i in range(n - 1):
        conn[i * n + (i + 1)] = 100.0
        conn[(i + 1) * n + i] = 100.0
    node_coords = None
    if coords:
        node_coords = [
            GeoCoordinate(latitude=float(i), longitude=float(i))
            for i in range(n)
        ]
    return NodeConfig(
        num_nodes=n,
        nodes_connections=conn,
        node_coordinates=node_coords,
    )


def make_zone(name="ZoneA", technology="Solar", **kwargs):
    polygon = kwargs.pop(
        "polygon",
        [
            GeoCoordinate(latitude=0.0, longitude=0.0),
            GeoCoordinate(latitude=0.0, longitude=2.0),
            GeoCoordinate(latitude=2.0, longitude=2.0),
        ],
    )
    return DevelopmentZoneConfig(
        name=name, technology=technology, polygon=polygon, **kwargs
    )


def make_system(zones_list=None, n=2, **kwargs):
    return SystemConfig(
        name="sys",
        nodes=make_nodes(n),
        development_zones=zones_list or [],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _haversine
# ---------------------------------------------------------------------------

def test_haversine_zero_distance():
    assert zones._haversine(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0)


def test_haversine_known_distance():
    # 1 degree of latitude ~ 111.19 km
    d = zones._haversine(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111.19, abs=0.5)


def test_haversine_symmetric():
    a = zones._haversine(40.0, -3.0, 41.0, -4.0)
    b = zones._haversine(41.0, -4.0, 40.0, -3.0)
    assert a == pytest.approx(b)


# ---------------------------------------------------------------------------
# _polygon_centroid
# ---------------------------------------------------------------------------

def test_polygon_centroid_empty():
    assert zones._polygon_centroid([]) == (0.0, 0.0)


def test_polygon_centroid_mean():
    poly = [
        GeoCoordinate(latitude=0.0, longitude=0.0),
        GeoCoordinate(latitude=2.0, longitude=4.0),
        GeoCoordinate(latitude=4.0, longitude=8.0),
    ]
    lat, lon = zones._polygon_centroid(poly)
    assert lat == pytest.approx(2.0)
    assert lon == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# _find_nearest_bus
# ---------------------------------------------------------------------------

def test_find_nearest_bus_picks_closest():
    buses = [
        BusConfig(bus_id="b0", parent_node=0, demand_fraction=1.0),
        BusConfig(bus_id="b1", parent_node=1, demand_fraction=1.0),
    ]
    coords = [
        GeoCoordinate(latitude=0.0, longitude=0.0),
        GeoCoordinate(latitude=10.0, longitude=10.0),
    ]
    idx, node, dist = zones._find_nearest_bus((0.1, 0.1), buses, coords)
    assert idx == 0
    assert node == 0
    assert dist > 0.0


def test_find_nearest_bus_skips_out_of_range_parent():
    # parent_node beyond node_coords length is skipped; best stays defaults
    buses = [BusConfig(bus_id="b5", parent_node=5, demand_fraction=1.0)]
    coords = [GeoCoordinate(latitude=0.0, longitude=0.0)]
    idx, node, dist = zones._find_nearest_bus((1.0, 1.0), buses, coords)
    assert idx == 0
    assert node == 0
    assert dist == float("inf")


# ---------------------------------------------------------------------------
# _match_generators
# ---------------------------------------------------------------------------

def test_match_generators_allowed_filters_to_existing():
    zone = make_zone(technology="Solar")
    zone.allowed_generators = ["g_solar", "g_missing"]
    gens = {"g_solar": make_generator(name="g_solar", fuel="Coal")}
    assert zones._match_generators(zone, gens) == ["g_solar"]


def test_match_generators_fuzzy_by_key():
    zone = make_zone(technology="solar")
    gens = {"SolarFarm": make_generator(name="SolarFarm", fuel="Coal")}
    assert zones._match_generators(zone, gens) == ["SolarFarm"]


def test_match_generators_fuzzy_by_fuel():
    zone = make_zone(technology="wind")
    gens = {"plant1": make_generator(name="plant1", fuel="Wind")}
    assert zones._match_generators(zone, gens) == ["plant1"]


def test_match_generators_fuzzy_by_technology_field():
    zone = make_zone(technology="pv")
    gens = {"u1": make_generator(name="u1", fuel="Coal", technology="PV-array")}
    assert zones._match_generators(zone, gens) == ["u1"]


def test_match_generators_no_match():
    zone = make_zone(technology="geothermal")
    gens = {"u1": make_generator(name="u1", fuel="Coal", technology=None)}
    assert zones._match_generators(zone, gens) == []


# ---------------------------------------------------------------------------
# _match_batteries
# ---------------------------------------------------------------------------

def test_match_batteries_battery_zone_matches_all():
    zone = make_zone(technology="Battery")
    bats = {"b1": make_battery("b1"), "b2": make_battery("b2")}
    assert set(zones._match_batteries(zone, bats)) == {"b1", "b2"}


def test_match_batteries_non_storage_zone_empty():
    zone = make_zone(technology="Solar")
    bats = {"b1": make_battery("b1")}
    assert zones._match_batteries(zone, bats) == []


@pytest.mark.parametrize("tech", ["battery", "storage", "bess", "ess"])
def test_match_batteries_synonyms(tech):
    zone = make_zone(technology=tech)
    bats = {"b1": make_battery("b1")}
    assert zones._match_batteries(zone, bats) == ["b1"]


# ---------------------------------------------------------------------------
# _match_technologies
# ---------------------------------------------------------------------------

def test_match_technologies_allowed_dict():
    zone = make_zone(technology="Solar")
    zone.allowed_technologies = {"PV": 100.0, "absent": 0.0}
    techs = {"PV": make_technology("PV-tech", fuel="Solar")}
    assert zones._match_technologies(zone, techs) == ["PV"]


def test_match_technologies_fuzzy_by_name():
    zone = make_zone(technology="wind")
    techs = {"t1": make_technology(name="Onshore Wind", fuel="Air")}
    assert zones._match_technologies(zone, techs) == ["t1"]


def test_match_technologies_fuzzy_by_fuel():
    zone = make_zone(technology="solar")
    techs = {"t1": make_technology(name="Generic", fuel="Solar")}
    assert zones._match_technologies(zone, techs) == ["t1"]


def test_match_technologies_no_match():
    zone = make_zone(technology="nuclear")
    techs = {"t1": make_technology(name="Generic", fuel="Solar")}
    assert zones._match_technologies(zone, techs) == []


# ---------------------------------------------------------------------------
# _match_battery_technologies
# ---------------------------------------------------------------------------

def test_match_battery_technologies_non_battery_zone_empty():
    zone = make_zone(technology="Solar")
    bts = {"bt1": make_battery_technology("bt1")}
    assert zones._match_battery_technologies(zone, bts) == []


def test_match_battery_technologies_all_when_battery():
    zone = make_zone(technology="storage")
    bts = {"bt1": make_battery_technology("bt1"), "bt2": make_battery_technology("bt2")}
    assert set(zones._match_battery_technologies(zone, bts)) == {"bt1", "bt2"}


def test_match_battery_technologies_allowed_filter():
    zone = make_zone(technology="battery")
    zone.allowed_technologies = {"bt1": 50.0, "absent": 0.0}
    bts = {"bt1": make_battery_technology("bt1")}
    assert zones._match_battery_technologies(zone, bts) == ["bt1"]


# ---------------------------------------------------------------------------
# expand_config_with_zones — no-zone short circuit
# ---------------------------------------------------------------------------

def test_expand_no_zones_returns_same_object():
    cfg = make_system(zones_list=[])
    out_cfg, mappings = zones.expand_config_with_zones(cfg)
    assert out_cfg is cfg
    assert mappings == []


# ---------------------------------------------------------------------------
# expand_config_with_zones — full pipeline
# ---------------------------------------------------------------------------

def test_expand_basic_node_growth_and_mapping():
    zone = make_zone(name="Z1", technology="Solar", max_capacity_mw=400.0)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.generators = {"g_solar": make_generator(name="g_solar", fuel="Solar")}

    out, mappings = zones.expand_config_with_zones(cfg)

    # original config not mutated (deepcopy used)
    assert cfg.nodes.num_nodes == 2
    assert out.nodes.num_nodes == 3

    assert len(mappings) == 1
    m = mappings[0]
    assert m.zone_name == "Z1"
    assert m.technology == "Solar"
    assert m.virtual_node_idx == 2  # num_nodes_orig + 0
    assert m.virtual_bus_idx == 2   # num_buses_orig (auto 2) + 0
    assert "g_solar" in m.matched_generators

    # node names appended
    assert out.nodes.node_names[-1] == "zone_Z1"
    # node coordinates appended (centroid of polygon)
    assert len(out.nodes.node_coordinates) == 3
    # adjacency expanded to (3x3) flattened = 9
    assert len(out.nodes.nodes_connections) == 9


def test_expand_epsilon_connection_added():
    zone = make_zone(name="Z1", technology="Solar")
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    n = out.nodes.num_nodes
    conn = out.nodes.nodes_connections
    vi = m.virtual_node_idx
    pn = m.nearest_bus_parent_node
    assert conn[vi * n + pn] == pytest.approx(0.001)
    assert conn[pn * n + vi] == pytest.approx(0.001)


def test_expand_interconnection_cost_formula():
    zone = make_zone(
        name="Z1", technology="Solar",
        line_cost_per_mw_km=10.0, transformer_cost_per_mw=1000.0,
    )
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    expected = 10.0 * m.distance_km + 1000.0
    assert m.interconnection_cost_per_mw == pytest.approx(expected)
    # encoded into transference_invest_cost at virtual node
    assert out.nodes.transference_invest_cost[m.virtual_node_idx] == pytest.approx(
        expected
    )


def test_expand_transference_invest_max_from_capacity():
    zone = make_zone(name="Z1", technology="Solar", max_capacity_mw=250.0)
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    assert out.nodes.transference_invest_max[m.virtual_node_idx] == pytest.approx(
        250.0
    )


def test_expand_transference_invest_max_default_when_none():
    zone = make_zone(name="Z1", technology="Solar", max_capacity_mw=None)
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    assert out.nodes.transference_invest_max[m.virtual_node_idx] == pytest.approx(1e6)


def test_expand_target_bus_override():
    zone = make_zone(name="Z1", technology="Solar", target_bus=1)
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    # target_bus=1 -> bus index 1, whose parent_node is 1 (auto bus per node)
    assert m.nearest_bus_idx == 1
    assert m.nearest_bus_parent_node == 1


def test_expand_no_node_coords_distance_zero():
    zone = make_zone(name="Z1", technology="Solar")
    cfg = SystemConfig(
        name="sys",
        nodes=make_nodes(2, coords=False),
        development_zones=[zone],
    )
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    assert m.distance_km == 0.0
    assert m.nearest_bus_idx == 0
    assert m.nearest_bus_parent_node == 0


def test_expand_generator_arrays_grow_and_matched_invest():
    zone = make_zone(name="Z1", technology="Solar", max_capacity_mw=400.0)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.generators = {
        "g_solar": make_generator(name="g_solar", fuel="Solar"),
        "g_coal": make_generator(name="g_coal", fuel="Coal"),
    }
    out, mappings = zones.expand_config_with_zones(cfg)

    g_solar = out.generators["g_solar"]
    g_coal = out.generators["g_coal"]
    # arrays grew from 2 -> 3
    assert len(g_solar.rated_power) == 3
    assert len(g_coal.rated_power) == 3
    # matched generator: invest_max_power at zone = max_capacity_mw
    assert g_solar.invest_max_power[2] == pytest.approx(400.0)
    # non-matched: invest_max_power zero
    assert g_coal.invest_max_power[2] == pytest.approx(0.0)
    # rated_power forced to 0 (nothing built)
    assert g_solar.rated_power[2] == pytest.approx(0.0)
    # matched invest_cost copied from reference node
    assert g_solar.invest_cost[2] == pytest.approx(1000.0)
    # non-matched invest_cost zeroed
    assert g_coal.invest_cost[2] == pytest.approx(0.0)
    # non-matched life_time defaults to int reference value (25)
    assert g_coal.life_time[2] == 25


def test_expand_battery_arrays_for_storage_zone():
    zone = make_zone(name="ZB", technology="Battery", max_capacity_mw=300.0)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.batteries = {"b1": make_battery("b1")}
    out, mappings = zones.expand_config_with_zones(cfg)
    b = out.batteries["b1"]
    assert len(b.rated_power) == 3
    # matched -> invest_max_power = max_capacity_mw
    assert b.invest_max_power[2] == pytest.approx(300.0)
    # capacity/charge powers forced to 0
    assert b.capacity[2] == pytest.approx(0.0)
    assert b.MaxChargePower[2] == pytest.approx(0.0)
    assert b.MaxDischargePower[2] == pytest.approx(0.0)
    # matched invest_cost_energy copied from reference
    assert b.invest_cost_energy[2] == pytest.approx(200.0)


def test_expand_technology_arrays_and_max_from_allowed_dict():
    zone = make_zone(name="ZT", technology="Solar")
    zone.allowed_technologies = {"PV": 123.0}
    cfg = make_system(zones_list=[zone], n=2)
    cfg.technologies = {"PV": make_technology(name="PV-tech", fuel="Solar")}
    out, mappings = zones.expand_config_with_zones(cfg)
    t = out.technologies["PV"]
    assert len(t.invest_cost) == 3
    # allowed dict cap (>0) is used
    assert t.invest_max_power[2] == pytest.approx(123.0)
    # matched invest_cost copied from reference node
    assert t.invest_cost[2] == pytest.approx(1200.0)


def test_expand_technology_max_falls_back_to_capacity_when_allowed_zero():
    zone = make_zone(name="ZT", technology="Solar", max_capacity_mw=77.0)
    zone.allowed_technologies = {"PV": 0.0}  # 0 -> fall back to max_capacity_mw
    cfg = make_system(zones_list=[zone], n=2)
    cfg.technologies = {"PV": make_technology(name="PV-tech", fuel="Solar")}
    out, mappings = zones.expand_config_with_zones(cfg)
    t = out.technologies["PV"]
    assert t.invest_max_power[2] == pytest.approx(77.0)


def test_expand_technology_max_default_1e6_when_no_caps():
    # no allowed_technologies, fuzzy match by fuel, no max_capacity_mw
    zone = make_zone(name="ZT", technology="Solar", max_capacity_mw=None)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.technologies = {"PV": make_technology(name="PV-tech", fuel="Solar")}
    out, mappings = zones.expand_config_with_zones(cfg)
    t = out.technologies["PV"]
    assert t.invest_max_power[2] == pytest.approx(1e6)


def test_expand_battery_technology_arrays():
    zone = make_zone(name="ZBT", technology="storage", max_capacity_mw=500.0)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.battery_technologies = {"bt1": make_battery_technology("bt1")}
    out, mappings = zones.expand_config_with_zones(cfg)
    bt = out.battery_technologies["bt1"]
    assert len(bt.invest_cost_power) == 3
    assert bt.invest_max_power[2] == pytest.approx(500.0)
    # matched invest_cost_power copied from reference node
    assert bt.invest_cost_power[2] == pytest.approx(700.0)


def test_expand_exclusive_zeros_original_nodes():
    zone = make_zone(name="ZX", technology="Solar", exclusive=True)
    zone.allowed_technologies = {"PV": 100.0}
    cfg = make_system(zones_list=[zone], n=2)
    cfg.technologies = {"PV": make_technology(name="PV-tech", fuel="Solar")}
    out, mappings = zones.expand_config_with_zones(cfg)
    t = out.technologies["PV"]
    # original nodes 0 and 1 zeroed
    assert t.invest_max_power[0] == pytest.approx(0.0)
    assert t.invest_max_power[1] == pytest.approx(0.0)
    # zone node keeps its cap
    assert t.invest_max_power[2] == pytest.approx(100.0)


def test_expand_exclusive_zeros_battery_tech_nodes():
    zone = make_zone(name="ZBX", technology="storage", exclusive=True)
    cfg = make_system(zones_list=[zone], n=2)
    cfg.battery_technologies = {"bt1": make_battery_technology("bt1")}
    out, mappings = zones.expand_config_with_zones(cfg)
    bt = out.battery_technologies["bt1"]
    assert bt.invest_max_power[0] == pytest.approx(0.0)
    assert bt.invest_max_power[1] == pytest.approx(0.0)
    assert bt.invest_max_capacity[0] == pytest.approx(0.0)
    assert bt.invest_max_capacity[1] == pytest.approx(0.0)


def test_expand_virtual_buses_created():
    zone = make_zone(name="Z1", technology="Solar")
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    # buses auto-created (2) + 1 virtual bus
    assert len(out.buses) == 3
    vbus = out.buses[-1]
    assert vbus.bus_id == "zone_bus_0"
    assert vbus.parent_node == mappings[0].virtual_node_idx
    assert vbus.demand_fraction == 0.0


def test_expand_node_per_node_arrays_padded():
    zone = make_zone(name="Z1", technology="Solar")
    cfg = make_system(zones_list=[zone], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    assert len(out.nodes.reserve_static) == 3
    assert len(out.nodes.reserve_dynamic) == 3
    assert len(out.nodes.reserve_duration) == 3
    assert len(out.nodes.losses) == 3
    assert out.nodes.reserve_static[-1] == pytest.approx(0.0)
    assert out.nodes.reserve_duration[-1] == 1


def test_expand_fuel_transport_distances_expanded():
    zone = make_zone(name="Z1", technology="Solar")
    cfg = make_system(zones_list=[zone], n=2)
    cfg.fuel_transport_distances = [[0.0, 5.0], [5.0, 0.0]]
    out, mappings = zones.expand_config_with_zones(cfg)
    ftd = out.fuel_transport_distances
    assert len(ftd) == 3
    assert all(len(row) == 3 for row in ftd)
    # original block preserved
    assert ftd[0][1] == pytest.approx(5.0)
    # zone rows/cols zero
    assert ftd[2][0] == pytest.approx(0.0)
    assert ftd[0][2] == pytest.approx(0.0)


def test_expand_multiple_zones_indices():
    z1 = make_zone(name="Z1", technology="Solar")
    z2 = make_zone(name="Z2", technology="Wind")
    cfg = make_system(zones_list=[z1, z2], n=2)
    out, mappings = zones.expand_config_with_zones(cfg)
    assert out.nodes.num_nodes == 4
    assert mappings[0].virtual_node_idx == 2
    assert mappings[1].virtual_node_idx == 3
    assert mappings[0].virtual_bus_idx == 2
    assert mappings[1].virtual_bus_idx == 3
    assert out.nodes.node_names[-2:] == ["zone_Z1", "zone_Z2"]


def test_expand_existing_buses_used_when_present():
    zone = make_zone(name="Z1", technology="Solar", target_bus=0)
    cfg = make_system(zones_list=[zone], n=2)
    # define explicit buses (3 buses, all parent_node 0/1)
    cfg.buses = [
        BusConfig(bus_id="x0", parent_node=0, demand_fraction=1.0),
        BusConfig(bus_id="x1", parent_node=1, demand_fraction=1.0),
        BusConfig(bus_id="x2", parent_node=1, role="connection", demand_fraction=0.0),
    ]
    out, mappings = zones.expand_config_with_zones(cfg)
    m = mappings[0]
    # virtual bus index = num_buses_orig (3) + 0
    assert m.virtual_bus_idx == 3
    # one virtual bus appended -> 4
    assert len(out.buses) == 4
