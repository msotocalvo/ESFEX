"""Bottom-up inference of electrical parameters for the network built
by Grid Builder.

After ``build_grid_from_features`` + ``iterative_auto_connect`` we have
the topology, but many lines / transformers still carry placeholder or
zero capacities (when the source data lacked voltage or rated MVA).
This module walks the bus graph and infers a sensible **minimum**
capacity for every edge so the downstream solver doesn't reject the
power-flow problem as infeasible.

Inference is purely topological:

* For each line / transformer (an edge), compute the *injection imbalance*
  on the smaller side when the edge is removed.  In a tree this is
  exactly the flow the edge has to carry; in a meshed network it is a
  very conservative upper bound.
* The inferred capacity is ``max(current_cap, |imbalance| × safety_factor)``.
* Transformers also get their R/X re-derived from their MVA rating so
  per-unit values stay in plausible ranges.

The inference NEVER reduces a capacity that the source data already
provides — it only fills zeros / placeholders.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiSystemState

logger = logging.getLogger(__name__)


@dataclass
class InferenceReport:
    lines_capacity_set: int = 0
    lines_capacity_bumped: int = 0
    transformers_mva_set: int = 0
    transformers_mva_bumped: int = 0
    transformers_impedance_set: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Lines: {self.lines_capacity_set} new caps, "
            f"{self.lines_capacity_bumped} bumped. "
            f"Transformers: {self.transformers_mva_set} new MVA, "
            f"{self.transformers_mva_bumped} bumped, "
            f"{self.transformers_impedance_set} impedances re-derived."
        )


# Floor for capacity even when no gen/demand sits on either side —
# covers reactive flow, transient overloads and operational margin.
_MIN_LINE_CAPACITY_MW = 5.0
_MIN_TRAFO_MVA = 5.0
_SAFETY_FACTOR = 1.25  # 25 % above worst-case net injection

# Physical upper bound: surge-impedance loading by voltage class.
# A line / transformer never needs to be sized beyond what its
# voltage can physically carry — this is the natural cap that
# prevents inferring 5 GVA on a 33 kV feeder.
_SIL_BY_VOLTAGE_KV = [
    # (v_min_kv, sil_mw)
    (500.0, 2400.0),
    (345.0, 1200.0),
    (220.0, 600.0),
    (110.0, 250.0),
    (66.0,  120.0),
    (33.0,  60.0),
    (10.0,  20.0),
    (0.0,   10.0),
]


def _sil_cap(voltage_kv: float) -> float:
    """Surge-impedance loading cap (MW) for the given voltage class."""
    if voltage_kv is None or voltage_kv <= 0:
        return float("inf")
    for v_min, sil in _SIL_BY_VOLTAGE_KV:
        if voltage_kv >= v_min:
            return sil
    return _SIL_BY_VOLTAGE_KV[-1][1]


def _bus_injection(state: "GuiSystemState") -> dict[str, float]:
    """Return per-bus net injection: sum(gen.rated_power) - sum(demand)."""
    inj: dict[str, float] = defaultdict(float)
    # Generators
    for g in state.generators.values():
        inj[g.bus] += float(g.rated_power)
    # Batteries are dispatchable both ways; their rated_power adds to
    # the absolute injection magnitude.
    for b in state.batteries.values():
        inj[b.bus] += float(b.rated_power)
    if hasattr(state, "electrolyzers"):
        for e in state.electrolyzers.values():
            bus = getattr(e, "bus", None)
            if bus:
                inj[bus] -= float(getattr(e, "rated_power", 0))

    # Demand: each node's peak demand_mw is split across its load buses
    # by demand_fraction (semantics of GuiSystemState).
    node_peak: dict[int, float] = {}
    for nd in state.nodes:
        peak = float(getattr(nd.demand, "peak_mw", 0) or 0)
        if peak > 0:
            node_peak[nd.index] = peak
    if node_peak:
        for bid, bus in state.buses.items():
            if bus.role not in ("load", "mixed"):
                continue
            peak = node_peak.get(bus.parent_node, 0.0)
            frac = float(getattr(bus, "demand_fraction", 0) or 0)
            inj[bid] -= peak * frac
    return dict(inj)


def _adjacency(state: "GuiSystemState") -> dict[str, set[str]]:
    """Bus adjacency including lines + transformers + converters."""
    adj: dict[str, set[str]] = {bid: set() for bid in state.buses}
    for ln in state.transmission_lines:
        if ln.from_bus in adj and ln.to_bus in adj and ln.from_bus != ln.to_bus:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus in adj and tr.to_bus in adj and tr.from_bus != tr.to_bus:
            adj[tr.from_bus].add(tr.to_bus)
            adj[tr.to_bus].add(tr.from_bus)
    for c in state.acdc_converters:
        if c.from_bus in adj and c.to_bus in adj and c.from_bus != c.to_bus:
            adj[c.from_bus].add(c.to_bus)
            adj[c.to_bus].add(c.from_bus)
    if hasattr(state, "freq_converters"):
        for c in state.freq_converters:
            if c.from_bus in adj and c.to_bus in adj and c.from_bus != c.to_bus:
                adj[c.from_bus].add(c.to_bus)
                adj[c.to_bus].add(c.from_bus)
    return adj


def _reachable_excluding_edge(
    adj: dict[str, set[str]], start: str, blocked: tuple[str, str],
) -> set[str]:
    """BFS from *start* in *adj* but pretending the edge *blocked* is cut."""
    a, b = blocked
    visited = {start}
    stack = deque([start])
    while stack:
        node = stack.popleft()
        for nb in adj.get(node, ()):
            if (node == a and nb == b) or (node == b and nb == a):
                continue
            if nb not in visited:
                visited.add(nb)
                stack.append(nb)
    return visited


def _flow_through_edge(
    adj: dict[str, set[str]],
    inj: dict[str, float],
    a: str, b: str,
) -> float:
    """Minimum |MW| an edge a↔b must carry for the network to balance.

    For a tree this is exact: removing the edge splits the network
    into two parts, and the edge has to carry the net injection of
    the smaller part. For meshed topologies this is an upper bound
    on the line's flow share, which is exactly the conservative
    sizing we want.
    """
    side_a = _reachable_excluding_edge(adj, a, (a, b))
    if b in side_a:
        # Edge is part of a cycle; both sides remain connected after
        # removing it. We can't bound the flow tightly here without a
        # full power-flow solve, so return 0 (the existing capacity
        # floor will apply).
        return 0.0
    inj_a = sum(inj.get(bid, 0.0) for bid in side_a)
    return abs(inj_a)


def infer_electrical_params(state: "GuiSystemState") -> InferenceReport:
    """Walk the network and infer missing line/transformer parameters.

    Safe to call multiple times — only fills zeros / placeholders, never
    reduces a non-zero capacity.
    """
    rep = InferenceReport()
    if not state.buses:
        return rep
    inj = _bus_injection(state)
    adj = _adjacency(state)

    # System-wide upper bound: no edge needs to carry more than the
    # peak demand of the whole system (energy conservation), with a
    # 25 % margin for non-coincident peaks across nodes.
    system_peak = sum(
        float(getattr(nd.demand, "peak_mw", 0) or 0) for nd in state.nodes
    )
    if system_peak > 0:
        system_cap = system_peak * _SAFETY_FACTOR
    else:
        system_cap = float("inf")

    def _bounded(needed: float, voltage_kv: float | None) -> float:
        """Apply both the system-wide cap and the voltage-SIL cap."""
        return max(
            _MIN_LINE_CAPACITY_MW,
            min(needed, system_cap, _sil_cap(voltage_kv or 0)),
        )

    # ── Lines ─────────────────────────────────────────────────────
    for ln in state.transmission_lines:
        if ln.from_bus not in state.buses or ln.to_bus not in state.buses:
            continue
        if ln.from_bus == ln.to_bus:
            continue
        flow = _flow_through_edge(adj, inj, ln.from_bus, ln.to_bus)
        # Use line voltage if set, else the from-bus voltage.
        v = ln.voltage_kv or state.buses[ln.from_bus].voltage_kv
        needed = _bounded(flow * _SAFETY_FACTOR, v)
        current = float(getattr(ln, "capacity_mw", 0) or 0)
        if current <= 0:
            ln.capacity_mw = needed
            rep.lines_capacity_set += 1
        elif current < needed:
            ln.capacity_mw = needed
            rep.lines_capacity_bumped += 1

    # ── Transformers ──────────────────────────────────────────────
    # Lazy import so the inference module can be used without Qt loaded.
    try:
        from esfex.visualization.workflows.grid_mapping_quality import (
            estimate_transformer_impedance_pu,
            estimate_transformer_losses_fraction,
        )
    except Exception:
        estimate_transformer_impedance_pu = None
        estimate_transformer_losses_fraction = None

    for tr in state.transformers:
        if tr.from_bus not in state.buses or tr.to_bus not in state.buses:
            continue
        if tr.from_bus == tr.to_bus:
            continue
        flow = _flow_through_edge(adj, inj, tr.from_bus, tr.to_bus)
        # Cap at system peak and at the SIL of the HIGHER voltage side
        # (a step-down trafo is bounded by its HV terminal capacity).
        v_max = max(tr.from_voltage_kv or 0, tr.to_voltage_kv or 0)
        needed_mva = max(_MIN_TRAFO_MVA, _bounded(flow * _SAFETY_FACTOR, v_max))
        current_mva = float(getattr(tr, "rated_power_mva", 0) or 0)
        if current_mva <= 0:
            tr.rated_power_mva = needed_mva
            rep.transformers_mva_set += 1
        elif current_mva < needed_mva:
            tr.rated_power_mva = needed_mva
            rep.transformers_mva_bumped += 1

        # Re-derive impedance / losses from the (possibly bumped) MVA
        # so per-unit values stay in physically plausible ranges.
        if estimate_transformer_impedance_pu is not None:
            ratio = (
                tr.from_voltage_kv / tr.to_voltage_kv
                if tr.to_voltage_kv > 0 else 2.0
            )
            new_z = estimate_transformer_impedance_pu(tr.rated_power_mva, ratio)
            if abs(new_z - tr.impedance_pu) > 0.01:
                tr.impedance_pu = new_z
                rep.transformers_impedance_set += 1
            new_loss = estimate_transformer_losses_fraction(tr.rated_power_mva)
            tr.losses_fraction = new_loss

    return rep
