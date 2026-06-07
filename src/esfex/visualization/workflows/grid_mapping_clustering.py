"""Spatial clustering for automatic node placement in grid mapping.

Clusters fetched infrastructure features to determine optimal node
locations.  Three criteria are supported:

1. Infrastructure Density — K-means on infrastructure positions
2. Demand Proxy — K-means on ML building footprint centroids
3. Regional Balance — Grid-seeded K-means for uniform coverage

Multiple criteria can be selected simultaneously.  When more than one
criterion is active the worker runs each independently and then merges
the candidate node positions via a consensus K-means pass.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QThread, Signal

from esfex.visualization.workflows.place_naming import name_positions_by_region

logger = logging.getLogger(__name__)


# ── Result ────────────────────────────────────────────────────────────


@dataclass
class ClusterResult:
    """Result of automatic node placement."""

    node_positions: list[tuple[float, float, str]]  # (lat, lng, name)
    n_clusters: int
    criterion_used: str


# ── Helpers ───────────────────────────────────────────────────────────


def _project_to_km(points: np.ndarray) -> np.ndarray:
    """Convert (lat, lng) array to approximate km using cos(mid_lat)."""
    mid_lat = float(np.mean(points[:, 0]))
    cos_lat = math.cos(math.radians(mid_lat))
    return np.column_stack([
        points[:, 0] * 111.32,
        points[:, 1] * 111.32 * cos_lat,
    ])


def _extract_infrastructure_points(
    features: list,
    type_filter: frozenset[str] | None = None,
) -> np.ndarray:
    """Extract (lat, lng) from point features.

    *type_filter* restricts to specific ``feature_type`` values.
    Line and road features are always excluded.
    """
    if type_filter is None:
        type_filter = frozenset({
            "substation", "generator", "battery", "transformer",
            "converter", "fuel_entry", "fuel_storage",
        })
    pts: list[tuple[float, float]] = []
    for f in features:
        if not f.include:
            continue
        if f.feature_type in type_filter:
            pts.append((f.latitude, f.longitude))
    if not pts:
        return np.empty((0, 2))
    return np.array(pts)


def _determine_optimal_k(
    points_km: np.ndarray,
    min_k: int,
    max_k: int,
) -> int:
    """Find optimal cluster count via the elbow method (knee detection).

    Computes K-means inertia for each candidate k, then finds the knee
    point — the k whose inertia is farthest from the line connecting the
    first and last inertia values.  This avoids the low-k bias of the
    silhouette score.
    """
    from sklearn.cluster import KMeans

    n = len(points_km)
    if n <= min_k:
        return max(1, n)

    max_k = min(max_k, n - 1)
    if min_k >= max_k:
        return min_k

    # Compute inertia for each k
    ks: list[int] = []
    inertias: list[float] = []
    for k in range(min_k, max_k + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        km.fit(points_km)
        ks.append(k)
        inertias.append(km.inertia_)

    if len(ks) < 3:
        return ks[0]

    # Knee detection: max perpendicular distance from the line
    # connecting (ks[0], inertias[0]) to (ks[-1], inertias[-1])
    x = np.array(ks, dtype=float)
    y = np.array(inertias, dtype=float)

    # Normalise to [0,1] so both axes contribute equally
    x_norm = (x - x[0]) / max(x[-1] - x[0], 1e-12)
    y_norm = (y - y[-1]) / max(y[0] - y[-1], 1e-12)

    # Line direction: (1, y_norm[-1] - y_norm[0]) ≈ (1, -1)
    dx = x_norm[-1] - x_norm[0]
    dy = y_norm[-1] - y_norm[0]
    line_len = math.sqrt(dx * dx + dy * dy)

    best_idx = 0
    best_dist = -1.0
    for i in range(len(ks)):
        # Perpendicular distance from point i to the line
        px = x_norm[i] - x_norm[0]
        py = y_norm[i] - y_norm[0]
        dist = abs(px * dy - py * dx) / line_len
        if dist > best_dist:
            best_dist = dist
            best_idx = i

    return ks[best_idx]


def _run_kmeans(
    points_km: np.ndarray,
    k: int,
    init: str | np.ndarray = "k-means++",
) -> tuple[np.ndarray, np.ndarray]:
    """Run K-means and return (labels, centroids_km)."""
    from sklearn.cluster import KMeans

    n_init = 10 if isinstance(init, str) else 1
    km = KMeans(n_clusters=k, init=init, n_init=n_init, random_state=42)
    labels = km.fit_predict(points_km)
    return labels, km.cluster_centers_


def _km_centroids_to_latlng(
    centroids_km: np.ndarray,
    mid_lat: float,
) -> list[tuple[float, float]]:
    """Convert km centroids back to (lat, lng)."""
    cos_lat = math.cos(math.radians(mid_lat))
    result: list[tuple[float, float]] = []
    for cy, cx in centroids_km:
        lat = cy / 111.32
        lng = cx / (111.32 * cos_lat) if cos_lat > 0 else 0.0
        result.append((lat, lng))
    return result


def _fetch_building_centroids_ml(
    bounds: tuple[float, float, float, float],
    polygon: list[tuple[float, float]] | None = None,
    progress_callback=None,
) -> list[tuple[float, float]]:
    """Fetch building centroids using ML building footprint databases.

    Tries Overture Maps first, then Microsoft ML, then Google Open
    Buildings.  Returns ``(lat, lng)`` pairs.
    """
    from esfex.visualization.workflows.data_fetchers import BuildingFetcher

    sources = ["overture", "microsoft", "google"]
    gdf = None

    for source in sources:
        try:
            if progress_callback:
                progress_callback(
                    f"Fetching buildings from {source.title()}..."
                )
            # Node clustering only needs a representative sample of
            # building centroids to seed K-means, so cap the pull low —
            # this keeps whole-country regions fast and memory-safe.
            fetcher = BuildingFetcher(source, bounds, max_buildings=300_000)
            if source == "overture":
                gdf = fetcher._fetch_overture()
            elif source == "microsoft":
                gdf = fetcher._fetch_microsoft()
            elif source == "google":
                gdf = fetcher._fetch_google()

            if gdf is not None and len(gdf) > 0:
                logger.info(
                    "Fetched %d buildings from %s", len(gdf), source,
                )
                break
        except Exception as exc:
            logger.warning("Building fetch from %s failed: %s", source, exc)
            continue

    if gdf is None or len(gdf) == 0:
        return []

    # Extract centroids — project to UTM for accuracy, then back to WGS-84
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    try:
        utm_crs = gdf.estimate_utm_crs()
        utm_gdf = gdf.to_crs(utm_crs)
        utm_centroids = utm_gdf.geometry.centroid
        wgs_centroids = utm_centroids.to_crs(epsg=4326)
    except Exception:
        # Fallback: use geographic CRS centroids (small polygons — negligible error)
        wgs_centroids = gdf.geometry.centroid
    centroids: list[tuple[float, float]] = list(zip(
        wgs_centroids.y.tolist(),
        wgs_centroids.x.tolist(),
    ))

    # Filter by polygon if provided
    if polygon and centroids:
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            _point_in_polygon,
        )
        centroids = [
            (lat, lng) for lat, lng in centroids
            if _point_in_polygon(lat, lng, polygon)
        ]

    return centroids


# ── Worker Thread ─────────────────────────────────────────────────────


class NodeClusteringWorker(QThread):
    """Background thread for automatic node placement via clustering.

    Accepts one or more criteria.  When multiple criteria are selected
    each is run independently and the candidate node positions are merged
    via a consensus K-means pass.
    """

    progress = Signal(int, str)
    finished = Signal(object)      # ClusterResult
    error = Signal(str)

    def __init__(
        self,
        features: list,
        criteria: list[str],
        min_nodes: int = 2,
        max_nodes: int = 20,
        bounds: tuple[float, float, float, float] | None = None,
        polygon: list[tuple[float, float]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._features = features
        self._criteria = criteria
        self._min_nodes = max(1, min_nodes)
        self._max_nodes = max(self._min_nodes, max_nodes)
        self._bounds = bounds
        self._polygon = polygon
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _apply_region_names(self, result: ClusterResult) -> ClusterResult:
        """Replace generic node names with OSM region names (best-effort)."""
        if not result.node_positions or self._cancelled:
            return result
        self.progress.emit(95, "Naming nodes from OpenStreetMap...")
        named = name_positions_by_region(
            result.node_positions,
            cancelled=lambda: self._cancelled,
            progress=lambda done, total: self.progress.emit(
                95 + int(5 * done / max(total, 1)),
                f"Naming nodes ({done}/{total})...",
            ),
        )
        return ClusterResult(named, result.n_clusters, result.criterion_used)

    def _finish(self, result: ClusterResult):
        """Apply region names and emit, unless cancelled."""
        if self._cancelled:
            return
        self.finished.emit(self._apply_region_names(result))

    def run(self):
        try:
            dispatch = {
                "infrastructure": self._cluster_infrastructure,
                "demand": self._cluster_demand,
                "regional": self._cluster_regional,
            }

            # Validate criteria
            valid = [c for c in self._criteria if c in dispatch]
            if not valid:
                self.error.emit(
                    f"No valid criteria selected: {self._criteria}"
                )
                return

            # Single criterion — run directly
            if len(valid) == 1:
                result = dispatch[valid[0]]()
                self._finish(result)
                return

            # Multiple criteria — run each, then consensus merge
            sub_results: list[ClusterResult] = []
            step_pct = 70 // len(valid)

            for i, crit in enumerate(valid):
                if self._cancelled:
                    return
                base_pct = i * step_pct
                self.progress.emit(
                    base_pct,
                    f"Running {crit} criterion ({i + 1}/{len(valid)})...",
                )
                result = dispatch[crit]()
                if result.node_positions:
                    sub_results.append(result)

            if self._cancelled:
                return

            if not sub_results:
                self.finished.emit(ClusterResult(
                    [], 0, "+".join(valid),
                ))
                return

            # If only one criterion produced results, use it directly
            if len(sub_results) == 1:
                r = sub_results[0]
                self._finish(ClusterResult(
                    r.node_positions,
                    r.n_clusters,
                    "+".join(valid),
                ))
                return

            # Consensus merge: pool all candidate positions, re-cluster
            self.progress.emit(75, "Merging criteria (consensus clustering)...")
            merged = self._consensus_merge(sub_results, valid)
            self._finish(merged)

        except Exception as exc:
            logger.exception("NodeClusteringWorker error")
            self.error.emit(str(exc))

    def _consensus_merge(
        self,
        sub_results: list[ClusterResult],
        criteria_names: list[str],
    ) -> ClusterResult:
        """Merge multiple clustering results via consensus K-means."""
        # Pool all candidate node positions
        all_positions: list[tuple[float, float]] = []
        for r in sub_results:
            for lat, lng, _name in r.node_positions:
                all_positions.append((lat, lng))

        if not all_positions:
            return ClusterResult([], 0, "+".join(criteria_names))

        raw = np.array(all_positions)

        # Target k = round(mean of individual k values)
        k_values = [r.n_clusters for r in sub_results if r.n_clusters > 0]
        target_k = max(
            self._min_nodes,
            min(self._max_nodes, round(sum(k_values) / len(k_values))),
        )

        # If we have fewer unique positions than target_k, reduce
        unique = np.unique(raw, axis=0)
        if len(unique) <= target_k:
            # Each unique position becomes a node
            named = [
                (float(p[0]), float(p[1]), f"Node {i}")
                for i, p in enumerate(unique)
            ]
            return ClusterResult(
                node_positions=named,
                n_clusters=len(unique),
                criterion_used="+".join(criteria_names),
            )

        mid_lat = float(np.mean(raw[:, 0]))
        projected = _project_to_km(raw)

        self.progress.emit(85, f"Consensus K-means (k={target_k})...")
        _labels, centroids_km = _run_kmeans(projected, target_k)

        positions = _km_centroids_to_latlng(centroids_km, mid_lat)
        named = [
            (lat, lng, f"Node {i}")
            for i, (lat, lng) in enumerate(positions)
        ]

        self.progress.emit(100, f"Consensus complete: {target_k} nodes")
        return ClusterResult(
            node_positions=named,
            n_clusters=target_k,
            criterion_used="+".join(criteria_names),
        )

    # ── Clustering criteria ───────────────────────────────────────

    def _cluster_infrastructure(self) -> ClusterResult:
        """K-means on all infrastructure point positions."""
        self.progress.emit(10, "Extracting infrastructure positions...")
        raw = _extract_infrastructure_points(self._features)
        return self._do_kmeans(raw, "infrastructure")

    def _cluster_demand(self) -> ClusterResult:
        """K-means on ML building footprint centroids."""
        if self._bounds is None:
            logger.warning("No bounds for building query; falling back")
            return self._cluster_infrastructure()

        self.progress.emit(10, "Fetching building footprints...")

        def _prog_cb(msg):
            self.progress.emit(20, msg)

        try:
            centroids = _fetch_building_centroids_ml(
                self._bounds,
                self._polygon,
                progress_callback=_prog_cb,
            )
        except Exception as exc:
            logger.warning("Building fetch failed: %s; falling back", exc)
            self.progress.emit(
                30, "Building fetch failed; using infrastructure...",
            )
            return self._cluster_infrastructure()

        if self._cancelled:
            return ClusterResult([], 0, "demand")

        if len(centroids) < self._min_nodes:
            logger.warning(
                "Only %d buildings found; falling back to infrastructure",
                len(centroids),
            )
            self.progress.emit(
                30, "Too few buildings; using infrastructure...",
            )
            return self._cluster_infrastructure()

        self.progress.emit(
            40, f"Clustering {len(centroids)} building centroids...",
        )
        raw = np.array(centroids)
        return self._do_kmeans(raw, "demand")

    def _cluster_regional(self) -> ClusterResult:
        """Grid-seeded K-means for uniform spatial coverage."""
        self.progress.emit(10, "Preparing regional balance clustering...")
        raw = _extract_infrastructure_points(self._features)
        if len(raw) < 2:
            return self._do_kmeans(raw, "regional")

        projected = _project_to_km(raw)
        mid_lat = float(np.mean(raw[:, 0]))

        # Determine k
        k = _determine_optimal_k(projected, self._min_nodes, self._max_nodes)

        # Generate regular grid as initial centroids
        y_min, x_min = projected.min(axis=0)
        y_max, x_max = projected.max(axis=0)

        # Approximate a grid with k points
        aspect = max((x_max - x_min), 0.01) / max((y_max - y_min), 0.01)
        cols = max(1, int(math.sqrt(k * aspect)))
        rows = max(1, int(math.ceil(k / cols)))
        # Trim to exactly k
        grid_pts: list[tuple[float, float]] = []
        for r in range(rows):
            for c in range(cols):
                if len(grid_pts) >= k:
                    break
                gy = y_min + (r + 0.5) * (y_max - y_min) / rows
                gx = x_min + (c + 0.5) * (x_max - x_min) / cols
                grid_pts.append((gy, gx))
            if len(grid_pts) >= k:
                break
        # Pad if needed
        while len(grid_pts) < k:
            grid_pts.append((
                (y_min + y_max) / 2, (x_min + x_max) / 2,
            ))
        init = np.array(grid_pts[:k])

        self.progress.emit(60, f"Running regional K-means (k={k})...")
        labels, centroids_km = _run_kmeans(projected, k, init=init)

        positions = _km_centroids_to_latlng(centroids_km, mid_lat)
        named = [
            (lat, lng, f"Node {i}") for i, (lat, lng) in enumerate(positions)
        ]
        return ClusterResult(
            node_positions=named,
            n_clusters=k,
            criterion_used="regional",
        )

    # ── Shared K-means pipeline ───────────────────────────────────

    def _do_kmeans(
        self,
        raw_points: np.ndarray,
        criterion: str,
    ) -> ClusterResult:
        """Project, determine k, cluster, convert back."""
        if len(raw_points) == 0:
            return ClusterResult([], 0, criterion)

        if len(raw_points) == 1:
            lat, lng = float(raw_points[0, 0]), float(raw_points[0, 1])
            return ClusterResult(
                node_positions=[(lat, lng, "Node 0")],
                n_clusters=1,
                criterion_used=criterion,
            )

        mid_lat = float(np.mean(raw_points[:, 0]))
        projected = _project_to_km(raw_points)

        self.progress.emit(50, "Determining optimal number of nodes...")
        k = _determine_optimal_k(projected, self._min_nodes, self._max_nodes)

        if self._cancelled:
            return ClusterResult([], 0, criterion)

        self.progress.emit(70, f"Running K-means (k={k})...")
        labels, centroids_km = _run_kmeans(projected, k)

        positions = _km_centroids_to_latlng(centroids_km, mid_lat)
        named = [
            (lat, lng, f"Node {i}") for i, (lat, lng) in enumerate(positions)
        ]

        self.progress.emit(100, f"Clustering complete: {k} nodes")
        return ClusterResult(
            node_positions=named,
            n_clusters=k,
            criterion_used=criterion,
        )
