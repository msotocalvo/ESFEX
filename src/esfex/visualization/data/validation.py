"""Validation of GUI system state for network consistency."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

from esfex.visualization.data.gui_model import (
    GuiInterSystemLink,
    GuiSystemState,
    RENEWABLE_FUELS,
)

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiModel


# ── Data structures ──────────────────────────────────────────────


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: Literal["error", "warning", "info"]
    category: str
    message: str
    element_type: str = ""
    element_id: str = ""


@dataclass
class SimplificationAction:
    """A single removal action for network simplification."""

    action_type: Literal[
        "remove_bus", "remove_line",
        "remove_fuel_entry", "remove_fuel_storage", "remove_fuel_route",
    ]
    element_id: str
    reason: str


@dataclass
class InfrastructureSuggestion:
    """A suggestion to merge multiple generators or batteries into one.

    Levels control geographic scope:
    - ``"bus"``: merge units on the same bus (same fuel + gen_type)
    - ``"circuit"``: merge across buses in the same connected component
    - ``"node"``: merge across all buses within a node
    """

    level: Literal["bus", "circuit", "node"]
    equipment_type: Literal["generator", "battery"]
    instance_ids: list[str]
    target_bus: str
    target_unit_key: str
    target_name: str
    fuel: str
    gen_type: str  # generators only; "" for batteries
    total_rated_power: float
    total_capacity: float  # batteries only; 0 for generators
    reduction: int  # len(instance_ids) - 1
    # Collateral infrastructure changes
    buses_to_remove: list[str] = field(default_factory=list)
    lines_to_remove: list[str] = field(default_factory=list)
    transformers_to_remove: list[int] = field(default_factory=list)  # indices
    description: str = ""


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two (lat, lng) points."""
    import math
    la1, lo1 = math.radians(lat1), math.radians(lng1)
    la2, lo2 = math.radians(lat2), math.radians(lng2)
    dlat, dlng = la2 - la1, lo2 - lo1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class SimplificationConfig:
    """User-tunable thresholds for network simplification."""

    small_generator_fraction: float = 0.01
    parallel_voltage_tolerance_kv: float = 1.0
    min_reactance_pu: float = 0.0001
    # Maximum geographic distance (km) between two buses for them to be
    # considered mergeable by intra-node voltage / full-node collapse.
    # Without this guard, a node whose spatial cluster spans hundreds
    # of kilometres (a clustering artefact) collapses all of its buses
    # onto a single "surviving" bus, redirecting every transmission
    # line that touched the others to the survivor's location and
    # producing visibly distorted lines (e.g. Moa → Pinar del Río).
    # 50 km is conservative for transmission-level networks; raise for
    # very sparse grids, lower for distribution-scale models.
    max_merge_distance_km: float = 50.0


@dataclass
class TopologySuggestion:
    """A suggestion to modify network topology (bus/line operations)."""

    action_type: Literal[
        "parallel_line_merge",
        "radial_prune",
        "series_eliminate",
        "voltage_collapse",
        "full_node_collapse",
        "small_gen_absorb",
    ]
    level: int  # simplification level (1-4)
    description: str
    # Elements affected
    buses_to_remove: list[str] = field(default_factory=list)
    buses_to_merge: dict[str, str] = field(default_factory=dict)  # removed → surviving
    lines_to_remove: list[str] = field(default_factory=list)
    lines_to_create: list[dict] = field(default_factory=list)  # equivalent line specs
    transformers_to_remove: list[int] = field(default_factory=list)
    demand_redistribution: dict[str, float] = field(default_factory=dict)
    equipment_reassignment: dict[str, str] = field(default_factory=dict)
    slack_transfer: Optional[tuple[str, str]] = None  # (old_bus, new_bus)
    elements_removed: int = 0


@dataclass
class SimplificationPlan:
    """Complete plan for a given simplification level."""

    level: int
    infrastructure_suggestions: list[InfrastructureSuggestion] = field(
        default_factory=list,
    )
    topology_suggestions: list[TopologySuggestion] = field(default_factory=list)
    buses_before: int = 0
    buses_after: int = 0
    lines_before: int = 0
    lines_after: int = 0
    generators_before: int = 0
    generators_after: int = 0
    transformers_before: int = 0
    transformers_after: int = 0


# Category → validator list mapping
_CATEGORY_VALIDATORS: dict[str, list[str]] = {
    "structural": ["_validate_nodes", "_validate_lines", "_validate_line_connections"],
    "electrical": [
        "_validate_buses",
        "_validate_generators",
        "_validate_batteries",
        "_validate_transformers",
        "_validate_converters",
    ],
    "demand": ["_validate_demand"],
    "generation": ["_validate_generation"],
    "fuel_network": [
        "_validate_fuel_catalog",
        "_validate_fuel_entries",
        "_validate_fuel_network",
    ],
    "connectivity": ["_validate_connectivity"],
    "topology_audit": ["_validate_topology_audit"],
}

# All category keys in display order
CATEGORY_ORDER: list[str] = [
    "structural",
    "electrical",
    "demand",
    "generation",
    "fuel_network",
    "connectivity",
    "topology_audit",
]


# ── Main entry point ─────────────────────────────────────────────


ProgressCallback = Optional[Callable[[int, int, str], None]]


def count_validators(categories: set[str] | None = None) -> int:
    """Return the total number of validator steps for the given categories.

    Useful for pre-calculating progress bar range before starting validation.
    """
    if categories is None:
        categories = set(CATEGORY_ORDER)
    return sum(
        len(_CATEGORY_VALIDATORS[cat])
        for cat in categories
        if cat in _CATEGORY_VALIDATORS
    )


def preload_demand_data(state: GuiSystemState) -> None:
    """Pre-load demand CSV data for nodes that have a path but no data yet.

    Call this on the main thread before starting background validation
    to avoid mutating *state* from a worker thread.
    """
    _load_demand_for_nodes(state.nodes)


def validate_state(
    state: GuiSystemState,
    categories: set[str] | None = None,
    progress_callback: ProgressCallback = None,
) -> list[ValidationIssue]:
    """Run selected validators and return a list of issues.

    Args:
        state: The system state to validate.
        categories: Set of category keys to run.  *None* = all.
        progress_callback: Optional ``(step, total, description)`` callback
            invoked before each validator step.
    """
    if categories is None:
        categories = set(CATEGORY_ORDER)

    # Collect validator functions in order
    validator_funcs: list[tuple[str, str]] = []  # (display_name, func_name)
    for cat in CATEGORY_ORDER:
        if cat not in categories:
            continue
        for func_name in _CATEGORY_VALIDATORS[cat]:
            display = cat.replace("_", " ").title()
            validator_funcs.append((display, func_name))

    total = len(validator_funcs)
    issues: list[ValidationIssue] = []

    for step, (display, func_name) in enumerate(validator_funcs):
        if progress_callback:
            progress_callback(step, total, f"Checking {display}...")
        func = globals()[func_name]
        issues.extend(func(state))

    if progress_callback:
        progress_callback(total, total, "Validation complete")

    return issues


# ── Structural validators ────────────────────────────────────────


def _validate_nodes(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not state.nodes:
        issues.append(ValidationIssue(
            severity="error", category="Node",
            message="System has no nodes defined",
        ))
        return issues

    indices = [n.index for n in state.nodes]
    if len(indices) != len(set(indices)):
        issues.append(ValidationIssue(
            severity="error",
            category="Node",
            message="Duplicate node indices detected",
        ))

    # Nodes with no buses
    for node in state.nodes:
        if not any(b.parent_node == node.index for b in state.buses.values()):
            issues.append(ValidationIssue(
                severity="warning", category="Node",
                message=f"Node {node.index} ({node.name}): has no buses",
                element_type="node", element_id=str(node.index),
            ))

    return issues


def _validate_lines(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bus_ids = set(state.buses.keys())

    # Wire-lines (decorative connectors to equipment / transformers)
    # are visual-only — capacity is 0 by design and the solver ignores
    # them.  Skip them in this validator so they don't drown real
    # issues in hundreds of bogus "capacity is zero" warnings.
    _wire_endpoint_types = {
        "generator", "battery", "electrolyzer",
        "transformer", "acdc_converter", "freq_converter",
    }

    def _is_wire_line(ln) -> bool:
        # Authoritative: the decorative flag set on construction by
        # _rebuild_visual_wire_lines. Legacy fallback for old YAMLs.
        if getattr(ln, "decorative", False):
            return True
        has_eq_endpoint = (
            (ln.from_endpoint and ln.from_endpoint.element_type in _wire_endpoint_types)
            or (ln.to_endpoint and ln.to_endpoint.element_type in _wire_endpoint_types)
        )
        zero_capacity = (getattr(ln, "capacity_mw", 0) or 0) <= 0
        return has_eq_endpoint and zero_capacity

    # Duplicate line IDs (apply to all lines, wire or not)
    seen_ids: set[str] = set()
    for ln in state.transmission_lines:
        if ln.line_id in seen_ids:
            issues.append(ValidationIssue(
                severity="error", category="Line",
                message=f"Duplicate line ID: {ln.line_id}",
                element_type="line", element_id=ln.line_id,
            ))
        seen_ids.add(ln.line_id)

    for ln in state.transmission_lines:
        if _is_wire_line(ln):
            continue
        lid = ln.line_id
        # Self-loop check.  Lines with from_bus == to_bus are valid when
        # the endpoint refs differ (e.g. equipment→bus or bus→transformer
        # links created by auto-complete chains).  Only flag as self-loop
        # when endpoints are truly identical or absent.
        if ln.from_bus == ln.to_bus:
            is_real_self_loop = True
            if (ln.from_endpoint and ln.to_endpoint
                    and (ln.from_endpoint.element_type != ln.to_endpoint.element_type
                         or ln.from_endpoint.element_id != ln.to_endpoint.element_id)):
                is_real_self_loop = False
            if is_real_self_loop:
                issues.append(ValidationIssue(
                    severity="error", category="Line",
                    message=f"Line {lid}: self-loop (from_bus == to_bus == {ln.from_bus})",
                    element_type="line", element_id=lid,
                ))
        if ln.from_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Line",
                message=f"Line {lid}: from_bus '{ln.from_bus}' does not exist",
                element_type="line", element_id=lid,
            ))
        if ln.to_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Line",
                message=f"Line {lid}: to_bus '{ln.to_bus}' does not exist",
                element_type="line", element_id=lid,
            ))
        if ln.capacity_mw <= 0:
            issues.append(ValidationIssue(
                severity="warning", category="Line",
                message=f"Line {lid}: capacity is zero or negative",
                element_type="line", element_id=lid,
            ))
    return issues


def _validate_line_connections(state: GuiSystemState) -> list[ValidationIssue]:
    """Validate that transmission line endpoints follow connection rules."""
    from esfex.visualization.data.connectivity_rules import (
        get_connection_error_message,
        is_valid_connection,
    )

    issues: list[ValidationIssue] = []

    for ln in state.transmission_lines:
        # Skip lines without endpoint metadata (legacy configs)
        if not ln.from_endpoint or not ln.to_endpoint:
            continue

        from_type = ln.from_endpoint.element_type
        to_type = ln.to_endpoint.element_type

        if not is_valid_connection(from_type, to_type):
            issues.append(
                ValidationIssue(
                    severity="error",
                    category="Connectivity",
                    message=f"Line {ln.line_id}: {get_connection_error_message(from_type, to_type)}",
                    element_type="line",
                    element_id=ln.line_id,
                )
            )

    return issues


# ── Electrical validators ────────────────────────────────────────


def _validate_buses(state: GuiSystemState) -> list[ValidationIssue]:
    """Check bus integrity: orphan buses, missing refs, demand fractions."""
    issues: list[ValidationIssue] = []
    node_indices = {n.index for n in state.nodes}
    bus_ids = set(state.buses.keys())

    for bus_id, bus in state.buses.items():
        # Orphan bus
        if bus.parent_node not in node_indices:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Bus '{bus.name}' ({bus_id}): parent node {bus.parent_node} does not exist",
                element_type="bus", element_id=bus_id,
            ))

        # Bus with no equipment, no connections, no demand
        has_equip = (
            any(g.bus == bus_id for g in state.generators.values())
            or any(b.bus == bus_id for b in state.batteries.values())
            or any(e.bus == bus_id for e in state.electrolyzers.values())
        )
        has_conn = (
            any(ln.from_bus == bus_id or ln.to_bus == bus_id
                for ln in state.transmission_lines)
            or any(tr.from_bus == bus_id or tr.to_bus == bus_id
                   for tr in state.transformers)
            or any(c.from_bus == bus_id or c.to_bus == bus_id
                   for c in state.acdc_converters)
            or any(c.from_bus == bus_id or c.to_bus == bus_id
                   for c in state.freq_converters)
        )
        if not has_equip and not has_conn and bus.demand_fraction <= 0:
            issues.append(ValidationIssue(
                severity="warning", category="Bus",
                message=f"Bus '{bus.name}' ({bus_id}): no equipment, no connections, no demand",
                element_type="bus", element_id=bus_id,
            ))

        # Role/demand_fraction invariant
        if bus.role == "connection" and bus.demand_fraction != 0:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=(
                    f"Bus '{bus.name}' ({bus_id}): role='connection' requires "
                    f"demand_fraction=0 (got {bus.demand_fraction})"
                ),
                element_type="bus", element_id=bus_id,
            ))

    # demand_fraction sum per node — only load/mixed buses count.
    # Connection buses are forced to df=0 and don't participate in the sum.
    for node in state.nodes:
        buses = [b for b in state.buses.values() if b.parent_node == node.index]
        if buses:
            load_buses = [b for b in buses if b.role in ("load", "mixed")]
            total_frac = sum(b.demand_fraction for b in load_buses)
            if load_buses and abs(total_frac - 1.0) > 0.01:
                # Error (not warning): a sum != 1 double-counts (or under-counts)
                # the node's demand on every solve, which silently invalidates
                # results. Surfaces in QMessageBox post-import. (See cuba.yaml
                # sum=2 incident: previously reported as warning and ignored.)
                issues.append(ValidationIssue(
                    severity="error", category="Bus",
                    message=(
                        f"Node {node.index} ({node.name}): "
                        f"load bus demand fractions sum to {total_frac:.4f}, expected 1.0"
                    ),
                    element_type="node", element_id=str(node.index),
                ))

    # Equipment referencing non-existent buses
    for inst_id, inst in state.generators.items():
        if inst.bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Generator '{inst.name}' ({inst_id}): bus '{inst.bus}' does not exist",
                element_type="generator", element_id=inst_id,
            ))
    for inst_id, inst in state.batteries.items():
        if inst.bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Battery '{inst.name}' ({inst_id}): bus '{inst.bus}' does not exist",
                element_type="battery", element_id=inst_id,
            ))
    for inst_id, inst in state.electrolyzers.items():
        if inst.bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Electrolyzer '{inst.name}' ({inst_id}): bus '{inst.bus}' does not exist",
                element_type="electrolyzer", element_id=inst_id,
            ))
    for ln in state.transmission_lines:
        if ln.from_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Line {ln.line_id}: from_bus '{ln.from_bus}' does not exist",
                element_type="line", element_id=ln.line_id,
            ))
        if ln.to_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Bus",
                message=f"Line {ln.line_id}: to_bus '{ln.to_bus}' does not exist",
                element_type="line", element_id=ln.line_id,
            ))

    return issues


def _validate_generators(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bus_ids = set(state.buses.keys())
    for inst_id, gen in state.generators.items():
        if gen.bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Generator",
                message=f"Generator '{gen.name}' ({inst_id}): bus '{gen.bus}' does not exist",
                element_type="generator", element_id=inst_id,
            ))
        else:
            # Bus-node consistency
            bus = state.buses[gen.bus]
            if bus.parent_node != gen.node:
                issues.append(ValidationIssue(
                    severity="warning", category="Generator",
                    message=(
                        f"Generator '{gen.name}' ({inst_id}): node={gen.node} "
                        f"doesn't match bus '{gen.bus}' parent_node={bus.parent_node}"
                    ),
                    element_type="generator", element_id=inst_id,
                ))

        # Negative rated power
        if gen.rated_power < 0:
            issues.append(ValidationIssue(
                severity="error", category="Generator",
                message=f"Generator '{gen.name}' ({inst_id}): negative rated_power ({gen.rated_power})",
                element_type="generator", element_id=inst_id,
            ))

        # min_power > rated_power
        if gen.rated_power > 0 and gen.min_power > gen.rated_power:
            issues.append(ValidationIssue(
                severity="error", category="Generator",
                message=(
                    f"Generator '{gen.name}' ({inst_id}): "
                    f"min_power ({gen.min_power}) > rated_power ({gen.rated_power})"
                ),
                element_type="generator", element_id=inst_id,
            ))

        # Efficiency out of range (non-renewable)
        if gen.fuel and gen.fuel not in RENEWABLE_FUELS:
            if gen.eff_at_rated > 1.0 or (gen.rated_power > 0 and gen.eff_at_rated <= 0):
                issues.append(ValidationIssue(
                    severity="warning", category="Generator",
                    message=(
                        f"Generator '{gen.name}' ({inst_id}): "
                        f"eff_at_rated={gen.eff_at_rated} out of valid range (0, 1]"
                    ),
                    element_type="generator", element_id=inst_id,
                ))

        # Lifetime already expired
        if gen.rated_power > 0 and gen.initial_age >= gen.life_time:
            issues.append(ValidationIssue(
                severity="warning", category="Generator",
                message=(
                    f"Generator '{gen.name}' ({inst_id}): "
                    f"initial_age ({gen.initial_age}) >= life_time ({gen.life_time}), "
                    "unit starts retired"
                ),
                element_type="generator", element_id=inst_id,
            ))

    return issues


def _validate_batteries(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bus_ids = set(state.buses.keys())
    for inst_id, bat in state.batteries.items():
        if bat.bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Battery",
                message=f"Battery '{bat.name}' ({inst_id}): bus '{bat.bus}' does not exist",
                element_type="battery", element_id=inst_id,
            ))
        else:
            # Bus-node consistency
            bus = state.buses[bat.bus]
            if bus.parent_node != bat.node:
                issues.append(ValidationIssue(
                    severity="warning", category="Battery",
                    message=(
                        f"Battery '{bat.name}' ({inst_id}): node={bat.node} "
                        f"doesn't match bus '{bat.bus}' parent_node={bus.parent_node}"
                    ),
                    element_type="battery", element_id=inst_id,
                ))

        # Negative rated power
        if bat.rated_power < 0:
            issues.append(ValidationIssue(
                severity="error", category="Battery",
                message=f"Battery '{bat.name}' ({inst_id}): negative rated_power ({bat.rated_power})",
                element_type="battery", element_id=inst_id,
            ))

        # Charge/discharge efficiency out of range
        if bat.efficiency_charge > 1.0 or bat.efficiency_charge <= 0:
            issues.append(ValidationIssue(
                severity="error", category="Battery",
                message=(
                    f"Battery '{bat.name}' ({inst_id}): "
                    f"efficiency_charge={bat.efficiency_charge} out of valid range (0, 1]"
                ),
                element_type="battery", element_id=inst_id,
            ))
        if bat.efficiency_discharge > 1.0 or bat.efficiency_discharge <= 0:
            issues.append(ValidationIssue(
                severity="error", category="Battery",
                message=(
                    f"Battery '{bat.name}' ({inst_id}): "
                    f"efficiency_discharge={bat.efficiency_discharge} out of valid range (0, 1]"
                ),
                element_type="battery", element_id=inst_id,
            ))

        # Energy capacity < rated power (less than 1 hour at full power)
        if bat.rated_power > 0 and bat.capacity > 0 and bat.capacity < bat.rated_power:
            issues.append(ValidationIssue(
                severity="warning", category="Battery",
                message=(
                    f"Battery '{bat.name}' ({inst_id}): "
                    f"capacity ({bat.capacity} MWh) < rated_power ({bat.rated_power} MW), "
                    "less than 1 hour at full power"
                ),
                element_type="battery", element_id=inst_id,
            ))

        # Lifetime already expired
        if bat.rated_power > 0 and bat.initial_age >= bat.life_time:
            issues.append(ValidationIssue(
                severity="warning", category="Battery",
                message=(
                    f"Battery '{bat.name}' ({inst_id}): "
                    f"initial_age ({bat.initial_age}) >= life_time ({bat.life_time}), "
                    "unit starts retired"
                ),
                element_type="battery", element_id=inst_id,
            ))

    return issues


def _validate_transformers(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bus_ids = set(state.buses.keys())
    for i, tr in enumerate(state.transformers):
        if tr.from_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Transformer",
                message=f"Transformer '{tr.name}': from_bus '{tr.from_bus}' does not exist",
                element_type="transformer", element_id=str(i),
            ))
        if tr.to_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Transformer",
                message=f"Transformer '{tr.name}': to_bus '{tr.to_bus}' does not exist",
                element_type="transformer", element_id=str(i),
            ))

        # Self-loop
        if tr.from_bus == tr.to_bus:
            issues.append(ValidationIssue(
                severity="error", category="Transformer",
                message=f"Transformer '{tr.name}': self-loop (from_bus == to_bus == {tr.from_bus})",
                element_type="transformer", element_id=str(i),
            ))

        # Same voltage on both sides — resolve from buses if available,
        # since the transformer's own voltage fields may be stale.
        from_bus_obj = state.buses.get(tr.from_bus)
        to_bus_obj = state.buses.get(tr.to_bus)
        from_v = from_bus_obj.voltage_kv if from_bus_obj else tr.from_voltage_kv
        to_v = to_bus_obj.voltage_kv if to_bus_obj else tr.to_voltage_kv
        if from_v > 0 and to_v > 0 and from_v == to_v:
            issues.append(ValidationIssue(
                severity="warning", category="Transformer",
                message=(
                    f"Transformer '{tr.name}': same voltage on both sides "
                    f"({from_v} kV)"
                ),
                element_type="transformer", element_id=str(i),
            ))

        # Zero or negative rated power
        if tr.rated_power_mva <= 0:
            issues.append(ValidationIssue(
                severity="warning", category="Transformer",
                message=f"Transformer '{tr.name}': rated_power_mva is zero or negative",
                element_type="transformer", element_id=str(i),
            ))

    return issues


def _validate_converters(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bus_ids = set(state.buses.keys())
    for i, conv in enumerate(state.acdc_converters):
        if conv.from_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="AC/DC Converter",
                message=f"AC/DC Converter '{conv.name}': from_bus '{conv.from_bus}' does not exist",
                element_type="acdc_converter", element_id=str(i),
            ))
        if conv.to_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="AC/DC Converter",
                message=f"AC/DC Converter '{conv.name}': to_bus '{conv.to_bus}' does not exist",
                element_type="acdc_converter", element_id=str(i),
            ))
        if conv.from_bus == conv.to_bus:
            issues.append(ValidationIssue(
                severity="error", category="AC/DC Converter",
                message=f"AC/DC Converter '{conv.name}': self-loop (from_bus == to_bus == {conv.from_bus})",
                element_type="acdc_converter", element_id=str(i),
            ))
        if conv.efficiency_rectify <= 0 or conv.efficiency_invert <= 0:
            issues.append(ValidationIssue(
                severity="error", category="AC/DC Converter",
                message=f"AC/DC Converter '{conv.name}': efficiency must be > 0",
                element_type="acdc_converter", element_id=str(i),
            ))
        if conv.efficiency_rectify > 1.0 or conv.efficiency_invert > 1.0:
            issues.append(ValidationIssue(
                severity="error", category="AC/DC Converter",
                message=f"AC/DC Converter '{conv.name}': efficiency > 1.0 is not physical",
                element_type="acdc_converter", element_id=str(i),
            ))
    for i, conv in enumerate(state.freq_converters):
        if conv.from_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Freq. Converter",
                message=f"Freq. Converter '{conv.name}': from_bus '{conv.from_bus}' does not exist",
                element_type="freq_converter", element_id=str(i),
            ))
        if conv.to_bus not in bus_ids:
            issues.append(ValidationIssue(
                severity="error", category="Freq. Converter",
                message=f"Freq. Converter '{conv.name}': to_bus '{conv.to_bus}' does not exist",
                element_type="freq_converter", element_id=str(i),
            ))
        if conv.from_bus == conv.to_bus:
            issues.append(ValidationIssue(
                severity="error", category="Freq. Converter",
                message=f"Freq. Converter '{conv.name}': self-loop (from_bus == to_bus == {conv.from_bus})",
                element_type="freq_converter", element_id=str(i),
            ))
        if conv.efficiency_a_to_b <= 0 or conv.efficiency_b_to_a <= 0:
            issues.append(ValidationIssue(
                severity="error", category="Freq. Converter",
                message=f"Freq. Converter '{conv.name}': efficiency must be > 0",
                element_type="freq_converter", element_id=str(i),
            ))
        if conv.efficiency_a_to_b > 1.0 or conv.efficiency_b_to_a > 1.0:
            issues.append(ValidationIssue(
                severity="error", category="Freq. Converter",
                message=f"Freq. Converter '{conv.name}': efficiency > 1.0 is not physical",
                element_type="freq_converter", element_id=str(i),
            ))
    return issues


# ── Demand validators ────────────────────────────────────────────


def _load_demand_for_nodes(nodes: list) -> None:
    """Lazily load demand data for nodes that have a csv_path but no data."""
    from pathlib import Path

    # Collect nodes needing loading, grouped by csv_path
    paths: dict[str, list] = {}
    for node in nodes:
        if node.demand.csv_path and node.demand.data is None:
            paths.setdefault(node.demand.csv_path, []).append(node)
    if not paths:
        return

    try:
        import pandas as pd
    except ImportError:
        return

    for csv_path, path_nodes in paths.items():
        p = Path(csv_path)
        if not p.is_file():
            continue
        # Read the whole file. The previous ``usecols=needed_cols``
        # optimisation was actively wrong for single-column per-node
        # files: passing ``usecols=[5]`` on a one-column file raises
        # "column 5 not found", which was caught silently → the node
        # never got its demand. Read everything; the projection below
        # picks the right column.
        try:
            if p.suffix in (".xlsx", ".xls"):
                try:
                    df = pd.read_excel(p, header=None, engine="calamine")
                except ImportError:
                    df = pd.read_excel(p, header=None)
            else:
                df = pd.read_csv(p, header=None)
        except Exception as exc:
            # The downstream check at _validate_demand emits a generic
            # "csv path set but data could not be loaded" warning, so we
            # don't add another issue here — but the *reason* (corrupt
            # encoding, bad delimiter, permissions) was being thrown
            # away. Log it so it lands in the console / log file.
            import logging
            logging.getLogger(__name__).warning(
                "Failed to read demand file %s: %s: %s",
                p, type(exc).__name__, exc,
            )
            continue

        # Single-column file shared by multiple nodes is the same
        # broadcast trap that bit ``_load_demand_csv``. Refuse to
        # silently assign identical demand to many nodes.
        if df.shape[1] == 1 and len(path_nodes) > 1:
            import logging
            logging.getLogger(__name__).warning(
                "csv %r is single-column but %d nodes share it; "
                "refusing to broadcast.", csv_path, len(path_nodes),
            )
            continue

        for node in path_nodes:
            col_idx = node.index
            if df.shape[1] == 1:
                series = df.iloc[:, 0].astype(float)
            elif col_idx < df.shape[1]:
                series = df.iloc[:, col_idx].astype(float)
            else:
                continue
            data_list = series.tolist()
            from esfex.visualization.data.gui_model import GuiNodeDemand
            node.demand = GuiNodeDemand(
                csv_path=csv_path,
                data=data_list,
                num_hours=len(data_list),
                peak_mw=float(series.max()),
                total_mwh=float(series.sum()),
            )


def _validate_demand(state: GuiSystemState) -> list[ValidationIssue]:
    """Check demand configuration across nodes."""
    issues: list[ValidationIssue] = []

    # Lazily load demand data if csv_path is set but data not yet loaded
    _load_demand_for_nodes(state.nodes)

    total_system_demand = 0.0
    for node in state.nodes:
        has_equipment = (
            any(g.node == node.index for g in state.generators.values())
            or any(b.node == node.index for b in state.batteries.values())
        )
        has_demand = (
            node.demand.peak_mw > 0
            or node.demand.total_mwh > 0
            or node.demand.csv_path
        )
        if has_equipment and not has_demand:
            issues.append(ValidationIssue(
                severity="warning", category="Demand",
                message=(
                    f"Node {node.index} ({node.name}): "
                    "has generation equipment but no demand data"
                ),
                element_type="node", element_id=str(node.index),
            ))

        total_system_demand += node.demand.peak_mw

        if node.demand.csv_path and not node.demand.data:
            issues.append(ValidationIssue(
                severity="warning", category="Demand",
                message=(
                    f"Node {node.index} ({node.name}): "
                    "demand CSV path set but data could not be loaded"
                ),
                element_type="node", element_id=str(node.index),
            ))

    if total_system_demand <= 0 and state.nodes:
        issues.append(ValidationIssue(
            severity="error", category="Demand",
            message="System has zero total peak demand across all nodes",
        ))

    # Demand time series length mismatch across nodes
    hours_set: dict[int, list[str]] = {}
    for node in state.nodes:
        if node.demand.data and node.demand.num_hours > 0:
            hours_set.setdefault(node.demand.num_hours, []).append(
                f"{node.index} ({node.name})"
            )
    if len(hours_set) > 1:
        detail = ", ".join(
            f"{h}h: [{', '.join(ns[:3])}{'...' if len(ns) > 3 else ''}]"
            for h, ns in sorted(hours_set.items())
        )
        issues.append(ValidationIssue(
            severity="warning", category="Demand",
            message=f"Demand time series have different lengths: {detail}",
        ))

    # Negative demand values
    for node in state.nodes:
        if node.demand.data:
            min_val = min(node.demand.data)
            if min_val < 0:
                issues.append(ValidationIssue(
                    severity="error", category="Demand",
                    message=(
                        f"Node {node.index} ({node.name}): "
                        f"demand data contains negative values (min={min_val:.2f} MW)"
                    ),
                    element_type="node", element_id=str(node.index),
                ))

    return issues


# ── Generation validators ────────────────────────────────────────


def _validate_generation(state: GuiSystemState) -> list[ValidationIssue]:
    """Check generation adequacy and configuration."""
    issues: list[ValidationIssue] = []

    total_gen = sum(
        g.rated_power for g in state.generators.values()
    )
    total_bat = sum(
        b.rated_power for b in state.batteries.values()
    )
    total_peak = sum(n.demand.peak_mw for n in state.nodes)

    if total_peak > 0:
        adequacy = (total_gen + total_bat) / total_peak
        if adequacy < 1.0:
            issues.append(ValidationIssue(
                severity="warning", category="Generation",
                message=(
                    f"Total installed + investable capacity "
                    f"({total_gen + total_bat:.0f} MW) < peak demand "
                    f"({total_peak:.0f} MW). Adequacy ratio: {adequacy:.2f}"
                ),
            ))

    from pathlib import Path

    for inst_id, gen in state.generators.items():
        if gen.gen_type == "Renewable" and gen.rated_power > 0:
            if not gen.availability_file:
                issues.append(ValidationIssue(
                    severity="warning", category="Generation",
                    message=(
                        f"Generator '{gen.name}' ({inst_id}): "
                        "renewable with rated_power > 0 but no availability file"
                    ),
                    element_type="generator", element_id=inst_id,
                ))
            elif not Path(gen.availability_file).is_file():
                issues.append(ValidationIssue(
                    severity="warning", category="Generation",
                    message=(
                        f"Generator '{gen.name}' ({inst_id}): "
                        f"availability file not found: {gen.availability_file}"
                    ),
                    element_type="generator", element_id=inst_id,
                ))

        if gen.rated_power <= 0:
            issues.append(ValidationIssue(
                severity="info", category="Generation",
                message=(
                    f"Generator '{gen.name}' ({inst_id}): "
                    "zero rated power"
                ),
                element_type="generator", element_id=inst_id,
            ))

    return issues


# ── Fuel network validators ──────────────────────────────────────


def _validate_fuel_catalog(state: GuiSystemState) -> list[ValidationIssue]:
    """Check fuels/technologies referenced by elements exist in the catalog.

    Different from ``_validate_fuel_network``: that one looks at the
    *supply chain* (fuel_entry_points, fuel routes); this one looks at
    the *catalog* — every ``gen.fuel`` / ``gen.technology`` must point
    to an entry in ``state.fuels`` / ``state.technologies``.
    """
    issues: list[ValidationIssue] = []

    # Build normalized lookup keys for the catalog so a generator with
    # fuel="Fuel Oil" still matches a catalog entry with id="Fuel_oil".
    from esfex.visualization.workflows.grid_mapping_builder import (
        _normalize_fuel_key,
    )
    catalog_keys: set[str] = set()
    for fid, fuel in state.fuels.items():
        catalog_keys.add(_normalize_fuel_key(fid))
        if fuel.name:
            catalog_keys.add(_normalize_fuel_key(fuel.name))

    seen_missing: set[str] = set()
    for gid, gen in state.generators.items():
        # Non-renewable generator without a fuel reference is a silent
        # bug: the LP sees fuel_cost=0 (free) AND no emissions (no entry
        # in fuel_co2 dict), so it dispatches the unit aggressively and
        # cheaply with zero CO2 — yielding implausibly low cost and
        # decarbonised results. Hard error so it surfaces before solve.
        gen_type = (getattr(gen, "type", "") or "").lower()
        if gen_type != "renewable" and gen_type != "storage":
            if not gen.fuel or gen.fuel == "None":
                issues.append(ValidationIssue(
                    severity="error", category="Fuel Network",
                    message=(
                        f"Generator '{gen.name}' ({gid}): non-renewable "
                        f"unit has empty fuel — the LP would see fuel_cost=0 "
                        f"and 0 emissions, dispatching it as free"
                    ),
                    element_type="generator", element_id=gid,
                ))
                continue
        if not gen.fuel or gen.fuel == "None":
            continue
        # Renewables (Sun/Wind/Water/Geothermal) don't need a supply
        # chain and the dispatch model treats them implicitly — flag
        # only non-renewables as catalog issues.
        if gen.fuel in RENEWABLE_FUELS:
            continue
        key = _normalize_fuel_key(gen.fuel)
        if key in catalog_keys or key in seen_missing:
            continue
        seen_missing.add(key)
        issues.append(ValidationIssue(
            severity="warning", category="Fuel Network",
            message=(
                f"Generator '{gen.name}' ({gid}): fuel '{gen.fuel}' "
                f"is not registered in the system fuel catalog"
            ),
            element_type="generator", element_id=gid,
        ))

    # Technology referenced by gens must exist in state.technologies
    tech_ids = set(state.technologies.keys())
    for gid, gen in state.generators.items():
        tid = getattr(gen, "technology", "")
        if tid and tid not in tech_ids:
            issues.append(ValidationIssue(
                severity="warning", category="Fuel Network",
                message=(
                    f"Generator '{gen.name}' ({gid}): technology "
                    f"'{tid}' is not registered in the system catalog"
                ),
                element_type="generator", element_id=gid,
            ))

    # Technology entries themselves: their `fuel` field must point at
    # an existing fuel in state.fuels (unless renewable or storage).
    seen_tech_missing: set[str] = set()
    for tid, tech in state.technologies.items():
        tfuel = getattr(tech, "fuel", "") or ""
        tcat = (getattr(tech, "category", "") or getattr(tech, "type", "") or "").lower()
        if tcat in ("storage", "electrolyzer"):
            continue
        if not tfuel or tfuel == "None":
            if tcat != "renewable":
                issues.append(ValidationIssue(
                    severity="error", category="Fuel Network",
                    message=(
                        f"Technology '{getattr(tech, 'name', tid)}' ({tid}): "
                        f"non-renewable tech has empty fuel — investments built "
                        f"from this tech will dispatch as free with 0 emissions"
                    ),
                    element_type="technology", element_id=tid,
                ))
            continue
        if tfuel in RENEWABLE_FUELS:
            continue
        tkey = _normalize_fuel_key(tfuel)
        if tkey in catalog_keys or tkey in seen_tech_missing:
            continue
        seen_tech_missing.add(tkey)
        issues.append(ValidationIssue(
            severity="warning", category="Fuel Network",
            message=(
                f"Technology '{getattr(tech, 'name', tid)}' ({tid}): "
                f"fuel '{tfuel}' is not in the system fuel catalog"
            ),
            element_type="technology", element_id=tid,
        ))

    return issues


def _validate_fuel_entries(state: GuiSystemState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    node_indices = {n.index for n in state.nodes}
    for i, fe in enumerate(state.fuel_entry_points):
        if fe.node not in node_indices:
            issues.append(ValidationIssue(
                severity="error", category="Fuel Entry",
                message=f"Fuel entry '{fe.name}': node {fe.node} does not exist",
                element_type="fuel_entry", element_id=str(i),
            ))
    return issues


def _validate_fuel_network(state: GuiSystemState) -> list[ValidationIssue]:
    """Check fuel supply chain integrity."""
    from esfex.visualization.workflows.grid_mapping_builder import (
        _normalize_fuel_key,
    )
    issues: list[ValidationIssue] = []

    # Fuels needed by non-renewable generators and electrolyzers.
    # Track display name + canonical key so aliases (Oil/Fuel_oil) match.
    fuels_needed: dict[str, str] = {}  # canonical → display name
    for gen in state.generators.values():
        if gen.fuel and gen.fuel not in RENEWABLE_FUELS and gen.fuel != "None":
            if gen.rated_power > 0:
                fuels_needed.setdefault(_normalize_fuel_key(gen.fuel), gen.fuel)
    for elz in state.electrolyzers.values():
        if elz.fuel and elz.fuel not in RENEWABLE_FUELS:
            fuels_needed.setdefault(_normalize_fuel_key(elz.fuel), elz.fuel)

    # Fuels supplied by fuel entries (canonical-keyed)
    supplied_keys: set[str] = set()
    for fe in state.fuel_entry_points:
        for f in fe.fuels:
            supplied_keys.add(_normalize_fuel_key(f))

    missing_keys = set(fuels_needed.keys()) - supplied_keys
    missing = {fuels_needed[k] for k in missing_keys}
    fuels_supplied: set[str] = supplied_keys
    if missing:
        issues.append(ValidationIssue(
            severity="warning", category="Fuel Network",
            message=(
                "Fuels used by generators but not supplied by any fuel entry: "
                + ", ".join(sorted(missing))
            ),
        ))

    # Fuel entries with no fuels assigned
    for i, fe in enumerate(state.fuel_entry_points):
        if not fe.fuels:
            issues.append(ValidationIssue(
                severity="warning", category="Fuel Network",
                message=f"Fuel entry '{fe.name}': no fuels assigned",
                element_type="fuel_entry", element_id=str(i),
            ))

    # Fuel routes referencing invalid nodes, self-loops, zero capacity
    node_indices = {n.index for n in state.nodes}
    for rt in state.fuel_transport_routes:
        if rt.from_node not in node_indices:
            issues.append(ValidationIssue(
                severity="error", category="Fuel Network",
                message=f"Fuel route '{rt.route_id}': from_node {rt.from_node} does not exist",
                element_type="fuel_route", element_id=rt.route_id,
            ))
        if rt.to_node not in node_indices:
            issues.append(ValidationIssue(
                severity="error", category="Fuel Network",
                message=f"Fuel route '{rt.route_id}': to_node {rt.to_node} does not exist",
                element_type="fuel_route", element_id=rt.route_id,
            ))
        # Intra-node routes (from_node == to_node) are valid — they model
        # within-region transport (e.g. port → depot).  No error needed.
        if rt.capacity <= 0 and not rt.fuel_params:
            issues.append(ValidationIssue(
                severity="warning", category="Fuel Network",
                message=f"Fuel route '{rt.route_id}': zero or negative capacity",
                element_type="fuel_route", element_id=rt.route_id,
            ))

    # Fuel storages with no fuels assigned
    for sid, fs in state.fuel_storages.items():
        if not fs.fuels:
            issues.append(ValidationIssue(
                severity="warning", category="Fuel Network",
                message=f"Fuel storage '{fs.name}' ({sid}): no fuels assigned",
                element_type="fuel_storage", element_id=sid,
            ))

    # Non-electric demand references unsupplied fuel
    for ned_id, ned in state.non_electric_demand.items():
        if ned.fuel and ned.fuel not in RENEWABLE_FUELS and ned.fuel != "None":
            if _normalize_fuel_key(ned.fuel) not in fuels_supplied:
                issues.append(ValidationIssue(
                    severity="warning", category="Fuel Network",
                    message=(
                        f"Non-electric demand '{ned_id}': "
                        f"fuel '{ned.fuel}' not supplied by any fuel entry"
                    ),
                ))

    return issues


# ── Connectivity validators ──────────────────────────────────────


def _validate_connectivity(state: GuiSystemState) -> list[ValidationIssue]:
    """Check that equipment on isolated nodes is flagged.

    Isolated nodes themselves are fine. But generators, batteries,
    transformers, or fuel entries on an isolated node cannot serve
    any demand, so that is a warning.
    """
    issues: list[ValidationIssue] = []
    if not state.nodes or len(state.nodes) < 2:
        return issues

    node_indices = {n.index for n in state.nodes}
    # Derive node connectivity from bus-level connections
    bus_to_node = {bid: bus.parent_node for bid, bus in state.buses.items()}
    adj: dict[int, set[int]] = {n.index: set() for n in state.nodes}
    for ln in state.transmission_lines:
        fn = bus_to_node.get(ln.from_bus)
        tn = bus_to_node.get(ln.to_bus)
        if fn is not None and tn is not None and fn in adj and tn in adj:
            adj[fn].add(tn)
            adj[tn].add(fn)

    start = state.nodes[0].index
    for n in state.nodes:
        if adj.get(n.index):
            start = n.index
            break
    visited: set[int] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adj.get(node, set()):
            if neighbor not in visited:
                queue.append(neighbor)

    # Find all connected components (not just isolated vs. visited)
    all_remaining = set(node_indices)
    components: list[set[int]] = []
    while all_remaining:
        seed = next(iter(all_remaining))
        comp: set[int] = set()
        q = deque([seed])
        while q:
            n = q.popleft()
            if n in comp:
                continue
            comp.add(n)
            for nb in adj.get(n, set()):
                if nb not in comp:
                    q.append(nb)
        components.append(comp)
        all_remaining -= comp

    if len(components) > 1:
        comps_with_demand = sum(
            1 for comp in components
            if any(nd.demand.peak_mw > 0 for nd in state.nodes if nd.index in comp)
        )
        sizes = sorted((len(c) for c in components), reverse=True)
        issues.append(ValidationIssue(
            severity="info", category="Connectivity",
            message=(
                f"Network has {len(components)} disconnected sub-networks "
                f"(sizes: {sizes}), {comps_with_demand} with demand"
            ),
        ))

    isolated = node_indices - visited
    for idx in sorted(isolated):
        node = next((n for n in state.nodes if n.index == idx), None)
        name = node.name if node else f"Node {idx}"
        has_gen = any(g.node == idx for g in state.generators.values())
        has_bat = any(b.node == idx for b in state.batteries.values())
        has_tr = any(
            tr.from_node == idx or tr.to_node == idx
            for tr in state.transformers
        )
        has_fe = any(fe.node == idx for fe in state.fuel_entry_points)
        if has_gen or has_bat or has_tr or has_fe:
            equipment = []
            if has_gen:
                equipment.append("generators")
            if has_bat:
                equipment.append("batteries")
            if has_tr:
                equipment.append("transformers")
            if has_fe:
                equipment.append("fuel entries")
            issues.append(ValidationIssue(
                severity="warning", category="Connectivity",
                message=f"Node {idx} ({name}) is isolated but has {', '.join(equipment)}",
                element_type="node", element_id=str(idx),
            ))

    return issues


# ── Dead-end detection and network simplification ────────────────
#
# Two independent simplification passes:
#   1. Electrical network: bus-level topology (buses + lines/transformers/converters)
#   2. Fuel transport network: fuel entries/storages connected by fuel routes
#
# Nodes are geographic entities and are NEVER removed by simplification.


def _build_bus_adjacency(
    state: GuiSystemState,
    active_buses: set[str],
    removed_lines: set[str] | None = None,
) -> dict[str, set[str]]:
    """Build bus-level adjacency graph from lines, transformers, converters."""
    adj: dict[str, set[str]] = {bid: set() for bid in active_buses}
    for ln in state.transmission_lines:
        if removed_lines and ln.line_id in removed_lines:
            continue
        if ln.from_bus in active_buses and ln.to_bus in active_buses:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus in active_buses and tr.to_bus in active_buses:
            adj[tr.from_bus].add(tr.to_bus)
            adj[tr.to_bus].add(tr.from_bus)
    for c in state.acdc_converters:
        if c.from_bus in active_buses and c.to_bus in active_buses:
            adj[c.from_bus].add(c.to_bus)
            adj[c.to_bus].add(c.from_bus)
    for c in state.freq_converters:
        if c.from_bus in active_buses and c.to_bus in active_buses:
            adj[c.from_bus].add(c.to_bus)
            adj[c.to_bus].add(c.from_bus)
    return adj


def _bus_has_useful_equipment(state: GuiSystemState, bus_id: str) -> bool:
    """Return True if the bus has generation, storage, or electrolyzers."""
    if any(g.bus == bus_id and g.rated_power > 0
           for g in state.generators.values()):
        return True
    if any(b.bus == bus_id and b.rated_power > 0
           for b in state.batteries.values()):
        return True
    if any(e.bus == bus_id for e in state.electrolyzers.values()):
        return True
    return False


def _bus_has_demand(state: GuiSystemState, bus_id: str) -> bool:
    """Return True if the bus carries non-zero demand."""
    bus = state.buses.get(bus_id)
    if not bus or bus.demand_fraction <= 0:
        return False
    node = next(
        (n for n in state.nodes if n.index == bus.parent_node), None,
    )
    if not node:
        return False
    return node.demand.peak_mw > 0 or node.demand.total_mwh > 0


def _find_dead_end_buses(
    state: GuiSystemState,
    progress_callback: ProgressCallback = None,
) -> list[SimplificationAction]:
    """Identify dead-end buses in the electrical network.

    A bus is a dead-end if:
    1. No generators with rated_power > 0
    2. No batteries with rated_power > 0
    3. No electrolyzers
    4. Parent node has zero demand OR bus.demand_fraction == 0
    5. Degree <= 1 in the bus adjacency graph (leaf or isolated)

    Returns actions in removal order (iterative: removing one may expose
    new dead-ends).
    """
    if not state.buses:
        return []

    actions: list[SimplificationAction] = []
    active_buses = set(state.buses.keys())
    removed_lines: set[str] = set()
    max_iterations = len(active_buses)

    for iteration in range(max_iterations):
        if progress_callback:
            progress_callback(
                iteration, max_iterations,
                f"Electrical dead-end scan iteration {iteration + 1}...",
            )

        adj = _build_bus_adjacency(state, active_buses, removed_lines)
        found_any = False

        for bus_id in sorted(active_buses):
            degree = len(adj.get(bus_id, set()))
            if degree > 1:
                continue

            if _bus_has_useful_equipment(state, bus_id):
                continue
            if _bus_has_demand(state, bus_id):
                continue

            bus = state.buses.get(bus_id)
            bus_name = bus.name if bus else bus_id

            for ln in state.transmission_lines:
                if ln.line_id in removed_lines:
                    continue
                if ln.from_bus == bus_id or ln.to_bus == bus_id:
                    if ln.from_bus in active_buses and ln.to_bus in active_buses:
                        actions.append(SimplificationAction(
                            action_type="remove_line",
                            element_id=ln.line_id,
                            reason=f"Stub line to dead-end bus '{bus_name}'",
                        ))
                        removed_lines.add(ln.line_id)

            actions.append(SimplificationAction(
                action_type="remove_bus",
                element_id=bus_id,
                reason="Dead-end bus: no generation, no demand, leaf connection",
            ))

            active_buses.discard(bus_id)
            found_any = True

        if not found_any:
            break

    return actions


# ── Fuel network simplification ──────────────────────────────────


def _build_fuel_adjacency(
    state: GuiSystemState,
    active_nodes: set[int],
    removed_routes: set[str] | None = None,
) -> dict[int, set[int]]:
    """Build node-level adjacency graph from fuel transport routes."""
    adj: dict[int, set[int]] = {idx: set() for idx in active_nodes}
    for rt in state.fuel_transport_routes:
        if removed_routes and rt.route_id in removed_routes:
            continue
        if rt.from_node in active_nodes and rt.to_node in active_nodes:
            adj[rt.from_node].add(rt.to_node)
            adj[rt.to_node].add(rt.from_node)
    return adj


def _fuel_node_has_consumers(state: GuiSystemState, node_idx: int) -> bool:
    """Return True if the node has generators that consume fuel."""
    for gen in state.generators.values():
        if gen.node == node_idx and gen.rated_power > 0:
            if gen.fuel and gen.fuel not in RENEWABLE_FUELS and gen.fuel != "None":
                return True
    return False


def _find_dead_end_fuel_elements(
    state: GuiSystemState,
    progress_callback: ProgressCallback = None,
) -> list[SimplificationAction]:
    """Identify dead-end elements in the fuel transport network.

    A fuel node (with fuel entries/storages) is a dead-end if:
    1. No generators at that node consume fuel
    2. Degree <= 1 in the fuel route graph (leaf or isolated)

    Returns removal actions for fuel routes, entries, and storages.
    """
    # Collect nodes that participate in the fuel network
    fuel_nodes: set[int] = set()
    for fe_idx, fe in enumerate(state.fuel_entry_points):
        fuel_nodes.add(fe.node)
    for fs in state.fuel_storages.values():
        fuel_nodes.add(fs.node)
    for rt in state.fuel_transport_routes:
        fuel_nodes.add(rt.from_node)
        fuel_nodes.add(rt.to_node)

    if not fuel_nodes:
        return []

    actions: list[SimplificationAction] = []
    active_nodes = set(fuel_nodes)
    removed_routes: set[str] = set()
    max_iterations = len(active_nodes)

    for iteration in range(max_iterations):
        if progress_callback:
            progress_callback(
                iteration, max_iterations,
                f"Fuel dead-end scan iteration {iteration + 1}...",
            )

        adj = _build_fuel_adjacency(state, active_nodes, removed_routes)
        found_any = False

        for node_idx in sorted(active_nodes):
            degree = len(adj.get(node_idx, set()))
            if degree > 1:
                continue

            if _fuel_node_has_consumers(state, node_idx):
                continue

            node = next((n for n in state.nodes if n.index == node_idx), None)
            node_name = node.name if node else f"Node {node_idx}"

            # Collect stub fuel routes
            for rt in state.fuel_transport_routes:
                if rt.route_id in removed_routes:
                    continue
                if rt.from_node == node_idx or rt.to_node == node_idx:
                    if rt.from_node in active_nodes and rt.to_node in active_nodes:
                        actions.append(SimplificationAction(
                            action_type="remove_fuel_route",
                            element_id=rt.route_id,
                            reason=f"Stub route to dead-end fuel node '{node_name}'",
                        ))
                        removed_routes.add(rt.route_id)

            # Remove fuel entries at this dead-end node
            for fe_idx, fe in enumerate(state.fuel_entry_points):
                if fe.node == node_idx:
                    actions.append(SimplificationAction(
                        action_type="remove_fuel_entry",
                        element_id=str(fe_idx),
                        reason=(
                            f"Fuel entry '{fe.name}' at dead-end fuel node "
                            f"'{node_name}' (no fuel consumers)"
                        ),
                    ))

            # Remove fuel storages at this dead-end node
            for sid, fs in state.fuel_storages.items():
                if fs.node == node_idx:
                    actions.append(SimplificationAction(
                        action_type="remove_fuel_storage",
                        element_id=sid,
                        reason=(
                            f"Fuel storage '{fs.name}' at dead-end fuel node "
                            f"'{node_name}' (no fuel consumers)"
                        ),
                    ))

            active_nodes.discard(node_idx)
            found_any = True

        if not found_any:
            break

    return actions


# ── Public entry points ──────────────────────────────────────────


def find_dead_end_buses(
    state: GuiSystemState,
    progress_callback: ProgressCallback = None,
) -> list[SimplificationAction]:
    """Identify dead-end elements in electrical and fuel networks.

    Runs two independent simplification passes:
    1. Electrical: dead-end buses (no equipment, no demand, leaf connection)
    2. Fuel: dead-end fuel entries/storages (no consumers, leaf connection)
    """
    actions: list[SimplificationAction] = []

    # Electrical network (bus-level)
    actions.extend(_find_dead_end_buses(state, progress_callback))

    # Fuel transport network
    actions.extend(_find_dead_end_fuel_elements(state, progress_callback))

    return actions


def simplify_network(
    model: GuiModel,
    actions: list[SimplificationAction],
) -> int:
    """Apply simplification actions to the model.

    Lines/routes are removed first, then buses, then fuel entries
    (in reverse index order to avoid index shifts), then fuel storages.

    Returns the number of actions applied.
    """
    applied = 0
    model.begin_bulk_update()
    try:
        # Phase 1: remove transmission lines
        for action in actions:
            if action.action_type == "remove_line":
                model.remove_line(action.element_id)
                applied += 1

        # Phase 2: remove buses (cascades to equipment on the bus)
        for action in actions:
            if action.action_type == "remove_bus":
                model.remove_bus(action.element_id)
                applied += 1

        # Phase 3: remove fuel routes
        for action in actions:
            if action.action_type == "remove_fuel_route":
                model.remove_fuel_route(action.element_id)
                applied += 1

        # Phase 4: remove fuel entries in reverse index order
        # (they are list-indexed, so highest index first avoids shifts)
        fe_actions = [a for a in actions if a.action_type == "remove_fuel_entry"]
        fe_actions.sort(key=lambda a: int(a.element_id), reverse=True)
        for action in fe_actions:
            idx = int(action.element_id)
            if idx < len(model.state.fuel_entry_points):
                model.remove_fuel_entry(idx)
                applied += 1

        # Phase 5: remove fuel storages
        for action in actions:
            if action.action_type == "remove_fuel_storage":
                model.remove_fuel_storage(action.element_id)
                applied += 1
    finally:
        model.end_bulk_update()

    return applied


# ── Infrastructure simplification ────────────────────────────────


def _find_intra_node_circuits(
    state: GuiSystemState,
    node_index: int,
) -> list[set[str]]:
    """Find connected components of buses within a single node.

    Connections are intra-node transmission lines and transformers.
    Returns a list of bus-id sets (each set is one circuit).
    """
    node_buses = {
        bid for bid, bus in state.buses.items()
        if bus.parent_node == node_index
    }
    if not node_buses:
        return []

    adj: dict[str, set[str]] = {bid: set() for bid in node_buses}
    for ln in state.transmission_lines:
        if ln.from_bus in node_buses and ln.to_bus in node_buses:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus in node_buses and tr.to_bus in node_buses:
            adj[tr.from_bus].add(tr.to_bus)
            adj[tr.to_bus].add(tr.from_bus)

    visited: set[str] = set()
    components: list[set[str]] = []
    for bus in node_buses:
        if bus in visited:
            continue
        comp: set[str] = set()
        queue = deque([bus])
        while queue:
            b = queue.popleft()
            if b in visited:
                continue
            visited.add(b)
            comp.add(b)
            for nb in adj.get(b, set()):
                if nb not in visited:
                    queue.append(nb)
        components.append(comp)
    return components


def _bus_has_other_equipment(
    state: GuiSystemState,
    bus_id: str,
    exclude_gen_ids: set[str],
    exclude_bat_ids: set[str],
) -> bool:
    """Check if a bus has generators/batteries/electrolyzers not in the exclude set."""
    for gid, gen in state.generators.items():
        if gen.bus == bus_id and gid not in exclude_gen_ids:
            return True
    for bid, bat in state.batteries.items():
        if bat.bus == bus_id and bid not in exclude_bat_ids:
            return True
    if hasattr(state, "electrolyzers"):
        for elec in state.electrolyzers:
            if getattr(elec, "bus", None) == bus_id:
                return True
    return False


def _find_redundant_infra(
    state: GuiSystemState,
    scope_buses: set[str],
    target_bus: str,
    merged_gen_ids: set[str],
    merged_bat_ids: set[str],
) -> tuple[list[str], list[str], list[int]]:
    """Find buses, lines, and transformers that become redundant after merging.

    Returns (buses_to_remove, lines_to_remove, transformer_indices_to_remove).
    """
    buses_to_remove: list[str] = []
    lines_to_remove: list[str] = []
    transformers_to_remove: list[int] = []

    # Find buses that will be empty after merge (no demand, no other equipment)
    for bid in scope_buses:
        if bid == target_bus:
            continue
        if _bus_has_demand(state, bid):
            continue
        if _bus_has_other_equipment(state, bid, merged_gen_ids, merged_bat_ids):
            continue
        buses_to_remove.append(bid)

    empty_set = set(buses_to_remove)

    # Lines where both endpoints are in empty buses or one is empty and
    # the other is within scope (internal connections no longer needed)
    for ln in state.transmission_lines:
        if ln.from_bus in empty_set or ln.to_bus in empty_set:
            # Only remove if both endpoints are within the scope
            if ln.from_bus in scope_buses and ln.to_bus in scope_buses:
                lines_to_remove.append(ln.line_id)

    # Transformers where both endpoints are in scope and at least one is empty
    for idx, tr in enumerate(state.transformers):
        if tr.from_bus in empty_set or tr.to_bus in empty_set:
            if tr.from_bus in scope_buses and tr.to_bus in scope_buses:
                transformers_to_remove.append(idx)

    return buses_to_remove, lines_to_remove, transformers_to_remove


def _weighted_avg(values: list[float], weights: list[float]) -> float:
    """Capacity-weighted average. Returns 0 if total weight is 0."""
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_w


def find_infrastructure_simplifications(
    state: GuiSystemState,
    level: Literal["bus", "circuit", "node"] = "bus",
) -> list[InfrastructureSuggestion]:
    """Identify groups of generators/batteries that can be merged.

    Parameters
    ----------
    state : GuiSystemState
        Current system state.
    level : ``"bus"`` | ``"circuit"`` | ``"node"``
        Geographic scope for grouping.

    Returns
    -------
    list[InfrastructureSuggestion]
        Each suggestion describes a group that can be merged into one unit.
    """
    suggestions: list[InfrastructureSuggestion] = []

    # -- Build bus→scope mapping based on level --
    bus_to_scope: dict[str, str] = {}  # bus_id -> scope_key
    scope_to_buses: dict[str, set[str]] = {}  # scope_key -> set of bus_ids

    if level == "bus":
        for bid in state.buses:
            bus_to_scope[bid] = bid
            scope_to_buses[bid] = {bid}
    elif level == "circuit":
        for node in state.nodes:
            circuits = _find_intra_node_circuits(state, node.index)
            for circuit in circuits:
                key = f"circuit_{node.index}_{'_'.join(sorted(circuit))}"
                for bid in circuit:
                    bus_to_scope[bid] = key
                scope_to_buses[key] = circuit
    elif level == "node":
        for node in state.nodes:
            key = f"node_{node.index}"
            for bid, bus in state.buses.items():
                if bus.parent_node == node.index:
                    bus_to_scope[bid] = key
                    scope_to_buses.setdefault(key, set()).add(bid)

    # -- Group generators by (scope, fuel, gen_type, availability_file) --
    gen_groups: dict[tuple, list[str]] = defaultdict(list)
    for gid, gen in state.generators.items():
        scope = bus_to_scope.get(gen.bus)
        if scope is None:
            continue
        key = (scope, gen.fuel, gen.gen_type, gen.availability_file or "")
        gen_groups[key].append(gid)

    for (scope, fuel, gen_type, avail), gids in gen_groups.items():
        if len(gids) < 2:
            continue
        instances = [state.generators[gid] for gid in gids]
        powers = [g.rated_power for g in instances]
        total_power = sum(powers)

        # Pick target bus: the bus with most total capacity
        bus_capacity: dict[str, float] = {}
        for g in instances:
            bus_capacity[g.bus] = bus_capacity.get(g.bus, 0) + g.rated_power
        target_bus = max(bus_capacity, key=bus_capacity.get)

        # Build a descriptive name
        node_idx = state.buses[target_bus].parent_node if target_bus in state.buses else 0
        node_name = ""
        for n in state.nodes:
            if n.index == node_idx:
                node_name = n.name
                break
        target_name = f"Agg. {fuel} ({node_name or f'Node {node_idx}'})"
        target_unit_key = f"agg_{fuel.lower().replace(' ', '_')}_{target_bus}"

        scope_buses = scope_to_buses.get(scope, {target_bus})
        merged_ids = set(gids)

        # Find redundant infrastructure for levels > bus
        buses_rm: list[str] = []
        lines_rm: list[str] = []
        trafos_rm: list[int] = []
        if level != "bus":
            buses_rm, lines_rm, trafos_rm = _find_redundant_infra(
                state, scope_buses, target_bus, merged_ids, set(),
            )

        desc_parts = [
            f"Merge {len(gids)} {fuel} {gen_type} generators → 1",
            f"({total_power:.1f} MW total)",
        ]
        if buses_rm:
            desc_parts.append(f", remove {len(buses_rm)} empty bus(es)")
        if lines_rm:
            desc_parts.append(f", {len(lines_rm)} line(s)")
        if trafos_rm:
            desc_parts.append(f", {len(trafos_rm)} transformer(s)")

        suggestions.append(InfrastructureSuggestion(
            level=level,
            equipment_type="generator",
            instance_ids=gids,
            target_bus=target_bus,
            target_unit_key=target_unit_key,
            target_name=target_name,
            fuel=fuel,
            gen_type=gen_type,
            total_rated_power=total_power,
            total_capacity=0.0,
            reduction=len(gids) - 1,
            buses_to_remove=buses_rm,
            lines_to_remove=lines_rm,
            transformers_to_remove=trafos_rm,
            description=" ".join(desc_parts),
        ))

    # -- Group batteries by (scope, fuel) --
    bat_groups: dict[tuple, list[str]] = defaultdict(list)
    for bid, bat in state.batteries.items():
        scope = bus_to_scope.get(bat.bus)
        if scope is None:
            continue
        key = (scope, bat.fuel, bat.availability_file or "")
        bat_groups[key].append(bid)

    for (scope, fuel, avail), bids in bat_groups.items():
        if len(bids) < 2:
            continue
        instances = [state.batteries[bid] for bid in bids]
        powers = [b.rated_power for b in instances]
        capacities = [b.capacity for b in instances]
        total_power = sum(powers)
        total_capacity = sum(capacities)

        bus_capacity: dict[str, float] = {}
        for b in instances:
            bus_capacity[b.bus] = bus_capacity.get(b.bus, 0) + b.rated_power
        target_bus = max(bus_capacity, key=bus_capacity.get)

        node_idx = state.buses[target_bus].parent_node if target_bus in state.buses else 0
        node_name = ""
        for n in state.nodes:
            if n.index == node_idx:
                node_name = n.name
                break
        target_name = f"Agg. Battery ({node_name or f'Node {node_idx}'})"
        target_unit_key = f"agg_bat_{fuel.lower().replace(' ', '_')}_{target_bus}"

        scope_buses = scope_to_buses.get(scope, {target_bus})
        merged_ids = set(bids)

        buses_rm: list[str] = []
        lines_rm: list[str] = []
        trafos_rm: list[int] = []
        if level != "bus":
            buses_rm, lines_rm, trafos_rm = _find_redundant_infra(
                state, scope_buses, target_bus, set(), merged_ids,
            )

        desc_parts = [
            f"Merge {len(bids)} batteries → 1",
            f"({total_power:.1f} MW / {total_capacity:.1f} MWh)",
        ]
        if buses_rm:
            desc_parts.append(f", remove {len(buses_rm)} empty bus(es)")
        if lines_rm:
            desc_parts.append(f", {len(lines_rm)} line(s)")
        if trafos_rm:
            desc_parts.append(f", {len(trafos_rm)} transformer(s)")

        suggestions.append(InfrastructureSuggestion(
            level=level,
            equipment_type="battery",
            instance_ids=bids,
            target_bus=target_bus,
            target_unit_key=target_unit_key,
            target_name=target_name,
            fuel=fuel,
            gen_type="",
            total_rated_power=total_power,
            total_capacity=total_capacity,
            reduction=len(bids) - 1,
            buses_to_remove=buses_rm,
            lines_to_remove=lines_rm,
            transformers_to_remove=trafos_rm,
            description=" ".join(desc_parts),
        ))

    return suggestions


def apply_infrastructure_simplification(
    model: "GuiModel",
    suggestion: InfrastructureSuggestion,
) -> str:
    """Merge instances into one aggregate unit.

    Returns the instance_id of the newly created aggregate.
    """
    state = model.state

    if suggestion.equipment_type == "generator":
        instances = [state.generators[gid] for gid in suggestion.instance_ids
                     if gid in state.generators]
        if len(instances) < 2:
            # 0 = all consumed by prior operations; 1 = nothing to merge
            return ""

        powers = [g.rated_power for g in instances]
        total_power = sum(powers)

        # Position: use target bus coords, fallback to capacity-weighted centroid
        target_bus_obj = state.buses.get(suggestion.target_bus)
        if target_bus_obj and (target_bus_obj.latitude or target_bus_obj.longitude):
            agg_lat = target_bus_obj.latitude
            agg_lng = target_bus_obj.longitude
        else:
            agg_lat = _weighted_avg(
                [g.latitude for g in instances], powers,
            )
            agg_lng = _weighted_avg(
                [g.longitude for g in instances], powers,
            )

        # Capacity-weighted averages
        agg_params = {
            "rated_power": total_power,
            "min_power": sum(g.min_power for g in instances),
            "eff_at_rated": _weighted_avg([g.eff_at_rated for g in instances], powers),
            "eff_at_min": _weighted_avg([g.eff_at_min for g in instances], powers),
            "fuel_cost": _weighted_avg([g.fuel_cost for g in instances], powers),
            "fixed_cost": _weighted_avg([g.fixed_cost for g in instances], powers),
            "maintenance_cost": _weighted_avg([g.maintenance_cost for g in instances], powers),
            "degradation_rate": _weighted_avg([g.degradation_rate for g in instances], powers),
            "life_time": min(g.life_time for g in instances),
            "initial_age": max(g.initial_age for g in instances),
            "ramp_up": sum(g.ramp_up for g in instances),
            "ramp_down": sum(g.ramp_down for g in instances),
            "inertia": sum(g.inertia for g in instances),
            "start_up_cost": sum(g.start_up_cost for g in instances),
            "decommissioning_cost": sum(g.decommissioning_cost for g in instances),
            "availability_file": instances[0].availability_file,
            "frequency_hz": instances[0].frequency_hz,
            "current_type": instances[0].current_type,
            "latitude": agg_lat,
            "longitude": agg_lng,
        }

        # Remove sources (directly from state to avoid signal noise)
        for gid in suggestion.instance_ids:
            state.generators.pop(gid, None)

        # Remove redundant infrastructure
        _apply_infra_removal(model, suggestion)

        # Verify target bus still exists; fallback to instance's bus
        target_bus = suggestion.target_bus
        if target_bus not in state.buses:
            target_bus = instances[0].bus if instances[0].bus in state.buses else None
        if target_bus is None or target_bus not in state.buses:
            logger.error(
                "No valid bus for aggregate (original=%s).",
                suggestion.target_bus,
            )
            return ""

        # Create aggregate
        new_id = model.add_generator_instance(
            unit_key=suggestion.target_unit_key,
            name=suggestion.target_name,
            gen_type=suggestion.gen_type,
            fuel=suggestion.fuel,
            bus=target_bus,
            **agg_params,
        )
        logger.info(
            "Created aggregate generator %s (%s, %.1f MW) on %s",
            new_id, suggestion.fuel, total_power, target_bus,
        )
        return new_id

    else:  # battery
        instances = [state.batteries[bid] for bid in suggestion.instance_ids
                     if bid in state.batteries]
        if len(instances) < 2:
            return ""

        powers = [b.rated_power for b in instances]
        capacities = [b.capacity for b in instances]
        total_power = sum(powers)
        total_cap = sum(capacities)

        # Position: use target bus coords, fallback to capacity-weighted centroid
        target_bus_obj = state.buses.get(suggestion.target_bus)
        if target_bus_obj and (target_bus_obj.latitude or target_bus_obj.longitude):
            agg_lat = target_bus_obj.latitude
            agg_lng = target_bus_obj.longitude
        else:
            agg_lat = _weighted_avg(
                [b.latitude for b in instances], powers,
            )
            agg_lng = _weighted_avg(
                [b.longitude for b in instances], powers,
            )

        agg_params = {
            "rated_power": total_power,
            "capacity": total_cap,
            "MaxChargePower": sum(b.MaxChargePower for b in instances),
            "MaxDischargePower": sum(b.MaxDischargePower for b in instances),
            "efficiency_charge": _weighted_avg(
                [b.efficiency_charge for b in instances], capacities,
            ),
            "efficiency_discharge": _weighted_avg(
                [b.efficiency_discharge for b in instances], capacities,
            ),
            "soc_initial": _weighted_avg(
                [b.soc_initial for b in instances], capacities,
            ),
            "max_DoD": min(b.max_DoD for b in instances),
            "degradation_rate": _weighted_avg(
                [b.degradation_rate for b in instances], capacities,
            ),
            "life_time": min(b.life_time for b in instances),
            "initial_age": max(b.initial_age for b in instances),
            "fuel_cost": _weighted_avg([b.fuel_cost for b in instances], powers),
            "fixed_cost": _weighted_avg([b.fixed_cost for b in instances], powers),
            "maintenance_cost": _weighted_avg(
                [b.maintenance_cost for b in instances], powers,
            ),
            "availability_file": instances[0].availability_file,
            "current_type": instances[0].current_type,
            "latitude": agg_lat,
            "longitude": agg_lng,
        }

        # Remove sources (directly from state to avoid signal noise)
        for bid in suggestion.instance_ids:
            state.batteries.pop(bid, None)

        _apply_infra_removal(model, suggestion)

        # Verify target bus still exists; fallback to instance's bus
        target_bus = suggestion.target_bus
        if target_bus not in state.buses:
            target_bus = instances[0].bus if instances[0].bus in state.buses else None
        if target_bus is None or target_bus not in state.buses:
            logger.error(
                "No valid bus for aggregate battery (original=%s).",
                suggestion.target_bus,
            )
            return ""

        new_id = model.add_battery_instance(
            unit_key=suggestion.target_unit_key,
            name=suggestion.target_name,
            bus=target_bus,
            **agg_params,
        )
        logger.info(
            "Created aggregate battery %s (%.1f MW / %.1f MWh) on %s",
            new_id, total_power, total_cap, target_bus,
        )
        return new_id


def _apply_infra_removal(
    model: "GuiModel",
    suggestion: InfrastructureSuggestion,
) -> None:
    """Remove redundant lines, transformers, and buses from a suggestion.

    Uses **non-cascading** removal for buses: only deletes the bus entry
    itself.  Equipment, lines, and transformers are handled explicitly by
    the caller (generators/batteries already removed, lines/transformers
    removed here by their own lists).  Using ``model.remove_bus()`` would
    cascade and delete equipment on OTHER buses that share connections,
    breaking subsequent suggestions.
    """
    state = model.state

    # Lines — filter from list instead of model.remove_line() which
    # emits individual signals.
    if suggestion.lines_to_remove:
        rm_set = set(suggestion.lines_to_remove)
        state.transmission_lines = [
            ln for ln in state.transmission_lines
            if ln.line_id not in rm_set
        ]

    # Transformers (remove by index, highest first to avoid shifts)
    for idx in sorted(suggestion.transformers_to_remove, reverse=True):
        if idx < len(state.transformers):
            del state.transformers[idx]

    # Buses — non-cascading: only delete the bus entry.
    # Equipment has already been removed; associated lines/transformers
    # were handled above.
    for bid in suggestion.buses_to_remove:
        state.buses.pop(bid, None)


# ── Topology simplification ─────────────────────────────────────


def _is_wire_line(ln) -> bool:
    """Check if a line is purely visual wiring (decoration-only).

    The authoritative marker is the ``decorative`` flag set by
    :func:`_rebuild_visual_wire_lines`. We also keep the legacy
    "endpoint-to-equipment + zero capacity" detection so YAMLs saved
    before the flag existed still load correctly.
    """
    if getattr(ln, "decorative", False):
        return True
    wire_types = {"generator", "battery", "electrolyzer",
                  "transformer", "acdc_converter", "freq_converter"}
    has_eq_endpoint = (
        (ln.from_endpoint and ln.from_endpoint.element_type in wire_types)
        or (ln.to_endpoint and ln.to_endpoint.element_type in wire_types)
    )
    zero_capacity = (getattr(ln, "capacity_mw", 0) or 0) <= 0
    return has_eq_endpoint and zero_capacity


def _logical_bus_adjacency(
    state: GuiSystemState,
) -> dict[str, set[str]]:
    """Build bus adjacency from the LOGICAL electrical network only.

    The logical network is what the solver sees:
    - Vertices: buses
    - Edges: real transmission lines (not wire lines) + transformers + converters

    Wire lines (lines with ``EndpointRef`` to equipment/transformers)
    are decoration, NOT part of the electrical graph.
    """
    adj: dict[str, set[str]] = {bid: set() for bid in state.buses}
    for ln in state.transmission_lines:
        if _is_wire_line(ln):
            continue
        if ln.from_bus == ln.to_bus:
            continue
        if ln.from_bus in adj and ln.to_bus in adj:
            adj[ln.from_bus].add(ln.to_bus)
            adj[ln.to_bus].add(ln.from_bus)
    for tr in state.transformers:
        if tr.from_bus != tr.to_bus and tr.from_bus in adj and tr.to_bus in adj:
            adj[tr.from_bus].add(tr.to_bus)
            adj[tr.to_bus].add(tr.from_bus)
    for conv in state.acdc_converters:
        if conv.from_bus != conv.to_bus and conv.from_bus in adj and conv.to_bus in adj:
            adj[conv.from_bus].add(conv.to_bus)
            adj[conv.to_bus].add(conv.from_bus)
    if hasattr(state, "freq_converters"):
        for fc in state.freq_converters:
            if fc.from_bus != fc.to_bus and fc.from_bus in adj and fc.to_bus in adj:
                adj[fc.from_bus].add(fc.to_bus)
                adj[fc.to_bus].add(fc.from_bus)
    return adj


def _bus_has_equipment(state: GuiSystemState, bus_id: str) -> bool:
    """Check if a bus has any generators, batteries, or electrolyzers."""
    for gen in state.generators.values():
        if gen.bus == bus_id:
            return True
    for bat in state.batteries.values():
        if bat.bus == bus_id:
            return True
    if hasattr(state, "electrolyzers"):
        for elec in state.electrolyzers.values():
            if getattr(elec, "bus", None) == bus_id:
                return True
    return False


def _bus_is_slack(state: GuiSystemState, bus_id: str) -> bool:
    """Check if a bus is a slack bus."""
    bus = state.buses.get(bus_id)
    return bus is not None and bus.bus_type == "slack"


def _node_has_demand(state: GuiSystemState, node_index: int) -> bool:
    """Check if a node has any demand (peak or total)."""
    for node in state.nodes:
        if node.index == node_index:
            return node.demand.peak_mw > 0 or node.demand.total_mwh > 0
    return False


def _node_has_demand_fraction(state: GuiSystemState, node_index: int) -> bool:
    """Check if ANY bus in the node has non-zero demand_fraction.

    This is more reliable than checking GuiNodeDemand.peak_mw because
    demand CSVs may not be loaded yet when simplification runs.
    """
    for bus in state.buses.values():
        if bus.parent_node == node_index and bus.demand_fraction > 0:
            return True
    return False


def _node_has_equipment(state: GuiSystemState, node_index: int) -> bool:
    """Check if ANY bus in the node has equipment."""
    node_buses = {
        bid for bid, bus in state.buses.items()
        if bus.parent_node == node_index
    }
    for gen in state.generators.values():
        if gen.bus in node_buses:
            return True
    for bat in state.batteries.values():
        if bat.bus in node_buses:
            return True
    if hasattr(state, "electrolyzers"):
        for elec in state.electrolyzers.values():
            if getattr(elec, "bus", None) in node_buses:
                return True
    return False


def _bus_is_active(state: GuiSystemState, bus_id: str) -> bool:
    """Check if a bus directly carries demand or equipment."""
    if _bus_has_equipment(state, bus_id):
        return True
    if _bus_has_demand(state, bus_id):
        return True
    if _bus_is_slack(state, bus_id):
        return True
    return False


def _removal_disconnects_active(
    state: GuiSystemState,
    bus_id: str,
    adj: dict[str, set[str]],
    removed: set[str],
) -> bool:
    """Check if removing bus_id would disconnect active buses in its
    own connected component.

    Only considers active buses reachable from bus_id BEFORE removal.
    Active buses in OTHER components are irrelevant — they were already
    disconnected and removing bus_id doesn't make it worse.
    """
    # First: find which active buses are in bus_id's component
    # (BFS from bus_id INCLUDING bus_id, to find its component)
    comp: set[str] = set()
    queue = deque([bus_id])
    while queue:
        b = queue.popleft()
        if b in comp or b in removed:
            continue
        comp.add(b)
        for nb in adj.get(b, set()):
            if nb not in comp and nb not in removed:
                queue.append(nb)

    # Active buses in this component (excluding the candidate)
    active_in_comp = {
        bid for bid in comp
        if bid != bus_id and _bus_is_active(state, bid)
    }

    if len(active_in_comp) <= 1:
        return False  # 0 or 1 active bus in this component

    # BFS from any active bus in the component, WITHOUT bus_id
    start = next(iter(active_in_comp))
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        b = queue.popleft()
        if b in visited:
            continue
        visited.add(b)
        for nb in adj.get(b, set()):
            if nb != bus_id and nb not in removed and nb not in visited:
                queue.append(nb)

    unreachable = active_in_comp - visited
    return len(unreachable) > 0


def _bus_is_protected(
    state: GuiSystemState,
    bus_id: str,
    removed: set[str],
    adj: Optional[dict[str, set[str]]] = None,
) -> bool:
    """Check if a bus must NOT be removed.

    A bus is protected if:
    - It directly has equipment, demand, or is a slack bus
    - Removing it would disconnect active buses from each other
      (i.e., it is a structural bridge between demand and generation)
    - It is the last remaining bus in its node
    """
    # Direct protection: bus itself is active
    if _bus_is_active(state, bus_id):
        return True

    bus = state.buses.get(bus_id)
    if bus is None:
        return False

    # Last bus in node
    sibling_count = sum(
        1 for b in state.buses.values()
        if b.parent_node == bus.parent_node and b.bus_id not in removed
    )
    if sibling_count <= 1:
        return True

    # Structural protection: would removal disconnect active buses?
    if adj is None:
        adj = _logical_bus_adjacency(state)
    if _removal_disconnects_active(state, bus_id, adj, removed):
        return True

    return False


def _find_bridges(adj: dict[str, set[str]]) -> set[frozenset[str]]:
    """Find bridge edges whose removal would disconnect the graph.

    Returns a set of frozenset({bus_a, bus_b}) pairs.
    Uses Tarjan's bridge-finding algorithm.
    """
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    bridges: set[frozenset[str]] = set()
    timer = [0]

    def _dfs(u: str, parent: str | None):
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        for v in adj.get(u, set()):
            if v not in disc:
                _dfs(v, u)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridges.add(frozenset({u, v}))
            elif v != parent:
                low[u] = min(low[u], disc[v])

    for bus_id in adj:
        if bus_id not in disc:
            _dfs(bus_id, None)

    return bridges


def _compute_parallel_impedance(
    lines: list,
    min_x: float = 0.0001,
) -> dict:
    """Compute equivalent impedance for parallel lines.

    Returns dict with capacity_mw, reactance_pu, resistance_pu,
    susceptance_pu, num_circuits.
    """
    capacity = sum(ln.capacity_mw for ln in lines)
    num_circuits = sum(ln.num_circuits for ln in lines)

    # Parallel reactance: 1/X_eq = sum(1/X_i)
    inv_x_sum = 0.0
    for ln in lines:
        x = ln.reactance_pu if ln.reactance_pu and ln.reactance_pu > 0 else None
        if x is not None:
            inv_x_sum += 1.0 / x
    reactance = (1.0 / inv_x_sum) if inv_x_sum > 0 else None

    # Parallel resistance: 1/R_eq = sum(1/R_i)
    inv_r_sum = 0.0
    all_have_r = True
    for ln in lines:
        r = ln.resistance_pu if ln.resistance_pu and ln.resistance_pu > 0 else None
        if r is not None:
            inv_r_sum += 1.0 / r
        else:
            all_have_r = False
    resistance = (1.0 / inv_r_sum) if (all_have_r and inv_r_sum > 0) else None

    # Susceptance adds in parallel
    susceptance = None
    if any(ln.susceptance_pu is not None for ln in lines):
        susceptance = sum(ln.susceptance_pu or 0.0 for ln in lines)

    return {
        "capacity_mw": capacity,
        "reactance_pu": reactance,
        "resistance_pu": resistance,
        "susceptance_pu": susceptance,
        "num_circuits": num_circuits,
    }


def _find_parallel_lines(
    state: GuiSystemState,
    config: SimplificationConfig,
) -> list[TopologySuggestion]:
    """Find groups of true parallel transmission lines.

    Excludes equipment-chain lines (lines with EndpointRef to
    transformers, generators, batteries, etc.) as they represent
    physical wiring, not electrical parallel paths.
    """
    suggestions: list[TopologySuggestion] = []

    # Group lines by (from_bus, to_bus, current_type, frequency, voltage)
    groups: dict[tuple, list] = defaultdict(list)
    for ln in state.transmission_lines:
        if ln.from_bus == ln.to_bus:
            continue
        if _is_wire_line(ln):
            continue
        key = (
            min(ln.from_bus, ln.to_bus),
            max(ln.from_bus, ln.to_bus),
            ln.current_type,
            ln.frequency_hz,
            ln.voltage_kv,
        )
        groups[key].append(ln)

    for key, lines in groups.items():
        if len(lines) < 2:
            continue
        equiv = _compute_parallel_impedance(lines, config.min_reactance_pu)
        # Keep waypoints from the highest-capacity line for visualization
        best_line = max(lines, key=lambda ln: ln.capacity_mw)

        lines_to_remove = [ln.line_id for ln in lines]
        new_line_spec = {
            "from_bus": lines[0].from_bus,
            "to_bus": lines[0].to_bus,
            "capacity_mw": equiv["capacity_mw"],
            "reactance_pu": equiv["reactance_pu"],
            "resistance_pu": equiv["resistance_pu"],
            "susceptance_pu": equiv["susceptance_pu"],
            "num_circuits": equiv["num_circuits"],
            "voltage_kv": best_line.voltage_kv,
            "current_type": lines[0].current_type,
            "frequency_hz": lines[0].frequency_hz,
            "waypoints": best_line.waypoints,
            "from_endpoint": best_line.from_endpoint,
            "to_endpoint": best_line.to_endpoint,
            "length_km": best_line.length_km,
        }

        x_str = f", X={equiv['reactance_pu']:.4f} pu" if equiv["reactance_pu"] else ""
        suggestions.append(TopologySuggestion(
            action_type="parallel_line_merge",
            level=1,
            description=(
                f"Merge {len(lines)} parallel lines "
                f"{key[0]}↔{key[1]} → 1 "
                f"({equiv['capacity_mw']:.1f} MW{x_str})"
            ),
            lines_to_remove=lines_to_remove,
            lines_to_create=[new_line_spec],
            elements_removed=len(lines) - 1,
        ))

    return suggestions


def apply_topology_suggestion(
    model: "GuiModel",
    suggestion: TopologySuggestion,
) -> int:
    """Apply a single topology suggestion. Returns count of elements changed."""
    state = model.state
    changed = 0

    # 1. Transfer slack bus designation if needed
    if suggestion.slack_transfer:
        old_bus, new_bus = suggestion.slack_transfer
        if old_bus in state.buses:
            state.buses[old_bus].bus_type = "PQ"
        if new_bus in state.buses:
            state.buses[new_bus].bus_type = "slack"
        changed += 1

    # 2. Reassign equipment to new buses
    for equip_id, new_bus in suggestion.equipment_reassignment.items():
        if equip_id in state.generators:
            state.generators[equip_id].bus = new_bus
            changed += 1
        elif equip_id in state.batteries:
            state.batteries[equip_id].bus = new_bus
            changed += 1
        elif hasattr(state, "electrolyzers") and equip_id in state.electrolyzers:
            state.electrolyzers[equip_id].bus = new_bus
            changed += 1

    # 3. Update demand fractions
    for bus_id, new_frac in suggestion.demand_redistribution.items():
        if bus_id in state.buses:
            state.buses[bus_id].demand_fraction = new_frac

    # 4. Reterminate lines that reference merged buses.
    # Both the legacy string ID (``from_bus``/``to_bus``) and the
    # EndpointRef used by the renderer must be updated, otherwise the
    # line keeps a dangling endpoint pointing to a deleted bus and the
    # map can't resolve its coordinates → visual orphan.
    from esfex.visualization.data.gui_model import EndpointRef

    def _retarget_endpoint(ep, removed: str, survivor: str):
        if (ep is not None
                and ep.element_type == "bus"
                and ep.element_id == removed):
            ep.element_id = survivor

    for removed_bus, surviving_bus in suggestion.buses_to_merge.items():
        for ln in state.transmission_lines:
            if ln.from_bus == removed_bus:
                ln.from_bus = surviving_bus
            if ln.to_bus == removed_bus:
                ln.to_bus = surviving_bus
            _retarget_endpoint(ln.from_endpoint, removed_bus, surviving_bus)
            _retarget_endpoint(ln.to_endpoint, removed_bus, surviving_bus)
        for tr in state.transformers:
            if tr.from_bus == removed_bus:
                tr.from_bus = surviving_bus
            if tr.to_bus == removed_bus:
                tr.to_bus = surviving_bus
        for conv in state.acdc_converters:
            if conv.from_bus == removed_bus:
                conv.from_bus = surviving_bus
            if conv.to_bus == removed_bus:
                conv.to_bus = surviving_bus
        if hasattr(state, "freq_converters"):
            for fc in state.freq_converters:
                if fc.from_bus == removed_bus:
                    fc.from_bus = surviving_bus
                if fc.to_bus == removed_bus:
                    fc.to_bus = surviving_bus

    # 5. Remove lines
    if suggestion.lines_to_remove:
        rm_set = set(suggestion.lines_to_remove)
        state.transmission_lines = [
            ln for ln in state.transmission_lines
            if ln.line_id not in rm_set
        ]
        changed += len(rm_set)

    # 6. Remove transformers (by index, highest first)
    for idx in sorted(suggestion.transformers_to_remove, reverse=True):
        if idx < len(state.transformers):
            del state.transformers[idx]
            changed += 1

    # 7. Remove self-loop lines + transformers + converters created by
    # bus merging. After step 4 retargets edges from removed buses to
    # surviving ones, an edge whose two endpoints both got merged into
    # the same survivor becomes a self-loop and must die.
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if ln.from_bus != ln.to_bus
    ]
    state.transformers = [
        tr for tr in state.transformers if tr.from_bus != tr.to_bus
    ]
    state.acdc_converters = [
        c for c in state.acdc_converters if c.from_bus != c.to_bus
    ]
    if hasattr(state, "freq_converters"):
        state.freq_converters = [
            c for c in state.freq_converters if c.from_bus != c.to_bus
        ]

    # 8. Remove buses
    for bid in suggestion.buses_to_remove:
        state.buses.pop(bid, None)
        changed += 1

    # 9. Create new equivalent lines
    from esfex.visualization.data.gui_model import GeoPoint, GuiTransmissionLine

    for spec in suggestion.lines_to_create:
        next_id = max(
            (int(ln.line_id.split("_")[1]) for ln in state.transmission_lines
             if ln.line_id.startswith("line_")),
            default=-1,
        ) + 1
        new_line = GuiTransmissionLine(
            line_id=f"line_{next_id}",
            from_bus=spec["from_bus"],
            to_bus=spec["to_bus"],
            capacity_mw=spec.get("capacity_mw", 0.0),
            voltage_kv=spec.get("voltage_kv"),
            reactance_pu=spec.get("reactance_pu"),
            resistance_pu=spec.get("resistance_pu"),
            susceptance_pu=spec.get("susceptance_pu"),
            num_circuits=spec.get("num_circuits", 1),
            current_type=spec.get("current_type", "AC"),
            frequency_hz=spec.get("frequency_hz", 50.0),
            length_km=spec.get("length_km"),
            waypoints=spec.get("waypoints", []),
            from_endpoint=spec.get("from_endpoint"),
            to_endpoint=spec.get("to_endpoint"),
        )
        state.transmission_lines.append(new_line)
        changed += 1

    # 10. Defensive endpoint sweep. Any line/transformer/converter whose
    # bus-typed EndpointRef no longer resolves to an existing bus is
    # re-anchored to the current ``from_bus``/``to_bus`` (which the
    # earlier merge step has kept up to date). Without this, a chain of
    # simplifications can leave ghost endpoints that the map renderer
    # silently drops, producing the orphan-line artefact.
    _sync_endpoints_to_buses(state)

    return changed


def drop_isolated_components(
    state,
    min_buses: int = 2,
    keep_largest: bool = True,
) -> dict[str, int]:
    """Drop buses, lines, transformers, converters and equipment that
    live in tiny isolated subgraphs.

    A "component" is a connected subgraph of the bus adjacency graph
    induced by transmission lines + transformers + AC/DC + frequency
    converters (i.e., the electrical edges).

    Behaviour:
      * If ``keep_largest`` is True (default), the largest component
        is always preserved regardless of its size — that way a tiny
        island grid (e.g. an actual physical island) isn't deleted by
        accident if the whole network is small.
      * Components with fewer than ``min_buses`` buses are dropped in
        full. Equipment anchored at dropped buses is removed too.
      * ``min_buses=1`` drops only zero-edge orphans; ``2`` drops
        single-bus islands; raise to be more aggressive.

    Returns a dict with counts: ``{"buses", "lines", "transformers",
    "converters", "generators", "batteries", "electrolyzers"}``.
    """
    counts = {
        "buses": 0, "lines": 0, "transformers": 0, "converters": 0,
        "generators": 0, "batteries": 0, "electrolyzers": 0,
        "_components_total": 0, "_components_dropped": 0,
        "_largest_size": 0, "_top_sizes": [],
    }
    if not state.buses:
        return counts

    comp_of_bus = _bus_to_component_id(state)
    if not comp_of_bus:
        return counts

    # Component sizes
    sizes: dict[int, int] = defaultdict(int)
    for cid in comp_of_bus.values():
        sizes[cid] += 1
    largest = max(sizes, key=sizes.get) if sizes else None
    counts["_components_total"] = len(sizes)
    counts["_largest_size"] = sizes[largest] if largest is not None else 0
    # Top 5 component sizes for diagnostics
    counts["_top_sizes"] = sorted(sizes.values(), reverse=True)[:5]

    drop_components = {
        cid for cid, size in sizes.items()
        if size < min_buses and not (keep_largest and cid == largest)
    }
    counts["_components_dropped"] = len(drop_components)
    if not drop_components:
        return counts

    drop_buses = {
        bid for bid, cid in comp_of_bus.items()
        if cid in drop_components
    }
    if not drop_buses:
        return counts

    # Equipment first (so the bus removal helper doesn't try to
    # re-anchor them onto a sibling that's also being deleted).
    for gid in [
        g for g, gen in state.generators.items()
        if gen.bus in drop_buses
    ]:
        state.generators.pop(gid, None)
        counts["generators"] += 1
    for bid_eq in [
        b for b, bat in state.batteries.items()
        if bat.bus in drop_buses
    ]:
        state.batteries.pop(bid_eq, None)
        counts["batteries"] += 1
    if hasattr(state, "electrolyzers"):
        for eid in [
            e for e, el in state.electrolyzers.items()
            if getattr(el, "bus", None) in drop_buses
        ]:
            state.electrolyzers.pop(eid, None)
            counts["electrolyzers"] += 1

    # Lines touching the dropped subgraph
    n_lines_before = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if ln.from_bus not in drop_buses and ln.to_bus not in drop_buses
    ]
    counts["lines"] = n_lines_before - len(state.transmission_lines)

    # Transformers
    n_tr_before = len(state.transformers)
    state.transformers = [
        tr for tr in state.transformers
        if tr.from_bus not in drop_buses and tr.to_bus not in drop_buses
    ]
    counts["transformers"] = n_tr_before - len(state.transformers)

    # Converters
    n_acdc_before = len(state.acdc_converters)
    state.acdc_converters = [
        c for c in state.acdc_converters
        if c.from_bus not in drop_buses and c.to_bus not in drop_buses
    ]
    counts["converters"] = n_acdc_before - len(state.acdc_converters)
    if hasattr(state, "freq_converters"):
        n_fc_before = len(state.freq_converters)
        state.freq_converters = [
            c for c in state.freq_converters
            if c.from_bus not in drop_buses and c.to_bus not in drop_buses
        ]
        counts["converters"] += n_fc_before - len(state.freq_converters)

    # Finally, the buses themselves
    for bid in drop_buses:
        state.buses.pop(bid, None)
    counts["buses"] = len(drop_buses)

    return counts


def drop_dangling_refs(state) -> dict[str, int]:
    """Remove any element that references a bus that no longer exists.

    Hard cleanup that guarantees no element survives with a broken
    foreign key. Operates on:

    * lines:  remove if from_bus or to_bus is missing
    * transformers / acdc_converters / freq_converters: same
    * generators / batteries / electrolyzers: ``bus`` attribute must
      exist; if not, drop the equipment entirely (don't reassign —
      that's how we got phantom equipment in the wrong node before)
    * EndpointRefs of type "bus" pointing to deleted buses: drop the
      hosting line outright (one of its endpoints is unrenderable)

    Should be run as the *last* step of any structural mutation
    pipeline.
    """
    counts = {
        "lines": 0, "transformers": 0, "converters": 0,
        "generators": 0, "batteries": 0, "electrolyzers": 0,
    }
    if not state.buses:
        return counts
    valid_buses = set(state.buses)

    def _line_ok(ln) -> bool:
        if ln.from_bus not in valid_buses or ln.to_bus not in valid_buses:
            return False
        for ep in (ln.from_endpoint, ln.to_endpoint):
            if (ep is not None
                    and ep.element_type == "bus"
                    and ep.element_id not in valid_buses):
                return False
        return True

    n = len(state.transmission_lines)
    state.transmission_lines = [ln for ln in state.transmission_lines if _line_ok(ln)]
    counts["lines"] = n - len(state.transmission_lines)

    n = len(state.transformers)
    state.transformers = [
        tr for tr in state.transformers
        if tr.from_bus in valid_buses and tr.to_bus in valid_buses
    ]
    counts["transformers"] = n - len(state.transformers)

    n = len(state.acdc_converters)
    state.acdc_converters = [
        c for c in state.acdc_converters
        if c.from_bus in valid_buses and c.to_bus in valid_buses
    ]
    counts["converters"] = n - len(state.acdc_converters)
    if hasattr(state, "freq_converters"):
        n = len(state.freq_converters)
        state.freq_converters = [
            c for c in state.freq_converters
            if c.from_bus in valid_buses and c.to_bus in valid_buses
        ]
        counts["converters"] += n - len(state.freq_converters)

    for gid in [g for g, gen in state.generators.items()
                if gen.bus not in valid_buses]:
        state.generators.pop(gid, None)
        counts["generators"] += 1
    for bid in [b for b, bat in state.batteries.items()
                if bat.bus not in valid_buses]:
        state.batteries.pop(bid, None)
        counts["batteries"] += 1
    if hasattr(state, "electrolyzers"):
        for eid in [e for e, el in state.electrolyzers.items()
                    if getattr(el, "bus", None) not in valid_buses]:
            state.electrolyzers.pop(eid, None)
            counts["electrolyzers"] += 1
    return counts


def _drop_fully_orphan_buses(state) -> int:
    """Remove buses that have no electrical role at all.

    A bus is "fully orphan" if NONE of the following touch it:
      - any transmission line (real or wire) via from_bus/to_bus
        OR via from_endpoint/to_endpoint of type "bus"
      - any transformer
      - any AC/DC or frequency converter
      - any generator / battery / electrolyzer
      - the bus itself is a slack bus (preserve grid reference)
      - the bus carries demand (demand_fraction > 0)

    Such buses are visual debris from cascaded simplifications and
    can't participate in any flow. Returns count removed.
    """
    if not state.buses:
        return 0
    referenced: set[str] = set()

    for ln in state.transmission_lines:
        referenced.add(ln.from_bus)
        referenced.add(ln.to_bus)
        for ep in (ln.from_endpoint, ln.to_endpoint):
            if ep and ep.element_type == "bus":
                referenced.add(ep.element_id)
    for tr in state.transformers:
        referenced.add(tr.from_bus)
        referenced.add(tr.to_bus)
    for c in state.acdc_converters:
        referenced.add(c.from_bus)
        referenced.add(c.to_bus)
    if hasattr(state, "freq_converters"):
        for c in state.freq_converters:
            referenced.add(c.from_bus)
            referenced.add(c.to_bus)
    for g in state.generators.values():
        referenced.add(g.bus)
    for b in state.batteries.values():
        referenced.add(b.bus)
    if hasattr(state, "electrolyzers"):
        for e in state.electrolyzers.values():
            bus_attr = getattr(e, "bus", None)
            if bus_attr:
                referenced.add(bus_attr)

    to_drop = []
    for bid, bus in state.buses.items():
        if bid in referenced:
            continue
        if getattr(bus, "bus_type", "") == "slack":
            continue
        if getattr(bus, "demand_fraction", 0.0) > 0.0:
            continue
        to_drop.append(bid)

    for bid in to_drop:
        state.buses.pop(bid, None)
    return len(to_drop)


def _sync_endpoints_to_buses(state) -> int:
    """Re-anchor stale bus endpoints on lines/transformers/converters.

    When ``from_endpoint``/``to_endpoint`` reference a bus that is no
    longer present, copy the (already-updated) ``from_bus``/``to_bus``
    string ID into the EndpointRef. Returns the number of refs fixed.
    Safe to call after any structural mutation.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    fixed = 0
    valid_buses = set(state.buses.keys())

    def _fix(ep, fallback_bus: str):
        nonlocal fixed
        if ep is None:
            return None
        if ep.element_type != "bus":
            return ep
        if ep.element_id in valid_buses:
            return ep
        if fallback_bus in valid_buses:
            ep.element_id = fallback_bus
            fixed += 1
        return ep

    for ln in state.transmission_lines:
        ln.from_endpoint = _fix(ln.from_endpoint, ln.from_bus) \
            or (EndpointRef("bus", ln.from_bus)
                if ln.from_bus in valid_buses else None)
        ln.to_endpoint = _fix(ln.to_endpoint, ln.to_bus) \
            or (EndpointRef("bus", ln.to_bus)
                if ln.to_bus in valid_buses else None)
    return fixed


def _bus_degree(
    adj: dict[str, set[str]],
    bus_id: str,
) -> int:
    """Return the degree of a bus in the adjacency graph."""
    return len(adj.get(bus_id, set()))


def _edges_for_bus(
    state: GuiSystemState,
    bus_id: str,
) -> list[tuple[str, str, object]]:
    """Return all edges (lines + transformers) touching a bus.

    Returns list of (edge_type, edge_id_or_index, edge_object).
    """
    edges: list[tuple[str, str, object]] = []
    for ln in state.transmission_lines:
        if ln.from_bus == bus_id or ln.to_bus == bus_id:
            edges.append(("line", ln.line_id, ln))
    for idx, tr in enumerate(state.transformers):
        if tr.from_bus == bus_id or tr.to_bus == bus_id:
            edges.append(("transformer", str(idx), tr))
    return edges


def _other_bus(edge_obj, bus_id: str) -> str:
    """Return the bus on the other end of an edge."""
    if edge_obj.from_bus == bus_id:
        return edge_obj.to_bus
    return edge_obj.from_bus


def _find_radial_buses(
    state: GuiSystemState,
) -> list[TopologySuggestion]:
    """Find degree-1 buses with no equipment/demand that can be pruned.

    Iterates (onion-peeling) until no more candidates exist.

    Safeguards:
    - Never removes a bus that has equipment, demand, or is slack
    - Never removes a bus whose node has demand
    - Never removes the last bus in a node
    - Never removes a bus whose only edge is a bridge (would disconnect graph)
    """
    suggestions: list[TopologySuggestion] = []

    # Work on a mutable copy of adjacency
    adj = _logical_bus_adjacency(state)

    # Track buses already marked for removal
    removed: set[str] = set()

    changed = True
    while changed:
        changed = False
        # Recompute bridges after each round (topology changes)
        bridges = _find_bridges(adj)

        for bus_id in list(adj.keys()):
            if bus_id in removed:
                continue
            if _bus_degree(adj, bus_id) != 1:
                continue
            if _bus_is_protected(state, bus_id, removed, adj):
                continue

            # Check that the single edge is not a bridge connecting
            # to a component with protected buses
            neighbor = next(iter(adj[bus_id]))
            edge_key = frozenset({bus_id, neighbor})
            if edge_key in bridges:
                # This edge is a bridge — only safe to remove if the
                # bus side has no protected descendants (but degree-1
                # means bus IS the entire side, so it's safe if we
                # already checked _bus_is_protected above)
                pass

            # Find the single connecting edge object
            edges = _edges_for_bus(state, bus_id)
            removed_lines = {lid for s in suggestions for lid in s.lines_to_remove}
            live_edges = [
                e for e in edges
                if not (e[0] == "line" and e[1] in removed_lines)
            ]
            if not live_edges:
                continue

            edge_type, edge_id, edge_obj = live_edges[0]

            lines_rm = [edge_id] if edge_type == "line" else []
            trafos_rm = [int(edge_id)] if edge_type == "transformer" else []

            suggestions.append(TopologySuggestion(
                action_type="radial_prune",
                level=2,
                description=(
                    f"Prune radial bus {bus_id} "
                    f"(connected to {neighbor} via {edge_type})"
                ),
                buses_to_remove=[bus_id],
                lines_to_remove=lines_rm,
                transformers_to_remove=trafos_rm,
                elements_removed=2,  # bus + edge
            ))

            # Update adjacency for onion-peeling
            removed.add(bus_id)
            if neighbor in adj:
                adj[neighbor].discard(bus_id)
            adj.pop(bus_id, None)
            changed = True

    return suggestions


def _find_series_buses(
    state: GuiSystemState,
) -> list[TopologySuggestion]:
    """Find degree-2 pass-through buses for Kron reduction.

    A bus qualifies if:
    - Exactly 2 neighbors (degree 2) via transmission lines only
    - Not protected (no equipment, no demand, not slack, not last in node,
      node has no demand)
    - Both connecting edges are transmission lines (not transformers/converters)

    The two lines are merged into one equivalent: Z_eq = Z_1 + Z_2,
    capacity_eq = min(cap_1, cap_2).
    """
    suggestions: list[TopologySuggestion] = []
    adj = _logical_bus_adjacency(state)
    removed: set[str] = set()

    changed = True
    while changed:
        changed = False
        for bus_id in list(adj.keys()):
            if bus_id in removed:
                continue
            if _bus_degree(adj, bus_id) != 2:
                continue
            if _bus_is_protected(state, bus_id, removed, adj):
                continue

            # Must have exactly 2 transmission lines (not transformers/converters)
            line_edges = [
                ln for ln in state.transmission_lines
                if (ln.from_bus == bus_id or ln.to_bus == bus_id)
                and ln.line_id not in {lid for s in suggestions for lid in s.lines_to_remove}
            ]
            if len(line_edges) != 2:
                continue

            ln_a, ln_b = line_edges
            # Must be same current_type and frequency
            if ln_a.current_type != ln_b.current_type:
                continue
            if ln_a.frequency_hz != ln_b.frequency_hz:
                continue

            bus_a = _other_bus(ln_a, bus_id)
            bus_b = _other_bus(ln_b, bus_id)

            # Don't create self-loop
            if bus_a == bus_b:
                continue

            # Series impedance: Z_eq = Z_1 + Z_2
            x_a = ln_a.reactance_pu or 0.0
            x_b = ln_b.reactance_pu or 0.0
            r_a = ln_a.resistance_pu or 0.0
            r_b = ln_b.resistance_pu or 0.0
            b_a = ln_a.susceptance_pu or 0.0
            b_b = ln_b.susceptance_pu or 0.0

            eq_x = (x_a + x_b) if (x_a > 0 or x_b > 0) else None
            eq_r = (r_a + r_b) if (r_a > 0 or r_b > 0) else None
            # Susceptance in series: 1/B_eq = 1/B_a + 1/B_b (if both > 0)
            if b_a > 0 and b_b > 0:
                eq_b = 1.0 / (1.0 / b_a + 1.0 / b_b)
            else:
                eq_b = None
            eq_cap = min(ln_a.capacity_mw, ln_b.capacity_mw)

            # Length: sum if available
            len_a = ln_a.length_km or 0.0
            len_b = ln_b.length_km or 0.0
            eq_len = (len_a + len_b) if (len_a > 0 or len_b > 0) else None

            # Keep waypoints: A's endpoint → through bus → B's endpoint
            best_line = ln_a if ln_a.capacity_mw >= ln_b.capacity_mw else ln_b

            new_line_spec = {
                "from_bus": bus_a,
                "to_bus": bus_b,
                "capacity_mw": eq_cap,
                "reactance_pu": eq_x,
                "resistance_pu": eq_r,
                "susceptance_pu": eq_b,
                "num_circuits": min(ln_a.num_circuits, ln_b.num_circuits),
                "voltage_kv": best_line.voltage_kv,
                "current_type": ln_a.current_type,
                "frequency_hz": ln_a.frequency_hz,
                "length_km": eq_len,
                "waypoints": best_line.waypoints,
                "from_endpoint": None,
                "to_endpoint": None,
            }

            x_str = f", Z={eq_x:.4f} pu" if eq_x else ""
            suggestions.append(TopologySuggestion(
                action_type="series_eliminate",
                level=2,
                description=(
                    f"Eliminate pass-through bus {bus_id} "
                    f"({bus_a}↔{bus_b}, {eq_cap:.1f} MW{x_str})"
                ),
                buses_to_remove=[bus_id],
                lines_to_remove=[ln_a.line_id, ln_b.line_id],
                lines_to_create=[new_line_spec],
                elements_removed=2,  # bus + net 1 line (2 removed, 1 created)
            ))

            # Update adjacency for onion-peeling
            removed.add(bus_id)
            if bus_a in adj:
                adj[bus_a].discard(bus_id)
                adj[bus_a].add(bus_b)
            if bus_b in adj:
                adj[bus_b].discard(bus_id)
                adj[bus_b].add(bus_a)
            adj.pop(bus_id, None)
            changed = True

    return suggestions


def _merge_bus_into_suggestion(
    state: GuiSystemState,
    removed_bus_id: str,
    surviving_bus_id: str,
    trafo_idx: Optional[int],
    trafo_impedance_pu: float,
) -> TopologySuggestion:
    """Build a TopologySuggestion that merges removed_bus into surviving_bus.

    Handles:
    - Equipment reassignment
    - Demand fraction redistribution
    - Transformer removal
    - Slack bus transfer
    - Impedance adjustment for reterminated lines (adds trafo impedance)
    """
    equipment_reassignment: dict[str, str] = {}
    for gid, gen in state.generators.items():
        if gen.bus == removed_bus_id:
            equipment_reassignment[gid] = surviving_bus_id
    for bid, bat in state.batteries.items():
        if bat.bus == removed_bus_id:
            equipment_reassignment[bid] = surviving_bus_id
    if hasattr(state, "electrolyzers"):
        for eid, elec in state.electrolyzers.items():
            if getattr(elec, "bus", None) == removed_bus_id:
                equipment_reassignment[eid] = surviving_bus_id

    # Demand redistribution. If the surviving bus is a connection bus, the
    # transferred demand has nowhere to go inside this bus — caller must
    # promote it to load/mixed first or the demand is lost. We still record
    # the intent so the caller can react.
    demand_redistribution: dict[str, float] = {}
    removed_bus = state.buses.get(removed_bus_id)
    surviving_bus = state.buses.get(surviving_bus_id)
    if removed_bus and surviving_bus:
        if surviving_bus.role == "connection" and removed_bus.demand_fraction > 0:
            # Auto-promote: a connection bus inheriting load becomes mixed if
            # it has equipment, otherwise load.
            has_equipment = (
                any(g.bus == surviving_bus_id for g in state.generators.values())
                or any(b.bus == surviving_bus_id for b in state.batteries.values())
                or any(getattr(e, "bus", None) == surviving_bus_id
                       for e in getattr(state, "electrolyzers", {}).values())
            )
            surviving_bus.role = "mixed" if has_equipment else "load"
        new_frac = surviving_bus.demand_fraction + removed_bus.demand_fraction
        demand_redistribution[surviving_bus_id] = new_frac

    # Slack transfer
    slack_transfer = None
    if _bus_is_slack(state, removed_bus_id):
        slack_transfer = (removed_bus_id, surviving_bus_id)

    # Lines connecting removed_bus to external buses (not surviving_bus)
    # get their impedance adjusted by adding transformer impedance
    lines_to_create: list[dict] = []
    lines_to_remove: list[str] = []
    for ln in state.transmission_lines:
        touches_removed = (ln.from_bus == removed_bus_id or ln.to_bus == removed_bus_id)
        if not touches_removed:
            continue
        other = _other_bus(ln, removed_bus_id)
        if other == surviving_bus_id:
            # Internal line between the two merging buses — remove
            lines_to_remove.append(ln.line_id)
        else:
            # External line — reterminate and adjust impedance
            lines_to_remove.append(ln.line_id)
            new_x = (ln.reactance_pu or 0.0) + trafo_impedance_pu
            new_r = ln.resistance_pu
            lines_to_create.append({
                "from_bus": surviving_bus_id if ln.from_bus == removed_bus_id else ln.from_bus,
                "to_bus": surviving_bus_id if ln.to_bus == removed_bus_id else ln.to_bus,
                "capacity_mw": ln.capacity_mw,
                "reactance_pu": new_x if new_x > 0 else ln.reactance_pu,
                "resistance_pu": new_r,
                "susceptance_pu": ln.susceptance_pu,
                "num_circuits": ln.num_circuits,
                "voltage_kv": surviving_bus.voltage_kv if surviving_bus else ln.voltage_kv,
                "current_type": ln.current_type,
                "frequency_hz": ln.frequency_hz,
                "length_km": ln.length_km,
                "waypoints": ln.waypoints,
                "from_endpoint": ln.from_endpoint,
                "to_endpoint": ln.to_endpoint,
            })

    trafos_rm = [trafo_idx] if trafo_idx is not None else []

    v_rm = removed_bus.voltage_kv if removed_bus else "?"
    v_surv = surviving_bus.voltage_kv if surviving_bus else "?"
    n_equip = len(equipment_reassignment)
    desc_parts = [
        f"Collapse bus {removed_bus_id} ({v_rm} kV) "
        f"into {surviving_bus_id} ({v_surv} kV)",
    ]
    if n_equip:
        desc_parts.append(f", reassign {n_equip} unit(s)")
    if lines_to_create:
        desc_parts.append(f", reterminate {len(lines_to_create)} line(s)")

    return TopologySuggestion(
        action_type="voltage_collapse",
        level=3,
        description="".join(desc_parts),
        buses_to_remove=[removed_bus_id],
        buses_to_merge={removed_bus_id: surviving_bus_id},
        lines_to_remove=lines_to_remove,
        lines_to_create=lines_to_create,
        transformers_to_remove=trafos_rm,
        demand_redistribution=demand_redistribution,
        equipment_reassignment=equipment_reassignment,
        slack_transfer=slack_transfer,
        elements_removed=1 + len(trafos_rm) + len(lines_to_remove) - len(lines_to_create),
    )


def _find_voltage_collapse(
    state: GuiSystemState,
    config: Optional["SimplificationConfig"] = None,
) -> list[TopologySuggestion]:
    """Find intra-node voltage level collapses.

    For each node with multiple voltage levels, collapse lower-voltage
    buses into the highest-voltage bus by processing from lowest upward.
    Requires a transformer connecting the two buses within the node.
    Skips merges whose two buses are farther apart than
    ``config.max_merge_distance_km`` (avoids cross-region distortion).
    """
    suggestions: list[TopologySuggestion] = []
    cfg = config or SimplificationConfig()

    # Group buses by node
    node_buses: dict[int, list[str]] = defaultdict(list)
    for bid, bus in state.buses.items():
        node_buses[bus.parent_node].append(bid)

    for node_idx, bus_ids in node_buses.items():
        if len(bus_ids) < 2:
            continue

        # Sort by voltage descending → surviving = highest voltage
        sorted_buses = sorted(
            bus_ids,
            key=lambda b: state.buses[b].voltage_kv,
            reverse=True,
        )
        surviving_bus_id = sorted_buses[0]
        surviving_bus = state.buses[surviving_bus_id]

        # Process from lowest voltage upward
        for removed_bus_id in reversed(sorted_buses[1:]):
            removed_bus = state.buses[removed_bus_id]
            dist = _haversine_km(
                surviving_bus.latitude, surviving_bus.longitude,
                removed_bus.latitude, removed_bus.longitude,
            )
            if dist > cfg.max_merge_distance_km:
                continue
            # Find a transformer connecting this bus to a higher-voltage
            # bus in the same node
            trafo_idx = None
            trafo_impedance = 0.0
            for idx, tr in enumerate(state.transformers):
                same_node = (
                    (tr.from_bus == removed_bus_id and tr.to_bus in bus_ids)
                    or (tr.to_bus == removed_bus_id and tr.from_bus in bus_ids)
                )
                if same_node:
                    trafo_idx = idx
                    trafo_impedance = tr.impedance_pu
                    break

            # Even without a transformer, we can still collapse if the
            # buses are in the same node (impedance adjustment = 0)
            suggestion = _merge_bus_into_suggestion(
                state, removed_bus_id, surviving_bus_id,
                trafo_idx, trafo_impedance,
            )
            suggestions.append(suggestion)

    return suggestions


def _find_full_node_collapse(
    state: GuiSystemState,
    config: Optional["SimplificationConfig"] = None,
) -> list[TopologySuggestion]:
    """Collapse ALL buses within each node to a single bus.

    Like voltage collapse but also merges buses at the same voltage.
    Used at Level 4. The surviving bus is the one with the most
    inter-node connections (ties broken by highest voltage). Skips
    merges whose two buses are farther apart than
    ``config.max_merge_distance_km``.
    """
    suggestions: list[TopologySuggestion] = []
    cfg = config or SimplificationConfig()

    node_buses: dict[int, list[str]] = defaultdict(list)
    for bid, bus in state.buses.items():
        node_buses[bus.parent_node].append(bid)

    for node_idx, bus_ids in node_buses.items():
        if len(bus_ids) < 2:
            continue

        # Pick surviving bus: most inter-node connections, then highest voltage
        def _score(bid: str) -> tuple[int, float]:
            inter = 0
            for ln in state.transmission_lines:
                if ln.from_bus == bid or ln.to_bus == bid:
                    other = _other_bus(ln, bid)
                    if other in state.buses:
                        other_node = state.buses[other].parent_node
                        if other_node != node_idx:
                            inter += 1
            return (inter, state.buses[bid].voltage_kv)

        sorted_buses = sorted(bus_ids, key=_score, reverse=True)
        surviving_bus_id = sorted_buses[0]
        surviving_bus = state.buses[surviving_bus_id]

        for removed_bus_id in sorted_buses[1:]:
            removed_bus = state.buses[removed_bus_id]
            dist = _haversine_km(
                surviving_bus.latitude, surviving_bus.longitude,
                removed_bus.latitude, removed_bus.longitude,
            )
            if dist > cfg.max_merge_distance_km:
                continue
            # Find transformer between them (if any)
            trafo_idx = None
            trafo_impedance = 0.0
            for idx, tr in enumerate(state.transformers):
                pair = {tr.from_bus, tr.to_bus}
                if removed_bus_id in pair and surviving_bus_id in pair:
                    trafo_idx = idx
                    trafo_impedance = tr.impedance_pu
                    break

            suggestion = _merge_bus_into_suggestion(
                state, removed_bus_id, surviving_bus_id,
                trafo_idx, trafo_impedance,
            )
            suggestion.action_type = "full_node_collapse"
            suggestion.level = 4
            suggestions.append(suggestion)

    return suggestions


def _find_small_generators(
    state: GuiSystemState,
    config: SimplificationConfig,
) -> list[TopologySuggestion]:
    """Find small generators that can be absorbed into larger same-fuel units.

    A generator is "small" if its rated_power < config.small_generator_fraction
    of the total node generation capacity. It gets absorbed into the largest
    same-fuel generator on the same bus (or node if after collapse).
    """
    suggestions: list[TopologySuggestion] = []

    # Compute total generation capacity per node
    node_gen_cap: dict[int, float] = defaultdict(float)
    for gen in state.generators.values():
        bus = state.buses.get(gen.bus)
        if bus:
            node_gen_cap[bus.parent_node] += gen.rated_power

    # Group generators by (node, fuel, gen_type)
    groups: dict[tuple, list[str]] = defaultdict(list)
    for gid, gen in state.generators.items():
        bus = state.buses.get(gen.bus)
        if bus is None:
            continue
        groups[(bus.parent_node, gen.fuel, gen.gen_type)].append(gid)

    for (node_idx, fuel, gen_type), gids in groups.items():
        if len(gids) < 2:
            continue

        threshold = node_gen_cap.get(node_idx, 0) * config.small_generator_fraction
        if threshold <= 0:
            continue

        # Sort by rated_power: largest first
        sorted_gens = sorted(
            gids,
            key=lambda g: state.generators[g].rated_power,
            reverse=True,
        )
        # The largest is the absorber; small ones get absorbed
        absorber_id = sorted_gens[0]
        absorber = state.generators[absorber_id]

        for small_id in sorted_gens[1:]:
            small = state.generators[small_id]
            if small.rated_power >= threshold:
                continue

            suggestions.append(TopologySuggestion(
                action_type="small_gen_absorb",
                level=4,
                description=(
                    f"Absorb {small.name or small_id} "
                    f"({small.rated_power:.1f} MW {fuel}) into "
                    f"{absorber.name or absorber_id} "
                    f"({absorber.rated_power:.1f} MW)"
                ),
                elements_removed=1,
                # Store IDs for the apply function to handle
                buses_to_merge={},
                equipment_reassignment={small_id: "__absorb__" + absorber_id},
            ))

    return suggestions


def _apply_small_gen_absorb(
    model: "GuiModel",
    suggestion: TopologySuggestion,
) -> int:
    """Apply a small generator absorption: merge small into absorber."""
    state = model.state
    changed = 0

    for small_id, target_ref in suggestion.equipment_reassignment.items():
        if not target_ref.startswith("__absorb__"):
            continue
        absorber_id = target_ref[len("__absorb__"):]

        small = state.generators.get(small_id)
        absorber = state.generators.get(absorber_id)
        if small is None or absorber is None:
            continue

        # Add capacity and sum key parameters
        absorber.rated_power += small.rated_power
        absorber.min_power += small.min_power
        absorber.ramp_up += small.ramp_up
        absorber.ramp_down += small.ramp_down
        absorber.inertia += small.inertia

        # Weighted average for cost/efficiency parameters
        total_p = absorber.rated_power  # already includes small
        old_p = total_p - small.rated_power
        if total_p > 0:
            for attr in ("eff_at_rated", "eff_at_min", "fuel_cost",
                         "fixed_cost", "maintenance_cost", "degradation_rate"):
                old_val = getattr(absorber, attr)
                small_val = getattr(small, attr)
                new_val = (old_val * old_p + small_val * small.rated_power) / total_p
                setattr(absorber, attr, new_val)

        state.generators.pop(small_id, None)
        changed += 1

    return changed


# ── Network repair ───────────────────────────────────────────────


def _validate_topology_audit(
    state: GuiSystemState,
) -> list[ValidationIssue]:
    """Cross-check the GUI topology against what the Julia solver sees.

    Reports issues that would cause silent divergences between the
    visual network and the dispatch model:

    * Lines / transformers / converters whose bus references can't be
      resolved → solver drops them.
    * Buses with no edges → one-bus islands; equipment on them
      contributes to system totals but cannot reach external demand.
    * Connected components without demand (or without generation) →
      mathematically present but operationally inert / surplus.
    """
    issues: list[ValidationIssue] = []
    try:
        from esfex.bridge.topology_audit import audit_gui_state
    except ImportError:
        # Bridge module not installed — topology audit is an optional
        # check, skip without noise.
        return issues
    try:
        rep = audit_gui_state(state)
    except Exception as exc:
        # The audit itself crashed (not the import). Don't let it swallow
        # the entire validator — the previous `except Exception: return []`
        # caused real topology errors (lines_dropped_unresolved,
        # orphan_generators) to silently disappear. Report the crash as
        # a top-level error so the user knows topology coverage was lost.
        import logging
        logging.getLogger(__name__).exception(
            "topology_audit raised; topology issues could not be checked",
        )
        issues.append(ValidationIssue(
            severity="error", category="Topology Audit",
            message=(
                f"Topology audit crashed ({type(exc).__name__}: {exc}); "
                "topology integrity could not be checked. See log for trace."
            ),
            element_type="system", element_id="topology_audit",
        ))
        return issues

    for lid in rep.lines_dropped_unresolved:
        issues.append(ValidationIssue(
            severity="error", category="Topology Audit",
            message=(
                f"Line '{lid}': from_bus / to_bus unresolved — the "
                "solver will silently drop this line. Run auto-fix or "
                "set the endpoints manually."
            ),
            element_type="line", element_id=lid,
        ))
    for bid in rep.orphan_buses:
        # Error (not warning): a bus with no edges is a degenerate row in
        # the KCL system. Any equipment attached to it can't reach the
        # rest of the network, which silently invalidates dispatch
        # results. Consistent with orphan_generators below (already error).
        issues.append(ValidationIssue(
            severity="error", category="Topology Audit",
            message=(
                f"Bus '{bid}' has no electrical edges (no lines, "
                "transformers, or converters). It forms a one-bus "
                "island; remove it or connect it to the grid."
            ),
            element_type="bus", element_id=bid,
        ))
    for gid in rep.orphan_generators:
        issues.append(ValidationIssue(
            severity="error", category="Topology Audit",
            message=(
                f"Generator '{gid}': bus reference points to a bus that "
                "no longer exists. Solver will drop this generator."
            ),
            element_type="generator", element_id=gid,
        ))
    for bid_eq in rep.orphan_batteries:
        issues.append(ValidationIssue(
            severity="error", category="Topology Audit",
            message=(
                f"Battery '{bid_eq}': bus reference is invalid. Solver "
                "will drop this battery."
            ),
            element_type="battery", element_id=bid_eq,
        ))
    # ── Surplus components (gen but no demand reachable) ────────
    # Conceptually: a generator bus does NOT need local demand —
    # transformers and lines are valid bridges to demand elsewhere.
    # A surplus component is flagged only because the audit could
    # not find ANY transformer / line / converter from this island
    # to a demand-bearing bus.  We surface the trapped generators
    # explicitly so the user knows what's stranded and can either
    # connect them via a transformer or remove them.
    for cid in rep.surplus_components:
        comp = rep.components.get(cid, set())
        if not comp:
            continue
        trapped_gens = [
            (gid, g) for gid, g in state.generators.items()
            if g.bus in comp
        ]
        total_mw = sum(g.rated_power for _, g in trapped_gens)
        # Severity scales with how much capacity is stranded:
        # >5 MW = error (likely a serious modeling mistake);
        # smaller = warning (could be a small distributed unit).
        sev = "error" if total_mw > 5.0 else "warning"
        gen_names = ", ".join(
            f"{g.name or gid} ({g.rated_power:.1f} MW)"
            for gid, g in trapped_gens[:3]
        )
        if len(trapped_gens) > 3:
            gen_names += f", +{len(trapped_gens) - 3} more"
        any_bus = next(iter(comp))
        issues.append(ValidationIssue(
            severity=sev, category="Topology Audit",
            message=(
                f"Isolated electrical island around bus '{any_bus}' "
                f"({len(comp)} bus(es), {total_mw:.1f} MW gen): "
                f"{gen_names}. No transformer, line or converter "
                "connects this island to any demand. Add a connection "
                "or remove the generator(s)."
            ),
            element_type="bus", element_id=any_bus,
        ))
        # Also create one issue per stranded generator so they're
        # individually surfaced and clickable in the dialog.
        for gid, g in trapped_gens:
            issues.append(ValidationIssue(
                severity=sev, category="Topology Audit",
                message=(
                    f"Generator '{g.name or gid}' ({g.rated_power:.1f} "
                    f"MW, {g.fuel}) is on an isolated bus '{g.bus}' "
                    "with no path to any demand bus."
                ),
                element_type="generator", element_id=gid,
            ))

    # ── Inert components (no gen, no demand) — informational ────
    for cid in rep.inert_components:
        comp = rep.components.get(cid, set())
        any_bus = next(iter(comp), "?")
        issues.append(ValidationIssue(
            severity="info", category="Topology Audit",
            message=(
                f"Component containing bus '{any_bus}' ({len(comp)} "
                "buses) has neither generation nor demand — it has no "
                "effect on dispatch."
            ),
            element_type="bus", element_id=any_bus,
        ))
    return issues


def repair_network(state: GuiSystemState) -> list[str]:
    """Repair structural problems in the network. Returns log of actions."""
    log: list[str] = []
    bus_ids = set(state.buses.keys())

    # 1. Remove lines referencing non-existent buses
    before = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if ln.from_bus in bus_ids and ln.to_bus in bus_ids
    ]
    removed_lines = before - len(state.transmission_lines)
    if removed_lines:
        log.append(f"Removed {removed_lines} line(s) with invalid bus references")

    # 2. Remove self-loop lines
    before = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if ln.from_bus != ln.to_bus
    ]
    removed_loops = before - len(state.transmission_lines)
    if removed_loops:
        log.append(f"Removed {removed_loops} self-loop line(s)")

    # 3. Remove transformers referencing non-existent buses
    before = len(state.transformers)
    state.transformers = [
        tr for tr in state.transformers
        if tr.from_bus in bus_ids and tr.to_bus in bus_ids
    ]
    removed_tr = before - len(state.transformers)
    if removed_tr:
        log.append(f"Removed {removed_tr} transformer(s) with invalid bus references")

    # 3b. Remove self-loop transformers / converters (post-merge artefact)
    before = len(state.transformers)
    state.transformers = [
        tr for tr in state.transformers if tr.from_bus != tr.to_bus
    ]
    n = before - len(state.transformers)
    if n:
        log.append(f"Removed {n} self-loop transformer(s)")
    before = len(state.acdc_converters)
    state.acdc_converters = [
        c for c in state.acdc_converters if c.from_bus != c.to_bus
    ]
    n = before - len(state.acdc_converters)
    if n:
        log.append(f"Removed {n} self-loop AC/DC converter(s)")
    if hasattr(state, "freq_converters"):
        before = len(state.freq_converters)
        state.freq_converters = [
            c for c in state.freq_converters if c.from_bus != c.to_bus
        ]
        n = before - len(state.freq_converters)
        if n:
            log.append(f"Removed {n} self-loop freq. converter(s)")

    # 4-6. Reassign orphaned equipment to a valid bus, preferring buses
    # on the equipment's existing ``node`` field (so renderer keeps
    # markers near the correct node), then any bus, then drop.
    def _pick_fallback_bus(node_idx: int | None) -> str | None:
        if node_idx is not None:
            for b in state.buses.values():
                if b.parent_node == node_idx:
                    return b.bus_id
        return next(iter(state.buses), None)

    for gid, gen in list(state.generators.items()):
        if gen.bus not in bus_ids:
            fallback = _pick_fallback_bus(getattr(gen, "node", None))
            if fallback:
                log.append(
                    f"Reassigned generator '{gen.name or gid}' "
                    f"from '{gen.bus}' to '{fallback}'"
                )
                gen.bus = fallback
            else:
                state.generators.pop(gid)
                log.append(f"Removed orphaned generator '{gen.name or gid}'")

    for bid, bat in list(state.batteries.items()):
        if bat.bus not in bus_ids:
            fallback = _pick_fallback_bus(getattr(bat, "node", None))
            if fallback:
                log.append(
                    f"Reassigned battery '{bat.name or bid}' "
                    f"from '{bat.bus}' to '{fallback}'"
                )
                bat.bus = fallback
            else:
                state.batteries.pop(bid)
                log.append(f"Removed orphaned battery '{bat.name or bid}'")

    if hasattr(state, "electrolyzers"):
        for eid, elec in list(state.electrolyzers.items()):
            b = getattr(elec, "bus", None)
            if b and b not in bus_ids:
                fallback = _pick_fallback_bus(getattr(elec, "node", None))
                if fallback:
                    elec.bus = fallback
                    log.append(f"Reassigned electrolyzer '{eid}' to '{fallback}'")

    # 7. Normalize demand_fraction per node — only over load/mixed buses.
    # Connection buses have df=0 by definition and don't participate.
    node_sums: dict[int, float] = defaultdict(float)
    for bus in state.buses.values():
        if bus.role in ("load", "mixed"):
            node_sums[bus.parent_node] += bus.demand_fraction
    for node_idx, total in node_sums.items():
        if total > 0 and abs(total - 1.0) > 0.01:
            for bus in state.buses.values():
                if bus.parent_node == node_idx and bus.role in ("load", "mixed"):
                    bus.demand_fraction /= total
            log.append(
                f"Renormalized demand_fraction for node {node_idx} "
                f"(was {total:.3f})"
            )

    # Heal disconnected demand within nodes
    healed = _heal_disconnected_demand(state)
    if healed:
        log.append(
            f"Healed {healed} bus(es) with disconnected demand"
        )

    # Sync stale EndpointRefs against the (now-repaired) bus dict.
    # This is the last line of defence against the "orphan line"
    # rendering bug where ``from_bus``/``to_bus`` is fixed but the
    # corresponding EndpointRef still points to a deleted bus.
    n_synced = _sync_endpoints_to_buses(state)
    if n_synced:
        log.append(f"Re-anchored {n_synced} stale endpoint(s)")

    return log


def rebuild_visual_wire_lines(state: GuiSystemState) -> int:
    """Public wrapper around the internal wire-line rebuilder."""
    return _rebuild_visual_wire_lines(state)


def auto_fix_errors(state: GuiSystemState) -> dict[str, int]:
    """Run a conservative auto-fix pass for common validation errors.

    Does NOT change topology beyond removing strictly-broken elements:

    * self-loop lines / transformers / converters (electrically
      meaningless — both ends on the same bus)
    * elements referencing buses that no longer exist
    * stale ``EndpointRef`` of type ``"bus"`` re-anchored to the
      element's current ``from_bus``/``to_bus``
    * orphan equipment (gens/bats/electrolyzers) reattached to a bus
      on the same node when possible (or removed if no candidate)
    * visual wire-lines for transformers / equipment regenerated so
      the redraw shows them connected

    Returns a dict with per-category counts so callers can present a
    summary to the user.
    """
    counts = {
        "self_loop_lines": 0,
        "self_loop_transformers": 0,
        "self_loop_converters": 0,
        "dangling_lines": 0,
        "dangling_transformers": 0,
        "dangling_converters": 0,
        "dangling_generators": 0,
        "dangling_batteries": 0,
        "dangling_electrolyzers": 0,
        "wire_lines_rebuilt": 0,
    }

    # 1. Self-loops (lines / transformers / both converter kinds)
    n = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines if ln.from_bus != ln.to_bus
    ]
    counts["self_loop_lines"] = n - len(state.transmission_lines)

    n = len(state.transformers)
    state.transformers = [
        tr for tr in state.transformers if tr.from_bus != tr.to_bus
    ]
    counts["self_loop_transformers"] = n - len(state.transformers)

    n = len(state.acdc_converters)
    state.acdc_converters = [
        c for c in state.acdc_converters if c.from_bus != c.to_bus
    ]
    counts["self_loop_converters"] = n - len(state.acdc_converters)
    if hasattr(state, "freq_converters"):
        n = len(state.freq_converters)
        state.freq_converters = [
            c for c in state.freq_converters if c.from_bus != c.to_bus
        ]
        counts["self_loop_converters"] += n - len(state.freq_converters)

    # 2. Re-anchor stale endpoint refs (uses the helper added when we
    # tracked down the simplification orphan bug)
    _sync_endpoints_to_buses(state)

    # 3. Hard sweep: anything with a broken bus FK
    refs = drop_dangling_refs(state)
    counts["dangling_lines"] = refs.get("lines", 0)
    counts["dangling_transformers"] = refs.get("transformers", 0)
    counts["dangling_converters"] = refs.get("converters", 0)
    counts["dangling_generators"] = refs.get("generators", 0)
    counts["dangling_batteries"] = refs.get("batteries", 0)
    counts["dangling_electrolyzers"] = refs.get("electrolyzers", 0)

    # 4. Rebuild visual wire-lines so trafos/equipment render as
    # connected on the map after any element removal above.
    counts["wire_lines_rebuilt"] = _rebuild_visual_wire_lines(state)

    # 5. Apply realistic per-fuel defaults to generators with
    # degenerate values (min_power == rated_power, ramp == 0, etc.)
    # — the cuba.yaml legacy case.
    try:
        from esfex.visualization.workflows.grid_mapping_quality import (
            apply_realistic_generator_defaults,
            repair_bus_roles_and_demand,
            repair_fuel_consistency,
        )
        gen_counts = apply_realistic_generator_defaults(state, force=False)
        counts["gen_min_power_fixed"] = gen_counts.get("min_power", 0)
        counts["gen_ramp_fixed"] = (
            gen_counts.get("ramp_up", 0) + gen_counts.get("ramp_down", 0)
        )
        counts["gen_commitment_fixed"] = gen_counts.get("min_up", 0)
        counts["gen_inertia_fixed"] = gen_counts.get("inertia", 0)
        counts["gen_startup_fixed"] = gen_counts.get("start_up_cost", 0)
        counts["gen_efficiency_fixed"] = gen_counts.get("eff_at_rated", 0)

        # 6. Fuel/tech catalog + supply consistency
        fc = repair_fuel_consistency(state)
        counts["fuels_added"] = fc.get("fuels_added", 0)
        counts["techs_added"] = fc.get("techs_added", 0)
        counts["fuel_entries_updated"] = fc.get("fuel_entries_updated", 0)

        # 7. Bus role + demand_fraction inference (physical correctness):
        # without this, every bus defaults to role="load" with equal share
        # of node demand, which fragments demand to HV transmission junctions
        # that have no consumers and renders the operational LP infeasible.
        br = repair_bus_roles_and_demand(state)
        counts["bus_role_changed"] = br.get("buses_role_changed", 0)
        counts["bus_demand_changed"] = br.get("buses_demand_changed", 0)
    except Exception:
        pass
    return counts


def _rebuild_visual_wire_lines(state: GuiSystemState) -> int:
    """Create visual wire lines for equipment and transformers that lost theirs.

    After simplification, some elements may have valid bus fields but no
    visual wire line (EndpointRef-based line) connecting them to their
    bus on the map. This creates minimal wire lines so the GUI renders
    the connections properly.

    Creates wire lines for:
    - Generators, batteries, electrolyzers → single self-loop line
      with EndpointRef(equip_type, id) → EndpointRef("bus", bus_id)
    - Transformers → two lines:
      from_bus side: EndpointRef("bus", from_bus) → EndpointRef("transformer", idx)
      to_bus side: EndpointRef("transformer", idx) → EndpointRef("bus", to_bus)

    Does NOT create buses, transformers, or real transmission lines.
    """
    from esfex.visualization.data.gui_model import (
        EndpointRef, GuiTransmissionLine,
    )

    # Collect elements that already have wire lines
    has_wire: set[tuple[str, str]] = set()
    for ln in state.transmission_lines:
        for ep in (ln.from_endpoint, ln.to_endpoint):
            if ep and ep.element_type in (
                "generator", "battery", "electrolyzer", "transformer",
            ):
                has_wire.add((ep.element_type, ep.element_id))

    # Helper to generate unique line IDs
    existing_ids = {
        int(ln.line_id.split("_")[1])
        for ln in state.transmission_lines
        if ln.line_id.startswith("line_")
        and ln.line_id.split("_")[1].isdigit()
    }
    next_id = [(max(existing_ids) + 1) if existing_ids else 0]

    def _make_wire(from_bus, to_bus, from_ep, to_ep, capacity_display=0.0,
                   voltage_display=None):
        ln = GuiTransmissionLine(
            line_id=f"line_{next_id[0]}",
            from_bus=from_bus, to_bus=to_bus,
            capacity_mw=float(capacity_display),
            voltage_kv=voltage_display,
            from_endpoint=from_ep, to_endpoint=to_ep,
            decorative=True,         # ← marca estructural (no es eléctrica)
        )
        state.transmission_lines.append(ln)
        next_id[0] += 1
        return 1

    created = 0

    # Equipment wire lines (generator, battery, electrolyzer).
    # Display the parent equipment's rating so the wire isn't visually
    # "0 MW" next to a 200 MW generator. The decorative flag keeps it
    # out of the solver and the saver.
    for gid, gen in state.generators.items():
        if ("generator", gid) not in has_wire and gen.bus in state.buses:
            bus_v = state.buses[gen.bus].voltage_kv or None
            created += _make_wire(
                gen.bus, gen.bus,
                EndpointRef("generator", gid),
                EndpointRef("bus", gen.bus),
                capacity_display=getattr(gen, "rated_power", 0.0),
                voltage_display=bus_v,
            )
    for bid, bat in state.batteries.items():
        if ("battery", bid) not in has_wire and bat.bus in state.buses:
            bus_v = state.buses[bat.bus].voltage_kv or None
            created += _make_wire(
                bat.bus, bat.bus,
                EndpointRef("battery", bid),
                EndpointRef("bus", bat.bus),
                capacity_display=getattr(bat, "rated_power", 0.0),
                voltage_display=bus_v,
            )
    if hasattr(state, "electrolyzers"):
        for eid, elec in state.electrolyzers.items():
            b = getattr(elec, "bus", None)
            if b and ("electrolyzer", eid) not in has_wire and b in state.buses:
                bus_v = state.buses[b].voltage_kv or None
                created += _make_wire(
                    b, b,
                    EndpointRef("electrolyzer", eid),
                    EndpointRef("bus", b),
                    capacity_display=getattr(elec, "rated_power", 0.0),
                    voltage_display=bus_v,
                )

    # Transformer wire lines: each side carries the trafo's MVA rating
    # for visual consistency, with the bus's voltage on each respective
    # side.
    for idx, tr in enumerate(state.transformers):
        tr_key = str(idx)
        if ("transformer", tr_key) in has_wire:
            continue
        if tr.from_bus not in state.buses or tr.to_bus not in state.buses:
            continue
        mva = float(getattr(tr, "rated_power_mva", 0) or 0)
        v_from = state.buses[tr.from_bus].voltage_kv or tr.from_voltage_kv
        v_to = state.buses[tr.to_bus].voltage_kv or tr.to_voltage_kv
        # from_bus → transformer (HV side)
        created += _make_wire(
            tr.from_bus, tr.from_bus,
            EndpointRef("bus", tr.from_bus),
            EndpointRef("transformer", tr_key),
            capacity_display=mva, voltage_display=v_from,
        )
        # transformer → to_bus (LV side)
        created += _make_wire(
            tr.to_bus, tr.to_bus,
            EndpointRef("transformer", tr_key),
            EndpointRef("bus", tr.to_bus),
            capacity_display=mva, voltage_display=v_to,
        )

    return created


def _heal_disconnected_demand(state: GuiSystemState) -> int:
    """Fix pre-existing data issues where a geographic node contains
    multiple electrical components that create demand problems.

    Two healing operations:

    1. **Disconnected demand**: components without generation get their
       demand moved to the node's main (generation-bearing) component.

    2. **Capacity deficit**: components whose peak demand exceeds their
       local generation capacity get the excess demand moved to a
       surplus component within the same node.

    Neither operation changes topology — only demand_fraction is
    redistributed. The total demand per geographic node is preserved.

    Returns the number of buses whose demand was adjusted.
    """
    comp_of_bus = _bus_to_component_id(state)

    # Group: node → component → list of buses
    node_comps: dict[int, dict[int, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for bid, bus in state.buses.items():
        comp = comp_of_bus.get(bid, -1)
        node_comps[bus.parent_node][comp].append(bid)

    # Map node index → node object for peak_mw lookup
    node_by_index = {n.index: n for n in state.nodes}

    healed = 0
    for node_idx, comps in node_comps.items():
        if len(comps) < 2:
            continue  # single component, nothing to heal

        # Compute capacity per component
        comp_cap: dict[int, float] = {c: 0.0 for c in comps}
        for gen in state.generators.values():
            comp = comp_of_bus.get(gen.bus, -1)
            if comp in comp_cap:
                comp_cap[comp] += gen.rated_power
        for bat in state.batteries.values():
            comp = comp_of_bus.get(bat.bus, -1)
            if comp in comp_cap:
                comp_cap[comp] += bat.rated_power

        # The "main" component = largest generation capacity
        gen_comps = {c: cap for c, cap in comp_cap.items() if cap > 0}
        if not gen_comps:
            continue  # no generation anywhere in this node
        main_comp = max(gen_comps, key=gen_comps.get)
        main_buses = comps[main_comp]
        # Prefer load/mixed buses as the demand sink. Fall back to any bus
        # if the main component has only connection buses.
        load_main_buses = [
            b for b in main_buses
            if state.buses[b].role in ("load", "mixed")
        ]
        candidate_buses = load_main_buses or main_buses
        main_target = max(
            candidate_buses,
            key=lambda b: state.buses[b].demand_fraction,
        )

        # ─── Phase 1: heal components without generation ────────────
        for comp, buses in comps.items():
            if comp == main_comp or comp_cap[comp] > 0:
                continue
            for bid in buses:
                bus = state.buses[bid]
                if bus.demand_fraction > 0:
                    state.buses[main_target].demand_fraction += bus.demand_fraction
                    bus.demand_fraction = 0.0
                    healed += 1

        # ─── Phase 2: heal components with capacity deficit ─────────
        node = node_by_index.get(node_idx)
        peak_mw = node.demand.peak_mw if node else 0.0
        if peak_mw <= 0:
            continue  # no demand time series loaded, can't compute deficit

        # Recompute demand fraction per component (after phase 1)
        comp_frac: dict[int, float] = {c: 0.0 for c in comps}
        for comp, buses in comps.items():
            for bid in buses:
                comp_frac[comp] += state.buses[bid].demand_fraction

        # For each component with capacity deficit, move excess demand
        # to the component with the largest surplus
        for comp in list(comps.keys()):
            cap = comp_cap[comp]
            if cap <= 0:
                continue  # already handled in phase 1
            local_peak = peak_mw * comp_frac[comp]
            if local_peak <= cap * 1.001:
                continue  # within capacity (with tolerance)

            # Excess demand that must be relocated
            excess_peak = local_peak - cap
            excess_frac = excess_peak / peak_mw
            if excess_frac <= 0:
                continue

            # Find a surplus target component
            surplus: dict[int, float] = {}
            for c in comps:
                if c == comp or comp_cap[c] <= 0:
                    continue
                c_peak = peak_mw * comp_frac[c]
                headroom = comp_cap[c] - c_peak
                if headroom > 0:
                    surplus[c] = headroom
            if not surplus:
                continue  # no component can absorb the excess
            target_comp = max(surplus, key=surplus.get)
            target_buses = comps[target_comp]
            target_bus_id = max(
                target_buses,
                key=lambda b: state.buses[b].demand_fraction,
            )

            # Remove excess_frac proportionally from deficit buses
            deficit_buses = [
                bid for bid in comps[comp]
                if state.buses[bid].demand_fraction > 0
            ]
            total_deficit_frac = sum(
                state.buses[bid].demand_fraction for bid in deficit_buses
            )
            if total_deficit_frac <= 0:
                continue
            scale = min(1.0, excess_frac / total_deficit_frac)
            moved = 0.0
            for bid in deficit_buses:
                remove = state.buses[bid].demand_fraction * scale
                state.buses[bid].demand_fraction -= remove
                moved += remove
            state.buses[target_bus_id].demand_fraction += moved
            comp_frac[comp] -= moved
            comp_frac[target_comp] += moved
            healed += 1

    return healed


def _consolidate_parallel_lines(state: GuiSystemState) -> int:
    """Consolidate true parallel transmission lines. Returns count merged.

    Only merges lines that are both:
    - Real transmission lines (not equipment-chain wiring)
    - Between the same bus pair at the same voltage level
    """
    groups: dict[tuple, list] = defaultdict(list)
    for ln in state.transmission_lines:
        if ln.from_bus == ln.to_bus:
            continue
        # Skip equipment-chain lines — they have EndpointRefs to specific
        # non-bus elements (transformers, equipment) and represent
        # physical wiring, not parallel electrical paths.
        if _is_wire_line(ln):
            continue
        key = (
            min(ln.from_bus, ln.to_bus),
            max(ln.from_bus, ln.to_bus),
            ln.current_type,
            ln.frequency_hz,
            ln.voltage_kv,  # Don't merge different voltage levels
        )
        groups[key].append(ln)

    merged = 0
    new_lines = []
    consumed: set[str] = set()

    for key, lines in groups.items():
        if len(lines) < 2:
            continue
        equiv = _compute_parallel_impedance(lines)
        best = max(lines, key=lambda ln: ln.capacity_mw)

        # Generate unique ID
        next_id = max(
            (int(ln.line_id.split("_")[1])
             for ln in state.transmission_lines
             if ln.line_id.startswith("line_") and ln.line_id.split("_")[1].isdigit()),
            default=-1,
        ) + 1 + merged

        from esfex.visualization.data.gui_model import GuiTransmissionLine
        new_lines.append(GuiTransmissionLine(
            line_id=f"line_{next_id}",
            from_bus=lines[0].from_bus,
            to_bus=lines[0].to_bus,
            capacity_mw=equiv["capacity_mw"],
            reactance_pu=equiv["reactance_pu"],
            resistance_pu=equiv["resistance_pu"],
            susceptance_pu=equiv["susceptance_pu"],
            num_circuits=equiv["num_circuits"],
            voltage_kv=best.voltage_kv,
            current_type=lines[0].current_type,
            frequency_hz=lines[0].frequency_hz,
            length_km=best.length_km,
            waypoints=best.waypoints,
            from_endpoint=best.from_endpoint,
            to_endpoint=best.to_endpoint,
        ))
        for ln in lines:
            consumed.add(ln.line_id)
        merged += len(lines) - 1

    if consumed:
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id not in consumed
        ] + new_lines

    return merged


def _is_safe_to_remove_bus(state: GuiSystemState, bus_id: str) -> bool:
    """Check whether removing a bus would NOT disconnect active buses."""
    if bus_id not in state.buses:
        return False
    if _bus_is_active(state, bus_id):
        return False
    # Last bus in node?
    node_idx = state.buses[bus_id].parent_node
    siblings = sum(
        1 for b in state.buses.values()
        if b.parent_node == node_idx
    )
    if siblings <= 1:
        return False
    adj = _logical_bus_adjacency(state)
    return not _removal_disconnects_active(state, bus_id, adj, set())


def _cleanup_source_wire_lines(
    state: GuiSystemState,
    equip_type: str,
    equip_id: str,
) -> int:
    """Remove wire lines (decorative) that point to a deleted equipment.

    Wire lines have an ``EndpointRef`` to specific equipment and are
    used only for spatial rendering on the map. When the equipment is
    deleted, its wire lines become dangling references and must be
    removed.

    Does NOT touch buses, transformers, or real transmission lines.

    Returns the number of wire lines removed.
    """
    before = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if not (
            (ln.from_endpoint
             and ln.from_endpoint.element_type == equip_type
             and ln.from_endpoint.element_id == equip_id)
            or
            (ln.to_endpoint
             and ln.to_endpoint.element_type == equip_type
             and ln.to_endpoint.element_id == equip_id)
        )
    ]
    return before - len(state.transmission_lines)


def _bus_to_component_id(state: GuiSystemState) -> dict[str, int]:
    """Map each bus to its connected component ID in the logical graph.

    Only fuses generators that are in the same connected component,
    so that aggregation never moves capacity across electrical islands.
    """
    adj = _logical_bus_adjacency(state)
    comp_id: dict[str, int] = {}
    current = 0
    for seed in state.buses:
        if seed in comp_id:
            continue
        # BFS
        stack = [seed]
        while stack:
            b = stack.pop()
            if b in comp_id:
                continue
            comp_id[b] = current
            for nb in adj.get(b, set()):
                if nb not in comp_id:
                    stack.append(nb)
        current += 1
    return comp_id


def _aggregate_equipment(state: GuiSystemState, model: "GuiModel") -> int:
    """Aggregate same-fuel generators/batteries by absorbing into the
    highest-capacity unit (target). The target keeps its existing
    connection chain intact. Source generators are removed and their
    wire lines cleaned up.

    Only aggregates generators within the SAME connected component,
    never moves capacity across electrical islands.

    Returns count of groups aggregated.
    """
    applied = 0
    comp_of_bus = _bus_to_component_id(state)

    # ── Generators ────────────────────────────────────────────────
    gen_groups: dict[tuple, list[str]] = defaultdict(list)
    for gid, gen in state.generators.items():
        bus = state.buses.get(gen.bus)
        if bus is None:
            continue
        comp = comp_of_bus.get(gen.bus, -1)
        key = (comp, bus.parent_node, gen.fuel, gen.gen_type,
               gen.availability_file or "")
        gen_groups[key].append(gid)

    for key, gids in gen_groups.items():
        if len(gids) < 2:
            continue

        # Target = highest rated_power (keeps its chain)
        target_id = max(gids, key=lambda g: state.generators[g].rated_power)
        target = state.generators[target_id]
        sources = [gid for gid in gids if gid != target_id]

        # Absorb sources into target
        for src_id in sources:
            src = state.generators.get(src_id)
            if src is None:
                continue
            old_power = target.rated_power
            new_power = old_power + src.rated_power

            # Weighted averages
            if new_power > 0:
                for attr in ("eff_at_rated", "eff_at_min", "fuel_cost",
                             "fixed_cost", "maintenance_cost",
                             "degradation_rate"):
                    old_val = getattr(target, attr)
                    src_val = getattr(src, attr)
                    setattr(target, attr,
                            (old_val * old_power + src_val * src.rated_power) / new_power)

            target.rated_power = new_power
            target.min_power += src.min_power
            target.ramp_up += src.ramp_up
            target.ramp_down += src.ramp_down
            target.inertia += src.inertia
            target.start_up_cost += src.start_up_cost
            target.decommissioning_cost += src.decommissioning_cost
            target.life_time = min(target.life_time, src.life_time)
            target.initial_age = max(target.initial_age, src.initial_age)

        # Remove source generators and their wire lines (visual only).
        # L1 does NOT change topology — buses/transformers/real lines stay.
        for src_id in sources:
            _cleanup_source_wire_lines(state, "generator", src_id)
            state.generators.pop(src_id, None)

        applied += 1

    # ── Batteries ─────────────────────────────────────────────────
    bat_groups: dict[tuple, list[str]] = defaultdict(list)
    for bid, bat in state.batteries.items():
        bus = state.buses.get(bat.bus)
        if bus is None:
            continue
        comp = comp_of_bus.get(bat.bus, -1)
        key = (comp, bus.parent_node, bat.fuel, bat.availability_file or "")
        bat_groups[key].append(bid)

    for key, bids in bat_groups.items():
        if len(bids) < 2:
            continue

        target_id = max(bids, key=lambda b: state.batteries[b].rated_power)
        target = state.batteries[target_id]
        sources = [bid for bid in bids if bid != target_id]

        for src_id in sources:
            src = state.batteries.get(src_id)
            if src is None:
                continue
            old_power = target.rated_power
            old_cap = target.capacity
            new_power = old_power + src.rated_power
            new_cap = old_cap + src.capacity

            if new_cap > 0:
                for attr in ("efficiency_charge", "efficiency_discharge",
                             "soc_initial", "degradation_rate"):
                    old_val = getattr(target, attr)
                    src_val = getattr(src, attr)
                    setattr(target, attr,
                            (old_val * old_cap + src_val * src.capacity) / new_cap)

            if new_power > 0:
                for attr in ("fuel_cost", "fixed_cost", "maintenance_cost"):
                    old_val = getattr(target, attr)
                    src_val = getattr(src, attr)
                    setattr(target, attr,
                            (old_val * old_power + src_val * src.rated_power) / new_power)

            target.rated_power = new_power
            target.capacity = new_cap
            target.MaxChargePower += src.MaxChargePower
            target.MaxDischargePower += src.MaxDischargePower
            target.max_DoD = min(target.max_DoD, src.max_DoD)
            target.life_time = min(target.life_time, src.life_time)
            target.initial_age = max(target.initial_age, src.initial_age)

        for src_id in sources:
            _cleanup_source_wire_lines(state, "battery", src_id)
            state.batteries.pop(src_id, None)

        applied += 1

    return applied


def _remove_orphaned_infrastructure(state: GuiSystemState) -> int:
    """Remove buses that have no equipment, no demand, and are not needed
    for connectivity between active buses. Also removes lines and
    transformers left dangling.

    This handles the general case: buses of ANY degree that became empty
    after equipment aggregation, including those connected by a mix of
    lines and transformers.

    Returns total number of elements removed.
    """
    removed_total = 0
    changed = True
    while changed:
        changed = False
        adj = _logical_bus_adjacency(state)
        buses_to_remove: list[str] = []

        for bus_id in list(state.buses.keys()):
            if _bus_is_active(state, bus_id):
                continue
            # Last bus in its node → keep
            node_idx = state.buses[bus_id].parent_node
            sibling_count = sum(
                1 for b in state.buses.values()
                if b.parent_node == node_idx
            )
            if sibling_count <= 1:
                continue
            # Would removal disconnect active buses?
            if _removal_disconnects_active(state, bus_id, adj, set()):
                continue
            buses_to_remove.append(bus_id)

        if not buses_to_remove:
            break

        rm_set = set(buses_to_remove)
        for bid in buses_to_remove:
            state.buses.pop(bid, None)

        # Remove lines and transformers that touch removed buses
        before_lines = len(state.transmission_lines)
        state.transmission_lines = [
            ln for ln in state.transmission_lines
            if ln.from_bus not in rm_set and ln.to_bus not in rm_set
        ]
        before_trafos = len(state.transformers)
        state.transformers = [
            tr for tr in state.transformers
            if tr.from_bus not in rm_set and tr.to_bus not in rm_set
        ]
        # Also clean converters
        state.acdc_converters = [
            c for c in state.acdc_converters
            if c.from_bus not in rm_set and c.to_bus not in rm_set
        ]
        if hasattr(state, "freq_converters"):
            state.freq_converters = [
                c for c in state.freq_converters
                if c.from_bus not in rm_set and c.to_bus not in rm_set
            ]

        n_removed = (
            len(buses_to_remove)
            + (before_lines - len(state.transmission_lines))
            + (before_trafos - len(state.transformers))
        )
        removed_total += n_removed
        changed = True

    # Final: remove self-loops and lines to non-existent buses
    bus_ids = set(state.buses.keys())
    before = len(state.transmission_lines)
    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if ln.from_bus in bus_ids and ln.to_bus in bus_ids
        and ln.from_bus != ln.to_bus
    ]
    removed_total += before - len(state.transmission_lines)

    return removed_total


def _prune_radial_buses(state: GuiSystemState) -> int:
    """Remove all radial (degree-1) unprotected buses. Returns count removed."""
    adj = _logical_bus_adjacency(state)
    removed: set[str] = set()

    changed = True
    while changed:
        changed = False
        for bus_id in list(adj.keys()):
            if bus_id in removed:
                continue
            if _bus_degree(adj, bus_id) != 1:
                continue
            if _bus_is_protected(state, bus_id, removed, adj):
                continue

            neighbor = next(iter(adj[bus_id]))
            removed.add(bus_id)
            if neighbor in adj:
                adj[neighbor].discard(bus_id)
            adj.pop(bus_id, None)
            changed = True

    if not removed:
        return 0

    _remove_buses_and_their_infrastructure(state, removed)
    return len(removed)


def _remove_buses_and_their_infrastructure(
    state: GuiSystemState,
    removed_buses: set[str],
) -> None:
    """Remove a set of buses along with all associated edges and cascaded
    visual wiring.

    Order of operations (each step is necessary for visual+logical
    consistency after the removal):

    1. Migrate equipment (gens/bats/electrolyzers) anchored at removed
       buses to a sibling bus in the same parent_node. If no sibling
       exists, remove the equipment.
    2. Identify transformers about to be removed (need their indices
       BEFORE the bus removal so we can clean wire-lines pointing to
       them).
    3. Remove the buses.
    4. Remove transmission lines that touch a removed bus or point to
       an orphaned transformer via EndpointRef.
    5. Remove orphaned transformers and remap remaining wire-lines'
       transformer indices (list shift).
    6. Remove AC/DC and frequency converters touching removed buses.
    """
    # Step 1: migrate equipment off removed buses to surviving siblings.
    # We pick the *closest* surviving bus on the same parent_node as
    # the removed bus, preferring the same voltage class.
    bus_node = {bid: b.parent_node for bid, b in state.buses.items()}
    bus_voltage = {
        bid: getattr(b, "voltage_kv", 0.0)
        for bid, b in state.buses.items()
    }

    def _migrate_target(removed_bus: str) -> str | None:
        """Best surviving bus on the same node, preferring same voltage."""
        if removed_bus not in bus_node:
            return None
        node = bus_node[removed_bus]
        v_target = bus_voltage.get(removed_bus, 0.0)
        candidates = [
            (bid, bus_voltage.get(bid, 0.0))
            for bid in state.buses
            if bid not in removed_buses
            and bus_node.get(bid) == node
        ]
        if not candidates:
            return None
        # Prefer same voltage; tiebreak: closest voltage
        candidates.sort(
            key=lambda x: (
                0 if abs(x[1] - v_target) < 1e-3 else 1,
                abs(x[1] - v_target),
            )
        )
        return candidates[0][0]

    migrate_cache: dict[str, str | None] = {}

    def _resolve_target(bus_id: str) -> str | None:
        if bus_id not in migrate_cache:
            migrate_cache[bus_id] = _migrate_target(bus_id)
        return migrate_cache[bus_id]

    for gid, gen in list(state.generators.items()):
        if gen.bus in removed_buses:
            tgt = _resolve_target(gen.bus)
            if tgt:
                gen.bus = tgt
            else:
                state.generators.pop(gid, None)
    for bid_eq, bat in list(state.batteries.items()):
        if bat.bus in removed_buses:
            tgt = _resolve_target(bat.bus)
            if tgt:
                bat.bus = tgt
            else:
                state.batteries.pop(bid_eq, None)
    if hasattr(state, "electrolyzers"):
        for eid, elec in list(state.electrolyzers.items()):
            if getattr(elec, "bus", None) in removed_buses:
                tgt = _resolve_target(elec.bus)
                if tgt:
                    elec.bus = tgt
                else:
                    state.electrolyzers.pop(eid, None)

    # Step 2: find transformers that will be removed, to clean their wires
    orphaned_tr_ids: set[str] = set()
    for idx, tr in enumerate(state.transformers):
        if tr.from_bus in removed_buses or tr.to_bus in removed_buses:
            orphaned_tr_ids.add(str(idx))

    # Step 3: remove buses
    for bid in removed_buses:
        state.buses.pop(bid, None)

    # Step 3: remove transmission lines touching removed buses or
    # pointing to orphaned transformers.
    # Both real lines and wire lines are removed if they touch a
    # removed bus. Wire lines pointing to orphaned transformers
    # are also removed. Surviving wire lines (not touching any
    # removed bus) keep their original geometry intact.
    def _line_should_be_removed(ln) -> bool:
        if ln.from_bus in removed_buses or ln.to_bus in removed_buses:
            return True
        for ep in (ln.from_endpoint, ln.to_endpoint):
            if (ep and ep.element_type == "transformer"
                    and ep.element_id in orphaned_tr_ids):
                return True
        return False

    state.transmission_lines = [
        ln for ln in state.transmission_lines
        if not _line_should_be_removed(ln)
    ]

    # Step 4: remove orphaned transformers and fix index references.
    # Transformers are stored in a list — removing entries shifts the
    # indices of all subsequent transformers. Wire lines reference
    # transformers by index string (EndpointRef("transformer", "54")).
    # We must remap these references to the new indices.
    from esfex.visualization.data.gui_model import EndpointRef

    old_to_new_tr: dict[str, str] = {}
    new_transformers = []
    for old_idx, tr in enumerate(state.transformers):
        if tr.from_bus in removed_buses or tr.to_bus in removed_buses:
            continue  # orphaned, skip
        old_to_new_tr[str(old_idx)] = str(len(new_transformers))
        new_transformers.append(tr)
    state.transformers = new_transformers

    # Update EndpointRef indices in surviving wire lines
    for ln in state.transmission_lines:
        if (ln.from_endpoint
                and ln.from_endpoint.element_type == "transformer"):
            new_idx = old_to_new_tr.get(ln.from_endpoint.element_id)
            if new_idx is not None:
                ln.from_endpoint = EndpointRef("transformer", new_idx)
        if (ln.to_endpoint
                and ln.to_endpoint.element_type == "transformer"):
            new_idx = old_to_new_tr.get(ln.to_endpoint.element_id)
            if new_idx is not None:
                ln.to_endpoint = EndpointRef("transformer", new_idx)

    # Step 5: remove converters touching removed buses
    state.acdc_converters = [
        c for c in state.acdc_converters
        if c.from_bus not in removed_buses and c.to_bus not in removed_buses
    ]
    if hasattr(state, "freq_converters"):
        state.freq_converters = [
            c for c in state.freq_converters
            if c.from_bus not in removed_buses
            and c.to_bus not in removed_buses
        ]


def _eliminate_series_buses(state: GuiSystemState) -> int:
    """Eliminate degree-2 pass-through buses (Kron reduction). Returns count."""
    from esfex.visualization.data.gui_model import (
        EndpointRef, GuiTransmissionLine,
    )

    adj = _logical_bus_adjacency(state)
    removed: set[str] = set()
    lines_to_remove: set[str] = set()
    lines_to_add: list[GuiTransmissionLine] = []
    next_id_counter = max(
        (int(ln.line_id.split("_")[1])
         for ln in state.transmission_lines
         if ln.line_id.startswith("line_") and ln.line_id.split("_")[1].isdigit()),
        default=-1,
    ) + 1

    changed = True
    while changed:
        changed = False
        for bus_id in list(adj.keys()):
            if bus_id in removed:
                continue
            if _bus_degree(adj, bus_id) != 2:
                continue
            if _bus_is_protected(state, bus_id, removed, adj):
                continue

            # Find the two transmission lines
            live_lines = [
                ln for ln in state.transmission_lines
                if (ln.from_bus == bus_id or ln.to_bus == bus_id)
                and ln.line_id not in lines_to_remove
            ]
            if len(live_lines) != 2:
                continue

            ln_a, ln_b = live_lines
            if ln_a.current_type != ln_b.current_type:
                continue
            if ln_a.frequency_hz != ln_b.frequency_hz:
                continue

            bus_a = _other_bus(ln_a, bus_id)
            bus_b = _other_bus(ln_b, bus_id)
            if bus_a == bus_b:
                continue

            # Series impedance
            eq_x = (ln_a.reactance_pu or 0) + (ln_b.reactance_pu or 0)
            eq_r = (ln_a.resistance_pu or 0) + (ln_b.resistance_pu or 0)
            eq_cap = min(ln_a.capacity_mw, ln_b.capacity_mw)
            best = ln_a if ln_a.capacity_mw >= ln_b.capacity_mw else ln_b

            lines_to_add.append(GuiTransmissionLine(
                line_id=f"line_{next_id_counter}",
                from_bus=bus_a, to_bus=bus_b,
                capacity_mw=eq_cap,
                reactance_pu=eq_x or None,
                resistance_pu=eq_r or None,
                num_circuits=min(ln_a.num_circuits, ln_b.num_circuits),
                voltage_kv=best.voltage_kv,
                current_type=ln_a.current_type,
                frequency_hz=ln_a.frequency_hz,
                waypoints=best.waypoints,
                # Map renderer resolves geometry via endpoints, not the
                # legacy from_bus/to_bus strings. Without these, the
                # equivalent line is invisible after Kron reduction.
                from_endpoint=EndpointRef("bus", bus_a),
                to_endpoint=EndpointRef("bus", bus_b),
            ))
            next_id_counter += 1

            lines_to_remove.add(ln_a.line_id)
            lines_to_remove.add(ln_b.line_id)
            removed.add(bus_id)

            # Update adjacency
            if bus_a in adj:
                adj[bus_a].discard(bus_id)
                adj[bus_a].add(bus_b)
            if bus_b in adj:
                adj[bus_b].discard(bus_id)
                adj[bus_b].add(bus_a)
            adj.pop(bus_id, None)
            changed = True

    if not removed:
        return 0

    # Remove the merged source lines first (by line_id)
    state.transmission_lines = [
        ln for ln in state.transmission_lines if ln.line_id not in lines_to_remove
    ]
    # Add the new equivalent lines
    state.transmission_lines.extend(lines_to_add)
    # Remove the pass-through buses and cascade cleanup
    _remove_buses_and_their_infrastructure(state, removed)
    return len(removed)


def _cleanup_fuel_infrastructure(
    state: GuiSystemState,
    model: "GuiModel",
) -> int:
    """Remove orphaned fuel infrastructure (entries, storages, routes).

    Only targets the fuel transport network — does NOT touch the
    electrical graph (buses, transmission lines, transformers).
    Uses the existing dead-end detection for the fuel network.
    """
    dead_actions = _find_dead_end_fuel_elements(state)
    if not dead_actions:
        return 0
    # Filter out any electrical actions that may have slipped in
    fuel_actions = [
        a for a in dead_actions
        if a.action_type in (
            "remove_fuel_entry",
            "remove_fuel_storage",
            "remove_fuel_route",
        )
    ]
    if not fuel_actions:
        return 0
    return simplify_network(model, fuel_actions)


def apply_simplification_level(
    model: "GuiModel",
    level: int,
    config: Optional[SimplificationConfig] = None,
    max_iterations: int = 20,
) -> tuple[list[str], list[NetworkIssue]]:
    """Apply simplification as a fixpoint loop.

    Each iteration:
      1. repair_network             — fix broken references, self-loops
      2. consolidate_parallel_lines — merge parallel lines
      3. aggregate_equipment        — absorb-in-place + cleanup source chains
      4. (L2+) prune_radial_buses   — remove degree-1 empty buses
      5. (L2+) eliminate_series     — Kron reduction
      6. repair_network             — fix artifacts from above

    Equipment aggregation uses absorb-in-place: the target generator
    KEEPS its existing connection chain, source generators are removed
    and their exclusive chains cleaned up. No auto-connect needed.

    Iterates until no changes or max_iterations.
    """
    if config is None:
        config = SimplificationConfig()

    state = model.state
    all_log: list[str] = []

    all_log.append(
        f"── Initial: {len(state.buses)} buses, "
        f"{len(state.transmission_lines)} lines, "
        f"{len(state.generators)} gens, "
        f"{len(state.batteries)} bats, "
        f"{len(state.transformers)} trafos ──"
    )

    # Phase 0: Initial repair
    repair_log = repair_network(state)
    if repair_log:
        all_log.append("── Initial Repair ──")
        all_log.extend(f"  {line}" for line in repair_log)

    for iteration in range(max_iterations):
        changes = 0
        round_log: list[str] = []

        # Step 0: Remove orphaned fuel infrastructure (fuel entries,
        # storages, routes with no remaining consumers). Does not
        # touch the electrical graph.
        n_fuel = _cleanup_fuel_infrastructure(state, model)
        if n_fuel:
            round_log.append(f"Removed {n_fuel} orphaned fuel element(s)")
            changes += n_fuel

        # Step 1: Parallel line consolidation (L1+)
        if level >= 1:
            n = _consolidate_parallel_lines(state)
            if n:
                round_log.append(f"Consolidated {n} parallel line(s)")
                changes += n

        # Step 2: Equipment aggregation (L1+)
        if level >= 1:
            n = _aggregate_equipment(state, model)
            if n:
                round_log.append(f"Aggregated {n} equipment group(s)")
                changes += n

        # Step 3: Radial bus pruning (L2+)
        if level >= 2:
            n = _prune_radial_buses(state)
            if n:
                round_log.append(f"Pruned {n} radial bus(es)")
                changes += n

        # Step 4: Series bus elimination (L2+)
        if level >= 2:
            n = _eliminate_series_buses(state)
            if n:
                round_log.append(f"Eliminated {n} series bus(es)")
                changes += n

        # Step 5: Intra-node voltage collapse (L3+)
        if level >= 3:
            suggestions = _find_voltage_collapse(state, config)
            for s in suggestions:
                apply_topology_suggestion(model, s)
                changes += 1
            if suggestions:
                round_log.append(
                    f"Collapsed {len(suggestions)} voltage level(s)"
                )

        # Step 6: Full node collapse (L4+)
        if level >= 4:
            suggestions = _find_full_node_collapse(state, config)
            for s in suggestions:
                apply_topology_suggestion(model, s)
                changes += 1
            if suggestions:
                round_log.append(
                    f"Collapsed {len(suggestions)} node bus(es)"
                )
            absorb = _find_small_generators(state, config)
            for s in absorb:
                _apply_small_gen_absorb(model, s)
                changes += 1
            if absorb:
                round_log.append(
                    f"Absorbed {len(absorb)} small generator(s)"
                )

        # Step 7: Repair artifacts
        repair_log = repair_network(state)
        if repair_log:
            round_log.extend(f"Repair: {r}" for r in repair_log)

        # Step 8: Sweep fully-orphaned buses. After equipment migration
        # and line cleanup, some buses may have nothing connected to
        # them at all — no lines, no transformers, no equipment, no
        # demand, not a slack bus. They contribute nothing and only
        # appear as floating dots on the map. Drop them.
        n_orphan = _drop_fully_orphan_buses(state)
        if n_orphan:
            round_log.append(f"Dropped {n_orphan} orphan bus(es)")
            changes += n_orphan

        # Snapshot
        round_log.append(
            f"→ {len(state.buses)} buses, "
            f"{len(state.transmission_lines)} lines, "
            f"{len(state.generators)} gens, "
            f"{len(state.batteries)} bats, "
            f"{len(state.transformers)} trafos"
        )

        if round_log:
            all_log.append(f"── Iteration {iteration + 1} ──")
            all_log.extend(f"  {line}" for line in round_log)

        if changes == 0:
            all_log.append(f"Converged after {iteration + 1} iteration(s)")
            break
    else:
        all_log.append(f"Reached max iterations ({max_iterations})")

    # Rebuild visual wire-lines so transformers / equipment render
    # connected to their buses on the map. Simplification frequently
    # drops or rewires these without recreating them, leaving the
    # logical graph fully connected but visually fragmented (e.g. 388
    # transformers showing as disconnected dots between 390 buses).
    n_wires = _rebuild_visual_wire_lines(state)
    if n_wires:
        all_log.append(f"Rebuilt {n_wires} visual wire-line(s)")

    # Final validation
    remaining = validate_network_integrity(state)
    if not remaining:
        all_log.append("Final validation: OK")
    else:
        all_log.append(
            f"Final validation: {len(remaining)} issue(s) remaining"
        )
        for issue in remaining:
            all_log.append(f"  [{issue.severity}] {issue.message}")

    return all_log, remaining


# ── Post-simplification network validation ───────────────────────


@dataclass
class NetworkIssue:
    """A problem detected in the simplified network."""

    severity: Literal["error", "warning"]
    message: str


def validate_network_integrity(
    state: GuiSystemState,
) -> list[NetworkIssue]:
    """Validate that a network is structurally sound after simplification.

    Checks performed:
    1. Orphaned references: equipment/lines pointing to non-existent buses
    2. Self-loop lines
    3. Demand fraction consistency (sum per node)
    4. Graph connectivity: demand buses reachable from generation
    5. Capacity adequacy per connected component
    6. Empty nodes (no buses left)

    Returns list of issues found (empty = network is valid).
    """
    issues: list[NetworkIssue] = []
    bus_ids = set(state.buses.keys())

    # 1. Orphaned references
    for gid, gen in state.generators.items():
        if gen.bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Generator '{gen.name or gid}' references non-existent "
                f"bus '{gen.bus}'",
            ))
    for bid, bat in state.batteries.items():
        if bat.bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Battery '{bat.name or bid}' references non-existent "
                f"bus '{bat.bus}'",
            ))
    if hasattr(state, "electrolyzers"):
        for eid, elec in state.electrolyzers.items():
            b = getattr(elec, "bus", None)
            if b and b not in bus_ids:
                issues.append(NetworkIssue(
                    "error",
                    f"Electrolyzer '{eid}' references non-existent bus '{b}'",
                ))
    for ln in state.transmission_lines:
        if ln.from_bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Line '{ln.line_id}' from_bus '{ln.from_bus}' does not exist",
            ))
        if ln.to_bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Line '{ln.line_id}' to_bus '{ln.to_bus}' does not exist",
            ))
    for idx, tr in enumerate(state.transformers):
        if tr.from_bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Transformer '{tr.name}' from_bus '{tr.from_bus}' "
                f"does not exist",
            ))
        if tr.to_bus not in bus_ids:
            issues.append(NetworkIssue(
                "error",
                f"Transformer '{tr.name}' to_bus '{tr.to_bus}' "
                f"does not exist",
            ))

    # 2. Self-loop lines (exclude wire lines — equipment→bus self-loops
    #    are normal visual decoration)
    for ln in state.transmission_lines:
        if ln.from_bus == ln.to_bus and not _is_wire_line(ln):
            issues.append(NetworkIssue(
                "warning",
                f"Line '{ln.line_id}' is a self-loop on bus '{ln.from_bus}'",
            ))

    # 3. Demand fraction consistency
    node_df: dict[int, float] = defaultdict(float)
    for bus in state.buses.values():
        node_df[bus.parent_node] += bus.demand_fraction
    for node in state.nodes:
        total = node_df.get(node.index, 0.0)
        if total > 0 and abs(total - 1.0) > 0.01:
            issues.append(NetworkIssue(
                "warning",
                f"Node '{node.name}' (idx={node.index}): demand_fraction "
                f"sum = {total:.3f} (expected ~1.0)",
            ))

    # 4. Empty nodes (no buses)
    node_bus_count: dict[int, int] = defaultdict(int)
    for bus in state.buses.values():
        node_bus_count[bus.parent_node] += 1
    for node in state.nodes:
        if node_bus_count.get(node.index, 0) == 0:
            issues.append(NetworkIssue(
                "error",
                f"Node '{node.name}' (idx={node.index}) has no buses",
            ))

    # 5. Connectivity: demand buses reachable from generation
    adj = _logical_bus_adjacency(state)

    # Find connected components via BFS
    visited: set[str] = set()
    components: list[set[str]] = []
    for seed in bus_ids:
        if seed in visited:
            continue
        comp: set[str] = set()
        queue = deque([seed])
        while queue:
            b = queue.popleft()
            if b in visited:
                continue
            visited.add(b)
            comp.add(b)
            for nb in adj.get(b, set()):
                if nb not in visited:
                    queue.append(nb)
        components.append(comp)

    # Classify each component
    for comp in components:
        comp_gen_cap = sum(
            gen.rated_power for gen in state.generators.values()
            if gen.bus in comp
        )
        comp_bat_cap = sum(
            bat.rated_power for bat in state.batteries.values()
            if bat.bus in comp
        )
        comp_has_gen = comp_gen_cap > 0 or comp_bat_cap > 0

        comp_demand_frac = sum(
            state.buses[bid].demand_fraction
            for bid in comp if bid in state.buses
        )
        comp_has_demand = comp_demand_frac > 0

        if comp_has_demand and not comp_has_gen:
            bus_list = ", ".join(sorted(comp)[:5])
            issues.append(NetworkIssue(
                "error",
                f"Disconnected demand: buses [{bus_list}] have demand "
                f"(frac={comp_demand_frac:.2f}) but no generation capacity",
            ))
        elif comp_has_gen and not comp_has_demand and comp_gen_cap > 1.0:
            bus_list = ", ".join(sorted(comp)[:5])
            issues.append(NetworkIssue(
                "warning",
                f"Isolated generation: buses [{bus_list}] have "
                f"{comp_gen_cap:.1f} MW generation but no demand",
            ))

    # 6. Capacity adequacy per component with demand
    for comp in components:
        comp_gen_cap = sum(
            gen.rated_power for gen in state.generators.values()
            if gen.bus in comp
        )
        comp_bat_cap = sum(
            bat.rated_power for bat in state.batteries.values()
            if bat.bus in comp
        )
        total_cap = comp_gen_cap + comp_bat_cap

        # Estimate peak demand for this component
        comp_demand_frac = sum(
            state.buses[bid].demand_fraction
            for bid in comp if bid in state.buses
        )
        if comp_demand_frac <= 0:
            continue

        # Check if any node in this component has loaded demand
        comp_nodes = {
            state.buses[bid].parent_node
            for bid in comp if bid in state.buses
        }
        for node in state.nodes:
            if node.index in comp_nodes and node.demand.peak_mw > 0:
                node_frac_in_comp = sum(
                    state.buses[bid].demand_fraction
                    for bid in comp
                    if bid in state.buses
                    and state.buses[bid].parent_node == node.index
                )
                peak_in_comp = node.demand.peak_mw * node_frac_in_comp
                if total_cap > 0 and peak_in_comp > total_cap * 1.1:
                    issues.append(NetworkIssue(
                        "warning",
                        f"Capacity deficit in component: peak demand "
                        f"~{peak_in_comp:.1f} MW > capacity "
                        f"{total_cap:.1f} MW",
                    ))

    return issues


# ── Unified entry point ──────────────────────────────────────────


def _find_aggregatable_equipment(
    state: GuiSystemState,
) -> list[InfrastructureSuggestion]:
    """Find equipment groups that would be aggregated by _aggregate_equipment.

    Matches the grouping logic of _aggregate_equipment: groups by
    (connected_component, node, fuel, gen_type, availability_file) for
    generators and (connected_component, node, fuel, availability_file)
    for batteries. Only groups with ≥2 members produce suggestions.

    Used for PREVIEW — the result shows exactly what the apply step
    will do, ensuring idempotency.
    """
    suggestions: list[InfrastructureSuggestion] = []
    comp_of_bus = _bus_to_component_id(state)

    # Generators
    gen_groups: dict[tuple, list[str]] = defaultdict(list)
    for gid, gen in state.generators.items():
        bus = state.buses.get(gen.bus)
        if bus is None:
            continue
        comp = comp_of_bus.get(gen.bus, -1)
        key = (comp, bus.parent_node, gen.fuel, gen.gen_type,
               gen.availability_file or "")
        gen_groups[key].append(gid)

    for key, gids in gen_groups.items():
        if len(gids) < 2:
            continue
        instances = [state.generators[gid] for gid in gids]
        total_power = sum(g.rated_power for g in instances)
        target_id = max(gids, key=lambda g: state.generators[g].rated_power)
        target = state.generators[target_id]
        fuel = target.fuel
        suggestions.append(InfrastructureSuggestion(
            level="node",
            equipment_type="generator",
            instance_ids=gids,
            target_bus=target.bus,
            target_unit_key=target.unit_key,
            target_name=target.name or f"Agg {fuel}",
            fuel=fuel,
            gen_type=target.gen_type,
            total_rated_power=total_power,
            total_capacity=0.0,
            reduction=len(gids) - 1,
            description=(
                f"Absorb {len(gids)-1} {fuel} {target.gen_type} generator(s) "
                f"into {target.name or target_id} ({total_power:.1f} MW total)"
            ),
        ))

    # Batteries
    bat_groups: dict[tuple, list[str]] = defaultdict(list)
    for bid, bat in state.batteries.items():
        bus = state.buses.get(bat.bus)
        if bus is None:
            continue
        comp = comp_of_bus.get(bat.bus, -1)
        key = (comp, bus.parent_node, bat.fuel, bat.availability_file or "")
        bat_groups[key].append(bid)

    for key, bids in bat_groups.items():
        if len(bids) < 2:
            continue
        instances = [state.batteries[bid] for bid in bids]
        total_power = sum(b.rated_power for b in instances)
        total_cap = sum(b.capacity for b in instances)
        target_id = max(bids, key=lambda b: state.batteries[b].rated_power)
        target = state.batteries[target_id]
        fuel = target.fuel
        suggestions.append(InfrastructureSuggestion(
            level="node",
            equipment_type="battery",
            instance_ids=bids,
            target_bus=target.bus,
            target_unit_key=target.unit_key,
            target_name=target.name or f"Agg {fuel}",
            fuel=fuel,
            gen_type="",
            total_rated_power=total_power,
            total_capacity=total_cap,
            reduction=len(bids) - 1,
            description=(
                f"Absorb {len(bids)-1} {fuel} battery(ies) into "
                f"{target.name or target_id} "
                f"({total_power:.1f} MW / {total_cap:.1f} MWh)"
            ),
        ))

    return suggestions


def find_simplifications_for_level(
    state: GuiSystemState,
    level: int,
    config: Optional[SimplificationConfig] = None,
) -> SimplificationPlan:
    """Find all applicable simplifications for a given level (0-4).

    Levels are cumulative: level N includes all operations from levels < N.

    Parameters
    ----------
    state : GuiSystemState
        Current system state.
    level : int
        Simplification level (0-4).
    config : SimplificationConfig, optional
        Thresholds. Uses defaults if None.

    Returns
    -------
    SimplificationPlan
        Plan with both infrastructure and topology suggestions.
    """
    if config is None:
        config = SimplificationConfig()

    plan = SimplificationPlan(
        level=level,
        buses_before=len(state.buses),
        lines_before=len(state.transmission_lines),
        generators_before=len(state.generators),
        transformers_before=len(state.transformers),
    )

    # Level 0: cleanup only — no suggestions (cleanup is done separately)
    if level < 1:
        plan.buses_after = plan.buses_before
        plan.lines_after = plan.lines_before
        plan.generators_after = plan.generators_before
        plan.transformers_after = plan.transformers_before
        return plan

    # Level 1+: Equipment aggregation (component-aware) + parallel lines
    if level >= 1:
        plan.infrastructure_suggestions = _find_aggregatable_equipment(state)
        plan.topology_suggestions.extend(
            _find_parallel_lines(state, config),
        )

    # Level 2+: Radial & series bus elimination
    if level >= 2:
        plan.topology_suggestions.extend(_find_radial_buses(state))
        plan.topology_suggestions.extend(_find_series_buses(state))

    # Level 3: Intra-node voltage collapse
    if level >= 3:
        plan.topology_suggestions.extend(_find_voltage_collapse(state, config))

    # Level 4: Full node collapse + small generator absorption
    if level >= 4:
        plan.topology_suggestions.extend(_find_full_node_collapse(state, config))
        plan.topology_suggestions.extend(
            _find_small_generators(state, config),
        )

    # Estimate after counts
    buses_removed = sum(
        len(s.buses_to_remove) for s in plan.topology_suggestions
    )
    lines_removed = sum(
        len(s.lines_to_remove) for s in plan.topology_suggestions
    )
    lines_created = sum(
        len(s.lines_to_create) for s in plan.topology_suggestions
    )
    trafos_removed = sum(
        len(s.transformers_to_remove) for s in plan.topology_suggestions
    )
    gen_reduction = sum(
        s.reduction for s in plan.infrastructure_suggestions
    )
    small_gen_absorbed = sum(
        1 for s in plan.topology_suggestions
        if s.action_type == "small_gen_absorb"
    )

    plan.buses_after = max(0, plan.buses_before - buses_removed)
    plan.lines_after = max(0, plan.lines_before - lines_removed + lines_created)
    plan.generators_after = max(
        0, plan.generators_before - gen_reduction - small_gen_absorbed,
    )
    plan.transformers_after = max(0, plan.transformers_before - trafos_removed)

    return plan


# ── Inter-system links (cross-state validation) ──────────────────


def validate_inter_system_links(
    links: list["GuiInterSystemLink"],
    states_by_name: dict[str, "GuiSystemState"],
) -> list[ValidationIssue]:
    """Validate every inter-system link against the multi-system state.

    Unlike per-state validators above, an inter-system link references
    two different ``GuiSystemState`` instances (``from_system`` and
    ``to_system``) so it can't be checked from inside ``validate_state``.
    Call this once with ``model.inter_system_links`` and the full
    ``_all_states`` dict.

    Checks:
      - referenced systems exist in ``states_by_name``
      - ``from_system != to_system`` (self-loops aren't meaningful)
      - ``from_node`` / ``to_node`` are valid indices in their state
      - ``capacity_mw``, ``max_investment_mw``, ``distance_km``,
        ``reactance_pu``, ``resistance_pu`` are non-negative
      - ``loss_factor`` in [0, 1]
      - persisted ``from_endpoint`` / ``to_endpoint`` bus IDs exist in
        their respective state
      - no two links connect the same ordered (from_sys/from_node,
        to_sys/to_node) pair (silent duplicate would inflate capacity)
    """
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, int, str, int]] = set()

    for lk in links or []:
        lid = lk.link_id
        # Referenced systems exist
        if lk.from_system not in states_by_name:
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: from_system {lk.from_system!r} not loaded "
                    f"(silently dropped by runner)"),
            ))
            continue
        if lk.to_system not in states_by_name:
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: to_system {lk.to_system!r} not loaded "
                    f"(silently dropped by runner)"),
            ))
            continue
        if lk.from_system == lk.to_system:
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: from_system == to_system ({lk.from_system!r}); "
                    "use an intra-system transmission line instead"),
            ))
            continue

        from_st = states_by_name[lk.from_system]
        to_st = states_by_name[lk.to_system]

        # Node index in range
        if lk.from_node < 0 or lk.from_node >= len(from_st.nodes):
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: from_node {lk.from_node} out of range "
                    f"[0, {len(from_st.nodes)})"),
            ))
        if lk.to_node < 0 or lk.to_node >= len(to_st.nodes):
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: to_node {lk.to_node} out of range "
                    f"[0, {len(to_st.nodes)})"),
            ))

        # Non-negative scalars
        for fname in ("capacity_mw", "max_investment_mw", "distance_km",
                       "reactance_pu", "resistance_pu", "investment_cost",
                       "cost_per_mw_km"):
            val = getattr(lk, fname, 0.0) or 0.0
            if val < 0:
                issues.append(ValidationIssue(
                    severity="error", category="Inter-System",
                    element_type="inter_system_link", element_id=lid,
                    message=f"Link {lid}: {fname}={val} must be ≥ 0",
                ))

        # Loss factor in [0, 1]
        lf = lk.loss_factor or 0.0
        if not (0.0 <= lf <= 1.0):
            issues.append(ValidationIssue(
                severity="error", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=f"Link {lid}: loss_factor={lf} must be in [0, 1]",
            ))

        # Endpoint bus references (when persisted) resolve in the right state
        if lk.from_endpoint and lk.from_endpoint.element_type == "bus":
            if lk.from_endpoint.element_id not in from_st.buses:
                issues.append(ValidationIssue(
                    severity="warning", category="Inter-System",
                    element_type="inter_system_link", element_id=lid,
                    message=(
                        f"Link {lid}: from_endpoint bus "
                        f"{lk.from_endpoint.element_id!r} not in "
                        f"system {lk.from_system!r} (polyline may not render)"),
                ))
        if lk.to_endpoint and lk.to_endpoint.element_type == "bus":
            if lk.to_endpoint.element_id not in to_st.buses:
                issues.append(ValidationIssue(
                    severity="warning", category="Inter-System",
                    element_type="inter_system_link", element_id=lid,
                    message=(
                        f"Link {lid}: to_endpoint bus "
                        f"{lk.to_endpoint.element_id!r} not in "
                        f"system {lk.to_system!r} (polyline may not render)"),
                ))

        # Capacity for an active transmission link sanity-check
        if lk.link_type == "transmission" and (lk.capacity_mw or 0.0) <= 0:
            issues.append(ValidationIssue(
                severity="warning", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: capacity_mw is 0 — the link will exist "
                    "in the topology but carry no power; set capacity or "
                    "max_investment > 0"),
            ))

        # Duplicate detection (same ordered endpoints)
        key = (lk.from_system, lk.from_node, lk.to_system, lk.to_node)
        if key in seen:
            issues.append(ValidationIssue(
                severity="warning", category="Inter-System",
                element_type="inter_system_link", element_id=lid,
                message=(
                    f"Link {lid}: duplicate of an earlier link "
                    f"({lk.from_system}:{lk.from_node} → "
                    f"{lk.to_system}:{lk.to_node}); merged adjacency uses "
                    "max() so the second entry is silently ignored"),
            ))
        else:
            seen.add(key)

    return issues
