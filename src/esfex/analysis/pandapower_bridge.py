"""Bridge between Studio state and pandapower for AC power flow analysis.

Converts ``GuiSystemState`` + ``HypotheticalScenario`` into a pandapower
network and runs Newton-Raphson AC power flow and IEC 60909 short-circuit
analysis.

The impedance conversion follows the established pattern in
``tests/fixtures/ieee_reference_solvers.py``::

    z_base = vn_kv² / base_mva
    x_ohm  = x_pu  * z_base
    max_i_ka = capacity_mw / (√3 * vn_kv)

All pandapower imports are lazy — the module can be imported safely even
when pandapower is not installed.
"""

from __future__ import annotations

import logging
import math
import warnings
from functools import lru_cache
from typing import Any

from esfex.analysis.ac_types import ACPowerFlowResult, ShortCircuitResult

log = logging.getLogger(__name__)

# ── Defaults ──
_DEFAULT_REACTANCE_PU = 0.01
_DEFAULT_RESISTANCE_PU = 0.001
_DEFAULT_BASE_MVA = 100.0
_DEFAULT_VN_KV = 220.0
_DEFAULT_POWER_FACTOR = 0.95


class PandapowerBridge:
    """Convert GUI state to pandapower network and run AC analyses.

    Parameters
    ----------
    state : GuiSystemState
        Editor state providing topology, impedances, and equipment data.
    base_mva : float
        System base power for per-unit conversion.
    """

    def __init__(self, state: Any, base_mva: float = _DEFAULT_BASE_MVA) -> None:
        self._state = state
        self._base_mva = base_mva

        # Mappings built during network creation
        self._bus_id_to_pp: dict[str, int] = {}
        self._pp_to_bus_id: dict[int, str] = {}
        self._gen_id_to_pp: dict[str, int] = {}
        self._sgen_id_to_pp: dict[str, int] = {}
        self._line_id_to_pp: dict[str, int] = {}
        self._trafo_name_to_pp: dict[str, int] = {}
        self._load_node_to_pp: dict[int, int] = {}
        self._storage_id_to_pp: dict[str, int] = {}

        self._slack_bus_id: str | None = None
        self._net = None

    # ── Static availability check ──

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        """Check whether pandapower is installed and importable."""
        try:
            import pandapower  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Network construction ──

    def build_network(self, scenario: Any) -> Any:
        """Build a pandapower network from the editor state and scenario.

        Parameters
        ----------
        scenario : HypotheticalScenario
            Dispatch scenario with generator outputs, status, and demands.

        Returns
        -------
        pandapower.auxiliary.pandapowerNet
            The constructed network (also stored as ``self._net``).
        """
        import pandapower as pp

        logging.getLogger("pandapower").setLevel(logging.ERROR)

        net = pp.create_empty_network(sn_mva=self._base_mva)
        state = self._state

        # Reset mappings
        self._bus_id_to_pp.clear()
        self._pp_to_bus_id.clear()
        self._gen_id_to_pp.clear()
        self._sgen_id_to_pp.clear()
        self._line_id_to_pp.clear()
        self._trafo_name_to_pp.clear()
        self._load_node_to_pp.clear()
        self._storage_id_to_pp.clear()
        self._slack_bus_id = None

        # ── Buses ──
        for bus_id, bus in state.buses.items():
            vn_kv = bus.voltage_kv if bus.voltage_kv > 0 else _DEFAULT_VN_KV
            pp_idx = pp.create_bus(net, vn_kv=vn_kv, name=bus_id)
            self._bus_id_to_pp[bus_id] = pp_idx
            self._pp_to_bus_id[pp_idx] = bus_id

            # External grid at slack bus
            if bus.bus_type.lower() == "slack":
                pp.create_ext_grid(
                    net, bus=pp_idx, vm_pu=1.0,
                    s_sc_max_mva=1000.0, rx_max=0.1,
                )
                self._slack_bus_id = bus_id

        # If no slack bus found, use the first bus
        if self._slack_bus_id is None and self._bus_id_to_pp:
            first_bus_id = next(iter(self._bus_id_to_pp))
            first_pp = self._bus_id_to_pp[first_bus_id]
            pp.create_ext_grid(
                net, bus=first_pp, vm_pu=1.0,
                s_sc_max_mva=1000.0, rx_max=0.1,
            )
            self._slack_bus_id = first_bus_id

        # ── Generators ──
        for gen_id, gen in state.generators.items():
            pp_bus = self._bus_id_to_pp.get(gen.bus)
            if pp_bus is None:
                continue

            output_mw = scenario.gen_outputs.get(gen_id, 0.0)
            is_on = scenario.gen_status.get(gen_id, True)
            is_re = gen.gen_type.lower() == "renewable"

            if is_re:
                # Renewable → static generator (no voltage control)
                pp_idx = pp.create_sgen(
                    net,
                    bus=pp_bus,
                    p_mw=output_mw if is_on else 0.0,
                    q_mvar=0.0,
                    sn_mva=gen.rated_power,
                    k=1.0,  # IEC 60909: nominal-to-SC current ratio
                    name=gen_id,
                    in_service=is_on,
                )
                self._sgen_id_to_pp[gen_id] = pp_idx
            else:
                # Dispatchable → controllable gen (skip if this is the slack bus)
                if gen.bus == self._slack_bus_id:
                    # Slack gen: set ext_grid limits instead
                    if len(net.ext_grid) > 0:
                        net.ext_grid.at[0, "max_p_mw"] = gen.rated_power
                        net.ext_grid.at[0, "min_p_mw"] = 0.0
                    continue

                q_max = gen.rated_power * 0.5  # ±0.5 × P_rated
                vn_kv = _DEFAULT_VN_KV
                bus_obj = state.buses.get(gen.bus)
                if bus_obj and bus_obj.voltage_kv > 0:
                    vn_kv = bus_obj.voltage_kv
                sn_mva = gen.rated_power if gen.rated_power > 0 else 1.0
                pp_idx = pp.create_gen(
                    net,
                    bus=pp_bus,
                    p_mw=output_mw if is_on else 0.0,
                    max_q_mvar=q_max,
                    min_q_mvar=-q_max,
                    name=gen_id,
                    in_service=is_on,
                    slack=False,
                    vn_kv=vn_kv,
                    sn_mva=sn_mva,
                    xdss_pu=0.2,      # Subtransient reactance for IEC 60909
                    rdss_ohm=0.0,     # Subtransient resistance
                    cos_phi=0.85,
                )
                self._gen_id_to_pp[gen_id] = pp_idx

        # ── Loads ──
        # Distribute node demand to buses via demand_fraction
        for ni in range(len(state.nodes)):
            demand_mw = scenario.node_demands.get(ni, 0.0)
            if demand_mw <= 0:
                continue

            # Find buses in this node and their demand_fraction
            node_buses = [
                (bid, b) for bid, b in state.buses.items()
                if b.parent_node == ni
            ]
            total_frac = sum(b.demand_fraction for _, b in node_buses) or 1.0

            for bid, bus in node_buses:
                pp_bus = self._bus_id_to_pp.get(bid)
                if pp_bus is None:
                    continue
                frac = bus.demand_fraction / total_frac
                p_mw = demand_mw * frac
                q_mvar = p_mw * math.tan(math.acos(_DEFAULT_POWER_FACTOR))
                pp_idx = pp.create_load(
                    net, bus=pp_bus, p_mw=p_mw, q_mvar=q_mvar,
                    name=f"load_node_{ni}_{bid}",
                )
                self._load_node_to_pp[ni] = pp_idx

        # ── Transmission lines ──
        for tl in state.transmission_lines:
            from_pp = self._bus_id_to_pp.get(tl.from_bus)
            to_pp = self._bus_id_to_pp.get(tl.to_bus)
            if from_pp is None or to_pp is None:
                continue
            if from_pp == to_pp:
                continue  # Skip intra-bus lines

            # Voltage for impedance conversion
            from_bus = state.buses.get(tl.from_bus)
            vn_kv = (from_bus.voltage_kv if from_bus and from_bus.voltage_kv > 0
                     else _DEFAULT_VN_KV)

            z_base = vn_kv ** 2 / self._base_mva
            x_pu = tl.reactance_pu if tl.reactance_pu else _DEFAULT_REACTANCE_PU
            r_pu = tl.resistance_pu if tl.resistance_pu else _DEFAULT_RESISTANCE_PU
            b_pu = tl.susceptance_pu if tl.susceptance_pu else 0.0

            x_ohm = x_pu * z_base
            r_ohm = r_pu * z_base
            # Susceptance → capacitance (nF)
            c_nf = b_pu / (2 * math.pi * (tl.frequency_hz or 50.0) * z_base) * 1e9

            # Thermal limit
            cap_mw = tl.capacity_mw or 0.0
            max_i_ka = (cap_mw / (math.sqrt(3) * vn_kv)) if vn_kv > 0 else 9999.0

            pp_idx = pp.create_line_from_parameters(
                net,
                from_bus=from_pp,
                to_bus=to_pp,
                length_km=1.0,
                r_ohm_per_km=max(r_ohm, 1e-6),
                x_ohm_per_km=max(x_ohm, 1e-6),
                c_nf_per_km=max(c_nf, 0.0),
                max_i_ka=max_i_ka if max_i_ka > 0 else 9999.0,
                max_loading_percent=100.0,
                name=tl.line_id,
            )
            self._line_id_to_pp[tl.line_id] = pp_idx

        # ── Transformers ──
        for trafo in getattr(state, "transformers", []):
            from_pp = self._bus_id_to_pp.get(trafo.from_bus)
            to_pp = self._bus_id_to_pp.get(trafo.to_bus)
            if from_pp is None or to_pp is None:
                continue

            vk_percent = trafo.impedance_pu * 100.0
            vkr_percent = trafo.losses_fraction * 100.0

            pp_idx = pp.create_transformer_from_parameters(
                net,
                hv_bus=from_pp,
                lv_bus=to_pp,
                sn_mva=trafo.rated_power_mva,
                vn_hv_kv=trafo.from_voltage_kv,
                vn_lv_kv=trafo.to_voltage_kv,
                vk_percent=max(vk_percent, 0.1),
                vkr_percent=max(vkr_percent, 0.01),
                pfe_kw=0.0,
                i0_percent=0.0,
                name=trafo.name,
            )
            self._trafo_name_to_pp[trafo.name] = pp_idx

        # ── Batteries ──
        for bat_id, bat in state.batteries.items():
            pp_bus = self._bus_id_to_pp.get(bat.bus)
            if pp_bus is None:
                continue

            pp_idx = pp.create_storage(
                net,
                bus=pp_bus,
                p_mw=0.0,  # neutral by default
                max_e_mwh=bat.capacity,
                name=bat_id,
            )
            self._storage_id_to_pp[bat_id] = pp_idx

        self._net = net
        return net

    # ── AC Power Flow ──

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
        import pandapower as pp

        net = self.build_network(scenario)
        result = ACPowerFlowResult()

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                pp.runpp(net, algorithm="nr", max_iteration=50)
        except Exception as exc:
            log.warning("AC power flow failed: %s", exc)
            return result

        if not net.converged:
            log.warning("AC power flow did not converge")
            return result

        result.converged = True
        result.iterations = int(net._ppc.get("iterations", 0)) if hasattr(net, "_ppc") else 0

        # ── Extract bus results ──
        v_min, v_max = 0.95, 1.05
        for pp_idx, row in net.res_bus.iterrows():
            bus_id = self._pp_to_bus_id.get(pp_idx, f"bus_{pp_idx}")
            vm = float(row["vm_pu"])
            va = float(row["va_degree"])
            result.bus_vm_pu[bus_id] = round(vm, 4)
            result.bus_va_deg[bus_id] = round(va, 2)
            result.bus_p_mw[bus_id] = round(float(row["p_mw"]), 2)
            result.bus_q_mvar[bus_id] = round(float(row["q_mvar"]), 2)

            if vm < v_min or vm > v_max:
                result.voltage_violations.append({
                    "bus_id": bus_id,
                    "vm_pu": round(vm, 4),
                    "type": "under" if vm < v_min else "over",
                })

        # ── Extract line results ──
        total_losses = 0.0
        for pp_idx, row in net.res_line.iterrows():
            line_name = net.line.at[pp_idx, "name"]
            edge_id = f"edge_{line_name}"
            p_from = float(row["p_from_mw"])
            q_from = float(row["q_from_mvar"])
            p_loss = float(row["pl_mw"])
            loading = float(row["loading_percent"])
            total_losses += p_loss

            result.line_p_from_mw[edge_id] = round(p_from, 2)
            result.line_q_from_mvar[edge_id] = round(q_from, 2)
            result.line_p_loss_mw[edge_id] = round(p_loss, 3)
            result.line_loading_pct[edge_id] = round(loading, 1)

        result.total_losses_mw = round(total_losses, 3)

        # ── Extract generator results ──
        for gen_id, pp_idx in self._gen_id_to_pp.items():
            if pp_idx in net.res_gen.index:
                result.gen_p_mw[gen_id] = round(float(net.res_gen.at[pp_idx, "p_mw"]), 2)
                result.gen_q_mvar[gen_id] = round(float(net.res_gen.at[pp_idx, "q_mvar"]), 2)

        for gen_id, pp_idx in self._sgen_id_to_pp.items():
            if pp_idx in net.res_sgen.index:
                result.gen_p_mw[gen_id] = round(float(net.res_sgen.at[pp_idx, "p_mw"]), 2)
                result.gen_q_mvar[gen_id] = round(float(net.res_sgen.at[pp_idx, "q_mvar"]), 2)

        return result

    # ── Short Circuit ──

    def run_short_circuit(self) -> ShortCircuitResult:
        """Run IEC 60909 short-circuit analysis on the last built network.

        Must be called after a successful ``run_power_flow()``.

        Returns
        -------
        ShortCircuitResult
        """
        import pandapower.shortcircuit as sc

        result = ShortCircuitResult()
        net = self._net
        if net is None or not getattr(net, "converged", False):
            log.warning("Short circuit requires a converged power flow first")
            return result

        try:
            # Ensure vn_kv column exists on gen/sgen (required by calc_sc)
            if len(net.gen) > 0 and "vn_kv" not in net.gen.columns:
                net.gen["vn_kv"] = net.gen["bus"].map(net.bus["vn_kv"])
            if len(net.sgen) > 0 and "vn_kv" not in net.sgen.columns:
                net.sgen["vn_kv"] = net.sgen["bus"].map(net.bus["vn_kv"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                sc.calc_sc(net, case="max", ip=True)
        except Exception as exc:
            log.warning("Short circuit analysis failed: %s", exc)
            return result

        for pp_idx, row in net.res_bus_sc.iterrows():
            bus_id = self._pp_to_bus_id.get(pp_idx, f"bus_{pp_idx}")
            result.ik_ka[bus_id] = round(float(row.get("ikss_ka", 0.0)), 3)
            result.ip_ka[bus_id] = round(float(row.get("ip_ka", 0.0)), 3)
            # Sk = √3 × Vn × Ik
            vn_kv = float(net.bus.at[pp_idx, "vn_kv"])
            ik = float(row.get("ikss_ka", 0.0))
            sk_mva = math.sqrt(3) * vn_kv * ik
            result.sk_mva[bus_id] = round(sk_mva, 1)

        return result

    # ── Helpers ──

    def get_network(self) -> Any:
        """Return the last built pandapower network (or None)."""
        return self._net

    def set_element_in_service(self, element_type: str, element_id: str, in_service: bool) -> None:
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
        if self._net is None:
            return

        lookup = {
            "gen": (self._gen_id_to_pp, "gen"),
            "sgen": (self._sgen_id_to_pp, "sgen"),
            "line": (self._line_id_to_pp, "line"),
            "trafo": (self._trafo_name_to_pp, "trafo"),
            "storage": (self._storage_id_to_pp, "storage"),
        }

        mapping, pp_table = lookup.get(element_type, (None, None))
        if mapping is None:
            return

        pp_idx = mapping.get(element_id)
        if pp_idx is not None:
            self._net[pp_table].at[pp_idx, "in_service"] = in_service

    def rerun_power_flow(self) -> ACPowerFlowResult:
        """Rerun AC power flow on the existing network (after modifications).

        Useful after calling ``set_element_in_service()`` for contingency analysis.

        Returns
        -------
        ACPowerFlowResult
        """
        import pandapower as pp

        result = ACPowerFlowResult()
        net = self._net
        if net is None:
            return result

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                pp.runpp(net, algorithm="nr", max_iteration=50)
        except Exception as exc:
            log.warning("AC power flow rerun failed: %s", exc)
            return result

        if not net.converged:
            return result

        result.converged = True

        # Extract same fields as run_power_flow
        v_min, v_max = 0.95, 1.05
        for pp_idx, row in net.res_bus.iterrows():
            bus_id = self._pp_to_bus_id.get(pp_idx, f"bus_{pp_idx}")
            vm = float(row["vm_pu"])
            va = float(row["va_degree"])
            result.bus_vm_pu[bus_id] = round(vm, 4)
            result.bus_va_deg[bus_id] = round(va, 2)
            result.bus_p_mw[bus_id] = round(float(row["p_mw"]), 2)
            result.bus_q_mvar[bus_id] = round(float(row["q_mvar"]), 2)
            if vm < v_min or vm > v_max:
                result.voltage_violations.append({
                    "bus_id": bus_id, "vm_pu": round(vm, 4),
                    "type": "under" if vm < v_min else "over",
                })

        total_losses = 0.0
        for pp_idx, row in net.res_line.iterrows():
            line_name = net.line.at[pp_idx, "name"]
            edge_id = f"edge_{line_name}"
            p_loss = float(row["pl_mw"])
            total_losses += p_loss
            result.line_p_from_mw[edge_id] = round(float(row["p_from_mw"]), 2)
            result.line_q_from_mvar[edge_id] = round(float(row["q_from_mvar"]), 2)
            result.line_p_loss_mw[edge_id] = round(p_loss, 3)
            result.line_loading_pct[edge_id] = round(float(row["loading_percent"]), 1)
        result.total_losses_mw = round(total_losses, 3)

        for gen_id, pp_idx in self._gen_id_to_pp.items():
            if pp_idx in net.res_gen.index:
                result.gen_p_mw[gen_id] = round(float(net.res_gen.at[pp_idx, "p_mw"]), 2)
                result.gen_q_mvar[gen_id] = round(float(net.res_gen.at[pp_idx, "q_mvar"]), 2)
        for gen_id, pp_idx in self._sgen_id_to_pp.items():
            if pp_idx in net.res_sgen.index:
                result.gen_p_mw[gen_id] = round(float(net.res_sgen.at[pp_idx, "p_mw"]), 2)
                result.gen_q_mvar[gen_id] = round(float(net.res_sgen.at[pp_idx, "q_mvar"]), 2)

        return result
