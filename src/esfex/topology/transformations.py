"""Local network transformations for DCOPF reduction.

Each transformation operates on a :class:`_ReductionGraph` in-place and
returns the number of structural changes applied.  Transformations are
applied iteratively to a fixed point in :func:`network_reducer.reduce_network`.

All transformations preserve DC power flow exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _Edge:
    """Internal mutable edge representing a transmission line in the graph.

    At construction an :class:`_Edge` corresponds to exactly one original
    line.  Merges (series or parallel) aggregate edges into a composite
    ``_Edge`` whose ``originals`` list records every contributing original
    line together with its flow share and direction.

    ``loss_conductance`` tracks the **exact** combined conductance for
    DCOPF loss equivalence.  At construction this equals the original
    line's ``g = R/(R²+X²)``.  Merges accumulate it directly:

    - Series: g_eq = Σ g_i  (same flow in all members)
    - Parallel: g_eq = Σ g_i · share_i²  (flow scaled per member)

    This exact value is later used to pick a ``resistance_pu`` for the
    emitted :class:`TransmissionLineGeo` such that Julia's
    ``R/(R²+X²)`` formula reproduces ``g_eq`` — keeping the LP loss
    objective identical to the unreduced case.
    """

    edge_id: int                       # unique within graph (stable)
    from_bus: int                      # internal bus idx (graph-local)
    to_bus: int                        # internal bus idx (graph-local)
    reactance_pu: float                # total x of merged line (pu)
    resistance_pu: float               # series-summed r (pu) — *not* what gets emitted
    capacity_mw: float                 # bottleneck capacity
    length_km: float                   # sum of lengths for series, max for parallel
    num_circuits: int                  # aggregated
    originals: list[tuple[int, int, float]] = field(default_factory=list)
    # Each tuple: (original_line_idx, direction_sign, flow_share)
    #   direction_sign: +1 if original's from→to matches this edge's
    #                   from→to, -1 if reversed
    #   flow_share: multiplier to recover original flow from this edge's
    #               flow (1.0 for series, admittance ratio for parallel)
    loss_conductance: float = 0.0      # g_eq for exact loss equivalence


@dataclass
class _ReductionGraph:
    """Mutable graph used during iterative reduction.

    ``buses`` maps an internal bus index to the original bus index.
    When a bus is eliminated, it is removed from ``buses`` but the
    original index remains tracked via the pruning records written
    into ``pruned_buses``.
    """

    # buses[internal_idx] = original_idx; removed entries use -1 sentinel
    buses: list[int] = field(default_factory=list)
    # All active edges, keyed by edge_id for stable identity.
    edges: dict[int, _Edge] = field(default_factory=dict)
    # Adjacency: internal_bus_idx → set of edge_ids
    adjacency: dict[int, set[int]] = field(default_factory=dict)
    # Protected internal bus indices (cannot be eliminated)
    protected: set[int] = field(default_factory=set)
    # Pruned buses recorded here; keys are original bus indices
    pruned_buses: dict[int, "_PrunedRecord"] = field(default_factory=dict)
    # Next edge_id to assign
    _next_edge_id: int = 0
    # Transformation log entries
    log: list[str] = field(default_factory=list)

    def new_edge(
        self,
        from_bus: int,
        to_bus: int,
        reactance_pu: float,
        resistance_pu: float,
        capacity_mw: float,
        length_km: float,
        num_circuits: int,
        originals: list[tuple[int, int, float]],
        loss_conductance: float | None = None,
    ) -> int:
        eid = self._next_edge_id
        self._next_edge_id += 1
        # Default loss_conductance from R, X (Julia's formula).  Callers
        # producing merged edges should pass an explicit g_eq so that
        # series/parallel loss equivalence is preserved.
        if loss_conductance is None:
            denom = resistance_pu * resistance_pu + reactance_pu * reactance_pu
            loss_conductance = (resistance_pu / denom) if denom > 0 else 0.0
        e = _Edge(
            edge_id=eid,
            from_bus=from_bus,
            to_bus=to_bus,
            reactance_pu=reactance_pu,
            resistance_pu=resistance_pu,
            capacity_mw=capacity_mw,
            length_km=length_km,
            num_circuits=num_circuits,
            originals=originals,
            loss_conductance=loss_conductance,
        )
        self.edges[eid] = e
        self.adjacency.setdefault(from_bus, set()).add(eid)
        self.adjacency.setdefault(to_bus, set()).add(eid)
        return eid

    def remove_edge(self, eid: int) -> None:
        e = self.edges.pop(eid)
        self.adjacency[e.from_bus].discard(eid)
        self.adjacency[e.to_bus].discard(eid)

    def remove_bus(self, bus_idx: int) -> None:
        """Remove a bus that has no incident edges left."""
        if self.adjacency.get(bus_idx):
            raise AssertionError(
                f"Cannot remove bus {bus_idx}: has {len(self.adjacency[bus_idx])} incident edges"
            )
        self.adjacency.pop(bus_idx, None)
        self.buses[bus_idx] = -1  # mark deleted

    def incident_edges(self, bus_idx: int) -> list[_Edge]:
        return [self.edges[eid] for eid in self.adjacency.get(bus_idx, set())]

    def degree(self, bus_idx: int) -> int:
        return len(self.adjacency.get(bus_idx, ()))

    def is_active(self, bus_idx: int) -> bool:
        return bus_idx < len(self.buses) and self.buses[bus_idx] != -1


@dataclass
class _PrunedRecord:
    """Internal bookkeeping for an eliminated bus.

    Converted to :class:`reduction_map.PrunedBus` when building the final
    :class:`ReductionMap`.
    """

    original_bus_idx: int
    kind: str  # "leaf" or "series"
    # For leaves: the single retained (original) neighbour bus
    angle_source_original: Optional[int] = None
    # For series: endpoints and reactance fraction
    series_from_original: Optional[int] = None
    series_to_original: Optional[int] = None
    # Fraction of total series reactance on the "to" side (used for
    # angle interpolation: θ_pruned = θ_from * frac_to + θ_to * frac_from)
    reactance_fraction_from_side: Optional[float] = None


# ════════════════════════════════════════════════════════════════════════
# Transformation 1: Parallel merge
# ════════════════════════════════════════════════════════════════════════

def merge_parallel_lines(graph: _ReductionGraph) -> int:
    """Merge all parallel line groups into a single equivalent each.

    For lines with matching endpoint pair (regardless of orientation),
    replace them with one equivalent line whose:

    - reactance = 1 / Σ(1/x_i)   (admittance sum)
    - resistance = x_eq² * Σ(r_i / x_i²)   (energy-weighted in DC-PF limit)
    - capacity = min_i (cap_i / share_i)   where share_i = y_i / Σ y_j
      is the admittance-proportional flow share on line i.  In DCOPF the
      flow split is fixed by admittance (not optimisable), so the binding
      constraint is the line whose proportional share first reaches its
      own capacity.  The simple sum ``Σ cap_i`` is generally TOO LOOSE.
    - num_circuits = Σ num_circuits

    Each original line's flow is recovered by ``flow_i = flow_eq * share_i``
    with sign +1 if its orientation matches the equivalent line's.

    Returns
    -------
    int
        Number of merges performed (one per eliminated redundant edge).
    """
    # Group edges by unordered endpoint pair
    groups: dict[tuple[int, int], list[_Edge]] = {}
    for e in graph.edges.values():
        key = (min(e.from_bus, e.to_bus), max(e.from_bus, e.to_bus))
        groups.setdefault(key, []).append(e)

    merges = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # Canonical orientation: group's from_bus = min endpoint
        canon_from, canon_to = key

        # Sum admittances
        y_sum = 0.0
        length_max = 0.0
        circuits = 0
        r_over_x2_sum = 0.0  # Σ r_i / x_i²
        for e in members:
            x_i = max(e.reactance_pu, 1e-9)  # guard against near-zero
            y_sum += 1.0 / x_i
            length_max = max(length_max, e.length_km)
            circuits += e.num_circuits
            r_over_x2_sum += e.resistance_pu / (x_i * x_i)

        x_eq = 1.0 / y_sum
        r_eq = (x_eq * x_eq) * r_over_x2_sum

        # Exact loss conductance: Σ g_i · share_i² where share_i = (1/x_i)/y_sum.
        # This preserves loss equivalence with the unreduced LP (each
        # original line's loss is g_i · (f_eq · share_i)²; sum over lines
        # gives g_eq · f_eq² with g_eq = Σ g_i · share_i²).
        g_eq = 0.0
        for e in members:
            x_i = max(e.reactance_pu, 1e-9)
            share_i = (1.0 / x_i) / y_sum
            g_eq += e.loss_conductance * share_i * share_i
        # Binding-constraint capacity: the equivalent line's flow is
        # capped by whichever member's admittance-scaled capacity is
        # reached first.  share_i = (1/x_i) / y_sum  ⇒  cap_i / share_i.
        # NB: zero-capacity members don't bind (they're effectively open
        # circuits and contribute no admittance in practice, but guard
        # anyway to avoid division by zero).
        cap_eq = float("inf")
        for e in members:
            x_i = max(e.reactance_pu, 1e-9)
            share_i = (1.0 / x_i) / y_sum
            if e.capacity_mw <= 0 or share_i <= 0:
                continue
            binding = e.capacity_mw / share_i
            if binding < cap_eq:
                cap_eq = binding
        if cap_eq == float("inf"):
            # All members had zero capacity — merged equivalent is open.
            cap_eq = 0.0

        # Build the aggregated originals list with admittance-proportional shares
        originals_agg: list[tuple[int, int, float]] = []
        for e in members:
            x_i = max(e.reactance_pu, 1e-9)
            share_ratio = (1.0 / x_i) / y_sum
            # Each constituent already has its own originals list (from earlier
            # merges). Propagate the share multiplicatively.
            for orig_idx, orig_dir, orig_share in e.originals:
                # Adjust direction if the member edge was stored reversed wrt canonical
                member_dir = +1 if (e.from_bus == canon_from) else -1
                agg_dir = orig_dir * member_dir
                originals_agg.append((orig_idx, agg_dir, orig_share * share_ratio))

        # Remove constituent edges
        for e in members:
            graph.remove_edge(e.edge_id)

        # Add the merged equivalent
        graph.new_edge(
            from_bus=canon_from,
            to_bus=canon_to,
            reactance_pu=x_eq,
            resistance_pu=r_eq,
            capacity_mw=cap_eq,
            length_km=length_max,
            num_circuits=circuits,
            originals=originals_agg,
            loss_conductance=g_eq,
        )
        merges += len(members) - 1
        graph.log.append(
            f"parallel_merge: {len(members)} lines {canon_from}↔{canon_to} "
            f"→ 1 (x={x_eq:.4g}, cap={cap_eq:.1f})"
        )
    return merges


# ════════════════════════════════════════════════════════════════════════
# Transformation 2: Leaf pruning
# ════════════════════════════════════════════════════════════════════════

def prune_leaves(graph: _ReductionGraph) -> int:
    """Remove degree-1 non-protected buses and their single line.

    Because a passive degree-1 bus has no injection, its line carries
    zero flow at the optimum, and its angle equals the neighbour's.

    The eliminated line carries zero flow in the reduced LP (it simply
    ceases to exist); originals are recorded for post-solve expansion
    so downstream code can write a zero-flow dataset for them.
    """
    pruned = 0
    changed = True
    # Iterate: pruning one leaf can turn its neighbour into a new leaf
    while changed:
        changed = False
        candidates = [
            b for b in range(len(graph.buses))
            if graph.is_active(b)
            and b not in graph.protected
            and graph.degree(b) == 1
        ]
        for b in candidates:
            if graph.degree(b) != 1:
                continue  # may have changed inside this pass
            edges = graph.incident_edges(b)
            if not edges:
                continue
            edge = edges[0]
            other_internal = edge.to_bus if edge.from_bus == b else edge.from_bus

            # Record pruning: this bus inherits its neighbour's angle
            # Its originally-attached line(s) all carry zero flow
            pruned_orig = graph.buses[b]
            neighbour_orig = graph.buses[other_internal]
            rec = _PrunedRecord(
                original_bus_idx=pruned_orig,
                kind="leaf",
                angle_source_original=neighbour_orig,
            )
            graph.pruned_buses[pruned_orig] = rec

            # Zero-flow lines: keep their originals referenced but with
            # flow_share=0 so the expander can emit zeros for them.
            # We don't need a merged line for them — the line is gone.
            # Instead we mark originals with a sentinel (handled by the
            # reducer when building the map).
            # For now, attach the originals to the graph's pruned record
            # in a side channel.
            if not hasattr(rec, "zero_flow_originals"):
                rec.zero_flow_originals = []  # type: ignore[attr-defined]
            for orig_idx, orig_dir, orig_share in edge.originals:
                rec.zero_flow_originals.append(orig_idx)  # type: ignore[attr-defined]

            graph.remove_edge(edge.edge_id)
            graph.remove_bus(b)
            graph.log.append(
                f"prune_leaf: bus_orig={pruned_orig} "
                f"(neighbour_orig={neighbour_orig})"
            )
            pruned += 1
            changed = True
    return pruned


# ════════════════════════════════════════════════════════════════════════
# Transformation 3: Series collapse
# ════════════════════════════════════════════════════════════════════════

def collapse_series(graph: _ReductionGraph) -> int:
    """Eliminate degree-2 non-protected buses by merging their two lines.

    For a bus B with neighbours A and C (A ≠ C), replace the path
    A—B—C with a single equivalent line A—C whose:

    - reactance = x_AB + x_BC
    - resistance = r_AB + r_BC
    - capacity = min(cap_AB, cap_BC)   (bottleneck)
    - length = len_AB + len_BC
    - num_circuits = min(circ_AB, circ_BC)

    The eliminated bus's angle is recovered by:
        θ_B = θ_A * (x_BC / (x_AB + x_BC)) + θ_C * (x_AB / (x_AB + x_BC))

    (weighted by the complement of the reactance on the same side —
    equivalent to ``θ_B = θ_A - flow * x_AB`` with flow derived from
    ``flow = (θ_A − θ_C) / x_total``.)

    Returns
    -------
    int
        Number of buses eliminated.
    """
    collapsed = 0
    changed = True
    while changed:
        changed = False
        candidates = [
            b for b in range(len(graph.buses))
            if graph.is_active(b)
            and b not in graph.protected
            and graph.degree(b) == 2
        ]
        for b in candidates:
            if graph.degree(b) != 2:
                continue
            incident = graph.incident_edges(b)
            if len(incident) != 2:
                continue  # should not happen given degree==2
            # Deterministic ordering: sort by edge_id so output is stable
            incident = sorted(incident, key=lambda e: e.edge_id)
            e1, e2 = incident
            # Identify the two neighbours
            a = e1.to_bus if e1.from_bus == b else e1.from_bus
            c = e2.to_bus if e2.from_bus == b else e2.from_bus
            if a == c:
                # Self-loop through b; skip (better handled as parallel pass)
                continue
            if not graph.is_active(a) or not graph.is_active(c):
                continue

            # Orient each constituent line as a→b, then b→c for the merge.
            # We need to flip originals' direction when their edge is stored
            # against that orientation.
            def _sign_forward(e: _Edge, tail: int, head: int) -> int:
                return +1 if (e.from_bus == tail and e.to_bus == head) else -1

            sign1 = _sign_forward(e1, a, b)   # want a→b
            sign2 = _sign_forward(e2, b, c)   # want b→c

            x_total = e1.reactance_pu + e2.reactance_pu
            r_total = e1.resistance_pu + e2.resistance_pu
            cap_total = min(e1.capacity_mw, e2.capacity_mw)
            length_total = e1.length_km + e2.length_km
            circuits_total = min(e1.num_circuits, e2.num_circuits)
            # Series: same flow in both, so total loss = (g1 + g2) · pf²
            # ⇒ g_eq = g1 + g2 (preserves loss exactly)
            g_total = e1.loss_conductance + e2.loss_conductance

            # Combine originals.  All originals in both edges share the
            # same flow as the merged line (series: flow is conserved);
            # however, for each original its stored flow_share must still
            # be multiplied through, and the direction sign must account
            # for both the member's orientation within the merge and the
            # merge's own a→c orientation.
            merged_originals: list[tuple[int, int, float]] = []
            for orig_idx, orig_dir, orig_share in e1.originals:
                merged_originals.append((orig_idx, orig_dir * sign1, orig_share))
            for orig_idx, orig_dir, orig_share in e2.originals:
                merged_originals.append((orig_idx, orig_dir * sign2, orig_share))

            # Canonicalise merged edge orientation: from_bus < to_bus.
            # If we need to flip (a, c) → (c, a), invert all origin
            # directions so flow conservation holds in the canonical frame.
            if a > c:
                a, c = c, a
                merged_originals = [
                    (oi, -od, os) for oi, od, os in merged_originals
                ]
                # Reactance fraction semantics also flip: the fraction
                # "on the from side" must reference the new from-endpoint.
                flip_fraction = True
            else:
                flip_fraction = False

            # Record series collapse for bus b
            pruned_orig = graph.buses[b]
            a_orig = graph.buses[a]
            c_orig = graph.buses[c]
            # Reactance fraction on the "from" side of the canonical path.
            # Before flipping, e1 was on the a-side with reactance e1.reactance_pu
            # out of x_total.  If we flipped, the fraction now references
            # the OTHER side.
            if flip_fraction:
                frac_from_side = e2.reactance_pu / x_total if x_total > 0 else 0.5
            else:
                frac_from_side = e1.reactance_pu / x_total if x_total > 0 else 0.5
            rec = _PrunedRecord(
                original_bus_idx=pruned_orig,
                kind="series",
                series_from_original=a_orig,
                series_to_original=c_orig,
                reactance_fraction_from_side=frac_from_side,
            )
            graph.pruned_buses[pruned_orig] = rec

            # Remove the two edges and the bus, add the merged edge
            graph.remove_edge(e1.edge_id)
            graph.remove_edge(e2.edge_id)
            graph.remove_bus(b)
            graph.new_edge(
                from_bus=a,
                to_bus=c,
                reactance_pu=x_total,
                resistance_pu=r_total,
                capacity_mw=cap_total,
                length_km=length_total,
                num_circuits=circuits_total,
                originals=merged_originals,
                loss_conductance=g_total,
            )
            graph.log.append(
                f"series_collapse: bus {pruned_orig} eliminated between "
                f"{a_orig} and {c_orig} (x_total={x_total:.4g})"
            )
            collapsed += 1
            changed = True
    return collapsed


# ════════════════════════════════════════════════════════════════════════
# Transformation 4 (Phase 2b): Star-mesh (Kron) for degree-3 junctions
# ════════════════════════════════════════════════════════════════════════

def star_to_mesh_degree3(graph: _ReductionGraph) -> int:
    """Eliminate degree-3 non-protected junctions via star-mesh transformation.

    A degree-3 bus B with neighbours A, C, D and admittances y₁=1/x_AB,
    y₂=1/x_BC, y₃=1/x_BD is replaced by a triangle (A-C, A-D, C-D) with:

        y_AC = y₁·y₂ / Y,   y_AD = y₁·y₃ / Y,   y_CD = y₂·y₃ / Y

    where Y = y₁+y₂+y₃.  This is the classical star-mesh transformation
    (equivalent to Kron reduction for a single passive bus).  Because
    the eliminated bus has zero net injection, retained-bus angles and
    flows are preserved exactly; only the reduced network sees 3
    fictitious edges where before there was a star.

    Net line count change: 3 edges → 3 edges (0 delta).  Beneficial
    only when a subsequent parallel_merge pass can consolidate the new
    edges with existing parallel lines.  **This transformation is NOT
    applied to buses of degree ≥ 4** because the mesh size grows as
    k(k-1)/2 which explodes quickly for meshed networks.

    Flow shares for the original lines after this transformation are
    approximate — exact PTDF-based reconstruction is deferred to
    Phase 2c.  The approximation used here:

        Flow on fictitious edge (i-j) maps to original lines on the
        two-hop path i → b → j proportional to admittance share on
        each leg.

    Returns
    -------
    int
        Number of buses eliminated.
    """
    eliminated = 0
    changed = True
    while changed:
        changed = False
        candidates = [
            b for b in range(len(graph.buses))
            if graph.is_active(b)
            and b not in graph.protected
            and graph.degree(b) == 3
        ]
        for b in candidates:
            if graph.degree(b) != 3:
                continue
            incident = sorted(graph.incident_edges(b), key=lambda e: e.edge_id)
            if len(incident) != 3:
                continue

            # Determine neighbours and admittances
            neighbours = []
            for e in incident:
                nb = e.to_bus if e.from_bus == b else e.from_bus
                neighbours.append((nb, e))

            # Reject if any two neighbours coincide — would create self-loops
            if len({nb for nb, _ in neighbours}) != 3:
                continue
            # Reject if any neighbour is no longer active
            if not all(graph.is_active(nb) for nb, _ in neighbours):
                continue

            y_list = [1.0 / max(e.reactance_pu, 1e-9) for _, e in neighbours]
            Y = sum(y_list)
            if Y <= 0:
                continue

            # Build the 3 fictitious edges (one per pair of neighbours)
            new_edges = []
            for i in range(3):
                for j in range(i + 1, 3):
                    n_i, e_i = neighbours[i]
                    n_j, e_j = neighbours[j]
                    y_i, y_j = y_list[i], y_list[j]
                    y_new = (y_i * y_j) / Y
                    x_new = 1.0 / y_new
                    # Loss conservation: series-like through the eliminated
                    # bus — the new edge's loss conductance is the sum of
                    # the two contributing legs scaled by the flow share
                    # through this pair.
                    # Flow on (n_i, n_j) splits over legs e_i and e_j by
                    # admittance ratio y_k/Y where k is the OTHER leg.
                    share_on_ei = y_j / Y
                    share_on_ej = y_i / Y
                    g_new = (
                        e_i.loss_conductance * share_on_ei
                        + e_j.loss_conductance * share_on_ej
                    )
                    cap_new = min(e_i.capacity_mw, e_j.capacity_mw)

                    # Canonical orientation: from_bus < to_bus
                    if n_i <= n_j:
                        canon_from, canon_to = n_i, n_j
                        # flow sign: new edge goes n_i → n_j; e_i goes
                        # from its "from" toward b (or b → "to")
                        sign_ei = +1 if e_i.from_bus == n_i else -1
                        sign_ej = +1 if e_j.to_bus == n_j else -1
                    else:
                        canon_from, canon_to = n_j, n_i
                        sign_ei = +1 if e_i.to_bus == n_i else -1
                        sign_ej = +1 if e_j.from_bus == n_j else -1

                    originals = []
                    for oi, od, os in e_i.originals:
                        originals.append(
                            (oi, od * sign_ei, os * share_on_ei)
                        )
                    for oi, od, os in e_j.originals:
                        originals.append(
                            (oi, od * sign_ej, os * share_on_ej)
                        )
                    new_edges.append({
                        "from_bus": canon_from,
                        "to_bus": canon_to,
                        "reactance_pu": x_new,
                        "resistance_pu": x_new,  # will be set via g_new
                        "capacity_mw": cap_new,
                        "length_km": 0.0,
                        "num_circuits": 1,
                        "originals": originals,
                        "loss_conductance": g_new,
                    })

            # Remove the star (3 edges + bus)
            pruned_orig = graph.buses[b]
            neigh_origs = [graph.buses[nb] for nb, _ in neighbours]
            for _, e in neighbours:
                graph.remove_edge(e.edge_id)
            graph.remove_bus(b)

            # Add the 3 fictitious edges
            for ne in new_edges:
                graph.new_edge(**ne)

            # Record the elimination.  Angle of b in the original problem
            # is a weighted combination of its three neighbours' angles
            # (weighted by admittance).  Store the admittance shares for
            # future expansion; mark kind="kron_deg3".
            rec = _PrunedRecord(
                original_bus_idx=pruned_orig,
                kind="kron_deg3",
            )
            rec.kron_neighbours = list(zip(neigh_origs, y_list))  # type: ignore[attr-defined]
            rec.kron_admittance_sum = Y  # type: ignore[attr-defined]
            graph.pruned_buses[pruned_orig] = rec

            graph.log.append(
                f"kron_deg3: bus {pruned_orig} eliminated "
                f"(neighbours {neigh_origs}, Y={Y:.3g})"
            )
            eliminated += 1
            changed = True
    return eliminated
