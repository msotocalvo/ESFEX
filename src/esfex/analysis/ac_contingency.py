"""AC N-1 contingency analysis using pandapower.

Wraps ``PandapowerBridge`` with the same API as ``ContingencyAnalyzer``
(DC), but produces AC power flow results including voltage violations
and reactive power.  Falls back to the DC analyzer if pandapower
diverges for a given contingency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from esfex.analysis.contingency import ContingencyAnalyzer, ContingencyResult

log = logging.getLogger(__name__)


@dataclass
class ACContingencyResult(ContingencyResult):
    """Extended contingency result with AC-specific fields.

    Inherits all fields from ``ContingencyResult`` and adds voltage
    data from the AC power flow solution.
    """

    post_vm_pu: dict[str, float] = field(default_factory=dict)
    voltage_violations: list[dict[str, Any]] = field(default_factory=list)
    ac_converged: bool = True


class ACContingencyAnalyzer:
    """N-1 contingency analyzer using AC power flow via pandapower.

    Uses the same public API as ``ContingencyAnalyzer`` so it can be
    a drop-in replacement when pandapower is available.

    Parameters
    ----------
    bridge : PandapowerBridge
        Configured bridge (state already set).
    dc_fallback : ContingencyAnalyzer | None
        DC analyzer used as fallback when AC PF diverges.
    """

    def __init__(
        self,
        bridge: Any,
        dc_fallback: ContingencyAnalyzer | None = None,
    ) -> None:
        self._bridge = bridge
        self._dc_fallback = dc_fallback

    # ── Main API (matches ContingencyAnalyzer) ──

    def analyze_generator_loss(
        self,
        snapshot: dict[str, Any],
        gen_element_id: str,
    ) -> ACContingencyResult:
        """Compute post-contingency state after losing a generator.

        Sets the generator out of service, redistributes its output
        to other dispatchable generators, and reruns AC power flow.

        Falls back to DC analysis if AC PF diverges.
        """
        from esfex.analysis.ac_types import ACPowerFlowResult  # noqa: F401

        gens_data = snapshot.get("generators", {})
        lost_output = gens_data.get(gen_element_id, {}).get("output_mw", 0.0)

        if lost_output <= 0:
            return ACContingencyResult(
                contingency_type="generator",
                element_id=gen_element_id,
                element_description=f"Generator {gen_element_id} (offline)",
                is_secure=True,
                ac_converged=True,
            )

        # Collect pre-contingency state
        pre_gen = {eid: gd.get("output_mw", 0.0) for eid, gd in gens_data.items()}
        pre_flow = {
            eid: ld.get("flow_mw", 0.0)
            for eid, ld in snapshot.get("lines", {}).items()
        }

        # Trip the generator in the existing network
        net = self._bridge.get_network()
        if net is None:
            return self._dc_fallback_gen(snapshot, gen_element_id)

        # Set generator out of service
        gen_type = self._find_gen_type(gen_element_id)
        if gen_type:
            self._bridge.set_element_in_service(gen_type, gen_element_id, False)

        # Redistribute lost generation to remaining dispatchable gens
        self._redistribute_generation(net, gen_element_id, lost_output, gens_data)

        # Rerun AC power flow
        pf_result = self._bridge.rerun_power_flow()

        # Restore generator
        if gen_type:
            self._bridge.set_element_in_service(gen_type, gen_element_id, True)

        if not pf_result.converged:
            log.info("AC PF diverged for gen loss %s, falling back to DC", gen_element_id)
            return self._dc_fallback_gen(snapshot, gen_element_id)

        # Build post-contingency generation
        post_gen = dict(pre_gen)
        post_gen[gen_element_id] = 0.0
        for gid, p_mw in pf_result.gen_p_mw.items():
            if gid != gen_element_id:
                post_gen[gid] = p_mw

        # Build post-contingency flows
        post_flow: dict[str, float] = {}
        overloaded = []
        max_overload = 0.0
        for edge_id, p_from in pf_result.line_p_from_mw.items():
            post_flow[edge_id] = p_from
            loading = pf_result.line_loading_pct.get(edge_id, 0.0)
            if loading > 100.0:
                overload_pct = loading - 100.0
                max_overload = max(max_overload, overload_pct)
                line_id = edge_id.replace("edge_", "")
                overloaded.append({
                    "line_id": line_id,
                    "edge_id": edge_id,
                    "flow_mw": round(p_from, 2),
                    "capacity_mw": 0.0,  # TODO: resolve from snapshot
                    "overload_pct": round(overload_pct, 1),
                    "loading_pct": round(loading, 1),
                })

        # Resolve line capacities for overloaded lines
        lines_data = snapshot.get("lines", {})
        for ol in overloaded:
            cap = lines_data.get(ol["edge_id"], {}).get("capacity_mw", 0.0)
            ol["capacity_mw"] = round(cap, 2)

        return ACContingencyResult(
            contingency_type="generator",
            element_id=gen_element_id,
            element_description=f"Loss of {gen_element_id} ({lost_output:.1f} MW)",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in post_gen.items()},
            post_flow_mw=post_flow,
            overloaded_lines=overloaded,
            is_secure=len(overloaded) == 0 and len(pf_result.voltage_violations) == 0,
            max_overload_pct=round(max_overload, 1),
            post_vm_pu=pf_result.bus_vm_pu,
            voltage_violations=pf_result.voltage_violations,
            ac_converged=True,
        )

    def analyze_line_loss(
        self,
        snapshot: dict[str, Any],
        line_id: str,
    ) -> ACContingencyResult:
        """Compute post-contingency state after losing a transmission line."""
        pre_gen = {
            eid: gd.get("output_mw", 0.0)
            for eid, gd in snapshot.get("generators", {}).items()
        }
        pre_flow = {
            eid: ld.get("flow_mw", 0.0)
            for eid, ld in snapshot.get("lines", {}).items()
        }

        net = self._bridge.get_network()
        if net is None:
            return self._dc_fallback_line(snapshot, line_id)

        # Take line out of service
        self._bridge.set_element_in_service("line", line_id, False)

        # Rerun AC power flow
        pf_result = self._bridge.rerun_power_flow()

        # Restore line
        self._bridge.set_element_in_service("line", line_id, True)

        if not pf_result.converged:
            log.info("AC PF diverged for line loss %s, falling back to DC", line_id)
            return self._dc_fallback_line(snapshot, line_id)

        # Post-contingency flows
        post_flow: dict[str, float] = {}
        overloaded = []
        max_overload = 0.0
        tripped_edge = f"edge_{line_id}"
        lines_data = snapshot.get("lines", {})

        for edge_id, p_from in pf_result.line_p_from_mw.items():
            post_flow[edge_id] = p_from
            loading = pf_result.line_loading_pct.get(edge_id, 0.0)
            if loading > 100.0:
                overload_pct = loading - 100.0
                max_overload = max(max_overload, overload_pct)
                lid = edge_id.replace("edge_", "")
                cap = lines_data.get(edge_id, {}).get("capacity_mw", 0.0)
                overloaded.append({
                    "line_id": lid,
                    "edge_id": edge_id,
                    "flow_mw": round(p_from, 2),
                    "capacity_mw": round(cap, 2),
                    "overload_pct": round(overload_pct, 1),
                    "loading_pct": round(loading, 1),
                })

        post_flow[tripped_edge] = 0.0

        cap_mw = lines_data.get(tripped_edge, {}).get("capacity_mw", 0.0)

        return ACContingencyResult(
            contingency_type="line",
            element_id=line_id,
            element_description=f"Loss of line {line_id} ({cap_mw:.0f} MW cap)",
            pre_gen_mw=pre_gen,
            pre_flow_mw=pre_flow,
            post_gen_mw={k: round(v, 2) for k, v in pre_gen.items()},
            post_flow_mw=post_flow,
            overloaded_lines=overloaded,
            is_secure=len(overloaded) == 0 and len(pf_result.voltage_violations) == 0,
            max_overload_pct=round(max_overload, 1),
            post_vm_pu=pf_result.bus_vm_pu,
            voltage_violations=pf_result.voltage_violations,
            ac_converged=True,
        )

    def get_contingency_list(
        self, snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return all possible contingencies (same format as DC version)."""
        contingencies: list[dict[str, Any]] = []

        gens_data = snapshot.get("generators", {})
        for eid, gdata in gens_data.items():
            output = gdata.get("output_mw", 0.0)
            status = gdata.get("status", 1)
            if status > 0 and output > 0.1:
                contingencies.append({
                    "type": "generator",
                    "element_id": eid,
                    "description": f"Loss: {eid} ({output:.0f} MW)",
                    "impact_mw": output,
                })

        for edge_id, ldata in snapshot.get("lines", {}).items():
            line_id = edge_id.replace("edge_", "")
            flow = abs(ldata.get("flow_mw", 0.0))
            cap = ldata.get("capacity_mw", 0.0)
            contingencies.append({
                "type": "line",
                "element_id": line_id,
                "description": f"Loss: {line_id} ({cap:.0f} MW cap)",
                "impact_mw": flow,
            })

        contingencies.sort(key=lambda c: c["impact_mw"], reverse=True)
        return contingencies

    # ── Private helpers ──

    def _find_gen_type(self, gen_id: str) -> str | None:
        """Determine the pandapower element type for a generator ID."""
        if gen_id in self._bridge._gen_id_to_pp:
            return "gen"
        if gen_id in self._bridge._sgen_id_to_pp:
            return "sgen"
        return None

    def _redistribute_generation(
        self, net: Any, tripped_id: str, lost_mw: float,
        gens_data: dict[str, Any],
    ) -> None:
        """Redistribute lost generation to remaining dispatchable gens."""
        import pandapower as pp  # noqa: F401

        # Find dispatchable gens with headroom
        headroom: dict[str, float] = {}
        for gen_id, pp_idx in self._bridge._gen_id_to_pp.items():
            if gen_id == tripped_id:
                continue
            if not net.gen.at[pp_idx, "in_service"]:
                continue
            current = gens_data.get(gen_id, {}).get("output_mw", 0.0)
            rated = gens_data.get(gen_id, {}).get("capacity_mw", 0.0)
            room = max(0.0, rated - current)
            if room > 0:
                headroom[gen_id] = room

        total_headroom = sum(headroom.values())
        if total_headroom <= 0:
            return

        # Pro-rata increase
        for gen_id, room in headroom.items():
            increase = min(lost_mw, total_headroom) * (room / total_headroom)
            pp_idx = self._bridge._gen_id_to_pp[gen_id]
            current_p = float(net.gen.at[pp_idx, "p_mw"])
            net.gen.at[pp_idx, "p_mw"] = current_p + increase

    def _dc_fallback_gen(
        self, snapshot: dict, gen_id: str,
    ) -> ACContingencyResult:
        """Fall back to DC contingency for generator loss."""
        if self._dc_fallback is not None:
            dc_result = self._dc_fallback.analyze_generator_loss(snapshot, gen_id)
            return ACContingencyResult(
                contingency_type=dc_result.contingency_type,
                element_id=dc_result.element_id,
                element_description=dc_result.element_description + " [DC fallback]",
                pre_gen_mw=dc_result.pre_gen_mw,
                pre_flow_mw=dc_result.pre_flow_mw,
                post_gen_mw=dc_result.post_gen_mw,
                post_flow_mw=dc_result.post_flow_mw,
                load_shed_mw=dc_result.load_shed_mw,
                overloaded_lines=dc_result.overloaded_lines,
                total_load_shed_mw=dc_result.total_load_shed_mw,
                is_secure=dc_result.is_secure,
                max_overload_pct=dc_result.max_overload_pct,
                ac_converged=False,
            )
        return ACContingencyResult(
            contingency_type="generator",
            element_id=gen_id,
            element_description=f"Loss of {gen_id} [AC diverged, no DC fallback]",
            ac_converged=False,
        )

    def _dc_fallback_line(
        self, snapshot: dict, line_id: str,
    ) -> ACContingencyResult:
        """Fall back to DC contingency for line loss."""
        if self._dc_fallback is not None:
            dc_result = self._dc_fallback.analyze_line_loss(snapshot, line_id)
            return ACContingencyResult(
                contingency_type=dc_result.contingency_type,
                element_id=dc_result.element_id,
                element_description=dc_result.element_description + " [DC fallback]",
                pre_gen_mw=dc_result.pre_gen_mw,
                pre_flow_mw=dc_result.pre_flow_mw,
                post_gen_mw=dc_result.post_gen_mw,
                post_flow_mw=dc_result.post_flow_mw,
                load_shed_mw=dc_result.load_shed_mw,
                overloaded_lines=dc_result.overloaded_lines,
                total_load_shed_mw=dc_result.total_load_shed_mw,
                is_secure=dc_result.is_secure,
                max_overload_pct=dc_result.max_overload_pct,
                ac_converged=False,
            )
        return ACContingencyResult(
            contingency_type="line",
            element_id=line_id,
            element_description=f"Loss of line {line_id} [AC diverged, no DC fallback]",
            ac_converged=False,
        )
