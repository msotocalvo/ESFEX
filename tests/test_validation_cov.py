"""Coverage tests for esfex.visualization.data.validation.

These tests exercise the dataclasses, the validator-registry helpers
(``count_validators`` / ``validate_state``), and each individual
``_validate_*`` validator with hand-built ``GuiSystemState`` objects.

The target module imports cleanly without PySide6 in this environment,
so no Qt stub is required; we import the real gui_model dataclasses.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Import helpers -- install a minimal PySide6 stub ONLY if a real, working
# PySide6 is unavailable and the target module genuinely needs it.  The
# module imports fine without Qt today, so this is defensive.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - environment dependent
    import PySide6.QtWidgets  # noqa: F401
    _PYSIDE6_AVAILABLE = True
except Exception:  # pragma: no cover
    _PYSIDE6_AVAILABLE = False

if not _PYSIDE6_AVAILABLE:  # pragma: no cover
    _qtcore = ModuleType("PySide6.QtCore")
    _qtcore.QObject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
    _qtcore.Signal = lambda *a, **kw: property(lambda self: None)
    _pyside6 = ModuleType("PySide6")
    sys.modules.setdefault("PySide6", _pyside6)
    sys.modules.setdefault("PySide6.QtCore", _qtcore)

try:
    from esfex.visualization.data import validation as V
    from esfex.visualization.data.gui_model import (
        EndpointRef,
        GuiACDCConverter,
        GuiBatteryInstance,
        GuiBus,
        GuiElectrolyzerInstance,
        GuiFrequencyConverter,
        GuiFuel,
        GuiFuelEntryPoint,
        GuiFuelStorage,
        GuiFuelTransportRoute,
        GuiGeneratorInstance,
        GuiNode,
        GuiNodeDemand,
        GuiSystemState,
        GuiTechnology,
        GuiTransformer,
        GuiTransmissionLine,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(f"validation module unimportable: {exc}", allow_module_level=True)


# ── small builders ──────────────────────────────────────────────


def _node(index=0, name="N", peak=0.0, total=0.0):
    n = GuiNode(index=index, name=name)
    n.demand = GuiNodeDemand(peak_mw=peak, total_mwh=total)
    return n


def _bus(bus_id, node=0, role="connection", df=0.0, voltage=220.0):
    return GuiBus(bus_id=bus_id, name=bus_id, parent_node=node,
                  role=role, demand_fraction=df, voltage_kv=voltage)


def _gen(iid, bus="bus_0", node=0, fuel="Diesel", gen_type="Thermal",
         rated=100.0):
    return GuiGeneratorInstance(
        instance_id=iid, unit_key="uk", name=iid, gen_type=gen_type,
        fuel=fuel, bus=bus, node=node, rated_power=rated,
    )


def _line(lid, fb, tb, cap=100.0, **kw):
    return GuiTransmissionLine(line_id=lid, from_bus=fb, to_bus=tb,
                               capacity_mw=cap, **kw)


def _state(**kw):
    s = GuiSystemState()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _sev(issues, sev):
    return [i for i in issues if i.severity == sev]


def _msgs(issues):
    return " | ".join(i.message for i in issues)


# ── dataclasses ─────────────────────────────────────────────────


def test_validation_issue_defaults():
    iss = V.ValidationIssue(severity="error", category="X", message="m")
    assert iss.element_type == "" and iss.element_id == ""


def test_simplification_action_fields():
    a = V.SimplificationAction(action_type="remove_bus", element_id="b1",
                               reason="r")
    assert a.action_type == "remove_bus"


def test_infrastructure_suggestion_collateral_defaults():
    s = V.InfrastructureSuggestion(
        level="bus", equipment_type="generator", instance_ids=["g1", "g2"],
        target_bus="bus_0", target_unit_key="k", target_name="n",
        fuel="Diesel", gen_type="Thermal", total_rated_power=10.0,
        total_capacity=0.0, reduction=1,
    )
    assert s.buses_to_remove == [] and s.transformers_to_remove == []
    assert s.description == ""


def test_simplification_config_defaults():
    cfg = V.SimplificationConfig()
    assert cfg.small_generator_fraction == 0.01
    assert cfg.max_merge_distance_km == 50.0


def test_topology_suggestion_defaults():
    t = V.TopologySuggestion(action_type="radial_prune", level=2, description="d")
    assert t.slack_transfer is None and t.elements_removed == 0
    assert t.buses_to_merge == {}


def test_simplification_plan_defaults():
    p = V.SimplificationPlan(level=1)
    assert p.infrastructure_suggestions == [] and p.buses_before == 0


def test_haversine_zero_and_known():
    assert V._haversine_km(0, 0, 0, 0) == pytest.approx(0.0, abs=1e-9)
    # ~111 km per degree of latitude at the equator
    d = V._haversine_km(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111.19, abs=1.0)


# ── registry helpers ────────────────────────────────────────────


def test_category_constants_consistent():
    assert set(V.CATEGORY_ORDER) == set(V._CATEGORY_VALIDATORS.keys())


def test_count_validators_all():
    expected = sum(len(v) for v in V._CATEGORY_VALIDATORS.values())
    assert V.count_validators() == expected
    assert V.count_validators(None) == expected


def test_count_validators_subset_and_unknown():
    assert V.count_validators({"demand"}) == 1
    # unknown categories are ignored
    assert V.count_validators({"demand", "does_not_exist"}) == 1
    assert V.count_validators(set()) == 0


def test_validate_state_progress_and_empty_categories():
    calls = []
    state = _state(nodes=[_node()])
    issues = V.validate_state(state, categories=set(),
                              progress_callback=lambda s, t, d: calls.append((s, t, d)))
    # No validators selected -> no issues, but a final "complete" callback
    assert issues == []
    assert calls and calls[-1][2] == "Validation complete"
    assert calls[-1][0] == calls[-1][1] == 0


def test_validate_state_runs_structural_only():
    # Empty nodes triggers the structural "no nodes" error
    state = _state()
    steps = []
    issues = V.validate_state(state, categories={"structural"},
                              progress_callback=lambda s, t, d: steps.append(d))
    assert any(i.category == "Node" and "no nodes" in i.message for i in issues)
    # structural has 3 validators -> 3 "Checking ..." + 1 complete
    assert any("Checking Structural" in d for d in steps)


def test_validate_state_default_all_categories():
    state = _state(nodes=[_node(0, "A", peak=10.0)])
    issues = V.validate_state(state)
    assert isinstance(issues, list)


# ── _validate_nodes ─────────────────────────────────────────────


def test_validate_nodes_empty():
    issues = V._validate_nodes(_state())
    assert len(issues) == 1 and issues[0].severity == "error"


def test_validate_nodes_duplicate_indices():
    state = _state(nodes=[_node(0, "A"), _node(0, "B")],
                   buses={"bus_0": _bus("bus_0", node=0)})
    issues = V._validate_nodes(state)
    assert any("Duplicate node indices" in i.message for i in issues)


def test_validate_nodes_no_buses_warning():
    state = _state(nodes=[_node(5, "Lonely")])
    issues = V._validate_nodes(state)
    warns = _sev(issues, "warning")
    assert any("has no buses" in w.message and w.element_id == "5" for w in warns)


def test_validate_nodes_with_bus_no_warning():
    state = _state(nodes=[_node(3, "Ok")],
                   buses={"b": _bus("b", node=3)})
    assert _validate_no_node_warning(state)


def _validate_no_node_warning(state):
    return not any("has no buses" in i.message
                   for i in V._validate_nodes(state))


# ── _validate_lines ─────────────────────────────────────────────


def test_validate_lines_duplicate_id():
    state = _state(
        buses={"bus_0": _bus("bus_0"), "bus_1": _bus("bus_1")},
        transmission_lines=[_line("L", "bus_0", "bus_1"),
                            _line("L", "bus_0", "bus_1")],
    )
    issues = V._validate_lines(state)
    assert any("Duplicate line ID" in i.message for i in issues)


def test_validate_lines_self_loop_and_missing_bus():
    state = _state(
        buses={"bus_0": _bus("bus_0")},
        transmission_lines=[_line("L", "bus_0", "bus_0")],
    )
    issues = V._validate_lines(state)
    assert any("self-loop" in i.message for i in issues)


def test_validate_lines_missing_endpoints():
    state = _state(
        buses={"bus_0": _bus("bus_0")},
        transmission_lines=[_line("L", "bus_0", "ghost")],
    )
    issues = V._validate_lines(state)
    assert any("to_bus 'ghost' does not exist" in i.message for i in issues)


def test_validate_lines_zero_capacity_warning():
    state = _state(
        buses={"bus_0": _bus("bus_0"), "bus_1": _bus("bus_1")},
        transmission_lines=[_line("L", "bus_0", "bus_1", cap=0.0)],
    )
    issues = V._validate_lines(state)
    assert any("capacity is zero" in i.message and i.severity == "warning"
               for i in issues)


def test_validate_lines_skips_wire_decorative():
    ln = _line("W", "bus_0", "bus_0", cap=0.0)
    ln.decorative = True
    state = _state(buses={"bus_0": _bus("bus_0")},
                   transmission_lines=[ln])
    # decorative wire-line -> self-loop / zero cap suppressed
    issues = V._validate_lines(state)
    assert not any("self-loop" in i.message or "capacity is zero" in i.message
                   for i in issues)


def test_validate_lines_self_loop_not_flagged_with_distinct_endpoints():
    ln = _line("L", "bus_0", "bus_0", cap=50.0)
    ln.from_endpoint = EndpointRef(element_type="bus", element_id="bus_0")
    ln.to_endpoint = EndpointRef(element_type="generator", element_id="g1")
    state = _state(buses={"bus_0": _bus("bus_0")}, transmission_lines=[ln])
    issues = V._validate_lines(state)
    assert not any("self-loop" in i.message for i in issues)


# ── _validate_line_connections ──────────────────────────────────


def test_validate_line_connections_skips_legacy_no_endpoints():
    state = _state(transmission_lines=[_line("L", "bus_0", "bus_1")])
    assert V._validate_line_connections(state) == []


def test_validate_line_connections_invalid():
    ln = _line("L", "bus_0", "bus_1")
    ln.from_endpoint = EndpointRef(element_type="generator", element_id="g1")
    ln.to_endpoint = EndpointRef(element_type="generator", element_id="g2")
    state = _state(transmission_lines=[ln])
    issues = V._validate_line_connections(state)
    assert len(issues) == 1 and issues[0].severity == "error"
    assert issues[0].category == "Connectivity"


# ── _validate_buses ─────────────────────────────────────────────


def test_validate_buses_orphan():
    state = _state(nodes=[_node(0, "A")],
                   buses={"b": _bus("b", node=99)})
    issues = V._validate_buses(state)
    assert any("parent node 99 does not exist" in i.message for i in issues)


def test_validate_buses_no_equipment_warning():
    state = _state(nodes=[_node(0, "A")],
                   buses={"b": _bus("b", node=0)})
    issues = V._validate_buses(state)
    assert any("no equipment, no connections, no demand" in i.message
               for i in issues)


def test_validate_buses_connection_role_with_demand_error():
    state = _state(nodes=[_node(0, "A")],
                   buses={"b": _bus("b", node=0, role="connection", df=0.5)})
    issues = V._validate_buses(state)
    assert any("role='connection' requires" in i.message
               and i.severity == "error" for i in issues)


def test_validate_buses_demand_fraction_sum_error():
    state = _state(
        nodes=[_node(0, "A")],
        buses={
            "b1": _bus("b1", node=0, role="load", df=0.5),
            "b2": _bus("b2", node=0, role="load", df=0.3),
        },
    )
    issues = V._validate_buses(state)
    assert any("sum to 0.8000, expected 1.0" in i.message for i in issues)


def test_validate_buses_demand_fraction_sum_ok():
    state = _state(
        nodes=[_node(0, "A")],
        buses={
            "b1": _bus("b1", node=0, role="load", df=0.5),
            "b2": _bus("b2", node=0, role="mixed", df=0.5),
        },
    )
    issues = V._validate_buses(state)
    assert not any("expected 1.0" in i.message for i in issues)


def test_validate_buses_equipment_bad_bus():
    state = _state(
        nodes=[_node(0, "A")],
        buses={"b": _bus("b", node=0)},
        generators={"g1": _gen("g1", bus="ghost")},
        batteries={"x1": GuiBatteryInstance(instance_id="x1", unit_key="k",
                                            name="x1", bus="ghost")},
        electrolyzers={"e1": GuiElectrolyzerInstance(instance_id="e1",
                                                     unit_key="k", name="e1",
                                                     bus="ghost")},
    )
    issues = V._validate_buses(state)
    txt = _msgs(issues)
    assert "Generator 'g1'" in txt and "Battery 'x1'" in txt
    assert "Electrolyzer 'e1'" in txt


# ── _validate_generators ────────────────────────────────────────


def test_validate_generators_bad_bus():
    state = _state(buses={}, generators={"g": _gen("g", bus="nope")})
    issues = V._validate_generators(state)
    assert any("bus 'nope' does not exist" in i.message for i in issues)


def test_validate_generators_node_mismatch_warning():
    state = _state(
        nodes=[_node(0, "A"), _node(1, "B")],
        buses={"b": _bus("b", node=1)},
        generators={"g": _gen("g", bus="b", node=0)},
    )
    issues = V._validate_generators(state)
    assert any("doesn't match bus" in i.message and i.severity == "warning"
               for i in issues)


def test_validate_generators_negative_and_minpower():
    g_neg = _gen("g1", bus="b", rated=-5.0)
    g_min = _gen("g2", bus="b", rated=100.0)
    g_min.min_power = 200.0
    state = _state(buses={"b": _bus("b")},
                   generators={"g1": g_neg, "g2": g_min})
    issues = V._validate_generators(state)
    txt = _msgs(issues)
    assert "negative rated_power" in txt
    assert "min_power (200.0) > rated_power (100.0)" in txt


def test_validate_generators_efficiency_and_lifetime():
    g = _gen("g", bus="b", fuel="Diesel", rated=10.0)
    g.eff_at_rated = 1.5
    g.initial_age = 30
    g.life_time = 25
    state = _state(buses={"b": _bus("b")}, generators={"g": g})
    issues = V._validate_generators(state)
    txt = _msgs(issues)
    assert "eff_at_rated=1.5 out of valid range" in txt
    assert "unit starts retired" in txt


def test_validate_generators_renewable_eff_skipped():
    g = _gen("g", bus="b", fuel="Sun", rated=10.0)
    g.eff_at_rated = 5.0  # would be flagged if non-renewable
    state = _state(buses={"b": _bus("b")}, generators={"g": g})
    issues = V._validate_generators(state)
    assert not any("eff_at_rated" in i.message for i in issues)


# ── _validate_batteries ─────────────────────────────────────────


def test_validate_batteries_efficiency_and_capacity():
    bat = GuiBatteryInstance(instance_id="x", unit_key="k", name="x", bus="b",
                             rated_power=100.0, capacity=10.0)
    bat.efficiency_charge = 1.5
    bat.efficiency_discharge = 0.0
    state = _state(buses={"b": _bus("b")}, batteries={"x": bat})
    issues = V._validate_batteries(state)
    txt = _msgs(issues)
    assert "efficiency_charge=1.5 out of valid range" in txt
    assert "efficiency_discharge=0.0 out of valid range" in txt
    assert "less than 1 hour at full power" in txt


def test_validate_batteries_negative_and_lifetime():
    bat = GuiBatteryInstance(instance_id="x", unit_key="k", name="x", bus="b",
                             rated_power=-1.0)
    bat2 = GuiBatteryInstance(instance_id="y", unit_key="k", name="y", bus="b",
                              rated_power=5.0, capacity=50.0,
                              initial_age=99, life_time=20)
    state = _state(buses={"b": _bus("b")}, batteries={"x": bat, "y": bat2})
    issues = V._validate_batteries(state)
    txt = _msgs(issues)
    assert "negative rated_power" in txt
    assert "unit starts retired" in txt


# ── _validate_transformers ──────────────────────────────────────


def test_validate_transformers_missing_selfloop_voltage_power():
    tr = GuiTransformer(name="T1", from_bus="b", to_bus="b",
                        rated_power_mva=0.0)
    state = _state(buses={"b": _bus("b", voltage=220.0)},
                   transformers=[tr])
    issues = V._validate_transformers(state)
    txt = _msgs(issues)
    assert "self-loop" in txt
    assert "same voltage on both sides" in txt
    assert "rated_power_mva is zero or negative" in txt


def test_validate_transformers_missing_bus():
    tr = GuiTransformer(name="T", from_bus="x", to_bus="y")
    state = _state(buses={}, transformers=[tr])
    issues = V._validate_transformers(state)
    txt = _msgs(issues)
    assert "from_bus 'x' does not exist" in txt
    assert "to_bus 'y' does not exist" in txt


# ── _validate_converters ────────────────────────────────────────


def test_validate_converters_acdc_errors():
    c = GuiACDCConverter(name="C", from_bus="b", to_bus="b",
                         efficiency_rectify=0.0, efficiency_invert=1.5)
    state = _state(buses={"b": _bus("b")}, acdc_converters=[c])
    issues = V._validate_converters(state)
    txt = _msgs(issues)
    assert "self-loop" in txt
    assert "efficiency must be > 0" in txt
    assert "efficiency > 1.0 is not physical" in txt


def test_validate_converters_freq_errors():
    c = GuiFrequencyConverter(name="F", from_bus="x", to_bus="b",
                              efficiency_a_to_b=0.0, efficiency_b_to_a=2.0)
    state = _state(buses={"b": _bus("b")}, freq_converters=[c])
    issues = V._validate_converters(state)
    txt = _msgs(issues)
    assert "from_bus 'x' does not exist" in txt
    assert "efficiency must be > 0" in txt
    assert "efficiency > 1.0 is not physical" in txt


# ── _validate_demand ────────────────────────────────────────────


def test_validate_demand_equipment_no_demand():
    state = _state(
        nodes=[_node(0, "A", peak=0.0)],
        generators={"g": _gen("g", node=0, rated=10.0)},
    )
    issues = V._validate_demand(state)
    txt = _msgs(issues)
    assert "has generation equipment but no demand data" in txt
    assert "zero total peak demand" in txt


def test_validate_demand_csv_path_unloaded_warning(tmp_path):
    n = _node(0, "A", peak=10.0)
    n.demand = GuiNodeDemand(csv_path=str(tmp_path / "missing.csv"),
                             data=None, peak_mw=10.0)
    state = _state(nodes=[n])
    issues = V._validate_demand(state)
    assert any("could not be loaded" in i.message for i in issues)


def test_validate_demand_length_mismatch_and_negative():
    n1 = _node(0, "A", peak=10.0)
    n1.demand = GuiNodeDemand(data=[1.0, 2.0, 3.0], num_hours=3, peak_mw=10.0)
    n2 = _node(1, "B", peak=5.0)
    n2.demand = GuiNodeDemand(data=[1.0, -4.0], num_hours=2, peak_mw=5.0)
    state = _state(nodes=[n1, n2])
    issues = V._validate_demand(state)
    txt = _msgs(issues)
    assert "different lengths" in txt
    assert "negative values" in txt


def test_validate_demand_loads_csv(tmp_path):
    csv = tmp_path / "d.csv"
    csv.write_text("10\n20\n30\n")
    n = _node(0, "A")
    n.demand = GuiNodeDemand(csv_path=str(csv), data=None)
    state = _state(nodes=[n])
    V._validate_demand(state)
    # _load_demand_for_nodes should have populated the node demand
    assert n.demand.data == [10.0, 20.0, 30.0]
    assert n.demand.peak_mw == 30.0


# ── _load_demand_for_nodes ──────────────────────────────────────


def test_load_demand_noop_when_nothing_to_load():
    n = _node(0, "A")
    n.demand = GuiNodeDemand(data=[1.0], num_hours=1)
    # no csv_path -> returns immediately, no mutation/exception
    V._load_demand_for_nodes([n])
    assert n.demand.data == [1.0]


def test_load_demand_single_column_shared_refused(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text("5\n6\n")
    n0 = _node(0, "A")
    n0.demand = GuiNodeDemand(csv_path=str(csv), data=None)
    n1 = _node(1, "B")
    n1.demand = GuiNodeDemand(csv_path=str(csv), data=None)
    V._load_demand_for_nodes([n0, n1])
    # single-column shared by 2 nodes -> refuses to broadcast
    assert n0.demand.data is None and n1.demand.data is None


def test_load_demand_multicolumn_by_index(tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text("1,100\n2,200\n")
    n = _node(1, "B")  # index 1 -> second column
    n.demand = GuiNodeDemand(csv_path=str(csv), data=None)
    V._load_demand_for_nodes([n])
    assert n.demand.data == [100.0, 200.0]


# ── _validate_generation ────────────────────────────────────────


def test_validate_generation_adequacy_warning():
    state = _state(
        nodes=[_node(0, "A", peak=1000.0)],
        generators={"g": _gen("g", rated=100.0)},
    )
    issues = V._validate_generation(state)
    assert any("Adequacy ratio" in i.message and i.severity == "warning"
               for i in issues)


def test_validate_generation_renewable_no_availability_and_zero_power():
    g_ren = _gen("g1", fuel="Sun", gen_type="Renewable", rated=10.0)
    g_ren.availability_file = None
    g_zero = _gen("g2", rated=0.0)
    state = _state(nodes=[_node(0, "A", peak=5.0)],
                   generators={"g1": g_ren, "g2": g_zero})
    issues = V._validate_generation(state)
    txt = _msgs(issues)
    assert "no availability file" in txt
    assert any("zero rated power" in i.message and i.severity == "info"
               for i in issues)


def test_validate_generation_missing_availability_file(tmp_path):
    g = _gen("g", gen_type="Renewable", fuel="Wind", rated=10.0)
    g.availability_file = str(tmp_path / "nope.csv")
    state = _state(nodes=[_node(0, "A", peak=5.0)], generators={"g": g})
    issues = V._validate_generation(state)
    assert any("availability file not found" in i.message for i in issues)


# ── _validate_fuel_catalog ──────────────────────────────────────


def test_validate_fuel_catalog_empty_fuel_error():
    g = _gen("g", fuel="", gen_type="Thermal", rated=10.0)
    g.type = "thermal"  # used by getattr(gen, "type", "")
    state = _state(generators={"g": g})
    issues = V._validate_fuel_catalog(state)
    assert any("non-renewable" in i.message and "empty fuel" in i.message
               and i.severity == "error" for i in issues)


def test_validate_fuel_catalog_unregistered_fuel_warning():
    g = _gen("g", fuel="Coal", gen_type="Thermal", rated=10.0)
    g.type = "thermal"
    state = _state(generators={"g": g}, fuels={})
    issues = V._validate_fuel_catalog(state)
    assert any("not registered in the system fuel catalog" in i.message
               for i in issues)


def test_validate_fuel_catalog_registered_alias_ok():
    g = _gen("g", fuel="Fuel Oil", gen_type="Thermal", rated=10.0)
    g.type = "thermal"
    fuel = GuiFuel(fuel_id="Fuel_oil", name="Fuel Oil")
    state = _state(generators={"g": g}, fuels={"Fuel_oil": fuel})
    issues = V._validate_fuel_catalog(state)
    assert not any("not registered" in i.message for i in issues)


def test_validate_fuel_catalog_technology_missing():
    g = _gen("g", fuel="Sun", gen_type="Renewable", rated=10.0)
    g.type = "renewable"
    g.technology = "T_ghost"
    state = _state(generators={"g": g}, technologies={})
    issues = V._validate_fuel_catalog(state)
    assert any("technology 'T_ghost' is not registered" in i.message
               for i in issues)


def test_validate_fuel_catalog_technology_empty_fuel_error():
    tech = GuiTechnology(tech_id="T1", name="GasTech", category="Thermal",
                         fuel="")
    state = _state(technologies={"T1": tech})
    issues = V._validate_fuel_catalog(state)
    assert any("non-renewable tech has empty fuel" in i.message
               and i.severity == "error" for i in issues)


# ── _validate_fuel_entries ──────────────────────────────────────


def test_validate_fuel_entries_bad_node():
    fe = GuiFuelEntryPoint(name="Port", node=99)
    state = _state(nodes=[_node(0, "A")], fuel_entry_points=[fe])
    issues = V._validate_fuel_entries(state)
    assert len(issues) == 1 and "node 99 does not exist" in issues[0].message


def test_validate_fuel_entries_ok():
    fe = GuiFuelEntryPoint(name="Port", node=0)
    state = _state(nodes=[_node(0, "A")], fuel_entry_points=[fe])
    assert V._validate_fuel_entries(state) == []


# ── _validate_fuel_network ──────────────────────────────────────


def test_validate_fuel_network_missing_fuel_supply():
    g = _gen("g", fuel="Coal", rated=10.0)
    state = _state(generators={"g": g})
    issues = V._validate_fuel_network(state)
    assert any("not supplied by any fuel entry" in i.message
               for i in issues)


def test_validate_fuel_network_entry_no_fuels_warning():
    fe = GuiFuelEntryPoint(name="Empty", node=0)
    fe.fuels = []
    state = _state(nodes=[_node(0, "A")], fuel_entry_points=[fe])
    issues = V._validate_fuel_network(state)
    assert any("no fuels assigned" in i.message for i in issues)


def test_validate_fuel_network_route_bad_node_and_zero_cap():
    rt = GuiFuelTransportRoute(route_id="R", from_node=0, to_node=99,
                               capacity=0.0)
    state = _state(nodes=[_node(0, "A")], fuel_transport_routes=[rt])
    issues = V._validate_fuel_network(state)
    txt = _msgs(issues)
    assert "to_node 99 does not exist" in txt
    assert "zero or negative capacity" in txt


def test_validate_fuel_network_storage_no_fuels():
    fs = GuiFuelStorage(storage_id="S1", name="Depot")
    fs.fuels = []
    state = _state(fuel_storages={"S1": fs})
    issues = V._validate_fuel_network(state)
    assert any("Fuel storage 'Depot'" in i.message and "no fuels" in i.message
               for i in issues)


def test_validate_fuel_network_supplied_ok():
    g = _gen("g", fuel="Coal", rated=10.0)
    fe = GuiFuelEntryPoint(name="Port", node=0)
    fe.fuels = ["Coal"]
    state = _state(nodes=[_node(0, "A")], generators={"g": g},
                   fuel_entry_points=[fe])
    issues = V._validate_fuel_network(state)
    assert not any("not supplied by any fuel entry" in i.message
                   for i in issues)


# ── _validate_connectivity ──────────────────────────────────────


def test_validate_connectivity_single_node_noop():
    state = _state(nodes=[_node(0, "A")])
    assert V._validate_connectivity(state) == []


def test_validate_connectivity_disconnected_components():
    # Two nodes, no lines -> two components
    state = _state(
        nodes=[_node(0, "A", peak=10.0), _node(1, "B", peak=5.0)],
        buses={"b0": _bus("b0", node=0), "b1": _bus("b1", node=1)},
    )
    issues = V._validate_connectivity(state)
    assert any("disconnected sub-networks" in i.message
               and i.severity == "info" for i in issues)


def test_validate_connectivity_isolated_with_equipment():
    state = _state(
        nodes=[_node(0, "A", peak=10.0), _node(1, "B")],
        buses={"b0": _bus("b0", node=0), "b1": _bus("b1", node=1)},
        transmission_lines=[],
        generators={"g": _gen("g", bus="b1", node=1, rated=5.0)},
    )
    issues = V._validate_connectivity(state)
    assert any("is isolated but has" in i.message and "generators" in i.message
               for i in issues)


def test_validate_connectivity_fully_connected():
    state = _state(
        nodes=[_node(0, "A", peak=10.0), _node(1, "B", peak=5.0)],
        buses={"b0": _bus("b0", node=0), "b1": _bus("b1", node=1)},
        transmission_lines=[_line("L", "b0", "b1")],
    )
    issues = V._validate_connectivity(state)
    assert not any("disconnected" in i.message for i in issues)
    assert not any("isolated" in i.message for i in issues)


# ── bus adjacency / dead-end helpers ────────────────────────────


def test_build_bus_adjacency_lines_and_removed():
    state = _state(
        buses={"a": _bus("a"), "b": _bus("b"), "c": _bus("c")},
        transmission_lines=[_line("L1", "a", "b"), _line("L2", "b", "c")],
    )
    active = {"a", "b", "c"}
    adj = V._build_bus_adjacency(state, active)
    assert adj["b"] == {"a", "c"}
    adj2 = V._build_bus_adjacency(state, active, removed_lines={"L2"})
    assert adj2["c"] == set() and adj2["b"] == {"a"}


def test_bus_has_useful_equipment():
    state = _state(
        buses={"a": _bus("a"), "b": _bus("b")},
        generators={"g": _gen("g", bus="a", rated=10.0)},
    )
    assert V._bus_has_useful_equipment(state, "a") is True
    assert V._bus_has_useful_equipment(state, "b") is False


def test_bus_has_demand():
    state = _state(
        nodes=[_node(0, "A", peak=10.0)],
        buses={"a": _bus("a", node=0, role="load", df=0.5),
               "b": _bus("b", node=0, role="connection", df=0.0)},
    )
    assert V._bus_has_demand(state, "a") is True
    assert V._bus_has_demand(state, "b") is False
    assert V._bus_has_demand(state, "missing") is False


def test_find_dead_end_buses_empty():
    assert V._find_dead_end_buses(_state()) == []


def test_find_dead_end_buses_prunes_stub():
    # a -- b ; b is a leaf with no equipment/demand -> removed (and stub line)
    state = _state(
        nodes=[_node(0, "A", peak=10.0)],
        buses={"a": _bus("a", node=0, role="load", df=1.0),
               "b": _bus("b", node=0, role="connection", df=0.0)},
        transmission_lines=[_line("L", "a", "b")],
        generators={"g": _gen("g", bus="a", node=0, rated=10.0)},
    )
    actions = V._find_dead_end_buses(state)
    removed_buses = {a.element_id for a in actions
                     if a.action_type == "remove_bus"}
    removed_lines = {a.element_id for a in actions
                     if a.action_type == "remove_line"}
    assert "b" in removed_buses
    assert "L" in removed_lines
    assert "a" not in removed_buses  # has demand + generation


# ── fuel adjacency helpers ──────────────────────────────────────


def test_build_fuel_adjacency():
    state = _state(
        fuel_transport_routes=[
            GuiFuelTransportRoute(route_id="R1", from_node=0, to_node=1),
            GuiFuelTransportRoute(route_id="R2", from_node=1, to_node=2),
        ],
    )
    adj = V._build_fuel_adjacency(state, {0, 1, 2})
    assert adj[1] == {0, 2}
    adj2 = V._build_fuel_adjacency(state, {0, 1, 2}, removed_routes={"R2"})
    assert adj2[2] == set()


def test_fuel_node_has_consumers():
    state = _state(
        generators={
            "g": _gen("g", node=0, fuel="Coal", rated=10.0),
            "r": _gen("r", node=1, fuel="Sun", gen_type="Renewable", rated=10.0),
        },
    )
    assert V._fuel_node_has_consumers(state, 0) is True
    # node 1 only has a renewable -> not a fuel consumer
    assert V._fuel_node_has_consumers(state, 1) is False
    assert V._fuel_node_has_consumers(state, 5) is False
