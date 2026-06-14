"""Turn an imported GeoAsset (vector GeoJSON) into a workflow study domain.

Workflows (Grid Builder, Solar PV, Wind, …) operate over a spatial domain. Today
the user draws it by hand; this module lets an imported GeoAsset polygon define
it instead — dissolving multi-feature assets into a single boundary.

Pure (no Qt) so it is unit-testable. Only depends on ``shapely`` (already a
project dependency).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LatLng = tuple[float, float]


def _polygon_geoms(geojson: dict) -> list:
    """Collect every Polygon/MultiPolygon as a shapely geometry.

    Accepts a FeatureCollection, a bare Feature, or a bare geometry (mirroring
    ``geojson_importer``). Non-polygon features (points, lines) are skipped.
    """
    from shapely.geometry import shape

    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        geoms_src = [f.get("geometry") for f in geojson.get("features", [])]
    elif gtype == "Feature":
        geoms_src = [geojson.get("geometry")]
    else:
        geoms_src = [geojson]  # bare geometry

    out = []
    for g in geoms_src:
        if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            out.append(shape(g))
        except Exception as exc:  # malformed geometry — skip, don't crash
            logger.warning("Skipping invalid polygon geometry: %s", exc)
    return out


def has_polygon(geojson: dict) -> bool:
    """Cheap check (no shapely) — does this GeoAsset hold any polygon feature?"""
    if not isinstance(geojson, dict):
        return False
    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        geoms = [(f or {}).get("geometry") or {} for f in geojson.get("features", [])]
    elif gtype == "Feature":
        geoms = [geojson.get("geometry") or {}]
    else:
        geoms = [geojson]
    return any(g.get("type") in ("Polygon", "MultiPolygon") for g in geoms)


def geoasset_to_domain_polygon(geojson: dict) -> list[LatLng]:
    """Dissolve all polygons of a GeoAsset into one domain ring ``[(lat, lng)]``.

    All Polygon/MultiPolygon geometries are unioned (``shapely.unary_union``).
    Returns the exterior ring of the result, in ``(lat, lng)`` order (GeoJSON
    stores ``(lng, lat)``; every ESFEX consumer expects ``(lat, lng)``).

    Returns ``[]`` when the asset has no polygon features.

    Limitation (v1): a multi-part dissolved domain (e.g. an archipelago)
    collapses to its single largest exterior ring — holes and disjoint parts
    are dropped, because every downstream consumer's domain contract is a single
    ``list[(lat, lng)]`` ring.
    """
    from shapely import unary_union
    from shapely.geometry import MultiPolygon, Polygon

    geoms = _polygon_geoms(geojson)
    if not geoms:
        return []

    dissolved = unary_union(geoms)
    if isinstance(dissolved, MultiPolygon):
        dissolved = max(dissolved.geoms, key=lambda g: g.area, default=None)
    if not isinstance(dissolved, Polygon) or dissolved.is_empty:
        return []

    coords = list(dissolved.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]  # drop the closing duplicate
    return [(lat, lng) for lng, lat in coords]


def domain_bounds(polygon: list[LatLng]) -> tuple[float, float, float, float]:
    """Bounding box ``(south, west, north, east)`` of a ``(lat, lng)`` ring."""
    lats = [p[0] for p in polygon]
    lngs = [p[1] for p in polygon]
    return (min(lats), min(lngs), max(lats), max(lngs))


def domain_shapely(polygon: list[LatLng]):
    """A shapely ``Polygon`` in ``(lng, lat)`` for GeoDataFrame clipping."""
    from shapely.geometry import Polygon

    return Polygon([(lng, lat) for lat, lng in polygon])
