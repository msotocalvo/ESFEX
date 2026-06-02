"""Main network reduction orchestrator.

Reads a :class:`SystemConfig`, builds an internal graph, applies local
reductions (parallel merge, leaf pruning, series collapse) iteratively
to a fixed point, and produces:

1. A reduced :class:`SystemConfig` with the same equipment (generators,
   batteries, transformers, converters) but a smaller bus set and a
   reduced transmission_lines_geo list.
2. A :class:`ReductionMap` that records every elimination and merge so
   the downstream expander can recover per-original-bus angles and
   per-original-line flows after the LP is solved.

The reduction is exact for DC-PF.  It does **not** touch:

- Per-node demand data (demand is per-node, not per-bus).
- Generator / battery / technology definitions (they remain per-node).
- Per-node reserve requirements, losses, transfer-invest settings.
- The adjacency matrix at the node level (only bus-level details change).
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

from esfex.topology.reduction_map import (
    MergedLine,
    OriginalLineRef,
    PrunedBus,
    ReductionMap,
)
from esfex.topology.transformations import (
    _Edge,
    _PrunedRecord,
    _ReductionGraph,
    collapse_series,
    merge_parallel_lines,
    prune_leaves,
    star_to_mesh_degree3,
)

if TYPE_CHECKING:
    from esfex.config.schema import SystemConfig, BusConfig, TransmissionLineGeo

logger = logging.getLogger(__name__)


def _resistance_for_target_conductance(g_target: float, x_eq: float) -> float:
    """Solve for R such that ``R / (R² + X²) = g_target`` (Julia's loss formula).

    Quadratic in R: ``g_target · R² − R + g_target · X² = 0``.

    Roots: ``R = (1 ± √(1 − 4·g_target²·X²)) / (2·g_target)``.

    The physically meaningful root is the smaller one (R << X for typical
    HV lines).  ``R/(R²+X²)`` peaks at R=X with value ``1/(2X)``, so when
    ``g_target > 1/(2X)`` the target exceeds the maximum representable
    conductance — clamp to the apex (R = X).

    Numerical caveat: for very small ``g·X`` the discriminant rounds to
    1.0 in float64 and the direct formula collapses to ``R = 0``.  Use
    a numerically stable form via the conjugate:

        R = (1 − √(1 − ε)) / (2g)   with   ε = 4 · g² · X²
          = ε / [2g · (1 + √(1 − ε))]
          ≈ g · X²   for ε ≪ 1
    """
    if g_target <= 0:
        return 0.0
    eps = 4.0 * g_target * g_target * x_eq * x_eq
    if eps > 1.0:
        # g_target exceeds the max representable 1/(2X); use the apex.
        return x_eq
    sqrt_term = (1.0 - eps) ** 0.5
    # Numerically stable inversion (avoids 1 - sqrt(≈1) cancellation):
    #   R = ε / [2g · (1 + sqrt(1 - ε))]
    return eps / (2.0 * g_target * (1.0 + sqrt_term))


def _identify_protected_buses(sys_config: "SystemConfig") -> set[int]:
    """Return the set of bus indices that must not be eliminated.

    Protection criteria
    -------------------
    - Role ≠ "connection" (has demand, serves load).
    - Is an endpoint of a transformer, AC/DC converter, or frequency
      converter (equipment terminals must be retained).
    - Is the bus-side endpoint of a transmission line whose other
      endpoint is a generator or battery (explicit unit placement
      recorded in ``transmission_lines_geo``).
    """
    buses = sys_config.buses or []
    protected: set[int] = set()

    def add(idx: "int | None") -> None:
        if idx is not None and 0 <= idx < len(buses):
            protected.add(idx)

    # 1) Buses with a load/mixed role (i.e., carry demand)
    for i, b in enumerate(buses):
        if b.role != "connection":
            protected.add(i)

    # 2) AC/DC converter and frequency converter endpoints.
    #    Transformers are NOT protected here — they participate as edges
    #    in the reduction graph and can be absorbed into equivalent lines.
    #    AC/DC + frequency converters are kept separate because their
    #    Julia representation has side effects (standby losses, efficiency
    #    curves) that don't reduce to a pure line impedance.
    for ac in (getattr(sys_config, "acdc_converters", None) or []):
        add(getattr(ac, "from_bus", None))
        add(getattr(ac, "to_bus", None))
    for fc in (getattr(sys_config, "freq_converters", None) or []):
        add(getattr(fc, "from_bus", None))
        add(getattr(fc, "to_bus", None))

    # 3) Bus-side endpoints of generator/battery-anchored lines.
    for line in (sys_config.transmission_lines_geo or []):
        f_type = getattr(line, "from_endpoint_type", None)
        t_type = getattr(line, "to_endpoint_type", None)
        if f_type in ("generator", "battery"):
            add(getattr(line, "to_bus", None))
        if t_type in ("generator", "battery"):
            add(getattr(line, "from_bus", None))

    return protected


def _build_graph(
    sys_config: "SystemConfig", protected: set[int]
) -> tuple[_ReductionGraph, list[str], int, int]:
    """Construct the mutable reduction graph from a :class:`SystemConfig`.

    Both transmission lines AND transformers enter the graph as edges
    because the Julia DCOPF formulation treats them identically (same
    admittance-based flow equation, same capacity constraint, same
    loss model).  This unification enables substantially more bus
    eliminations in systems where transformers dominate the topology.

    Original indices are encoded with an offset so the reducer can
    distinguish line from transformer after merging:
      - ``orig_idx < n_lines``                 → transmission line
      - ``n_lines ≤ orig_idx < n_lines+n_tf``  → transformer

    Returns
    -------
    graph : _ReductionGraph
    skipped_ids : list[str]
        IDs of edges (lines or transformers) that could not be added
        (e.g. missing from_bus/to_bus). These pass through unchanged.
    n_lines : int
        Count of transmission lines from the source config.
    n_transformers : int
        Count of transformers from the source config.
    """
    buses = sys_config.buses or []
    graph = _ReductionGraph()
    graph.buses = list(range(len(buses)))  # internal idx == original idx initially
    graph.protected = set(protected)
    for b in range(len(buses)):
        graph.adjacency[b] = set()

    skipped: list[str] = []
    lines = sys_config.transmission_lines_geo or []
    transformers = sys_config.transformers or []
    n_lines = len(lines)
    n_tf = len(transformers)

    # DC power flow defaults for deriving reactance from line length
    # (mirrors converters.convert_transmission_line_data lines 526-532).
    dc_cfg = getattr(sys_config, "dc_power_flow", None)
    dc_react_per_km = float(getattr(dc_cfg, "reactance_per_km", 0.4)) if dc_cfg else 0.4
    dc_base_z = float(getattr(dc_cfg, "base_impedance", 100.0)) if dc_cfg else 100.0

    for orig_idx, line in enumerate(lines):
        fb = getattr(line, "from_bus", None)
        tb = getattr(line, "to_bus", None)
        if fb is None or tb is None:
            skipped.append(line.line_id or f"line_{orig_idx}")
            continue
        if fb == tb:
            skipped.append(line.line_id or f"line_{orig_idx}")
            continue
        if not (0 <= fb < len(buses)) or not (0 <= tb < len(buses)):
            skipped.append(line.line_id or f"line_{orig_idx}")
            continue

        # Match Julia's reactance derivation exactly: per-line value if
        # given, else derived from length_km, else minimal default 0.01.
        length = float(line.length_km) if line.length_km is not None else 0.0
        x_raw = line.reactance_pu
        if x_raw is None or float(x_raw) <= 0:
            if length > 0 and dc_base_z > 0:
                x_pu = (length * dc_react_per_km) / dc_base_z
            else:
                x_pu = 0.01
        else:
            x_pu = float(x_raw)
        r_pu = float(line.resistance_pu) if line.resistance_pu is not None else 0.0
        cap = float(line.capacity_mw) if line.capacity_mw is not None else 0.0
        n_circ = int(line.num_circuits or 1)

        graph.new_edge(
            from_bus=fb,
            to_bus=tb,
            reactance_pu=x_pu,
            resistance_pu=r_pu,
            capacity_mw=cap,
            length_km=length,
            num_circuits=n_circ,
            originals=[(orig_idx, +1, 1.0)],
        )

    # Transformers as additional edges (indices offset by n_lines).
    # Julia derives r and x from the magnitude impedance |z|:
    #   r = (resistance_pu if given) else losses_fraction · |z|
    #   x = sqrt(|z|² - r²)
    # We must replicate this derivation exactly so the merged equivalent
    # line carries the correct reactance and conductance into the LP.
    import math as _math
    for t_idx, tf in enumerate(transformers):
        fb = getattr(tf, "from_bus", None)
        tb = getattr(tf, "to_bus", None)
        if fb is None or tb is None or fb == tb:
            skipped.append(getattr(tf, "name", None) or f"transformer_{t_idx}")
            continue
        if not (0 <= fb < len(buses)) or not (0 <= tb < len(buses)):
            skipped.append(getattr(tf, "name", None) or f"transformer_{t_idx}")
            continue

        z_pu = float(tf.impedance_pu)
        losses_frac = float(getattr(tf, "losses_fraction", 0.005))
        r_pu_attr = getattr(tf, "resistance_pu", None)
        r_pu = float(r_pu_attr) if r_pu_attr is not None else losses_frac * z_pu
        x_pu_sq = z_pu * z_pu - r_pu * r_pu
        x_pu = _math.sqrt(max(x_pu_sq, 1e-12))
        cap = float(tf.rated_power_mva)

        graph.new_edge(
            from_bus=fb,
            to_bus=tb,
            reactance_pu=x_pu,
            resistance_pu=r_pu,
            capacity_mw=cap,
            length_km=0.0,
            num_circuits=1,
            originals=[(n_lines + t_idx, +1, 1.0)],
        )

    return graph, skipped, n_lines, n_tf


def _fixed_point_reduce(
    graph: _ReductionGraph,
    max_iters: int = 20,
    enable_kron_deg3: bool = False,
) -> None:
    """Run local transformations until no more changes occur.

    Parameters
    ----------
    graph
        The mutable reduction graph.
    max_iters
        Safety cap on outer iterations.
    enable_kron_deg3
        When True, additionally apply star-mesh (Kron) elimination to
        degree-3 non-protected junctions.  This keeps the line count
        unchanged per elimination but creates parallel lines that a
        subsequent ``merge_parallel_lines`` pass can consolidate.
        Degree ≥ 4 is NOT attempted because the mesh size grows as
        k(k-1)/2 which explodes for high-degree hubs.
    """
    for i in range(max_iters):
        n1 = merge_parallel_lines(graph)
        n2 = prune_leaves(graph)
        n3 = collapse_series(graph)
        n4 = star_to_mesh_degree3(graph) if enable_kron_deg3 else 0
        if n1 == n2 == n3 == n4 == 0:
            return
    logger.warning(
        "Reduction did not converge after %d iterations (possibly cyclic)", max_iters
    )


def _build_reduced_system(
    sys_config: "SystemConfig",
    graph: _ReductionGraph,
    skipped_line_ids: list[str],
    n_lines: int,
    n_transformers: int,
) -> tuple["SystemConfig", ReductionMap]:
    """Materialise reduced SystemConfig + ReductionMap from the graph state."""
    from esfex.config.schema import BusConfig, TransmissionLineGeo

    original_buses = sys_config.buses or []
    original_lines = sys_config.transmission_lines_geo or []
    original_transformers = sys_config.transformers or []

    # ── Bus side ──
    # Reduced bus order: keep original ordering of retained buses
    retained_original_indices: list[int] = [
        graph.buses[i] for i in range(len(graph.buses))
        if graph.buses[i] != -1
    ]
    original_to_reduced_bus: list["int | None"] = [None] * len(original_buses)
    for red_idx, orig_idx in enumerate(retained_original_indices):
        original_to_reduced_bus[orig_idx] = red_idx

    reduced_buses: list[BusConfig] = [
        original_buses[orig_idx] for orig_idx in retained_original_indices
    ]

    # ── Line + transformer side ──
    # Each merged edge carries originals tagged by offset index:
    #   orig_idx < n_lines          → transmission line
    #   n_lines <= orig_idx         → transformer (t_idx = orig_idx - n_lines)
    # The reduced config emits every merged edge as TransmissionLineGeo
    # (Julia's DCOPF treats lines and transformers identically).  The
    # ReductionMap preserves both mappings separately so results can be
    # expanded back to each original element.
    merged_edges = sorted(
        graph.edges.values(),
        key=lambda e: (e.from_bus, e.to_bus, e.edge_id),
    )
    merged_lines_map: list[MergedLine] = []
    reduced_lines: list[TransmissionLineGeo] = []
    original_to_reduced_line: list["tuple[int, int, float] | None"] = [
        None
    ] * len(original_lines)
    # Transformer result mapping: same sentinel format as lines.
    original_to_reduced_transformer: list["tuple[int, int, float] | None"] = [
        None
    ] * len(original_transformers)

    def _split_originals(
        originals: list[tuple[int, int, float]],
    ) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:
        """Partition originals into (line_refs, transformer_refs) by offset."""
        line_refs: list[tuple[int, int, float]] = []
        tf_refs: list[tuple[int, int, float]] = []
        for oi, od, os in originals:
            if oi < n_lines:
                line_refs.append((oi, od, os))
            else:
                tf_refs.append((oi - n_lines, od, os))
        return line_refs, tf_refs

    for reduced_line_idx, edge in enumerate(merged_edges):
        fb_orig = graph.buses[edge.from_bus]
        tb_orig = graph.buses[edge.to_bus]
        fb_red = original_to_reduced_bus[fb_orig]
        tb_red = original_to_reduced_bus[tb_orig]
        assert fb_red is not None and tb_red is not None

        line_refs_raw, tf_refs_raw = _split_originals(edge.originals)

        # MergedLine stores only line references (for backward compat with
        # the public API of OriginalLineRef).  Transformer references go
        # into a parallel mapping.
        orig_refs = [
            OriginalLineRef(line_idx=oi, direction=od, flow_share=os)
            for oi, od, os in line_refs_raw
        ]
        merged = MergedLine(
            reduced_line_idx=reduced_line_idx,
            from_bus_reduced=fb_red,
            to_bus_reduced=tb_red,
            reactance_pu=edge.reactance_pu,
            resistance_pu=edge.resistance_pu,
            capacity_mw=edge.capacity_mw,
            length_km=edge.length_km,
            num_circuits=edge.num_circuits,
            originals=orig_refs,
        )
        merged_lines_map.append(merged)

        for oi, od, os in line_refs_raw:
            original_to_reduced_line[oi] = (reduced_line_idx, od, os)
        for oi, od, os in tf_refs_raw:
            original_to_reduced_transformer[oi] = (reduced_line_idx, od, os)

        # Build a synthetic TransmissionLineGeo for the reduced network.
        # Use the first line original as a prototype if available; else
        # use the first transformer as a prototype (TransformerConfig
        # doesn't have voltage_kv/line_type but we fall back to defaults).
        proto_line = None
        proto_tf = None
        if line_refs_raw:
            proto_line = original_lines[line_refs_raw[0][0]]
        if tf_refs_raw:
            proto_tf = original_transformers[tf_refs_raw[0][0]]

        # Build a descriptive line id summarising the first few originals
        id_parts = [f"L{oi}" for oi, _, _ in line_refs_raw[:3]]
        id_parts += [f"T{oi}" for oi, _, _ in tf_refs_raw[:3]]
        trail = "…" if (len(line_refs_raw) + len(tf_refs_raw)) > 6 else ""
        reduced_line_id = f"reduced_{reduced_line_idx}_{'-'.join(id_parts)}{trail}"

        # Pull descriptive attributes from whichever prototype is available
        def _proto_attr(attr, default):
            if proto_line is not None:
                v = getattr(proto_line, attr, None)
                if v is not None:
                    return v
            if proto_tf is not None:
                v = getattr(proto_tf, attr, None)
                if v is not None:
                    return v
            return default

        # Override resistance_pu so that Julia's R/(R²+X²) formula yields
        # the exact loss-equivalent conductance accumulated through the
        # series/parallel merges.  For unmerged edges this is a no-op
        # (the inverse is exact: r = (1−√(1−4g²x²))/(2g) ≈ r_original).
        r_for_loss = _resistance_for_target_conductance(
            edge.loss_conductance, edge.reactance_pu,
        )
        reduced_lines.append(
            TransmissionLineGeo(
                line_id=reduced_line_id,
                from_node=_proto_attr("from_node", 0),
                to_node=_proto_attr("to_node", 0),
                from_bus=fb_red,
                to_bus=tb_red,
                capacity_mw=edge.capacity_mw,
                length_km=edge.length_km,
                reactance_pu=edge.reactance_pu,
                resistance_pu=r_for_loss,
                num_circuits=edge.num_circuits,
                voltage_kv=_proto_attr("voltage_kv", None),
                frequency_hz=_proto_attr("frequency_hz", 50.0),
                current_type=_proto_attr("current_type", "AC"),
                line_type=_proto_attr("line_type", None),
            )
        )

    # ── Zero-flow originals from leaf pruning ──
    # A leaf line or transformer carries zero flow in the original
    # problem (its pruned bus had no injection).  Mark its mapping with
    # a ghost sentinel so the expander emits zeros.
    for orig_bus_idx, rec in graph.pruned_buses.items():
        ghosts = getattr(rec, "zero_flow_originals", None)
        if ghosts:
            for gidx in ghosts:
                if gidx < n_lines:
                    original_to_reduced_line[gidx] = (-1, +1, 0.0)
                else:
                    original_to_reduced_transformer[gidx - n_lines] = (-1, +1, 0.0)

    # ── Pruned bus records ──
    pruned_public: dict[int, PrunedBus] = {}
    for orig_bus_idx, rec in graph.pruned_buses.items():
        if rec.kind == "leaf":
            pruned_public[orig_bus_idx] = PrunedBus(
                original_bus_idx=orig_bus_idx,
                angle_source_bus=rec.angle_source_original,
            )
        elif rec.kind == "series":
            pruned_public[orig_bus_idx] = PrunedBus(
                original_bus_idx=orig_bus_idx,
                series_from_endpoint_original=rec.series_from_original,
                series_to_endpoint_original=rec.series_to_original,
                series_reactance_fraction_from_to=rec.reactance_fraction_from_side,
            )
        elif rec.kind == "kron_deg3":
            pruned_public[orig_bus_idx] = PrunedBus(
                original_bus_idx=orig_bus_idx,
                kron_neighbours=list(
                    getattr(rec, "kron_neighbours", []) or []
                ),
            )

    # ── Lines that were skipped (passed through unchanged) ──
    # They must be appended to the reduced transmission_lines_geo so Julia
    # still sees them.  Their bus endpoints might have been remapped if
    # those buses were retained (the common case); if a skipped line
    # references an eliminated bus we error out loudly.
    for orig_idx, line in enumerate(original_lines):
        if original_to_reduced_line[orig_idx] is not None:
            continue  # already handled (including ghosts)
        fb = getattr(line, "from_bus", None)
        tb = getattr(line, "to_bus", None)
        if fb is None or tb is None:
            # Pass through as-is
            reduced_lines.append(line.model_copy())
            original_to_reduced_line[orig_idx] = (
                len(reduced_lines) - 1, +1, 1.0,
            )
            continue
        fb_red = original_to_reduced_bus[fb]
        tb_red = original_to_reduced_bus[tb]
        if fb_red is None or tb_red is None:
            raise AssertionError(
                f"Skipped line {line.line_id!r} references eliminated bus "
                f"(from_bus={fb}→{fb_red}, to_bus={tb}→{tb_red}). "
                "This indicates the protection logic missed a bus with a "
                "passive transmission line."
            )
        remapped = line.model_copy(update={"from_bus": fb_red, "to_bus": tb_red})
        reduced_lines.append(remapped)
        original_to_reduced_line[orig_idx] = (len(reduced_lines) - 1, +1, 1.0)

    # ── Remap equipment bus references (converters + pass-through trafos) ──
    from esfex.config.schema import SystemConfig

    def _remap(obj, fields=("from_bus", "to_bus")):
        updates = {}
        for f in fields:
            v = getattr(obj, f, None)
            if v is not None:
                new = original_to_reduced_bus[v]
                if new is None:
                    raise AssertionError(
                        f"Equipment {obj} has {f}={v} which was eliminated "
                        "during reduction — protection logic failed."
                    )
                updates[f] = new
        return obj.model_copy(update=updates) if updates else obj.model_copy()

    # Transformers list: keep ONLY those whose originals were never
    # touched (i.e. original_to_reduced_transformer[t] is None because
    # the transformer never entered the graph, usually due to missing
    # endpoints).  Absorbed transformers are represented as part of the
    # reduced_lines list above.
    transformers_out = []
    for t_idx, tf in enumerate(original_transformers):
        if original_to_reduced_transformer[t_idx] is None:
            # Never touched (pass-through). Remap bus indices.
            transformers_out.append(_remap(tf))

    acdc = [
        _remap(ac) for ac in (getattr(sys_config, "acdc_converters", None) or [])
    ]
    freq = [
        _remap(fc) for fc in (getattr(sys_config, "freq_converters", None) or [])
    ]

    reduced_config = sys_config.model_copy(update={
        "buses": reduced_buses,
        "transmission_lines_geo": reduced_lines,
        "transformers": transformers_out,
        "acdc_converters": acdc,
        "freq_converters": freq,
    })

    # ── Assemble ReductionMap ──
    red_map = ReductionMap(
        n_original_buses=len(original_buses),
        n_reduced_buses=len(reduced_buses),
        original_bus_ids=[b.bus_id for b in original_buses],
        retained_original_indices=retained_original_indices,
        original_to_reduced_bus=original_to_reduced_bus,
        pruned_buses=pruned_public,
        n_original_lines=len(original_lines),
        n_reduced_lines=len(reduced_lines),
        original_line_ids=[l.line_id or f"line_{i}" for i, l in enumerate(original_lines)],
        merged_lines=merged_lines_map,
        original_to_reduced_line=original_to_reduced_line,
        transformation_log=list(graph.log),
    )
    # Attach transformer mapping (Phase 2).  Stored as a side channel so
    # consumers that don't need it (simple line-only reductions) aren't
    # affected.  n_original_transformers enables symmetry checks.
    red_map.n_original_transformers = len(original_transformers)  # type: ignore[attr-defined]
    red_map.original_to_reduced_transformer = original_to_reduced_transformer  # type: ignore[attr-defined]
    red_map.n_absorbed_transformers = sum(  # type: ignore[attr-defined]
        1 for m in original_to_reduced_transformer if m is not None
    )
    return reduced_config, red_map


def reduce_network(
    sys_config: "SystemConfig",
    *,
    kron_deg3: bool = False,
) -> tuple["SystemConfig", ReductionMap]:
    """Produce a reduced system config together with a reversible map.

    The returned ``SystemConfig`` contains fewer buses and fewer
    transmission lines, but the same generators, batteries, technologies,
    battery technologies, nodes (at the node level), transformers and
    converters (with bus indices remapped).

    Equipment objects and per-node data are not touched — only the
    bus-level transmission topology.

    Parameters
    ----------
    sys_config
        Original system configuration (typically the merged system after
        :meth:`Runner._merge_systems`).

    Returns
    -------
    reduced_config : SystemConfig
    reduction_map : ReductionMap
    """
    buses = sys_config.buses or []
    if not buses:
        # Nothing to reduce; return an identity map.
        return sys_config, _identity_reduction_map(sys_config)

    protected = _identify_protected_buses(sys_config)
    logger.info(
        "Network reduction start: %d buses, %d lines, %d protected",
        len(buses),
        len(sys_config.transmission_lines_geo or []),
        len(protected),
    )

    graph, skipped, n_lines, n_tf = _build_graph(sys_config, protected)
    _fixed_point_reduce(graph, enable_kron_deg3=kron_deg3)
    reduced_config, red_map = _build_reduced_system(
        sys_config, graph, skipped, n_lines, n_tf,
    )
    n_abs = getattr(red_map, "n_absorbed_transformers", 0)
    logger.info(
        "Network reduction complete: %s, transformers %d→%d (absorbed %d)",
        red_map.summary(), n_tf, len(reduced_config.transformers or []), n_abs,
    )
    return reduced_config, red_map


def _identity_reduction_map(sys_config: "SystemConfig") -> ReductionMap:
    """Identity map for systems with no buses (single-bus-per-node case)."""
    n_tf = len(sys_config.transformers or [])
    rm = ReductionMap(
        n_original_buses=0,
        n_reduced_buses=0,
        original_bus_ids=[],
        retained_original_indices=[],
        original_to_reduced_bus=[],
        pruned_buses={},
        n_original_lines=len(sys_config.transmission_lines_geo or []),
        n_reduced_lines=len(sys_config.transmission_lines_geo or []),
        original_line_ids=[
            l.line_id or f"line_{i}"
            for i, l in enumerate(sys_config.transmission_lines_geo or [])
        ],
        merged_lines=[],
        original_to_reduced_line=[
            (i, +1, 1.0) for i in range(len(sys_config.transmission_lines_geo or []))
        ],
    )
    rm.n_original_transformers = n_tf  # type: ignore[attr-defined]
    rm.original_to_reduced_transformer = [None] * n_tf  # type: ignore[attr-defined]
    rm.n_absorbed_transformers = 0  # type: ignore[attr-defined]
    return rm
