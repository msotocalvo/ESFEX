"""Unit tests for the node Voronoi-territory helper."""

import math

import pytest

from esfex.visualization.workflows.voronoi_cells import compute_voronoi_cells

# A square domain (lat, lng) covering [0,10] x [0,10].
SQUARE = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)]


# Fixed reference latitude so every area (domain + cells) is measured in the
# SAME projection the helper used (it projects with the centroids' mean lat,
# which is 5.0 for the fixtures below). Mixing per-ring latitudes would add a
# spurious cos(lat) mismatch.
_REF_LAT = 5.0


def _poly_area_latlng(ring, ref_lat=_REF_LAT):
    """Shoelace area in the same projected metric the helper uses."""
    if len(ring) < 3:
        return 0.0
    coslat = math.cos(math.radians(ref_lat)) or 1.0
    pts = [(lng * coslat, lat) for lat, lng in ring]
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _domain_area():
    return _poly_area_latlng(SQUARE)


def test_empty_inputs():
    assert compute_voronoi_cells([], SQUARE) == []
    assert compute_voronoi_cells([(5.0, 5.0)], []) == [[]]


def test_single_node_is_whole_domain():
    cells = compute_voronoi_cells([(5.0, 5.0)], SQUARE)
    assert len(cells) == 1
    assert len(cells[0]) >= 3
    # Cell area ≈ domain area.
    assert _poly_area_latlng(cells[0]) == pytest.approx(_domain_area(), rel=1e-6)


def test_cells_tile_the_domain():
    # Four centroids in the quadrant centers → four equal cells tiling the square.
    centroids = [(2.5, 2.5), (2.5, 7.5), (7.5, 2.5), (7.5, 7.5)]
    cells = compute_voronoi_cells(centroids, SQUARE)
    assert len(cells) == 4
    for c in cells:
        assert len(c) >= 3
    total = sum(_poly_area_latlng(c) for c in cells)
    # Areas sum to the domain (no gaps / no double counting).
    assert total == pytest.approx(_domain_area(), rel=1e-6)
    # By symmetry each cell is a quarter.
    for c in cells:
        assert _poly_area_latlng(c) == pytest.approx(_domain_area() / 4.0, rel=1e-6)


def test_assignment_consistency_with_nearest_centroid():
    """Every sampled point must fall in the cell of its nearest centroid
    (same projected metric as bus assignment)."""
    from shapely.geometry import Point, Polygon

    centroids = [(2.5, 2.5), (2.5, 7.5), (7.5, 2.5), (7.5, 7.5), (5.0, 5.0)]
    cells = compute_voronoi_cells(centroids, SQUARE)
    mean_lat = sum(p[0] for p in centroids) / len(centroids)
    coslat = math.cos(math.radians(mean_lat)) or 1.0

    def proj(lat, lng):
        return (lng * coslat, lat)

    shp = [Polygon([proj(la, ln) for la, ln in c]) if len(c) >= 3 else None
           for c in cells]

    rng = [(la, ln) for la in (1.0, 3.3, 4.9, 6.1, 8.7) for ln in (1.2, 4.4, 5.5, 7.7, 9.1)]
    for la, ln in rng:
        # nearest centroid in the projected metric
        px, py = proj(la, ln)
        nearest = min(
            range(len(centroids)),
            key=lambda i: (proj(*centroids[i])[0] - px) ** 2
            + (proj(*centroids[i])[1] - py) ** 2,
        )
        poly = shp[nearest]
        assert poly is not None
        assert poly.buffer(1e-9).contains(Point(px, py)), (
            f"point {(la, ln)} not in nearest cell {nearest}"
        )


def test_collinear_points_do_not_crash():
    # Three collinear centroids — must not raise; returns one list per node.
    centroids = [(5.0, 2.0), (5.0, 5.0), (5.0, 8.0)]
    cells = compute_voronoi_cells(centroids, SQUARE)
    assert len(cells) == 3


def test_duplicate_points_do_not_crash():
    centroids = [(5.0, 5.0), (5.0, 5.0)]
    cells = compute_voronoi_cells(centroids, SQUARE)
    assert len(cells) == 2


def test_node_outside_domain_gets_empty_or_small():
    centroids = [(5.0, 5.0), (50.0, 50.0)]  # second far outside
    cells = compute_voronoi_cells(centroids, SQUARE)
    assert len(cells) == 2
    # The inside node covers (almost) the whole domain.
    assert _poly_area_latlng(cells[0]) == pytest.approx(_domain_area(), rel=0.05)
    # The outside node contributes no real area.
    assert _poly_area_latlng(cells[1]) == pytest.approx(0.0, abs=1e-6)
