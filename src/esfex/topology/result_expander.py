"""Post-solve expansion of reduced-network LP results to original topology.

Given a :class:`ReductionMap` and the solver output arrays indexed on the
reduced network, produce equivalent arrays indexed on the original bus
and line ordering.  The expansion is algebraic and exact for DCOPF:

- **Retained bus angles**: copy the reduced angle directly.
- **Leaf-pruned bus angles**: inherit the neighbour's angle (no drop
  because the leaf carries no injection in the original problem).
- **Series-collapsed bus angles**: linear interpolation of the two
  series endpoints' angles weighted by reactance fractions.
- **Unmerged line flows**: copy the reduced flow (with direction sign).
- **Series-merged line flows**: every original line in the chain sees
  the same merged flow (signed by its stored direction).
- **Parallel-merged line flows**: split by admittance ratio.
- **Zero-flow (leaf-pruned) line flows**: emit zeros.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from esfex.topology.reduction_map import ReductionMap

logger = logging.getLogger(__name__)


class ResultExpander:
    """Expand reduced-network LP solution arrays to the original topology.

    Parameters
    ----------
    reduction_map
        The map produced by :func:`network_reducer.reduce_network`.
    """

    def __init__(self, reduction_map: ReductionMap):
        self.rm = reduction_map

    # ── Bus-indexed arrays ────────────────────────────────────────────

    def expand_bus_array(
        self,
        reduced_array: np.ndarray,
        axis: int = 0,
        copy_from_neighbour: bool = True,
    ) -> np.ndarray:
        """Expand an array indexed by reduced buses to original buses.

        Parameters
        ----------
        reduced_array
            Array with shape ``(..., n_reduced_buses, ...)``.
        axis
            Bus axis (default 0).
        copy_from_neighbour
            If True (default), leaf buses inherit the reduced value of
            their neighbour and series-collapsed buses receive the
            admittance-weighted interpolation.  If False, eliminated
            buses are filled with NaN.
        """
        if reduced_array is None:
            return None
        if self.rm.n_reduced_buses == 0 and self.rm.n_original_buses == 0:
            # Identity case
            return reduced_array

        n_orig = self.rm.n_original_buses
        new_shape = list(reduced_array.shape)
        new_shape[axis] = n_orig
        expanded = np.zeros(new_shape, dtype=reduced_array.dtype)

        # Move bus axis to front for slicing convenience
        red = np.moveaxis(reduced_array, axis, 0)
        out = np.moveaxis(expanded, axis, 0)

        # Retained buses: direct copy
        for orig_idx in self.rm.retained_original_indices:
            red_idx = self.rm.original_to_reduced_bus[orig_idx]
            out[orig_idx] = red[red_idx]

        if copy_from_neighbour:
            # Leaves: copy neighbour's value
            # Series-collapsed: interpolate.  We need multi-pass because a
            # series-collapsed bus's endpoints may themselves be
            # series-collapsed (they won't, because series-collapsed
            # endpoints are always retained; the endpoints stored are
            # ORIGINAL indices of retained buses).  But a leaf's
            # neighbour could be another eliminated bus in a pathological
            # case — resolve iteratively.
            pending = list(self.rm.pruned_buses.values())
            max_passes = len(pending) + 1
            for _ in range(max_passes):
                still_pending = []
                for rec in pending:
                    if rec.angle_source_bus is not None:
                        src = rec.angle_source_bus
                        src_red = self.rm.original_to_reduced_bus[src]
                        if src_red is not None:
                            out[rec.original_bus_idx] = red[src_red]
                        else:
                            still_pending.append(rec)
                    elif (
                        rec.series_from_endpoint_original is not None
                        and rec.series_to_endpoint_original is not None
                    ):
                        f = rec.series_from_endpoint_original
                        t = rec.series_to_endpoint_original
                        f_red = self.rm.original_to_reduced_bus[f]
                        t_red = self.rm.original_to_reduced_bus[t]
                        if f_red is not None and t_red is not None:
                            frac_from = rec.series_reactance_fraction_from_to or 0.5
                            # θ_pruned sits between θ_from and θ_to; the
                            # fraction gives the reactance share on the
                            # "from" side of the collapsed path, so the
                            # pruned bus is (1 - frac_from) of the way
                            # from "from" → "to":
                            out[rec.original_bus_idx] = (
                                red[f_red] * (1.0 - frac_from)
                                + red[t_red] * frac_from
                            )
                        else:
                            still_pending.append(rec)
                    elif rec.kron_neighbours:
                        # Admittance-weighted average: θ_b = Σ yᵢθ_nᵢ / Σ yᵢ
                        neigh_reduced = []
                        resolved = True
                        for n_orig, y_i in rec.kron_neighbours:
                            n_red = self.rm.original_to_reduced_bus[n_orig]
                            if n_red is None:
                                resolved = False
                                break
                            neigh_reduced.append((n_red, y_i))
                        if resolved and neigh_reduced:
                            total_y = sum(y for _, y in neigh_reduced)
                            acc = 0.0
                            for n_red, y_i in neigh_reduced:
                                acc = acc + red[n_red] * (y_i / total_y)
                            out[rec.original_bus_idx] = acc
                        else:
                            still_pending.append(rec)
                if not still_pending:
                    break
                if len(still_pending) == len(pending):
                    logger.warning(
                        "Could not resolve %d eliminated buses during expansion",
                        len(still_pending),
                    )
                    break
                pending = still_pending
        else:
            # Fill eliminated buses with NaN
            for orig_idx in self.rm.pruned_buses:
                out[orig_idx] = np.nan

        return np.moveaxis(out, 0, axis)

    # ── Line-indexed arrays ───────────────────────────────────────────

    def expand_line_array(
        self,
        reduced_array: np.ndarray,
        axis: int = 0,
    ) -> np.ndarray:
        """Expand a flow-like array indexed by reduced lines to original lines.

        For each original line ``i`` with mapping ``(reduced_idx, dir, share)``:

        - If ``reduced_idx == -1`` (leaf ghost): output is zero.
        - Else: ``out[i] = reduced[reduced_idx] * dir * share``.
        """
        if reduced_array is None:
            return None

        n_orig = self.rm.n_original_lines
        new_shape = list(reduced_array.shape)
        new_shape[axis] = n_orig
        expanded = np.zeros(new_shape, dtype=reduced_array.dtype)

        red = np.moveaxis(reduced_array, axis, 0)
        out = np.moveaxis(expanded, axis, 0)

        for orig_idx, mapping in enumerate(self.rm.original_to_reduced_line):
            if mapping is None:
                continue  # untouched (shouldn't happen if map is complete)
            red_idx, direction, share = mapping
            if red_idx < 0:
                # Ghost (leaf-pruned) line: stays zero
                continue
            if red_idx >= red.shape[0]:
                logger.warning(
                    "Line expansion: reduced_idx %d out of bounds (red shape %s)",
                    red_idx, red.shape,
                )
                continue
            out[orig_idx] = red[red_idx] * direction * share

        return np.moveaxis(out, 0, axis)

    def expand_line_plus_transformer_array(
        self,
        reduced_array: np.ndarray,
        axis: int = 0,
    ) -> np.ndarray:
        """Expand a flow array that concatenates original lines + transformers.

        Julia's DCOPF appends transformer branches after transmission lines
        in its per-line flow output (see ``transmission_dc.jl``).  This
        helper expands the reduced network's per-edge flow back to an
        array of shape ``(..., n_original_lines + n_original_transformers, ...)``
        matching the original Julia ordering.

        Indices ``[0, n_original_lines)`` are lines; ``[n_original_lines,
        n_original_lines + n_original_transformers)`` are transformers.
        """
        if reduced_array is None:
            return None

        n_lines = self.rm.n_original_lines
        n_tf = getattr(self.rm, "n_original_transformers", 0)
        n_out = n_lines + n_tf

        new_shape = list(reduced_array.shape)
        new_shape[axis] = n_out
        expanded = np.zeros(new_shape, dtype=reduced_array.dtype)

        red = np.moveaxis(reduced_array, axis, 0)
        out = np.moveaxis(expanded, axis, 0)

        def _apply(mapping, target_idx: int) -> None:
            if mapping is None:
                return
            red_idx, direction, share = mapping
            if red_idx < 0:
                return  # Ghost (leaf): stays zero
            if red_idx >= red.shape[0]:
                logger.warning(
                    "Expansion: reduced_idx %d out of bounds (red shape %s)",
                    red_idx, red.shape,
                )
                return
            out[target_idx] = red[red_idx] * direction * share

        for orig_idx, mapping in enumerate(self.rm.original_to_reduced_line):
            _apply(mapping, orig_idx)

        tf_map = getattr(self.rm, "original_to_reduced_transformer", None)
        if tf_map is not None:
            for t_idx, mapping in enumerate(tf_map):
                _apply(mapping, n_lines + t_idx)

        return np.moveaxis(out, 0, axis)

    # ── Convenience wrappers ──────────────────────────────────────────

    def expand_nodal_prices(
        self, reduced_prices: np.ndarray, axis: int = 0
    ) -> np.ndarray:
        """Expand LMPs.  Uses same logic as angles — eliminated buses
        inherit their neighbour's / series-weighted price."""
        return self.expand_bus_array(reduced_prices, axis=axis, copy_from_neighbour=True)

    def expand_voltage_angles(
        self, reduced_angles: np.ndarray, axis: int = 0
    ) -> np.ndarray:
        """Expand voltage angles with exact reconstruction."""
        return self.expand_bus_array(reduced_angles, axis=axis, copy_from_neighbour=True)

    def expand_line_flows(
        self, reduced_flows: np.ndarray, axis: int = 0
    ) -> np.ndarray:
        """Expand per-line flows (signed MW)."""
        return self.expand_line_array(reduced_flows, axis=axis)
