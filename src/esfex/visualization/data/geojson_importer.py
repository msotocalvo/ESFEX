"""Import GeoJSON features into GUI state with snapping and validation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from esfex.visualization.data.gui_model import (
    EndpointRef,
    GeoPoint,
    GuiDevelopmentZone,
    GuiNode,
    GuiSystemState,
    GuiTransmissionLine,
    VisualStyle,
)


@dataclass
class ImportResult:
    """Result of a GeoJSON import operation."""

    nodes_added: int = 0
    lines_added: int = 0
    zones_added: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    r = 6371.0
    la1, lo1 = math.radians(lat1), math.radians(lng1)
    la2, lo2 = math.radians(lat2), math.radians(lng2)
    dlat = la2 - la1
    dlng = lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _find_nearest_node(
    lat: float, lng: float, nodes: list[GuiNode],
) -> tuple[int | None, float]:
    """Return ``(index, distance_km)`` of the nearest node by centroid.

    Nodes left at the ``(0, 0)`` default centroid are treated as having no
    location and are ignored, so a freshly-created abstract node does not act
    as a spurious snap target. Returns ``(None, inf)`` when no node has a
    usable centroid.
    """
    best_idx: int | None = None
    best_dist = float("inf")
    for n in nodes:
        if n.centroid_lat == 0.0 and n.centroid_lng == 0.0:
            continue
        d = _haversine_km(lat, lng, n.centroid_lat, n.centroid_lng)
        if d < best_dist:
            best_idx, best_dist = n.index, d
    return best_idx, best_dist


def import_geojson(
    state: GuiSystemState,
    geojson_path: str | Path,
    snap_threshold_km: float = 5.0,
) -> ImportResult:
    """Import a GeoJSON file into the state.

    Feature mapping:
    - Point → new node (if no existing node within threshold)
    - LineString → transmission line (endpoints snapped to nearest node)
    - Polygon → development zone

    Parameters
    ----------
    state : GuiSystemState
        State to modify in place.
    geojson_path : str or Path
        Path to GeoJSON file.
    snap_threshold_km : float
        Maximum distance (km) for snapping endpoints to existing nodes.
        Floating endpoints beyond this threshold generate a warning.
    """
    result = ImportResult()

    with open(geojson_path, encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    if not features and data.get("type") == "Feature":
        features = [data]
    elif not features and data.get("geometry"):
        features = [data]

    if not features:
        result.errors.append("No features found in GeoJSON file")
        return result

    # Pass 1: Import Point features as nodes
    for feat in features:
        try:
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            geom_type = geom.get("type", "")

            if geom_type != "Point":
                continue
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                result.warnings.append("Point feature with invalid coordinates, skipped")
                continue
            lng, lat = coords[0], coords[1]  # GeoJSON is [lng, lat]
            name = props.get("name", f"Node {len(state.nodes)}")

            # Check if near an existing node
            nearest_idx, nearest_dist = _find_nearest_node(lat, lng, state.nodes)
            if nearest_idx is not None and nearest_dist < snap_threshold_km:
                result.warnings.append(
                    f"Point '{name}' at ({lat:.4f}, {lng:.4f}) is within "
                    f"{nearest_dist:.1f} km of existing Node {nearest_idx}, skipped"
                )
                continue

            idx = len(state.nodes)
            state.nodes.append(GuiNode(
                index=idx,
                name=name,
                centroid_lat=lat,
                centroid_lng=lng,
            ))
            result.nodes_added += 1
        except Exception as exc:
            result.warnings.append(f"Point feature error: {exc}")

    # Pass 2: Import LineString features as transmission lines
    for feat in features:
        try:
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            geom_type = geom.get("type", "")

            if geom_type != "LineString":
                continue
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                result.warnings.append("LineString with fewer than 2 points, skipped")
                continue

            # Snap endpoints to nearest nodes
            lng1, lat1 = coords[0][0], coords[0][1]
            lng2, lat2 = coords[-1][0], coords[-1][1]

            from_idx, from_dist = _find_nearest_node(lat1, lng1, state.nodes)
            to_idx, to_dist = _find_nearest_node(lat2, lng2, state.nodes)

            if from_idx is None or from_dist > snap_threshold_km:
                result.warnings.append(
                    f"LineString start ({lat1:.4f}, {lng1:.4f}): no node within "
                    f"{snap_threshold_km} km (nearest: {from_dist:.1f} km), skipped"
                )
                continue
            if to_idx is None or to_dist > snap_threshold_km:
                result.warnings.append(
                    f"LineString end ({lat2:.4f}, {lng2:.4f}): no node within "
                    f"{snap_threshold_km} km (nearest: {to_dist:.1f} km), skipped"
                )
                continue
            if from_idx == to_idx:
                result.warnings.append(
                    f"LineString endpoints snap to same node ({from_idx}), skipped"
                )
                continue

            capacity = props.get("capacity_mw", 100.0)
            # Intermediate waypoints (skip first and last which are endpoints)
            waypoints = []
            if len(coords) > 2:
                for c in coords[1:-1]:
                    waypoints.append(GeoPoint(c[1], c[0]))

            lid = f"line_{state._next_line_id}"
            state._next_line_id += 1
            state.transmission_lines.append(GuiTransmissionLine(
                line_id=lid,
                from_node=from_idx,
                to_node=to_idx,
                capacity_mw=float(capacity),
                waypoints=waypoints,
                from_endpoint=EndpointRef("node", str(from_idx)),
                to_endpoint=EndpointRef("node", str(to_idx)),
            ))
            result.lines_added += 1
        except Exception as exc:
            result.warnings.append(f"LineString feature error: {exc}")

    # Pass 3: Import Polygon features as development zones
    for feat in features:
        try:
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            geom_type = geom.get("type", "")

            if geom_type != "Polygon":
                continue
            rings = geom.get("coordinates", [])
            if not rings or not rings[0]:
                result.warnings.append("Polygon with no coordinates, skipped")
                continue

            outer_ring = rings[0]
            polygon = [GeoPoint(c[1], c[0]) for c in outer_ring]  # [lng,lat] -> GeoPoint
            name = props.get("name", f"Zone {len(state.development_zones)}")
            technology = props.get("technology", "Solar")
            max_cap = props.get("max_capacity_mw", None)

            state.development_zones.append(GuiDevelopmentZone(
                name=name,
                technology=technology,
                polygon=polygon,
                max_capacity_mw=float(max_cap) if max_cap is not None else None,
                style=VisualStyle(
                    color=props.get("color", None),
                    opacity=float(props["opacity"]) if "opacity" in props else 0.15,
                ),
            ))
            result.zones_added += 1
        except Exception as exc:
            result.warnings.append(f"Polygon feature error: {exc}")

    return result
