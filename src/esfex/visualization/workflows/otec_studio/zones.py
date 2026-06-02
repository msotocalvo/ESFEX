# -*- coding: utf-8 -*-
"""OTEC Studio — development-zone clustering (headless).

Clusters feasible OTEC sites into deployable development-zone polygons via
DBSCAN + (concave/convex) hulls, optionally clipped to the ocean. GUI-free
(needs only geopandas / shapely / sklearn); used by the Regional panel.

Moved here from the former ``otec_analysis`` wizard module so the Studio owns
it after the wizard's removal.
"""

from __future__ import annotations


def generate_development_zones(
    results_gdf,
    lcoe_threshold: float,
    buffer_km: float,
    grid_resolution_deg: float = 0.25,
    installation_type: str = "offshore",
):
    """Cluster neighbouring feasible sites into development zone polygons.

    The algorithm works in four stages:

    1. **Spatial clustering** — DBSCAN groups feasible sites that are adjacent
       on the evaluation grid (``eps`` = 1.5 × grid spacing so that diagonal
       neighbours are included).  Isolated single points form their own
       cluster.
    2. **Polygon construction** — For each cluster:
       * 1 site  → circular buffer (radius = ``buffer_km``).
       * 2 sites → convex hull of the two points, buffered.
       * 3+ sites → concave hull (``shapely.concave_hull`` with
         ``ratio=0.3``) buffered; falls back to convex hull if the concave
         hull is degenerate.
    3. **Ocean clipping** (offshore only) — Zone polygons are clipped against
       an ocean mask derived from the data grid.  Every point in
       ``results_gdf`` had valid seawater temperature → it is ocean.  The
       mask is the union of small squares (half-grid-cell) around each ocean
       point, so the resulting zones never extend onto land.
    4. **Zone statistics** — area, number of sites, LCOE statistics, and
       total installable capacity are computed per zone.

    Parameters
    ----------
    results_gdf : GeoDataFrame
        Per-site results with ``lcoe``, ``net_power``, ``feasible`` columns
        and point geometries in EPSG:4326.
    lcoe_threshold : float
        Maximum LCOE ($/kWh) to include in development zones.
    buffer_km : float
        Buffer distance (km) applied around each cluster polygon to smooth
        the boundary and fill small interior gaps.
    grid_resolution_deg : float
        Evaluation-grid resolution in degrees.  Used to derive the DBSCAN
        clustering radius (``eps = 1.5 × grid_resolution × 111 km``).
    installation_type : str
        ``"offshore"`` or ``"onshore"``.  When ``"offshore"``, zone polygons
        are clipped to ocean areas only.

    Returns
    -------
    GeoDataFrame
        Development zone polygons (EPSG:4326) with columns:
        ``zone_id``, ``geometry``, ``area_km2``, ``num_sites``,
        ``avg_lcoe``, ``min_lcoe``, ``total_capacity_mw``.
    """
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import MultiPoint
    from sklearn.cluster import DBSCAN

    _EMPTY_COLS = [
        "zone_id", "geometry", "area_km2", "num_sites",
        "avg_lcoe", "min_lcoe", "total_capacity_mw",
    ]

    feasible = results_gdf[
        (results_gdf["feasible"]) & (results_gdf["lcoe"] <= lcoe_threshold)
    ].copy()

    if feasible.empty:
        return gpd.GeoDataFrame(
            columns=_EMPTY_COLS, geometry="geometry", crs="EPSG:4326",
        )

    # ── Project to metric CRS ──
    utm_crs = feasible.estimate_utm_crs()
    feasible_utm = feasible.to_crs(utm_crs)

    coords_m = np.column_stack([
        feasible_utm.geometry.x,
        feasible_utm.geometry.y,
    ])

    # ── 1. Cluster with DBSCAN ──
    # eps = 1.5 × grid spacing (metres) so diagonals are connected
    eps_m = grid_resolution_deg * 111_320.0 * 1.5
    clustering = DBSCAN(eps=eps_m, min_samples=1).fit(coords_m)
    feasible_utm = feasible_utm.copy()
    feasible_utm["cluster"] = clustering.labels_

    # ── 2. Build ocean mask (offshore only) ──
    # Every point in results_gdf had valid data → it is ocean.
    # Buffer each point by half the grid cell to create square ocean cells,
    # then dissolve into a single ocean polygon.
    ocean_mask = None
    if installation_type == "offshore":
        half_cell_m = grid_resolution_deg * 111_320.0 / 2.0
        all_ocean_utm = results_gdf.to_crs(utm_crs)
        ocean_mask = all_ocean_utm.geometry.buffer(
            half_cell_m, cap_style="square",
        ).union_all()

    # ── 3. Build polygon per cluster ──
    buffer_m = buffer_km * 1000.0
    zones: list[dict] = []

    for cluster_id in sorted(feasible_utm["cluster"].unique()):
        if cluster_id == -1:
            continue  # noise (shouldn't happen with min_samples=1)

        members = feasible_utm[feasible_utm["cluster"] == cluster_id]
        n = len(members)
        points = MultiPoint(list(members.geometry))

        if n == 1:
            zone_geom = points.buffer(buffer_m)
        elif n == 2:
            zone_geom = points.convex_hull.buffer(buffer_m)
        else:
            # Concave hull gives a tighter polygon that follows the shape
            try:
                from shapely import concave_hull
                hull = concave_hull(points, ratio=0.3)
            except (ImportError, Exception):
                hull = points.convex_hull
            # Fall back if degenerate (line or point)
            if hull.is_empty or hull.geom_type in ("Point", "LineString"):
                hull = points.convex_hull
            zone_geom = hull.buffer(buffer_m)

        if zone_geom.is_empty:
            continue

        # Clip to ocean if offshore
        if ocean_mask is not None:
            zone_geom = zone_geom.intersection(ocean_mask)
            if zone_geom.is_empty:
                continue

        zones.append(
            {
                "zone_id": f"otec_zone_{cluster_id}",
                "geometry": zone_geom,
                "area_km2": zone_geom.area / 1e6,
                "num_sites": n,
                "avg_lcoe": float(members["lcoe"].mean()),
                "min_lcoe": float(members["lcoe"].min()),
                "total_capacity_mw": float(members["net_power"].sum() / 1000),
            }
        )

    if not zones:
        return gpd.GeoDataFrame(
            columns=_EMPTY_COLS, geometry="geometry", crs="EPSG:4326",
        )

    zones_gdf = gpd.GeoDataFrame(zones, crs=utm_crs).to_crs("EPSG:4326")
    return zones_gdf
