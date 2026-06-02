"""N-1 contingency analysis using DC power flow redistribution.

This module computes post-contingency power flows after losing a generator
or transmission line, using the DC power flow approximation:

    θ = B⁻¹ × P_inj       (voltage angles from power injections)
    f_l = (θ_i - θ_j) / x_l  (line flows from angle differences)

No time-domain simulation is required — all calculations are algebraic.

Generator loss contingency
--------------------------
When a generator trips, its power injection is removed and the remaining
generation must cover the deficit.  Two redistribution strategies:

1. **Pro-rata**: remaining generators increase output proportionally to
   their available headroom (capacity - current output).
2. **Droop-based**: generators respond proportionally to 1/R_i × P_rated_i,
   matching the primary frequency response assumption from the frequency
   analysis module.

If total headroom is insufficient, load shedding is applied.

Line loss contingency
---------------------
When a transmission line is removed, the B-matrix is rebuilt without that
line and new voltage angles are computed.  Flow redistribution follows the
PTDF (Power Transfer Distribution Factor) relationship implicitly through
the updated B-matrix solution.

For faster repeated analysis, PTDF and LODF matrices can be pre-computed
once and reused for all line outage contingencies via the Woodbury formula.

Mathematical basis
------------------
DC power flow (lossless approximation):

    B × θ = P_inj

where B is the bus susceptance matrix:

    B[i,j] = -1/x_ij           (off-diagonal, for each line between i and j)
    B[i,i] = Σ(1/x_ik)         (diagonal, sum of admittances of lines at bus i)

The slack bus row/column is removed before inversion.  Line flows:

    f_l = (θ_from - θ_to) / x_l × S_base

PTDF (Power Transfer Distribution Factors):

    PTDF[l, n] = (B_bus_inv[from_l, n] - B_bus_inv[to_l, n]) / x_l

LODF (Line Outage Distribution Factors):

    LODF[l, k] = PTDF_lk / (1 - PTDF_kk)

where PTDF_lk = PTDF[l, from_k] - PTDF[l, to_k].

References
----------
- Wood, A.J. & Wollenberg, B.F. (2014). *Power Generation, Operation,
  and Control*, 3rd ed. Wiley.
- Glover, J.D. et al. (2012). *Power Systems Analysis and Design*, 5th ed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ── Default parameters ──
_DEFAULT_REACTANCE_PU = 0.01  # Fallback if no per-line reactance
_DEFAULT_BASE_IMPEDANCE = 100.0  # MVA base


@dataclass
class LineInfo:
    """Transmission line parameters for contingency analysis."""

    line_id: str
    from_node: int       # 0-indexed node
    to_node: int         # 0-indexed node
    capacity_mw: float
    reactance_pu: float  # per-unit reactance on system base


@dataclass
class TransformerInfo:
    """Transformer parameters for contingency analysis.

    In DC power flow, transformers behave like transmission lines
    characterized by their series reactance.
    """

    name: str
    from_node: int       # 0-indexed node
    to_node: int         # 0-indexed node
    rated_power_mva: float
    reactance_pu: float  # per-unit reactance on system base


@dataclass
class GeneratorInfo:
    """Generator parameters for contingency analysis."""

    element_id: str
    node: int            # 0-indexed node
    rated_power_mw: float
    is_renewable: bool = False


@dataclass
class BatteryInfo:
    """Battery parameters for contingency analysis.

    Batteries that are actively discharging behave like generators
    from the perspective of N-1 contingency analysis.
    """

    element_id: str
    node: int            # 0-indexed node
    rated_power_mw: float


@dataclass
class ContingencyResult:
    """Result of a single contingency analysis.

    Contains the post-contingency state: redistributed generation,
    updated line flows, overloaded lines, and load shedding.
    """

    contingency_type: str          # "generator" or "line"
    element_id: str                # ID of the tripped element
    element_description: str       # Human-readable description

    # Pre-contingency
    pre_gen_mw: dict[str, float] = field(default_factory=dict)
    pre_flow_mw: dict[str, float] = field(default_factory=dict)

    # Post-contingency
    post_gen_mw: dict[str, float] = field(default_factory=dict)
    post_flow_mw: dict[str, float] = field(default_factory=dict)
    load_shed_mw: dict[int, float] = field(default_factory=dict)  # node → MW

    # Analysis
    overloaded_lines: list[dict[str, Any]] = field(default_factory=list)
    total_load_shed_mw: float = 0.0
    is_secure: bool = True         # True if no overloads and no load shedding
    max_overload_pct: float = 0.0  # Worst-case overload percentage


class ContingencyAnalyzer:
    """Post-contingency power flow analysis using DC power flow.

    Given the current operational state and system topology, computes
    the post-contingency power flow when a generator or line trips.

    Parameters
    ----------
    lines : list[LineInfo]
        Transmission line parameters (topology + impedance).
    generators : list[GeneratorInfo]
        Generator parameters (capacity + node assignment).
    num_nodes : int
        Number of nodes in the system.
    base_mva : float
        System base power for per-unit conversion.
    slack_node : int
        Slack/reference bus index (0-indexed).
    transformers : list[TransformerInfo]
        Transformer parameters (treated as lines in DC PF).
    batteries : list[BatteryInfo]
        Battery parameters for loss-of-discharge contingencies.
    redistribution_mode : str
        How to redistribute lost generation: "pro_rata" (default) or "droop".
    gen_droop : dict[str, float] | None
        Mapping generator element_id to droop value R (per-unit).
        Used when redistribution_mode is "droop".
    """

    def __init__(
        self,
        lines: list[LineInfo],
        generators: list[GeneratorInfo],
        num_nodes: int,
        base_mva: float = _DEFAULT_BASE_IMPEDANCE,
        slack_node: int = 0,
        transformers: list[TransformerInfo] | None = None,
        batteries: list[BatteryInfo] | None = None,
        redistribution_mode: str = "pro_rata",
        gen_droop: dict[str, float] | None = None,
    ) -> None:
        self.lines = list(lines)
        self.generators = list(generators)
        self.num_nodes = num_nodes
        self.base_mva = base_mva
        self.slack_node = slack_node
        self.transformers = list(transformers) if transformers else []
        self.batteries = list(batteries) if batteries else []
        self.redistribution_mode = redistribution_mode
        self.gen_droop = gen_droop or {}

        # Build line lookup
        self._line_by_id: dict[str, LineInfo] = {
            line.line_id: line for line in self.lines
        }
        self._gen_by_id: dict[str, GeneratorInfo] = {
            g.element_id: g for g in self.generators
        }
        self._bat_by_id: dict[str, BatteryInfo] = {
            b.element_id: b for b in self.batteries
        }
        self._transformer_by_name: dict[str, TransformerInfo] = {
            t.name: t for t in self.transformers
        }

        # Combined list of line-like elements (lines + transformers as LineInfo)
        self._all_branches: list[LineInfo] = list(self.lines)
        for t in self.transformers:
            self._all_branches.append(LineInfo(
                line_id=t.name,
                from_node=t.from_node,
                to_node=t.to_node,
                capacity_mw=t.rated_power_mva,
                reactance_pu=t.reactance_pu,
            ))
        self._branch_by_id: dict[str, LineInfo] = {
            b.line_id: b for b in self._all_branches
        }

        # Lazily computed PTDF/LODF matrices
        self._ptdf: np.ndarray | None = None
        self._lodf: np.ndarray | None = None

    # ── PTDF / LODF computation ──

    def _compute_ptdf(self) -> np.ndarray:
        """Compute the Power Transfer Distribution Factor matrix.

        PTDF[l, n] gives the fraction of a unit injection at node n
        (with withdrawal at the slack bus) that flows on branch l.

        Uses:
            PTDF[l, n] = (B_bus_inv[from_l, n] - B_bus_inv[to_l, n]) / x_l

        where B_bus_inv is the inverse of the reduced B-matrix (slack row/col
        removed), with the slack bus dimension re-inserted as zeros.

        Returns
        -------
        np.ndarray
            Shape (n_branches, n_nodes).
        """
        n = self.num_nodes
        n_br = len(self._all_branches)

        if n <= 1 or n_br == 0:
            return np.zeros((n_br, n))

        # Build full B-matrix (no excluded lines)
        b_mat = self._build_b_matrix(exclude_line=None)

        # Reduce: remove slack bus
        slack = self.slack_node
        mask = np.ones(n, dtype=bool)
        mask[slack] = False
        b_reduced = b_mat[np.ix_(mask, mask)]

        # Invert reduced B-matrix
        try:
            if b_reduced.size > 0 and np.linalg.matrix_rank(b_reduced) == b_reduced.shape[0]:
                b_inv_reduced = np.linalg.inv(b_reduced)
            else:
                log.warning("Singular B-matrix in PTDF computation, using pseudo-inverse")
                b_inv_reduced = np.linalg.pinv(b_reduced)
        except np.linalg.LinAlgError:
            log.error("Failed to invert B-matrix for PTDF")
            return np.zeros((n_br, n))

        # Re-expand to full dimension (slack row/col = 0)
        b_inv_full = np.zeros((n, n))
        idx = np.where(mask)[0]
        for ri, i in enumerate(idx):
            for ci, j in enumerate(idx):
                b_inv_full[i, j] = b_inv_reduced[ri, ci]

        # Compute PTDF
        ptdf = np.zeros((n_br, n))
        for l_idx, branch in enumerate(self._all_branches):
            i = branch.from_node
            j = branch.to_node
            x_l = branch.reactance_pu
            if x_l <= 0 or i >= n or j >= n or i == j:
                continue
            ptdf[l_idx, :] = (b_inv_full[i, :] - b_inv_full[j, :]) / x_l

        return ptdf

    def _compute_lodf(self) -> np.ndarray:
        """Compute the Line Outage Distribution Factor matrix.

        LODF[l, k] gives the fraction of pre-contingency flow on branch k
        that redistributes to branch l when branch k is tripped.

        Post-contingency flow on line l after outage of line k:
            f_l_post = f_l_pre + LODF[l, k] * f_k_pre

        Uses:
            d_lk = PTDF[l, from_k] - PTDF[l, to_k]
            LODF[l, k] = d_lk / (1 - d_kk)

        where d_kk = PTDF[k, from_k] - PTDF[k, to_k].

        Returns
        -------
        np.ndarray
            Shape (n_branches, n_branches).
        """
        if self._ptdf is None:
            self._ptdf = self._compute_ptdf()

        ptdf = self._ptdf
        n_br = len(self._all_branches)
        lodf = np.zeros((n_br, n_br))

        for k, branch_k in enumerate(self._all_branches):
            i_k = branch_k.from_node
            j_k = branch_k.to_node
            if i_k >= self.num_nodes or j_k >= self.num_nodes:
                continue

            # Diagonal sensitivity: how much of its own injection branch k carries
            d_kk = ptdf[k, i_k] - ptdf[k, j_k]
            denom = 1.0 - d_kk

            if abs(denom) < 1e-12:
                # Branch k is a cutset element (island-forming); LODF is
                # undefined (infinite).  Mark as NaN.
                lodf[:, k] = np.nan
                continue

            for l in range(n_br):
                if l == k:
                    lodf[l, k] = -1.0  # The tripped line itself goes to zero
                    continue
                d_lk = ptdf[l, i_k] - ptdf[l, j_k]
                lodf[l, k] = d_lk / denom

        return lodf

    @property
    def ptdf(self) -> np.ndarray:
        """Lazily computed PTDF matrix (n_branches x n_nodes)."""
        if self._ptdf is None:
            self._ptdf = self._compute_ptdf()
        return self._ptdf

    @property
    def lodf(self) -> np.ndarray:
        """Lazily computed LODF matrix (n_branches x n_branches)."""
        if self._lodf is None:
            self._lodf = self._compute_lodf()
        return self._lodf

    # ── Main API ──

    def analyze_generator_loss(
        self,
        snapshot: dict[str, Any],
        gen_element_id: str,
        participation_factors: dict[str, float] | None = None,
    ) -> ContingencyResult:
        """Compute post-contingency state after losing a generator.

        The lost generator's output is redistributed among remaining
        generators based on the configured redistribution mode or the
        supplied participation factors.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
        gen_element_id : str
            Element ID of the generator (or battery) to trip.
        participation_factors : dict[str, float] | None
            Optional explicit participation factors (gen_id -> factor).
            If provided, overrides the redistribution_mode setting.

        Returns
        -------
        ContingencyResult
        """
        # Check if this is a battery contingency
        bat_info = self._bat_by_id.get(gen_element_id)
        if bat_info is not None:
            return self._analyze_battery_loss(snapshot, gen_element_id)

        gen_info = self._gen_by_id.get(gen_element_id)
        if gen_info is None:
            return ContingencyResult(
                contingency_type="generator",
                element_id=gen_element_id,
                element_description=f"Unknown generator {gen_element_id}",
            )

        gens_data = snapshot.get("generators", {})
        lost_output = gens_data.get(gen_element_id, {}).get("output_mw", 0.0)

        if lost_output <= 0:
            return ContingencyResult(
                contingency_type="generator",
                element_id=gen_element_id,
                element_description=f"Generator {gen_element_id} (offline)",
                is_secure=True,
            )

        # ── Collect pre-contingency generation ──
        pre_gen: dict[str, float] = {}
        for eid, gdata in gens_data.items():
            pre_gen[eid] = gdata.get("output_mw", 0.0)

        # ── Redistribute lost generation ──
        post_gen = dict(pre_gen)
        post_gen[gen_element_id] = 0.0  # Generator trips

        # Determine redistribution weights
        if participation_factors is not None:
            # Use explicit participation factors
            weights = self._apply_participation_factors(
                gen_element_id, gens_data, participation_factors,
            )
        elif self.redistribution_mode == "droop":
            # Droop-based redistribution
            weights = self._compute_droop_weights(gen_element_id, gens_data)
        else:
            # Pro-rata (default) — weight by headroom
            weights = self._compute_prorata_weights(gen_element_id, gens_data)

        total_weight = sum(weights.values())
        deficit = lost_output

        if total_weight > 0:
            # Check total headroom for the weighted generators
            total_headroom = 0.0
            headroom: dict[str, float] = {}
            for eid, w in weights.items():
                ginfo = self._gen_by_id.get(eid)
                if ginfo is None:
                    continue
                current = gens_data.get(eid, {}).get("output_mw", 0.0)
                room = max(0.0, ginfo.rated_power_mw - current)
                headroom[eid] = room
                total_headroom += room

            if total_headroom >= deficit:
                # Distribute according to weights, but clamp to headroom
                remaining = deficit
                # First pass: allocate by weight, clamp to headroom
                allocated: dict[str, float] = {}
                for eid, w in weights.items():
                    share = deficit * (w / total_weight)
                    clamped = min(share, headroom.get(eid, 0.0))
                    allocated[eid] = clamped
                    remaining -= clamped

                # Second pass: distribute any remaining to generators with room
                if remaining > 1e-6:
                    for eid, w in weights.items():
                        extra_room = headroom.get(eid, 0.0) - allocated.get(eid, 0.0)
                        if extra_room > 0 and remaining > 0:
                            take = min(extra_room, remaining)
                            allocated[eid] = allocated.get(eid, 0.0) + take
                            remaining -= take

                for eid, inc in allocated.items():
                    post_gen[eid] = pre_gen.get(eid, 0.0) + inc
            else:
                # Use all headroom, remainder becomes load shedding
                for eid, room in headroom.items():
                    post_gen[eid] = pre_gen.get(eid, 0.0) + room
                deficit = lost_output - total_headroom
        else:
            # No available generators for redistribution
            pass

        # ── Compute load shedding if needed ──
        load_shed: dict[int, float] = {}
        total_shed = max(0.0, lost_output - sum(
            post_gen.get(eid, 0.0) - pre_gen.get(eid, 0.0)
            for eid in weights
        ))
        if total_shed > 0:
            # Distribute load shedding proportionally to demand
            loads = snapshot.get("loads", {})
            total_demand = sum(v.get("demand_mw", 0) for v in loads.values())
            if total_demand > 0:
                for load_key, ldata in loads.items():
                    demand = ldata.get("demand_mw", 0)
                    if demand > 0:
                        node_idx = int(load_key.replace("load_node_", ""))
                        shed = total_shed * (demand / total_demand)
                        load_shed[node_idx] = round(shed, 2)

        # ── Build post-contingency nodal injections and solve DC PF ──
        p_inj = self._compute_nodal_injections(post_gen, snapshot, load_shed)
        post_flow = self._solve_dc_power_flow(p_inj, exclude_line=None)

        # ── Collect pre-contingency flows ──
        pre_flow: dict[str, float] = {}
        for edge_id, ldata in snapshot.get("lines", {}).items():
            pre_flow[edge_id] = ldata.get("flow_mw", 0.0)

        # ── Check for overloads ──
        overloaded = []
        max_overload = 0.0
        for line in self.lines:
            edge_id = f"edge_{line.line_id}"
            flow = post_flow.get(edge_id, 0.0)
            if line.capacity_mw > 0 and abs(flow) > line.capacity_mw:
                overload_pct = (abs(flow) / line.capacity_mw - 1.0) * 100
                max_overload = max(max_overload, overload_pct)
                overloaded.append({
                    "line_id": line.line_id,
                    "edge_id": edge_id,
                    "flow_mw": round(flow, 2),
                    "capacity_mw": round(line.capacity_mw, 2),
                    "overload_pct": round(overload_pct, 1),
                })

        return ContingencyResult(
            contingency_type="generator",
            element_id=gen_element_id,
            element_description=f"Loss of {gen_element_id} ({lost_output:.1f} MW)",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in post_gen.items()},
            post_flow_mw=post_flow,
            load_shed_mw=load_shed,
            overloaded_lines=overloaded,
            total_load_shed_mw=round(total_shed, 2),
            is_secure=len(overloaded) == 0 and total_shed == 0,
            max_overload_pct=round(max_overload, 1),
        )

    def _analyze_battery_loss(
        self,
        snapshot: dict[str, Any],
        battery_id: str,
    ) -> ContingencyResult:
        """Compute post-contingency state after losing a discharging battery.

        Battery loss is treated like generator loss: the discharge power is
        removed and redistributed among remaining generators.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        battery_id : str
            Element ID of the battery to trip.

        Returns
        -------
        ContingencyResult
        """
        bat_info = self._bat_by_id.get(battery_id)
        if bat_info is None:
            return ContingencyResult(
                contingency_type="generator",
                element_id=battery_id,
                element_description=f"Unknown battery {battery_id}",
            )

        bats_data = snapshot.get("batteries", {})
        bat_data = bats_data.get(battery_id, {})
        discharge = bat_data.get("discharge_mw", 0.0)
        charge = bat_data.get("charge_mw", 0.0)
        lost_output = discharge - charge  # Net injection lost

        if lost_output <= 0:
            return ContingencyResult(
                contingency_type="generator",
                element_id=battery_id,
                element_description=f"Battery {battery_id} (not discharging)",
                is_secure=True,
            )

        # ── Collect pre-contingency generation ──
        gens_data = snapshot.get("generators", {})
        pre_gen: dict[str, float] = {}
        for eid, gdata in gens_data.items():
            pre_gen[eid] = gdata.get("output_mw", 0.0)

        # ── Redistribute lost discharge using pro-rata among generators ──
        post_gen = dict(pre_gen)

        headroom: dict[str, float] = {}
        for ginfo in self.generators:
            eid = ginfo.element_id
            current = gens_data.get(eid, {}).get("output_mw", 0.0)
            status = gens_data.get(eid, {}).get("status", 1)
            if status <= 0 or ginfo.is_renewable:
                continue
            room = max(0.0, ginfo.rated_power_mw - current)
            if room > 0:
                headroom[eid] = room

        total_headroom = sum(headroom.values())

        if total_headroom >= lost_output:
            for eid, room in headroom.items():
                increase = lost_output * (room / total_headroom)
                post_gen[eid] = pre_gen.get(eid, 0.0) + increase
        else:
            for eid, room in headroom.items():
                post_gen[eid] = pre_gen.get(eid, 0.0) + room

        # ── Load shedding ──
        load_shed: dict[int, float] = {}
        total_shed = max(0.0, lost_output - total_headroom)
        if total_shed > 0:
            loads = snapshot.get("loads", {})
            total_demand = sum(v.get("demand_mw", 0) for v in loads.values())
            if total_demand > 0:
                for load_key, ldata in loads.items():
                    demand = ldata.get("demand_mw", 0)
                    if demand > 0:
                        node_idx = int(load_key.replace("load_node_", ""))
                        shed = total_shed * (demand / total_demand)
                        load_shed[node_idx] = round(shed, 2)

        # ── Solve DC PF with modified battery injection ──
        # Build a modified snapshot where the tripped battery has zero output
        modified_snapshot = dict(snapshot)
        modified_bats = dict(bats_data)
        modified_bats[battery_id] = {
            **bat_data,
            "discharge_mw": 0.0,
            "charge_mw": 0.0,
        }
        modified_snapshot["batteries"] = modified_bats

        p_inj = self._compute_nodal_injections(post_gen, modified_snapshot, load_shed)
        post_flow = self._solve_dc_power_flow(p_inj, exclude_line=None)

        # ── Collect pre-contingency flows ──
        pre_flow: dict[str, float] = {}
        for edge_id, ldata in snapshot.get("lines", {}).items():
            pre_flow[edge_id] = ldata.get("flow_mw", 0.0)

        # ── Check for overloads ──
        overloaded = []
        max_overload = 0.0
        for line in self.lines:
            edge_id = f"edge_{line.line_id}"
            flow = post_flow.get(edge_id, 0.0)
            if line.capacity_mw > 0 and abs(flow) > line.capacity_mw:
                overload_pct = (abs(flow) / line.capacity_mw - 1.0) * 100
                max_overload = max(max_overload, overload_pct)
                overloaded.append({
                    "line_id": line.line_id,
                    "edge_id": edge_id,
                    "flow_mw": round(flow, 2),
                    "capacity_mw": round(line.capacity_mw, 2),
                    "overload_pct": round(overload_pct, 1),
                })

        return ContingencyResult(
            contingency_type="generator",
            element_id=battery_id,
            element_description=f"Loss of battery {battery_id} ({lost_output:.1f} MW)",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in post_gen.items()},
            post_flow_mw=post_flow,
            load_shed_mw=load_shed,
            overloaded_lines=overloaded,
            total_load_shed_mw=round(total_shed, 2),
            is_secure=len(overloaded) == 0 and total_shed == 0,
            max_overload_pct=round(max_overload, 1),
        )

    def analyze_line_loss(
        self,
        snapshot: dict[str, Any],
        line_id: str,
    ) -> ContingencyResult:
        """Compute post-contingency state after losing a transmission line.

        The B-matrix is rebuilt without the tripped line, and new voltage
        angles and line flows are computed.

        If PTDF/LODF matrices have been pre-computed (via prior call to
        ``analyze_line_loss_fast`` or the ``ptdf``/``lodf`` properties),
        this method automatically uses the fast LODF-based path.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
        line_id : str
            Line ID of the line to trip (e.g. "line_0").

        Returns
        -------
        ContingencyResult
        """
        # Use fast path if PTDF/LODF are already computed
        if self._ptdf is not None and self._lodf is not None:
            return self.analyze_line_loss_fast(snapshot, line_id)

        line_info = self._line_by_id.get(line_id)
        if line_info is None:
            # Check transformers
            line_info = self._transformer_by_name.get(line_id)
            if line_info is None:
                return ContingencyResult(
                    contingency_type="line",
                    element_id=line_id,
                    element_description=f"Unknown line {line_id}",
                )

        # ── Collect pre-contingency state ──
        pre_gen: dict[str, float] = {}
        gens_data = snapshot.get("generators", {})
        for eid, gdata in gens_data.items():
            pre_gen[eid] = gdata.get("output_mw", 0.0)

        pre_flow: dict[str, float] = {}
        for edge_id, ldata in snapshot.get("lines", {}).items():
            pre_flow[edge_id] = ldata.get("flow_mw", 0.0)

        # ── Solve DC PF without the tripped line ──
        p_inj = self._compute_nodal_injections(pre_gen, snapshot, {})
        post_flow = self._solve_dc_power_flow(p_inj, exclude_line=line_id)

        # Mark the tripped line as zero flow
        edge_id_tripped = f"edge_{line_id}"
        post_flow[edge_id_tripped] = 0.0

        # ── Check for overloads ──
        overloaded = []
        max_overload = 0.0
        for line in self.lines:
            if line.line_id == line_id:
                continue  # Skip the tripped line
            edge_id = f"edge_{line.line_id}"
            flow = post_flow.get(edge_id, 0.0)
            if line.capacity_mw > 0 and abs(flow) > line.capacity_mw:
                overload_pct = (abs(flow) / line.capacity_mw - 1.0) * 100
                max_overload = max(max_overload, overload_pct)
                overloaded.append({
                    "line_id": line.line_id,
                    "edge_id": edge_id,
                    "flow_mw": round(flow, 2),
                    "capacity_mw": round(line.capacity_mw, 2),
                    "overload_pct": round(overload_pct, 1),
                })

        cap_str = ""
        if isinstance(line_info, LineInfo):
            cap_str = f"{line_info.capacity_mw:.0f} MW"
        elif isinstance(line_info, TransformerInfo):
            cap_str = f"{line_info.rated_power_mva:.0f} MVA"

        return ContingencyResult(
            contingency_type="line",
            element_id=line_id,
            element_description=f"Loss of line {line_id} ({cap_str})",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in pre_gen.items()},
            post_flow_mw=post_flow,
            load_shed_mw={},
            overloaded_lines=overloaded,
            total_load_shed_mw=0.0,
            is_secure=len(overloaded) == 0,
            max_overload_pct=round(max_overload, 1),
        )

    def analyze_line_loss_fast(
        self,
        snapshot: dict[str, Any],
        line_id: str,
    ) -> ContingencyResult:
        """Fast line outage analysis using pre-computed LODF matrices.

        Instead of rebuilding and solving the full B-matrix for each line
        outage, this method uses:

            f_l_post = f_l_pre + LODF[l, k] * f_k_pre

        where k is the index of the tripped line. The PTDF and LODF
        matrices are computed lazily on first use and cached.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
        line_id : str
            Line ID of the line to trip.

        Returns
        -------
        ContingencyResult
        """
        # Find the branch index for the tripped line
        branch_idx = None
        for idx, branch in enumerate(self._all_branches):
            if branch.line_id == line_id:
                branch_idx = idx
                break

        if branch_idx is None:
            return ContingencyResult(
                contingency_type="line",
                element_id=line_id,
                element_description=f"Unknown line {line_id}",
            )

        tripped_branch = self._all_branches[branch_idx]

        # Ensure LODF is computed
        lodf = self.lodf

        # ── Collect pre-contingency state ──
        pre_gen: dict[str, float] = {}
        gens_data = snapshot.get("generators", {})
        for eid, gdata in gens_data.items():
            pre_gen[eid] = gdata.get("output_mw", 0.0)

        pre_flow: dict[str, float] = {}
        for edge_id, ldata in snapshot.get("lines", {}).items():
            pre_flow[edge_id] = ldata.get("flow_mw", 0.0)

        # Get pre-contingency flow on the tripped line
        edge_tripped = f"edge_{line_id}"
        f_k_pre = pre_flow.get(edge_tripped, 0.0)

        # If we don't have pre-contingency flows from snapshot, compute them
        if not pre_flow:
            p_inj = self._compute_nodal_injections(pre_gen, snapshot, {})
            computed_flows = self._solve_dc_power_flow(p_inj, exclude_line=None)
            pre_flow = computed_flows
            f_k_pre = computed_flows.get(edge_tripped, 0.0)

        # ── Compute post-contingency flows using LODF ──
        post_flow: dict[str, float] = {}
        for l_idx, branch in enumerate(self._all_branches):
            edge_id = f"edge_{branch.line_id}"
            if branch.line_id == line_id:
                post_flow[edge_id] = 0.0
                continue
            f_l_pre = pre_flow.get(edge_id, 0.0)
            lodf_val = lodf[l_idx, branch_idx]
            if np.isnan(lodf_val):
                # Cutset line — LODF undefined; use pre-contingency flow
                # (in reality the system islands)
                post_flow[edge_id] = round(f_l_pre, 2)
            else:
                post_flow[edge_id] = round(f_l_pre + lodf_val * f_k_pre, 2)

        # ── Check for overloads ──
        overloaded = []
        max_overload = 0.0
        for line in self.lines:
            if line.line_id == line_id:
                continue
            edge_id = f"edge_{line.line_id}"
            flow = post_flow.get(edge_id, 0.0)
            if line.capacity_mw > 0 and abs(flow) > line.capacity_mw:
                overload_pct = (abs(flow) / line.capacity_mw - 1.0) * 100
                max_overload = max(max_overload, overload_pct)
                overloaded.append({
                    "line_id": line.line_id,
                    "edge_id": edge_id,
                    "flow_mw": round(flow, 2),
                    "capacity_mw": round(line.capacity_mw, 2),
                    "overload_pct": round(overload_pct, 1),
                })

        cap_mw = tripped_branch.capacity_mw
        return ContingencyResult(
            contingency_type="line",
            element_id=line_id,
            element_description=f"Loss of line {line_id} ({cap_mw:.0f} MW)",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in pre_gen.items()},
            post_flow_mw=post_flow,
            load_shed_mw={},
            overloaded_lines=overloaded,
            total_load_shed_mw=0.0,
            is_secure=len(overloaded) == 0,
            max_overload_pct=round(max_overload, 1),
        )

    def get_contingency_list(
        self,
        snapshot: dict[str, Any],
        min_flow_pct: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Return all possible contingencies for the current operating state.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        min_flow_pct : float
            Minimum flow as percentage of line capacity to include a line
            contingency. Lines with flow below this threshold are excluded.
            Default 0.0 (include all lines).

        Returns
        -------
        list[dict]
            Each dict has keys: type, element_id, description, impact_mw.
        """
        contingencies: list[dict[str, Any]] = []

        # Generator contingencies
        gens_data = snapshot.get("generators", {})
        for ginfo in self.generators:
            gdata = gens_data.get(ginfo.element_id, {})
            output = gdata.get("output_mw", 0.0)
            status = gdata.get("status", 1)
            if status > 0 and output > 0.1:
                contingencies.append({
                    "type": "generator",
                    "element_id": ginfo.element_id,
                    "description": f"Loss: {ginfo.element_id} ({output:.0f} MW)",
                    "impact_mw": output,
                })

        # Line contingencies
        for line in self.lines:
            edge_id = f"edge_{line.line_id}"
            line_data = snapshot.get("lines", {}).get(edge_id, {})
            flow = abs(line_data.get("flow_mw", 0.0))
            # Apply minimum flow filter
            if min_flow_pct > 0 and line.capacity_mw > 0:
                flow_pct = (flow / line.capacity_mw) * 100
                if flow_pct < min_flow_pct:
                    continue
            contingencies.append({
                "type": "line",
                "element_id": line.line_id,
                "description": f"Loss: {line.line_id} ({line.capacity_mw:.0f} MW cap)",
                "impact_mw": flow,
            })

        # Transformer contingencies
        for tr in self.transformers:
            edge_id = f"edge_{tr.name}"
            tr_data = snapshot.get("lines", {}).get(edge_id, {})
            flow = abs(tr_data.get("flow_mw", 0.0))
            if min_flow_pct > 0 and tr.rated_power_mva > 0:
                flow_pct = (flow / tr.rated_power_mva) * 100
                if flow_pct < min_flow_pct:
                    continue
            contingencies.append({
                "type": "transformer",
                "element_id": tr.name,
                "description": f"Loss: transformer {tr.name} ({tr.rated_power_mva:.0f} MVA)",
                "impact_mw": flow,
            })

        # Battery contingencies (actively discharging)
        bats_data = snapshot.get("batteries", {})
        for bat in self.batteries:
            bdata = bats_data.get(bat.element_id, {})
            discharge = bdata.get("discharge_mw", 0.0)
            charge = bdata.get("charge_mw", 0.0)
            net_output = discharge - charge
            if net_output > 0.1:
                contingencies.append({
                    "type": "battery",
                    "element_id": bat.element_id,
                    "description": f"Loss: battery {bat.element_id} ({net_output:.0f} MW)",
                    "impact_mw": net_output,
                })

        # Sort by impact (highest first)
        contingencies.sort(key=lambda c: c["impact_mw"], reverse=True)
        return contingencies

    def screen_contingencies(
        self,
        snapshot: dict[str, Any],
        pi_threshold: float = 1.0,
        max_contingencies: int = 50,
    ) -> list[dict]:
        """Screen and rank contingencies using Performance Index.

        The Performance Index (PI) for a contingency is defined as:

            PI = sum_l (f_l / f_max_l)^2

        where f_l is the post-contingency flow on line l and f_max_l is
        its capacity. Only contingencies with PI > pi_threshold are
        returned, sorted by PI descending and capped at max_contingencies.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        pi_threshold : float
            Minimum PI to include a contingency. Default 1.0.
        max_contingencies : int
            Maximum number of contingencies to return. Default 50.

        Returns
        -------
        list[dict]
            Each dict has keys: type, element_id, description, pi,
            contingency_result.
        """
        all_contingencies = self.get_contingency_list(snapshot)
        ranked: list[dict] = []

        for c in all_contingencies:
            c_type = c["type"]
            eid = c["element_id"]

            if c_type == "generator" or c_type == "battery":
                result = self.analyze_generator_loss(snapshot, eid)
            elif c_type == "line" or c_type == "transformer":
                result = self.analyze_line_loss(snapshot, eid)
            else:
                continue

            # Compute Performance Index
            pi = self._compute_performance_index(result)

            if pi >= pi_threshold:
                ranked.append({
                    "type": c_type,
                    "element_id": eid,
                    "description": c["description"],
                    "pi": round(pi, 4),
                    "contingency_result": result,
                })

        # Sort by PI descending
        ranked.sort(key=lambda r: r["pi"], reverse=True)
        return ranked[:max_contingencies]

    def analyze_n1_1(
        self,
        snapshot: dict[str, Any],
        first_contingency: dict[str, str],
        second_contingency: dict[str, str],
    ) -> ContingencyResult:
        """Sequential N-1-1 analysis: apply first contingency, then second.

        The first contingency is applied to get a post-contingency state,
        which is then used as the base state for the second contingency.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        first_contingency : dict
            Must have keys "type" ("generator", "line", "battery",
            "transformer") and "element_id".
        second_contingency : dict
            Same format as first_contingency.

        Returns
        -------
        ContingencyResult
            Result of the second contingency applied after the first.
        """
        # Apply first contingency
        first_result = self._apply_contingency(snapshot, first_contingency)

        # Build a modified snapshot reflecting the post-first-contingency state
        modified_snapshot = self._build_post_contingency_snapshot(
            snapshot, first_result, first_contingency,
        )

        # Apply second contingency on the modified state
        second_result = self._apply_contingency(
            modified_snapshot, second_contingency,
        )

        # Annotate the result with N-1-1 context
        first_desc = first_contingency.get("element_id", "?")
        second_desc = second_contingency.get("element_id", "?")
        second_result.element_description = (
            f"N-1-1: [{first_desc}] then [{second_desc}] — "
            + second_result.element_description
        )

        return second_result

    def screen_n1_1(
        self,
        snapshot: dict[str, Any],
        stress_threshold_pct: float = 80.0,
        max_pairs: int = 20,
    ) -> list[dict]:
        """Find stressed N-1 contingencies and evaluate second-order failures.

        First, all N-1 contingencies are evaluated. Lines that are loaded
        above ``stress_threshold_pct`` after the first contingency are
        candidates for the second contingency. The most critical N-1-1
        pairs are returned.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        stress_threshold_pct : float
            Post-N-1 line loading threshold (%) to trigger second-order
            analysis. Default 80.0.
        max_pairs : int
            Maximum number of N-1-1 pairs to return. Default 20.

        Returns
        -------
        list[dict]
            Each dict has keys: first_contingency, second_contingency,
            description, max_overload_pct, result.
        """
        all_contingencies = self.get_contingency_list(snapshot)
        pairs: list[dict] = []

        for first_c in all_contingencies:
            first_result = self._apply_contingency(snapshot, first_c)

            # Find stressed lines after first contingency
            stressed_lines: list[str] = []
            for line in self.lines:
                if line.line_id == first_c.get("element_id"):
                    continue  # Already tripped
                edge_id = f"edge_{line.line_id}"
                flow = abs(first_result.post_flow_mw.get(edge_id, 0.0))
                if line.capacity_mw > 0:
                    loading_pct = (flow / line.capacity_mw) * 100
                    if loading_pct >= stress_threshold_pct:
                        stressed_lines.append(line.line_id)

            # For each stressed line, evaluate the second contingency
            for stressed_lid in stressed_lines:
                second_c = {"type": "line", "element_id": stressed_lid}
                n11_result = self.analyze_n1_1(snapshot, first_c, second_c)

                pairs.append({
                    "first_contingency": first_c,
                    "second_contingency": second_c,
                    "description": (
                        f"[{first_c['element_id']}] then [{stressed_lid}]"
                    ),
                    "max_overload_pct": n11_result.max_overload_pct,
                    "result": n11_result,
                })

                if len(pairs) >= max_pairs:
                    break
            if len(pairs) >= max_pairs:
                break

        # Sort by severity
        pairs.sort(key=lambda p: p["max_overload_pct"], reverse=True)
        return pairs[:max_pairs]

    # ── Private helpers ──

    def _compute_prorata_weights(
        self,
        tripped_gen_id: str,
        gens_data: dict[str, Any],
    ) -> dict[str, float]:
        """Compute pro-rata redistribution weights based on headroom.

        Returns
        -------
        dict[str, float]
            gen_id -> weight (headroom).
        """
        weights: dict[str, float] = {}
        for ginfo in self.generators:
            eid = ginfo.element_id
            if eid == tripped_gen_id:
                continue
            current = gens_data.get(eid, {}).get("output_mw", 0.0)
            status = gens_data.get(eid, {}).get("status", 1)
            if status <= 0 or ginfo.is_renewable:
                continue
            room = max(0.0, ginfo.rated_power_mw - current)
            if room > 0:
                weights[eid] = room
        return weights

    def _compute_droop_weights(
        self,
        tripped_gen_id: str,
        gens_data: dict[str, Any],
    ) -> dict[str, float]:
        """Compute droop-based participation factors.

        pf_i = P_rated_i / (R_i * f_nom)

        where R_i is the droop constant and f_nom is the nominal frequency
        (normalized to 1.0 here since it cancels in normalization).

        Returns
        -------
        dict[str, float]
            gen_id -> weight (droop participation factor).
        """
        f_nom = 1.0  # Cancels in normalization
        weights: dict[str, float] = {}
        for ginfo in self.generators:
            eid = ginfo.element_id
            if eid == tripped_gen_id:
                continue
            current = gens_data.get(eid, {}).get("output_mw", 0.0)
            status = gens_data.get(eid, {}).get("status", 1)
            if status <= 0 or ginfo.is_renewable:
                continue
            room = max(0.0, ginfo.rated_power_mw - current)
            if room <= 0:
                continue
            droop = self.gen_droop.get(eid, 0.05)  # Default 5% droop
            if droop <= 0:
                continue
            pf = ginfo.rated_power_mw / (droop * f_nom)
            weights[eid] = pf
        return weights

    def _apply_participation_factors(
        self,
        tripped_gen_id: str,
        gens_data: dict[str, Any],
        participation_factors: dict[str, float],
    ) -> dict[str, float]:
        """Apply explicit participation factors, filtering out tripped/offline.

        Returns
        -------
        dict[str, float]
            gen_id -> weight.
        """
        weights: dict[str, float] = {}
        for eid, pf in participation_factors.items():
            if eid == tripped_gen_id:
                continue
            status = gens_data.get(eid, {}).get("status", 1)
            if status <= 0 or pf <= 0:
                continue
            ginfo = self._gen_by_id.get(eid)
            if ginfo is None:
                continue
            current = gens_data.get(eid, {}).get("output_mw", 0.0)
            room = max(0.0, ginfo.rated_power_mw - current)
            if room > 0:
                weights[eid] = pf
        return weights

    def _compute_performance_index(self, result: ContingencyResult) -> float:
        """Compute Performance Index for a contingency result.

        PI = sum_l (f_l / f_max_l)^2

        Parameters
        ----------
        result : ContingencyResult
            Post-contingency result.

        Returns
        -------
        float
            Performance Index value.
        """
        pi = 0.0
        for branch in self._all_branches:
            edge_id = f"edge_{branch.line_id}"
            flow = result.post_flow_mw.get(edge_id, 0.0)
            cap = branch.capacity_mw
            if cap > 0:
                pi += (flow / cap) ** 2
        return pi

    def _apply_contingency(
        self,
        snapshot: dict[str, Any],
        contingency: dict[str, str],
    ) -> ContingencyResult:
        """Apply a single contingency and return the result.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot.
        contingency : dict
            Must have "type" and "element_id".

        Returns
        -------
        ContingencyResult
        """
        c_type = contingency.get("type", "")
        eid = contingency.get("element_id", "")

        if c_type in ("generator", "battery"):
            return self.analyze_generator_loss(snapshot, eid)
        elif c_type in ("line", "transformer"):
            return self.analyze_line_loss(snapshot, eid)
        else:
            return ContingencyResult(
                contingency_type=c_type,
                element_id=eid,
                element_description=f"Unknown contingency type: {c_type}",
            )

    def _build_post_contingency_snapshot(
        self,
        original_snapshot: dict[str, Any],
        result: ContingencyResult,
        contingency: dict[str, str],
    ) -> dict[str, Any]:
        """Build a modified snapshot reflecting the post-contingency state.

        Updates generator outputs, line flows, and loads (with shedding)
        to reflect the state after a contingency has occurred.

        Parameters
        ----------
        original_snapshot : dict
            Original operational snapshot.
        result : ContingencyResult
            Result of the first contingency.
        contingency : dict
            The contingency that was applied.

        Returns
        -------
        dict
            Modified snapshot for use as input to a second contingency.
        """
        modified = dict(original_snapshot)

        # Update generator outputs
        new_gens = {}
        for eid, gdata in original_snapshot.get("generators", {}).items():
            new_gdata = dict(gdata)
            if eid in result.post_gen_mw:
                new_gdata["output_mw"] = result.post_gen_mw[eid]
            new_gens[eid] = new_gdata
        modified["generators"] = new_gens

        # Update line flows
        new_lines = {}
        for edge_id, ldata in original_snapshot.get("lines", {}).items():
            new_ldata = dict(ldata)
            if edge_id in result.post_flow_mw:
                new_ldata["flow_mw"] = result.post_flow_mw[edge_id]
            new_lines[edge_id] = new_ldata
        modified["lines"] = new_lines

        # Update loads with shedding
        if result.load_shed_mw:
            new_loads = {}
            for load_key, ldata in original_snapshot.get("loads", {}).items():
                new_ldata = dict(ldata)
                node_idx = int(load_key.replace("load_node_", ""))
                shed = result.load_shed_mw.get(node_idx, 0.0)
                if shed > 0:
                    new_ldata["demand_mw"] = max(
                        0.0, ldata.get("demand_mw", 0.0) - shed,
                    )
                new_loads[load_key] = new_ldata
            modified["loads"] = new_loads

        # If a line was tripped, mark it as zero capacity for subsequent analysis
        c_type = contingency.get("type", "")
        if c_type in ("line", "transformer"):
            eid = contingency.get("element_id", "")
            edge_id = f"edge_{eid}"
            if edge_id in modified.get("lines", {}):
                modified["lines"][edge_id]["flow_mw"] = 0.0

        return modified

    def _compute_nodal_injections(
        self,
        gen_mw: dict[str, float],
        snapshot: dict[str, Any],
        load_shed: dict[int, float],
    ) -> np.ndarray:
        """Compute net power injection per node (generation - demand + shed).

        Returns
        -------
        np.ndarray
            Shape (num_nodes,) in MW.
        """
        p_inj = np.zeros(self.num_nodes)

        # Add generation
        for ginfo in self.generators:
            output = gen_mw.get(ginfo.element_id, 0.0)
            if ginfo.node < self.num_nodes:
                p_inj[ginfo.node] += output

        # Subtract demand
        for load_key, ldata in snapshot.get("loads", {}).items():
            node_idx = int(load_key.replace("load_node_", ""))
            demand = ldata.get("demand_mw", 0.0)
            if node_idx < self.num_nodes:
                p_inj[node_idx] -= demand

        # Add battery net (discharge - charge)
        for eid, bdata in snapshot.get("batteries", {}).items():
            discharge = bdata.get("discharge_mw", 0.0)
            charge = bdata.get("charge_mw", 0.0)
            # Find the node for this battery — check batteries list first
            bat_info = self._bat_by_id.get(eid)
            if bat_info is not None:
                if bat_info.node < self.num_nodes:
                    p_inj[bat_info.node] += discharge - charge
            else:
                # Fallback: check generators list (legacy behavior)
                for ginfo in self.generators:
                    if ginfo.element_id == eid:
                        if ginfo.node < self.num_nodes:
                            p_inj[ginfo.node] += discharge - charge
                        break

        # Subtract load shedding (reduces demand → increases net injection)
        for node_idx, shed in load_shed.items():
            if node_idx < self.num_nodes:
                p_inj[node_idx] += shed

        return p_inj

    def _build_b_matrix(
        self, exclude_line: str | None = None,
    ) -> np.ndarray:
        """Build the bus susceptance matrix B.

        Parameters
        ----------
        exclude_line : str | None
            If set, exclude this line from the B-matrix (line outage).

        Returns
        -------
        np.ndarray
            Shape (num_nodes, num_nodes).
        """
        n = self.num_nodes
        b_mat = np.zeros((n, n))

        for line in self._all_branches:
            if exclude_line and line.line_id == exclude_line:
                continue
            i, j = line.from_node, line.to_node
            if i >= n or j >= n or i == j:
                continue
            if line.reactance_pu <= 0:
                continue
            b_ij = 1.0 / line.reactance_pu
            b_mat[i, j] -= b_ij
            b_mat[j, i] -= b_ij
            b_mat[i, i] += b_ij
            b_mat[j, j] += b_ij

        return b_mat

    def _solve_dc_power_flow(
        self,
        p_inj: np.ndarray,
        exclude_line: str | None = None,
    ) -> dict[str, float]:
        """Solve DC power flow and return line flows.

        Parameters
        ----------
        p_inj : np.ndarray
            Net power injection per node (MW).
        exclude_line : str | None
            Line to exclude (for line-loss contingency).

        Returns
        -------
        dict[str, float]
            Mapping edge_id → flow_mw.
        """
        n = self.num_nodes
        if n <= 1:
            # Single-node system — no transmission
            return {}

        b_mat = self._build_b_matrix(exclude_line)

        # Remove slack bus row/column
        slack = self.slack_node
        mask = np.ones(n, dtype=bool)
        mask[slack] = False
        b_reduced = b_mat[np.ix_(mask, mask)]
        p_reduced = p_inj[mask]

        # Solve for voltage angles
        theta = np.zeros(n)
        try:
            if b_reduced.size > 0 and np.linalg.matrix_rank(b_reduced) == b_reduced.shape[0]:
                theta_reduced = np.linalg.solve(b_reduced, p_reduced / self.base_mva)
                theta[mask] = theta_reduced
            else:
                log.warning("Singular B-matrix, falling back to pseudo-inverse")
                theta_reduced = np.linalg.lstsq(b_reduced, p_reduced / self.base_mva, rcond=None)[0]
                theta[mask] = theta_reduced
        except np.linalg.LinAlgError:
            log.error("Failed to solve DC power flow")
            return {}

        # Compute line flows: f_l = (θ_from - θ_to) / x_l × S_base
        flows: dict[str, float] = {}
        for line in self._all_branches:
            if exclude_line and line.line_id == exclude_line:
                edge_id = f"edge_{line.line_id}"
                flows[edge_id] = 0.0
                continue
            i, j = line.from_node, line.to_node
            if i >= n or j >= n or line.reactance_pu <= 0:
                continue
            flow = (theta[i] - theta[j]) / line.reactance_pu * self.base_mva
            edge_id = f"edge_{line.line_id}"
            flows[edge_id] = round(flow, 2)

        return flows


def build_contingency_from_state(
    state: Any,
    num_nodes: int,
    base_mva: float = _DEFAULT_BASE_IMPEDANCE,
    slack_node: int = 0,
) -> ContingencyAnalyzer:
    """Build a ContingencyAnalyzer from GuiSystemState.

    Parameters
    ----------
    state : GuiSystemState
        GUI system state with topology and generator data.
    num_nodes : int
        Number of nodes in the system.
    base_mva : float
        System base power (MVA).
    slack_node : int
        Slack bus index.

    Returns
    -------
    ContingencyAnalyzer
    """
    bus_to_node: dict[str, int] = {}
    if hasattr(state, "buses"):
        bus_to_node = {b.bus_id: b.parent_node for b in state.buses.values()}

    # Build line info
    lines: list[LineInfo] = []
    if hasattr(state, "transmission_lines"):
        for tl in state.transmission_lines:
            from_n = bus_to_node.get(tl.from_bus, 0)
            to_n = bus_to_node.get(tl.to_bus, 0)
            if from_n == to_n:
                continue  # Skip intra-node lines

            reactance = tl.reactance_pu if tl.reactance_pu else _DEFAULT_REACTANCE_PU
            lines.append(LineInfo(
                line_id=tl.line_id,
                from_node=from_n,
                to_node=to_n,
                capacity_mw=tl.capacity_mw or 0.0,
                reactance_pu=reactance,
            ))

    # Build generator info
    generators: list[GeneratorInfo] = []
    if hasattr(state, "generators"):
        for eid, gen in state.generators.items():
            n_idx = bus_to_node.get(gen.bus, 0)
            is_re = getattr(gen, "fuel", "") in {"Sun", "Wind", "Water", "OTEC"}
            generators.append(GeneratorInfo(
                element_id=eid,
                node=n_idx,
                rated_power_mw=getattr(gen, "rated_power", 0.0),
                is_renewable=is_re,
            ))

    # Build transformer info
    transformers: list[TransformerInfo] = []
    if hasattr(state, "transformers"):
        for tid, tr in (
            state.transformers.items()
            if isinstance(state.transformers, dict)
            else enumerate(state.transformers)
        ):
            name = getattr(tr, "name", str(tid))
            from_n = bus_to_node.get(getattr(tr, "from_bus", ""), 0)
            to_n = bus_to_node.get(getattr(tr, "to_bus", ""), 0)
            if from_n == to_n:
                continue
            reactance = getattr(tr, "reactance_pu", _DEFAULT_REACTANCE_PU)
            rated = getattr(tr, "rated_power_mva", 0.0) or getattr(tr, "rated_power", 0.0)
            transformers.append(TransformerInfo(
                name=name,
                from_node=from_n,
                to_node=to_n,
                rated_power_mva=rated,
                reactance_pu=reactance if reactance else _DEFAULT_REACTANCE_PU,
            ))

    # Build battery info
    batteries: list[BatteryInfo] = []
    if hasattr(state, "batteries"):
        for eid, bat in (
            state.batteries.items()
            if isinstance(state.batteries, dict)
            else enumerate(state.batteries)
        ):
            element_id = getattr(bat, "element_id", str(eid)) if not isinstance(eid, str) else eid
            n_idx = bus_to_node.get(getattr(bat, "bus", ""), 0)
            rated = getattr(bat, "rated_power_mw", 0.0) or getattr(bat, "rated_power", 0.0)
            batteries.append(BatteryInfo(
                element_id=element_id,
                node=n_idx,
                rated_power_mw=rated,
            ))

    return ContingencyAnalyzer(
        lines=lines,
        generators=generators,
        num_nodes=num_nodes,
        base_mva=base_mva,
        slack_node=slack_node,
        transformers=transformers,
        batteries=batteries,
    )
