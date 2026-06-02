"""Cross-validation between the GUI bus-level topology and the
Julia solver's effective view.

Without this audit, divergences between what the user *sees* on the
map and what the optimizer *uses* electrically are silent and
extremely difficult to debug.  Typical sources:

* Lines where ``from_bus`` / ``to_bus`` are unresolved (None) — the
  solver simply drops them, so a generator that "looks" connected on
  the map can be electrically isolated.
* Buses that no edge touches (no lines, no transformers, no
  converters) but still hold generators / batteries / load — the
  solver sees them as a one-bus island, generation contributes to
  system totals but cannot reach demand elsewhere.
* Generators / batteries whose ``bus`` field references a deleted or
  out-of-range bus — silently dropped or remapped to bus 0 by some
  legacy code paths.

The :class:`TopologyAuditReport` produced here is consumed by both
the validation dialog (visible to the user) and the runtime adapter
(logged to stderr).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from esfex.config.schema import SystemConfig
    from esfex.visualization.data.gui_model import GuiSystemState


@dataclass
class TopologyAuditReport:
    """Diff between GUI topology and the topology the solver will use."""

    # Buses with zero edges in the solver graph (no real lines, no
    # transformers, no converters touching them). They form one-bus
    # islands.
    orphan_buses: list[str] = field(default_factory=list)
    # Equipment whose bus reference is missing / out of range. The
    # solver either drops them or remaps to bus 0.
    orphan_generators: list[str] = field(default_factory=list)
    orphan_batteries: list[str] = field(default_factory=list)
    orphan_electrolyzers: list[str] = field(default_factory=list)
    # Lines that the solver will silently skip (unresolved endpoints).
    lines_dropped_unresolved: list[str] = field(default_factory=list)
    # Lines whose endpoints are out of the bus index range.
    lines_dropped_out_of_range: list[str] = field(default_factory=list)
    # Connected components in the solver graph: {comp_id: {bus_id, ...}}
    components: dict[int, set[str]] = field(default_factory=dict)
    # Components that contain neither generation nor demand → contribute
    # nothing to the dispatch but are still in the model.
    inert_components: list[int] = field(default_factory=list)
    # Components that contain generation but no demand → free energy
    # in the model unless capacity bridges to demand.
    surplus_components: list[int] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (
            self.orphan_buses or self.orphan_generators
            or self.orphan_batteries or self.orphan_electrolyzers
            or self.lines_dropped_unresolved
            or self.lines_dropped_out_of_range
            or self.inert_components or self.surplus_components
        )

    def summary(self) -> str:
        n_comp = len(self.components)
        largest = max((len(c) for c in self.components.values()), default=0)
        lines = [
            f"Components: {n_comp} (largest = {largest} bus(es))",
            f"Orphan buses: {len(self.orphan_buses)}",
            f"Orphan equipment: {len(self.orphan_generators)} gen, "
            f"{len(self.orphan_batteries)} bat, "
            f"{len(self.orphan_electrolyzers)} elz",
            f"Lines silently dropped: {len(self.lines_dropped_unresolved)} "
            f"unresolved, {len(self.lines_dropped_out_of_range)} out of range",
            f"Inert components (no gen, no demand): "
            f"{len(self.inert_components)}",
            f"Surplus components (gen but no demand): "
            f"{len(self.surplus_components)}",
        ]
        return "\n".join(lines)


def audit_gui_state(state: "GuiSystemState") -> TopologyAuditReport:
    """Audit a GUI state by reproducing what the solver will see.

    Operates on the GUI representation directly (string bus IDs,
    EndpointRefs). For the SystemConfig (post-serialisation) variant,
    use :func:`audit_system_config`.
    """
    rep = TopologyAuditReport()
    valid_buses = set(state.buses)

    # ── Orphan equipment (FK to non-existent bus) ───────────────
    for gid, gen in state.generators.items():
        if gen.bus not in valid_buses:
            rep.orphan_generators.append(gid)
    for bid, bat in state.batteries.items():
        if bat.bus not in valid_buses:
            rep.orphan_batteries.append(bid)
    if hasattr(state, "electrolyzers"):
        for eid, elec in state.electrolyzers.items():
            if getattr(elec, "bus", None) not in valid_buses:
                rep.orphan_electrolyzers.append(eid)

    # ── Lines the solver will silently drop ────────────────────
    # The solver requires from_bus / to_bus to be resolved bus IDs.
    # A line is *purely decorative* (wire-line) ONLY when BOTH:
    #   (a) at least one endpoint points to non-bus equipment, AND
    #   (b) capacity_mw is zero.
    # A real bus↔transformer line (like the bus_27 → trafo_1
    # connection on Isla de la Juventud) has capacity > 0 and IS
    # consumed by the solver via from_bus/to_bus — it must be
    # treated as an electrical edge here too.
    def _is_wire_line(ln) -> bool:
        if getattr(ln, "decorative", False):
            return True
        wire_types = {
            "generator", "battery", "electrolyzer",
            "transformer", "acdc_converter", "freq_converter",
        }
        has_eq_endpoint = (
            (ln.from_endpoint and ln.from_endpoint.element_type in wire_types)
            or (ln.to_endpoint and ln.to_endpoint.element_type in wire_types)
        )
        zero_capacity = (getattr(ln, "capacity_mw", 0) or 0) <= 0
        return has_eq_endpoint and zero_capacity

    for ln in state.transmission_lines:
        if _is_wire_line(ln):
            continue
        if ln.from_bus not in valid_buses or ln.to_bus not in valid_buses:
            rep.lines_dropped_unresolved.append(ln.line_id)

    # ── Connected components on the solver graph ───────────────
    adj: dict[str, set[str]] = {bid: set() for bid in valid_buses}
    for ln in state.transmission_lines:
        if _is_wire_line(ln):
            continue
        if ln.from_bus in valid_buses and ln.to_bus in valid_buses:
            if ln.from_bus != ln.to_bus:
                adj[ln.from_bus].add(ln.to_bus)
                adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus in valid_buses and tr.to_bus in valid_buses:
            if tr.from_bus != tr.to_bus:
                adj[tr.from_bus].add(tr.to_bus)
                adj[tr.to_bus].add(tr.from_bus)
    for c in state.acdc_converters:
        if c.from_bus in valid_buses and c.to_bus in valid_buses:
            if c.from_bus != c.to_bus:
                adj[c.from_bus].add(c.to_bus)
                adj[c.to_bus].add(c.from_bus)
    if hasattr(state, "freq_converters"):
        for c in state.freq_converters:
            if c.from_bus in valid_buses and c.to_bus in valid_buses:
                if c.from_bus != c.to_bus:
                    adj[c.from_bus].add(c.to_bus)
                    adj[c.to_bus].add(c.from_bus)

    visited: set[str] = set()
    comp_id = 0
    for seed in valid_buses:
        if seed in visited:
            continue
        stack = [seed]
        comp: set[str] = set()
        while stack:
            b = stack.pop()
            if b in visited:
                continue
            visited.add(b)
            comp.add(b)
            for nb in adj.get(b, set()):
                if nb not in visited:
                    stack.append(nb)
        rep.components[comp_id] = comp
        comp_id += 1

    # ── Per-component classification ───────────────────────────
    for cid, comp in rep.components.items():
        has_gen = any(
            g.bus in comp for g in state.generators.values()
        )
        has_demand = any(
            (state.buses[b].demand_fraction or 0.0) > 0.0
            and state.buses[b].role in ("load", "mixed")
            for b in comp
        )
        if not has_gen and not has_demand:
            rep.inert_components.append(cid)
        elif has_gen and not has_demand:
            rep.surplus_components.append(cid)

    # ── Orphan buses: singleton components with neither equipment
    # nor demand — pure floating dots on the map that contribute
    # nothing.  Buses that hold demand (or generation) but lack
    # edges are NOT orphan in this sense; they're caught by
    # surplus / inert component classification above.
    for cid, comp in rep.components.items():
        if len(comp) != 1:
            continue
        (bid,) = comp
        bus = state.buses[bid]
        has_eq = (
            any(g.bus == bid for g in state.generators.values())
            or any(b.bus == bid for b in state.batteries.values())
            or (hasattr(state, "electrolyzers")
                and any(getattr(e, "bus", None) == bid
                        for e in state.electrolyzers.values()))
        )
        has_demand = (
            (bus.demand_fraction or 0.0) > 0.0
            and bus.role in ("load", "mixed")
        )
        if not has_eq and not has_demand:
            rep.orphan_buses.append(bid)

    return rep


def audit_system_config(sys: "SystemConfig") -> TopologyAuditReport:
    """Audit a serialised SystemConfig (post YAML round-trip).

    Operates on the same data the Julia adapter consumes, so this
    catches issues introduced during serialisation as well.
    """
    rep = TopologyAuditReport()

    buses = list(sys.buses or [])
    num_buses = len(buses)
    if num_buses == 0:
        return rep
    bus_id_by_index = {i: b.bus_id for i, b in enumerate(buses)}

    # ── Lines the solver will silently drop ─────────────────────
    lines = list(getattr(sys, "transmission_lines_geo", None) or [])
    for line in lines:
        ft = getattr(line, "from_endpoint_type", None)
        tt = getattr(line, "to_endpoint_type", None)
        wire_types = {
            "generator", "battery", "electrolyzer",
            "transformer", "acdc_converter", "freq_converter",
        }
        if ft in wire_types or tt in wire_types:
            continue
        line_id = getattr(line, "line_id", "?") or "?"
        if line.from_bus is None or line.to_bus is None:
            rep.lines_dropped_unresolved.append(line_id)
            continue
        if not (0 <= line.from_bus < num_buses
                and 0 <= line.to_bus < num_buses):
            rep.lines_dropped_out_of_range.append(line_id)

    # ── Connected components ────────────────────────────────────
    adj: dict[int, set[int]] = {i: set() for i in range(num_buses)}
    for line in lines:
        ft = getattr(line, "from_endpoint_type", None)
        tt = getattr(line, "to_endpoint_type", None)
        if ft in {"generator", "battery", "electrolyzer",
                  "transformer", "acdc_converter", "freq_converter"}:
            continue
        if tt in {"generator", "battery", "electrolyzer",
                  "transformer", "acdc_converter", "freq_converter"}:
            continue
        fb, tb = line.from_bus, line.to_bus
        if fb is None or tb is None:
            continue
        if 0 <= fb < num_buses and 0 <= tb < num_buses and fb != tb:
            adj[fb].add(tb)
            adj[tb].add(fb)
    for tr in (sys.transformers or []):
        fb, tb = tr.from_bus, tr.to_bus
        if fb is None or tb is None:
            continue
        if 0 <= fb < num_buses and 0 <= tb < num_buses and fb != tb:
            adj[fb].add(tb)
            adj[tb].add(fb)
    for c in (getattr(sys, "acdc_converters", None) or []):
        fb, tb = c.from_bus, c.to_bus
        if fb is None or tb is None:
            continue
        if 0 <= fb < num_buses and 0 <= tb < num_buses and fb != tb:
            adj[fb].add(tb)
            adj[tb].add(fb)

    visited: set[int] = set()
    comp_id = 0
    for seed in range(num_buses):
        if seed in visited:
            continue
        stack = [seed]
        comp: set[str] = set()
        while stack:
            b = stack.pop()
            if b in visited:
                continue
            visited.add(b)
            comp.add(bus_id_by_index[b])
            for nb in adj[b]:
                if nb not in visited:
                    stack.append(nb)
        rep.components[comp_id] = comp
        comp_id += 1

    return rep


def diff_audits(
    gui_audit: TopologyAuditReport,
    cfg_audit: TopologyAuditReport,
) -> list[str]:
    """Human-readable diff between GUI-side and SystemConfig-side audits.

    A non-empty diff means serialisation lost or distorted topology.
    """
    msgs: list[str] = []
    g_n = len(gui_audit.components)
    c_n = len(cfg_audit.components)
    if g_n != c_n:
        msgs.append(
            f"Component count differs: GUI sees {g_n}, "
            f"solver sees {c_n}."
        )
    g_largest = max((len(c) for c in gui_audit.components.values()), default=0)
    c_largest = max((len(c) for c in cfg_audit.components.values()), default=0)
    if g_largest != c_largest:
        msgs.append(
            f"Largest component size differs: GUI={g_largest} "
            f"vs solver={c_largest}."
        )
    g_drop = set(gui_audit.lines_dropped_unresolved)
    c_drop = set(cfg_audit.lines_dropped_unresolved)
    only_cfg = c_drop - g_drop
    if only_cfg:
        msgs.append(
            f"Lines dropped only in solver path: {sorted(only_cfg)[:10]}"
            + (f" (+{len(only_cfg) - 10} more)" if len(only_cfg) > 10 else "")
        )
    return msgs
