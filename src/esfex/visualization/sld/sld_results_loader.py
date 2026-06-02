"""Load HDF5 simulation results and map them to SLD element IDs.

The HDF5 file produced by ``Orchestrator`` stores arrays indexed by
generator-index × node-index × hour.  The SLD uses *element IDs*
(strings like ``"unit_1_bus_0"``).  This module bridges the gap:

    loader = SldResultsLoader(h5_path, state)
    snapshot = loader.get_timestep(2030, 100)
    # snapshot["generators"]["unit_1_bus_0"]["output_mw"] == 125.3
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from esfex.analysis.frequency import (
    FrequencyAnalyzer,
    GeneratorFreqParams,
    build_gen_freq_params_from_hdf5,
)
from esfex.visualization.data.gui_model import GuiSystemState

log = logging.getLogger(__name__)


def _safe_read_2d(ds, row: int, col: int) -> float:
    """Read from a dataset that might be 1D or 2D, returning 0.0 on bounds error."""
    if ds is None:
        return 0.0
    s = ds.shape
    ndim = len(s)
    try:
        if ndim >= 2 and row < s[0] and col < s[1]:
            return float(ds[row, col])
        if ndim == 1 and col < s[0]:
            return float(ds[col])
    except (IndexError, ValueError):
        pass
    return 0.0


class SldResultsLoader:
    """Read HDF5 results and extract per-timestep snapshots for the SLD.

    Parameters
    ----------
    h5_path : str | Path
        Path to an HDF5 file written by ``Orchestrator``.
    state : GuiSystemState
        The current GUI system state (provides element IDs and topology).
    """

    def __init__(self, h5_path: str | Path, state: GuiSystemState,
                 base_prefix: str = "") -> None:
        self._path = Path(h5_path)
        self._state = state
        self._base_prefix = base_prefix
        self._freq_analyzer = None  # Set up after loading mappings

        # Imported lazily to avoid a hard Qt-side import on module load.
        from esfex.visualization.panels.results_charts import (
            _open_system_config, _system_node_range,
        )

        with h5py.File(self._path, "r") as f:
            # ── Discover available years ──
            # Per-system mirrors under /systems/{name}/detailed_results/
            # were removed by the Phase-2 refactor; the scenario list
            # lives at the root and is identical across subsystems.
            detailed = f.get("detailed_results", {})
            self._years: list[int] = sorted({
                int(k.split("_")[1])
                for k in detailed.keys()
                if k.startswith("year_")
            })

            # ── Determine hours per year from first year ──
            self._hours = 0
            if self._years:
                first_key = f"year_{self._years[0]}_threshold_0"
                grp = detailed.get(first_key, {})
                # Try generation first, then demand, then power_flow
                gen_grp = grp.get("generation", {})
                for ds_name in gen_grp:
                    ds = gen_grp[ds_name]
                    self._hours = ds.shape[-1]  # [nodes x hours]
                    break
                if self._hours == 0:
                    demand_ds = grp.get("demand")
                    if demand_ds is not None:
                        self._hours = demand_ds.shape[-1]

            # ── Read num_nodes ──
            # Multi-system: count only this subsystem's nodes via the
            # root subsystem-layout attrs (set by the runner). Falls
            # back to the global ``num_nodes`` for single-system runs.
            rng = _system_node_range(f, base_prefix) if base_prefix else None
            if rng is not None:
                self._num_nodes = int(rng[1] - rng[0])
            else:
                self._num_nodes = int(f.attrs.get("num_nodes", 1))

            # ── Read generator / battery names (dataset names in HDF5) ──
            # _open_system_config falls back to a filtered view of the
            # root system_configuration when the per-system mirror is
            # absent, so the same code reads both old and new files.
            sysconf = _open_system_config(f, base_prefix)
            if sysconf is not None and "generators" in sysconf:
                gen_conf = sysconf["generators"]
                self._h5_gen_names: list[str] = sorted(
                    [k for k in gen_conf.keys() if k.startswith("generator_")],
                    key=lambda x: int(x.split("_")[1]),
                )
            else:
                self._h5_gen_names = []
            if sysconf is not None and "batteries" in sysconf:
                bat_conf = sysconf["batteries"]
                self._h5_bat_names: list[str] = sorted(
                    [k for k in bat_conf.keys() if k.startswith("battery_")],
                    key=lambda x: int(x.split("_")[1]),
                )
            else:
                self._h5_bat_names = []

        # ── Build mappings: GUI element ID → (gen_index, node_index) ──
        self._gen_map = self._build_gen_map(state)
        self._bat_map = self._build_bat_map(state)
        self._node_bus_map = self._build_node_bus_map(state)

        # ── Build edge → (from_node, to_node) mapping ──
        self._edge_map = self._build_edge_map(state)

        # ── Initialize frequency analyzer (Level 3) ──
        self._freq_analyzer = self._build_freq_analyzer()

        log.info(
            "SldResultsLoader: %d years, %d hours, %d gens, %d bats, %d edges, freq=%s",
            len(self._years), self._hours,
            len(self._gen_map), len(self._bat_map), len(self._edge_map),
            "yes" if self._freq_analyzer else "no",
        )

    # ── Public properties ──

    @property
    def years(self) -> list[int]:
        """Available simulation years."""
        return list(self._years)

    @property
    def hours_per_year(self) -> int:
        """Number of hourly timesteps per year."""
        return self._hours

    @property
    def num_nodes(self) -> int:
        return self._num_nodes

    # ── Main API ──

    def get_timestep(self, year: int, hour: int) -> dict[str, Any]:
        """Extract an operational snapshot for one timestep.

        Returns
        -------
        dict with keys:
            generators : {element_id: {output_mw, capacity_mw, curtailment_mw,
                          status, is_startup}}
            batteries  : {element_id: {charge_mw, discharge_mw, soc_mwh, capacity_mwh}}
            loads      : {"load_node_N": {demand_mw, shed_mw}}
            lines      : {"edge_LINE_ID": {flow_mw, capacity_mw, utilization_pct}}
            nodes      : {node_index: {price_usd, reserve_static_mw, reserve_dynamic_mw,
                          voltage_angle_deg, co2_tons}}
            system     : {re_penetration, total_gen_mw, total_demand_mw, co2_tons,
                          frequency: {rocof_hz_s, nadir_hz, ...}}
        """
        scenario_key = f"year_{year}_threshold_0"

        result: dict[str, Any] = {
            "generators": {},
            "batteries": {},
            "loads": {},
            "lines": {},
            "nodes": {},
            "system": {},
        }

        # _open_scenario returns a Group-like view (the legacy
        # per-system mirror when present, or a sliced proxy onto the
        # root scenario otherwise). The downstream _safe_read_2d /
        # nodal-index accesses work uniformly against both.
        from esfex.visualization.panels.results_charts import _open_scenario

        with h5py.File(self._path, "r") as f:
            try:
                grp = _open_scenario(f, self._base_prefix, scenario_key)
            except KeyError:
                grp = None
            if grp is None:
                log.warning("No data for %s", scenario_key)
                return result

            h = min(hour, self._hours - 1)

            # ── Generators ──
            gen_grp = grp.get("generation")
            curt_ds = grp.get("curtailment")  # [nodes x hours]
            for elem_id, (g_idx, n_idx) in self._gen_map.items():
                out_mw = 0.0
                if gen_grp is not None:
                    ds_name = self._h5_gen_names[g_idx] if g_idx < len(self._h5_gen_names) else None
                    if ds_name and ds_name in gen_grp:
                        out_mw = _safe_read_2d(gen_grp[ds_name], n_idx, h)

                curt_mw = _safe_read_2d(curt_ds, n_idx, h)

                gen_inst = self._state.generators.get(elem_id)
                cap_mw = gen_inst.rated_power if gen_inst else 0.0

                result["generators"][elem_id] = {
                    "output_mw": round(out_mw, 2),
                    "capacity_mw": round(cap_mw, 2),
                    "curtailment_mw": round(curt_mw, 2),
                    "status": 1,
                    "is_startup": False,
                }

            # ── Generator status (on/off, startup) ──
            status_grp = grp.get("gen_status")
            startup_grp = grp.get("gen_startup")
            for elem_id, (g_idx, n_idx) in self._gen_map.items():
                ds_name = self._h5_gen_names[g_idx] if g_idx < len(self._h5_gen_names) else None
                if ds_name:
                    if status_grp and ds_name in status_grp:
                        status_val = _safe_read_2d(status_grp[ds_name], n_idx, h)
                        result["generators"][elem_id]["status"] = int(status_val > 0.5)
                    if startup_grp and ds_name in startup_grp:
                        result["generators"][elem_id]["is_startup"] = (
                            _safe_read_2d(startup_grp[ds_name], n_idx, h) > 0.5
                        )

            # ── Batteries ──
            charge_grp = grp.get("battery_charge")
            discharge_grp = grp.get("battery_discharge")
            soc_grp = grp.get("battery_soc")
            for elem_id, (b_idx, n_idx) in self._bat_map.items():
                ds_name = self._h5_bat_names[b_idx] if b_idx < len(self._h5_bat_names) else None
                charge_mw = discharge_mw = soc_mwh = 0.0

                if ds_name:
                    if charge_grp and ds_name in charge_grp:
                        charge_mw = _safe_read_2d(charge_grp[ds_name], n_idx, h)
                    if discharge_grp and ds_name in discharge_grp:
                        discharge_mw = _safe_read_2d(discharge_grp[ds_name], n_idx, h)
                    if soc_grp and ds_name in soc_grp:
                        soc_mwh = _safe_read_2d(soc_grp[ds_name], n_idx, h)

                bat_inst = self._state.batteries.get(elem_id)
                cap_mwh = bat_inst.capacity if bat_inst else 0.0

                result["batteries"][elem_id] = {
                    "charge_mw": round(charge_mw, 2),
                    "discharge_mw": round(discharge_mw, 2),
                    "soc_mwh": round(soc_mwh, 2),
                    "capacity_mwh": round(cap_mwh, 2),
                }

            # ── Demand / Load shedding ──
            demand_ds = grp.get("demand")  # [nodes x hours]
            shed_ds = grp.get("loss_load")  # [nodes x hours]
            for node in self._state.nodes:
                ni = node.index
                demand_mw = _safe_read_2d(demand_ds, ni, h)
                shed_mw = _safe_read_2d(shed_ds, ni, h)
                result["loads"][f"load_node_{ni}"] = {
                    "demand_mw": round(demand_mw, 2),
                    "shed_mw": round(shed_mw, 2),
                }

            # ── Power flow on lines ──
            flow_ds = grp.get("power_flow")  # [from x to x hours]
            if flow_ds is not None:
                fshape = flow_ds.shape
                for edge_id, (from_n, to_n, cap_mw) in self._edge_map.items():
                    flow_mw = 0.0
                    if (len(fshape) == 3
                            and from_n < fshape[0] and to_n < fshape[1]
                            and h < fshape[2]):
                        flow_mw = float(flow_ds[from_n, to_n, h])
                    elif len(fshape) == 2 and from_n < fshape[0] and h < fshape[1]:
                        flow_mw = float(flow_ds[from_n, h])
                    util = abs(flow_mw) / cap_mw * 100 if cap_mw > 0 else 0.0
                    result["lines"][edge_id] = {
                        "flow_mw": round(flow_mw, 2),
                        "capacity_mw": round(cap_mw, 2),
                        "utilization_pct": round(util, 1),
                    }

            # ── Nodal prices ──
            price_ds = grp.get("nodal_electricity_prices")  # [nodes x hours]
            for ni in range(self._num_nodes):
                price = _safe_read_2d(price_ds, ni, h)
                result["nodes"][ni] = {"price_usd": round(price, 2)}

            # ── Reserves per node ──
            res_static_ds = grp.get("reserve_static")
            res_dynamic_ds = grp.get("reserve_dynamic")
            res_static_loss_ds = grp.get("loss_of_reserve_static")
            res_dynamic_loss_ds = grp.get("loss_of_reserve_dynamic")
            for ni in range(self._num_nodes):
                if ni not in result["nodes"]:
                    result["nodes"][ni] = {}
                result["nodes"][ni]["reserve_static_mw"] = round(
                    _safe_read_2d(res_static_ds, ni, h), 2,
                )
                result["nodes"][ni]["reserve_dynamic_mw"] = round(
                    _safe_read_2d(res_dynamic_ds, ni, h), 2,
                )
                result["nodes"][ni]["reserve_static_loss_mw"] = round(
                    _safe_read_2d(res_static_loss_ds, ni, h), 2,
                )
                result["nodes"][ni]["reserve_dynamic_loss_mw"] = round(
                    _safe_read_2d(res_dynamic_loss_ds, ni, h), 2,
                )

            # ── Voltage angles ──
            angle_ds = grp.get("voltage_angle")
            for ni in range(self._num_nodes):
                angle_rad = _safe_read_2d(angle_ds, ni, h)
                result["nodes"][ni]["voltage_angle_deg"] = round(
                    float(np.degrees(angle_rad)), 2,
                )

            # ── CO₂ per node ──
            co2_ds = grp.get("CO2_emissions")
            for ni in range(self._num_nodes):
                result["nodes"][ni]["co2_tons"] = round(
                    _safe_read_2d(co2_ds, ni, h), 2,
                )

            # ── System-level summary ──
            total_gen_mw = sum(
                v["output_mw"] for v in result["generators"].values()
            )
            total_demand_mw = sum(
                v["demand_mw"] for v in result["loads"].values()
            )
            result["system"] = {
                "year": year,
                "hour": hour,
                "re_penetration": float(grp.attrs.get("renewable_penetration", 0)),
                "total_gen_mw": total_gen_mw,
                "total_demand_mw": total_demand_mw,
                "co2_tons": float(grp.attrs.get("co2_emissions", 0)),
            }

            # ── Frequency stability metrics (Level 3) ──
            if self._freq_analyzer is not None:
                largest_gen_mw = max(
                    (
                        v["output_mw"]
                        for v in result["generators"].values()
                        if v.get("status", 1) > 0
                    ),
                    default=0,
                )
                if largest_gen_mw > 0:
                    freq_resp = self._freq_analyzer.analyze(result, largest_gen_mw)
                    result["system"]["frequency"] = {
                        "rocof_hz_s": round(freq_resp.rocof_hz_per_s, 3),
                        "nadir_hz": round(freq_resp.nadir_hz, 2),
                        "steady_state_hz": round(freq_resp.steady_state_hz, 2),
                        "t_nadir_s": round(freq_resp.t_nadir_s, 2),
                        "h_total_mws": round(freq_resp.h_total_mws, 1),
                        "delta_p_mw": round(freq_resp.delta_p_mw, 1),
                        "d_total_mw_per_hz": round(freq_resp.d_total_mw_per_hz, 2),
                        "is_stable": freq_resp.is_stable,
                        "rocof_ok": freq_resp.rocof_ok,
                    }

        return result

    # ── Private helpers ──

    def _build_freq_analyzer(self) -> FrequencyAnalyzer | None:
        """Build a FrequencyAnalyzer from HDF5 generator config data."""
        try:
            gen_params = build_gen_freq_params_from_hdf5(
                self._path, self._gen_map,
            )
            if not gen_params:
                return None
            return FrequencyAnalyzer(gen_params)
        except Exception:
            log.debug("Could not build frequency analyzer", exc_info=True)
            return None

    # ── Private: build index mappings ──

    def _build_gen_map(
        self, state: GuiSystemState,
    ) -> dict[str, tuple[int, int]]:
        """Map generator instance_id → (gen_index, node_index).

        The HDF5 stores generation as ``generation/generator_G`` with shape
        ``[nodes x hours]``.  ``G`` is the order of generators in the config.
        The GUI stores generators with instance IDs like ``"unit_1_bus_0"``,
        and each has a ``unit_key`` (e.g. ``"unit_1"``) that corresponds to
        the config generator key.
        """
        # Build unit_key → gen_index mapping from HDF5 config order
        # HDF5 has generator_0, generator_1, ... in config order
        # The config keys are the unit_keys from the GUI
        gen_keys = list(
            {g.unit_key for g in state.generators.values()}
        )
        gen_keys.sort()  # ensure deterministic order

        # Map unit_key → gen_index (matching HDF5 generator_0, generator_1, ...)
        key_to_idx: dict[str, int] = {}
        for i, k in enumerate(gen_keys):
            key_to_idx[k] = i

        # Bus → parent_node
        bus_to_node: dict[str, int] = {
            b.bus_id: b.parent_node for b in state.buses.values()
        }

        result: dict[str, tuple[int, int]] = {}
        for inst_id, gen in state.generators.items():
            g_idx = key_to_idx.get(gen.unit_key)
            n_idx = bus_to_node.get(gen.bus, 0)
            if g_idx is not None:
                result[inst_id] = (g_idx, n_idx)

        return result

    def _build_bat_map(
        self, state: GuiSystemState,
    ) -> dict[str, tuple[int, int]]:
        """Map battery instance_id → (bat_index, node_index)."""
        bat_keys = sorted({b.unit_key for b in state.batteries.values()})
        key_to_idx = {k: i for i, k in enumerate(bat_keys)}

        bus_to_node = {b.bus_id: b.parent_node for b in state.buses.values()}

        result: dict[str, tuple[int, int]] = {}
        for inst_id, bat in state.batteries.items():
            b_idx = key_to_idx.get(bat.unit_key)
            n_idx = bus_to_node.get(bat.bus, 0)
            if b_idx is not None:
                result[inst_id] = (b_idx, n_idx)

        return result

    def _build_node_bus_map(
        self, state: GuiSystemState,
    ) -> dict[int, list[str]]:
        """Map node_index → list of bus_ids."""
        m: dict[int, list[str]] = {}
        for bus in state.buses.values():
            m.setdefault(bus.parent_node, []).append(bus.bus_id)
        return m

    def _build_edge_map(
        self, state: GuiSystemState,
    ) -> dict[str, tuple[int, int, float]]:
        """Map SLD edge_id → (from_node, to_node, capacity_mw).

        Only transmission line edges are mapped (transformers/converters
        are intra-node and don't carry inter-node power flow).
        """
        bus_to_node = {b.bus_id: b.parent_node for b in state.buses.values()}
        result: dict[str, tuple[int, int, float]] = {}

        for line in state.transmission_lines:
            from_n = bus_to_node.get(line.from_bus)
            to_n = bus_to_node.get(line.to_bus)
            if from_n is None or to_n is None or from_n == to_n:
                continue
            edge_id = f"edge_{line.line_id}"
            cap = line.capacity_mw or 0.0
            result[edge_id] = (from_n, to_n, cap)

        return result
