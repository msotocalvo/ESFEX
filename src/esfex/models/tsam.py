"""
Time-Series Aggregation Method (TSAM) for representative period selection.

Replaces peak-demand-based representative day selection with data-driven
clustering (k-medoids or k-means) that produces weighted representative
periods with optional inter-period SOC linking for seasonal storage.

References:
    Nahmmacher et al. (2016). Carpe diem: A novel approach to select
    representative days for long-term power system planning models.
    Energy, 112, 430-442.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TSAMResult:
    """Result of TSAM clustering.

    Attributes:
        period_start_hours: Start hour index (0-based) for each representative
            period within the annual demand array.
        period_weights: Number of original periods each representative period
            stands for (cluster sizes). sum(weights) == total_periods.
        chronological_order: Indices into period_start_hours sorted by their
            position in the year (ascending day-of-year).
        num_periods: Number of representative periods (K).
    """

    period_start_hours: list[int]
    period_weights: list[float]
    chronological_order: list[int]
    num_periods: int


def compute_tsam_periods(
    demand: np.ndarray,
    num_periods: int = 10,
    method: str = "kmedoids",
    period_length_hours: int = 24,
    availability: dict[str, np.ndarray] | None = None,
    ensure_peak_period: bool = True,
) -> TSAMResult:
    """Compute representative periods using time-series aggregation.

    Args:
        demand: Annual demand array (timesteps x nodes). Typically 8760 x N
            or aggregated resolution (e.g. 1460 x N for 6h resolution).
        num_periods: Number of representative periods (K). Must be >= 2.
        method: Clustering method - "kmedoids" or "kmeans".
        period_length_hours: Timesteps per period (24 for daily periods).
        availability: Optional dict of generator availability profiles,
            keyed by name, each (timesteps x nodes). Concatenated as
            additional clustering features.
        ensure_peak_period: When True (default), the medoid of the cluster
            that contains the annual peak demand day is replaced with that
            peak day itself. This guarantees the peak hour is represented
            in the operational dispatch, preventing the LOLE-2046 failure
            mode where vanilla kmedoids/kmeans selects a "typical" shoulder
            day and undershoots the system's actual peak demand. Cluster
            weights are unaffected (only the representative changes).

    Returns:
        TSAMResult with period start hours, weights, and chronological order.
    """
    total_timesteps = demand.shape[0]
    num_possible_periods = total_timesteps // period_length_hours

    if num_possible_periods < num_periods:
        logger.warning(
            f"Requested {num_periods} periods but only {num_possible_periods} "
            f"possible. Using {num_possible_periods}."
        )
        num_periods = num_possible_periods

    if num_periods < 2:
        # Degenerate case: single period covers entire year
        return TSAMResult(
            period_start_hours=[0],
            period_weights=[float(num_possible_periods)],
            chronological_order=[0],
            num_periods=1,
        )

    # --- Build feature matrix: (num_possible_periods, features_per_period) ---
    n_nodes = demand.shape[1]
    features_per_period = period_length_hours * n_nodes

    # Reshape demand into period blocks
    demand_blocks = demand[: num_possible_periods * period_length_hours, :]
    demand_features = demand_blocks.reshape(num_possible_periods, features_per_period)

    # Optionally add availability profiles as features
    if availability:
        avail_features_list = []
        for name, avail in availability.items():
            avail_trimmed = avail[: num_possible_periods * period_length_hours, :]
            avail_block = avail_trimmed.reshape(num_possible_periods, -1)
            avail_features_list.append(avail_block)
        if avail_features_list:
            avail_features = np.hstack(avail_features_list)
            # Normalize availability features to same scale as demand
            avail_std = avail_features.std()
            demand_std = demand_features.std()
            if avail_std > 0 and demand_std > 0:
                scale_factor = demand_std / avail_std
                avail_features = avail_features * scale_factor
            demand_features = np.hstack([demand_features, avail_features])

    # Standardize features (zero mean, unit variance)
    feature_mean = demand_features.mean(axis=0)
    feature_std = demand_features.std(axis=0)
    feature_std[feature_std == 0] = 1.0  # Avoid division by zero
    features_normalized = (demand_features - feature_mean) / feature_std

    # --- Clustering ---
    if method == "kmedoids":
        labels, medoid_indices = _cluster_kmedoids(features_normalized, num_periods)
    elif method == "kmeans":
        labels, medoid_indices = _cluster_kmeans(features_normalized, num_periods)
    else:
        raise ValueError(f"Unknown TSAM method: {method}. Use 'kmedoids' or 'kmeans'.")

    # --- Extreme-period augmentation (Lombardi et al. 2020 style) ---
    # The clustering above optimizes for cluster compactness, which biases the
    # representatives toward the "typical" centre of each cluster. When the
    # annual peak day sits at the tail of one cluster (the LOLE-2046 pattern),
    # vanilla kmedoids/kmeans picks a shoulder day and the operational LP
    # never sees the actual system peak — yielding artificial LOLE.
    # Fix: identify the peak-demand period across the year, find which
    # cluster owns it, and substitute that cluster's medoid with the
    # peak-period index. Weights remain unchanged (labels untouched).
    if ensure_peak_period:
        # Per-period peak demand (max over all hours × nodes within the period).
        period_peaks = demand_blocks.reshape(
            num_possible_periods, period_length_hours, n_nodes
        ).max(axis=(1, 2))
        peak_period_idx = int(np.argmax(period_peaks))
        if peak_period_idx not in medoid_indices:
            peak_label = int(labels[peak_period_idx])
            medoid_indices = list(medoid_indices)
            medoid_indices[peak_label] = peak_period_idx

    # --- Compute weights (cluster sizes) ---
    weights = []
    for k in range(num_periods):
        cluster_size = int(np.sum(labels == k))
        weights.append(float(max(cluster_size, 1)))

    # --- Map medoid indices to start hours ---
    period_start_hours = [int(idx * period_length_hours) for idx in medoid_indices]

    # --- Chronological ordering (sort by day-of-year) ---
    sorted_indices = sorted(range(num_periods), key=lambda i: medoid_indices[i])
    chronological_order = sorted_indices

    logger.info(
        f"TSAM: {num_periods} representative periods selected via {method}. "
        f"Weights: {weights} (sum={sum(weights):.0f})"
    )

    return TSAMResult(
        period_start_hours=period_start_hours,
        period_weights=weights,
        chronological_order=chronological_order,
        num_periods=num_periods,
    )


def _cluster_kmedoids(
    features: np.ndarray, n_clusters: int
) -> tuple[np.ndarray, list[int]]:
    """K-medoids clustering with sklearn_extra, falling back to manual implementation."""
    try:
        from sklearn_extra.cluster import KMedoids

        kmed = KMedoids(n_clusters=n_clusters, metric="euclidean", random_state=42)
        labels = kmed.fit_predict(features)
        medoid_indices = list(kmed.medoid_indices_)
        return labels, medoid_indices
    except ImportError:
        logger.info("sklearn_extra not available, using manual k-medoids (PAM).")
        return _kmedoids_pam(features, n_clusters)


def _kmedoids_pam(
    features: np.ndarray, n_clusters: int, max_iter: int = 100
) -> tuple[np.ndarray, list[int]]:
    """Simple PAM (Partitioning Around Medoids) implementation.

    Used as fallback when sklearn_extra is not installed.
    """
    n_samples = features.shape[0]

    # Compute pairwise distance matrix
    from scipy.spatial.distance import cdist

    dist_matrix = cdist(features, features, metric="euclidean")

    # Initialize: select medoids greedily (first = point with min total distance)
    total_dists = dist_matrix.sum(axis=1)
    medoids = [int(np.argmin(total_dists))]

    for _ in range(1, n_clusters):
        # Select point that maximally reduces cost
        best_reduction = -np.inf
        best_candidate = -1
        current_dists = np.min(dist_matrix[:, medoids], axis=1)
        for candidate in range(n_samples):
            if candidate in medoids:
                continue
            new_dists = np.minimum(current_dists, dist_matrix[:, candidate])
            reduction = current_dists.sum() - new_dists.sum()
            if reduction > best_reduction:
                best_reduction = reduction
                best_candidate = candidate
        medoids.append(best_candidate)

    # Assign labels and refine (swap step)
    for _ in range(max_iter):
        # Assign each point to nearest medoid
        dists_to_medoids = dist_matrix[:, medoids]
        labels = np.argmin(dists_to_medoids, axis=1)

        # Try to swap each medoid with each non-medoid
        improved = False
        for k in range(n_clusters):
            cluster_mask = labels == k
            cluster_indices = np.where(cluster_mask)[0]
            if len(cluster_indices) == 0:
                continue
            # Find best medoid within cluster
            cluster_dists = dist_matrix[np.ix_(cluster_indices, cluster_indices)]
            intra_sums = cluster_dists.sum(axis=1)
            best_in_cluster = cluster_indices[np.argmin(intra_sums)]
            if best_in_cluster != medoids[k]:
                medoids[k] = int(best_in_cluster)
                improved = True

        if not improved:
            break

    # Final assignment
    dists_to_medoids = dist_matrix[:, medoids]
    labels = np.argmin(dists_to_medoids, axis=1)

    return labels, medoids


def _cluster_kmeans(
    features: np.ndarray, n_clusters: int
) -> tuple[np.ndarray, list[int]]:
    """K-means clustering, selecting closest-to-centroid as representative."""
    from scipy.cluster.vq import kmeans2

    centroids, labels = kmeans2(features, n_clusters, minit="++", seed=42)

    # For each cluster, find the sample closest to the centroid
    from scipy.spatial.distance import cdist

    medoid_indices = []
    for k in range(n_clusters):
        cluster_mask = labels == k
        cluster_indices = np.where(cluster_mask)[0]
        if len(cluster_indices) == 0:
            # Empty cluster: use nearest point to centroid overall
            dists = cdist(features, centroids[k : k + 1]).ravel()
            medoid_indices.append(int(np.argmin(dists)))
        else:
            cluster_features = features[cluster_indices]
            dists = cdist(cluster_features, centroids[k : k + 1]).ravel()
            medoid_indices.append(int(cluster_indices[np.argmin(dists)]))

    return labels, medoid_indices
