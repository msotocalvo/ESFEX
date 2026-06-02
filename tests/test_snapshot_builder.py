"""Unit tests for the hypothetical scenario snapshot builder."""

from unittest.mock import MagicMock

import pytest

from esfex.analysis.snapshot_builder import (
    HypotheticalScenario,
    build_default_scenario,
    build_snapshot_from_scenario,
)


def _make_state(num_nodes=2, num_gens=2, num_bats=1):
    """Create a mock GuiSystemState for testing."""
    state = MagicMock()

    # Buses
    buses = {}
    for ni in range(num_nodes):
        bus = MagicMock()
        bus.bus_id = f"bus_{ni}"
        bus.parent_node = ni
        buses[f"bus_{ni}"] = bus
    state.buses = buses

    # Nodes
    nodes = []
    for ni in range(num_nodes):
        node = MagicMock()
        node.index = ni
        node.name = f"Node {ni}"
        nodes.append(node)
    state.nodes = nodes

    # Generators
    generators = {}
    for gi in range(num_gens):
        gen = MagicMock()
        gen.instance_id = f"gen_{gi}"
        gen.name = f"Generator {gi}"
        gen.rated_power = 100.0
        gen.fuel = "Diesel" if gi == 0 else "Sun"
        gen.gen_type = "Non-renewable" if gi == 0 else "Renewable"
        gen.bus = f"bus_{gi % num_nodes}"
        gen.inertia = 5.0 if gi == 0 else 0.0
        gen.droop = 0.05
        gen.governor_time_const = 5.0
        generators[f"gen_{gi}"] = gen
    state.generators = generators

    # Batteries
    batteries = {}
    for bi in range(num_bats):
        bat = MagicMock()
        bat.bat_id = f"bat_{bi}"
        bat.capacity = 50.0
        bat.bus = f"bus_{bi % num_nodes}"
        batteries[f"bat_{bi}"] = bat
    state.batteries = batteries

    # Transmission lines
    state.transmission_lines = []

    return state


class TestHypotheticalScenario:
    """Tests for the HypotheticalScenario dataclass."""

    def test_defaults_are_empty(self):
        scenario = HypotheticalScenario()
        assert scenario.gen_outputs == {}
        assert scenario.gen_status == {}
        assert scenario.node_demands == {}

    def test_fields_assignable(self):
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0},
            gen_status={"gen_0": True},
            node_demands={0: 100.0},
        )
        assert scenario.gen_outputs["gen_0"] == 80.0
        assert scenario.gen_status["gen_0"] is True
        assert scenario.node_demands[0] == 100.0


class TestBuildDefaultScenario:
    """Tests for build_default_scenario."""

    def test_all_generators_present(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        assert "gen_0" in scenario.gen_outputs
        assert "gen_1" in scenario.gen_outputs
        assert len(scenario.gen_outputs) == 2

    def test_all_generators_online(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        assert all(scenario.gen_status.values())

    def test_non_renewable_at_80pct(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        # gen_0 is Non-renewable, rated 100 MW → 80 MW
        assert scenario.gen_outputs["gen_0"] == pytest.approx(80.0)

    def test_renewable_at_50pct(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        # gen_1 is Renewable, rated 100 MW → 50 MW
        assert scenario.gen_outputs["gen_1"] == pytest.approx(50.0)

    def test_demand_per_node_populated(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        assert 0 in scenario.node_demands
        assert 1 in scenario.node_demands

    def test_demand_equals_generation(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        total_gen = sum(scenario.gen_outputs.values())
        total_demand = sum(scenario.node_demands.values())
        assert total_gen == pytest.approx(total_demand)


class TestBuildSnapshotFromScenario:
    """Tests for build_snapshot_from_scenario."""

    def test_snapshot_has_required_keys(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        snapshot = build_snapshot_from_scenario(state, scenario)
        assert "generators" in snapshot
        assert "loads" in snapshot
        assert "batteries" in snapshot
        assert "lines" in snapshot
        assert "nodes" in snapshot
        assert "system" in snapshot

    def test_generators_in_snapshot(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        snapshot = build_snapshot_from_scenario(state, scenario)
        assert len(snapshot["generators"]) == 2
        for gen_data in snapshot["generators"].values():
            assert "output_mw" in gen_data
            assert "capacity_mw" in gen_data
            assert "status" in gen_data

    def test_offline_generator_zero_output(self):
        state = _make_state()
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": False, "gen_1": True},
            node_demands={0: 0.0, 1: 50.0},
        )
        snapshot = build_snapshot_from_scenario(state, scenario)
        assert snapshot["generators"]["gen_0"]["output_mw"] == 0.0
        assert snapshot["generators"]["gen_0"]["status"] == 0
        assert snapshot["generators"]["gen_1"]["output_mw"] == 50.0
        assert snapshot["generators"]["gen_1"]["status"] == 1

    def test_loads_match_demand(self):
        state = _make_state()
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 60.0, "gen_1": 40.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 70.0, 1: 30.0},
        )
        snapshot = build_snapshot_from_scenario(state, scenario)
        assert snapshot["loads"]["load_node_0"]["demand_mw"] == 70.0
        assert snapshot["loads"]["load_node_1"]["demand_mw"] == 30.0

    def test_system_summary(self):
        state = _make_state()
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 80.0, 1: 50.0},
        )
        snapshot = build_snapshot_from_scenario(state, scenario)
        sys = snapshot["system"]
        assert sys["total_gen_mw"] == pytest.approx(130.0)
        assert sys["total_demand_mw"] == pytest.approx(130.0)
        # gen_1 (Renewable) output 50 out of 130 total → fraction 0.385
        assert sys["re_penetration"] == pytest.approx(50.0 / 130.0, rel=1e-2)

    def test_nodes_have_enhanced_fields(self):
        state = _make_state()
        scenario = build_default_scenario(state)
        snapshot = build_snapshot_from_scenario(state, scenario)
        for ni, node_data in snapshot["nodes"].items():
            assert "demand_mw" in node_data
            assert "generation_mw" in node_data
            assert "reserve_static_mw" in node_data
            assert "voltage_angle_deg" in node_data
            assert "co2_tons" in node_data

    def test_batteries_in_snapshot(self):
        state = _make_state(num_bats=2)
        scenario = build_default_scenario(state)
        snapshot = build_snapshot_from_scenario(state, scenario)
        assert len(snapshot["batteries"]) == 2
        for bat_data in snapshot["batteries"].values():
            assert "charge_mw" in bat_data
            assert "soc_mwh" in bat_data

    def test_snapshot_compatible_with_frequency_analyzer(self):
        """Snapshot should work directly with FrequencyAnalyzer."""
        from esfex.analysis.frequency import FrequencyAnalyzer, GeneratorFreqParams

        state = _make_state()
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 80.0, 1: 50.0},
        )
        snapshot = build_snapshot_from_scenario(state, scenario)

        params = [
            GeneratorFreqParams(
                element_id="gen_0", rated_power_mw=100.0,
                inertia_h=5.0, droop=0.05, governor_time_const=5.0,
            ),
            GeneratorFreqParams(
                element_id="gen_1", rated_power_mw=100.0,
                inertia_h=0.0, droop=0.0, governor_time_const=0.0,
                is_renewable=True,
            ),
        ]
        analyzer = FrequencyAnalyzer(params)
        resp = analyzer.analyze(snapshot, delta_p_mw=80.0)

        assert resp.rocof_hz_per_s > 0
        assert resp.nadir_hz < 50.0

    def test_snapshot_compatible_with_contingency_analyzer(self):
        """Snapshot should work directly with ContingencyAnalyzer."""
        from esfex.analysis.contingency import ContingencyAnalyzer, GeneratorInfo

        state = _make_state()
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 80.0, 1: 50.0},
        )
        snapshot = build_snapshot_from_scenario(state, scenario)

        generators = [
            GeneratorInfo(element_id="gen_0", node=0, rated_power_mw=100.0),
            GeneratorInfo(element_id="gen_1", node=1, rated_power_mw=100.0,
                          is_renewable=True),
        ]
        analyzer = ContingencyAnalyzer(
            lines=[], generators=generators, num_nodes=2,
        )

        result = analyzer.analyze_generator_loss(snapshot, "gen_0")
        assert result.post_gen_mw["gen_0"] == 0.0

    def test_build_gen_freq_params_from_state(self):
        """build_gen_freq_params_from_state should read from GuiGeneratorInstance."""
        from esfex.analysis.frequency import build_gen_freq_params_from_state

        state = _make_state()
        params = build_gen_freq_params_from_state(state)
        assert len(params) == 2

        # gen_0 is Non-renewable
        p0 = next(p for p in params if p.element_id == "gen_0")
        assert p0.rated_power_mw == 100.0
        assert p0.inertia_h == 5.0
        assert p0.droop == 0.05
        assert p0.is_renewable is False

        # gen_1 is Renewable
        p1 = next(p for p in params if p.element_id == "gen_1")
        assert p1.inertia_h == 0.0
        assert p1.is_renewable is True
