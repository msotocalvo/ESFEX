# -*- coding: utf-8 -*-
"""End-to-end validation for the faithful Grid Builder (issue #16).

These tests prove that a network produced by the new faithful import scheme
(real OSM topology, no fabricated connectivity) is not just drawable but
actually *solvable* and yields correct dispatch:

  features → build_grid_from_features(faithful=True) → infer params
          → serialize to YAML → load_config → SystemConfig
          → PowerSystemAdapter → solve → assert dispatch is correct

The structural test runs everywhere (CI included). The solve test needs Julia
and is marked accordingly (skipped when juliacall is unavailable).
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import deque

import numpy as np
import pytest

# Sibling test modules provide the GUI build harness and a base ESFEXConfig.
sys.path.insert(0, "tests")
from test_grid_mapping_builder_extra import MockGuiModel, _make_state, _feat
from test_serializer import _make_esfex_config

import esfex.visualization.workflows.grid_mapping_builder as gmb
from esfex.visualization.workflows.grid_mapping_inference import (
    infer_electrical_params,
)

SYS_NAME = "e2e"
PEAK_MW = 200.0      # flat demand per node
GEN_MW = 500.0       # capacity of each gas unit (comfortably covers demand)


def _build_faithful_system():
    """Build a faithful 2-substation network: two stations ~106 km apart,
    joined by a real 220 kV line, with a dispatchable gas unit at each.

    Returns the built GuiSystemState (after parameter inference, mirroring
    exactly what the GUI does right after the build)."""
    feats = [
        _feat("substation", name="A", lat=21.0, lng=-82.0, voltage_kv=220.0),
        _feat("substation", name="B", lat=21.0, lng=-81.0, voltage_kv=220.0),
        # endpoints sit ~90 m off each substation centroid → clustering
        # attaches them, so the line genuinely connects A to B (no fabrication)
        _feat("line", name="A-B 220kV", lat=21.0, lng=-81.5, voltage_kv=220.0,
              line_coords=[(21.0008, -82.0), (21.0008, -81.0)]),
        _feat("generator", name="Gas A", lat=21.0, lng=-82.0,
              voltage_kv=220.0, fuel="Natural Gas", capacity_mw=GEN_MW),
        _feat("generator", name="Gas B", lat=21.0, lng=-81.0,
              voltage_kv=220.0, fuel="Natural Gas", capacity_mw=GEN_MW),
    ]
    model = MockGuiModel(_make_state(buses={}))
    model.state.name = SYS_NAME
    gmb.build_grid_from_features(model, feats, faithful=True)
    # The GUI runs parameter inference right after the build; do the same so
    # lines/transformers get electrical parameters instead of staying at 0.
    infer_electrical_params(model.state)
    return model.state


def _components(state):
    """Connected components of the bus graph (lines as edges)."""
    adj: dict[str, set[str]] = {b: set() for b in state.buses}
    for ln in state.transmission_lines:
        if ln.from_bus in adj and ln.to_bus in adj:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    seen: set[str] = set()
    comps: list[set[str]] = []
    for seed in adj:
        if seed in seen:
            continue
        comp: set[str] = set()
        q = deque([seed])
        while q:
            n = q.popleft()
            if n in seen:
                continue
            seen.add(n)
            comp.add(n)
            q.extend(adj[n] - seen)
        comps.append(comp)
    return comps


def _to_system_config(state):
    """Serialize the GUI state through the real save path and load it back as
    a validated SystemConfig — the same round-trip the GUI/runner rely on."""
    from esfex.visualization.data.serializer import gui_state_to_yaml
    from esfex.config.loader import load_config

    base = _make_esfex_config(sys_name=SYS_NAME)
    tmp = tempfile.mkdtemp()
    ypath = os.path.join(tmp, f"{SYS_NAME}.yaml")
    gui_state_to_yaml({SYS_NAME: state}, base, ypath)
    cfg = load_config(ypath)
    return cfg.systems[SYS_NAME]


# ── Structural (no Julia) ────────────────────────────────────────────


def test_faithful_network_is_structurally_solvable():
    """The faithful build yields a single connected component that serializes
    into a schema-valid SystemConfig ready to solve — no fabrication, but no
    fragmentation either for a genuinely connected grid."""
    state = _build_faithful_system()

    # Real topology: two station buses joined by the one real line.
    assert len(state.buses) == 2
    assert len(state.transmission_lines) == 1
    assert len(state.generators) == 2

    # The line connects the two stations into ONE electrical island.
    comps = _components(state)
    assert len(comps) == 1 and len(comps[0]) == 2

    # Inference gave the line a real thermal capacity (not left at 0).
    line = state.transmission_lines[0]
    assert line.capacity_mw > 0

    # Round-trips into a validated SystemConfig with the network intact.
    sysc = _to_system_config(state)
    assert sysc.generators, "generators must survive serialization"
    assert len(getattr(sysc, "transmission_lines_geo", []) or []) == 1
    # Enough dispatchable capacity to cover peak demand at every node.
    total_cap = sum(max(g.rated_power) for g in sysc.generators.values())
    assert total_cap >= PEAK_MW * sysc.nodes.num_nodes


# ── Real solve (needs Julia) ─────────────────────────────────────────


@pytest.mark.julia
def test_faithful_network_solves_and_serves_demand():
    """The faithful-built network solves to optimality and the dispatch is
    correct: demand is fully served (no load shedding) and generation balances
    demand over the horizon."""
    from esfex.bridge.adapters import PowerSystemAdapter

    state = _build_faithful_system()
    sysc = _to_system_config(state)

    num_nodes = sysc.nodes.num_nodes
    hours = 24
    demand = np.full((hours, num_nodes), PEAK_MW)

    ps = PowerSystemAdapter(
        config=sysc, demand=demand, hours=hours, num_nodes=num_nodes,
        year=2025, base_year=2025, mode="development",
    )
    ps.build_model()
    status = ps.solve()
    sol = ps.get_solution_values()

    # 1 == optimal (PuLP-compatible status from the adapter).
    assert status == 1, f"solve did not reach optimality (status={status})"

    # Correct results: all demand served, energy balances, cost is positive.
    assert sol["load_shed_total"] == pytest.approx(0.0, abs=1e-6), (
        "faithful network could not serve its demand"
    )
    gen_total = float(np.asarray(sol["gen_output"]).sum())
    demand_total = float(demand.sum())
    assert gen_total >= demand_total - 1e-6, "generation under-supplies demand"
    # No losses are modelled intra-node, so generation matches demand exactly;
    # allow a small tolerance for any curtailment/loss terms.
    assert gen_total == pytest.approx(demand_total, rel=0.02)
    assert sol["objective"] > 0
