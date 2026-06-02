"""
Tests for pure utility functions in esfex.visualization.data.geo_asset_parser.

Covers: _prop, _prop_float, _prop_int, _prop_str, _feature_name,
        _haversine_km, _point_coords, _make_instance_id, ParseResult.
"""

import math

import pytest

from esfex.visualization.data.geo_asset_parser import (
    ParseResult,
    _feature_name,
    _haversine_km,
    _make_instance_id,
    _point_coords,
    _prop,
    _prop_float,
    _prop_int,
    _prop_str,
)


# ──────────────────────────────────────────────────────────────────────
# _prop
# ──────────────────────────────────────────────────────────────────────


class TestProp:
    """Tests for _prop() case-insensitive property lookup."""

    def test_exact_key_match(self):
        """Exact key present returns its value immediately."""
        props = {"voltage": 220, "name": "Node A"}
        assert _prop(props, "voltage") == 220

    def test_case_insensitive_fallback(self):
        """When exact key is missing, falls back to case-insensitive match."""
        props = {"Voltage": 110}
        assert _prop(props, "voltage") == 110

    def test_multiple_key_aliases_first_wins(self):
        """First matching alias is returned, even if later aliases also match."""
        props = {"capacity_mw": 500, "MW": 300}
        assert _prop(props, "capacity_mw", "MW") == 500

    def test_multiple_key_aliases_second_match(self):
        """Second alias is tried when first is absent."""
        props = {"MW": 300}
        assert _prop(props, "capacity_mw", "MW") == 300

    def test_case_insensitive_with_aliases(self):
        """Case-insensitive search across multiple aliases."""
        props = {"CAPACITY": 750}
        assert _prop(props, "cap", "capacity") == 750

    def test_default_when_not_found(self):
        """Returns default when no key matches (case-sensitive or insensitive)."""
        props = {"color": "red"}
        assert _prop(props, "voltage", "MW", default=42) == 42

    def test_default_none_when_not_found(self):
        """Default is None when no explicit default and no match."""
        props = {"color": "red"}
        assert _prop(props, "missing") is None

    def test_empty_props(self):
        """Empty dict returns default."""
        assert _prop({}, "anything", default="fallback") == "fallback"


# ──────────────────────────────────────────────────────────────────────
# _prop_float
# ──────────────────────────────────────────────────────────────────────


class TestPropFloat:
    """Tests for _prop_float() float extraction."""

    def test_valid_float(self):
        """Numeric float value is returned as float."""
        assert _prop_float({"power": 3.14}, "power") == pytest.approx(3.14)

    def test_string_float(self):
        """String that represents a float is converted."""
        assert _prop_float({"power": "2.718"}, "power") == pytest.approx(2.718)

    def test_integer_value(self):
        """Integer value is cast to float."""
        assert _prop_float({"capacity": 100}, "capacity") == pytest.approx(100.0)

    def test_default_when_missing(self):
        """Returns default when key is absent."""
        assert _prop_float({}, "power", default=5.5) == pytest.approx(5.5)

    def test_invalid_value_returns_default(self):
        """Non-numeric string returns default instead of raising."""
        assert _prop_float({"power": "not_a_number"}, "power", default=9.9) == pytest.approx(9.9)

    def test_none_value_returns_default(self):
        """Explicit None value returns default."""
        assert _prop_float({"power": None}, "power", default=1.0) == pytest.approx(1.0)


# ──────────────────────────────────────────────────────────────────────
# _prop_int
# ──────────────────────────────────────────────────────────────────────


class TestPropInt:
    """Tests for _prop_int() integer extraction."""

    def test_valid_int(self):
        """Integer value is returned directly."""
        assert _prop_int({"age": 10}, "age") == 10

    def test_default_when_missing(self):
        """Returns default when key is absent."""
        assert _prop_int({}, "age", default=25) == 25

    def test_invalid_value_returns_default(self):
        """Non-integer string returns default."""
        assert _prop_int({"age": "old"}, "age", default=0) == 0

    def test_float_string_truncates(self):
        """A pure integer string is returned; float string may raise and use default."""
        # int("5") works fine
        assert _prop_int({"count": "5"}, "count") == 5


# ──────────────────────────────────────────────────────────────────────
# _prop_str
# ──────────────────────────────────────────────────────────────────────


class TestPropStr:
    """Tests for _prop_str() string extraction."""

    def test_valid_string(self):
        """String value is returned directly."""
        assert _prop_str({"name": "Solar Farm"}, "name") == "Solar Farm"

    def test_default_when_missing(self):
        """Returns default when key is absent."""
        assert _prop_str({}, "name", default="Unknown") == "Unknown"

    def test_non_string_converted(self):
        """Non-string value is converted to string."""
        assert _prop_str({"code": 42}, "code") == "42"

    def test_none_value_returns_default(self):
        """Explicit None value returns default string, not 'None'."""
        assert _prop_str({"name": None}, "name", default="fallback") == "fallback"


# ──────────────────────────────────────────────────────────────────────
# _feature_name
# ──────────────────────────────────────────────────────────────────────


class TestFeatureName:
    """Tests for _feature_name() GeoJSON name extraction."""

    def test_with_name_key(self):
        """Extracts from 'name' property."""
        assert _feature_name({"name": "Central Plant"}) == "Central Plant"

    def test_with_uppercase_name_key(self):
        """Extracts from 'Name' property."""
        assert _feature_name({"Name": "Northern Hub"}) == "Northern Hub"

    def test_with_label_key(self):
        """Falls back to 'label' when 'name' is absent."""
        assert _feature_name({"label": "Substation Alpha"}) == "Substation Alpha"

    def test_with_title_key(self):
        """Falls back to 'title' when earlier keys are absent."""
        assert _feature_name({"title": "Wind Park"}) == "Wind Park"

    def test_with_id_key(self):
        """Falls back to 'id' when name/label/title are absent."""
        assert _feature_name({"id": "WP-001"}) == "WP-001"

    def test_fallback_string(self):
        """Returns fallback when no recognized name key exists."""
        assert _feature_name({"color": "blue"}, fallback="Unnamed") == "Unnamed"

    def test_empty_props(self):
        """Empty properties dict returns fallback."""
        assert _feature_name({}, fallback="Default") == "Default"


# ──────────────────────────────────────────────────────────────────────
# _haversine_km
# ──────────────────────────────────────────────────────────────────────


class TestHaversineKm:
    """Tests for _haversine_km() geodesic distance calculation."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself is exactly zero."""
        assert _haversine_km(23.0, -82.0, 23.0, -82.0) == 0.0

    def test_one_degree_latitude(self):
        """One degree of latitude at the equator is approximately 111.19 km."""
        dist = _haversine_km(0.0, 0.0, 1.0, 0.0)
        assert dist == pytest.approx(111.19, abs=0.5)

    def test_new_york_to_london(self):
        """New York to London is approximately 5570 km."""
        ny_lat, ny_lng = 40.7128, -74.0060
        lon_lat, lon_lng = 51.5074, -0.1278
        dist = _haversine_km(ny_lat, ny_lng, lon_lat, lon_lng)
        assert dist == pytest.approx(5570, abs=30)

    def test_symmetry(self):
        """Distance A->B equals distance B->A."""
        d1 = _haversine_km(10.0, 20.0, 30.0, 40.0)
        d2 = _haversine_km(30.0, 40.0, 10.0, 20.0)
        assert d1 == pytest.approx(d2)

    def test_antipodal_points(self):
        """Antipodal points (diametrically opposite) are ~20015 km apart."""
        # North pole to south pole
        dist = _haversine_km(90.0, 0.0, -90.0, 0.0)
        half_circumference = math.pi * 6371.0
        assert dist == pytest.approx(half_circumference, abs=1.0)

    def test_equator_quarter_turn(self):
        """90 degrees of longitude at the equator is ~10008 km."""
        dist = _haversine_km(0.0, 0.0, 0.0, 90.0)
        quarter_circumference = math.pi * 6371.0 / 2
        assert dist == pytest.approx(quarter_circumference, abs=1.0)


# ──────────────────────────────────────────────────────────────────────
# _point_coords
# ──────────────────────────────────────────────────────────────────────


class TestPointCoords:
    """Tests for _point_coords() GeoJSON coordinate extraction."""

    def test_standard_point(self):
        """GeoJSON [lng, lat] is returned as (lat, lng)."""
        lat, lng = _point_coords([-74.006, 40.7128])
        assert lat == pytest.approx(40.7128)
        assert lng == pytest.approx(-74.006)

    def test_verify_swap(self):
        """Coordinates are swapped: index 1 becomes lat, index 0 becomes lng."""
        coords = [100.0, 200.0]
        lat, lng = _point_coords(coords)
        assert lat == 200.0
        assert lng == 100.0

    def test_with_altitude(self):
        """GeoJSON may include altitude as third element; only first two matter."""
        lat, lng = _point_coords([-82.0, 23.0, 50.0])
        assert lat == pytest.approx(23.0)
        assert lng == pytest.approx(-82.0)


# ──────────────────────────────────────────────────────────────────────
# _make_instance_id
# ──────────────────────────────────────────────────────────────────────


class TestMakeInstanceId:
    """Tests for _make_instance_id() unique ID generation."""

    def test_first_instance(self):
        """First instance with no collisions returns base ID without suffix."""
        existing = {}
        result = _make_instance_id("gen", "solar", 0, existing)
        assert result == "gen_solar_0"

    def test_collision_avoidance(self):
        """When base ID exists, appends incrementing suffix."""
        existing = {"gen_solar_0": "occupied"}
        result = _make_instance_id("gen", "solar", 0, existing)
        assert result == "gen_solar_0_1"

    def test_multiple_collisions(self):
        """Increments suffix until a free slot is found."""
        existing = {
            "bat_lion_2": "occupied",
            "bat_lion_2_1": "occupied",
            "bat_lion_2_2": "occupied",
        }
        result = _make_instance_id("bat", "lion", 2, existing)
        assert result == "bat_lion_2_3"

    def test_different_node_no_collision(self):
        """Same prefix and unit_key but different node does not collide."""
        existing = {"gen_wind_0": "occupied"}
        result = _make_instance_id("gen", "wind", 1, existing)
        assert result == "gen_wind_1"

    def test_different_prefix_no_collision(self):
        """Different prefix does not collide with existing keys."""
        existing = {"gen_solar_0": "occupied"}
        result = _make_instance_id("bat", "solar", 0, existing)
        assert result == "bat_solar_0"


# ──────────────────────────────────────────────────────────────────────
# ParseResult
# ──────────────────────────────────────────────────────────────────────


class TestParseResult:
    """Tests for ParseResult dataclass and summary() method."""

    def test_default_creation(self):
        """All counters start at zero and warnings list is empty."""
        r = ParseResult()
        assert r.buses_added == 0
        assert r.generators_added == 0
        assert r.batteries_added == 0
        assert r.lines_added == 0
        assert r.fuel_entries_added == 0
        assert r.fuel_routes_added == 0
        assert r.zones_added == 0
        assert r.electrolyzers_added == 0
        assert r.transformers_added == 0
        assert r.acdc_converters_added == 0
        assert r.freq_converters_added == 0
        assert r.fuel_storages_added == 0
        assert r.warnings == []

    def test_summary_no_elements(self):
        """Summary with no elements created shows appropriate message."""
        r = ParseResult()
        assert r.summary() == "No elements created."

    def test_summary_with_generators(self):
        """Summary includes generator count."""
        r = ParseResult(generators_added=3)
        s = r.summary()
        assert "3 generator(s)" in s
        assert s.startswith("Created: ")

    def test_summary_multiple_types(self):
        """Summary lists multiple element types."""
        r = ParseResult(buses_added=2, lines_added=1, batteries_added=4)
        s = r.summary()
        assert "2 bus(es)" in s
        assert "1 transmission line(s)" in s
        assert "4 battery(ies)" in s

    def test_summary_with_warnings(self):
        """Summary appends warning section."""
        r = ParseResult(generators_added=1, warnings=["Bad feature", "Missing coord"])
        s = r.summary()
        assert "Warnings (2):" in s
        assert "Bad feature" in s
        assert "Missing coord" in s

    def test_summary_lists_all_warnings_with_count(self):
        """summary() includes the total warning count and the full list
        (no truncation — operators need every warning to debug a parse)."""
        warnings = [f"Warning {i}" for i in range(15)]
        r = ParseResult(warnings=warnings)
        s = r.summary()
        assert "Warnings (15)" in s
        assert "Warning 0" in s
        assert "Warning 14" in s

    def test_warnings_list_independence(self):
        """Each ParseResult gets its own warnings list (no shared mutable state)."""
        r1 = ParseResult()
        r2 = ParseResult()
        r1.warnings.append("only in r1")
        assert r2.warnings == []

    def test_summary_all_types(self):
        """Summary includes all 12 element types when all are nonzero."""
        r = ParseResult(
            buses_added=1,
            generators_added=1,
            batteries_added=1,
            lines_added=1,
            fuel_entries_added=1,
            fuel_routes_added=1,
            zones_added=1,
            electrolyzers_added=1,
            transformers_added=1,
            acdc_converters_added=1,
            freq_converters_added=1,
            fuel_storages_added=1,
        )
        s = r.summary()
        assert "bus(es)" in s
        assert "generator(s)" in s
        assert "battery(ies)" in s
        assert "transmission line(s)" in s
        assert "fuel entry(ies)" in s
        assert "fuel route(s)" in s
        assert "development zone(s)" in s
        assert "electrolyzer(s)" in s
        assert "transformer(s)" in s
        assert "AC/DC converter(s)" in s
        assert "freq. converter(s)" in s
        assert "fuel storage(s)" in s
