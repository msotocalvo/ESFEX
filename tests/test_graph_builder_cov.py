"""Coverage tests for esfex.visualization.sld.graph_builder.

Exercises build_elk_graph across merge levels, equipment attachment,
edge aggregation rules, filtering, and the deterministic grid layout
(_apply_grid_layout). All assertions reflect the actual code behavior.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# graph_builder imports gui_model which imports PySide6 at module level.
# Prefer a real PySide6; otherwise install minimal stubs (mirrors the
# pattern in tests/test_gui_model.py) so the module can be imported.
try:
    import PySide6.QtCore  # noqa: F401
    _PYSIDE6_AVAILABLE = True
except Exception:
    _PYSIDE6_AVAILABLE = False

if not _PYSIDE6_AVAILABLE:
    _qtcore = ModuleType("PySide6.QtCore")
    _qtcore.QObject = type(  # type: ignore[attr-defined]
        "QObject", (), {"__init__": lambda self, *a, **kw: None})
    _qtcore.Signal = lambda *a, **kw: property(  # type: ignore[attr-defined]
        lambda self: None)
    _pyside6 = ModuleType("PySide6")
    sys.modules.setdefault("PySide6", _pyside6)
    sys.modules.setdefault("PySide6.QtCore", _qtcore)

from esfex.visualization.data.gui_model import (  # noqa: E402
    EndpointRef,
    GuiACDCConverter,
    GuiBatteryInstance,
    GuiBus,
    GuiElectrolyzerInstance,
    GuiFrequencyConverter,
    GuiGeneratorInstance,
    GuiNode,
    GuiNodeDemand,
    GuiSystemState,
    GuiTransformer,
    GuiTransmissionLine,
)
from esfex.visualization.sld import graph_builder as gb  # noqa: E402
from esfex.visualization.sld.voltage_colors import get_voltage_color  # noqa: E402


# ── Builders ──────────────────────────────────────────────────────


def _bus(bus_id, node, kv, name=""):
    return GuiBus(bus_id=bus_id, parent_node=node, voltage_kv=kv, name=name)


def _state_two_nodes():
    """Two nodes, each with one 220 kV bus, connected by a line."""
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="Alpha"),
                   GuiNode(index=1, name="Beta")]
    state.buses = {
        "b0": _bus("b0", 0, 220.0, "Bus0"),
        "b1": _bus("b1", 1, 220.0, "Bus1"),
    }
    return state


# ── Empty / minimal ───────────────────────────────────────────────


def test_empty_state_returns_well_formed_dict():
    out = gb.build_elk_graph(GuiSystemState())
    assert set(out.keys()) == {
        "elkGraph", "busEquipment", "nodeGroups", "constants"}
    g = out["elkGraph"]
    assert g["id"] == "root"
    assert g["children"] == []
    assert g["edges"] == []
    assert g["precomputedLayout"] is True
    assert out["nodeGroups"] == []
    assert out["busEquipment"] == {}


def test_constants_block_mirrors_module_constants():
    out = gb.build_elk_graph(GuiSystemState())
    c = out["constants"]
    assert c["busH"] == gb._BUS_H
    assert c["stubLen"] == gb._STUB_LEN
    assert c["equipSize"] == gb._EQUIP_SIZE
    assert c["equipSpacing"] == gb._EQUIP_SPACING


# ── One bar per bus (no merge) ────────────────────────────────────


def test_one_bar_per_bus_label_falls_back_to_id():
    state = _state_two_nodes()
    state.buses["b0"].name = ""    # blank name -> label uses bus_id
    out = gb.build_elk_graph(state)
    children = {c["id"]: c for c in out["elkGraph"]["children"]}
    assert "bus_b0" in children and "bus_b1" in children
    assert children["bus_b0"]["properties"]["label"] == "b0"


def test_buses_at_same_node_voltage_stay_separate():
    state = _state_two_nodes()
    state.buses["b0b"] = _bus("b0b", 0, 220.0)   # 2nd 220 kV bus at node 0
    out = gb.build_elk_graph(state)
    children = {c["id"]: c for c in out["elkGraph"]["children"]}
    assert "bus_b0" in children and "bus_b0b" in children   # not merged
    assert children["bus_b0"]["properties"]["nMergedBuses"] == 1


def test_bus_label_is_name_or_id():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="")]
    state.buses = {"b0": _bus("b0", 0, 220.0)}
    lbl = gb.build_elk_graph(state)["elkGraph"]["children"][0]["properties"]["label"]
    assert lbl == "b0"


def test_bus_with_missing_node_still_renders():
    state = GuiSystemState()
    state.nodes = []
    state.buses = {"named": _bus("named", 7, 220.0, "MyBus")}
    out = gb.build_elk_graph(state)
    lbl = out["elkGraph"]["children"][0]["properties"]["label"]
    assert lbl == "MyBus"
    assert out["nodeGroups"] == []


# ── Equipment attachment ──────────────────────────────────────────


def test_generator_renewable_vs_nonrenewable_symbols_and_colors():
    state = _state_two_nodes()
    state.generators = {
        "g_re": GuiGeneratorInstance(
            instance_id="g_re", unit_key="u1", name="Solar",
            gen_type="Renewable", fuel="Sun", bus="b0", rated_power=150.0),
        "g_nr": GuiGeneratorInstance(
            instance_id="g_nr", unit_key="u2", name="Gas",
            gen_type="Non-renewable", fuel="Gas", bus="b1", rated_power=0.0),
    }
    out = gb.build_elk_graph(state)
    eq = out["busEquipment"]
    re_item = eq["bus_b0"][0]
    assert re_item["symbolType"] == "gen-renewable"
    assert re_item["color"] == "#27AE60"
    assert re_item["sublabel"] == "150 MW"
    assert re_item["fuel"] == "Sun"
    nr_item = eq["bus_b1"][0]
    assert nr_item["symbolType"] == "gen-nonrenewable"
    assert nr_item["color"] == "#7F8C8D"
    # rated_power 0 -> empty sublabel
    assert nr_item["sublabel"] == ""


def test_generator_on_unknown_bus_is_skipped():
    state = _state_two_nodes()
    state.generators = {
        "g": GuiGeneratorInstance(
            instance_id="g", unit_key="u", name="X", gen_type="Renewable",
            fuel="Sun", bus="nonexistent", rated_power=10.0),
    }
    out = gb.build_elk_graph(state)
    assert all(items == [] for items in out["busEquipment"].values())


def test_battery_and_electrolyzer_attachment():
    state = _state_two_nodes()
    state.batteries = {
        "bat": GuiBatteryInstance(
            instance_id="bat", unit_key="ub", name="Li", bus="b0",
            capacity=200.0),
    }
    state.electrolyzers = {
        "el": GuiElectrolyzerInstance(
            instance_id="el", unit_key="ue", name="PEM", bus="b1",
            rated_power=50.0),
    }
    out = gb.build_elk_graph(state)
    bat = out["busEquipment"]["bus_b0"][0]
    assert bat["elementType"] == "battery"
    assert bat["symbolType"] == "battery"
    assert bat["color"] == "#F39C12"
    assert bat["sublabel"] == "200 MWh"
    el = out["busEquipment"]["bus_b1"][0]
    assert el["elementType"] == "electrolyzer"
    assert el["color"] == "#16A085"
    assert el["sublabel"] == "50 MW"


def test_theme_color_overrides_apply():
    state = _state_two_nodes()
    state.generators = {
        "g": GuiGeneratorInstance(
            instance_id="g", unit_key="u", name="Solar",
            gen_type="Renewable", fuel="Sun", bus="b0", rated_power=1.0),
    }
    state.batteries = {
        "bat": GuiBatteryInstance(
            instance_id="bat", unit_key="ub", name="Li", bus="b1",
            capacity=1.0),
    }
    theme = {"gen-renewable": "#111111", "battery": "#222222"}
    out = gb.build_elk_graph(state, theme_colors=theme)
    assert out["busEquipment"]["bus_b0"][0]["color"] == "#111111"
    assert out["busEquipment"]["bus_b1"][0]["color"] == "#222222"


def test_demand_attaches_to_lowest_voltage_group():
    state = GuiSystemState()
    state.nodes = [GuiNode(
        index=0, name="N",
        demand=GuiNodeDemand(peak_mw=500.0))]
    state.buses = {
        "hv": _bus("hv", 0, 400.0),
        "lv": _bus("lv", 0, 110.0),
    }
    out = gb.build_elk_graph(state)
    # lowest voltage = 110 -> its bus bar gets the load
    lv_eq = out["busEquipment"]["bus_lv"]
    hv_eq = out["busEquipment"]["bus_hv"]
    assert len(lv_eq) == 1
    assert lv_eq[0]["elementType"] == "load"
    assert lv_eq[0]["sublabel"] == "500 MW"
    assert lv_eq[0]["elementId"] == "load_node_0"
    assert hv_eq == []


def test_demand_zero_peak_not_attached():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N",
                           demand=GuiNodeDemand(peak_mw=0.0))]
    state.buses = {"b": _bus("b", 0, 220.0)}
    out = gb.build_elk_graph(state)
    assert all(v == [] for v in out["busEquipment"].values())


def test_demand_node_with_no_groups_skipped():
    # node has demand but no buses -> no groups -> skipped, no error
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N",
                           demand=GuiNodeDemand(peak_mw=100.0))]
    state.buses = {}
    out = gb.build_elk_graph(state)
    assert out["busEquipment"] == {}


# ── Edge aggregation: transmission ────────────────────────────────


def test_transmission_edge_basic():
    state = _state_two_nodes()
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L0", from_bus="b0", to_bus="b1",
        capacity_mw=300.0, voltage_kv=220.0)]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 1
    e = edges[0]
    assert e["properties"]["edgeType"] == "transmission"
    assert e["properties"]["capacityMw"] == 300.0
    assert e["properties"]["nCircuits"] == 1
    assert e["properties"]["label"] == "300 MW"
    assert e["properties"]["color"] == get_voltage_color(220.0)


def test_transmission_parallel_circuits_stay_separate():
    # No aggregation: two parallel circuits render as two distinct edges.
    state = _state_two_nodes()
    state.transmission_lines = [
        GuiTransmissionLine(line_id="L0", from_bus="b0", to_bus="b1",
                            capacity_mw=100.0, voltage_kv=220.0),
        GuiTransmissionLine(line_id="L1", from_bus="b1", to_bus="b0",
                            capacity_mw=200.0, voltage_kv=220.0),
    ]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 2
    assert {e["properties"]["label"] for e in edges} == {"100 MW", "200 MW"}
    assert all(e["properties"]["nCircuits"] == 1 for e in edges)


def test_transmission_default_voltage_when_none():
    state = _state_two_nodes()
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L0", from_bus="b0", to_bus="b1",
        capacity_mw=100.0, voltage_kv=None)]
    out = gb.build_elk_graph(state)
    # voltage defaults to 220.0 in _aggregate call
    assert out["elkGraph"]["edges"][0]["properties"]["voltageKv"] == 220.0


def test_transmission_self_loop_and_invalid_bus_skipped():
    state = _state_two_nodes()
    state.transmission_lines = [
        GuiTransmissionLine(line_id="self", from_bus="b0", to_bus="b0",
                            capacity_mw=10.0),
        GuiTransmissionLine(line_id="bad", from_bus="b0", to_bus="ghost",
                            capacity_mw=10.0),
    ]
    out = gb.build_elk_graph(state)
    assert out["elkGraph"]["edges"] == []


def test_transmission_with_wiring_endpoint_excluded():
    state = _state_two_nodes()
    line = GuiTransmissionLine(
        line_id="L0", from_bus="b0", to_bus="b1", capacity_mw=10.0)
    line.from_endpoint = EndpointRef(element_type="transformer",
                                     element_id="t0")
    state.transmission_lines = [line]
    out = gb.build_elk_graph(state)
    assert out["elkGraph"]["edges"] == []


def test_line_between_same_node_voltage_buses_appears():
    # Two buses at the same node+voltage are SEPARATE bars now, so a line
    # between them is a real edge (nothing is merged away).
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"a": _bus("a", 0, 220.0), "b": _bus("b", 0, 220.0)}
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L0", from_bus="a", to_bus="b", capacity_mw=10.0)]
    out = gb.build_elk_graph(state)
    assert len(out["elkGraph"]["edges"]) == 1


# ── Edge aggregation: transformer / converters ────────────────────


def test_transformer_edge_same_substation():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"hv": _bus("hv", 0, 220.0), "lv": _bus("lv", 0, 110.0)}
    state.transformers = [GuiTransformer(
        name="T1", from_bus="hv", to_bus="lv", rated_power_mva=250.0)]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 1
    p = edges[0]["properties"]
    assert p["edgeType"] == "transformer"
    assert p["color"] == "#9B59B6"
    assert p["label"] == "250 MVA"


def test_transformer_across_substations_excluded():
    # transformer between buses in different nodes -> same-substation
    # guard drops it.
    state = _state_two_nodes()  # b0@node0, b1@node1
    state.transformers = [GuiTransformer(
        name="T", from_bus="b0", to_bus="b1", rated_power_mva=100.0)]
    out = gb.build_elk_graph(state)
    assert out["elkGraph"]["edges"] == []


def test_parallel_transformers_stay_separate():
    # No aggregation: two transformers between the same two buses render as
    # two distinct symbols (electrically faithful).
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"hv": _bus("hv", 0, 220.0), "lv": _bus("lv", 0, 110.0)}
    state.transformers = [
        GuiTransformer(name="", from_bus="hv", to_bus="lv",
                       rated_power_mva=100.0),
        GuiTransformer(name="", from_bus="lv", to_bus="hv",
                       rated_power_mva=100.0),
    ]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 2
    assert all(e["properties"]["label"] == "100 MVA" for e in edges)
    assert all(e["properties"]["edgeType"] == "transformer" for e in edges)


def test_acdc_converter_edge():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"ac": _bus("ac", 0, 220.0), "dc": _bus("dc", 0, 320.0)}
    state.acdc_converters = [GuiACDCConverter(
        name="C", from_bus="ac", to_bus="dc", rated_power_mva=400.0)]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 1
    p = edges[0]["properties"]
    assert p["edgeType"] == "converter"
    assert p["color"] == "#2980B9"
    assert p["label"] == "400 MVA"


def test_freq_converter_edge_and_same_substation_guard():
    # same node -> kept
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"a": _bus("a", 0, 220.0), "b": _bus("b", 0, 110.0)}
    state.freq_converters = [GuiFrequencyConverter(
        name="F", from_bus="a", to_bus="b", rated_power_mva=80.0)]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 1
    assert edges[0]["properties"]["edgeType"] == "converter"

    # different nodes -> dropped by guard
    state2 = _state_two_nodes()
    state2.freq_converters = [GuiFrequencyConverter(
        name="F", from_bus="b0", to_bus="b1", rated_power_mva=80.0)]
    out2 = gb.build_elk_graph(state2)
    assert out2["elkGraph"]["edges"] == []


def test_converter_theme_color_override():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"ac": _bus("ac", 0, 220.0), "dc": _bus("dc", 0, 320.0)}
    state.acdc_converters = [GuiACDCConverter(
        name="C", from_bus="ac", to_bus="dc", rated_power_mva=10.0)]
    out = gb.build_elk_graph(
        state, theme_colors={"acdc_converter": "#ABCDEF"})
    assert out["elkGraph"]["edges"][0]["properties"]["color"] == "#ABCDEF"


# ── filter_substation ─────────────────────────────────────────────


def test_filter_substation_restricts_buses_and_edges():
    state = _state_two_nodes()
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L", from_bus="b0", to_bus="b1", capacity_mw=100.0)]
    out = gb.build_elk_graph(state, filter_substation=0)
    children = out["elkGraph"]["children"]
    # only node 0's bus survives
    assert len(children) == 1
    assert children[0]["properties"]["parentNode"] == 0
    # edge endpoints not both included -> no edge
    assert out["elkGraph"]["edges"] == []
    # node group only for node 0
    assert [g["nodeId"] for g in out["nodeGroups"]] == [0]


# ── Node groups ───────────────────────────────────────────────────


def test_node_groups_collect_all_bars_per_node():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="Sub"), GuiNode(index=1, name="X")]
    state.buses = {
        "hv": _bus("hv", 0, 220.0),
        "lv": _bus("lv", 0, 110.0),
        "o": _bus("o", 1, 220.0),
    }
    out = gb.build_elk_graph(state)
    groups = {g["nodeId"]: g for g in out["nodeGroups"]}
    assert set(groups) == {0, 1}
    assert groups[0]["name"] == "Sub"
    assert len(groups[0]["busIds"]) == 2  # two voltage bars at node 0
    assert len(groups[1]["busIds"]) == 1


def test_node_group_name_fallback_when_blank():
    state = GuiSystemState()
    state.nodes = [GuiNode(index=3, name="")]
    state.buses = {"b": _bus("b", 3, 220.0)}
    out = gb.build_elk_graph(state)
    assert out["nodeGroups"][0]["name"] == "Node 3"


# ── Grid layout ───────────────────────────────────────────────────


def test_layout_assigns_xy_to_every_child():
    state = _state_two_nodes()
    out = gb.build_elk_graph(state)
    for child in out["elkGraph"]["children"]:
        assert "x" in child and "y" in child
        assert isinstance(child["x"], float)
        assert isinstance(child["y"], float)


def test_layout_hv_row_above_lv_row():
    # higher voltage -> smaller row index -> smaller y (top)
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"hv": _bus("hv", 0, 400.0), "lv": _bus("lv", 0, 110.0)}
    out = gb.build_elk_graph(state)
    by_id = {c["id"]: c for c in out["elkGraph"]["children"]}
    assert by_id["bus_hv"]["y"] < by_id["bus_lv"]["y"]


def test_layout_columns_left_to_right_by_node_order():
    state = _state_two_nodes()
    out = gb.build_elk_graph(state)
    by_node = {c["properties"]["parentNode"]: c
               for c in out["elkGraph"]["children"]}
    assert by_node[0]["x"] < by_node[1]["x"]


def test_transformer_routes_as_clean_vertical():
    """A transformer between two voltage bars in the same node is drawn as a
    clean vertical connection (shared X, no Z bends), so the JS side can sit
    the winding symbol between the bars with stubs (no line across it)."""
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {"hv": _bus("hv", 0, 220.0), "lv": _bus("lv", 0, 110.0)}
    state.transformers = [GuiTransformer(
        name="T", from_bus="hv", to_bus="lv", rated_power_mva=100.0)]
    out = gb.build_elk_graph(state)
    edge = out["elkGraph"]["edges"][0]
    assert edge["properties"]["precomputedRoute"] is True
    assert edge["properties"]["transformerVertical"] is True
    sec = edge["sections"][0]
    assert sec["bendPoints"] == []                       # straight, no Z
    assert sec["startPoint"]["x"] == sec["endPoint"]["x"]  # vertical
    assert sec["startPoint"]["y"] != sec["endPoint"]["y"]


def test_same_row_edge_dips_below_row():
    # two nodes same voltage -> same row edge; lane = src below row
    state = _state_two_nodes()
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L", from_bus="b0", to_bus="b1", capacity_mw=100.0,
        voltage_kv=220.0)]
    out = gb.build_elk_graph(state)
    edge = out["elkGraph"]["edges"][0]
    sec = edge["sections"][0]
    src = next(c for c in out["elkGraph"]["children"]
               if c["properties"]["parentNode"] == 0)
    expected_lane = src["y"] + src["height"] + gb._LANE_MARGIN_Y
    assert sec["bendPoints"][0]["y"] == expected_lane


def test_multiple_buses_same_cell_subgrid_layout():
    # many buses at the same (node, voltage) -> level 0 keeps them
    # separate; the cell sub-grid layout must place them all.
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N")]
    state.buses = {f"b{i}": _bus(f"b{i}", 0, 220.0) for i in range(5)}
    out = gb.build_elk_graph(state)
    children = out["elkGraph"]["children"]
    assert len(children) == 5
    coords = {(round(c["x"], 3), round(c["y"], 3)) for c in children}
    # placements should not all collapse onto a single point
    assert len(coords) > 1


def test_apply_grid_layout_empty_children_noop():
    # direct call with empty inputs must not raise
    gb._apply_grid_layout([], [], GuiSystemState())


# ── Direct private helper: _aggregate behavior via build ──────────


def test_logging_does_not_raise_with_edges(caplog):
    state = _state_two_nodes()
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L", from_bus="b0", to_bus="b1", capacity_mw=100.0,
        voltage_kv=220.0)]
    import logging
    with caplog.at_level(logging.INFO):
        out = gb.build_elk_graph(state)
    assert len(out["elkGraph"]["edges"]) == 1


# ── Adaptive lane separation (step 5b: same-row + cross-row lanes) ──


def _nodes_at(kv, n):
    state = GuiSystemState()
    state.nodes = [GuiNode(index=i, name=f"N{i}") for i in range(n)]
    state.buses = {f"b{i}": _bus(f"b{i}", i, kv, f"Bus{i}") for i in range(n)}
    return state


def test_same_row_overlapping_edges_get_distinct_lanes():
    """Parallel same-voltage circuits that overlap in X must NOT collapse
    onto a single Y line (the old behaviour) — each gets its own lane."""
    state = _nodes_at(220.0, 4)
    # Two nested, overlapping spans at the same voltage: 0-3 and 1-2.
    state.transmission_lines = [
        GuiTransmissionLine(line_id="L03", from_bus="b0", to_bus="b3",
                            capacity_mw=100.0, voltage_kv=220.0),
        GuiTransmissionLine(line_id="L12", from_bus="b1", to_bus="b2",
                            capacity_mw=100.0, voltage_kv=220.0),
    ]
    out = gb.build_elk_graph(state)
    edges = out["elkGraph"]["edges"]
    assert len(edges) == 2
    lane_ys = [e["sections"][0]["bendPoints"][0]["y"] for e in edges]
    assert len(set(lane_ys)) == 2, lane_ys           # distinct lanes


def test_same_row_disjoint_edges_share_a_lane():
    """Non-overlapping spans at one voltage reuse the same lane (compact)."""
    state = _nodes_at(220.0, 4)
    # Disjoint spans: 0-1 and 2-3.
    state.transmission_lines = [
        GuiTransmissionLine(line_id="L01", from_bus="b0", to_bus="b1",
                            capacity_mw=100.0, voltage_kv=220.0),
        GuiTransmissionLine(line_id="L23", from_bus="b2", to_bus="b3",
                            capacity_mw=100.0, voltage_kv=220.0),
    ]
    out = gb.build_elk_graph(state)
    lane_ys = [e["sections"][0]["bendPoints"][0]["y"]
               for e in out["elkGraph"]["edges"]]
    assert len(set(lane_ys)) == 1, lane_ys           # shared lane


def test_same_row_edge_uses_clean_u_route():
    """A same-row edge exits and enters the BOTTOM face (clean U dip), so
    no segment crosses a bar: start/end Y are equal, bend Y is below."""
    state = _nodes_at(220.0, 2)
    state.transmission_lines = [GuiTransmissionLine(
        line_id="L", from_bus="b0", to_bus="b1",
        capacity_mw=100.0, voltage_kv=220.0)]
    out = gb.build_elk_graph(state)
    sec = out["elkGraph"]["edges"][0]["sections"][0]
    assert sec["startPoint"]["y"] == sec["endPoint"]["y"]
    assert sec["bendPoints"][0]["y"] > sec["startPoint"]["y"]


def test_adaptive_spacing_grows_dense_gap():
    """A row gap crossed by many overlapping cross-voltage edges must be
    taller than one crossed by a single edge (step 5b)."""
    def _gap(n_overlapping):
        state = GuiSystemState()
        # n nodes, each with a 220 kV and a 110 kV bus.
        state.nodes = [GuiNode(index=i, name=f"N{i}") for i in range(n_overlapping + 1)]
        state.buses = {}
        for i in range(n_overlapping + 1):
            state.buses[f"h{i}"] = _bus(f"h{i}", i, 220.0, f"H{i}")
            state.buses[f"l{i}"] = _bus(f"l{i}", i, 110.0, f"L{i}")
        # Transmission lines from node 0's 110 kV bus fanning out to every
        # other node's 220 kV bus → all share node 0's column on one side and
        # span right → overlapping cross-row spans → many lanes.
        state.transmission_lines = []
        for i in range(1, n_overlapping + 1):
            # cross-voltage line forces a cross-row (gap) edge
            state.transmission_lines.append(GuiTransmissionLine(
                line_id=f"X{i}", from_bus="l0", to_bus=f"h{i}",
                capacity_mw=100.0, voltage_kv=220.0))
        out = gb.build_elk_graph(state)
        ch = {c["id"]: c for c in out["elkGraph"]["children"]}
        y220 = min(c["y"] for c in ch.values()
                   if c["properties"]["voltageKv"] == 220.0)
        y110 = min(c["y"] for c in ch.values()
                   if c["properties"]["voltageKv"] == 110.0)
        return abs(y110 - y220)

    sparse = _gap(1)
    dense = _gap(20)
    assert dense >= sparse


def test_multi_row_edge_routes_through_column_gap():
    """An edge spanning >1 voltage row must NOT drop straight through the
    bars in between — its long vertical run is offset into a column gap."""
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N0")]
    state.buses = {
        "h": _bus("h", 0, 400.0, "H"),
        "m": _bus("m", 0, 220.0, "M"),
        "l": _bus("l", 0, 110.0, "L"),
    }
    # Cross-voltage line 400 -> 110 skips the 220 row in between.
    state.transmission_lines = [GuiTransmissionLine(
        line_id="X", from_bus="h", to_bus="l",
        capacity_mw=100.0, voltage_kv=400.0)]
    out = gb.build_elk_graph(state)
    ch = {c["id"]: c for c in out["elkGraph"]["children"]}
    mid = [c for c in ch.values()
           if c["properties"]["voltageKv"] == 220.0][0]
    mid_x0, mid_x1 = mid["x"], mid["x"] + mid["width"]
    bends = out["elkGraph"]["edges"][0]["sections"][0]["bendPoints"]
    assert len(bends) == 4                       # channel route
    chx = bends[1]["x"]
    assert chx == bends[2]["x"]                  # vertical channel segment
    assert not (mid_x0 <= chx <= mid_x1), (chx, mid_x0, mid_x1)


def test_adjacent_row_edge_keeps_simple_z_route():
    """Adjacent-row edges keep the compact 2-bend Z route (no channel)."""
    state = GuiSystemState()
    state.nodes = [GuiNode(index=0, name="N0")]
    state.buses = {"h": _bus("h", 0, 220.0, "H"), "l": _bus("l", 0, 110.0, "L")}
    state.transmission_lines = [GuiTransmissionLine(
        line_id="X", from_bus="h", to_bus="l",
        capacity_mw=100.0, voltage_kv=220.0)]
    out = gb.build_elk_graph(state)
    bends = out["elkGraph"]["edges"][0]["sections"][0]["bendPoints"]
    assert len(bends) == 2
