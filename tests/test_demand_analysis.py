"""
Tests for esfex.visualization.workflows.demand_analysis module.

Covers the following public functions and classes:
- BuildingTypeRule (dataclass defaults and field validation)
- DEFAULT_RULES (expected default rule set)
- classify_buildings (classification + weight computation)
- compute_classification_summary (per-type aggregation)
- ClusteringWorker._compute_summary (static cluster statistics)
"""

import math

import numpy as np
import pandas as pd
import pytest

gpd = pytest.importorskip("geopandas")
pytest.importorskip("shapely")
from shapely.geometry import Point, Polygon

from esfex.visualization.workflows.demand_analysis import (
    CLUSTER_COLORS,
    DEFAULT_RULES,
    BuildingTypeRule,
    ClusteringWorker,
    classify_buildings,
    compute_classification_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_buildings_gdf(areas, floors=None, crs="EPSG:4326"):
    """Create a minimal GeoDataFrame of buildings with given footprint areas.

    Each building is represented by a small square polygon centred at a
    distinct longitude along the equator so that spatial operations work.
    """
    geoms = []
    for i, area in enumerate(areas):
        side = math.sqrt(area) / 111320  # approximate degrees for area in m²
        cx, cy = i * 0.01, 0.0
        geoms.append(Polygon([
            (cx - side / 2, cy - side / 2),
            (cx + side / 2, cy - side / 2),
            (cx + side / 2, cy + side / 2),
            (cx - side / 2, cy + side / 2),
        ]))
    data = {"footprint_area_m2": areas, "geometry": geoms}
    if floors is not None:
        data["num_floors"] = floors
    return gpd.GeoDataFrame(data, crs=crs)


# ---------------------------------------------------------------------------
# BuildingTypeRule
# ---------------------------------------------------------------------------


class TestBuildingTypeRule:
    """Tests for the BuildingTypeRule dataclass."""

    def test_default_values(self):
        rule = BuildingTypeRule(name="Test")
        assert rule.name == "Test"
        assert rule.area_min_m2 == 0.0
        assert rule.area_max_m2 == math.inf
        assert rule.weight_per_m2 == 0.05
        assert rule.min_floors == 0
        assert rule.max_floors == 999
        assert rule.color == "#3498db"

    def test_custom_values(self):
        rule = BuildingTypeRule(
            name="Industrial",
            area_min_m2=500.0,
            area_max_m2=10000.0,
            weight_per_m2=0.12,
            min_floors=1,
            max_floors=3,
            color="#e74c3c",
        )
        assert rule.area_min_m2 == 500.0
        assert rule.area_max_m2 == 10000.0
        assert rule.weight_per_m2 == 0.12
        assert rule.min_floors == 1
        assert rule.max_floors == 3


# ---------------------------------------------------------------------------
# DEFAULT_RULES
# ---------------------------------------------------------------------------


class TestDefaultRules:
    """Tests for the DEFAULT_RULES list."""

    def test_three_default_rules(self):
        assert len(DEFAULT_RULES) == 3

    def test_rule_names(self):
        names = [r.name for r in DEFAULT_RULES]
        assert names == ["Residential", "Commercial", "Industrial"]

    def test_rules_cover_all_areas(self):
        """Default rules cover the full area range without gaps."""
        assert DEFAULT_RULES[0].area_min_m2 == 30.0
        assert DEFAULT_RULES[0].area_max_m2 == 300.0
        assert DEFAULT_RULES[1].area_min_m2 == 300.0
        assert DEFAULT_RULES[1].area_max_m2 == 2000.0
        assert DEFAULT_RULES[2].area_min_m2 == 2000.0
        assert DEFAULT_RULES[2].area_max_m2 == math.inf

    def test_weight_densities_increase(self):
        """Weight per m² increases from residential to industrial."""
        densities = [r.weight_per_m2 for r in DEFAULT_RULES]
        assert densities[0] < densities[1] < densities[2]


# ---------------------------------------------------------------------------
# classify_buildings — basic classification
# ---------------------------------------------------------------------------


class TestClassifyBuildingsBasic:
    """Tests for classify_buildings basic behaviour."""

    def test_returns_geodataframe(self):
        gdf = _make_buildings_gdf([100.0, 500.0, 3000.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_adds_required_columns(self):
        gdf = _make_buildings_gdf([100.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert "building_type" in result.columns
        assert "demand_weight" in result.columns
        assert "rule_color" in result.columns

    def test_does_not_modify_original(self):
        gdf = _make_buildings_gdf([100.0, 500.0])
        original_cols = set(gdf.columns)
        classify_buildings(gdf, DEFAULT_RULES)
        assert set(gdf.columns) == original_cols

    def test_preserves_row_count(self):
        gdf = _make_buildings_gdf([50.0, 150.0, 1000.0, 5000.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert len(result) == len(gdf)


# ---------------------------------------------------------------------------
# classify_buildings — classification logic
# ---------------------------------------------------------------------------


class TestClassifyBuildingsLogic:
    """Tests for classification rule matching."""

    def test_residential_classification(self):
        gdf = _make_buildings_gdf([100.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Residential"

    def test_commercial_classification(self):
        gdf = _make_buildings_gdf([500.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Commercial"

    def test_industrial_classification(self):
        gdf = _make_buildings_gdf([5000.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Industrial"

    def test_unclassified_building(self):
        """Building below all rules stays unclassified."""
        gdf = _make_buildings_gdf([10.0])  # Below 30 m² residential minimum
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Unclassified"

    def test_mixed_classification(self):
        areas = [50.0, 100.0, 500.0, 1500.0, 3000.0]
        gdf = _make_buildings_gdf(areas)
        result = classify_buildings(gdf, DEFAULT_RULES)
        types = result["building_type"].tolist()
        assert types[0] == "Residential"
        assert types[1] == "Residential"
        assert types[2] == "Commercial"
        assert types[3] == "Commercial"
        assert types[4] == "Industrial"

    def test_boundary_value_lower_inclusive(self):
        """area_min_m2 is inclusive."""
        gdf = _make_buildings_gdf([300.0])  # Exactly at Commercial lower bound
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Commercial"

    def test_boundary_value_upper_exclusive(self):
        """area_max_m2 is exclusive."""
        gdf = _make_buildings_gdf([299.99])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["building_type"].iloc[0] == "Residential"

    def test_first_matching_rule_wins(self):
        """When rules overlap, the first match takes precedence."""
        overlapping_rules = [
            BuildingTypeRule(name="RuleA", area_min_m2=0.0, area_max_m2=500.0),
            BuildingTypeRule(name="RuleB", area_min_m2=100.0, area_max_m2=1000.0),
        ]
        gdf = _make_buildings_gdf([200.0])
        result = classify_buildings(gdf, overlapping_rules)
        assert result["building_type"].iloc[0] == "RuleA"


# ---------------------------------------------------------------------------
# classify_buildings — weight computation
# ---------------------------------------------------------------------------


class TestClassifyBuildingsWeights:
    """Tests for demand weight computation."""

    def test_weight_equals_area_times_density(self):
        """Weight = footprint_area_m2 × weight_per_m2."""
        gdf = _make_buildings_gdf([200.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        expected = 200.0 * 0.05  # Residential weight_per_m2
        assert result["demand_weight"].iloc[0] == pytest.approx(expected)

    def test_commercial_weight(self):
        gdf = _make_buildings_gdf([600.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        expected = 600.0 * 0.08  # Commercial weight_per_m2
        assert result["demand_weight"].iloc[0] == pytest.approx(expected)

    def test_industrial_weight(self):
        gdf = _make_buildings_gdf([4000.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        expected = 4000.0 * 0.12  # Industrial weight_per_m2
        assert result["demand_weight"].iloc[0] == pytest.approx(expected)

    def test_unclassified_uses_fallback_weight(self):
        gdf = _make_buildings_gdf([10.0])
        result = classify_buildings(gdf, DEFAULT_RULES, fallback_weight_per_m2=0.03)
        expected = 10.0 * 0.03
        assert result["demand_weight"].iloc[0] == pytest.approx(expected)

    def test_custom_fallback_weight(self):
        gdf = _make_buildings_gdf([15.0])
        result = classify_buildings(gdf, DEFAULT_RULES, fallback_weight_per_m2=0.10)
        expected = 15.0 * 0.10
        assert result["demand_weight"].iloc[0] == pytest.approx(expected)

    def test_all_weights_positive(self):
        areas = [10.0, 100.0, 500.0, 3000.0]
        gdf = _make_buildings_gdf(areas)
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert (result["demand_weight"] > 0).all()


# ---------------------------------------------------------------------------
# classify_buildings — color assignment
# ---------------------------------------------------------------------------


class TestClassifyBuildingsColors:
    """Tests for color assignment in classification."""

    def test_classified_building_gets_rule_color(self):
        gdf = _make_buildings_gdf([100.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["rule_color"].iloc[0] == "#3498db"  # Residential

    def test_unclassified_gets_grey(self):
        gdf = _make_buildings_gdf([10.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert result["rule_color"].iloc[0] == "#95a5a6"

    def test_each_type_gets_distinct_color(self):
        areas = [100.0, 500.0, 3000.0]
        gdf = _make_buildings_gdf(areas)
        result = classify_buildings(gdf, DEFAULT_RULES)
        colors = result["rule_color"].tolist()
        assert len(set(colors)) == 3  # All different


# ---------------------------------------------------------------------------
# classify_buildings — floor-based classification
# ---------------------------------------------------------------------------


class TestClassifyBuildingsFloors:
    """Tests for floor-based classification rules."""

    def test_floor_rule_matches(self):
        rules = [
            BuildingTypeRule(
                name="HighRise",
                area_min_m2=100.0,
                area_max_m2=1000.0,
                min_floors=5,
                max_floors=50,
                weight_per_m2=0.10,
            ),
            BuildingTypeRule(
                name="Regular",
                area_min_m2=100.0,
                area_max_m2=1000.0,
                weight_per_m2=0.05,
            ),
        ]
        gdf = _make_buildings_gdf([200.0], floors=[10])
        result = classify_buildings(gdf, rules)
        assert result["building_type"].iloc[0] == "HighRise"

    def test_floor_rule_skipped_when_no_floor_data(self):
        """When building has no floor data, floor-constrained rule is skipped."""
        rules = [
            BuildingTypeRule(
                name="HighRise",
                area_min_m2=100.0,
                area_max_m2=1000.0,
                min_floors=5,
                max_floors=50,
            ),
            BuildingTypeRule(
                name="Regular",
                area_min_m2=100.0,
                area_max_m2=1000.0,
            ),
        ]
        gdf = _make_buildings_gdf([200.0])  # No num_floors column
        result = classify_buildings(gdf, rules)
        assert result["building_type"].iloc[0] == "Regular"

    def test_floor_outside_range_falls_to_area_check(self):
        """Building with floors outside rule's floor range still matches on area.

        The area-only skip condition only triggers when nf == 0 (no floor data).
        When the building has floor data but it's outside the rule's range,
        the area-only check still matches.
        """
        rules = [
            BuildingTypeRule(
                name="HighRise",
                area_min_m2=100.0,
                area_max_m2=1000.0,
                min_floors=5,
                max_floors=50,
            ),
            BuildingTypeRule(
                name="Regular",
                area_min_m2=100.0,
                area_max_m2=1000.0,
            ),
        ]
        gdf = _make_buildings_gdf([200.0], floors=[2])  # Below min_floors=5
        result = classify_buildings(gdf, rules)
        # Matches HighRise on area-only path (nf != 0, so skip condition is False)
        assert result["building_type"].iloc[0] == "HighRise"

    def test_nan_floors_treated_as_zero(self):
        gdf = _make_buildings_gdf([200.0], floors=[float("nan")])
        rules = [
            BuildingTypeRule(
                name="HighRise",
                area_min_m2=100.0,
                area_max_m2=1000.0,
                min_floors=5,
                max_floors=50,
            ),
            BuildingTypeRule(
                name="Regular",
                area_min_m2=100.0,
                area_max_m2=1000.0,
            ),
        ]
        result = classify_buildings(gdf, rules)
        assert result["building_type"].iloc[0] == "Regular"


# ---------------------------------------------------------------------------
# classify_buildings — empty / edge cases
# ---------------------------------------------------------------------------


class TestClassifyBuildingsEdgeCases:
    """Tests for edge cases in classify_buildings."""

    def test_empty_gdf(self):
        gdf = gpd.GeoDataFrame(
            {"footprint_area_m2": [], "geometry": []},
            crs="EPSG:4326",
        )
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert len(result) == 0
        assert "building_type" in result.columns

    def test_empty_rules(self):
        """With no rules, all buildings are unclassified."""
        gdf = _make_buildings_gdf([100.0, 500.0])
        result = classify_buildings(gdf, [])
        assert (result["building_type"] == "Unclassified").all()

    def test_single_building(self):
        gdf = _make_buildings_gdf([150.0])
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert len(result) == 1

    def test_large_dataset(self):
        """Classify 1000 buildings without error."""
        rng = np.random.default_rng(42)
        areas = rng.uniform(10, 5000, 1000).tolist()
        gdf = _make_buildings_gdf(areas)
        result = classify_buildings(gdf, DEFAULT_RULES)
        assert len(result) == 1000
        assert (result["demand_weight"] > 0).all()


# ---------------------------------------------------------------------------
# compute_classification_summary
# ---------------------------------------------------------------------------


class TestComputeClassificationSummary:
    """Tests for compute_classification_summary."""

    def test_returns_dataframe(self):
        gdf = _make_buildings_gdf([100.0, 500.0, 3000.0])
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        assert isinstance(summary, pd.DataFrame)

    def test_expected_columns(self):
        gdf = _make_buildings_gdf([100.0])
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        expected_cols = {"building_type", "count", "total_area_m2", "total_weight"}
        assert expected_cols == set(summary.columns)

    def test_row_count_matches_types(self):
        areas = [100.0, 500.0, 3000.0]
        gdf = _make_buildings_gdf(areas)
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        assert len(summary) == 3  # Residential, Commercial, Industrial

    def test_count_sums_match(self):
        areas = [50.0, 150.0, 200.0, 600.0, 4000.0]
        gdf = _make_buildings_gdf(areas)
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        assert summary["count"].sum() == 5

    def test_total_area_matches(self):
        areas = [100.0, 200.0]
        gdf = _make_buildings_gdf(areas)
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        assert summary["total_area_m2"].sum() == pytest.approx(300.0)

    def test_total_weight_matches(self):
        areas = [100.0, 200.0]  # Both residential
        gdf = _make_buildings_gdf(areas)
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        expected_weight = (100.0 + 200.0) * 0.05
        assert summary["total_weight"].sum() == pytest.approx(expected_weight)

    def test_sorted_by_count_descending(self):
        areas = [100.0, 150.0, 200.0, 500.0, 3000.0]  # 3 Res, 1 Com, 1 Ind
        gdf = _make_buildings_gdf(areas)
        classified = classify_buildings(gdf, DEFAULT_RULES)
        summary = compute_classification_summary(classified)
        counts = summary["count"].tolist()
        assert counts == sorted(counts, reverse=True)

    def test_empty_gdf_returns_empty_summary(self):
        summary = compute_classification_summary(None)
        assert len(summary) == 0
        assert "building_type" in summary.columns

    def test_empty_gdf_object(self):
        gdf = gpd.GeoDataFrame(
            {"footprint_area_m2": [], "building_type": [],
             "demand_weight": [], "geometry": []},
            crs="EPSG:4326",
        )
        summary = compute_classification_summary(gdf)
        assert len(summary) == 0


# ---------------------------------------------------------------------------
# ClusteringWorker._compute_summary — static method
# ---------------------------------------------------------------------------


def _make_clustered_gdf(cluster_ids, weights, lats=None, lngs=None):
    """Create a GeoDataFrame with cluster_id, demand_weight, and point geometry."""
    n = len(cluster_ids)
    if lats is None:
        lats = [0.0 + i * 0.001 for i in range(n)]
    if lngs is None:
        lngs = [0.0 + i * 0.001 for i in range(n)]
    geoms = [Point(lng, lat) for lat, lng in zip(lats, lngs)]
    return gpd.GeoDataFrame(
        {"cluster_id": cluster_ids, "demand_weight": weights, "geometry": geoms},
        crs="EPSG:4326",
    )


class TestComputeClusterSummary:
    """Tests for ClusteringWorker._compute_summary static method."""

    def test_returns_dataframe(self):
        gdf = _make_clustered_gdf([0, 0, 1], [10.0, 20.0, 30.0])
        summary = ClusteringWorker._compute_summary(gdf)
        assert isinstance(summary, pd.DataFrame)

    def test_expected_columns(self):
        gdf = _make_clustered_gdf([0, 1], [10.0, 20.0])
        summary = ClusteringWorker._compute_summary(gdf)
        expected = {
            "cluster_id", "count", "total_weight",
            "centroid_lat", "centroid_lng",
            "demand_fraction", "color",
        }
        assert expected == set(summary.columns)

    def test_cluster_count(self):
        gdf = _make_clustered_gdf([0, 0, 1, 2], [1.0, 2.0, 3.0, 4.0])
        summary = ClusteringWorker._compute_summary(gdf)
        assert len(summary) == 3

    def test_building_count_per_cluster(self):
        gdf = _make_clustered_gdf([0, 0, 0, 1, 1], [1.0] * 5)
        summary = ClusteringWorker._compute_summary(gdf)
        c0 = summary[summary["cluster_id"] == 0].iloc[0]
        c1 = summary[summary["cluster_id"] == 1].iloc[0]
        assert c0["count"] == 3
        assert c1["count"] == 2

    def test_total_weight_per_cluster(self):
        gdf = _make_clustered_gdf([0, 0, 1], [10.0, 20.0, 30.0])
        summary = ClusteringWorker._compute_summary(gdf)
        c0 = summary[summary["cluster_id"] == 0].iloc[0]
        c1 = summary[summary["cluster_id"] == 1].iloc[0]
        assert c0["total_weight"] == pytest.approx(30.0)
        assert c1["total_weight"] == pytest.approx(30.0)

    def test_fractions_sum_to_one(self):
        gdf = _make_clustered_gdf([0, 0, 1, 2], [5.0, 10.0, 15.0, 20.0])
        summary = ClusteringWorker._compute_summary(gdf)
        assert summary["demand_fraction"].sum() == pytest.approx(1.0)

    def test_fractions_proportional_to_weight(self):
        gdf = _make_clustered_gdf([0, 1], [25.0, 75.0])
        summary = ClusteringWorker._compute_summary(gdf)
        c0 = summary[summary["cluster_id"] == 0].iloc[0]
        c1 = summary[summary["cluster_id"] == 1].iloc[0]
        assert c0["demand_fraction"] == pytest.approx(0.25)
        assert c1["demand_fraction"] == pytest.approx(0.75)

    def test_single_cluster(self):
        gdf = _make_clustered_gdf([0, 0, 0], [10.0, 20.0, 30.0])
        summary = ClusteringWorker._compute_summary(gdf)
        assert len(summary) == 1
        assert summary["demand_fraction"].iloc[0] == pytest.approx(1.0)

    def test_sorted_by_cluster_id(self):
        gdf = _make_clustered_gdf([2, 0, 1], [10.0, 20.0, 30.0])
        summary = ClusteringWorker._compute_summary(gdf)
        ids = summary["cluster_id"].tolist()
        assert ids == sorted(ids)

    def test_colors_assigned_from_palette(self):
        gdf = _make_clustered_gdf([0, 1, 2], [10.0, 20.0, 30.0])
        summary = ClusteringWorker._compute_summary(gdf)
        for _, row in summary.iterrows():
            cid = int(row["cluster_id"])
            assert row["color"] == CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]

    def test_color_wraps_for_many_clusters(self):
        """More clusters than colours wraps around."""
        n = len(CLUSTER_COLORS) + 3
        gdf = _make_clustered_gdf(list(range(n)), [1.0] * n)
        summary = ClusteringWorker._compute_summary(gdf)
        assert len(summary) == n
        # Last cluster should wrap
        last = summary[summary["cluster_id"] == n - 1].iloc[0]
        assert last["color"] == CLUSTER_COLORS[(n - 1) % len(CLUSTER_COLORS)]

    def test_centroid_coordinates_reasonable(self):
        """Centroids should be within the bounding box of input points."""
        lats = [10.0, 10.001, 20.0]
        lngs = [30.0, 30.001, 40.0]
        gdf = _make_clustered_gdf([0, 0, 1], [10.0, 10.0, 20.0], lats=lats, lngs=lngs)
        summary = ClusteringWorker._compute_summary(gdf)
        for _, row in summary.iterrows():
            assert 9.0 < row["centroid_lat"] < 21.0
            assert 29.0 < row["centroid_lng"] < 41.0

    def test_equal_weight_clusters(self):
        """Clusters with equal total weight get equal fractions."""
        gdf = _make_clustered_gdf([0, 0, 1, 1], [10.0, 10.0, 10.0, 10.0])
        summary = ClusteringWorker._compute_summary(gdf)
        fracs = summary["demand_fraction"].tolist()
        assert fracs[0] == pytest.approx(0.5)
        assert fracs[1] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# CLUSTER_COLORS
# ---------------------------------------------------------------------------


class TestClusterColors:
    """Tests for CLUSTER_COLORS palette."""

    def test_has_ten_colors(self):
        assert len(CLUSTER_COLORS) == 10

    def test_all_hex_format(self):
        for color in CLUSTER_COLORS:
            assert color.startswith("#")
            assert len(color) == 7

    def test_all_unique(self):
        assert len(set(CLUSTER_COLORS)) == 10
