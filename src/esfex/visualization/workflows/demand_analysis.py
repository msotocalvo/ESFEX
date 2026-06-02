"""Demand distribution analysis: building classification and spatial clustering.

Provides utilities for:
1. Classifying buildings by type (residential/commercial/industrial) based on
   footprint area and optional floor metadata.
2. Spatially clustering classified buildings using DBSCAN, KMeans, or
   Agglomerative clustering to derive demand fractions for busbars.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# 10-colour categorical palette for cluster visualisation
CLUSTER_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
]


@dataclass
class BuildingTypeRule:
    """Rule for classifying a building based on footprint area and floors."""

    name: str                       # e.g. "Residential"
    area_min_m2: float = 0.0
    area_max_m2: float = math.inf
    weight_per_m2: float = 0.05
    min_floors: int = 0             # 0 = disabled
    max_floors: int = 999
    color: str = "#3498db"


DEFAULT_RULES: list[BuildingTypeRule] = [
    BuildingTypeRule(
        name="Residential",
        area_min_m2=30.0,
        area_max_m2=300.0,
        weight_per_m2=0.05,
        color="#3498db",
    ),
    BuildingTypeRule(
        name="Commercial",
        area_min_m2=300.0,
        area_max_m2=2000.0,
        weight_per_m2=0.08,
        color="#e67e22",
    ),
    BuildingTypeRule(
        name="Industrial",
        area_min_m2=2000.0,
        area_max_m2=math.inf,
        weight_per_m2=0.12,
        color="#e74c3c",
    ),
]


def classify_buildings(
    gdf,
    rules: list[BuildingTypeRule],
    fallback_weight_per_m2: float = 0.03,
):
    """Classify buildings and compute relative demand weights.

    Weights are proportional values used to distribute a node's actual demand
    among its busbars.  They are **not** absolute demand figures.

    Parameters
    ----------
    gdf : GeoDataFrame
        Must contain ``footprint_area_m2`` column; optionally ``num_floors``.
    rules : list[BuildingTypeRule]
        Ordered classification rules (first match wins).
    fallback_weight_per_m2 : float
        Weight density for buildings that match no rule.

    Returns
    -------
    GeoDataFrame
        Copy with added columns: ``building_type``, ``demand_weight``,
        ``rule_color``.
    """
    result = gdf.copy()
    result["building_type"] = "Unclassified"
    result["demand_weight"] = 0.0
    result["rule_color"] = "#95a5a6"  # grey fallback

    areas = result["footprint_area_m2"].values
    has_floors = "num_floors" in result.columns
    floors = result["num_floors"].values if has_floors else None

    types = result["building_type"].values.copy()
    weights = result["demand_weight"].values.copy()
    colors = result["rule_color"].values.copy()

    for i in range(len(result)):
        area = areas[i]
        nf = int(floors[i]) if (has_floors and pd.notna(floors[i])) else 0
        matched = False

        for rule in rules:
            # Floor-based check (if building has floor data and rule uses floors)
            if nf > 0 and rule.min_floors > 0:
                if rule.min_floors <= nf <= rule.max_floors:
                    if rule.area_min_m2 <= area < rule.area_max_m2:
                        types[i] = rule.name
                        weights[i] = area * rule.weight_per_m2
                        colors[i] = rule.color
                        matched = True
                        break

            # Area-only check
            if not matched and rule.area_min_m2 <= area < rule.area_max_m2:
                # If rule has floor constraint but building has no floor data, skip
                if rule.min_floors > 0 and nf == 0:
                    continue
                types[i] = rule.name
                weights[i] = area * rule.weight_per_m2
                colors[i] = rule.color
                matched = True
                break

        if not matched:
            weights[i] = area * fallback_weight_per_m2

    result["building_type"] = types
    result["demand_weight"] = weights
    result["rule_color"] = colors
    return result


def compute_classification_summary(gdf) -> pd.DataFrame:
    """Summarise classified buildings by type.

    Returns DataFrame with columns: building_type, count, total_area_m2,
    total_weight.
    """
    if gdf is None or gdf.empty:
        return pd.DataFrame(
            columns=["building_type", "count", "total_area_m2", "total_weight"]
        )
    return (
        gdf.groupby("building_type")
        .agg(
            count=("building_type", "size"),
            total_area_m2=("footprint_area_m2", "sum"),
            total_weight=("demand_weight", "sum"),
        )
        .reset_index()
        .sort_values("count", ascending=False)
    )


class ClusteringWorker(QThread):
    """Run spatial clustering on classified buildings in a background thread.

    Emits
    -----
    finished(clustered_gdf, summary_df)
        clustered_gdf has added column ``cluster_id``.
        summary_df has: cluster_id, count, total_demand_kw, centroid_lat,
        centroid_lng, demand_fraction, color.
    error(str)
        Error message if clustering fails.
    progress(int, str)
        Percent + status message.
    """

    finished = Signal(object, object)  # clustered_gdf, summary_df
    error = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        gdf,
        algorithm: str = "dbscan",
        params: Optional[dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._gdf = gdf
        self._algorithm = algorithm.lower()
        self._params = params or {}

    # Maximum buildings for direct clustering.  Above this threshold,
    # buildings are pre-aggregated into a spatial grid to avoid O(n²)
    # memory usage in algorithms like Agglomerative/DBSCAN.
    _MAX_DIRECT = 50_000

    def run(self):
        try:
            self.progress.emit(10, "Projecting to UTM...")
            gdf = self._gdf.copy()

            # Project to UTM for metric distances
            utm_crs = gdf.estimate_utm_crs()
            gdf_utm = gdf.to_crs(utm_crs)

            n_buildings = len(gdf_utm)
            use_grid = n_buildings > self._MAX_DIRECT

            if use_grid:
                self.progress.emit(
                    20,
                    f"Pre-aggregating {n_buildings:,} buildings into "
                    f"grid cells (too many for direct clustering)...",
                )
                grid_df, grid_coords = self._grid_aggregate(gdf_utm)
                self.progress.emit(
                    30,
                    f"Clustering {len(grid_df)} grid cells "
                    f"({self._algorithm})...",
                )
                grid_labels = self._cluster_coords(grid_coords)
                # Map grid labels back to individual buildings
                grid_df["cluster_id"] = grid_labels
                cell_to_cluster = dict(
                    zip(grid_df["cell_key"], grid_df["cluster_id"]),
                )
                gdf["cluster_id"] = gdf_utm["_cell_key"].map(cell_to_cluster)
                # Fill any unmapped (shouldn't happen) with cluster 0
                gdf["cluster_id"] = (
                    gdf["cluster_id"].fillna(0).astype(int)
                )
            else:
                coords = np.column_stack([
                    gdf_utm.geometry.centroid.x,
                    gdf_utm.geometry.centroid.y,
                ])
                self.progress.emit(30, f"Running {self._algorithm}...")
                labels = self._cluster_coords(coords)
                gdf["cluster_id"] = labels

            self.progress.emit(80, "Computing cluster statistics...")
            summary = self._compute_summary(gdf)

            self.progress.emit(100, f"Done — {summary['cluster_id'].nunique()} clusters")
            self.finished.emit(gdf, summary)

        except Exception as exc:
            logger.exception("ClusteringWorker error")
            self.error.emit(str(exc))

    def _cluster_coords(self, coords: np.ndarray) -> np.ndarray:
        """Route to the selected clustering algorithm."""
        if self._algorithm == "dbscan":
            return self._run_dbscan(coords)
        elif self._algorithm == "kmeans":
            return self._run_kmeans(coords)
        elif self._algorithm == "agglomerative":
            return self._run_agglomerative(coords)
        else:
            raise ValueError(f"Unknown algorithm: {self._algorithm}")

    @staticmethod
    def _grid_aggregate(gdf_utm) -> tuple:
        """Bin buildings into spatial grid cells and aggregate weights.

        Returns ``(grid_df, coords)`` where *grid_df* has one row per
        occupied cell with columns ``cell_key``, ``count``,
        ``total_weight``, ``cx``, ``cy``; and *coords* is a (N, 2) array
        of cell centroids in UTM.
        """
        cx = gdf_utm.geometry.centroid.x.values
        cy = gdf_utm.geometry.centroid.y.values

        # Choose cell size so we get ~5000-10000 cells
        n = len(gdf_utm)
        x_range = cx.max() - cx.min()
        y_range = cy.max() - cy.min()
        target_cells = min(10_000, max(1_000, n // 50))
        cell_size = max(
            math.sqrt(x_range * y_range / target_cells), 50.0,
        )

        # Assign each building to a grid cell
        ix = ((cx - cx.min()) / cell_size).astype(int)
        iy = ((cy - cy.min()) / cell_size).astype(int)
        keys = ix * 1_000_000 + iy  # unique cell key
        gdf_utm["_cell_key"] = keys

        weights = gdf_utm["demand_weight"].values

        # Aggregate per cell
        cell_data: dict[int, list] = {}  # key → [sum_cx, sum_cy, sum_w, count]
        for k, x, y, w in zip(keys, cx, cy, weights):
            if k not in cell_data:
                cell_data[k] = [0.0, 0.0, 0.0, 0]
            cell_data[k][0] += x
            cell_data[k][1] += y
            cell_data[k][2] += w
            cell_data[k][3] += 1

        rows = []
        for k, (sx, sy, sw, cnt) in cell_data.items():
            rows.append({
                "cell_key": k,
                "count": cnt,
                "total_weight": sw,
                "cx": sx / cnt,
                "cy": sy / cnt,
            })

        grid_df = pd.DataFrame(rows)
        coords = np.column_stack([grid_df["cx"].values, grid_df["cy"].values])
        return grid_df, coords

    def _run_dbscan(self, coords: np.ndarray) -> np.ndarray:
        from sklearn.cluster import DBSCAN
        from sklearn.neighbors import NearestNeighbors

        eps = self._params.get("eps", 500)
        min_samples = self._params.get("min_samples", 5)

        db = DBSCAN(eps=eps, min_samples=min_samples)
        labels = db.fit_predict(coords)

        # Assign noise points (-1) to nearest cluster
        noise_mask = labels == -1
        if noise_mask.any() and not noise_mask.all():
            valid_mask = ~noise_mask
            nn = NearestNeighbors(n_neighbors=1)
            nn.fit(coords[valid_mask])
            _, indices = nn.kneighbors(coords[noise_mask])
            labels[noise_mask] = labels[valid_mask][indices.ravel()]

        # If all noise (no clusters formed), put everything in cluster 0
        if (labels == -1).all():
            labels[:] = 0

        return labels

    def _run_kmeans(self, coords: np.ndarray) -> np.ndarray:
        n_clusters = self._params.get("n_clusters", 3)
        n_clusters = min(n_clusters, len(coords))

        if len(coords) > 10_000:
            from sklearn.cluster import MiniBatchKMeans
            km = MiniBatchKMeans(
                n_clusters=n_clusters, random_state=42, batch_size=1024,
            )
        else:
            from sklearn.cluster import KMeans
            km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
        return km.fit_predict(coords)

    def _run_agglomerative(self, coords: np.ndarray) -> np.ndarray:
        from sklearn.cluster import AgglomerativeClustering

        n_clusters = self._params.get("n_clusters", 3)
        n_clusters = min(n_clusters, len(coords))
        linkage = self._params.get("linkage", "ward")

        agg = AgglomerativeClustering(n_clusters=n_clusters, linkage=linkage)
        return agg.fit_predict(coords)

    @staticmethod
    def _compute_summary(gdf) -> pd.DataFrame:
        """Per-cluster statistics with auto-normalized demand fractions.

        Uses coordinate-mean centroids instead of geometric union to
        handle millions of buildings without O(n) memory overhead.
        """
        import geopandas as gpd
        from shapely.geometry import Point

        # Compute centroids in WGS84 using simple coordinate means
        # (fast, O(1) memory per cluster vs union_all which builds
        # a MultiPoint of all geometries).
        centroids = gdf.geometry.centroid
        cx = centroids.x.values
        cy = centroids.y.values
        weights = gdf["demand_weight"].values
        cluster_ids = gdf["cluster_id"].values

        total_weight = weights.sum()

        # Aggregate per cluster using numpy for speed
        unique_ids = np.unique(cluster_ids)
        rows = []
        for cid in unique_ids:
            mask = cluster_ids == cid
            count = int(mask.sum())
            weight = float(weights[mask].sum())
            mean_lng = float(cx[mask].mean())
            mean_lat = float(cy[mask].mean())

            rows.append({
                "cluster_id": int(cid),
                "count": count,
                "total_weight": round(weight, 2),
                "centroid_lat": mean_lat,
                "centroid_lng": mean_lng,
                "demand_fraction": weight / total_weight
                if total_weight > 0
                else 0.0,
                "color": CLUSTER_COLORS[int(cid) % len(CLUSTER_COLORS)],
            })

        df = pd.DataFrame(rows).sort_values("cluster_id").reset_index(drop=True)

        # Auto-normalize fractions to sum exactly 1.0
        frac_sum = df["demand_fraction"].sum()
        if frac_sum > 0:
            df["demand_fraction"] = df["demand_fraction"] / frac_sum

        return df
