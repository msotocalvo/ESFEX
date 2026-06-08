"""Additive coverage for ``grid_mapping_inference``.

The target module is fully duck-typed against ``GuiSystemState`` — it only
reads attributes on the passed object and on small record objects.  We
therefore exercise it with lightweight namespace fakes rather than
constructing the heavyweight GUI model.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from esfex.visualization.workflows import grid_mapping_inference as gmi


# ── Fake record / state builders ────────────────────────────────────────


def _bus(role="load", voltage_kv=110.0, parent_node=0, demand_fraction=0.0):
    return SimpleNamespace(
        role=role,
        voltage_kv=voltage_kv,
        parent_node=parent_node,
        demand_fraction=demand_fraction,
    )


def _gen(bus, rated_power):
    return SimpleNamespace(bus=bus, rated_power=rated_power)


def _bat(bus, rated_power):
    return SimpleNamespace(bus=bus, rated_power=rated_power)


def _node(index, peak_mw):
    return SimpleNamespace(index=index, demand=SimpleNamespace(peak_mw=peak_mw))


def _line(from_bus, to_bus, voltage_kv=0.0, capacity_mw=0.0):
    return SimpleNamespace(
        from_bus=from_bus,
        to_bus=to_bus,
        voltage_kv=voltage_kv,
        capacity_mw=capacity_mw,
    )


def _trafo(
    from_bus,
    to_bus,
    from_voltage_kv=110.0,
    to_voltage_kv=33.0,
    rated_power_mva=0.0,
    impedance_pu=0.0,
    losses_fraction=0.0,
):
    return SimpleNamespace(
        from_bus=from_bus,
        to_bus=to_bus,
        from_voltage_kv=from_voltage_kv,
        to_voltage_kv=to_voltage_kv,
        rated_power_mva=rated_power_mva,
        impedance_pu=impedance_pu,
        losses_fraction=losses_fraction,
    )


def _conv(from_bus, to_bus):
    return SimpleNamespace(from_bus=from_bus, to_bus=to_bus)


def _state(
    buses=None,
    generators=None,
    batteries=None,
    nodes=None,
    transmission_lines=None,
    transformers=None,
    acdc_converters=None,
    electrolyzers=None,
    freq_converters="UNSET",
):
    ns = SimpleNamespace()
    ns.buses = buses or {}
    ns.generators = generators or {}
    ns.batteries = batteries or {}
    ns.nodes = nodes or []
    ns.transmission_lines = transmission_lines or []
    ns.transformers = transformers or []
    ns.acdc_converters = acdc_converters or []
    if electrolyzers is not None:
        ns.electrolyzers = electrolyzers
    if freq_converters != "UNSET":
        ns.freq_converters = freq_converters
    return ns


# ── InferenceReport ─────────────────────────────────────────────────────


def test_report_defaults_and_summary():
    rep = gmi.InferenceReport()
    assert rep.lines_capacity_set == 0
    assert rep.notes == []
    s = rep.summary()
    assert "Lines:" in s and "Transformers:" in s
    rep.lines_capacity_set = 2
    rep.transformers_impedance_set = 3
    assert "2 new caps" in rep.summary()
    assert "3 impedances re-derived" in rep.summary()


def test_report_notes_are_independent_lists():
    a = gmi.InferenceReport()
    b = gmi.InferenceReport()
    a.notes.append("x")
    assert b.notes == []


# ── _sil_cap ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "v,expected",
    [
        (None, math.inf),
        (0.0, math.inf),
        (-5.0, math.inf),
        (500.0, 2400.0),
        (600.0, 2400.0),
        (345.0, 1200.0),
        (220.0, 600.0),
        (110.0, 250.0),
        (66.0, 120.0),
        (33.0, 60.0),
        (10.0, 20.0),
        (1.0, 10.0),  # below 10kV bucket -> 0.0 floor row -> 10.0
    ],
)
def test_sil_cap(v, expected):
    assert gmi._sil_cap(v) == expected


# ── _bus_injection ──────────────────────────────────────────────────────


def test_bus_injection_gen_battery_and_demand_split():
    state = _state(
        buses={
            "b1": _bus(role="load", parent_node=0, demand_fraction=0.4),
            "b2": _bus(role="mixed", parent_node=0, demand_fraction=0.6),
            "b3": _bus(role="gen", parent_node=0, demand_fraction=1.0),
        },
        generators={"g1": _gen("b1", 30.0)},
        batteries={"bat1": _bat("b2", 10.0)},
        nodes=[_node(0, 100.0)],
    )
    inj = gmi._bus_injection(state)
    # b1: +30 gen - 100*0.4 = -10
    assert inj["b1"] == pytest.approx(-10.0)
    # b2: +10 battery - 100*0.6 = -50
    assert inj["b2"] == pytest.approx(-50.0)
    # b3 has role 'gen' so demand not subtracted; no gen -> absent
    assert "b3" not in inj


def test_bus_injection_electrolyzer_subtracts():
    e_with_bus = SimpleNamespace(bus="b1", rated_power=15.0)
    e_no_bus = SimpleNamespace(bus=None, rated_power=99.0)
    e_missing_attrs = SimpleNamespace()  # no bus / rated_power attrs
    state = _state(
        buses={"b1": _bus(role="gen", parent_node=0)},
        nodes=[_node(0, 0.0)],
        electrolyzers={"e1": e_with_bus, "e2": e_no_bus, "e3": e_missing_attrs},
    )
    inj = gmi._bus_injection(state)
    assert inj["b1"] == pytest.approx(-15.0)


def test_bus_injection_no_peak_demand_skips_split():
    state = _state(
        buses={"b1": _bus(role="load", parent_node=0, demand_fraction=0.5)},
        nodes=[_node(0, 0.0)],  # peak 0 -> node_peak empty -> no subtraction
    )
    inj = gmi._bus_injection(state)
    assert dict(inj) == {}


# ── _adjacency ──────────────────────────────────────────────────────────


def test_adjacency_includes_all_edge_types_and_filters():
    state = _state(
        buses={"a": _bus(), "b": _bus(), "c": _bus(), "d": _bus()},
        transmission_lines=[
            _line("a", "b"),
            _line("a", "a"),  # self-loop filtered
            _line("a", "ZZ"),  # unknown bus filtered
        ],
        transformers=[_trafo("b", "c")],
        acdc_converters=[_conv("c", "d")],
        freq_converters=[_conv("d", "a")],
    )
    adj = gmi._adjacency(state)
    assert adj["a"] == {"b", "d"}
    assert adj["b"] == {"a", "c"}
    assert adj["c"] == {"b", "d"}
    assert adj["d"] == {"c", "a"}


def test_adjacency_without_freq_converters_attr():
    state = _state(
        buses={"a": _bus(), "b": _bus()},
        transmission_lines=[_line("a", "b")],
        freq_converters="UNSET",  # attribute absent
    )
    adj = gmi._adjacency(state)
    assert adj == {"a": {"b"}, "b": {"a"}}


# ── _reachable_excluding_edge / _flow_through_edge ──────────────────────


def test_reachable_excluding_edge_cuts_blocked():
    adj = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}
    # Cutting a-b isolates 'a'
    assert gmi._reachable_excluding_edge(adj, "a", ("a", "b")) == {"a"}
    # From b, cutting a-b still reaches c
    assert gmi._reachable_excluding_edge(adj, "b", ("a", "b")) == {"b", "c"}


def test_flow_through_edge_tree_uses_smaller_side():
    adj = {"a": {"b"}, "b": {"a"}}
    inj = {"a": 7.0, "b": -7.0}
    assert gmi._flow_through_edge(adj, inj, "a", "b") == pytest.approx(7.0)


def test_flow_through_edge_in_cycle_returns_zero():
    # Triangle: removing a-b still leaves b reachable from a via c.
    adj = {"a": {"b", "c"}, "b": {"a", "c"}, "c": {"a", "b"}}
    inj = {"a": 10.0, "b": -10.0, "c": 0.0}
    assert gmi._flow_through_edge(adj, inj, "a", "b") == 0.0


# ── infer_electrical_params ─────────────────────────────────────────────


def test_infer_empty_state_returns_empty_report():
    rep = gmi.infer_electrical_params(_state())
    assert isinstance(rep, gmi.InferenceReport)
    assert rep.lines_capacity_set == 0


def test_infer_sets_line_capacity_from_flow():
    # Two buses, one generator on a, demand on b -> tree flow = injection.
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=110.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 40.0)},
        nodes=[_node(0, 40.0)],
        transmission_lines=[_line("a", "b", voltage_kv=0.0, capacity_mw=0.0)],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.lines_capacity_set == 1
    ln = state.transmission_lines[0]
    # flow=40, *1.25*1.25=62.5; system_cap=40*1.25=50; sil(110)=250 -> 50
    assert ln.capacity_mw == pytest.approx(50.0)


def test_infer_line_floor_applied_when_no_injection():
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=110.0),
               "b": _bus(role="gen", voltage_kv=110.0)},
        transmission_lines=[_line("a", "b")],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.lines_capacity_set == 1
    # No demand -> system_cap inf, flow 0 -> floor 5.0
    assert state.transmission_lines[0].capacity_mw == pytest.approx(5.0)


def test_infer_line_bumped_when_existing_below_needed():
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=110.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 40.0)},
        nodes=[_node(0, 40.0)],
        transmission_lines=[_line("a", "b", capacity_mw=1.0)],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.lines_capacity_bumped == 1
    assert rep.lines_capacity_set == 0
    assert state.transmission_lines[0].capacity_mw == pytest.approx(50.0)


def test_infer_line_not_reduced_when_already_high():
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=110.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 40.0)},
        nodes=[_node(0, 40.0)],
        transmission_lines=[_line("a", "b", capacity_mw=9999.0)],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.lines_capacity_set == 0
    assert rep.lines_capacity_bumped == 0
    assert state.transmission_lines[0].capacity_mw == pytest.approx(9999.0)


def test_infer_line_uses_line_voltage_over_bus():
    # Line voltage 33 -> sil cap 60, lower than bus 110 sil 250.
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=110.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 1000.0)},
        nodes=[_node(0, 1000.0)],
        transmission_lines=[_line("a", "b", voltage_kv=33.0)],
    )
    gmi.infer_electrical_params(state)
    # sil(33)=60 < system_cap(1250) and < flow*… -> capped at 60
    assert state.transmission_lines[0].capacity_mw == pytest.approx(60.0)


def test_infer_skips_line_with_unknown_or_self_bus():
    state = _state(
        buses={"a": _bus(role="gen"), "b": _bus(role="gen")},
        transmission_lines=[
            _line("a", "ZZ"),  # to_bus unknown
            _line("a", "a"),   # self loop
        ],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.lines_capacity_set == 0


def test_infer_transformer_full_path_sets_mva_and_impedance():
    pytest.importorskip("esfex.visualization.workflows.grid_mapping_quality")
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=33.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 30.0)},
        nodes=[_node(0, 30.0)],
        transformers=[
            _trafo("a", "b", from_voltage_kv=110.0, to_voltage_kv=33.0,
                   rated_power_mva=0.0, impedance_pu=0.0, losses_fraction=0.0)
        ],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_mva_set == 1
    tr = state.transformers[0]
    assert tr.rated_power_mva > 0
    # impedance re-derived from 0.0 baseline -> changed
    assert rep.transformers_impedance_set == 1
    assert tr.impedance_pu > 0
    assert tr.losses_fraction > 0


def test_infer_transformer_mva_bumped():
    state = _state(
        buses={
            "a": _bus(role="gen", voltage_kv=110.0, parent_node=0),
            "b": _bus(role="load", voltage_kv=33.0, parent_node=0,
                      demand_fraction=1.0),
        },
        generators={"g": _gen("a", 40.0)},
        nodes=[_node(0, 40.0)],
        transformers=[
            _trafo("a", "b", rated_power_mva=1.0)
        ],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_mva_bumped == 1
    assert rep.transformers_mva_set == 0


def test_infer_transformer_floor_min_mva():
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=110.0),
               "b": _bus(role="gen", voltage_kv=33.0)},
        transformers=[_trafo("a", "b")],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_mva_set == 1
    assert state.transformers[0].rated_power_mva == pytest.approx(5.0)


def test_infer_transformer_to_voltage_zero_uses_default_ratio():
    # to_voltage_kv == 0 -> ratio defaults to 2.0 branch
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=110.0),
               "b": _bus(role="gen", voltage_kv=0.0)},
        transformers=[
            _trafo("a", "b", from_voltage_kv=110.0, to_voltage_kv=0.0,
                   impedance_pu=0.0)
        ],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_mva_set == 1
    assert state.transformers[0].impedance_pu > 0


def test_infer_transformer_impedance_not_changed_when_close():
    # Pre-set impedance equal to estimate -> abs(new - cur) <= 0.01, no set.
    from esfex.visualization.workflows.grid_mapping_quality import (
        estimate_transformer_impedance_pu,
    )
    # MVA will floor to 5.0; ratio 110/33.
    z = estimate_transformer_impedance_pu(5.0, 110.0 / 33.0)
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=110.0),
               "b": _bus(role="gen", voltage_kv=33.0)},
        transformers=[
            _trafo("a", "b", from_voltage_kv=110.0, to_voltage_kv=33.0,
                   rated_power_mva=5.0, impedance_pu=z)
        ],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_impedance_set == 0
    assert state.transformers[0].impedance_pu == pytest.approx(z)


def test_infer_transformer_skips_unknown_and_self_bus():
    state = _state(
        buses={"a": _bus(role="gen"), "b": _bus(role="gen")},
        transformers=[_trafo("a", "ZZ"), _trafo("a", "a")],
    )
    rep = gmi.infer_electrical_params(state)
    assert rep.transformers_mva_set == 0


def test_infer_transformer_impedance_skipped_when_quality_unavailable(
    monkeypatch,
):
    # Force the lazy import to fail so the estimate_* funcs become None and
    # the impedance branch is skipped entirely.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "esfex.visualization.workflows.grid_mapping_quality":
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=110.0),
               "b": _bus(role="gen", voltage_kv=33.0)},
        transformers=[_trafo("a", "b", rated_power_mva=0.0)],
    )
    rep = gmi.infer_electrical_params(state)
    # MVA still set, but no impedance re-derivation.
    assert rep.transformers_mva_set == 1
    assert rep.transformers_impedance_set == 0
    assert state.transformers[0].impedance_pu == 0.0


def test_infer_system_cap_infinite_without_demand():
    # No nodes/demand -> system_cap inf branch; flow 0 -> floor applies.
    state = _state(
        buses={"a": _bus(role="gen", voltage_kv=500.0),
               "b": _bus(role="gen", voltage_kv=500.0)},
        transmission_lines=[_line("a", "b", voltage_kv=500.0)],
    )
    gmi.infer_electrical_params(state)
    assert state.transmission_lines[0].capacity_mw == pytest.approx(5.0)


# ── _bridge_flow_index (linear-time replacement for per-edge BFS) ────────


def test_bridge_flow_index_matches_bfs_on_random_graphs():
    """The O(V+E) bridge index must return exactly what the per-edge BFS
    (``_flow_through_edge``) returns, for every edge and both directions."""
    import random
    rng = random.Random(1234)
    for _ in range(150):
        n = rng.randint(2, 16)
        nodes = [f"n{i}" for i in range(n)]
        adj = {x: set() for x in nodes}
        edges = []
        for _e in range(rng.randint(1, n * 2)):
            u, v = rng.sample(nodes, 2)
            if v not in adj[u]:
                adj[u].add(v)
                adj[v].add(u)
                edges.append((u, v))
        inj = {x: rng.uniform(-10, 10) for x in nodes}
        flow = gmi._bridge_flow_index(adj, inj)
        for (u, v) in edges:
            for a, b in ((u, v), (v, u)):
                assert flow(a, b) == pytest.approx(
                    gmi._flow_through_edge(adj, inj, a, b))


def test_bridge_flow_index_handles_deep_chain_without_recursion():
    """A long linear chain must not hit Python's recursion limit (the index
    uses an iterative DFS) and must treat every chain edge as a bridge."""
    n = 5000
    nodes = [f"n{i}" for i in range(n)]
    adj = {x: set() for x in nodes}
    for i in range(n - 1):
        adj[nodes[i]].add(nodes[i + 1])
        adj[nodes[i + 1]].add(nodes[i])
    inj = {x: 1.0 for x in nodes}
    inj[nodes[0]] = -float(n - 1)  # balance so the far end carries known flow
    flow = gmi._bridge_flow_index(adj, inj)
    # Edge between n0 and n1: n0 side injection = inj[n0] = -(n-1) → |n-1|.
    assert flow("n0", "n1") == pytest.approx(n - 1)


def test_infer_electrical_params_scales_on_large_mesh():
    """Country-scale inference must stay fast — the per-edge BFS made this
    O(E²) and hung the build ('Building network…') for minutes."""
    import time
    import random
    rng = random.Random(7)
    buses = {f"b{i}": _bus() for i in range(3000)}
    # a connected backbone (chain) + random chords (mesh)
    lines = [_line(f"b{i}", f"b{i+1}", voltage_kv=220.0) for i in range(2999)]
    for _ in range(3000):
        u, v = rng.randint(0, 2999), rng.randint(0, 2999)
        if u != v:
            lines.append(_line(f"b{u}", f"b{v}", voltage_kv=220.0))
    state = _state(buses=buses, transmission_lines=lines,
                   nodes=[_node(0, 5000.0)])
    t0 = time.time()
    rep = gmi.infer_electrical_params(state)
    elapsed = time.time() - t0
    assert rep.lines_capacity_set > 0
    assert elapsed < 5.0, f"inference too slow ({elapsed:.1f}s) — O(E²)?"
