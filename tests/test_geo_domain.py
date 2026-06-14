"""Unit tests for GeoAsset → workflow domain dissolving."""

import pytest

from esfex.visualization.workflows.geo_domain import (
    domain_bounds,
    domain_shapely,
    geoasset_to_domain_polygon,
)


def _poly_feature(ring_lnglat):
    return {"type": "Feature", "geometry": {
        "type": "Polygon", "coordinates": [ring_lnglat]}}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


# A 0..10 x 0..10 square in (lng, lat).
SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]


def _area(ring):
    from esfex.visualization.workflows.geo_domain import domain_shapely
    return domain_shapely(ring).area


def test_single_polygon_returns_latlng_ring():
    poly = geoasset_to_domain_polygon(_fc(_poly_feature(SQUARE)))
    assert len(poly) == 4
    # (lat, lng) order — GeoJSON was (lng, lat).
    assert (0.0, 0.0) in poly and (10.0, 10.0) in poly
    assert _area(poly) == pytest.approx(100.0)


def test_overlapping_polygons_union_to_one():
    a = _poly_feature(SQUARE)
    b = _poly_feature([[5, 5], [15, 5], [15, 15], [5, 15], [5, 5]])
    poly = geoasset_to_domain_polygon(_fc(a, b))
    # Union of two overlapping squares → one L-shaped ring (area 175, not 200).
    assert _area(poly) == pytest.approx(175.0)


def test_disjoint_polygons_take_largest_part():
    small = _poly_feature([[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]])      # area 4
    big = _poly_feature([[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]])  # area 100
    poly = geoasset_to_domain_polygon(_fc(small, big))
    # Documented v1 limitation: largest part only.
    assert _area(poly) == pytest.approx(100.0)


def test_polygon_with_hole_keeps_exterior_only():
    geom = {"type": "Feature", "geometry": {
        "type": "Polygon",
        "coordinates": [
            SQUARE,
            [[3, 3], [3, 7], [7, 7], [7, 3], [3, 3]],  # hole
        ],
    }}
    poly = geoasset_to_domain_polygon(_fc(geom))
    assert _area(poly) == pytest.approx(100.0)  # hole ignored


def test_multipolygon_geometry_supported():
    geom = {"type": "Feature", "geometry": {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]],     # area 16
            [[[20, 20], [22, 20], [22, 22], [20, 22], [20, 20]]],  # area 4
        ],
    }}
    poly = geoasset_to_domain_polygon(_fc(geom))
    assert _area(poly) == pytest.approx(16.0)


def test_no_polygons_returns_empty():
    point = {"type": "Feature", "geometry": {
        "type": "Point", "coordinates": [1, 2]}}
    line = {"type": "Feature", "geometry": {
        "type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
    assert geoasset_to_domain_polygon(_fc(point, line)) == []
    assert geoasset_to_domain_polygon(_fc()) == []


def test_bare_geometry_and_feature_accepted():
    bare = {"type": "Polygon", "coordinates": [SQUARE]}
    assert len(geoasset_to_domain_polygon(bare)) == 4
    feat = _poly_feature(SQUARE)
    assert len(geoasset_to_domain_polygon(feat)) == 4


def test_domain_bounds():
    poly = [(1.0, 2.0), (3.0, 8.0), (5.0, 4.0)]
    assert domain_bounds(poly) == (1.0, 2.0, 5.0, 8.0)


def test_domain_shapely_roundtrip():
    poly = geoasset_to_domain_polygon(_fc(_poly_feature(SQUARE)))
    shp = domain_shapely(poly)
    assert shp.area == pytest.approx(100.0)
