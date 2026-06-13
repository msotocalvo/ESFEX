"""Voronoi territory cells for network nodes.

A node in ESFEX is a *point* (its centroid), but it is conceptually a spatial
region: every bus is assigned to the node whose centroid is nearest
(:func:`esfex.visualization.data.geo_asset_parser._find_nearest_node_idx`).
That nearest-centroid partition is exactly a Voronoi diagram.  This module
makes that implicit territory explicit so it can be drawn on the map.

The projection used here MUST match the one used for bus assignment — a simple
equirectangular projection ``x = lng * cos(mean_lat)``, ``y = lat`` — otherwise
the drawn cells would disagree with where buses actually land.

The single public function is pure (no Qt, no I/O) so it can be unit-tested in
isolation.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

LatLng = tuple[float, float]


def compute_voronoi_cells(
    centroids: list[LatLng],
    domain_polygon: list[LatLng],
) -> list[list[LatLng]]:
    """Compute each node's Voronoi cell, clipped to the study domain.

    Parameters
    ----------
    centroids:
        ``(lat, lng)`` per node, index-aligned to ``state.nodes``.
    domain_polygon:
        ``(lat, lng)`` ring of the study region (the drawn domain). The cells
        are clipped to this polygon so they tile exactly the area of interest.

    Returns
    -------
    list of polygons, index-aligned to ``centroids``. Each polygon is a list of
    ``(lat, lng)`` vertices (no repeated closing vertex). A node whose cell is
    empty (e.g. a centroid outside the domain) or that cannot be computed gets
    an empty list ``[]`` — callers should simply skip drawing it. The function
    never raises on degenerate input; it logs and returns empties.
    """
    n = len(centroids)
    if n == 0 or len(domain_polygon) < 3:
        return [[] for _ in range(n)]

    # Projection consistent with geo_asset_parser bus assignment.
    mean_lat = sum(lat for lat, _ in centroids) / n
    coslat = math.cos(math.radians(mean_lat)) or 1.0

    def project(p: LatLng) -> tuple[float, float]:
        lat, lng = p
        return (lng * coslat, lat)

    def unproject(x: float, y: float) -> LatLng:
        return (y, x / coslat)

    try:
        from shapely import voronoi_polygons
        from shapely.geometry import MultiPoint, Point, Polygon
    except Exception as exc:  # shapely is a declared dep; be defensive anyway
        logger.warning("shapely unavailable, skipping Voronoi cells: %s", exc)
        return [[] for _ in range(n)]

    domain_proj = Polygon([project(p) for p in domain_polygon])
    if not domain_proj.is_valid:
        domain_proj = domain_proj.buffer(0)  # repair self-intersections
    if domain_proj.is_empty or domain_proj.area <= 0:
        return [[] for _ in range(n)]

    proj_pts = [Point(*project(c)) for c in centroids]

    # Single node: the whole domain is its territory.
    if n == 1:
        return [_rings_of(domain_proj, unproject)]

    try:
        cells = voronoi_polygons(
            MultiPoint(proj_pts), extend_to=domain_proj.envelope
        )
        cell_list = list(getattr(cells, "geoms", [cells]))
    except Exception as exc:
        # Collinear / duplicate generators can trip the diagram. Don't crash
        # the wizard — just skip the overlay.
        logger.warning("Voronoi computation failed (%d nodes): %s", n, exc)
        return [[] for _ in range(n)]

    out: list[list[LatLng]] = []
    for pt in proj_pts:
        cell = None
        for c in cell_list:
            try:
                if c.contains(pt):
                    cell = c
                    break
            except Exception:
                continue
        if cell is None and cell_list:
            # Point on a shared boundary (fp): pick the nearest cell.
            cell = min(cell_list, key=lambda c: c.distance(pt))
        if cell is None:
            out.append([])
            continue
        clipped = cell.intersection(domain_proj)
        if clipped.is_empty:
            out.append([])
        else:
            out.append(_rings_of(clipped, unproject))
    return out


def _rings_of(geom, unproject) -> list[LatLng]:
    """Exterior ring (lat, lng) of a (possibly Multi) polygon — largest part."""
    from shapely.geometry import MultiPolygon, Polygon

    poly = geom
    if isinstance(geom, MultiPolygon):
        poly = max(geom.geoms, key=lambda g: g.area, default=None)
    if not isinstance(poly, Polygon) or poly.is_empty:
        return []
    coords = list(poly.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]  # drop the closing duplicate
    return [unproject(x, y) for x, y in coords]
