"""Reversible network reduction map.

Stores all information needed to expand a reduced-network LP solution
(flows, angles, nodal prices) back to the original bus/line topology.

Invariants
----------
- Each original bus is either **retained** (appears in the reduced
  network) or **eliminated** (recorded in ``pruned_buses``).
- Each original line is referenced by exactly one :class:`MergedLine`
  (possibly by itself when it was not merged).
- For a series collapse, all original lines in the chain share the same
  flow magnitude with appropriate signs.
- For a parallel merge, the flow on each original line is
  ``flow_reduced * (1/x_original) / sum(1/x_i)`` — i.e. proportional to
  admittance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OriginalLineRef:
    """Reference from a merged line back to one of its original lines.

    Attributes
    ----------
    line_idx
        Index into the original line list.
    direction
        ``+1`` if the original line's from→to orientation matches the
        merged line's from→to orientation, ``-1`` if reversed.
    flow_share
        Multiplier applied to the merged line's flow to obtain this
        original line's flow.  Equals 1.0 for series merges; for parallel
        merges equals ``(1/x_original) / sum(1/x_i)``.
    """

    line_idx: int
    direction: int  # +1 or -1
    flow_share: float


@dataclass
class MergedLine:
    """A line in the reduced network, representing one or more original lines.

    A ``MergedLine`` carrying exactly one :class:`OriginalLineRef` with
    ``flow_share=1.0`` and ``direction=+1`` corresponds to an unmerged
    original line.
    """

    reduced_line_idx: int
    from_bus_reduced: int
    to_bus_reduced: int
    reactance_pu: float
    resistance_pu: float
    capacity_mw: float
    length_km: float
    num_circuits: int
    originals: list[OriginalLineRef]

    def is_single(self) -> bool:
        """True when this merged line wraps exactly one original line."""
        return len(self.originals) == 1


@dataclass
class PrunedBus:
    """A bus that was eliminated during reduction.

    For **leaf pruning**: ``angle_source_bus`` is the single retained
    neighbour (zero voltage drop because no injection).

    For **series collapse**: ``inside_merged_line`` points to the merged
    line that absorbed this bus's two adjacent lines.  The angle of this
    bus is recovered by linear interpolation of the two endpoints'
    angles weighted by admittance.

    For **kron_deg3**: ``kron_neighbours`` lists the three original
    neighbour bus indices and the admittance (y=1/x) of the leg
    connecting them to the eliminated bus.  With zero net injection
    at the pruned bus, its angle is the admittance-weighted average of
    the neighbour angles:

        θ_pruned = Σ (y_i · θ_{n_i}) / Σ y_i
    """

    original_bus_idx: int
    # Angle recovery strategy:
    # - leaf: inherit directly from angle_source_bus (no drop)
    # - series: interpolate from series_endpoints using series_admittance_fractions
    # - kron_deg3: admittance-weighted average of 3 neighbours
    angle_source_bus: Optional[int] = None  # for leaves
    inside_merged_line: Optional[int] = None  # for series collapse (reduced_line_idx)
    # For series collapse: (endpoint_bus_original_idx, admittance_fraction)
    # where admittance_fraction is the share of reactance from this bus to the endpoint
    # out of the total series reactance.  The eliminated bus's angle is:
    #   θ_pruned = θ_from * frac_from + θ_to * (1 - frac_from)
    # with frac_from being the reactance fraction on the "to" side.
    series_from_endpoint_original: Optional[int] = None
    series_to_endpoint_original: Optional[int] = None
    series_reactance_fraction_from_to: Optional[float] = None  # x_to_side / x_total
    # For kron_deg3: list of (neighbour_original_bus, admittance) tuples
    kron_neighbours: Optional[list] = None  # list[tuple[int, float]]


@dataclass
class ReductionMap:
    """Reversible mapping between original and reduced topology.

    This object is produced by :func:`network_reducer.reduce_network`
    and consumed by :class:`result_expander.ResultExpander`.
    """

    # ── Bus side ──
    n_original_buses: int
    n_reduced_buses: int
    original_bus_ids: list[str]
    # retained_original_indices[reduced_idx] = original_idx
    retained_original_indices: list[int]
    # original_to_reduced[original_idx] = reduced_idx or None
    original_to_reduced_bus: list[Optional[int]]

    # ── Line side ──
    n_original_lines: int
    n_reduced_lines: int
    original_line_ids: list[str]

    # ── Fields with defaults (must come after required fields) ──
    pruned_buses: dict[int, PrunedBus] = field(default_factory=dict)
    merged_lines: list[MergedLine] = field(default_factory=list)
    # original_to_reduced_line[original_idx] = (reduced_idx, direction, share)
    original_to_reduced_line: list[Optional[tuple[int, int, float]]] = field(
        default_factory=list
    )
    transformation_log: list[str] = field(default_factory=list)

    def reduction_ratio_buses(self) -> float:
        if self.n_original_buses == 0:
            return 1.0
        return self.n_reduced_buses / self.n_original_buses

    def reduction_ratio_lines(self) -> float:
        if self.n_original_lines == 0:
            return 1.0
        return self.n_reduced_lines / self.n_original_lines

    def summary(self) -> str:
        return (
            f"ReductionMap: buses {self.n_original_buses}→{self.n_reduced_buses} "
            f"({100*self.reduction_ratio_buses():.1f}%), "
            f"lines {self.n_original_lines}→{self.n_reduced_lines} "
            f"({100*self.reduction_ratio_lines():.1f}%), "
            f"{len(self.pruned_buses)} pruned, "
            f"{len(self.transformation_log)} transformations"
        )
