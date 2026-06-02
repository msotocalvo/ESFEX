"""Build analysis-compatible snapshots from editor state.

This module bridges the Studio (``GuiSystemState``) and the algebraic
analysis modules (``FrequencyAnalyzer``, ``ContingencyAnalyzer``) by
constructing snapshot dicts in the same format as
``SldResultsLoader.get_timestep()`` — without requiring HDF5 results.

Users configure a hypothetical dispatch scenario (generator outputs,
on/off status, demand per node) and get real-time analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HypotheticalScenario:
    """User-editable dispatch scenario for real-time analysis.

    Attributes
    ----------
    gen_outputs : dict[str, float]
        Generator instance_id → output MW.
    gen_status : dict[str, bool]
        Generator instance_id → on/off.
    node_demands : dict[int, float]
        Node index → demand MW.
    """

    gen_outputs: dict[str, float] = field(default_factory=dict)
    gen_status: dict[str, bool] = field(default_factory=dict)
    node_demands: dict[int, float] = field(default_factory=dict)


def build_default_scenario(state) -> HypotheticalScenario:
    """Create a default scenario from the current editor state.

    All non-renewable generators are set to 80% of rated power and online.
    Renewable generators are set to 50% of rated power.
    Demand per node equals the sum of generation at that node.

    Parameters
    ----------
    state : GuiSystemState
        Current editor state with generators, batteries, and nodes.

    Returns
    -------
    HypotheticalScenario
    """
    gen_outputs: dict[str, float] = {}
    gen_status: dict[str, bool] = {}

    for gen_id, gen in state.generators.items():
        is_re = gen.gen_type.lower() == "renewable"
        ratio = 0.5 if is_re else 0.8
        gen_outputs[gen_id] = gen.rated_power * ratio
        gen_status[gen_id] = True

    # Distribute generation to nodes to balance demand
    node_gen: dict[int, float] = {}
    for gen_id, gen in state.generators.items():
        bus = gen.bus
        # Resolve bus → node index
        ni = _bus_to_node(state, bus)
        node_gen[ni] = node_gen.get(ni, 0) + gen_outputs[gen_id]

    node_demands: dict[int, float] = {}
    for ni in range(len(state.nodes)):
        node_demands[ni] = node_gen.get(ni, 0.0)

    return HypotheticalScenario(
        gen_outputs=gen_outputs,
        gen_status=gen_status,
        node_demands=node_demands,
    )


def build_snapshot_from_scenario(
    state,
    scenario: HypotheticalScenario,
) -> dict[str, Any]:
    """Build a snapshot dict compatible with FrequencyAnalyzer and ContingencyAnalyzer.

    The output format matches ``SldResultsLoader.get_timestep()``.

    Parameters
    ----------
    state : GuiSystemState
        Current editor state (provides rated_power, topology, etc.).
    scenario : HypotheticalScenario
        User-configured dispatch scenario.

    Returns
    -------
    dict
        Snapshot with ``generators``, ``loads``, ``batteries``, ``lines``,
        ``nodes``, and ``system`` keys.
    """
    # ── Generators ──
    generators: dict[str, dict[str, Any]] = {}
    for gen_id, gen in state.generators.items():
        output_mw = scenario.gen_outputs.get(gen_id, 0.0)
        is_on = scenario.gen_status.get(gen_id, True)
        generators[gen_id] = {
            "output_mw": output_mw if is_on else 0.0,
            "capacity_mw": gen.rated_power,
            "status": 1 if is_on else 0,
            "is_startup": False,
            "fuel": gen.fuel,
            "gen_type": gen.gen_type,
        }

    # ── Loads ──
    loads: dict[str, dict[str, Any]] = {}
    for ni in range(len(state.nodes)):
        demand = scenario.node_demands.get(ni, 0.0)
        loads[f"load_node_{ni}"] = {"demand_mw": demand}

    # ── Batteries ──
    batteries: dict[str, dict[str, Any]] = {}
    for bat_id, bat in state.batteries.items():
        batteries[bat_id] = {
            "charge_mw": 0.0,
            "discharge_mw": 0.0,
            "soc_mwh": bat.capacity * 0.5,
            "capacity_mwh": bat.capacity,
        }

    # ── Lines ──
    lines: dict[str, dict[str, Any]] = {}
    for tl in state.transmission_lines:
        line_id = tl.line_id
        lines[f"edge_{line_id}"] = {
            "flow_mw": 0.0,
            "capacity_mw": tl.capacity_mw,
            "utilization_pct": 0.0,
            "q_from_mvar": 0.0,
            "p_loss_mw": 0.0,
            "loading_pct": 0.0,
        }

    # ── Nodes ──
    nodes: dict[int, dict[str, Any]] = {}
    for ni in range(len(state.nodes)):
        demand = scenario.node_demands.get(ni, 0.0)
        node_gen_total = sum(
            generators[gid]["output_mw"]
            for gid, gen in state.generators.items()
            if _bus_to_node(state, gen.bus) == ni
        )
        nodes[ni] = {
            "demand_mw": demand,
            "generation_mw": node_gen_total,
            "price": 0.0,
            "reserve_static_mw": 0.0,
            "reserve_dynamic_mw": 0.0,
            "reserve_static_loss_mw": 0.0,
            "reserve_dynamic_loss_mw": 0.0,
            "voltage_angle_deg": 0.0,
            "vm_pu": 1.0,
            "co2_tons": 0.0,
        }

    # ── System summary ──
    # Field names must match SldResultsLoader output (consumed by JS)
    total_gen = sum(g["output_mw"] for g in generators.values())
    total_demand = sum(l["demand_mw"] for l in loads.values())
    total_re = sum(
        g["output_mw"] for g in generators.values()
        if g.get("gen_type", "").lower() == "renewable"
    )
    re_frac = (total_re / total_gen) if total_gen > 0 else 0.0

    system: dict[str, Any] = {
        "year": "Analysis",
        "hour": "-",
        "total_gen_mw": total_gen,
        "total_demand_mw": total_demand,
        "re_penetration": re_frac,
        "co2_tons": 0.0,
        "power_flow": None,
        "short_circuit": None,
    }

    return {
        "generators": generators,
        "loads": loads,
        "batteries": batteries,
        "lines": lines,
        "nodes": nodes,
        "system": system,
    }


def _bus_to_node(state, bus_id: str) -> int:
    """Resolve a bus_id to its parent node index."""
    bus = state.buses.get(bus_id)
    if bus is not None:
        return bus.parent_node
    # Fallback: parse "bus_N" → N
    try:
        return int(bus_id.split("_")[-1])
    except (ValueError, IndexError):
        return 0
