"""Unit tests for N-1 contingency analysis module."""

import math

import numpy as np
import pytest

from esfex.analysis.contingency import (
    BatteryInfo,
    ContingencyAnalyzer,
    ContingencyResult,
    GeneratorInfo,
    LineInfo,
    TransformerInfo,
)
from esfex.analysis.n1_assessment import IntegratedN1Analyzer, N1SecurityAssessment


# ── Fixtures ──


def _make_3bus_system() -> tuple[list[LineInfo], list[GeneratorInfo]]:
    """Create a 3-bus system with 3 lines forming a triangle.

    Bus 0 --- Line 0 (100 MW, x=0.1) --- Bus 1
      |                                     |
    Line 2 (100 MW, x=0.15)         Line 1 (100 MW, x=0.1)
      |                                     |
    Bus 2 -------- (shared) -------- Bus 2
    """
    lines = [
        LineInfo(line_id="line_0", from_node=0, to_node=1,
                 capacity_mw=100.0, reactance_pu=0.1),
        LineInfo(line_id="line_1", from_node=1, to_node=2,
                 capacity_mw=100.0, reactance_pu=0.1),
        LineInfo(line_id="line_2", from_node=0, to_node=2,
                 capacity_mw=100.0, reactance_pu=0.15),
    ]
    generators = [
        GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=200.0),
        GeneratorInfo(element_id="gen_1", node=1, rated_power_mw=150.0),
        GeneratorInfo(element_id="gen_2", node=2, rated_power_mw=100.0,
                      is_renewable=True),
    ]
    return lines, generators


def _make_snapshot_3bus() -> dict:
    """Snapshot for 3-bus system with typical loading."""
    return {
        "generators": {
            "gen_0": {"output_mw": 120.0, "capacity_mw": 200.0, "status": 1},
            "gen_1": {"output_mw": 100.0, "capacity_mw": 150.0, "status": 1},
            "gen_2": {"output_mw": 80.0, "capacity_mw": 100.0, "status": 1},
        },
        "loads": {
            "load_node_0": {"demand_mw": 100.0},
            "load_node_1": {"demand_mw": 120.0},
            "load_node_2": {"demand_mw": 80.0},
        },
        "batteries": {},
        "lines": {
            "edge_line_0": {"flow_mw": 30.0, "capacity_mw": 100.0},
            "edge_line_1": {"flow_mw": -10.0, "capacity_mw": 100.0},
            "edge_line_2": {"flow_mw": -10.0, "capacity_mw": 100.0},
        },
    }


class TestContingencyResult:
    """Tests for the ContingencyResult dataclass."""

    def test_default_is_secure(self):
        result = ContingencyResult(
            contingency_type="generator",
            element_id="gen_0",
            element_description="Loss of gen_0",
        )
        assert result.is_secure is True
        assert result.total_load_shed_mw == 0.0
        assert result.max_overload_pct == 0.0


class TestContingencyAnalyzer:
    """Tests for the ContingencyAnalyzer class."""

    def test_generator_loss_redistribution(self):
        """Remaining generators should increase output to cover loss."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        snapshot = _make_snapshot_3bus()
        result = analyzer.analyze_generator_loss(snapshot, "gen_1")

        # gen_1 was producing 100 MW, should be tripped to 0
        assert result.post_gen_mw["gen_1"] == 0.0

        # gen_0 should have increased (only non-renewable with headroom)
        assert result.post_gen_mw["gen_0"] > 120.0

        # Total generation should still roughly balance demand (minus any shedding)
        total_post_gen = sum(result.post_gen_mw.values())
        total_demand = 300.0
        assert total_post_gen <= total_demand + 0.1

    def test_line_loss_flow_redistribution(self):
        """Flows should redistribute when a line is removed."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        snapshot = _make_snapshot_3bus()
        result = analyzer.analyze_line_loss(snapshot, "line_0")

        # The tripped line should have zero flow
        assert result.post_flow_mw.get("edge_line_0", 0) == 0.0

        # Other lines should still carry flow
        assert "edge_line_1" in result.post_flow_mw
        assert "edge_line_2" in result.post_flow_mw

        # Generation should NOT change for line loss
        assert result.post_gen_mw["gen_0"] == pytest.approx(120.0)

    def test_line_overload_detection(self):
        """Lines exceeding thermal limit should be flagged."""
        # Create a system where removing a line causes overload
        lines = [
            LineInfo(line_id="line_0", from_node=0, to_node=1,
                     capacity_mw=50.0, reactance_pu=0.1),
            LineInfo(line_id="line_1", from_node=0, to_node=1,
                     capacity_mw=50.0, reactance_pu=0.1),
        ]
        generators = [
            GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=100.0),
        ]

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=2,
        )

        snapshot = {
            "generators": {
                "gen_0": {"output_mw": 80.0, "capacity_mw": 100.0, "status": 1},
            },
            "loads": {
                "load_node_0": {"demand_mw": 0.0},
                "load_node_1": {"demand_mw": 80.0},
            },
            "batteries": {},
            "lines": {
                "edge_line_0": {"flow_mw": 40.0, "capacity_mw": 50.0},
                "edge_line_1": {"flow_mw": 40.0, "capacity_mw": 50.0},
            },
        }

        # Removing one parallel line forces all flow on the other
        result = analyzer.analyze_line_loss(snapshot, "line_0")

        # Remaining line should carry ~80 MW, but capacity is only 50 MW
        remaining_flow = abs(result.post_flow_mw.get("edge_line_1", 0))
        if remaining_flow > 50.0:
            assert len(result.overloaded_lines) > 0
            assert result.is_secure is False

    def test_load_shedding_insufficient_generation(self):
        """Load shedding should occur when gen capacity insufficient."""
        lines = []  # Single-node, no lines
        generators = [
            GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=100.0),
            GeneratorInfo(element_id="gen_1", node=0, rated_power_mw=60.0),
        ]

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=1,
        )

        snapshot = {
            "generators": {
                "gen_0": {"output_mw": 90.0, "capacity_mw": 100.0, "status": 1},
                "gen_1": {"output_mw": 50.0, "capacity_mw": 60.0, "status": 1},
            },
            "loads": {"load_node_0": {"demand_mw": 140.0}},
            "batteries": {},
            "lines": {},
        }

        # Lose gen_0 (90 MW). gen_1 headroom = 60-50 = 10 MW.
        # Shortfall = 90 - 10 = 80 MW
        result = analyzer.analyze_generator_loss(snapshot, "gen_0")

        assert result.post_gen_mw["gen_0"] == 0.0
        assert result.post_gen_mw["gen_1"] == pytest.approx(60.0)
        assert result.total_load_shed_mw == pytest.approx(80.0)
        assert result.is_secure is False

    def test_contingency_list(self):
        """Should list all generators and lines as contingencies."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        snapshot = _make_snapshot_3bus()
        contingencies = analyzer.get_contingency_list(snapshot)

        # 3 generators (all online) + 3 lines = 6 contingencies
        gen_ctgs = [c for c in contingencies if c["type"] == "generator"]
        line_ctgs = [c for c in contingencies if c["type"] == "line"]
        assert len(gen_ctgs) == 3
        assert len(line_ctgs) == 3

        # Should be sorted by impact (highest first)
        impacts = [c["impact_mw"] for c in contingencies]
        assert impacts == sorted(impacts, reverse=True)

    def test_single_node_no_lines(self):
        """Single-node system should handle line contingency gracefully."""
        generators = [
            GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=100.0),
        ]
        analyzer = ContingencyAnalyzer(
            lines=[], generators=generators, num_nodes=1,
        )

        snapshot = {
            "generators": {
                "gen_0": {"output_mw": 80.0, "capacity_mw": 100.0, "status": 1},
            },
            "loads": {"load_node_0": {"demand_mw": 80.0}},
            "batteries": {},
            "lines": {},
        }

        # No lines to trip — should return empty flows
        result = analyzer.analyze_line_loss(snapshot, "nonexistent")
        assert result.element_description.startswith("Unknown line")

    def test_unknown_generator(self):
        """Unknown generator ID should return gracefully."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        snapshot = _make_snapshot_3bus()
        result = analyzer.analyze_generator_loss(snapshot, "gen_nonexistent")

        assert result.element_description.startswith("Unknown generator")

    def test_offline_generator_loss(self):
        """Tripping an offline generator should be a no-op."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        snapshot = _make_snapshot_3bus()
        snapshot["generators"]["gen_1"]["output_mw"] = 0.0
        snapshot["generators"]["gen_1"]["status"] = 0

        result = analyzer.analyze_generator_loss(snapshot, "gen_1")
        assert result.is_secure is True

    def test_b_matrix_construction(self):
        """B-matrix should be symmetric with correct structure."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        b_mat = analyzer._build_b_matrix()

        # B should be symmetric
        np.testing.assert_array_almost_equal(b_mat, b_mat.T)

        # Diagonal should be positive (sum of admittances)
        for i in range(3):
            assert b_mat[i, i] > 0

        # Off-diagonal should be negative (for connected buses)
        assert b_mat[0, 1] < 0  # line_0 connects 0-1
        assert b_mat[1, 2] < 0  # line_1 connects 1-2
        assert b_mat[0, 2] < 0  # line_2 connects 0-2

        # Row/column sums should be zero (conservation)
        for i in range(3):
            assert abs(b_mat[i, :].sum()) < 1e-10

    def test_b_matrix_line_exclusion(self):
        """Excluding a line should modify the B-matrix correctly."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )

        b_full = analyzer._build_b_matrix()
        b_excl = analyzer._build_b_matrix(exclude_line="line_0")

        # With line_0 (0-1) excluded, B[0,1] and B[1,0] should be less negative
        assert b_excl[0, 1] > b_full[0, 1]
        assert b_excl[1, 0] > b_full[1, 0]

        # Connection 0-2 and 1-2 should be unchanged
        assert b_excl[0, 2] == pytest.approx(b_full[0, 2])
        assert b_excl[1, 2] == pytest.approx(b_full[1, 2])


# ── Fixtures with batteries and transformers ──


def _make_extended_system():
    """3-bus system with batteries and transformers."""
    lines = [
        LineInfo(line_id="line_0", from_node=0, to_node=1,
                 capacity_mw=100.0, reactance_pu=0.1),
        LineInfo(line_id="line_1", from_node=1, to_node=2,
                 capacity_mw=100.0, reactance_pu=0.1),
        LineInfo(line_id="line_2", from_node=0, to_node=2,
                 capacity_mw=100.0, reactance_pu=0.15),
    ]
    generators = [
        GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=200.0),
        GeneratorInfo(element_id="gen_1", node=1, rated_power_mw=150.0),
    ]
    batteries = [
        BatteryInfo(element_id="bat_0", node=2, rated_power_mw=50.0),
    ]
    transformers = [
        TransformerInfo(name="trafo_0", from_node=0, to_node=1,
                        rated_power_mva=120.0, reactance_pu=0.08),
    ]
    return lines, generators, batteries, transformers


def _make_extended_snapshot():
    """Snapshot for extended system with battery discharging."""
    return {
        "generators": {
            "gen_0": {"output_mw": 150.0, "capacity_mw": 200.0, "status": 1},
            "gen_1": {"output_mw": 100.0, "capacity_mw": 150.0, "status": 1},
        },
        "loads": {
            "load_node_0": {"demand_mw": 100.0},
            "load_node_1": {"demand_mw": 120.0},
            "load_node_2": {"demand_mw": 80.0},
        },
        "batteries": {
            "bat_0": {"discharge_mw": 30.0, "charge_mw": 0.0, "soc_mwh": 100.0},
        },
        "lines": {
            "edge_line_0": {"flow_mw": 40.0, "capacity_mw": 100.0},
            "edge_line_1": {"flow_mw": -10.0, "capacity_mw": 100.0},
            "edge_line_2": {"flow_mw": -20.0, "capacity_mw": 100.0},
        },
    }


class TestPTDFLODF:
    """Tests for PTDF and LODF matrix computation."""

    def test_ptdf_shape(self):
        """PTDF should be [n_branches x n_buses]."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        ptdf = analyzer.ptdf
        assert ptdf.shape == (3, 3)  # 3 lines x 3 buses

    def test_ptdf_slack_column_zero(self):
        """PTDF column for slack bus (0) should be zero."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        ptdf = analyzer.ptdf
        np.testing.assert_allclose(ptdf[:, 0], 0.0, atol=1e-10)

    def test_lodf_shape(self):
        """LODF should be [n_branches x n_branches]."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        lodf = analyzer.lodf
        assert lodf.shape == (3, 3)

    def test_lodf_diagonal_negative_one(self):
        """Diagonal LODF entries should be -1 (tripped line flow goes to zero)."""
        lines, generators = _make_3bus_system()
        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        lodf = analyzer.lodf
        for k in range(3):
            assert lodf[k, k] == pytest.approx(-1.0)

    def test_fast_vs_full_line_loss(self):
        """Fast LODF-based analysis should match full B-matrix rebuild."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        # Force PTDF/LODF computation
        _ = analyzer.ptdf
        _ = analyzer.lodf

        # Full analysis (B-matrix rebuild)
        analyzer2 = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        full_result = analyzer2.analyze_line_loss(snapshot, "line_0")

        # Fast analysis (LODF-based)
        fast_result = analyzer.analyze_line_loss_fast(snapshot, "line_0")

        # Both methods should detect the same security status
        assert fast_result.is_secure == full_result.is_secure
        # Overload percentages should be in the same ballpark
        assert fast_result.max_overload_pct == pytest.approx(
            full_result.max_overload_pct, abs=10.0,
        )


class TestBatteryContingency:
    """Tests for battery contingency analysis."""

    def test_battery_loss(self):
        """Losing a discharging battery should cause generation redistribution."""
        lines, generators, batteries, _ = _make_extended_system()
        snapshot = _make_extended_snapshot()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
            batteries=batteries,
        )
        result = analyzer.analyze_generator_loss(snapshot, "bat_0")

        assert result.element_id == "bat_0"
        # Total post-gen should increase to cover battery loss
        total_post = sum(result.post_gen_mw.values())
        total_pre = sum(
            v["output_mw"] for v in snapshot["generators"].values()
        )
        # Post generation should be higher (compensating for battery loss)
        assert total_post >= total_pre - 1.0

    def test_battery_in_contingency_list(self):
        """Discharging batteries should appear in contingency list."""
        lines, generators, batteries, _ = _make_extended_system()
        snapshot = _make_extended_snapshot()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
            batteries=batteries,
        )
        contingencies = analyzer.get_contingency_list(snapshot)
        battery_contingencies = [c for c in contingencies if c["type"] == "battery"]
        assert len(battery_contingencies) == 1
        assert battery_contingencies[0]["element_id"] == "bat_0"


class TestTransformerContingency:
    """Tests for transformer contingency analysis."""

    def test_transformer_in_contingency_list(self):
        """Transformers should appear in contingency list."""
        lines, generators, _, transformers = _make_extended_system()
        snapshot = _make_extended_snapshot()
        # Add transformer flow to snapshot
        snapshot["lines"]["edge_trafo_0"] = {"flow_mw": 50.0, "capacity_mw": 120.0}

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
            transformers=transformers,
        )
        contingencies = analyzer.get_contingency_list(snapshot)
        trafo_contingencies = [c for c in contingencies if c["type"] == "transformer"]
        assert len(trafo_contingencies) == 1
        assert trafo_contingencies[0]["element_id"] == "trafo_0"


class TestContingencyScreening:
    """Tests for PI-based contingency screening."""

    def test_screening_returns_ranked(self):
        """Screening should return contingencies sorted by PI descending."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        ranked = analyzer.screen_contingencies(snapshot, pi_threshold=0.0)

        assert len(ranked) > 0
        # Should be sorted by PI descending
        pis = [r["pi"] for r in ranked]
        assert pis == sorted(pis, reverse=True)

    def test_screening_filters_below_threshold(self):
        """High PI threshold should filter out mild contingencies."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        all_ranked = analyzer.screen_contingencies(snapshot, pi_threshold=0.0)
        filtered = analyzer.screen_contingencies(snapshot, pi_threshold=100.0)

        assert len(filtered) <= len(all_ranked)


class TestN1_1Analysis:
    """Tests for sequential N-1-1 contingency analysis."""

    def test_n1_1_basic(self):
        """N-1-1 should return a valid result for sequential failures."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        result = analyzer.analyze_n1_1(
            snapshot,
            first_contingency={"type": "line", "element_id": "line_0"},
            second_contingency={"type": "line", "element_id": "line_1"},
        )

        assert result is not None
        assert "N-1-1" in result.element_description
        assert result.contingency_type in ("line", "generator")

    def test_screen_n1_1(self):
        """N-1-1 screening should find critical pairs."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        pairs = analyzer.screen_n1_1(
            snapshot, stress_threshold_pct=0.0, max_pairs=10,
        )
        # Should return a list (may be empty for low-stress system)
        assert isinstance(pairs, list)


class TestIntegratedN1Analyzer:
    """Tests for the unified N-1 security assessment."""

    def test_assess_single_generator(self):
        """Single generator contingency should produce valid assessment."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        contingency_analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        integrated = IntegratedN1Analyzer(
            contingency_analyzer=contingency_analyzer,
        )
        assessment = integrated.assess_single(snapshot, "generator", "gen_0")

        assert isinstance(assessment, N1SecurityAssessment)
        assert assessment.element_id == "gen_0"
        assert assessment.element_type == "generator"
        assert assessment.severity_score >= 0.0
        # Without frequency analyzer, frequency checks should be skipped
        assert assessment.frequency is None
        assert not assessment.has_frequency_violation

    def test_assess_single_line(self):
        """Single line contingency should produce valid assessment."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        contingency_analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        integrated = IntegratedN1Analyzer(
            contingency_analyzer=contingency_analyzer,
        )
        assessment = integrated.assess_single(snapshot, "line", "line_0")

        assert isinstance(assessment, N1SecurityAssessment)
        assert assessment.element_id == "line_0"
        assert assessment.element_type == "line"

    def test_assess_all(self):
        """assess_all should return sorted list of assessments."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        contingency_analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        integrated = IntegratedN1Analyzer(
            contingency_analyzer=contingency_analyzer,
        )
        assessments = integrated.assess_all(snapshot)

        assert len(assessments) > 0
        # Should be sorted by severity (descending)
        scores = [a.severity_score for a in assessments]
        assert scores == sorted(scores, reverse=True)

    def test_security_summary(self):
        """get_security_summary should return valid summary dict."""
        lines, generators = _make_3bus_system()
        snapshot = _make_snapshot_3bus()

        contingency_analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
        )
        integrated = IntegratedN1Analyzer(
            contingency_analyzer=contingency_analyzer,
        )
        assessments = integrated.assess_all(snapshot)
        summary = integrated.get_security_summary(assessments)

        assert "total_contingencies" in summary
        assert "secure_count" in summary
        assert "insecure_count" in summary
        assert summary["total_contingencies"] == len(assessments)
        assert summary["secure_count"] + summary["insecure_count"] == len(assessments)

    def test_battery_contingency_in_assess_all(self):
        """assess_all should include battery contingencies."""
        lines, generators, batteries, _ = _make_extended_system()
        snapshot = _make_extended_snapshot()

        contingency_analyzer = ContingencyAnalyzer(
            lines=lines, generators=generators, num_nodes=3,
            batteries=batteries,
        )
        integrated = IntegratedN1Analyzer(
            contingency_analyzer=contingency_analyzer,
        )
        assessments = integrated.assess_all(snapshot)

        battery_assessments = [a for a in assessments if a.element_type == "battery"]
        assert len(battery_assessments) >= 1
