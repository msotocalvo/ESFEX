"""Native AC power flow bridge using the Julia Newton-Raphson solver.

Drop-in replacement for ``PandapowerBridge`` for AC power flow and N-1
contingency analysis.  Uses the NR solver in ``transmission_ac.jl`` via
juliacall, so no external dependencies are needed beyond Julia itself.

**What this bridge handles:**

- AC Newton-Raphson power flow (``run_power_flow``, ``rerun_power_flow``)
- Element in/out of service toggling for contingency analysis

**What this bridge does NOT handle:**

- IEC 60909 short-circuit analysis (stays with pandapower)
"""

from __future__ import annotations

import logging
import math
from typing import Any

from esfex.analysis.ac_types import ACPowerFlowResult

log = logging.getLogger(__name__)

_DEFAULT_REACTANCE_PU = 0.01
_DEFAULT_RESISTANCE_PU = 0.001
_DEFAULT_BASE_MVA = 100.0
_DEFAULT_VN_KV = 220.0
_DEFAULT_POWER_FACTOR = 0.95


class NativeACBridge:
    """AC power flow bridge using the native Julia Newton-Raphson solver.

    Implements the same duck-typed interface as ``PandapowerBridge`` so it
    can be used as a drop-in replacement by ``ACContingencyAnalyzer``.

    Parameters
    ----------
    state : GuiSystemState
        Editor state providing topology, impedances, and equipment data.
    base_mva : float
        System base power for per-unit conversion.
    voltage_min_pu : float
        Lower voltage limit for violation detection.
    voltage_max_pu : float
        Upper voltage limit for violation detection.
    """

    def __init__(
        self,
        state: Any,
        base_mva: float = _DEFAULT_BASE_MVA,
        voltage_min_pu: float = 0.90,
        voltage_max_pu: float = 1.10,
    ) -> None:
        self._state = state
        self._base_mva = base_mva
        self._voltage_min_pu = voltage_min_pu
        self._voltage_max_pu = voltage_max_pu

        # Julia module handle (lazy-loaded)
        self._jl = None

        # Internal network representation (built once per run_power_flow)
        self._bus_ids: list[str] = []
        self._bus_id_to_idx: dict[str, int] = {}
        self._gen_id_to_bus: dict[str, str] = {}
        self._gen_types: dict[str, str] = {}  # gen_id → "gen" or "sgen"
        self._line_ids: list[str] = []
        self._line_id_to_idx: dict[str, int] = {}
        self._trafo_names: list[str] = []

        # Mutable arrays for rerun (modified by set_element_in_service)
        self._p_gen_mw: list[float] = []
        self._q_gen_mvar: list[float] = []
        self._p_load_mw: list[float] = []
        self._q_load_mvar: list[float] = []
        self._bus_types: list[int] = []
        self._line_in_service: list[bool] = []
        self._gen_in_service: dict[str, bool] = {}

        # Line/trafo arrays
        self._line_from: list[int] = []
        self._line_to: list[int] = []
        self._line_r_pu: list[float] = []
        self._line_x_pu: list[float] = []
        self._line_b_pu: list[float] = []
        self._line_capacity_mw: list[float] = []
        self._trafo_from: list[int] = []
        self._trafo_to: list[int] = []
        self._trafo_r_pu: list[float] = []
        self._trafo_x_pu: list[float] = []
        self._trafo_tap: list[float] = []
        self._trafo_rated_mva: list[float] = []
        self._trafo_impedance_pu: list[float] = []

        self._slack_bus_id: str | None = None
        self._network_built = False

    # ── Availability ──

    @staticmethod
    def is_available() -> bool:
        """Always available — no external dependencies beyond Julia."""
        try:
            from juliacall import Main as jl  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Julia module loading ──

    def _ensure_julia(self):
        """Lazy-load the Julia ESFEX module."""
        if self._jl is not None:
            return
        from juliacall import Main as jl
        # Ensure the ESFEX module is loaded
        jl.seval('using ESFEX')
        self._jl = jl

    # ── Network building ──

    def _build_network(self, scenario: Any) -> None:
        """Build internal network arrays from editor state and scenario."""
        state = self._state

        # ── Buses ──
        self._bus_ids = list(state.buses.keys())
        self._bus_id_to_idx = {bid: i for i, bid in enumerate(self._bus_ids)}
        n = len(self._bus_ids)

        self._bus_types = [1] * n  # Default: PQ
        bus_voltage_kv = []
        self._slack_bus_id = None

        for i, (bus_id, bus) in enumerate(state.buses.items()):
            vn = bus.voltage_kv if bus.voltage_kv > 0 else _DEFAULT_VN_KV
            bus_voltage_kv.append(vn)
            if bus.bus_type.lower() == "slack":
                self._bus_types[i] = 3
                self._slack_bus_id = bus_id

        # If no slack bus, use first bus
        if self._slack_bus_id is None and n > 0:
            self._bus_types[0] = 3
            self._slack_bus_id = self._bus_ids[0]

        # ── Generator injections ──
        self._p_gen_mw = [0.0] * n
        self._q_gen_mvar = [0.0] * n
        self._gen_id_to_bus = {}
        self._gen_types = {}
        self._gen_in_service = {}
        self._original_gen_outputs: dict[str, float] = {}

        for gen_id, gen in state.generators.items():
            pp_bus_idx = self._bus_id_to_idx.get(gen.bus)
            if pp_bus_idx is None:
                continue

            output_mw = scenario.gen_outputs.get(gen_id, 0.0)
            is_on = scenario.gen_status.get(gen_id, True)
            is_re = gen.gen_type.lower() == "renewable"

            self._gen_id_to_bus[gen_id] = gen.bus
            self._gen_types[gen_id] = "sgen" if is_re else "gen"
            self._gen_in_service[gen_id] = is_on
            self._original_gen_outputs[gen_id] = output_mw if is_on else 0.0

            if is_on:
                self._p_gen_mw[pp_bus_idx] += output_mw
                # Renewables inject zero reactive power
                if not is_re:
                    # PV bus: known P and |V|
                    if gen.bus != self._slack_bus_id:
                        self._bus_types[pp_bus_idx] = 2

        # ── Loads ──
        self._p_load_mw = [0.0] * n
        self._q_load_mvar = [0.0] * n

        for ni in range(len(state.nodes)):
            demand_mw = scenario.node_demands.get(ni, 0.0)
            if demand_mw <= 0:
                continue

            # Find buses in this node
            node_buses = [
                (bid, b) for bid, b in state.buses.items()
                if b.parent_node == ni
            ]
            total_frac = sum(b.demand_fraction for _, b in node_buses) or 1.0

            for bid, bus in node_buses:
                idx = self._bus_id_to_idx.get(bid)
                if idx is None:
                    continue
                frac = bus.demand_fraction / total_frac
                p = demand_mw * frac
                q = p * math.tan(math.acos(_DEFAULT_POWER_FACTOR))
                self._p_load_mw[idx] += p
                self._q_load_mvar[idx] += q

        # ── Transmission lines ──
        self._line_from = []
        self._line_to = []
        self._line_r_pu = []
        self._line_x_pu = []
        self._line_b_pu = []
        self._line_capacity_mw = []
        self._line_ids = []
        self._line_in_service = []

        for tl in state.transmission_lines:
            from_idx = self._bus_id_to_idx.get(tl.from_bus)
            to_idx = self._bus_id_to_idx.get(tl.to_bus)
            if from_idx is None or to_idx is None:
                continue
            if from_idx == to_idx:
                continue

            self._line_from.append(from_idx + 1)   # 1-based for Julia
            self._line_to.append(to_idx + 1)
            self._line_r_pu.append(tl.resistance_pu or _DEFAULT_RESISTANCE_PU)
            self._line_x_pu.append(tl.reactance_pu or _DEFAULT_REACTANCE_PU)
            self._line_b_pu.append(tl.susceptance_pu or 0.0)
            self._line_capacity_mw.append(tl.capacity_mw or 0.0)
            self._line_ids.append(tl.line_id)
            self._line_in_service.append(True)

        self._line_id_to_idx = {lid: i for i, lid in enumerate(self._line_ids)}

        # ── Transformers ──
        self._trafo_from = []
        self._trafo_to = []
        self._trafo_r_pu = []
        self._trafo_x_pu = []
        self._trafo_tap = []
        self._trafo_rated_mva = []
        self._trafo_impedance_pu = []
        self._trafo_names = []

        for trafo in getattr(state, "transformers", []):
            from_idx = self._bus_id_to_idx.get(trafo.from_bus)
            to_idx = self._bus_id_to_idx.get(trafo.to_bus)
            if from_idx is None or to_idx is None:
                continue

            r_pu = getattr(trafo, "resistance_pu", 0.0) or 0.0
            x_pu = getattr(trafo, "reactance_pu", 0.0)
            if not x_pu:
                x_pu = trafo.impedance_pu * 0.99  # Mostly reactive
            tap = getattr(trafo, "tap_ratio", 1.0) or 1.0

            self._trafo_from.append(from_idx + 1)
            self._trafo_to.append(to_idx + 1)
            self._trafo_r_pu.append(r_pu)
            self._trafo_x_pu.append(x_pu)
            self._trafo_tap.append(tap)
            self._trafo_rated_mva.append(trafo.rated_power_mva)
            self._trafo_impedance_pu.append(trafo.impedance_pu)
            self._trafo_names.append(trafo.name)

        self._network_built = True

    # ── Solve ──

    def _solve(self) -> ACPowerFlowResult:
        """Call the Julia NR solver with current network arrays."""
        self._ensure_julia()
        jl = self._jl

        n = len(self._bus_ids)
        if n == 0:
            return ACPowerFlowResult()

        # Apply in-service masks: zero out tripped lines/gens
        p_gen = list(self._p_gen_mw)
        q_gen = list(self._q_gen_mvar)
        bus_types = list(self._bus_types)

        # Filter lines by in-service status
        active_line_from = []
        active_line_to = []
        active_line_r = []
        active_line_x = []
        active_line_b = []
        active_line_cap = []
        active_line_ids = []
        active_line_indices = []

        for i, in_svc in enumerate(self._line_in_service):
            if in_svc:
                active_line_from.append(self._line_from[i])
                active_line_to.append(self._line_to[i])
                active_line_r.append(self._line_r_pu[i])
                active_line_x.append(self._line_x_pu[i])
                active_line_b.append(self._line_b_pu[i])
                active_line_cap.append(self._line_capacity_mw[i])
                active_line_ids.append(self._line_ids[i])
                active_line_indices.append(i)

        # Build GuiACPowerFlowInput and call Julia
        gui_input = jl.GuiACPowerFlowInput(
            n,
            self._bus_ids,
            [_DEFAULT_VN_KV] * n,
            bus_types,
            p_gen,
            q_gen,
            list(self._p_load_mw),
            list(self._q_load_mvar),
            active_line_from or [0],   # Julia needs non-empty vectors
            active_line_to or [0],
            active_line_r or [0.0],
            active_line_x or [0.0],
            active_line_b or [0.0],
            active_line_cap or [0.0],
            active_line_ids or [""],
            self._trafo_from or [0],
            self._trafo_to or [0],
            self._trafo_r_pu or [0.0],
            self._trafo_x_pu or [0.0],
            self._trafo_tap or [0.0],
            self._trafo_rated_mva or [0.0],
            self._trafo_impedance_pu or [0.0],
            self._trafo_names or [""],
        )

        # Handle empty network edge case
        n_active_lines = len(active_line_from)
        if n_active_lines == 0 and len(self._trafo_from) == 0:
            # No branches → trivial power flow (single bus or disconnected)
            result = ACPowerFlowResult(converged=True, iterations=0)
            for i, bid in enumerate(self._bus_ids):
                result.bus_vm_pu[bid] = 1.0
                result.bus_va_deg[bid] = 0.0
                result.bus_p_mw[bid] = round(p_gen[i] - self._p_load_mw[i], 2)
                result.bus_q_mvar[bid] = round(q_gen[i] - self._q_load_mvar[i], 2)
            return result

        jl_result = jl.solve_gui_ac_power_flow(
            gui_input,
            max_iterations=50,
            tolerance=1e-6,
            base_mva=self._base_mva,
            voltage_min_pu=self._voltage_min_pu,
            voltage_max_pu=self._voltage_max_pu,
        )

        return self._convert_result(jl_result, active_line_ids)

    def _convert_result(self, jl_result, line_ids: list[str]) -> ACPowerFlowResult:
        """Convert Julia ACPowerFlowResult to Python ACPowerFlowResult."""
        result = ACPowerFlowResult()
        result.converged = bool(jl_result.converged)
        result.iterations = int(jl_result.iterations)

        v_min = self._voltage_min_pu
        v_max = self._voltage_max_pu

        # Bus results
        for i, bus_id in enumerate(self._bus_ids):
            vm = float(jl_result.voltage_magnitude[i + 1])  # Julia 1-indexed
            va_rad = float(jl_result.voltage_angle[i + 1])
            va_deg = math.degrees(va_rad)
            result.bus_vm_pu[bus_id] = round(vm, 4)
            result.bus_va_deg[bus_id] = round(va_deg, 2)
            result.bus_p_mw[bus_id] = round(float(jl_result.p_injection[i + 1]), 2)
            result.bus_q_mvar[bus_id] = round(float(jl_result.q_injection[i + 1]), 2)

            if vm < v_min or vm > v_max:
                result.voltage_violations.append({
                    "bus_id": bus_id,
                    "vm_pu": round(vm, 4),
                    "type": "under" if vm < v_min else "over",
                })

        # Line results
        total_losses = 0.0
        n_active = len(line_ids)
        for k, lid in enumerate(line_ids):
            edge_id = f"edge_{lid}"
            jk = k + 1  # Julia 1-indexed
            p_from = float(jl_result.p_flow_from[jk])
            q_from = float(jl_result.q_flow_from[jk])
            p_loss = float(jl_result.p_losses[jk])
            total_losses += p_loss

            result.line_p_from_mw[edge_id] = round(p_from, 2)
            result.line_q_from_mvar[edge_id] = round(q_from, 2)
            result.line_p_loss_mw[edge_id] = round(p_loss, 3)

            # Loading percentage
            cap = self._line_capacity_mw[self._line_id_to_idx[lid]]
            if cap > 0:
                loading = abs(p_from) / cap * 100.0
            else:
                loading = 0.0
            result.line_loading_pct[edge_id] = round(loading, 1)

        # Transformer results (appended after lines in Julia output)
        for k, tname in enumerate(self._trafo_names):
            edge_id = f"edge_{tname}"
            jk = n_active + k + 1
            if jk <= len(jl_result.p_flow_from):
                p_from = float(jl_result.p_flow_from[jk])
                q_from = float(jl_result.q_flow_from[jk])
                p_loss = float(jl_result.p_losses[jk])
                total_losses += p_loss
                result.line_p_from_mw[edge_id] = round(p_from, 2)
                result.line_q_from_mvar[edge_id] = round(q_from, 2)
                result.line_p_loss_mw[edge_id] = round(p_loss, 3)

                cap = self._trafo_rated_mva[k]
                loading = (abs(p_from) / cap * 100.0) if cap > 0 else 0.0
                result.line_loading_pct[edge_id] = round(loading, 1)

        result.total_losses_mw = round(total_losses, 3)

        # Generator results — report per-gen active power from scenario inputs
        # (NR solves for bus injections, not individual gens)
        for gen_id, bus_id in self._gen_id_to_bus.items():
            output = self._original_gen_outputs.get(gen_id, 0.0)
            if self._gen_in_service.get(gen_id, True):
                result.gen_p_mw[gen_id] = round(output, 2)
            else:
                result.gen_p_mw[gen_id] = 0.0
            if bus_id in result.bus_q_mvar:
                result.gen_q_mvar[gen_id] = result.bus_q_mvar[bus_id]

        return result

    # ── Public API (matches PandapowerBridge interface) ──

    def run_power_flow(self, scenario: Any) -> ACPowerFlowResult:
        """Build network and run AC Newton-Raphson power flow.

        Parameters
        ----------
        scenario : HypotheticalScenario
            Dispatch scenario.

        Returns
        -------
        ACPowerFlowResult
        """
        try:
            self._build_network(scenario)
            return self._solve()
        except Exception as exc:
            log.warning("Native AC power flow failed: %s", exc)
            return ACPowerFlowResult()

    def rerun_power_flow(self) -> ACPowerFlowResult:
        """Rerun AC power flow on the existing network (after modifications).

        Useful after calling ``set_element_in_service()`` for contingency
        analysis.

        Returns
        -------
        ACPowerFlowResult
        """
        if not self._network_built:
            return ACPowerFlowResult()
        try:
            return self._solve()
        except Exception as exc:
            log.warning("Native AC power flow rerun failed: %s", exc)
            return ACPowerFlowResult()

    def set_element_in_service(
        self, element_type: str, element_id: str, in_service: bool,
    ) -> None:
        """Set an element in/out of service in the current network.

        Parameters
        ----------
        element_type : str
            One of "gen", "sgen", "line", "trafo", "storage".
        element_id : str
            The GUI element ID.
        in_service : bool
            Whether the element should be in service.
        """
        if not self._network_built:
            return

        if element_type in ("gen", "sgen"):
            self._gen_in_service[element_id] = in_service
            # Rebuild per-bus generation sums
            n = len(self._bus_ids)
            self._p_gen_mw = [0.0] * n
            self._q_gen_mvar = [0.0] * n
            # Reset bus types to PQ, then re-classify
            self._bus_types = [1] * n
            if self._slack_bus_id:
                slack_idx = self._bus_id_to_idx.get(self._slack_bus_id, 0)
                self._bus_types[slack_idx] = 3

            for gen_id, bus_id in self._gen_id_to_bus.items():
                if not self._gen_in_service.get(gen_id, True):
                    continue
                idx = self._bus_id_to_idx.get(bus_id)
                if idx is None:
                    continue
                # We stored the original per-bus sums; need individual gen outputs
                # Retrieve from the scenario stored during build_network
                # For simplicity, we track original per-gen outputs
                gen = self._state.generators.get(gen_id)
                if gen is None:
                    continue
                is_re = gen.gen_type.lower() == "renewable"
                output = self._original_gen_outputs.get(gen_id, 0.0)
                self._p_gen_mw[idx] += output
                if not is_re and bus_id != self._slack_bus_id:
                    self._bus_types[idx] = 2

        elif element_type == "line":
            idx = self._line_id_to_idx.get(element_id)
            if idx is not None:
                self._line_in_service[idx] = in_service

    def get_network(self) -> Any:
        """Return a truthy value if network is built (compatibility)."""
        return self._network_built or None

    # ── Mappings for ACContingencyAnalyzer compatibility ──

    @property
    def _gen_id_to_pp(self) -> dict[str, int]:
        """Dispatchable gen mapping (ACContingencyAnalyzer reads this)."""
        return {
            gid: i for i, gid in enumerate(self._gen_id_to_bus)
            if self._gen_types.get(gid) == "gen"
        }

    @property
    def _sgen_id_to_pp(self) -> dict[str, int]:
        """Renewable gen mapping (ACContingencyAnalyzer reads this)."""
        return {
            gid: i for i, gid in enumerate(self._gen_id_to_bus)
            if self._gen_types.get(gid) == "sgen"
        }
