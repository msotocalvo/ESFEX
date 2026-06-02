"""Unit tests for pandapower bridge and AC contingency analyzer."""

from unittest.mock import MagicMock

import pytest

# Check if pandapower is available for conditional skipping
try:
    import pandapower as pp
    HAS_PANDAPOWER = True
except ImportError:
    HAS_PANDAPOWER = False

# Detect pandapower-vs-numpy incompatibility (pandapower 3.4 + numpy 2.x
# triggers "assignment destination is read-only" inside pp.runpp). When
# present, AC-PF cannot succeed regardless of caller code, so skip the
# whole module rather than report library-version failures as test bugs.
_PP_RUNPP_BROKEN = False
if HAS_PANDAPOWER:
    try:
        _net = pp.create_empty_network()
        _b1 = pp.create_bus(_net, vn_kv=20)
        _b2 = pp.create_bus(_net, vn_kv=20)
        pp.create_ext_grid(_net, bus=_b1, vm_pu=1.0)
        pp.create_load(_net, bus=_b2, p_mw=1)
        pp.create_line_from_parameters(
            _net, _b1, _b2, length_km=1.0,
            r_ohm_per_km=0.1, x_ohm_per_km=0.1, c_nf_per_km=0, max_i_ka=1,
        )
        pp.runpp(_net)
    except Exception:
        _PP_RUNPP_BROKEN = True

pytestmark = pytest.mark.skipif(
    _PP_RUNPP_BROKEN,
    reason="pp.runpp incompatible with current numpy (read-only array assignment)",
)

from esfex.analysis.ac_types import ACPowerFlowResult, ShortCircuitResult
from esfex.analysis.pandapower_bridge import PandapowerBridge
from esfex.analysis.ac_contingency import ACContingencyAnalyzer, ACContingencyResult
from esfex.analysis.snapshot_builder import HypotheticalScenario

# Check if Julia/NativeACBridge is available
try:
    from esfex.analysis.native_ac_bridge import NativeACBridge
    HAS_NATIVE = NativeACBridge.is_available()
except ImportError:
    HAS_NATIVE = False


def _make_state(num_nodes=2, num_gens=2, num_bats=1, num_lines=1):
    """Create a mock GuiSystemState for testing."""
    state = MagicMock()

    # Buses
    buses = {}
    for ni in range(num_nodes):
        bus = MagicMock()
        bus.bus_id = f"bus_{ni}"
        bus.parent_node = ni
        bus.voltage_kv = 220.0
        bus.frequency_hz = 50.0
        bus.current_type = "AC"
        bus.bus_type = "slack" if ni == 0 else "PQ"
        bus.demand_fraction = 1.0
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
    lines = []
    for li in range(num_lines):
        tl = MagicMock()
        tl.line_id = f"line_{li}"
        tl.from_bus = "bus_0"
        tl.to_bus = f"bus_{min(li + 1, num_nodes - 1)}"
        tl.capacity_mw = 200.0
        tl.voltage_kv = 220.0
        tl.reactance_pu = 0.01
        tl.resistance_pu = 0.001
        tl.susceptance_pu = 0.0
        tl.frequency_hz = 50.0
        tl.num_circuits = 1
        lines.append(tl)
    state.transmission_lines = lines

    # Transformers (empty by default)
    state.transformers = []

    return state


def _make_scenario(gen_outputs=None, gen_status=None, node_demands=None):
    """Create a HypotheticalScenario for testing."""
    return HypotheticalScenario(
        gen_outputs=gen_outputs or {"gen_0": 80.0, "gen_1": 50.0},
        gen_status=gen_status or {"gen_0": True, "gen_1": True},
        node_demands=node_demands or {0: 80.0, 1: 50.0},
    )


# ── Tests that don't require pandapower ──


class TestPandapowerBridgeAvailability:
    """Test the is_available static method."""

    def test_is_available_returns_bool(self):
        result = PandapowerBridge.is_available()
        assert isinstance(result, bool)

    def test_is_available_matches_import(self):
        assert PandapowerBridge.is_available() == HAS_PANDAPOWER


class TestACPowerFlowResult:
    """Test the ACPowerFlowResult dataclass defaults."""

    def test_defaults(self):
        result = ACPowerFlowResult()
        assert result.converged is False
        assert result.iterations == 0
        assert result.bus_vm_pu == {}
        assert result.line_p_from_mw == {}
        assert result.voltage_violations == []
        assert result.total_losses_mw == 0.0

    def test_fields_assignable(self):
        result = ACPowerFlowResult(
            converged=True,
            bus_vm_pu={"bus_0": 1.02},
            voltage_violations=[{"bus_id": "bus_1", "vm_pu": 0.94, "type": "under"}],
        )
        assert result.converged is True
        assert result.bus_vm_pu["bus_0"] == 1.02
        assert len(result.voltage_violations) == 1


class TestShortCircuitResult:
    """Test the ShortCircuitResult dataclass defaults."""

    def test_defaults(self):
        result = ShortCircuitResult()
        assert result.ik_ka == {}
        assert result.ip_ka == {}
        assert result.sk_mva == {}


class TestACContingencyResult:
    """Test the ACContingencyResult dataclass."""

    def test_inherits_from_contingency_result(self):
        result = ACContingencyResult(
            contingency_type="generator",
            element_id="gen_0",
            element_description="Loss of gen_0",
        )
        assert result.contingency_type == "generator"
        assert result.post_vm_pu == {}
        assert result.voltage_violations == []
        assert result.ac_converged is True

    def test_ac_specific_fields(self):
        result = ACContingencyResult(
            contingency_type="line",
            element_id="line_0",
            element_description="Loss of line_0",
            post_vm_pu={"bus_0": 1.01, "bus_1": 0.93},
            voltage_violations=[{"bus_id": "bus_1", "vm_pu": 0.93, "type": "under"}],
            ac_converged=True,
        )
        assert result.post_vm_pu["bus_1"] == 0.93
        assert len(result.voltage_violations) == 1


# ── Tests that require pandapower ──


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestPandapowerBridgeBuildNetwork:
    """Test network construction from editor state."""

    def test_creates_network(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)
        assert net is not None
        assert len(net.bus) == 2
        assert len(net.ext_grid) == 1

    def test_buses_mapped(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        bridge.build_network(scenario)
        assert "bus_0" in bridge._bus_id_to_pp
        assert "bus_1" in bridge._bus_id_to_pp

    def test_generators_created(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)
        # gen_0 is non-renewable at slack bus → ext_grid handles it
        # gen_1 is renewable → sgen
        assert "gen_1" in bridge._sgen_id_to_pp
        assert len(net.sgen) >= 1

    def test_loads_created(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)
        assert len(net.load) >= 1

    def test_lines_created(self):
        state = _make_state(num_lines=1)
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)
        assert len(net.line) == 1
        assert "line_0" in bridge._line_id_to_pp

    def test_batteries_as_storage(self):
        state = _make_state(num_bats=2)
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)
        assert len(net.storage) == 2

    def test_offline_generator(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario(gen_status={"gen_0": False, "gen_1": True})
        net = bridge.build_network(scenario)
        # gen_1 (renewable) should still be in service
        sgen_idx = bridge._sgen_id_to_pp.get("gen_1")
        if sgen_idx is not None:
            assert bool(net.sgen.at[sgen_idx, "in_service"]) is True


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestPandapowerBridgePowerFlow:
    """Test AC power flow execution."""

    def test_power_flow_converges(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert result.converged is True

    def test_bus_results_populated(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert len(result.bus_vm_pu) == 2
        assert all(0.5 < v < 1.5 for v in result.bus_vm_pu.values())

    def test_line_results_populated(self):
        state = _make_state(num_lines=1)
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert len(result.line_p_from_mw) >= 1

    def test_losses_computed(self):
        state = _make_state(num_lines=1)
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        # Losses should be >= 0 for any valid network
        assert result.total_losses_mw >= 0.0

    def test_generator_q_computed(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        # At least renewable gen should have Q results
        assert len(result.gen_q_mvar) >= 1


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestPandapowerBridgeShortCircuit:
    """Test short-circuit analysis."""

    def test_short_circuit_after_pf(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        pf = bridge.run_power_flow(scenario)
        assert pf.converged

        sc = bridge.run_short_circuit()
        # SC should produce results for all buses
        assert len(sc.sk_mva) == 2
        assert all(v > 0 for v in sc.sk_mva.values())

    def test_short_circuit_without_pf_returns_empty(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        sc = bridge.run_short_circuit()
        assert sc.ik_ka == {}
        assert sc.sk_mva == {}


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestPandapowerBridgeSetElement:
    """Test element in/out of service toggling."""

    def test_set_line_out_of_service(self):
        state = _make_state(num_lines=1)
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)

        bridge.set_element_in_service("line", "line_0", False)
        assert bool(net.line.at[bridge._line_id_to_pp["line_0"], "in_service"]) is False

        bridge.set_element_in_service("line", "line_0", True)
        assert bool(net.line.at[bridge._line_id_to_pp["line_0"], "in_service"]) is True

    def test_set_sgen_out_of_service(self):
        state = _make_state()
        bridge = PandapowerBridge(state)
        scenario = _make_scenario()
        net = bridge.build_network(scenario)

        if "gen_1" in bridge._sgen_id_to_pp:
            bridge.set_element_in_service("sgen", "gen_1", False)
            idx = bridge._sgen_id_to_pp["gen_1"]
            assert bool(net.sgen.at[idx, "in_service"]) is False


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestPandapowerBridgeRerun:
    """Test power flow rerun after modifications."""

    def test_rerun_after_line_outage(self):
        state = _make_state(num_nodes=3, num_lines=2)
        bridge = PandapowerBridge(state)
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 80.0, 1: 50.0, 2: 0.0},
        )
        pf1 = bridge.run_power_flow(scenario)
        if not pf1.converged:
            pytest.skip("Initial PF did not converge")

        bridge.set_element_in_service("line", "line_0", False)
        pf2 = bridge.rerun_power_flow()
        # May or may not converge depending on topology
        assert isinstance(pf2.converged, bool)


@pytest.mark.skipif(not HAS_PANDAPOWER, reason="pandapower not installed")
class TestACContingencyAnalyzer:
    """Test AC contingency analysis with pandapower."""

    def _make_snapshot(self):
        """Build a snapshot for contingency testing."""
        from esfex.analysis.snapshot_builder import build_snapshot_from_scenario

        state = _make_state(num_lines=1)
        scenario = _make_scenario()
        bridge = PandapowerBridge(state)
        pf = bridge.run_power_flow(scenario)
        snapshot = build_snapshot_from_scenario(state, scenario)

        # Merge AC results into snapshot
        if pf.converged:
            for edge_id in snapshot.get("lines", {}):
                if edge_id in pf.line_p_from_mw:
                    snapshot["lines"][edge_id]["flow_mw"] = pf.line_p_from_mw[edge_id]

        return state, bridge, snapshot

    def test_generator_loss(self):
        state, bridge, snapshot = self._make_snapshot()
        analyzer = ACContingencyAnalyzer(bridge)
        result = analyzer.analyze_generator_loss(snapshot, "gen_1")
        assert isinstance(result, ACContingencyResult)
        assert result.contingency_type == "generator"
        assert result.element_id == "gen_1"

    def test_line_loss(self):
        state, bridge, snapshot = self._make_snapshot()
        analyzer = ACContingencyAnalyzer(bridge)
        result = analyzer.analyze_line_loss(snapshot, "line_0")
        assert isinstance(result, ACContingencyResult)
        assert result.contingency_type == "line"

    def test_contingency_list(self):
        state, bridge, snapshot = self._make_snapshot()
        analyzer = ACContingencyAnalyzer(bridge)
        ctg_list = analyzer.get_contingency_list(snapshot)
        assert isinstance(ctg_list, list)
        # Should have at least 1 generator + 1 line contingency
        types = {c["type"] for c in ctg_list}
        assert "generator" in types or "line" in types

    def test_offline_gen_returns_secure(self):
        state, bridge, snapshot = self._make_snapshot()
        # Set gen_0 offline
        snapshot["generators"]["gen_0"]["output_mw"] = 0.0
        snapshot["generators"]["gen_0"]["status"] = 0

        analyzer = ACContingencyAnalyzer(bridge)
        result = analyzer.analyze_generator_loss(snapshot, "gen_0")
        assert result.is_secure is True

    def test_dc_fallback_on_no_network(self):
        from esfex.analysis.contingency import build_contingency_from_state

        state = _make_state()
        bridge = PandapowerBridge(state)
        # Don't build network → bridge._net is None
        dc_analyzer = build_contingency_from_state(state, len(state.nodes))
        ac_analyzer = ACContingencyAnalyzer(bridge, dc_fallback=dc_analyzer)

        snapshot = {
            "generators": {
                "gen_0": {"output_mw": 80.0, "capacity_mw": 100.0, "status": 1},
                "gen_1": {"output_mw": 50.0, "capacity_mw": 100.0, "status": 1},
            },
            "loads": {"load_node_0": {"demand_mw": 80.0}, "load_node_1": {"demand_mw": 50.0}},
            "lines": {},
            "batteries": {},
        }

        result = ac_analyzer.analyze_generator_loss(snapshot, "gen_0")
        assert isinstance(result, ACContingencyResult)
        assert result.ac_converged is False
        assert "[DC fallback]" in result.element_description


# ── Tests for shared ac_types module ──


class TestACTypesImport:
    """Test that ac_types can be imported independently."""

    def test_import_from_ac_types(self):
        from esfex.analysis.ac_types import ACPowerFlowResult, ShortCircuitResult
        r1 = ACPowerFlowResult()
        r2 = ShortCircuitResult()
        assert r1.converged is False
        assert r2.ik_ka == {}

    def test_ac_types_same_as_bridge_exports(self):
        """Ensure pandapower_bridge re-exports the same classes."""
        from esfex.analysis.ac_types import ACPowerFlowResult as AT
        from esfex.analysis.pandapower_bridge import ACPowerFlowResult as PB
        assert AT is PB


# ── Tests for NativeACBridge ──


class TestNativeACBridgeAvailability:
    """Test the is_available static method."""

    def test_returns_bool(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        assert isinstance(NativeACBridge.is_available(), bool)


class TestNativeACBridgeInterface:
    """Test the NativeACBridge duck-typed interface (without Julia)."""

    def test_get_network_before_build(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        assert bridge.get_network() is None

    def test_set_element_before_build(self):
        """set_element_in_service should not crash before build."""
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        bridge.set_element_in_service("line", "line_0", False)  # no-op

    def test_rerun_before_build(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        result = bridge.rerun_power_flow()
        assert result.converged is False

    def test_gen_id_mappings(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        # Before build, mappings should be empty
        assert bridge._gen_id_to_pp == {}
        assert bridge._sgen_id_to_pp == {}


@pytest.mark.julia
@pytest.mark.skipif(not HAS_NATIVE, reason="Julia/NativeACBridge not available")
class TestNativeACBridgePowerFlow:
    """Test AC power flow execution via native Julia NR solver.

    Requires a fully instantiated ESFEX Julia environment (not merely an
    importable juliacall), so it carries the ``julia`` marker and is skipped
    by ``-m "not julia"`` in lightweight/CI runs.
    """

    def test_power_flow_converges(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert result.converged is True

    def test_bus_results_populated(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert len(result.bus_vm_pu) == 2
        assert all(0.5 < v < 1.5 for v in result.bus_vm_pu.values())

    def test_line_results_populated(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state(num_lines=1)
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert len(result.line_p_from_mw) >= 1

    def test_losses_computed(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state(num_lines=1)
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        result = bridge.run_power_flow(scenario)
        assert result.total_losses_mw >= 0.0

    def test_rerun_after_line_outage(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state(num_nodes=3, num_lines=2)
        bridge = NativeACBridge(state)
        scenario = HypotheticalScenario(
            gen_outputs={"gen_0": 80.0, "gen_1": 50.0},
            gen_status={"gen_0": True, "gen_1": True},
            node_demands={0: 80.0, 1: 50.0, 2: 0.0},
        )
        pf1 = bridge.run_power_flow(scenario)
        if not pf1.converged:
            pytest.skip("Initial PF did not converge")

        bridge.set_element_in_service("line", "line_0", False)
        pf2 = bridge.rerun_power_flow()
        assert isinstance(pf2.converged, bool)

    def test_set_gen_out_of_service(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        state = _make_state()
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        bridge.run_power_flow(scenario)

        bridge.set_element_in_service("gen", "gen_0", False)
        result = bridge.rerun_power_flow()
        assert isinstance(result.converged, bool)

    def test_contingency_analyzer_with_native(self):
        from esfex.analysis.native_ac_bridge import NativeACBridge
        from esfex.analysis.snapshot_builder import build_snapshot_from_scenario

        state = _make_state(num_lines=1)
        bridge = NativeACBridge(state)
        scenario = _make_scenario()
        pf = bridge.run_power_flow(scenario)
        snapshot = build_snapshot_from_scenario(state, scenario)

        analyzer = ACContingencyAnalyzer(bridge)
        result = analyzer.analyze_generator_loss(snapshot, "gen_1")
        assert isinstance(result, ACContingencyResult)
        assert result.contingency_type == "generator"
