"""Additive coverage tests for esfex.visualization.data.geo_asset_parser.

Focuses on the branches not exercised by tests/test_geo_asset_parser.py:
the ensure/find helpers, the centroid/nearest-node logic, and the full
``apply_assignments`` pipeline (points, lines, polygons) including all
target-type branches, snapping, voltage filtering, warnings and error paths.
"""

import math

import pytest

# parse_geo_asset_dialog imports PySide6 at module level; skip cleanly if
# PySide6 cannot be imported in this environment.
pytest.importorskip("PySide6")

from esfex.visualization.data.geo_asset_parser import (  # noqa: E402
    ParseResult,
    _compute_node_centroids,
    _ensure_bus_at,
    _ensure_fuel_entry_at,
    _find_nearest_bus,
    _find_nearest_fuel_point,
    _find_nearest_node,
    _find_nearest_node_idx,
    _haversine_km,
    _make_instance_id,
    _normalize_voltage_kv,
    _point_coords,
    _prop_int,
    _unique_unit_key,
    apply_assignments,
)
from esfex.visualization.data.gui_model import (  # noqa: E402
    GeoPoint,
    GuiBus,
    GuiFuelEntryPoint,
    GuiNode,
    GuiSystemState,
)
from esfex.visualization.panels.parse_geo_asset_dialog import ParseAssignment  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _state_with_node(index=0, name="N0", lat=23.0, lng=-82.0):
    """State with a single node carrying a stored centroid."""
    s = GuiSystemState()
    s.nodes.append(GuiNode(index=index, name=name,
                           centroid_lat=lat, centroid_lng=lng))
    return s


def _point(target_type, lng, lat, properties=None, target_node=None):
    return ParseAssignment(
        feature_index=0,
        geometry_type="Point",
        target_type=target_type,
        properties=properties or {},
        coordinates=[lng, lat],
        target_node=target_node,
    )


def _line(target_type, coords, properties=None, geometry_type="LineString"):
    return ParseAssignment(
        feature_index=0,
        geometry_type=geometry_type,
        target_type=target_type,
        properties=properties or {},
        coordinates=coords,
    )


def _polygon(coords, properties=None, geometry_type="Polygon"):
    return ParseAssignment(
        feature_index=0,
        geometry_type=geometry_type,
        target_type="zone",
        properties=properties or {},
        coordinates=coords,
    )


# ──────────────────────────────────────────────────────────────────────
# ParseResult.summary
# ──────────────────────────────────────────────────────────────────────


class TestParseResultSummary:
    def test_empty(self):
        assert ParseResult().summary() == "No elements created."

    def test_all_counters_and_warnings(self):
        r = ParseResult(
            buses_added=1, generators_added=2, batteries_added=1,
            lines_added=1, fuel_entries_added=1, fuel_routes_added=1,
            zones_added=1, electrolyzers_added=1, transformers_added=1,
            acdc_converters_added=1, freq_converters_added=1,
            fuel_storages_added=1, fuels_created=1, technologies_created=1,
            warnings=["w1", "w2"],
        )
        msg = r.summary()
        assert msg.startswith("Created:")
        assert "2 generator(s)" in msg
        assert "AC/DC converter(s)" in msg
        assert "freq. converter(s)" in msg
        assert "Warnings (2):" in msg
        assert "  - w1" in msg
        assert "  - w2" in msg


# ──────────────────────────────────────────────────────────────────────
# _normalize_voltage_kv / _prop_int / _point_coords
# ──────────────────────────────────────────────────────────────────────


def test_normalize_voltage_kv_volts_to_kv():
    assert _normalize_voltage_kv(220000) == 220.0


def test_normalize_voltage_kv_already_kv():
    assert _normalize_voltage_kv(220) == 220


def test_normalize_voltage_kv_boundary():
    # exactly 1200 stays, just above is divided
    assert _normalize_voltage_kv(1200) == 1200
    assert _normalize_voltage_kv(1201) == pytest.approx(1.201)


def test_prop_int_bad_value_returns_default():
    assert _prop_int({"x": "not-an-int"}, "x", default=7) == 7


def test_point_coords_swaps_lng_lat():
    assert _point_coords([10.0, 20.0]) == (20.0, 10.0)


# ──────────────────────────────────────────────────────────────────────
# _compute_node_centroids
# ──────────────────────────────────────────────────────────────────────


class TestComputeNodeCentroids:
    def test_stored_centroid_preferred(self):
        s = _state_with_node(0, lat=10.0, lng=20.0)
        c = _compute_node_centroids(s)
        assert c[0] == (10.0, 20.0)

    def test_fallback_to_bus_average(self):
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0"))  # no centroid
        s.buses["b1"] = GuiBus(bus_id="b1", parent_node=0,
                               latitude=10.0, longitude=20.0)
        s.buses["b2"] = GuiBus(bus_id="b2", parent_node=0,
                               latitude=20.0, longitude=40.0)
        c = _compute_node_centroids(s)
        assert c[0] == (15.0, 30.0)

    def test_skips_zero_zero_buses(self):
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0"))
        s.buses["b1"] = GuiBus(bus_id="b1", parent_node=0,
                               latitude=0.0, longitude=0.0)
        c = _compute_node_centroids(s)
        assert 0 not in c

    def test_bus_on_node_with_stored_centroid_ignored(self):
        s = _state_with_node(0, lat=10.0, lng=20.0)
        s.buses["b1"] = GuiBus(bus_id="b1", parent_node=0,
                               latitude=99.0, longitude=99.0)
        c = _compute_node_centroids(s)
        assert c[0] == (10.0, 20.0)


# ──────────────────────────────────────────────────────────────────────
# _find_nearest_node / _find_nearest_node_idx
# ──────────────────────────────────────────────────────────────────────


class TestFindNearestNode:
    def test_with_centroids_picks_closest(self):
        nodes = [GuiNode(index=0, name="A"), GuiNode(index=1, name="B")]
        centroids = {0: (0.0, 0.0), 1: (50.0, 50.0)}
        idx, dist = _find_nearest_node(1.0, 1.0, nodes, centroids)
        assert idx == 0
        assert dist > 0

    def test_no_centroids_falls_back_to_first_node(self):
        nodes = [GuiNode(index=3, name="A")]
        idx, dist = _find_nearest_node(1.0, 1.0, nodes, None)
        assert idx == 3
        assert dist == 0.0

    def test_empty_nodes_returns_none(self):
        idx, dist = _find_nearest_node(1.0, 1.0, [], None)
        assert idx is None
        assert dist == float("inf")

    def test_centroids_without_matching_node_falls_back(self):
        # centroid keys don't match any node.index -> best_idx stays None
        nodes = [GuiNode(index=7, name="A")]
        centroids = {99: (0.0, 0.0)}
        idx, _ = _find_nearest_node(1.0, 1.0, nodes, centroids)
        assert idx == 7  # fall back to first node

    def test_idx_no_nodes_returns_zero(self):
        s = GuiSystemState()
        assert _find_nearest_node_idx(s, 1.0, 1.0) == 0

    def test_idx_uses_nearest(self):
        s = _state_with_node(5, lat=10.0, lng=10.0)
        centroids = _compute_node_centroids(s)
        assert _find_nearest_node_idx(s, 10.0, 10.0, centroids) == 5


# ──────────────────────────────────────────────────────────────────────
# _find_nearest_bus
# ──────────────────────────────────────────────────────────────────────


class TestFindNearestBus:
    def test_finds_close_bus(self):
        s = GuiSystemState()
        s.buses["b1"] = GuiBus(bus_id="b1", latitude=10.0, longitude=10.0)
        bid, dist = _find_nearest_bus(10.0, 10.0, s)
        assert bid == "b1"
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_prefilter_skips_far_bus(self):
        s = GuiSystemState()
        s.buses["b1"] = GuiBus(bus_id="b1", latitude=80.0, longitude=80.0)
        bid, dist = _find_nearest_bus(10.0, 10.0, s, _snap_km=50.0)
        assert bid is None
        assert dist == float("inf")

    def test_voltage_filter_excludes_incompatible(self):
        s = GuiSystemState()
        s.buses["b1"] = GuiBus(bus_id="b1", latitude=10.0, longitude=10.0,
                               voltage_kv=220.0)
        # request 110 kV: ratio 2.0 outside tolerance -> excluded
        bid, _ = _find_nearest_bus(10.0, 10.0, s, voltage_kv=110.0)
        assert bid is None

    def test_voltage_filter_includes_compatible(self):
        s = GuiSystemState()
        s.buses["b1"] = GuiBus(bus_id="b1", latitude=10.0, longitude=10.0,
                               voltage_kv=110.0)
        bid, _ = _find_nearest_bus(10.0, 10.0, s, voltage_kv=110.0)
        assert bid == "b1"


# ──────────────────────────────────────────────────────────────────────
# _find_nearest_fuel_point
# ──────────────────────────────────────────────────────────────────────


class TestFindNearestFuelPoint:
    def test_no_entries(self):
        s = GuiSystemState()
        bid, dist = _find_nearest_fuel_point(10.0, 10.0, s)
        assert bid is None
        assert dist == float("inf")

    def test_finds_entry(self):
        s = GuiSystemState()
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe", node=0,
                              coordinate=GeoPoint(10.0, 10.0, "fe")))
        bid, dist = _find_nearest_fuel_point(10.0, 10.0, s)
        assert bid == "fuel_entry_0"
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_entry_without_coordinate_skipped(self):
        s = GuiSystemState()
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe", node=0, coordinate=None))
        bid, _ = _find_nearest_fuel_point(10.0, 10.0, s)
        assert bid is None


# ──────────────────────────────────────────────────────────────────────
# _unique_unit_key / _make_instance_id collision branches
# ──────────────────────────────────────────────────────────────────────


class _FakeInst:
    def __init__(self, unit_key, node):
        self.unit_key = unit_key
        self.node = node


class TestUniqueUnitKey:
    def test_no_collision_returns_base(self):
        assert _unique_unit_key("solar", 0, {}) == "solar"

    def test_collision_appends_suffix(self):
        existing = {"a": _FakeInst("solar", 0)}
        assert _unique_unit_key("solar", 0, existing) == "solar_1"

    def test_collision_on_suffix_increments(self):
        existing = {
            "a": _FakeInst("solar", 0),
            "b": _FakeInst("solar_1", 0),
        }
        assert _unique_unit_key("solar", 0, existing) == "solar_2"

    def test_same_key_different_node_no_collision(self):
        existing = {"a": _FakeInst("solar", 1)}
        assert _unique_unit_key("solar", 0, existing) == "solar"


def test_make_instance_id_collision():
    existing = {"gen_solar_0": object()}
    assert _make_instance_id("gen", "solar", 0, existing) == "gen_solar_0_1"


def test_make_instance_id_double_collision():
    existing = {"gen_solar_0": 1, "gen_solar_0_1": 1}
    assert _make_instance_id("gen", "solar", 0, existing) == "gen_solar_0_2"


# ──────────────────────────────────────────────────────────────────────
# _ensure_bus_at
# ──────────────────────────────────────────────────────────────────────


class TestEnsureBusAt:
    def test_creates_bus_with_defaults(self):
        s = _state_with_node(0, lat=23.0, lng=-82.0)
        r = ParseResult()
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "MyBus", 5.0, r,
            props={}, centroids=_compute_node_centroids(s),
        )
        assert node_idx == 0
        assert bus_id in s.buses
        assert r.buses_added == 1
        assert s.buses[bus_id].voltage_kv == 220.0

    def test_snaps_to_existing_bus(self):
        s = _state_with_node(0, lat=23.0, lng=-82.0)
        s.buses["existing"] = GuiBus(bus_id="existing", parent_node=0,
                                     latitude=23.0, longitude=-82.0)
        r = ParseResult()
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0001, -82.0001, "MyBus", 5.0, r,
        )
        assert bus_id == "existing"
        assert r.buses_added == 0

    def test_force_node_prevents_snap_to_other_node_bus(self):
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.nodes.append(GuiNode(index=1, name="N1", centroid_lat=24.0,
                               centroid_lng=-83.0))
        # existing bus lives on node 0
        s.buses["existing"] = GuiBus(bus_id="existing", parent_node=0,
                                     latitude=23.0, longitude=-82.0)
        r = ParseResult()
        # force node 1 even though existing bus is right here on node 0
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "MyBus", 5.0, r, force_node=1,
        )
        assert node_idx == 1
        assert bus_id != "existing"
        assert r.buses_added == 1

    def test_reuses_bus_already_on_target_node(self):
        s = _state_with_node(0, lat=23.0, lng=-82.0)
        # bus far enough that proximity snap (step 1) is skipped by voltage,
        # but same node within snap distance triggers step 2 reuse.
        s.buses["onnode"] = GuiBus(bus_id="onnode", parent_node=0,
                                   latitude=23.0, longitude=-82.0,
                                   voltage_kv=220.0)
        r = ParseResult()
        # request a 110 kV bus -> step 1 voltage filter excludes onnode,
        # step 2 also excludes it (ratio check), so a NEW bus is created.
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "MyBus", 50.0, r,
            props={"voltage_kv": 110.0},
        )
        assert bus_id != "onnode"
        assert r.buses_added == 1

    def test_reuses_compatible_bus_on_target_node(self):
        s = _state_with_node(0, lat=23.0, lng=-82.0)
        # Place existing bus just outside step-1 prefilter but same node,
        # compatible voltage -> reused in step 2.
        s.buses["onnode"] = GuiBus(bus_id="onnode", parent_node=0,
                                   latitude=23.0, longitude=-82.0,
                                   voltage_kv=220.0)
        r = ParseResult()
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "MyBus", 50.0, r,
            props={"voltage_kv": 220.0},
        )
        assert bus_id == "onnode"
        assert r.buses_added == 0

    def test_step2_reuse_bus_on_forced_node(self):
        # Two nodes each with a bus at the same point. Step 1 snaps to one of
        # them, but force_node points at the OTHER node, so step 1 declines
        # (force_node != bus.parent_node) and step 2 reuses the bus that
        # lives on the forced node.
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.nodes.append(GuiNode(index=1, name="N1", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.buses["b0"] = GuiBus(bus_id="b0", parent_node=0,
                               latitude=23.0, longitude=-82.0)
        s.buses["b1"] = GuiBus(bus_id="b1", parent_node=1,
                               latitude=23.0, longitude=-82.0)
        r = ParseResult()
        node_idx, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "X", 50.0, r, force_node=1,
        )
        assert node_idx == 1
        assert bus_id == "b1"
        assert r.buses_added == 0

    def test_voltage_normalization_on_create(self):
        s = _state_with_node(0)
        r = ParseResult()
        _, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "HV", 5.0, r,
            props={"voltage": 110000},
        )
        assert s.buses[bus_id].voltage_kv == 110.0

    def test_explicit_frequency_used(self):
        s = _state_with_node(0)
        r = ParseResult()
        _, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "B", 5.0, r,
            props={"frequency_hz": 60.0},
        )
        assert s.buses[bus_id].frequency_hz == 60.0

    def test_bus_name_from_station_property(self):
        s = _state_with_node(0)
        r = ParseResult()
        _, bus_id = _ensure_bus_at(
            s, 23.0, -82.0, "fallback", 5.0, r,
            props={"station": "Central Station"},
        )
        assert s.buses[bus_id].name == "Central Station"


# ──────────────────────────────────────────────────────────────────────
# _ensure_fuel_entry_at
# ──────────────────────────────────────────────────────────────────────


class TestEnsureFuelEntryAt:
    def test_create_path_creates_entry(self):
        # The create branch builds a GuiFuelEntryPoint with a fuel_params
        # dict and succeeds; one fuel entry is added at the resolved node.
        s = _state_with_node(0)
        r = ParseResult()
        node_idx, entry_id = _ensure_fuel_entry_at(
            s, 23.0, -82.0, "FE", 5.0, r,
            props={"fuels": "Diesel"},
        )
        assert node_idx == 0
        assert entry_id == "fuel_entry_0"
        assert r.fuel_entries_added == 1
        assert len(s.fuel_entry_points) == 1
        fe = s.fuel_entry_points[0]
        assert fe.fuels == ["Diesel"]
        assert "Diesel" in fe.fuel_params

    def test_snaps_to_existing_entry(self):
        s = _state_with_node(0)
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.0, -82.0, "fe0")))
        r = ParseResult()
        node_idx, entry_id = _ensure_fuel_entry_at(
            s, 23.0001, -82.0001, "FE", 5.0, r,
        )
        assert entry_id == "fuel_entry_0"
        assert r.fuel_entries_added == 0

    def test_force_node_prevents_snap_then_creates_on_forced_node(self):
        # The forced node differs from the existing entry's node, so the snap
        # is declined and a new entry is created on the forced node.
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.nodes.append(GuiNode(index=1, name="N1", centroid_lat=24.0,
                               centroid_lng=-83.0))
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.0, -82.0, "fe0")))
        r = ParseResult()
        node_idx, _ = _ensure_fuel_entry_at(
            s, 23.0, -82.0, "FE", 5.0, r, force_node=1,
        )
        assert node_idx == 1
        assert r.fuel_entries_added == 1
        assert len(s.fuel_entry_points) == 2
        assert s.fuel_entry_points[-1].node == 1

    def test_reuses_entry_on_target_node(self):
        s = _state_with_node(0)
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.5, -82.5, "fe0")))
        r = ParseResult()
        # Point is >snap from the entry by proximity step (so step 1 skips),
        # but with a large snap and same node it gets reused in step 2.
        node_idx, entry_id = _ensure_fuel_entry_at(
            s, 23.5, -82.5, "FE", 100.0, r,
        )
        assert entry_id == "fuel_entry_0"
        assert r.fuel_entries_added == 0


# ──────────────────────────────────────────────────────────────────────
# apply_assignments — Point branches
# ──────────────────────────────────────────────────────────────────────


class TestApplyPoints:
    def test_generator(self):
        s = _state_with_node(0)
        a = _point("generator", -82.0, 23.0,
                   {"name": "GenA", "gen_type": "Renewable", "fuel": "Solar",
                    "rated_power": 50.0, "technology": "PV"})
        r = apply_assignments(s, [a])
        assert r.generators_added == 1
        gen = next(iter(s.generators.values()))
        assert gen.gen_type == "Renewable"
        assert gen.technology_id == "PV"

    def test_generator_invalid_type_defaults(self):
        s = _state_with_node(0)
        a = _point("generator", -82.0, 23.0,
                   {"gen_type": "Bogus", "rated_power": 10.0})
        r = apply_assignments(s, [a])
        gen = next(iter(s.generators.values()))
        assert gen.gen_type == "Non-renewable"

    def test_battery(self):
        s = _state_with_node(0)
        a = _point("battery", -82.0, 23.0,
                   {"name": "BatA", "rated_power": 20.0,
                    "capacity_mwh": 80.0})
        r = apply_assignments(s, [a])
        assert r.batteries_added == 1
        bat = next(iter(s.batteries.values()))
        assert bat.capacity == 80.0

    def test_fuel_entry_point_creates_entry(self):
        # The fuel_entry point branch builds a valid GuiFuelEntryPoint and
        # adds it (single fuel).
        s = _state_with_node(0)
        a = _point("fuel_entry", -82.0, 23.0,
                   {"name": "FE", "fuel": "Diesel"})
        r = apply_assignments(s, [a])
        assert r.fuel_entries_added == 1
        assert len(s.fuel_entry_points) == 1
        assert s.fuel_entry_points[0].fuels == ["Diesel"]

    def test_fuel_entry_list_fuels_creates_entry(self):
        s = _state_with_node(0)
        a = _point("fuel_entry", -82.0, 23.0,
                   {"fuels": ["Diesel", "Gas"]})
        r = apply_assignments(s, [a])
        assert r.fuel_entries_added == 1
        fe = s.fuel_entry_points[0]
        assert fe.fuels == ["Diesel", "Gas"]
        assert set(fe.fuel_params) == {"Diesel", "Gas"}

    def test_electrolyzer(self):
        s = _state_with_node(0)
        a = _point("electrolyzer", -82.0, 23.0,
                   {"name": "EZ", "rated_power": 5.0, "technology": "AEM"})
        r = apply_assignments(s, [a])
        assert r.electrolyzers_added == 1
        ez = next(iter(s.electrolyzers.values()))
        assert ez.technology == "AEM"

    def test_bus(self):
        s = _state_with_node(0)
        a = _point("bus", -82.0, 23.0, {"name": "Bus1"})
        r = apply_assignments(s, [a])
        assert r.buses_added == 1

    def test_transformer(self):
        s = _state_with_node(0)
        a = _point("transformer", -82.0, 23.0,
                   {"name": "TX", "from_voltage_kv": 220, "to_voltage_kv": 110})
        r = apply_assignments(s, [a])
        assert r.transformers_added == 1
        tx = s.transformers[0]
        assert tx.from_voltage_kv == 220.0
        assert tx.to_voltage_kv == 110.0

    def test_acdc_converter(self):
        s = _state_with_node(0)
        a = _point("acdc_converter", -82.0, 23.0,
                   {"name": "ACDC", "converter_type": "LCC"})
        r = apply_assignments(s, [a])
        assert r.acdc_converters_added == 1
        assert s.acdc_converters[0].converter_type == "LCC"

    def test_freq_converter(self):
        s = _state_with_node(0)
        a = _point("freq_converter", -82.0, 23.0, {"name": "FC"})
        r = apply_assignments(s, [a])
        assert r.freq_converters_added == 1
        assert s.freq_converters[0].from_frequency_hz == 50.0

    def test_fuel_storage(self):
        s = _state_with_node(0)
        a = _point("fuel_storage", -82.0, 23.0,
                   {"name": "FS", "fuel": "LNG", "capacity": 1000.0,
                    "initial_level": 0.7, "min_level": 0.2})
        r = apply_assignments(s, [a])
        assert r.fuel_storages_added == 1
        fs = s.fuel_storages["fuel_storage_0"]
        assert fs.fuels == ["LNG"]
        assert fs.fuel_params["LNG"].capacity == 1000.0

    def test_fuel_storage_id_collision(self):
        s = _state_with_node(0)
        # Pre-occupy fuel_storage_1 (len==1 so idx starts at 1 -> collision,
        # exercising the while-loop bump to fuel_storage_2).
        from esfex.visualization.data.gui_model import GuiFuelStorage
        s.fuel_storages["fuel_storage_1"] = GuiFuelStorage(
            storage_id="fuel_storage_1", name="pre", node=0)
        a = _point("fuel_storage", -82.0, 23.0, {"name": "FS"})
        apply_assignments(s, [a])
        assert "fuel_storage_2" in s.fuel_storages

    def test_point_exception_recorded_as_warning(self):
        s = _state_with_node(0)
        # coordinates too short -> _point_coords raises IndexError -> warning
        a = ParseAssignment(
            feature_index=0, geometry_type="Point",
            target_type="bus", properties={"name": "X"}, coordinates=[1.0],
        )
        r = apply_assignments(s, [a])
        assert r.buses_added == 0
        assert any("Point" in w for w in r.warnings)

    def test_target_node_override(self):
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.nodes.append(GuiNode(index=1, name="N1", centroid_lat=40.0,
                               centroid_lng=-100.0))
        a = _point("generator", -82.0, 23.0,
                   {"rated_power": 1.0}, target_node=1)
        apply_assignments(s, [a])
        gen = next(iter(s.generators.values()))
        assert gen.node == 1


# ──────────────────────────────────────────────────────────────────────
# apply_assignments — Line branches
# ──────────────────────────────────────────────────────────────────────


class TestApplyLines:
    def test_transmission_line(self):
        s = _state_with_node(0)
        a = _line("line", [[-82.0, 23.0], [-83.0, 24.0]],
                  {"name": "L1", "capacity_mw": 200.0, "voltage_kv": 220})
        r = apply_assignments(s, [a])
        assert r.lines_added == 1
        line = s.transmission_lines[0]
        assert line.capacity_mw == 200.0
        assert line.voltage_kv == 220.0

    def test_line_with_waypoints(self):
        s = _state_with_node(0)
        a = _line("line",
                  [[-82.0, 23.0], [-82.5, 23.5], [-83.0, 24.0]],
                  {"name": "L"})
        apply_assignments(s, [a])
        assert len(s.transmission_lines[0].waypoints) == 1

    def test_line_too_few_points(self):
        s = _state_with_node(0)
        a = _line("line", [[-82.0, 23.0]], {"name": "Short"})
        r = apply_assignments(s, [a])
        assert r.lines_added == 0
        assert any("fewer than 2 points" in w for w in r.warnings)

    def test_line_endpoints_same_bus(self):
        s = _state_with_node(0)
        # Both endpoints identical -> snap to same bus -> warning
        a = _line("line", [[-82.0, 23.0], [-82.0, 23.0]], {"name": "Dup"})
        r = apply_assignments(s, [a])
        assert r.lines_added == 0
        assert any("same bus" in w for w in r.warnings)

    def test_multilinestring_uses_first_segment(self):
        s = _state_with_node(0)
        a = _line("line",
                  [[[-82.0, 23.0], [-83.0, 24.0]], [[-90.0, 30.0], [-91.0, 31.0]]],
                  {"name": "ML"}, geometry_type="MultiLineString")
        r = apply_assignments(s, [a])
        assert r.lines_added == 1

    def test_fuel_route_auto_creates_entries_and_route(self):
        # fuel_route auto-creates a fuel entry at each endpoint (now that the
        # GuiFuelEntryPoint constructor is valid) and adds the transport route.
        s = _state_with_node(0)
        a = _line("fuel_route", [[-82.0, 23.0], [-83.0, 24.0]],
                  {"name": "R1", "fuels": "Gas", "capacity": 500.0})
        r = apply_assignments(s, [a])
        assert r.fuel_routes_added == 1
        assert len(s.fuel_transport_routes) == 1
        assert len(s.fuel_entry_points) == 2

    def test_fuel_route_snaps_to_existing_entries(self):
        # When both endpoints snap to PRE-EXISTING fuel entries (no creation
        # needed), the route block runs to completion.
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.0, -82.0, "fe0")))
        # second entry far enough to be a distinct snap target
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe1", node=0,
                              coordinate=GeoPoint(24.0, -83.0, "fe1")))
        a = _line("fuel_route", [[-82.0, 23.0], [-83.0, 24.0]],
                  {"name": "R", "fuels": "Gas", "capacity": 500.0,
                   "length_km": 42.0})
        r = apply_assignments(s, [a], snap_threshold_km=50.0)
        assert r.fuel_routes_added == 1
        route = s.fuel_transport_routes[0]
        assert route.capacity == 500.0
        assert route.length_km == 42.0

    def test_fuel_route_computed_length(self):
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.0, -82.0, "fe0")))
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe1", node=0,
                              coordinate=GeoPoint(24.0, -83.0, "fe1")))
        # waypoint in the middle, no length_km -> length computed via haversine
        a = _line("fuel_route",
                  [[-82.0, 23.0], [-82.5, 23.5], [-83.0, 24.0]],
                  {"name": "R", "fuels": "Gas"})
        r = apply_assignments(s, [a], snap_threshold_km=50.0)
        assert r.fuel_routes_added == 1
        assert s.fuel_transport_routes[0].length_km > 0

    def test_fuel_route_same_entry(self):
        # Both endpoints snap to the SAME pre-existing entry -> warning.
        s = GuiSystemState()
        s.nodes.append(GuiNode(index=0, name="N0", centroid_lat=23.0,
                               centroid_lng=-82.0))
        s.fuel_entry_points.append(
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(23.0, -82.0, "fe0")))
        a = _line("fuel_route", [[-82.0, 23.0], [-82.0, 23.0]], {"name": "Dup"})
        r = apply_assignments(s, [a], snap_threshold_km=50.0)
        assert r.fuel_routes_added == 0
        assert any("same fuel entry" in w for w in r.warnings)

    def test_line_exception_recorded(self):
        s = _state_with_node(0)
        # malformed coords element triggers exception in the line block
        a = ParseAssignment(
            feature_index=0, geometry_type="LineString",
            target_type="line", properties={"name": "Bad"},
            coordinates=[[1.0], [2.0]],
        )
        r = apply_assignments(s, [a])
        assert any("Line feature" in w for w in r.warnings)


# ──────────────────────────────────────────────────────────────────────
# apply_assignments — Polygon branches
# ──────────────────────────────────────────────────────────────────────


class TestApplyPolygons:
    def test_simple_polygon(self):
        s = _state_with_node(0)
        ring = [[[-82.0, 23.0], [-82.0, 24.0], [-83.0, 24.0], [-82.0, 23.0]]]
        a = _polygon(ring, {"name": "Z1", "technology": "Wind",
                            "max_capacity_mw": 300.0, "color": "#abc"})
        r = apply_assignments(s, [a])
        assert r.zones_added == 1
        z = s.development_zones[0]
        assert z.technology == "Wind"
        assert z.max_capacity_mw == 300.0
        assert len(z.polygon) == 4

    def test_polygon_no_max_capacity(self):
        s = _state_with_node(0)
        ring = [[[-82.0, 23.0], [-82.0, 24.0], [-83.0, 24.0], [-82.0, 23.0]]]
        a = _polygon(ring, {"name": "Z"})
        apply_assignments(s, [a])
        assert s.development_zones[0].max_capacity_mw is None

    def test_multipolygon(self):
        s = _state_with_node(0)
        poly1 = [[[-82.0, 23.0], [-82.0, 24.0], [-83.0, 24.0], [-82.0, 23.0]]]
        poly2 = [[[-90.0, 30.0], [-90.0, 31.0], [-91.0, 31.0], [-90.0, 30.0]]]
        a = _polygon([poly1, poly2], {"name": "MP"},
                     geometry_type="MultiPolygon")
        r = apply_assignments(s, [a])
        assert r.zones_added == 1

    def test_polygon_flat_ring(self):
        # outer ring given as a flat list of points (not nested in rings)
        s = _state_with_node(0)
        flat = [[-82.0, 23.0], [-82.0, 24.0], [-83.0, 24.0], [-82.0, 23.0]]
        a = _polygon(flat, {"name": "Flat"})
        r = apply_assignments(s, [a])
        assert r.zones_added == 1
        assert len(s.development_zones[0].polygon) == 4

    def test_polygon_exception_recorded(self):
        s = _state_with_node(0)
        a = ParseAssignment(
            feature_index=0, geometry_type="Polygon", target_type="zone",
            properties={"name": "Bad"}, coordinates=[],
        )
        r = apply_assignments(s, [a])
        assert any("Polygon" in w for w in r.warnings)


# ──────────────────────────────────────────────────────────────────────
# apply_assignments — mixed / ordering
# ──────────────────────────────────────────────────────────────────────


def test_apply_mixed_assignments_processes_all_geometries():
    s = _state_with_node(0)
    pt = _point("bus", -82.0, 23.0, {"name": "B"})
    ln = _line("line", [[-82.0, 23.0], [-83.0, 24.0]], {"name": "L"})
    poly = _polygon(
        [[[-82.0, 23.0], [-82.0, 24.0], [-83.0, 24.0], [-82.0, 23.0]]],
        {"name": "Z"})
    r = apply_assignments(s, [poly, ln, pt])
    assert r.zones_added == 1
    assert r.lines_added == 1
    # summary should contain at least the line and zone tokens
    summ = r.summary()
    assert "transmission line(s)" in summ
    assert "development zone(s)" in summ


class TestFindNearestNodeIdxScales:
    """The projected KD-tree path must match a full haversine scan AND must
    not reuse a stale tree across rebuilds (the bug that collapsed networks
    toward wrong centroids — 'lines to a centroid')."""

    @staticmethod
    def _hav(lat, lng, clat, clng):
        return _haversine_km(lat, lng, clat, clng)

    def _state(self, centroids):
        from esfex.visualization.data.gui_model import GuiSystemState
        st = GuiSystemState(name="s")
        st.nodes = [GuiNode(index=k, name=f"N{k}",
                            centroid_lat=c[0], centroid_lng=c[1])
                    for k, c in centroids.items()]
        return st

    def test_matches_full_haversine_scan(self):
        import random
        rng = random.Random(5)
        centroids = {k: (30.0 + k * (15.0 / 7), 135.0 + rng.uniform(-2, 2))
                     for k in range(8)}
        st = self._state(centroids)
        for _ in range(2000):
            lat = 30.0 + rng.random() * 15.0
            lng = 135.0 + rng.uniform(-3, 3)
            got = _find_nearest_node_idx(st, lat, lng, centroids)
            tru = min(centroids.items(),
                      key=lambda kv: self._hav(lat, lng, kv[1][0], kv[1][1]))[0]
            assert got == tru

    def test_cache_invalidates_on_recluster_same_count(self):
        # First call builds the tree; a second call with DIFFERENT centroids
        # of the SAME length must NOT reuse it.
        import random
        rng = random.Random(6)
        c1 = {k: (30.0 + k * 2.0, 135.0) for k in range(8)}
        c2 = {k: (45.0 - k * 2.0, 140.0) for k in range(8)}  # moved, same count
        st = self._state(c1)
        _find_nearest_node_idx(st, 31.0, 135.0, c1)  # warm cache on c1
        # rebuild the node objects to match c2 too
        st = self._state(c2)
        for _ in range(1500):
            lat = 30.0 + rng.random() * 15.0
            lng = 135.0 + rng.uniform(-3, 3)
            got = _find_nearest_node_idx(st, lat, lng, c2)
            tru = min(c2.items(),
                      key=lambda kv: self._hav(lat, lng, kv[1][0], kv[1][1]))[0]
            assert got == tru

    def test_many_nodes_correct(self):
        import random
        rng = random.Random(7)
        centroids = {k: (20 + rng.random() * 30, 120 + rng.random() * 30)
                     for k in range(500)}
        st = self._state(centroids)
        for _ in range(1500):
            lat = 20 + rng.random() * 30
            lng = 120 + rng.random() * 30
            got = _find_nearest_node_idx(st, lat, lng, centroids)
            tru = min(centroids.items(),
                      key=lambda kv: self._hav(lat, lng, kv[1][0], kv[1][1]))[0]
            assert got == tru
