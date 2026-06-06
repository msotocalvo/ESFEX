"""Bidirectional conversion between ESFEXConfig and GUI state."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
import yaml

from esfex.utils.temporal import HOURS_STD_YEAR
from esfex.config.schema import (
    GeoCoordinate,
    ESFEXConfig,
    SystemConfig,
)
from esfex.visualization.data.gui_model import (
    RENEWABLE_FUELS,
    EndpointRef,
    GeoPoint,
    GuiACDCConverter,
    GuiACPowerFlow,
    GuiBatteryInstance,
    GuiBus,
    GuiDCPowerFlow,
    GuiDemandSector,
    GuiDevelopmentZone,
    GuiElectrolyzerInstance,
    GuiEVCategory,
    GuiEVConfig,
    GuiFrequencyConverter,
    FuelEntryParams,
    FuelRouteParams,
    FuelStorageParams,
    GuiFuel,
    GuiFuelEntryPoint,
    GuiFuelSource,
    GuiFuelStorage,
    GuiFuelTransportRoute,
    GuiGeneratorInstance,
    GuiGlobalSettings,
    GuiVisualScaling,
    GuiInvestmentEntry,
    GuiInvestmentNodeData,
    GuiInterSystemLink,
    GuiNode,
    GuiNodeDemand,
    GuiNonElectricDemand,
    GuiPenalties,
    GuiRooftopSolar,
    GuiStochasticScenario,
    GuiSystemSettings,
    GuiSystemState,
    GuiTechnology,
    GuiTransformer,
    GuiTransmissionLine,
    VisualStyle,
)


def _style_to_dict(s: VisualStyle | None) -> dict | None:
    """Serialize a VisualStyle, returning None if all fields are unset."""
    if s is None:
        return None
    out: dict = {}
    if s.color is not None:
        out["color"] = s.color
    if s.size is not None:
        out["size"] = s.size
    if s.icon_shape is not None:
        out["icon_shape"] = s.icon_shape
    if s.opacity is not None:
        out["opacity"] = s.opacity
    if s.width is not None:
        out["width"] = s.width
    return out or None


def _dict_to_style(d: dict | None) -> VisualStyle:
    """Restore a VisualStyle from a serialized dict."""
    d = d or {}
    return VisualStyle(
        color=d.get("color"),
        size=d.get("size"),
        icon_shape=d.get("icon_shape"),
        opacity=d.get("opacity"),
        width=d.get("width"),
    )

def _normalize_voltage_kv(value: float) -> float:
    """Normalize a voltage to kilo-volts.

    Config / GeoJSON data sometimes stores voltage in Volts (e.g. 220 000)
    while the data-model expects kV.  Any value > 1 200 is assumed to be
    in Volts and divided by 1 000.
    """
    if value > 1200:
        return value / 1000.0
    return value


# Fields shared between generators and batteries (common scalars)
_COMMON_SCALAR_FIELDS = [
    "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
    "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
    "eff_at_rated", "eff_at_min", "inertia", "risk_coefficient",
    "start_up_cost",
    "fuel_cost", "fixed_cost", "maintenance_cost",
]

# Generator-only fields (NOT shared with batteries)
_GEN_ONLY_FIELDS = ["droop", "governor_time_const"]

_GEN_SCALAR_FIELDS_BASE = _COMMON_SCALAR_FIELDS + _GEN_ONLY_FIELDS

# Reservoir fields (generator-only, NOT shared with batteries)
_GEN_RESERVOIR_FIELDS = [
    "reservoir_capacity", "reservoir_initial_level",
    "reservoir_min_level", "reservoir_max_level",
    "reservoir_turbine_efficiency", "reservoir_evaporation_rate",
    "reservoir_pump_capacity", "reservoir_pump_efficiency",
    "reservoir_invest_cost", "reservoir_invest_max",
    "reservoir_min_release",
]

_GEN_SCALAR_FIELDS = _GEN_SCALAR_FIELDS_BASE + _GEN_RESERVOIR_FIELDS

# Investment fields read from YAML per-node arrays → investment portfolio
_GEN_INVEST_FIELDS = ["invest_cost", "invest_max_power"]
_BAT_INVEST_FIELDS = ["invest_cost", "invest_max_power", "invest_cost_energy", "invest_max_capacity"]

_BAT_SCALAR_FIELDS = _COMMON_SCALAR_FIELDS + [
    "efficiency_charge", "efficiency_discharge", "soc_initial",
    "max_DoD", "capacity", "MaxChargePower", "MaxDischargePower",
    "throughput_degradation_cost",
]

# Fields that indicate a generator has presence at a node
_GEN_PRESENCE_FIELDS = ["rated_power", "invest_max_power"]
_BAT_PRESENCE_FIELDS = ["rated_power", "capacity", "invest_max_power", "invest_max_capacity"]


def _cost_curve_to_gui_data(curve) -> dict | None:
    """Convert a CostCurveConfig to GUI-storable dict."""
    ct = curve.curve_type
    if ct == "flat":
        return None
    if ct == "linear":
        return {
            "price_at_zero": curve.price_at_zero or 0.0,
            "price_at_max": curve.price_at_max or 0.0,
            "num_segments": curve.num_segments,
        }
    if ct == "stepwise":
        blocks = [{"fraction": b.fraction, "price": b.price} for b in (curve.blocks or [])]
        return {"blocks": blocks}
    if ct == "exponential":
        return {
            "base_price": curve.base_price or 0.0,
            "scale_factor": curve.scale_factor or 1.0,
            "num_segments": curve.num_segments,
        }
    return None


def _gui_data_to_cost_curve_config(curve_type: str, data: dict | None) -> dict | None:
    """Convert GUI curve data to a CostCurveConfig dict for YAML export."""
    if curve_type == "flat" or data is None:
        return None
    cfg = {"curve_type": curve_type}
    if curve_type == "linear":
        cfg["price_at_zero"] = data.get("price_at_zero", 0.0)
        cfg["price_at_max"] = data.get("price_at_max", 0.0)
        cfg["num_segments"] = data.get("num_segments", 5)
    elif curve_type == "stepwise":
        cfg["blocks"] = data.get("blocks", [])
    elif curve_type == "exponential":
        cfg["base_price"] = data.get("base_price", 0.0)
        cfg["scale_factor"] = data.get("scale_factor", 1.0)
        cfg["num_segments"] = data.get("num_segments", 5)
    return cfg


# =====================================================================
# Config -> GUI State
# =====================================================================


def config_to_gui_states(config: ESFEXConfig) -> dict[str, GuiSystemState]:
    """Convert a :class:`ESFEXConfig` into editable GUI states.

    Returns one :class:`GuiSystemState` per system.
    """
    states: dict[str, GuiSystemState] = {}
    for sys_name, sys_config in config.systems.items():
        states[sys_name] = _system_to_gui_state(sys_config)
    return states


def config_to_inter_system_links(
    config: ESFEXConfig,
) -> list[GuiInterSystemLink]:
    """Parse ``meta_network.systems_links`` into GUI inter-system link objects."""
    links: list[GuiInterSystemLink] = []
    counter = 0
    if not hasattr(config, "meta_network") or config.meta_network is None:
        return links
    for sl in config.meta_network.systems_links:
        if len(sl.systems) < 2:
            continue
        from_sys, to_sys = sl.systems[0], sl.systems[1]
        wps_list = getattr(sl, "waypoints", []) or []
        eps_list = getattr(sl, "endpoints", []) or []
        # GUI-extra parallel lists (defaults to []; per-link element may
        # also be missing — fall back to dataclass defaults in that case).
        vk_list  = getattr(sl, "voltage_kv", []) or []
        lt_list  = getattr(sl, "line_type", []) or []
        lkm_list = getattr(sl, "length_km", []) or []
        bi_list  = getattr(sl, "base_impedance", []) or []
        rpk_list = getattr(sl, "reactance_per_km", []) or []
        sus_list = getattr(sl, "susceptance_pu", []) or []
        nc_list  = getattr(sl, "num_circuits", []) or []
        fz_list  = getattr(sl, "frequency_hz", []) or []
        ct_list  = getattr(sl, "current_type", []) or []
        dec_list = getattr(sl, "decorative", []) or []
        sty_list = getattr(sl, "style", []) or []
        for i, conn in enumerate(sl.connections):
            if len(conn) < 2:
                continue
            # Rehydrate GUI-only geometry (waypoints + endpoints) so the
            # map can redraw the polyline at the same place it was last
            # saved. Missing or malformed entries silently fall back to
            # empty / None so the round-trip never breaks loading.
            link_waypoints: list[GeoPoint] = []
            raw_wps = wps_list[i] if i < len(wps_list) else []
            for wp in (raw_wps or []):
                try:
                    link_waypoints.append(
                        GeoPoint(float(wp["lat"]), float(wp["lng"])))
                except (KeyError, TypeError, ValueError):
                    pass
            from_ep = to_ep = None
            raw_eps = eps_list[i] if i < len(eps_list) else []
            if isinstance(raw_eps, list) and len(raw_eps) >= 2:
                from_d, to_d = raw_eps[0], raw_eps[1]
                try:
                    if from_d and "element_type" in from_d and "element_id" in from_d:
                        from_ep = EndpointRef(str(from_d["element_type"]),
                                              str(from_d["element_id"]))
                    if to_d and "element_type" in to_d and "element_id" in to_d:
                        to_ep = EndpointRef(str(to_d["element_type"]),
                                            str(to_d["element_id"]))
                except (TypeError, ValueError):
                    pass

            # GuiInterSystemLink defaults to a purple VisualStyle; only
            # override fields that were actually persisted (so a missing
            # color in the YAML stays at the default purple).
            from esfex.visualization.data.gui_model import VisualStyle
            link_style = VisualStyle(color="#8e44ad", width=3.0)
            if i < len(sty_list) and isinstance(sty_list[i], dict):
                sd = sty_list[i]
                if sd.get("color"):
                    link_style.color = sd["color"]
                if sd.get("width") is not None:
                    link_style.width = float(sd["width"])
                if sd.get("opacity") is not None:
                    link_style.opacity = float(sd["opacity"])

            def _pick(seq, idx, default):
                return seq[idx] if idx < len(seq) and seq[idx] not in ("", None) else default

            link = GuiInterSystemLink(
                link_id=f"islink_{counter}",
                link_type="transmission",
                from_system=from_sys,
                to_system=to_sys,
                from_node=conn[0],
                to_node=conn[1],
                capacity_mw=sl.existing_capacity_mw[i] if i < len(sl.existing_capacity_mw) else 0.0,
                max_investment_mw=sl.max_investment_mw[i] if i < len(sl.max_investment_mw) else 0.0,
                investment_cost=sl.investment_cost_per_mw[i] if i < len(sl.investment_cost_per_mw) else 0.0,
                loss_factor=sl.loss_factor[i] if i < len(sl.loss_factor) else 0.0,
                distance_km=sl.distance_km[i] if i < len(sl.distance_km) else 0.0,
                cost_per_mw_km=sl.cost_per_mw_km[i] if i < len(sl.cost_per_mw_km) else 0.0,
                reactance_pu=sl.reactance_pu[i] if i < len(sl.reactance_pu) else 0.01,
                resistance_pu=sl.resistance_pu[i] if i < len(sl.resistance_pu) else 0.001,
                waypoints=link_waypoints,
                from_endpoint=from_ep,
                to_endpoint=to_ep,
                # Restored GUI-extra electrical metadata
                voltage_kv=_pick(vk_list, i, None),
                line_type=_pick(lt_list, i, None),
                length_km=_pick(lkm_list, i, None),
                base_impedance=_pick(bi_list, i, None),
                reactance_per_km=_pick(rpk_list, i, None),
                susceptance_pu=_pick(sus_list, i, None),
                num_circuits=int(_pick(nc_list, i, 1)),
                frequency_hz=float(_pick(fz_list, i, 50.0)),
                current_type=str(_pick(ct_list, i, "AC")),
                decorative=bool(dec_list[i]) if i < len(dec_list) else False,
                style=link_style,
            )
            links.append(link)
            counter += 1
    return links


def inter_system_links_to_config_dict(
    links: list[GuiInterSystemLink],
    config_dict: dict,
) -> None:
    """Write inter-system links back into ``config_dict["meta_network"]``."""
    if not links:
        return
    meta = config_dict.get("meta_network")
    if meta is None:
        return

    # Group by (from_system, to_system)
    groups: dict[tuple[str, str], list[GuiInterSystemLink]] = defaultdict(list)
    for lk in links:
        key = (lk.from_system, lk.to_system)
        groups[key].append(lk)

    systems_links = []
    for (from_sys, to_sys), group in groups.items():
        # Per-link GUI metadata so the map polyline survives save→load.
        # SystemLinkConfig now accepts these as optional fields; the
        # solver pipeline ignores them.
        waypoints_per_link: list[list[dict]] = []
        endpoints_per_link: list[list[dict]] = []
        for lk in group:
            waypoints_per_link.append(
                [{"lat": wp.lat, "lng": wp.lng} for wp in (lk.waypoints or [])]
            )
            from_ep = (
                {"element_type": lk.from_endpoint.element_type,
                 "element_id":   lk.from_endpoint.element_id}
                if lk.from_endpoint else {}
            )
            to_ep = (
                {"element_type": lk.to_endpoint.element_type,
                 "element_id":   lk.to_endpoint.element_id}
                if lk.to_endpoint else {}
            )
            endpoints_per_link.append([from_ep, to_ep])

        # Per-link visual style snapshot (color/width/opacity).
        style_per_link: list[dict] = []
        for lk in group:
            s = lk.style
            style_per_link.append({
                "color":   getattr(s, "color", None),
                "width":   getattr(s, "width", None),
                "opacity": getattr(s, "opacity", None),
            })

        sl = {
            "systems": [from_sys, to_sys],
            "connections": [[lk.from_node, lk.to_node] for lk in group],
            "existing_capacity_MW": [lk.capacity_mw for lk in group],
            "max_investment_MW": [lk.max_investment_mw for lk in group],
            "investment_cost_per_MW": [lk.investment_cost for lk in group],
            "loss_factor": [lk.loss_factor for lk in group],
            "distance_km": [lk.distance_km for lk in group],
            "cost_per_mw_km": [lk.cost_per_mw_km for lk in group],
            "reactance_pu": [lk.reactance_pu for lk in group],
            "resistance_pu": [lk.resistance_pu for lk in group],
            "waypoints": waypoints_per_link,
            "endpoints": endpoints_per_link,
            # Extra LineForm-parity electrical metadata (GUI-only;
            # solver doesn't read these).
            "voltage_kv":       [lk.voltage_kv or 0.0      for lk in group],
            "line_type":        [lk.line_type or ""        for lk in group],
            "length_km":        [lk.length_km or 0.0       for lk in group],
            "base_impedance":   [lk.base_impedance or 0.0  for lk in group],
            "reactance_per_km": [lk.reactance_per_km or 0.0 for lk in group],
            "susceptance_pu":   [lk.susceptance_pu or 0.0  for lk in group],
            "num_circuits":     [int(lk.num_circuits or 1) for lk in group],
            "frequency_hz":     [lk.frequency_hz or 0.0    for lk in group],
            "current_type":     [lk.current_type or "AC"   for lk in group],
            "decorative":       [bool(lk.decorative)       for lk in group],
            "style":            style_per_link,
        }
        systems_links.append(sl)
    meta["systems_links"] = systems_links


def _load_demand_csv(demand_path: str | None, nodes: list[GuiNode]) -> None:
    """Load demand CSV/Excel and populate each node's demand data.

    File-shape semantics:

    * **Multi-column file** (``df.shape[1] > 1``): wide layout, one
      column per node; node ``i`` reads column ``node.index``.
    * **Single-column file**: per-node format. Only valid when the
      caller passes exactly one node — broadcasting one column to many
      nodes silently assigns identical demand to all of them, which
      is almost always a config mistake (this used to be the source
      of the "same demand for every node" bug). When multiple nodes
      are passed with a single-column file, we log a warning and bail
      out so the user notices the misconfiguration instead of seeing
      silently broadcast data.
    """
    if not demand_path:
        return
    p = Path(demand_path)
    # Reject `..` traversal in relative paths (e.g. a yaml that says
    # demand_path: "../../home/user/.ssh/id_rsa" would otherwise
    # silently try to read it as a CSV — info disclosure via error
    # messages or partial parse). Absolute paths are allowed as-is
    # since users do sometimes point at data outside cwd intentionally.
    if not p.is_absolute():
        from esfex.utils.paths import safe_resolve_under
        try:
            p = safe_resolve_under(Path.cwd(), demand_path)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "Refusing demand_path %r: traversal out of cwd not allowed",
                demand_path,
            )
            return
    if not p.is_file():
        return
    try:
        import pandas as pd

        if p.suffix in (".xlsx", ".xls"):
            df = pd.read_excel(p, header=None)
        else:
            df = pd.read_csv(p, header=None)
    except Exception:
        return

    if df.shape[1] == 1 and len(nodes) > 1:
        import logging
        logging.getLogger(__name__).warning(
            "demand_path %r is a single-column file but %d nodes were "
            "passed for shared loading; refusing to broadcast the same "
            "series to all nodes. Use per-node demand_paths instead.",
            demand_path, len(nodes),
        )
        return

    for node in nodes:
        col_idx = node.index
        if df.shape[1] == 1:
            series = df.iloc[:, 0].astype(float)
        elif col_idx < df.shape[1]:
            series = df.iloc[:, col_idx].astype(float)
        else:
            continue
        data_list = series.tolist()
        node.demand = GuiNodeDemand(
            csv_path=demand_path,
            data=data_list,
            num_hours=len(data_list),
            peak_mw=float(series.max()),
            total_mwh=float(series.sum()),
        )


def _parse_allowed_technologies(raw) -> dict[str, float]:
    """Convert allowed_technologies from various formats to dict[str, float]."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): float(v) for k, v in raw.items()}
    if isinstance(raw, (list, tuple)):
        # Legacy format: list of tech_id strings → each gets 0.0 (unlimited)
        return {str(k): 0.0 for k in raw}
    return {}


def _system_to_gui_state(sys: SystemConfig) -> GuiSystemState:
    n = sys.num_nodes

    # Nodes — populate centroid from node_coordinates when available
    nodes: list[GuiNode] = []
    for i in range(n):
        name = f"Node {i}"
        if sys.nodes.node_names and i < len(sys.nodes.node_names):
            name = sys.nodes.node_names[i]
        coord = (
            sys.nodes.node_coordinates[i]
            if sys.nodes.node_coordinates and i < len(sys.nodes.node_coordinates)
            else None
        )
        nodes.append(
            GuiNode(
                index=i,
                name=name,
                centroid_lat=coord.latitude if coord else 0.0,
                centroid_lng=coord.longitude if coord else 0.0,
                reserve_static=sys.nodes.reserve_static[i] if i < len(sys.nodes.reserve_static) else 0,
                reserve_dynamic=sys.nodes.reserve_dynamic[i] if i < len(sys.nodes.reserve_dynamic) else 0,
                reserve_duration=sys.nodes.reserve_duration[i] if i < len(sys.nodes.reserve_duration) else 1,
                losses=sys.nodes.losses[i] if i < len(sys.nodes.losses) else 0,
                transference_invest_cost=(
                    sys.nodes.transference_invest_cost[i * n + i]
                    if i * n + i < len(sys.nodes.transference_invest_cost)
                    else 0
                ),
                transference_invest_max=(
                    sys.nodes.transference_invest_max[i * n + i]
                    if i * n + i < len(sys.nodes.transference_invest_max)
                    else 0
                ),
            )
        )

    # Transmission lines: prefer geo entries with line_id (new format),
    # fall back to adjacency matrix extraction (old format)
    lines: list[GuiTransmissionLine] = []
    line_counter = 0
    has_new_format = any(lg.line_id for lg in sys.transmission_lines_geo)

    if has_new_format:
        # New format: one geo entry per line, with unique line_id
        for lg in sys.transmission_lines_geo:
            lid = lg.line_id or f"line_{line_counter}"
            line_counter += 1
            # Use per-line capacity from geo entry (NOT adjacency matrix which sums parallel lines)
            cap = getattr(lg, 'capacity_mw', 0) or 0
            if cap <= 0:
                # Fallback to adjacency matrix only if per-line capacity is missing
                conns = np.array(sys.nodes.nodes_connections).reshape(n, n)
                cap = max(conns[lg.from_node, lg.to_node], conns[lg.to_node, lg.from_node])
            line = GuiTransmissionLine(
                line_id=lid,
                from_node=lg.from_node,
                to_node=lg.to_node,
                capacity_mw=cap if cap > 0 else 100.0,
                voltage_kv=_normalize_voltage_kv(lg.voltage_kv) if lg.voltage_kv else None,
                line_type=lg.line_type,
                waypoints=[GeoPoint(wp.latitude, wp.longitude) for wp in (getattr(lg, 'waypoints', None) or [])],
                from_endpoint=EndpointRef(lg.from_endpoint_type, lg.from_endpoint_id)
                    if lg.from_endpoint_type and lg.from_endpoint_id
                    else EndpointRef("node", str(lg.from_node)),
                to_endpoint=EndpointRef(lg.to_endpoint_type, lg.to_endpoint_id)
                    if lg.to_endpoint_type and lg.to_endpoint_id
                    else EndpointRef("node", str(lg.to_node)),
                length_km=lg.length_km,
                reactance_pu=lg.reactance_pu,
                resistance_pu=lg.resistance_pu,
                susceptance_pu=lg.susceptance_pu,
                num_circuits=lg.num_circuits,
                frequency_hz=getattr(lg, 'frequency_hz', 50.0),
                current_type=getattr(lg, 'current_type', 'AC'),
            )
            lines.append(line)
    else:
        # Old format: extract from adjacency matrix
        if n == 0:
            # Empty system (n=0) — np.array.reshape(0, 0) fails when the
            # raw list isn't already empty (e.g. defaults to [0]).
            # Skip the legacy adjacency walk entirely; there's nothing
            # to read. Lets empty/fresh projects load without crashing
            # the GUI.
            conns = np.zeros((0, 0))
        else:
            conns = np.array(sys.nodes.nodes_connections).reshape(n, n)
        for i in range(n):
            for j in range(i + 1, n):
                cap = max(conns[i, j], conns[j, i])
                if cap > 0:
                    lid = f"line_{line_counter}"
                    line_counter += 1
                    line = GuiTransmissionLine(
                        line_id=lid,
                        from_node=i, to_node=j, capacity_mw=cap,
                        from_endpoint=EndpointRef("node", str(i)),
                        to_endpoint=EndpointRef("node", str(j)),
                    )
                    for lg in sys.transmission_lines_geo:
                        if (lg.from_node == i and lg.to_node == j) or (lg.from_node == j and lg.to_node == i):
                            line.voltage_kv = _normalize_voltage_kv(lg.voltage_kv) if lg.voltage_kv else None
                            line.line_type = lg.line_type
                            line.waypoints = [
                                GeoPoint(wp.latitude, wp.longitude) for wp in (getattr(lg, 'waypoints', None) or [])
                            ]
                            line.length_km = lg.length_km
                            line.reactance_pu = lg.reactance_pu
                            line.resistance_pu = lg.resistance_pu
                            line.susceptance_pu = lg.susceptance_pu
                            line.num_circuits = lg.num_circuits
                            break
                    lines.append(line)

    # Buses
    buses: dict[str, GuiBus] = {}
    next_bus_id = 0
    for bc in getattr(sys, 'buses', []):
        bid = bc.bus_id or f"bus_{next_bus_id}"
        next_bus_id += 1
        buses[bid] = GuiBus(
            bus_id=bid,
            name=bc.name or bid.replace("_", " ").title(),
            parent_node=bc.parent_node,
            voltage_kv=_normalize_voltage_kv(bc.voltage_kv),
            frequency_hz=bc.frequency_hz,
            current_type=bc.current_type,
            bus_type=bc.bus_type,
            role=bc.role,
            demand_fraction=bc.demand_fraction,
        )
        # Track counter
        if bid.startswith("bus_"):
            try:
                num = int(bid[4:])
                if num >= next_bus_id:
                    next_bus_id = num + 1
            except ValueError:
                pass

    # Auto-create one default bus per node when config defines no buses.
    # This ensures the electrical topology is always bus-level.
    if not buses:
        for node_idx in range(n):
            bid = f"bus_{next_bus_id}"
            next_bus_id += 1
            node = nodes[node_idx] if node_idx < len(nodes) else None
            buses[bid] = GuiBus(
                bus_id=bid,
                name=node.name if node else f"Bus {node_idx}",
                parent_node=node_idx,
                demand_fraction=1.0,
            )

    # Build node → bus mapping (first bus per node, for wiring equipment)
    _node_to_bus: dict[int, str] = {}
    # Also build node → ALL buses mapping for nearest-bus lookup
    _node_to_buses: dict[int, list[str]] = defaultdict(list)
    for bid, bus in buses.items():
        if bus.parent_node not in _node_to_bus:
            _node_to_bus[bus.parent_node] = bid
        _node_to_buses[bus.parent_node].append(bid)

    # Load gui_layout early so presence checks can use it
    gui_layout = getattr(sys, 'gui_layout', None) or {}

    # Generators -> instances (split per-node arrays into per-instance scalars)
    generators: dict[str, GuiGeneratorInstance] = {}
    _gen_layout_ids = set(gui_layout.get("generators", {}).keys()) if gui_layout else set()
    for key, gen in sys.generators.items():
        for node_idx in range(n):
            instance_id_check = f"{key}_n{node_idx}"
            # Check if this generator has presence at this node:
            # either non-zero capacity/investment, or explicitly in gui_layout
            in_layout = instance_id_check in _gen_layout_ids
            has_presence = in_layout or any(
                getattr(gen, f)[node_idx] > 0
                for f in _GEN_PRESENCE_FIELDS
                if node_idx < len(getattr(gen, f))
            )
            if not has_presence:
                continue
            instance_id = f"{key}_n{node_idx}"
            inst = GuiGeneratorInstance(
                instance_id=instance_id,
                unit_key=key,
                name=gen.name,
                gen_type=gen.type,
                fuel=gen.fuel,
                node=node_idx,
                reservable=gen.reservable,
                technology_id=gen.technology,
                availability_file=gen.availability_file,
                frequency_hz=getattr(gen, 'frequency_hz', 50.0),
                current_type=getattr(gen, 'current_type', 'AC'),
                reservoir_inflow_file=getattr(gen, 'reservoir_inflow_file', None),
                reservoir_spillage_allowed=getattr(gen, 'reservoir_spillage_allowed', True),
                cascade_downstream=getattr(gen, 'cascade_downstream', '') or '',
                cascade_delay_hours=int(getattr(gen, 'cascade_delay_hours', 0) or 0),
            )
            # Copy scalar values from per-node arrays
            for f in _GEN_SCALAR_FIELDS:
                arr = getattr(gen, f, None)
                if arr is not None and node_idx < len(arr):
                    setattr(inst, f, arr[node_idx])
            # Import fuel cost curve (per-node)
            fcc = getattr(gen, 'fuel_cost_curve', None)
            if fcc and node_idx < len(fcc):
                curve = fcc[node_idx]
                inst.fuel_cost_curve_type = curve.curve_type
                inst.fuel_cost_curve_data = _cost_curve_to_gui_data(curve)
            generators[instance_id] = inst

    # Batteries -> instances
    batteries: dict[str, GuiBatteryInstance] = {}
    _bat_layout_ids = set(gui_layout.get("batteries", {}).keys()) if gui_layout else set()
    for key, bat in sys.batteries.items():
        for node_idx in range(n):
            instance_id_check = f"{key}_n{node_idx}"
            in_layout = instance_id_check in _bat_layout_ids
            has_presence = in_layout or any(
                getattr(bat, f)[node_idx] > 0
                for f in _BAT_PRESENCE_FIELDS
                if node_idx < len(getattr(bat, f))
            )
            if not has_presence:
                continue
            instance_id = f"{key}_n{node_idx}"
            inst = GuiBatteryInstance(
                instance_id=instance_id,
                unit_key=key,
                name=bat.name,
                fuel=bat.fuel,
                node=node_idx,
                reservable=bat.reservable,
                spillage=bat.spillage,
                min_duration_hours=bat.min_duration_hours,
                max_duration_hours=bat.max_duration_hours,
                availability_file=bat.availability_file,
                current_type=getattr(bat, 'current_type', 'DC'),
            )
            for f in _BAT_SCALAR_FIELDS:
                arr = getattr(bat, f, None)
                if arr is not None and node_idx < len(arr):
                    setattr(inst, f, arr[node_idx])
            # Import discharge cost curve (per-node)
            dcc = getattr(bat, 'discharge_cost_curve', None)
            if dcc and node_idx < len(dcc):
                curve = dcc[node_idx]
                inst.discharge_cost_curve_type = curve.curve_type
                inst.discharge_cost_curve_data = _cost_curve_to_gui_data(curve)
            batteries[instance_id] = inst

    # Transformers (from_node/to_node, with fallback from legacy single 'node')
    # Build integer-index → bus_id mapping so transformers can reference buses
    _bus_id_by_index: list[str] = list(buses.keys())

    # Helper: pick a sibling bus on a given node whose voltage matches
    # the requested kV (within 10 %). Used to repair legacy YAMLs where
    # multi-voltage transformers were saved with from_bus == to_bus.
    def _sibling_bus_at_voltage(
        node_idx: int, target_kv: float, exclude_bus: str,
    ) -> str:
        best_id, best_diff = "", float("inf")
        for bid, bus in buses.items():
            if bid == exclude_bus or bus.parent_node != node_idx:
                continue
            diff = abs(bus.voltage_kv - target_kv)
            if diff < best_diff and diff / max(target_kv, 1e-3) < 0.10:
                best_id, best_diff = bid, diff
        return best_id

    transformers = []
    for t in sys.transformers:
        # Resolve bus IDs from integer indices
        from_bus_id = (
            _bus_id_by_index[t.from_bus]
            if t.from_bus is not None and 0 <= t.from_bus < len(_bus_id_by_index)
            else _node_to_bus.get(t.from_node, "bus_0")
        )
        to_bus_id = (
            _bus_id_by_index[t.to_bus]
            if t.to_bus is not None and 0 <= t.to_bus < len(_bus_id_by_index)
            else _node_to_bus.get(t.to_node, "bus_0")
        )

        # Legacy self-loop repair: pre-fix-era YAMLs persisted multi-
        # voltage transformers with from_bus == to_bus. Two cases:
        #   (a) voltages still distinct in YAML → use them to pick
        #       which side to relocate.
        #   (b) voltages also collapsed (same on both sides) → still
        #       a self-loop; relocate the to-side to ANY sibling bus
        #       on the same node, preferring one with a different
        #       voltage (typical HV/LV substation pattern).
        if from_bus_id == to_bus_id:
            stuck_bus = buses.get(from_bus_id)
            if stuck_bus is not None:
                voltage_mismatch = abs(t.from_voltage_kv - t.to_voltage_kv) > 0.1
                if voltage_mismatch:
                    stuck_v = stuck_bus.voltage_kv
                    if abs(stuck_v - t.from_voltage_kv) <= abs(stuck_v - t.to_voltage_kv):
                        target = _sibling_bus_at_voltage(
                            t.to_node, t.to_voltage_kv, from_bus_id,
                        )
                        if target:
                            to_bus_id = target
                    else:
                        target = _sibling_bus_at_voltage(
                            t.from_node, t.from_voltage_kv, to_bus_id,
                        )
                        if target:
                            from_bus_id = target
                else:
                    # Voltages also collapsed: pick any other bus in
                    # the same node, preferring one at a different
                    # voltage (HV ↔ LV is the typical case).
                    target = ""
                    best_diff = -1.0
                    for bid, bus in buses.items():
                        if bid == from_bus_id:
                            continue
                        if bus.parent_node != t.to_node:
                            continue
                        diff = abs(bus.voltage_kv - stuck_bus.voltage_kv)
                        if diff > best_diff:
                            best_diff, target = diff, bid
                    if target:
                        to_bus_id = target

        # Derive voltages from the actual buses (more reliable than stale
        # from_voltage_kv / to_voltage_kv stored on the transformer config)
        from_bus_obj = buses.get(from_bus_id)
        to_bus_obj = buses.get(to_bus_id)
        from_v = from_bus_obj.voltage_kv if from_bus_obj else _normalize_voltage_kv(t.from_voltage_kv)
        to_v = to_bus_obj.voltage_kv if to_bus_obj else _normalize_voltage_kv(t.to_voltage_kv)

        transformers.append(GuiTransformer(
            name=t.name,
            from_bus=from_bus_id,
            to_bus=to_bus_id,
            from_node=t.from_node,
            to_node=t.to_node,
            from_voltage_kv=from_v,
            to_voltage_kv=to_v,
            rated_power_mva=t.rated_power_mva, impedance_pu=t.impedance_pu,
            losses_fraction=t.losses_fraction,
        ))

    # AC/DC Converters
    acdc_converters = []
    if hasattr(sys, 'acdc_converters') and sys.acdc_converters:
        for conv in sys.acdc_converters:
            acdc_converters.append(GuiACDCConverter(
                name=conv.name,
                converter_type=getattr(conv, 'converter_type', 'VSC'),
                from_node=conv.from_node,
                to_node=conv.to_node,
                from_voltage_kv=_normalize_voltage_kv(getattr(conv, 'from_voltage_kv', 220.0)),
                dc_voltage_kv=_normalize_voltage_kv(getattr(conv, 'dc_voltage_kv', 320.0)),
                rated_power_mva=getattr(conv, 'rated_power_mva', 100.0),
                min_power_mva=getattr(conv, 'min_power_mva', 0.0),
                efficiency_rectify=getattr(conv, 'efficiency_rectify', 0.98),
                efficiency_invert=getattr(conv, 'efficiency_invert', 0.98),
                standby_losses_mw=getattr(conv, 'standby_losses_mw', 0.5),
                reactive_power_min_mvar=getattr(conv, 'reactive_power_min_mvar', -50.0),
                reactive_power_max_mvar=getattr(conv, 'reactive_power_max_mvar', 50.0),
                power_factor=getattr(conv, 'power_factor', 1.0),
                impedance_pu=getattr(conv, 'impedance_pu', 0.05),
                resistance_pu=getattr(conv, 'resistance_pu', 0.01),
                fixed_cost=getattr(conv, 'fixed_cost', 0.0),
                variable_cost=getattr(conv, 'variable_cost', 0.0),
                life_time=getattr(conv, 'life_time', 30),
                initial_age=getattr(conv, 'initial_age', 0),
                degradation_rate=getattr(conv, 'degradation_rate', 0.005),
            ))

    # Frequency Converters
    freq_converters = []
    if hasattr(sys, 'freq_converters') and sys.freq_converters:
        for conv in sys.freq_converters:
            freq_converters.append(GuiFrequencyConverter(
                name=conv.name,
                from_node=conv.from_node,
                to_node=conv.to_node,
                from_frequency_hz=getattr(conv, 'from_frequency_hz', 50.0),
                to_frequency_hz=getattr(conv, 'to_frequency_hz', 60.0),
                rated_power_mva=getattr(conv, 'rated_power_mva', 100.0),
                min_power_mva=getattr(conv, 'min_power_mva', 0.0),
                efficiency_a_to_b=getattr(conv, 'efficiency_a_to_b', 0.98),
                efficiency_b_to_a=getattr(conv, 'efficiency_b_to_a', 0.98),
                standby_losses_mw=getattr(conv, 'standby_losses_mw', 0.5),
                reactive_power_min_mvar=getattr(conv, 'reactive_power_min_mvar', -50.0),
                reactive_power_max_mvar=getattr(conv, 'reactive_power_max_mvar', 50.0),
                impedance_pu=getattr(conv, 'impedance_pu', 0.05),
                resistance_pu=getattr(conv, 'resistance_pu', 0.01),
                fixed_cost=getattr(conv, 'fixed_cost', 0.0),
                variable_cost=getattr(conv, 'variable_cost', 0.0),
                life_time=getattr(conv, 'life_time', 30),
                initial_age=getattr(conv, 'initial_age', 0),
                degradation_rate=getattr(conv, 'degradation_rate', 0.005),
            ))

    # Development zones
    zones = [
        GuiDevelopmentZone(
            name=z.name, technology=z.technology, layer=z.layer,
            polygon=[GeoPoint(p.latitude, p.longitude) for p in z.polygon],
            max_capacity_mw=z.max_capacity_mw, notes=z.notes,
            line_cost_per_mw_km=getattr(z, 'line_cost_per_mw_km', 1500.0),
            transformer_cost_per_mw=getattr(z, 'transformer_cost_per_mw', 50000.0),
            target_bus_override=getattr(z, 'target_bus', None),
            allowed_generators=list(getattr(z, 'allowed_generators', None) or []),
            allowed_technologies=_parse_allowed_technologies(getattr(z, 'allowed_technologies', None)),
            exclusive=getattr(z, 'exclusive', False),
        )
        for z in sys.development_zones
    ]

    # Fuel entry points
    fuel_entries = []
    for fe in sys.fuel_entry_points:
        # Support both old single-fuel and new multi-fuel format
        fuels_list = getattr(fe, 'fuels', None)
        if fuels_list is None:
            single = getattr(fe, 'fuel', '')
            fuels_list = [single] if single else []

        # Per-fuel parameters (new format) or fallback from global values (old format)
        raw_fp = getattr(fe, 'fuel_params', None)
        if isinstance(raw_fp, dict) and raw_fp:
            fuel_params = {
                fname: FuelEntryParams(
                    max_import_rate=fdata.get("max_import_rate", 0.0) if isinstance(fdata, dict) else getattr(fdata, 'max_import_rate', 0.0),
                    import_cost=fdata.get("import_cost", 0.0) if isinstance(fdata, dict) else getattr(fdata, 'import_cost', 0.0),
                )
                for fname, fdata in raw_fp.items()
            }
        else:
            # Old format: global max_import_rate/import_cost → apply to all fuels
            mir = getattr(fe, 'max_import_rate', 0.0)
            ic = getattr(fe, 'import_cost', 0.0)
            fuel_params = {f: FuelEntryParams(max_import_rate=mir, import_cost=ic) for f in fuels_list}

        fuel_entries.append(GuiFuelEntryPoint(
            name=fe.name, fuels=fuels_list, node=fe.node,
            coordinate=GeoPoint(fe.coordinate.latitude, fe.coordinate.longitude, fe.name),
            fuel_params=fuel_params,
        ))

    # Primary energy sources
    fuel_sources: dict[str, GuiFuelSource] = {}
    if hasattr(sys, 'primary_energy_sources') and sys.primary_energy_sources:
        for src_key, src_cfg in sys.primary_energy_sources.items():
            fuel_sources[src_key] = GuiFuelSource(
                source_id=src_key,
                name=src_cfg.name,
                unit=src_cfg.unit,
                max_availability=list(src_cfg.max_availability),
                import_cost=list(src_cfg.import_cost),
                storage_capacity=list(src_cfg.storage_capacity),
                initial_storage_level=list(src_cfg.initial_storage_level),
                min_storage_level=src_cfg.min_storage_level,
                storage_investment_cost=src_cfg.storage_investment_cost,
                transport_cost=src_cfg.transport_cost,
                transport_losses=src_cfg.transport_losses,
                max_storage_investment_per_node=src_cfg.max_storage_investment_per_node,
                max_transport_investment_per_arc=src_cfg.max_transport_investment_per_arc,
            )

    # Fuel transport routes
    fuel_routes: list[GuiFuelTransportRoute] = []
    fuel_route_counter = 0
    if hasattr(sys, 'fuel_infrastructure') and sys.fuel_infrastructure:
        pipes = getattr(sys.fuel_infrastructure, 'transport_pipelines', None) or {}
        for pipe_key, pipe_data in pipes.items():
            rid = pipe_data.get("route_id", f"fuel_route_{fuel_route_counter}")
            fuel_route_counter += 1
            from_node = pipe_data.get("from_node", 0)
            to_node = pipe_data.get("to_node", 0)
            wps = [
                GeoPoint(wp["latitude"], wp["longitude"])
                for wp in pipe_data.get("waypoints", [])
            ]
            # Support both old single-fuel and new multi-fuel format
            fuels_raw = pipe_data.get("fuels", None)
            if fuels_raw is None:
                single = pipe_data.get("fuel", "")
                fuels_raw = [single] if single else []

            # Per-fuel params (new format) or fallback from global values (old format)
            raw_fp = pipe_data.get("fuel_params")
            if isinstance(raw_fp, dict) and raw_fp:
                fuel_params = {
                    fname: FuelRouteParams(
                        capacity=fdata.get("capacity", 0.0) if isinstance(fdata, dict) else 0.0,
                        transport_cost=fdata.get("transport_cost", 0.0) if isinstance(fdata, dict) else 0.0,
                        losses_fraction=fdata.get("losses_fraction", 0.0) if isinstance(fdata, dict) else 0.0,
                    )
                    for fname, fdata in raw_fp.items()
                }
            else:
                # Old format: global capacity/transport_cost/losses → apply to all fuels
                cap = pipe_data.get("capacity", 0.0)
                tc = pipe_data.get("transport_cost", 0.0)
                lf = pipe_data.get("losses_fraction", 0.0)
                fuel_params = {f: FuelRouteParams(capacity=cap, transport_cost=tc, losses_fraction=lf) for f in fuels_raw}

            route = GuiFuelTransportRoute(
                route_id=rid,
                fuels=fuels_raw,
                from_node=from_node,
                to_node=to_node,
                capacity=pipe_data.get("capacity", 0.0),
                transport_cost=pipe_data.get("transport_cost", 0.0),
                losses_fraction=pipe_data.get("losses_fraction", 0.0),
                fuel_params=fuel_params,
                length_km=pipe_data.get("length_km"),
                waypoints=wps,
                from_endpoint=EndpointRef(pipe_data["from_endpoint_type"], pipe_data["from_endpoint_id"])
                    if pipe_data.get("from_endpoint_type") and pipe_data.get("from_endpoint_id")
                    else EndpointRef("node", str(from_node)),
                to_endpoint=EndpointRef(pipe_data["to_endpoint_type"], pipe_data["to_endpoint_id"])
                    if pipe_data.get("to_endpoint_type") and pipe_data.get("to_endpoint_id")
                    else EndpointRef("node", str(to_node)),
            )
            fuel_routes.append(route)

    # Fuel storage facilities
    fuel_storages: dict[str, GuiFuelStorage] = {}
    if hasattr(sys, 'fuel_infrastructure') and sys.fuel_infrastructure:
        stores = getattr(sys.fuel_infrastructure, 'storage_facilities', None) or {}
        for sid, sdata in stores.items():
            if isinstance(sdata, dict):
                # Support both old single-fuel and new multi-fuel format
                raw_fp = sdata.get("fuel_params")
                if isinstance(raw_fp, dict) and raw_fp:
                    fuels_list = sdata.get("fuels", list(raw_fp.keys()))
                    fp = {
                        fname: FuelStorageParams(
                            capacity=fdata.get("capacity", 0.0),
                            initial_level=fdata.get("initial_level", 0.5),
                            min_level=fdata.get("min_level", 0.1),
                        )
                        for fname, fdata in raw_fp.items()
                    }
                else:
                    # Old format: single fuel with global params
                    old_fuel = sdata.get("fuel", "")
                    fuels_list = [old_fuel] if old_fuel else []
                    if old_fuel:
                        fp = {old_fuel: FuelStorageParams(
                            capacity=sdata.get("capacity", 0.0),
                            initial_level=sdata.get("initial_level", 0.5),
                            min_level=sdata.get("min_level", 0.1),
                        )}
                    else:
                        fp = {}
                fuel_storages[sid] = GuiFuelStorage(
                    storage_id=sid,
                    name=sdata.get("name", sid),
                    fuels=fuels_list,
                    fuel_params=fp,
                    node=sdata.get("node", 0),
                )

    # Map center
    map_center = None
    if sys.map_center:
        map_center = GeoPoint(sys.map_center.latitude, sys.map_center.longitude)

    # Fuels (FuelConfig)
    fuels: dict[str, GuiFuel] = {}
    if hasattr(sys, 'fuels') and sys.fuels:
        for fid, fc in sys.fuels.items():
            fuels[fid] = GuiFuel(
                fuel_id=fid,
                name=fc.name,
                unit=getattr(fc, 'unit', None),
                emission_factor=getattr(fc, 'emission_factor', 0.0),
                energy_content=getattr(fc, 'energy_content', None),
                price_base=getattr(fc, 'price_base', 0.0),
                price_growth_rate=getattr(fc, 'price_growth_rate', 0.0),
            )

    # System settings
    settings = GuiSystemSettings(
        demand_scale=getattr(sys, 'demand_scale', 1.0),
        discount_rate=getattr(sys, 'discount_rate', 0.05),
        base_lcoe=getattr(sys, 'base_lcoe', 93.0),
        target_re_penetration=getattr(sys, 'target_re_penetration', 1.0),
        min_annual_increment=getattr(sys, 'min_annual_increment', 0.01),
        max_annual_increment=getattr(sys, 'max_annual_increment', 0.10),
        max_annual_system_cost=getattr(sys, 'max_annual_system_cost', 20e9),
        max_npv_penalty_per_mw=getattr(sys, 'max_npv_penalty_per_mw', 1e6),
        max_decommission_cost_per_mw=getattr(sys, 'max_decommission_cost_per_mw', 5e5),
        force_replacement=getattr(sys, 'force_replacement', -5e5),
        life_extension_cost_factor=getattr(sys, 'life_extension_cost_factor', 0.20),
        loss_demand_threshold=getattr(sys, 'loss_demand_threshold', 0.05),
        inertia_limit_threshold=getattr(sys, 'inertia_limit_threshold', 0.1),
        sim_rooftop=getattr(sys, 'sim_rooftop', False),
    )

    # Penalties
    penalties = GuiPenalties()
    if hasattr(sys, 'penalties') and sys.penalties:
        pen = sys.penalties
        for fld in (
            'loss_of_load', 'loss_of_reserve_static', 'loss_of_reserve_dynamic',
            'loss_of_inertia', 'transfer_margin', 'curtailment',
            'max_curtailment_ratio', 'curtailment_cost',
            'curtailment_excess_penalty', 're_excess_penalty',
            'rooftop_curtailment', 'co2_cost',
            'co2_budget_violation', 'fre_penetration_loss', 'ev_loss',
            'loss_of_fuel_supply', 'coupling_slack_penalty',
            'transport_congestion', 'storage_violation',
            'non_electric_demand_loss',
        ):
            if hasattr(pen, fld):
                setattr(penalties, fld, getattr(pen, fld))

    # CO2 Budget → merged into settings
    if hasattr(sys, 'co2_budget') and sys.co2_budget:
        settings.co2_budget_enabled = getattr(sys.co2_budget, 'enabled', True)
        settings.co2_annual_budget = getattr(sys.co2_budget, 'annual_budget', 1e6)

    # Power flow mode
    power_flow_mode = getattr(sys, 'power_flow_mode', 'dcopf')

    # DC Power Flow (system-level: angle limits + slack bus only)
    dc_pf = GuiDCPowerFlow()
    if hasattr(sys, 'dc_power_flow') and sys.dc_power_flow:
        dc = sys.dc_power_flow
        dc_pf.max_angle_diff_deg = getattr(dc, 'max_angle_diff_deg', 30.0)
        dc_pf.slack_bus = getattr(dc, 'slack_bus', 0)

    # AC Power Flow
    ac_pf = GuiACPowerFlow()
    if hasattr(sys, 'ac_power_flow') and sys.ac_power_flow:
        ac = sys.ac_power_flow
        ac_pf.base_mva = getattr(ac, 'base_mva', 100.0)
        ac_pf.voltage_min_pu = getattr(ac, 'voltage_min_pu', 0.90)
        ac_pf.voltage_max_pu = getattr(ac, 'voltage_max_pu', 1.10)
        ac_pf.default_power_factor = getattr(ac, 'default_power_factor', 0.85)
        ac_pf.load_power_factor = getattr(ac, 'load_power_factor', 0.9)
        ac_pf.q_slack_penalty = getattr(ac, 'q_slack_penalty', 100.0)
        ac_pf.min_reactance_pu = getattr(ac, 'min_reactance_pu', 0.01)
        ac_pf.tap_ratio_min = getattr(ac, 'tap_ratio_min', 0.5)
        ac_pf.tap_ratio_max = getattr(ac, 'tap_ratio_max', 2.0)
        ac_pf.q_min_ratio = getattr(ac, 'q_min_ratio', 0.5)

    # Criticality penalties → merged into penalties
    if hasattr(sys, 'criticality_penalties') and sys.criticality_penalties:
        cp = sys.criticality_penalties
        for lvl in ('critical', 'high', 'medium', 'low'):
            if hasattr(cp, lvl):
                setattr(penalties, f'criticality_{lvl}', getattr(cp, lvl))

    # Electrolyzers — sys.electrolyzers is a dict[str, ElectrolyzerConfig]
    electrolyzers: dict[str, GuiElectrolyzerInstance] = {}
    _elec_layout_ids = set(gui_layout.get("electrolyzers", {}).keys()) if gui_layout else set()
    for el_key, el_cfg in sys.electrolyzers.items():
        for node_idx in range(n):
            iid_check = f"{el_key}_n{node_idx}"
            in_layout = iid_check in _elec_layout_ids
            rp = getattr(el_cfg, 'rated_power', [])
            im = getattr(el_cfg, 'invest_max_power', [])
            has_rp = node_idx < len(rp) and rp[node_idx] > 0
            has_im = node_idx < len(im) and im[node_idx] > 0
            if not in_layout and not has_rp and not has_im:
                continue
            iid = f"{el_key}_n{node_idx}"
            inst = GuiElectrolyzerInstance(
                instance_id=iid,
                unit_key=el_key,
                name=getattr(el_cfg, 'name', el_key),
                fuel=getattr(el_cfg, 'fuel', 'Hydrogen'),
                technology=getattr(el_cfg, 'technology', 'PEM'),
                node=node_idx,
            )
            for fld in (
                'life_time', 'initial_age', 'degradation_rate',
                'rated_power', 'min_power', 'ramp_up', 'ramp_down',
                'eff_at_rated', 'eff_at_min', 'energy_per_kg_h2',
                'fixed_cost', 'variable_cost', 'water_cost',
            ):
                arr = getattr(el_cfg, fld, [])
                if isinstance(arr, list) and node_idx < len(arr):
                    setattr(inst, fld, arr[node_idx])
                elif not isinstance(arr, list):
                    setattr(inst, fld, arr)
            electrolyzers[iid] = inst

    # EV configuration
    ev_config = GuiEVConfig()
    if hasattr(sys, 'ev_initial_soc') and sys.ev_initial_soc:
        ev_config.initial_soc = list(sys.ev_initial_soc)
    if hasattr(sys, 'ev_categories') and sys.ev_categories:
        for cat_id, cat_cfg in sys.ev_categories.items():
            cat = GuiEVCategory(category_id=cat_id)
            for fld in (
                'battery_capacity', 'charging_power', 'v2g_power',
                'v2g_participation', 'efficiency_charge', 'efficiency_discharge',
                'min_soc', 'max_adoption', 'growth_rate', 'mid_point_fraction',
            ):
                if hasattr(cat_cfg, fld):
                    setattr(cat, fld, getattr(cat_cfg, fld))
            # Per-node quantities
            if hasattr(sys, 'ev_quantity') and sys.ev_quantity:
                qty = sys.ev_quantity.get(cat_id, [])
                cat.quantity = list(qty) if qty else [0] * n
            # Base patterns
            if hasattr(sys, 'base_patterns') and sys.base_patterns:
                pat = sys.base_patterns.get(cat_id, [])
                cat.base_pattern = list(pat) if pat else [0.0] * 24
            ev_config.categories[cat_id] = cat

    # Rooftop solar
    rooftop_solar = None
    if hasattr(sys, 'rooftop_solar_config') and sys.rooftop_solar_config:
        rc = sys.rooftop_solar_config
        rooftop_solar = GuiRooftopSolar()
        for fld in (
            'adoption_scenario', 'weather_variability', 'simulation_seed',
            'performance_ratio', 'degradation_rate', 'cost_per_kw',
            'cost_reduction_rate', 'o_and_m_cost', 'base_year', 'target_year',
        ):
            if hasattr(rc, fld):
                setattr(rooftop_solar, fld, getattr(rc, fld))
        for arr_fld in ('systems_per_node', 'avg_system_size', 'initial_adoption'):
            if hasattr(rc, arr_fld):
                setattr(rooftop_solar, arr_fld, list(getattr(rc, arr_fld)))
        if hasattr(rc, 'max_adoption') and rc.max_adoption:
            rooftop_solar.max_adoption = dict(rc.max_adoption)
        if hasattr(rc, 'adoption_rates') and rc.adoption_rates:
            rooftop_solar.adoption_rates = dict(rc.adoption_rates)

    # Demand sectors
    demand_sectors: dict[str, GuiDemandSector] = {}
    if hasattr(sys, 'electric_demand') and sys.electric_demand:
        for sec_id, sec_cfg in sys.electric_demand.items():
            demand_sectors[sec_id] = GuiDemandSector(
                sector_id=sec_id,
                is_flexible=getattr(sec_cfg, 'is_flexible', False),
                flexibility_ratio=getattr(sec_cfg, 'flexibility_ratio', 0.0),
                criticality=str(getattr(sec_cfg, 'criticality', 'medium')),
                delay_tolerance=getattr(sec_cfg, 'delay_tolerance', 0),
                price_sensitivity=getattr(sec_cfg, 'price_sensitivity', 0.0),
            )

    # Non-electric demand
    non_electric: dict[str, GuiNonElectricDemand] = {}
    if hasattr(sys, 'non_electric_demand') and sys.non_electric_demand:
        for did, dcfg in sys.non_electric_demand.items():
            non_electric[did] = GuiNonElectricDemand(
                demand_id=did,
                fuel=getattr(dcfg, 'fuel', ''),
                unit=getattr(dcfg, 'unit', ''),
                is_flexible=getattr(dcfg, 'is_flexible', False),
                flexibility_ratio=getattr(dcfg, 'flexibility_ratio', 0.0),
                criticality=str(getattr(dcfg, 'criticality', 'medium')),
                delay_tolerance=getattr(dcfg, 'delay_tolerance', 0),
                price_sensitivity=getattr(dcfg, 'price_sensitivity', 0.0),
                demand=list(getattr(dcfg, 'demand', [])),
            )

    # Sector distribution
    sector_dist: dict[int, dict[str, float]] = {}
    if hasattr(sys, 'sector_distribution') and sys.sector_distribution:
        for node_key, dist in sys.sector_distribution.items():
            idx = int(node_key) if isinstance(node_key, str) else node_key
            sector_dist[idx] = dict(dist)

    # ── Assign absolute positions to equipment ──
    # Build node coordinate lookup from config (nodes are abstract, not geographic)
    _node_coords: dict[int, tuple[float, float]] = {}
    if sys.nodes.node_coordinates:
        for i, gc in enumerate(sys.nodes.node_coordinates):
            _node_coords[i] = (gc.latitude, gc.longitude)

    # Check if gui_layout uses absolute coords (new format) or offsets (old format)
    # Heuristic: if values are large (>1.0), they're absolute lat/lng; else offsets
    _layout_is_absolute = False
    if gui_layout:
        for section in ("generators", "batteries", "electrolyzers", "transformers", "acdc_converters", "freq_converters", "buses", "fuel_storages"):
            for _k, vals in gui_layout.get(section, {}).items():
                if isinstance(vals, list) and len(vals) == 2:
                    if abs(vals[0]) > 1.0 or abs(vals[1]) > 1.0:
                        _layout_is_absolute = True
                    break
            if _layout_is_absolute:
                break

    # Auto-layout: distribute equipment around their node
    _OFFSET_DEG = 0.012  # ~1.3 km at equator; good visual separation
    node_items: dict[int, list] = defaultdict(list)
    for inst in generators.values():
        node_items[inst.node].append(inst)
    for inst in batteries.values():
        node_items[inst.node].append(inst)
    for inst in electrolyzers.values():
        node_items[inst.node].append(inst)
    for tr in transformers:
        node_items[tr.from_node].append(tr)
    for conv in acdc_converters:
        node_items[conv.from_node].append(conv)
    for conv in freq_converters:
        node_items[conv.from_node].append(conv)
    for ni, items in node_items.items():
        base_lat, base_lng = _node_coords.get(ni, (0.0, 0.0))
        count = len(items)
        if count == 0:
            continue
        if count == 1:
            items[0].latitude = base_lat + _OFFSET_DEG * 0.6
            items[0].longitude = base_lng
            continue
        for i, item in enumerate(items):
            angle = 2 * math.pi * i / count
            item.latitude = base_lat + _OFFSET_DEG * math.cos(angle)
            item.longitude = base_lng + _OFFSET_DEG * math.sin(angle)

    # Auto-position buses at their node center
    for bus in buses.values():
        base_lat, base_lng = _node_coords.get(bus.parent_node, (0.0, 0.0))
        bus.latitude = base_lat
        bus.longitude = base_lng

    # Auto-position fuel storages at their node center
    for fst in fuel_storages.values():
        base_lat, base_lng = _node_coords.get(fst.node, (0.0, 0.0))
        fst.latitude = base_lat
        fst.longitude = base_lng

    # Restore saved equipment layout (overrides auto-layout if present)
    if gui_layout:
        for gid, coords in gui_layout.get("generators", {}).items():
            if gid in generators:
                if _layout_is_absolute:
                    generators[gid].latitude = coords[0]
                    generators[gid].longitude = coords[1]
                else:
                    # Old offset format: convert to absolute
                    base = _node_coords.get(generators[gid].node, (0.0, 0.0))
                    generators[gid].latitude = base[0] + coords[0]
                    generators[gid].longitude = base[1] + coords[1]
        for bid, coords in gui_layout.get("batteries", {}).items():
            if bid in batteries:
                if _layout_is_absolute:
                    batteries[bid].latitude = coords[0]
                    batteries[bid].longitude = coords[1]
                else:
                    base = _node_coords.get(batteries[bid].node, (0.0, 0.0))
                    batteries[bid].latitude = base[0] + coords[0]
                    batteries[bid].longitude = base[1] + coords[1]
        for eid, coords in gui_layout.get("electrolyzers", {}).items():
            if eid in electrolyzers:
                if _layout_is_absolute:
                    electrolyzers[eid].latitude = coords[0]
                    electrolyzers[eid].longitude = coords[1]
                else:
                    base = _node_coords.get(electrolyzers[eid].node, (0.0, 0.0))
                    electrolyzers[eid].latitude = base[0] + coords[0]
                    electrolyzers[eid].longitude = base[1] + coords[1]
        for key, coords in gui_layout.get("transformers", {}).items():
            # New format: index-based key (e.g., "0", "1")
            # Legacy format: "tr_{from_node}_{to_node}" (e.g., "tr_0_1")
            target_tr = None
            if key.isdigit():
                idx = int(key)
                if 0 <= idx < len(transformers):
                    target_tr = transformers[idx]
            else:
                # Legacy format fallback
                parts = key.split("_")
                if len(parts) == 3:
                    fn, tn = int(parts[1]), int(parts[2])
                    for tr in transformers:
                        if tr.from_node == fn and tr.to_node == tn:
                            target_tr = tr
                            break
            if target_tr is not None:
                if _layout_is_absolute:
                    target_tr.latitude = coords[0]
                    target_tr.longitude = coords[1]
                else:
                    base = _node_coords.get(target_tr.from_node, (0.0, 0.0))
                    target_tr.latitude = base[0] + coords[0]
                    target_tr.longitude = base[1] + coords[1]
        for idx_str, coords in gui_layout.get("acdc_converters", {}).items():
            idx = int(idx_str)
            if 0 <= idx < len(acdc_converters):
                if _layout_is_absolute:
                    acdc_converters[idx].latitude = coords[0]
                    acdc_converters[idx].longitude = coords[1]
                else:
                    base = _node_coords.get(acdc_converters[idx].from_node, (0.0, 0.0))
                    acdc_converters[idx].latitude = base[0] + coords[0]
                    acdc_converters[idx].longitude = base[1] + coords[1]
        for idx_str, coords in gui_layout.get("freq_converters", {}).items():
            idx = int(idx_str)
            if 0 <= idx < len(freq_converters):
                if _layout_is_absolute:
                    freq_converters[idx].latitude = coords[0]
                    freq_converters[idx].longitude = coords[1]
                else:
                    base = _node_coords.get(freq_converters[idx].from_node, (0.0, 0.0))
                    freq_converters[idx].latitude = base[0] + coords[0]
                    freq_converters[idx].longitude = base[1] + coords[1]
        for bus_id, coords in gui_layout.get("buses", {}).items():
            if bus_id in buses:
                if _layout_is_absolute:
                    buses[bus_id].latitude = coords[0]
                    buses[bus_id].longitude = coords[1]
                else:
                    base = _node_coords.get(buses[bus_id].parent_node, (0.0, 0.0))
                    buses[bus_id].latitude = base[0] + coords[0]
                    buses[bus_id].longitude = base[1] + coords[1]
        # For buses not in gui_layout, inherit parent node coordinates
        for bus_id, bus in buses.items():
            if bus_id not in gui_layout.get("buses", {}):
                parent_coords = _node_coords.get(bus.parent_node, (0.0, 0.0))
                bus.latitude = parent_coords[0]
                bus.longitude = parent_coords[1]
        for sid, coords in gui_layout.get("fuel_storages", {}).items():
            if sid in fuel_storages:
                if _layout_is_absolute:
                    fuel_storages[sid].latitude = coords[0]
                    fuel_storages[sid].longitude = coords[1]
                else:
                    base = _node_coords.get(fuel_storages[sid].node, (0.0, 0.0))
                    fuel_storages[sid].latitude = base[0] + coords[0]
                    fuel_storages[sid].longitude = base[1] + coords[1]
        for idx_str, coords in gui_layout.get("fuel_entries", {}).items():
            idx = int(idx_str)
            if 0 <= idx < len(fuel_entries):
                fuel_entries[idx].coordinate = GeoPoint(coords[0], coords[1])

    # Load demand data from CSV into each node. ``demand_paths``
    # (per-node) takes precedence; empty entries fall back to the
    # legacy system-wide ``demand_path`` so old configs keep working.
    if sys.demand_paths:
        for ni, dpath in enumerate(sys.demand_paths):
            if ni < len(nodes):
                _load_demand_csv(dpath or sys.demand_path, [nodes[ni]])
    elif sys.demand_path:
        _load_demand_csv(sys.demand_path, nodes)

    # Build investment portfolio from invest fields in YAML config
    investment_portfolio: dict[str, GuiInvestmentEntry] = {}
    inv_counter = 0

    # Generators
    for key, gen in sys.generators.items():
        ic = getattr(gen, 'invest_cost', [])
        im = getattr(gen, 'invest_max_power', [])
        has_invest = any(
            (i < len(ic) and ic[i] > 0) or (i < len(im) and im[i] > 0)
            for i in range(n)
        )
        if has_invest:
            entry_id = f"inv_{inv_counter}"
            inv_counter += 1
            nd_list = []
            for i in range(n):
                cost_val = ic[i] if i < len(ic) else 0.0
                max_val = im[i] if i < len(im) else 0.0
                if cost_val > 0 or max_val > 0:
                    nd_list.append(GuiInvestmentNodeData(
                        node_index=i, invest_cost=cost_val, invest_max=max_val,
                    ))
            investment_portfolio[entry_id] = GuiInvestmentEntry(
                entry_id=entry_id, name=gen.name,
                technology_type="generator", target_key=key,
                node_data=nd_list,
            )

    # Batteries
    for key, bat in sys.batteries.items():
        ic = getattr(bat, 'invest_cost', [])
        im = getattr(bat, 'invest_max_power', [])
        ice = getattr(bat, 'invest_cost_energy', [])
        imc = getattr(bat, 'invest_max_capacity', [])
        has_invest = any(
            (i < len(ic) and ic[i] > 0) or (i < len(im) and im[i] > 0)
            or (i < len(ice) and ice[i] > 0) or (i < len(imc) and imc[i] > 0)
            for i in range(n)
        )
        if has_invest:
            entry_id = f"inv_{inv_counter}"
            inv_counter += 1
            nd_list = []
            cost_energy: dict[int, float] = {}
            max_capacity: dict[int, float] = {}
            for i in range(n):
                cost_val = ic[i] if i < len(ic) else 0.0
                max_val = im[i] if i < len(im) else 0.0
                ce = ice[i] if i < len(ice) else 0.0
                mc = imc[i] if i < len(imc) else 0.0
                if cost_val > 0 or max_val > 0 or ce > 0 or mc > 0:
                    nd_list.append(GuiInvestmentNodeData(
                        node_index=i, invest_cost=cost_val, invest_max=max_val,
                    ))
                    if ce > 0:
                        cost_energy[i] = ce
                    if mc > 0:
                        max_capacity[i] = mc
            investment_portfolio[entry_id] = GuiInvestmentEntry(
                entry_id=entry_id, name=bat.name,
                technology_type="battery", target_key=key,
                node_data=nd_list,
                invest_cost_energy=cost_energy,
                invest_max_capacity=max_capacity,
            )

    # Electrolyzers
    for key, el_cfg in sys.electrolyzers.items():
        ic = getattr(el_cfg, 'invest_cost', [])
        im = getattr(el_cfg, 'invest_max_power', [])
        has_invest = any(
            (i < len(ic) and ic[i] > 0) or (i < len(im) and im[i] > 0)
            for i in range(n)
        )
        if has_invest:
            entry_id = f"inv_{inv_counter}"
            inv_counter += 1
            nd_list = []
            for i in range(n):
                cost_val = ic[i] if i < len(ic) else 0.0
                max_val = im[i] if i < len(im) else 0.0
                if cost_val > 0 or max_val > 0:
                    nd_list.append(GuiInvestmentNodeData(
                        node_index=i, invest_cost=cost_val, invest_max=max_val,
                    ))
            investment_portfolio[entry_id] = GuiInvestmentEntry(
                entry_id=entry_id, name=el_cfg.name,
                technology_type="electrolyzer", target_key=key,
                node_data=nd_list,
            )

    # AC/DC Converters (scalar invest fields)
    for i, conv in enumerate(acdc_converters):
        ic = getattr(sys.acdc_converters[i], 'invest_cost', 0.0) if i < len(sys.acdc_converters) else 0.0
        im = getattr(sys.acdc_converters[i], 'invest_max_power', 0.0) if i < len(sys.acdc_converters) else 0.0
        if ic > 0 or im > 0:
            entry_id = f"inv_{inv_counter}"
            inv_counter += 1
            investment_portfolio[entry_id] = GuiInvestmentEntry(
                entry_id=entry_id, name=conv.name,
                technology_type="acdc_converter", target_key=str(i),
                node_data=[GuiInvestmentNodeData(node_index=0, invest_cost=ic, invest_max=im)],
            )

    # Frequency Converters (scalar invest fields)
    for i, conv in enumerate(freq_converters):
        ic = getattr(sys.freq_converters[i], 'invest_cost', 0.0) if i < len(sys.freq_converters) else 0.0
        im = getattr(sys.freq_converters[i], 'invest_max_power', 0.0) if i < len(sys.freq_converters) else 0.0
        if ic > 0 or im > 0:
            entry_id = f"inv_{inv_counter}"
            inv_counter += 1
            investment_portfolio[entry_id] = GuiInvestmentEntry(
                entry_id=entry_id, name=conv.name,
                technology_type="freq_converter", target_key=str(i),
                node_data=[GuiInvestmentNodeData(node_index=0, invest_cost=ic, invest_max=im)],
            )

    # Fuel storages — extract invest data from raw config dicts
    if hasattr(sys, 'fuel_infrastructure') and sys.fuel_infrastructure:
        raw_stores = getattr(sys.fuel_infrastructure, 'storage_facilities', None) or {}
        for sid, fst in fuel_storages.items():
            sdata = raw_stores.get(sid, {})
            if isinstance(sdata, dict):
                ic_val = sdata.get("invest_cost", 0.0)
                im_val = sdata.get("invest_max_capacity", 0.0)
            else:
                ic_val = getattr(sdata, 'invest_cost', 0.0)
                im_val = getattr(sdata, 'invest_max_capacity', 0.0)
            if ic_val > 0 or im_val > 0:
                entry_id = f"inv_{inv_counter}"
                inv_counter += 1
                investment_portfolio[entry_id] = GuiInvestmentEntry(
                    entry_id=entry_id, name=f"Fuel Storage {fst.name}",
                    technology_type="fuel_storage", target_key=sid,
                    node_data=[GuiInvestmentNodeData(node_index=fst.node, invest_cost=ic_val, invest_max=im_val)],
                )

    # Transmission (from NodeConfig.transference_invest_cost/max)
    tic = getattr(sys.nodes, 'transference_invest_cost', [])
    tim = getattr(sys.nodes, 'transference_invest_max', [])
    has_trans_invest = any(v > 0 for v in tic) or any(v > 0 for v in tim)
    if has_trans_invest:
        entry_id = f"inv_{inv_counter}"
        inv_counter += 1
        nd_list = []
        for i in range(n):
            # Diagonal element = self-investment at node i
            cost_val = tic[i * n + i] if i * n + i < len(tic) else 0.0
            max_val = tim[i * n + i] if i * n + i < len(tim) else 0.0
            if cost_val > 0 or max_val > 0:
                nd_list.append(GuiInvestmentNodeData(
                    node_index=i, invest_cost=cost_val, invest_max=max_val,
                ))
        investment_portfolio[entry_id] = GuiInvestmentEntry(
            entry_id=entry_id, name="Transmission",
            technology_type="transmission", target_key="",
            node_data=nd_list,
        )

    # Load technologies from _technologies key
    technologies: dict[str, GuiTechnology] = {}
    tech_counter = 0
    raw_techs = getattr(sys, 'gui_technologies', None) or {}
    # The GUI ``_technologies`` block does not carry per-tech colors; those
    # live on the optimizer technology/battery configs. Build a lookup so a
    # tech loaded from ``_technologies`` still picks up its configured color.
    opt_color_by_id: dict[str, str] = {}
    opt_color_by_name: dict[str, str] = {}
    for _src in (getattr(sys, 'technologies', None) or {},
                 getattr(sys, 'battery_technologies', None) or {}):
        for _tid, _conf in _src.items():
            if isinstance(_conf, dict):
                _c, _n = _conf.get('color'), _conf.get('name')
            else:
                _c, _n = getattr(_conf, 'color', None), getattr(_conf, 'name', None)
            if _c:
                opt_color_by_id[_tid] = _c
                if _n:
                    opt_color_by_name[_n.strip().lower()] = _c
    for tid, tdata in raw_techs.items():
        if isinstance(tdata, dict):
            tech = GuiTechnology(
                tech_id=tid,
                name=tdata.get('name', 'Technology'),
                category=tdata.get('category', 'Renewable'),
                fuel=tdata.get('fuel', ''),
                life_time=tdata.get('life_time', 25),
                degradation_rate=tdata.get('degradation_rate', 0.0),
                eff_at_rated=tdata.get('eff_at_rated', 0.35),
                eff_at_min=tdata.get('eff_at_min', 0.25),
                invest_cost=tdata.get('invest_cost', 0.0),
                invest_max_power=tdata.get('invest_max_power', 0.0),
                invest_cost_energy=tdata.get('invest_cost_energy', 0.0),
                invest_max_capacity=tdata.get('invest_max_capacity', 0.0),
            )
            if tdata.get('color'):
                tech.style.color = tdata['color']
            elif opt_color_by_id.get(tid):
                tech.style.color = opt_color_by_id[tid]
            elif opt_color_by_name.get(tech.name.strip().lower()):
                tech.style.color = opt_color_by_name[tech.name.strip().lower()]
            technologies[tid] = tech
            try:
                num = int(tid.replace('tech_', ''))
                if num >= tech_counter:
                    tech_counter = num + 1
            except ValueError:
                pass

    # Fallback: also load from optimizer technologies (TechnologyConfig objects)
    # if gui_technologies was empty — ensures technologies are visible for zone forms
    if not technologies:
        opt_techs = getattr(sys, 'technologies', None) or {}
        for tid, tconf in opt_techs.items():
            if hasattr(tconf, 'name'):
                # TechnologyConfig or BatteryTechnologyConfig Pydantic model
                invest_cost_val = tconf.invest_cost[0] if hasattr(tconf, 'invest_cost') and tconf.invest_cost else 0.0
                invest_max_val = tconf.invest_max_power[0] if hasattr(tconf, 'invest_max_power') and tconf.invest_max_power else 0.0
                tech = GuiTechnology(
                    tech_id=tid,
                    name=tconf.name,
                    category=getattr(tconf, 'type', 'Renewable'),
                    fuel=getattr(tconf, 'fuel', ''),
                    life_time=getattr(tconf, 'lifetime', 25),
                    degradation_rate=tconf.degradation_rate[0] if hasattr(tconf, 'degradation_rate') and tconf.degradation_rate else 0.0,
                    eff_at_rated=tconf.eff_at_rated[0] if hasattr(tconf, 'eff_at_rated') and tconf.eff_at_rated else 0.35,
                    eff_at_min=tconf.eff_at_min[0] if hasattr(tconf, 'eff_at_min') and tconf.eff_at_min else 0.25,
                    invest_cost=invest_cost_val,
                    invest_max_power=invest_max_val,
                )
                if getattr(tconf, 'color', None):
                    tech.style.color = tconf.color
                technologies[tid] = tech
                try:
                    num = int(tid.replace('tech_', ''))
                    if num >= tech_counter:
                        tech_counter = num + 1
                except ValueError:
                    pass
        # Also load battery technologies
        opt_bat_techs = getattr(sys, 'battery_technologies', None) or {}
        for tid, btconf in opt_bat_techs.items():
            if hasattr(btconf, 'name'):
                tech = GuiTechnology(
                    tech_id=tid,
                    name=btconf.name,
                    category="Storage",
                    fuel="",
                    life_time=getattr(btconf, 'lifetime', 15),
                    degradation_rate=btconf.degradation_rate[0] if hasattr(btconf, 'degradation_rate') and btconf.degradation_rate else 0.0,
                    invest_cost=btconf.invest_cost_power[0] if hasattr(btconf, 'invest_cost_power') and btconf.invest_cost_power else 0.0,
                    invest_max_power=btconf.invest_max_power[0] if hasattr(btconf, 'invest_max_power') and btconf.invest_max_power else 0.0,
                    invest_cost_energy=btconf.invest_cost_energy[0] if hasattr(btconf, 'invest_cost_energy') and btconf.invest_cost_energy else 0.0,
                    invest_max_capacity=btconf.invest_max_capacity[0] if hasattr(btconf, 'invest_max_capacity') and btconf.invest_max_capacity else 0.0,
                )
                if getattr(btconf, 'color', None):
                    tech.style.color = btconf.color
                technologies[tid] = tech
                try:
                    num = int(tid.replace('tech_', ''))
                    if num >= tech_counter:
                        tech_counter = num + 1
                except ValueError:
                    pass

    # ── Phase 1: Wire lines to bus-type endpoints ──
    # Lines: wire to buses, creating endpoint-specific buses when a line
    # connects two different geographic points within the same node.
    import logging as _logging
    _line_log = _logging.getLogger(__name__)

    def _resolve_endpoint(side: str, ln, endpoint, node_idx):
        """Pick the bus to wire `endpoint` to and log silent remaps.

        Returns the bus_id to use, or None if nothing usable was found.
        Previously, when an endpoint pointed at a bus_id that didn't
        exist in `buses`, the loader silently substituted the node's
        default bus — making upstream data corruption (typos, stale
        refs after bus deletion) invisible to the user.
        """
        if endpoint and endpoint.element_type == "bus":
            if endpoint.element_id in buses:
                return endpoint.element_id
            fallback = _node_to_bus.get(node_idx)
            if fallback is not None:
                _line_log.warning(
                    "Line %r: %s_endpoint references unknown bus %r; "
                    "falling back to node %d default bus %r. The source "
                    "data may be stale or contain a typo.",
                    getattr(ln, "line_id", "?"),
                    side, endpoint.element_id, node_idx, fallback,
                )
            return fallback
        return _node_to_bus.get(node_idx)

    for ln in lines:
        # If endpoints already reference existing buses, use those directly
        fb = _resolve_endpoint("from", ln, ln.from_endpoint, ln.from_node)
        if fb is not None:
            ln.from_bus = fb
        tb = _resolve_endpoint("to", ln, ln.to_endpoint, ln.to_node)
        if tb is not None:
            ln.to_bus = tb

        # If both endpoints resolved to the same bus but the line has
        # waypoints (i.e., it actually connects two different locations),
        # create a new bus for the to-endpoint.
        if ln.from_bus == ln.to_bus and (ln.waypoints or ln.from_node != ln.to_node):
            # Use the last waypoint or approximate a position
            if ln.waypoints:
                to_lat = ln.waypoints[-1].lat
                to_lng = ln.waypoints[-1].lng
            else:
                to_lat, to_lng = 0.0, 0.0
            new_bid = f"bus_{next_bus_id}"
            next_bus_id += 1
            parent = ln.to_node
            node = nodes[parent] if parent < len(nodes) else None
            buses[new_bid] = GuiBus(
                bus_id=new_bid,
                name=f"{node.name} - B" if node else f"Bus {parent} - B",
                parent_node=parent,
                role="connection",
                demand_fraction=0.0,
                latitude=to_lat,
                longitude=to_lng,
            )
            ln.to_bus = new_bid
            ln.to_endpoint = EndpointRef("bus", new_bid)

    # ── Phase 2: Resolve element → bus from line endpoint connectivity ──
    # When a line connects a bus to a non-bus element (generator, battery,
    # transformer, etc.), the non-bus element's bus = the bus on the other end.
    _element_to_bus: dict[tuple[str, str], str] = {}
    # Transformers have two sides; track which bus each side connects to.
    # "arriving" = line *to* transformer, "departing" = line *from* transformer
    _tr_arriving_bus: dict[str, str] = {}
    _tr_departing_bus: dict[str, str] = {}

    for ln in lines:
        fep, tep = ln.from_endpoint, ln.to_endpoint
        if not fep or not tep:
            continue
        from_is_bus = fep.element_type == "bus" and fep.element_id in buses
        to_is_bus = tep.element_type == "bus" and tep.element_id in buses

        # from-side is a bus, to-side is a non-bus element
        if from_is_bus and not to_is_bus:
            bus_id = fep.element_id
            if tep.element_type == "transformer":
                _tr_arriving_bus.setdefault(tep.element_id, bus_id)
            elif tep.element_type == "acdc_converter":
                _element_to_bus.setdefault(
                    ("acdc_from", tep.element_id), bus_id)
            elif tep.element_type == "freq_converter":
                _element_to_bus.setdefault(
                    ("freq_from", tep.element_id), bus_id)
            else:
                _element_to_bus.setdefault(
                    (tep.element_type, tep.element_id), bus_id)

        # to-side is a bus, from-side is a non-bus element
        if to_is_bus and not from_is_bus:
            bus_id = tep.element_id
            if fep.element_type == "transformer":
                _tr_departing_bus.setdefault(fep.element_id, bus_id)
            elif fep.element_type == "acdc_converter":
                _element_to_bus.setdefault(
                    ("acdc_to", fep.element_id), bus_id)
            elif fep.element_type == "freq_converter":
                _element_to_bus.setdefault(
                    ("freq_to", fep.element_id), bus_id)
            else:
                _element_to_bus.setdefault(
                    (fep.element_type, fep.element_id), bus_id)

    # ── Phase 3: Apply resolved buses to equipment ──
    # Helper: find nearest bus at the same node by geographic distance
    def _nearest_bus_for(node_idx: int, lat: float, lng: float) -> str:
        bus_ids = _node_to_buses.get(node_idx, [])
        if not bus_ids:
            return _node_to_bus.get(node_idx, "")
        if len(bus_ids) == 1:
            return bus_ids[0]
        best_id = bus_ids[0]
        best_dist = float("inf")
        for bid in bus_ids:
            b = buses[bid]
            dlat = b.latitude - lat
            dlng = b.longitude - lng
            d = dlat * dlat + dlng * dlng
            if d < best_dist:
                best_dist = d
                best_id = bid
        return best_id

    for inst_id, inst in generators.items():
        bus = _element_to_bus.get(("generator", inst_id))
        if bus:
            inst.bus = bus
        elif inst.node in _node_to_bus:
            inst.bus = _nearest_bus_for(inst.node, inst.latitude, inst.longitude)
    for inst_id, inst in batteries.items():
        bus = _element_to_bus.get(("battery", inst_id))
        if bus:
            inst.bus = bus
        elif inst.node in _node_to_bus:
            inst.bus = _nearest_bus_for(inst.node, inst.latitude, inst.longitude)
    for inst_id, inst in electrolyzers.items():
        bus = _element_to_bus.get(("electrolyzer", inst_id))
        if bus:
            inst.bus = bus
        elif inst.node in _node_to_bus:
            inst.bus = _nearest_bus_for(inst.node, inst.latitude, inst.longitude)

    # The wire-line-based bus inference and the proximity fallback
    # below must NEVER clobber explicit bus indices that the YAML
    # already carried (``transformer.from_bus`` / ``to_bus`` int
    # fields, resolved earlier into bus_id strings). Doing so used to
    # collapse multi-voltage transformers to self-loops on reload
    # because both sides shared the same parent_node and the
    # nearest-bus fallback returned the same bus for both. Only run
    # the inference for transformers whose buses were not persisted
    # OR whose two ends ended up identical (e.g. legacy YAML).
    def _need_resolve(a: str, b: str) -> bool:
        return (not a) or (not b) or (a == b)

    for i, tr in enumerate(transformers):
        tr_id = str(i)
        if not _need_resolve(tr.from_bus, tr.to_bus):
            continue
        fb = _tr_arriving_bus.get(tr_id)
        tb = _tr_departing_bus.get(tr_id)
        tr.from_bus = fb if fb else _nearest_bus_for(
            tr.from_node, tr.latitude, tr.longitude,
        ) or tr.from_bus
        tr.to_bus = tb if tb else _nearest_bus_for(
            tr.to_node, tr.latitude, tr.longitude,
        ) or tr.to_bus


    for i, conv in enumerate(acdc_converters):
        cid = str(i)
        if not _need_resolve(conv.from_bus, conv.to_bus):
            continue
        fb = _element_to_bus.get(("acdc_from", cid))
        tb = _element_to_bus.get(("acdc_to", cid))
        conv.from_bus = fb if fb else _nearest_bus_for(
            conv.from_node, conv.latitude, conv.longitude,
        ) or conv.from_bus
        conv.to_bus = tb if tb else _nearest_bus_for(
            conv.to_node, conv.latitude, conv.longitude,
        ) or conv.to_bus
    for i, conv in enumerate(freq_converters):
        cid = str(i)
        if not _need_resolve(conv.from_bus, conv.to_bus):
            continue
        fb = _element_to_bus.get(("freq_from", cid))
        tb = _element_to_bus.get(("freq_to", cid))
        conv.from_bus = fb if fb else _nearest_bus_for(
            conv.from_node, conv.latitude, conv.longitude,
        ) or conv.from_bus
        conv.to_bus = tb if tb else _nearest_bus_for(
            conv.to_node, conv.latitude, conv.longitude,
        ) or conv.to_bus

    state = GuiSystemState(
        name=sys.name,
        map_center=map_center,
        map_zoom=sys.map_zoom or 7,
        nodes=nodes,
        buses=buses,
        _next_bus_id=next_bus_id,
        generators=generators,
        batteries=batteries,
        transmission_lines=lines,
        transformers=transformers,
        acdc_converters=acdc_converters,
        freq_converters=freq_converters,
        development_zones=zones,
        fuel_entry_points=fuel_entries,
        fuel_sources=fuel_sources,
        fuel_storages=fuel_storages,
        fuel_transport_routes=fuel_routes,
        demand_path=sys.demand_path,
        demand_paths=sys.demand_paths if sys.demand_paths else None,
        _next_line_id=line_counter,
        _next_fuel_route_id=fuel_route_counter,
        investment_portfolio=investment_portfolio,
        _next_investment_id=inv_counter,
        technologies=technologies,
        _next_tech_id=tech_counter,
        fuels=fuels,
        settings=settings,
        penalties=penalties,
        power_flow_mode=power_flow_mode,
        dc_power_flow=dc_pf,
        ac_power_flow=ac_pf,
        electrolyzers=electrolyzers,
        ev_config=ev_config,
        rooftop_solar=rooftop_solar,
        demand_sectors=demand_sectors,
        non_electric_demand=non_electric,
        sector_distribution=sector_dist,
    )

    # Regenerate visual wire-lines (transformer/equipment ↔ bus
    # connectors). They're decoration-only, intentionally not
    # persisted, so without this every transformer/generator on the
    # map would render as a floating dot after reload.
    try:
        from esfex.visualization.data.validation import (
            rebuild_visual_wire_lines,
        )
        rebuild_visual_wire_lines(state)
    except Exception:
        # Don't let a wire-line rebuild failure block the load.
        pass

    # Restore per-element visual styles from ``_gui_styles``.
    gui_styles = getattr(sys, 'gui_styles', None) or {}
    if gui_styles:
        for nd in state.nodes:
            sd = gui_styles.get("nodes", {}).get(str(nd.index))
            if sd:
                nd.style = _dict_to_style(sd)
        for bus_id, bus in state.buses.items():
            sd = gui_styles.get("buses", {}).get(bus_id)
            if sd:
                bus.style = _dict_to_style(sd)
        for gid, inst in state.generators.items():
            sd = gui_styles.get("generators", {}).get(gid)
            if sd:
                inst.style = _dict_to_style(sd)
        for bid, inst in state.batteries.items():
            sd = gui_styles.get("batteries", {}).get(bid)
            if sd:
                inst.style = _dict_to_style(sd)
        for eid, inst in state.electrolyzers.items():
            sd = gui_styles.get("electrolyzers", {}).get(eid)
            if sd:
                inst.style = _dict_to_style(sd)
        for i, tr in enumerate(state.transformers):
            sd = gui_styles.get("transformers", {}).get(str(i))
            if sd:
                tr.style = _dict_to_style(sd)
        for i, conv in enumerate(state.acdc_converters):
            sd = gui_styles.get("acdc_converters", {}).get(str(i))
            if sd:
                conv.style = _dict_to_style(sd)
        for i, conv in enumerate(state.freq_converters):
            sd = gui_styles.get("freq_converters", {}).get(str(i))
            if sd:
                conv.style = _dict_to_style(sd)
        for ln in state.transmission_lines:
            sd = gui_styles.get("transmission_lines", {}).get(ln.line_id)
            if sd:
                ln.style = _dict_to_style(sd)
        for sid, fst in state.fuel_storages.items():
            sd = gui_styles.get("fuel_storages", {}).get(sid)
            if sd:
                fst.style = _dict_to_style(sd)
        for i, fe in enumerate(state.fuel_entry_points):
            sd = gui_styles.get("fuel_entries", {}).get(str(i))
            if sd:
                fe.style = _dict_to_style(sd)
        for rt in state.fuel_transport_routes:
            sd = gui_styles.get("fuel_routes", {}).get(rt.route_id)
            if sd:
                rt.style = _dict_to_style(sd)
        for i, zone in enumerate(state.development_zones):
            sd = gui_styles.get("zones", {}).get(str(i))
            if sd:
                zone.style = _dict_to_style(sd)

    return state


# =====================================================================
# GUI State -> YAML
# =====================================================================


def config_to_global_settings(
    config: ESFEXConfig,
    raw_dict: dict | None = None,
) -> GuiGlobalSettings:
    """Extract top-level config into :class:`GuiGlobalSettings`.

    Parameters
    ----------
    config : ESFEXConfig
        Parsed configuration.
    raw_dict : dict, optional
        Raw YAML dict — used to read GUI-only keys (e.g. ``visual_scaling``)
        that are not part of the Pydantic schema and get dropped during parsing.
    """
    g = GuiGlobalSettings()

    # Systems to simulate (from meta_network.systems)
    if hasattr(config, 'meta_network') and config.meta_network is not None:
        g.systems_to_simulate = list(getattr(config.meta_network, 'systems', []))
    else:
        g.systems_to_simulate = list(config.systems.keys()) if hasattr(config, 'systems') else []

    g.simulation_mode = getattr(config, 'simulation_mode', 'development')
    g.unit_commitment_hours = getattr(config, 'unit_commitment_hours', 24)
    g.date_start = getattr(config, 'date_start', '01/01/2025 00:00')
    g.enable_primary_energy = getattr(config, 'enable_primary_energy', True)

    # GUI-only: console verbose level (stored in raw YAML, not in Pydantic schema)
    raw = raw_dict or {}
    g.console_log_level = raw.get("logging", {}).get("console_level", "basic")

    if hasattr(config, 'temporal') and config.temporal:
        t = config.temporal
        g.resolution_hours = getattr(t, 'resolution_hours', 1)
        g.rolling_horizon_hours = getattr(t, 'rolling_horizon_hours', 48)
        g.overlap_hours = getattr(t, 'overlap_hours', 6)
        g.investment_resolution = getattr(t, 'investment_resolution', HOURS_STD_YEAR)
        g.primary_energy_resolution = getattr(t, 'primary_energy_resolution', 24)
        g.use_rolling_horizon = getattr(t, 'use_rolling_horizon', True)

    if hasattr(config, 'solver') and config.solver:
        sv = config.solver
        g.solver_name = getattr(sv, 'name', 'highs')
        g.solver_threads = getattr(sv, 'threads', 4)
        g.solver_time_limit = getattr(sv, 'time_limit', 10800)
        g.solver_gap = getattr(sv, 'gap', 0.01)
        g.solver_verbose = getattr(sv, 'verbose', False)
        g.solver_scale_constraints = getattr(sv, 'scale_constraints', True)
        g.solver_specific_options = dict(getattr(sv, 'options', {}))

    if hasattr(config, 'n1_security') and config.n1_security:
        n1 = config.n1_security
        g.n1_enabled = getattr(n1, 'enabled', False)
        g.n1_apply_to_modes = list(getattr(n1, 'apply_to_modes', ['unit_commitment']))
        g.n1_transmission_enabled = getattr(n1, 'transmission_enabled', True)
        g.n1_transmission_reserve_factor = getattr(n1, 'transmission_reserve_factor', 0.70)
        g.n1_critical_line_threshold = getattr(n1, 'critical_line_threshold', 0.0)
        g.n1_generation_enabled = getattr(n1, 'generation_enabled', True)
        g.n1_generation_reserve_type = getattr(n1, 'generation_reserve_type', 'largest_unit')
        g.n1_generation_reserve_percentage = getattr(n1, 'generation_reserve_percentage', 0.15)
        g.n1_scopf_enabled = getattr(n1, 'scopf_enabled', False)
        g.n1_scopf_max_iterations = getattr(n1, 'scopf_max_iterations', 5)
        g.n1_scopf_violation_tolerance = getattr(n1, 'scopf_violation_tolerance', 0.01)
        g.n1_corrective_enabled = getattr(n1, 'corrective_enabled', False)
        g.n1_contingency_depth = getattr(n1, 'contingency_depth', 'n1')
        g.n1_redistribution_mode = getattr(n1, 'redistribution_mode', 'pro_rata')
        g.n1_pi_screening_threshold = getattr(n1, 'pi_screening_threshold', 0.0)
        g.n1_transformer_contingencies = getattr(n1, 'transformer_contingencies', False)
        g.n1_battery_contingencies = getattr(n1, 'battery_contingencies', False)

    if hasattr(config, 'master_problem') and config.master_problem:
        mp = config.master_problem
        g.mp_stochastic = getattr(mp, 'stochastic', False)
        g.mp_representative_days = getattr(mp, 'representative_days', 5)
        g.mp_min_day_separation = getattr(mp, 'min_day_separation', 5)
        g.mp_solver_method = getattr(mp, 'solver_method', 'monolithic')
        g.mp_benders_max_iterations = getattr(mp, 'benders_max_iterations', 50)
        g.mp_benders_tolerance = getattr(mp, 'benders_tolerance', 1e-4)
        g.mp_use_tsam = getattr(mp, 'use_tsam', False)
        g.mp_tsam_num_periods = getattr(mp, 'tsam_num_periods', 10)
        g.mp_tsam_method = getattr(mp, 'tsam_method', 'kmedoids')
        g.mp_tsam_inter_period_linking = getattr(mp, 'tsam_inter_period_linking', True)
        g.mp_use_uc_in_dispatch = getattr(mp, 'use_uc_in_dispatch', False)

        mga = getattr(mp, 'mga', None)
        if mga is not None:
            g.mp_mga_enabled = getattr(mga, 'enabled', False)
            g.mp_mga_method = getattr(mga, 'method', 'mga')
            objectives = getattr(mga, 'objectives', None) or []
            # Pydantic returns SporesObjective enum members; the GUI state
            # is plain str so we coerce here.
            g.mp_mga_objectives = [
                o.value if hasattr(o, 'value') else str(o) for o in objectives
            ]
            g.mp_mga_num_alternatives = getattr(mga, 'num_alternatives', 10)
            g.mp_mga_slack_fraction = getattr(mga, 'slack_fraction', 0.05)
            g.mp_mga_investment_threshold = getattr(mga, 'investment_threshold', 0.1)

    # visual_scaling is a GUI-only key not in the Pydantic schema,
    # so it must be read from the raw YAML dict.
    vs_dict = (raw_dict or {}).get("visual_scaling")
    if isinstance(vs_dict, dict):
        g.visual_scaling = GuiVisualScaling(
            marker_min_px=vs_dict.get('marker_min_px', 6.0),
            electrical_marker_scale=vs_dict.get('electrical_marker_scale', 0.02),
            energy_marker_scale=vs_dict.get('energy_marker_scale', 0.02),
            fuel_marker_scale=vs_dict.get('fuel_marker_scale', 0.5),
            line_min_px=vs_dict.get('line_min_px', 1.5),
            electrical_line_scale=vs_dict.get('electrical_line_scale', 0.005),
            fuel_line_scale=vs_dict.get('fuel_line_scale', 0.1),
        )

    return g


def config_to_stochastic_scenarios(
    config: ESFEXConfig,
) -> list[GuiStochasticScenario]:
    """Extract stochastic scenarios from the primary system config."""
    scenarios: list[GuiStochasticScenario] = []
    # Stochastic scenarios live on SystemConfig, not ESFEXConfig
    primary = getattr(config, 'primary_system', None)
    if primary is None:
        return scenarios
    for sc_cfg in getattr(primary, 'stochastic_scenarios', []):
        # sc_cfg is StochasticScenarioConfig with .multipliers: ScenarioMultipliers
        multipliers: dict[str, float] = {}
        if hasattr(sc_cfg, 'multipliers') and sc_cfg.multipliers is not None:
            mult = sc_cfg.multipliers
            for k in mult.model_fields:
                multipliers[k] = float(getattr(mult, k, 1.0))
        sc = GuiStochasticScenario(
            name=sc_cfg.name,
            probability=sc_cfg.probability,
            description=getattr(sc_cfg, 'description', ''),
            multipliers=multipliers,
        )
        scenarios.append(sc)
    return scenarios


def global_settings_to_config_dict(
    g: GuiGlobalSettings, config_dict: dict,
) -> None:
    """Apply global settings back to config dict."""
    config_dict["simulation_mode"] = g.simulation_mode
    config_dict["unit_commitment_hours"] = g.unit_commitment_hours
    config_dict["date_start"] = g.date_start
    config_dict["enable_primary_energy"] = g.enable_primary_energy

    # GUI-only: console verbose level
    config_dict.setdefault("logging", {})
    config_dict["logging"]["console_level"] = g.console_log_level

    config_dict.setdefault("temporal", {})
    config_dict["temporal"]["resolution_hours"] = g.resolution_hours
    config_dict["temporal"]["rolling_horizon_hours"] = g.rolling_horizon_hours
    config_dict["temporal"]["overlap_hours"] = g.overlap_hours
    config_dict["temporal"]["investment_resolution"] = g.investment_resolution
    config_dict["temporal"]["primary_energy_resolution"] = g.primary_energy_resolution
    config_dict["temporal"]["use_rolling_horizon"] = g.use_rolling_horizon

    config_dict.setdefault("solver", {})
    config_dict["solver"]["name"] = g.solver_name
    config_dict["solver"]["threads"] = g.solver_threads
    config_dict["solver"]["time_limit"] = g.solver_time_limit
    config_dict["solver"]["gap"] = g.solver_gap
    config_dict["solver"]["verbose"] = g.solver_verbose
    config_dict["solver"]["scale_constraints"] = g.solver_scale_constraints
    if g.solver_specific_options:
        config_dict["solver"]["options"] = dict(g.solver_specific_options)

    config_dict.setdefault("n1_security", {})
    config_dict["n1_security"]["enabled"] = g.n1_enabled
    config_dict["n1_security"]["apply_to_modes"] = g.n1_apply_to_modes
    config_dict["n1_security"]["transmission_enabled"] = g.n1_transmission_enabled
    config_dict["n1_security"]["transmission_reserve_factor"] = g.n1_transmission_reserve_factor
    config_dict["n1_security"]["critical_line_threshold"] = g.n1_critical_line_threshold
    config_dict["n1_security"]["generation_enabled"] = g.n1_generation_enabled
    config_dict["n1_security"]["generation_reserve_type"] = g.n1_generation_reserve_type
    config_dict["n1_security"]["generation_reserve_percentage"] = g.n1_generation_reserve_percentage
    config_dict["n1_security"]["scopf_enabled"] = g.n1_scopf_enabled
    config_dict["n1_security"]["scopf_max_iterations"] = g.n1_scopf_max_iterations
    config_dict["n1_security"]["scopf_violation_tolerance"] = g.n1_scopf_violation_tolerance
    config_dict["n1_security"]["corrective_enabled"] = g.n1_corrective_enabled
    config_dict["n1_security"]["contingency_depth"] = g.n1_contingency_depth
    config_dict["n1_security"]["redistribution_mode"] = g.n1_redistribution_mode
    config_dict["n1_security"]["pi_screening_threshold"] = g.n1_pi_screening_threshold
    config_dict["n1_security"]["transformer_contingencies"] = g.n1_transformer_contingencies
    config_dict["n1_security"]["battery_contingencies"] = g.n1_battery_contingencies

    config_dict.setdefault("master_problem", {})
    config_dict["master_problem"]["stochastic"] = g.mp_stochastic
    config_dict["master_problem"]["representative_days"] = g.mp_representative_days
    config_dict["master_problem"]["min_day_separation"] = g.mp_min_day_separation
    config_dict["master_problem"]["solver_method"] = g.mp_solver_method
    config_dict["master_problem"]["benders_max_iterations"] = g.mp_benders_max_iterations
    config_dict["master_problem"]["benders_tolerance"] = g.mp_benders_tolerance
    config_dict["master_problem"]["use_tsam"] = g.mp_use_tsam
    config_dict["master_problem"]["tsam_num_periods"] = g.mp_tsam_num_periods
    config_dict["master_problem"]["tsam_method"] = g.mp_tsam_method
    config_dict["master_problem"]["tsam_inter_period_linking"] = g.mp_tsam_inter_period_linking
    config_dict["master_problem"]["use_uc_in_dispatch"] = g.mp_use_uc_in_dispatch
    # MGAConfig validator rejects (method='mga' AND objectives non-empty)
    # and (method='spores' AND objectives empty). Emit only the fields
    # relevant to the selected method so the YAML round-trips cleanly
    # even when GUI state carries stale objectives from a prior spores
    # session.
    mga_dict: dict[str, object] = {
        "enabled": g.mp_mga_enabled,
        "method": g.mp_mga_method,
        "slack_fraction": g.mp_mga_slack_fraction,
        "investment_threshold": g.mp_mga_investment_threshold,
    }
    if g.mp_mga_method == "spores":
        mga_dict["objectives"] = list(g.mp_mga_objectives)
    else:
        # Classical MGA: num_alternatives drives the HSJ loop; objectives unused.
        mga_dict["num_alternatives"] = g.mp_mga_num_alternatives
    config_dict["master_problem"]["mga"] = mga_dict

    vs = g.visual_scaling
    config_dict["visual_scaling"] = {
        "marker_min_px": vs.marker_min_px,
        "electrical_marker_scale": vs.electrical_marker_scale,
        "energy_marker_scale": vs.energy_marker_scale,
        "fuel_marker_scale": vs.fuel_marker_scale,
        "line_min_px": vs.line_min_px,
        "electrical_line_scale": vs.electrical_line_scale,
        "fuel_line_scale": vs.fuel_line_scale,
    }

    # Risk & Resilience
    config_dict.setdefault("risk", {})
    config_dict["risk"]["enabled"] = g.risk_enabled
    config_dict["risk"]["risk_measure"] = g.risk_measure
    config_dict["risk"]["cvar_alpha"] = g.risk_cvar_alpha
    config_dict["risk"]["cvar_lambda"] = g.risk_cvar_lambda
    config_dict["risk"]["combination_method"] = g.risk_combination_method
    config_dict["risk"]["voll"] = {
        "residential": g.risk_voll_residential,
        "commercial": g.risk_voll_commercial,
        "industrial": g.risk_voll_industrial,
        "critical": g.risk_voll_critical,
    }
    config_dict["risk"]["demand_base_temperature"] = g.risk_base_temperature
    config_dict["risk"]["demand_heating_coefficient"] = g.risk_heating_coefficient
    config_dict["risk"]["demand_cooling_coefficient"] = g.risk_cooling_coefficient
    config_dict["risk"]["insurance_premium_rate"] = g.risk_insurance_premium_rate
    config_dict["risk"]["monte_carlo_samples"] = g.risk_monte_carlo_samples
    config_dict["risk"]["monte_carlo_seed"] = g.risk_monte_carlo_seed


def stochastic_scenarios_to_config_dict(
    scenarios: list[GuiStochasticScenario], sys_dict: dict,
) -> None:
    """Apply stochastic scenarios to a system config dict.

    Parameters
    ----------
    scenarios : list of GuiStochasticScenario
    sys_dict : dict
        The system-level dict (not top-level config dict).
    """
    if not scenarios:
        return
    sys_dict["stochastic_scenarios"] = [
        {
            "name": sc.name,
            "probability": sc.probability,
            "description": sc.description,
            "multipliers": dict(sc.multipliers),
        }
        for sc in scenarios
    ]


def gui_state_to_yaml(
    states: dict[str, GuiSystemState],
    base_config: ESFEXConfig,
    output_path: str | Path,
    inter_system_links: list[GuiInterSystemLink] | None = None,
    global_settings: GuiGlobalSettings | None = None,
    stochastic_scenarios: list[GuiStochasticScenario] | None = None,
) -> None:
    """Export GUI state to YAML, preserving non-GUI config fields."""
    config_dict = base_config.model_dump(mode="python", by_alias=True)

    # Update meta_network.systems — use selected systems if available,
    # otherwise include all GUI systems
    config_dict.setdefault("meta_network", {})
    if global_settings and global_settings.systems_to_simulate:
        config_dict["meta_network"]["systems"] = list(global_settings.systems_to_simulate)
    else:
        config_dict["meta_network"]["systems"] = list(states.keys())

    # Re-apply bus role + demand_fraction redistribution right before
    # serialization.  The Grid Builder runs this in its Phase 10, but
    # any buses added afterwards by `_ensure_bus_at` (transformer
    # endpoints, fuel storage anchors, etc.) would otherwise be written
    # out with the Pydantic defaults (role="load", demand_fraction=1.0)
    # and silently inflate node demand on the next load.  Idempotent.
    import logging
    from esfex.visualization.workflows.grid_mapping_quality import (
        repair_bus_roles_and_demand,
    )
    _repair_log = logging.getLogger(__name__)
    for _sys_name, state in states.items():
        if not (state and getattr(state, "buses", None)):
            continue
        try:
            repair_bus_roles_and_demand(state)
        except Exception:
            # Per-system best-effort: a failure here must not silently
            # corrupt sibling systems (the previous outer-try swallowed
            # everything and skipped repair for every system after the
            # first failure, which masked the cuba.yaml demand_fraction
            # sum=2 bug).
            _repair_log.warning(
                "repair_bus_roles_and_demand failed for system %r — "
                "serializing without repair for this system; "
                "demand_fraction invariants may be violated.",
                _sys_name,
                exc_info=True,
            )

    for sys_name, state in states.items():
        if sys_name not in config_dict["systems"]:
            config_dict["systems"][sys_name] = {"name": sys_name}
        sys_dict = config_dict["systems"][sys_name]
        _apply_gui_state_to_dict(state, sys_dict)

    if inter_system_links:
        inter_system_links_to_config_dict(inter_system_links, config_dict)

    if global_settings:
        global_settings_to_config_dict(global_settings, config_dict)

    if stochastic_scenarios:
        # Write stochastic scenarios to the primary (first) system dict
        primary_name = next(iter(states), None)
        if primary_name and primary_name in config_dict.get("systems", {}):
            stochastic_scenarios_to_config_dict(
                stochastic_scenarios, config_dict["systems"][primary_name]
            )

    # Convert numpy types to native Python for YAML serialization
    config_dict = _to_native(config_dict)

    output_path = Path(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)


def _to_native(obj):
    """Recursively convert numpy scalars/arrays to Python native types."""
    if isinstance(obj, dict):
        return {_to_native(k): _to_native(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    r = 6371.0
    la1, lo1 = math.radians(lat1), math.radians(lng1)
    la2, lo2 = math.radians(lat2), math.radians(lng2)
    dlat = la2 - la1
    dlng = lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _route_length_from_waypoints(
    state: GuiSystemState, rt: GuiFuelTransportRoute,
) -> float:
    """Calculate route length by chaining haversine segments along waypoints."""
    points: list[tuple[float, float]] = []
    for wp in rt.waypoints:
        points.append((wp.lat, wp.lng))

    if len(points) < 2:
        return 0.0

    total = 0.0
    for i in range(len(points) - 1):
        total += _haversine_km(points[i][0], points[i][1],
                               points[i + 1][0], points[i + 1][1])
    return total


def _build_fuel_transport_distances(state: GuiSystemState) -> list[list[float]]:
    """Build NxN fuel transport distance matrix (backward compatibility).

    Explicit fuel transport routes take priority.  For node pairs without
    an explicit route, falls back to haversine distance between fuel
    infrastructure centroids (or node centroids).
    """
    n = len(state.nodes)
    dist = [[0.0] * n for _ in range(n)]

    # Track which pairs have explicit routes
    has_route: set[tuple[int, int]] = set()

    # Phase 1: explicit fuel transport routes
    for rt in state.fuel_transport_routes:
        i, j = rt.from_node, rt.to_node
        if 0 <= i < n and 0 <= j < n and i != j:
            km = rt.length_km or 0.0
            if km <= 0:
                km = _route_length_from_waypoints(state, rt)
            if km > 0:
                dist[i][j] = km
                dist[j][i] = km
                has_route.add((min(i, j), max(i, j)))

    # Phase 2: haversine fallback for remaining pairs
    centroids: list[tuple[float, float]] = []
    for node_idx in range(n):
        lats: list[float] = []
        lngs: list[float] = []
        for fe in state.fuel_entry_points:
            if fe.node == node_idx:
                lat, lng = fe.coordinate.lat, fe.coordinate.lng
                if lat != 0.0 or lng != 0.0:
                    lats.append(lat)
                    lngs.append(lng)
        for fs in state.fuel_storages.values():
            if fs.node == node_idx:
                if fs.latitude != 0.0 or fs.longitude != 0.0:
                    lats.append(fs.latitude)
                    lngs.append(fs.longitude)
        if lats:
            centroids.append((sum(lats) / len(lats), sum(lngs) / len(lngs)))
        else:
            nd = state.nodes[node_idx]
            centroids.append((nd.centroid_lat, nd.centroid_lng))

    for i in range(n):
        for j in range(i + 1, n):
            if (i, j) in has_route:
                continue  # explicit route already set
            lat_i, lng_i = centroids[i]
            lat_j, lng_j = centroids[j]
            if (lat_i == 0.0 and lng_i == 0.0) or (lat_j == 0.0 and lng_j == 0.0):
                continue
            km = _haversine_km(lat_i, lng_i, lat_j, lng_j)
            dist[i][j] = km
            dist[j][i] = km

    return dist


def _build_fuel_transport_routes(state: GuiSystemState) -> list[dict]:
    """Convert GUI fuel routes into optimizer route list.

    Each GUI route (bidirectional) becomes TWO unidirectional routes.
    For node pairs without explicit routes, auto-generates haversine fallback routes.

    Returns:
        List of route dicts with keys: route_id, from_node, to_node, distance_km, fuel_params
    """
    routes: list[dict] = []
    covered_pairs: set[tuple[int, int]] = set()
    n = len(state.nodes)

    # Phase 1: Explicit GUI routes -> 2 unidirectional routes each
    for rt in state.fuel_transport_routes:
        i, j = rt.from_node, rt.to_node
        if not (0 <= i < n and 0 <= j < n):
            continue

        km = rt.length_km or 0.0
        if km <= 0:
            km = _route_length_from_waypoints(state, rt)
        if km <= 0:
            continue

        fuel_params: dict[str, dict] = {}
        for fuel_name, fp in rt.fuel_params.items():
            fuel_params[fuel_name] = {
                "capacity": fp.capacity,
                "transport_cost": fp.transport_cost,
                "losses_fraction": fp.losses_fraction,
            }

        # Forward route
        routes.append({
            "route_id": f"{rt.route_id}_fwd",
            "from_node": i,
            "to_node": j,
            "distance_km": km,
            "fuel_params": fuel_params,
        })
        # Reverse route
        routes.append({
            "route_id": f"{rt.route_id}_rev",
            "from_node": j,
            "to_node": i,
            "distance_km": km,
            "fuel_params": fuel_params,
        })
        if i != j:  # intra-node routes don't suppress haversine for that "pair"
            covered_pairs.add((min(i, j), max(i, j)))

    # Phase 2: Haversine fallback for node pairs without explicit routes
    centroids: list[tuple[float, float]] = []
    for node_idx in range(n):
        lats: list[float] = []
        lngs: list[float] = []
        for fe in state.fuel_entry_points:
            if fe.node == node_idx:
                lat, lng = fe.coordinate.lat, fe.coordinate.lng
                if lat != 0.0 or lng != 0.0:
                    lats.append(lat)
                    lngs.append(lng)
        for fs in state.fuel_storages.values():
            if fs.node == node_idx:
                if fs.latitude != 0.0 or fs.longitude != 0.0:
                    lats.append(fs.latitude)
                    lngs.append(fs.longitude)
        if lats:
            centroids.append((sum(lats) / len(lats), sum(lngs) / len(lngs)))
        else:
            nd = state.nodes[node_idx]
            centroids.append((nd.centroid_lat, nd.centroid_lng))

    for i in range(n):
        for j in range(i + 1, n):
            if (i, j) in covered_pairs:
                continue
            lat_i, lng_i = centroids[i]
            lat_j, lng_j = centroids[j]
            if (lat_i == 0.0 and lng_i == 0.0) or (lat_j == 0.0 and lng_j == 0.0):
                continue
            km = _haversine_km(lat_i, lng_i, lat_j, lng_j)
            if km > 0:
                routes.append({
                    "route_id": f"auto_{i}_{j}",
                    "from_node": i,
                    "to_node": j,
                    "distance_km": km,
                    "fuel_params": {},  # empty = use global defaults in Julia
                })
                routes.append({
                    "route_id": f"auto_{j}_{i}",
                    "from_node": j,
                    "to_node": i,
                    "distance_km": km,
                    "fuel_params": {},
                })

    return routes


def _apply_gui_state_to_dict(state: GuiSystemState, sys_dict: dict):
    """Mutate *sys_dict* to reflect *state*."""
    n = len(state.nodes)
    # Ensure n covers all node indices referenced by generators/batteries
    for inst in state.generators.values():
        n = max(n, inst.node + 1)
    for inst in state.batteries.values():
        n = max(n, inst.node + 1)

    # Node names and centroids
    if state.nodes:
        sys_dict.setdefault("nodes", {})
        sys_dict["nodes"]["node_names"] = [nd.name for nd in state.nodes]
        # Preserve node centroids (used for geo-asset assignment)
        coords = []
        for nd in state.nodes:
            if nd.centroid_lat != 0.0 or nd.centroid_lng != 0.0:
                coords.append({
                    "latitude": nd.centroid_lat,
                    "longitude": nd.centroid_lng,
                    "label": nd.name,
                })
            else:
                coords.append({"latitude": 0.0, "longitude": 0.0, "label": nd.name})
        if any(c["latitude"] != 0.0 or c["longitude"] != 0.0 for c in coords):
            sys_dict["nodes"]["node_coordinates"] = coords

    # Rebuild adjacency matrix (SUM parallel lines between same node pair)
    connections = [0.0] * (n * n)
    for line in state.transmission_lines:
        if line.from_node < n and line.to_node < n:
            connections[line.from_node * n + line.to_node] += line.capacity_mw
            connections[line.to_node * n + line.from_node] += line.capacity_mw
    sys_dict.setdefault("nodes", {})
    sys_dict["nodes"]["nodes_connections"] = connections
    sys_dict["nodes"]["num_nodes"] = n

    # Reserve arrays
    sys_dict["nodes"]["reserve_static"] = [nd.reserve_static for nd in state.nodes]
    sys_dict["nodes"]["reserve_dynamic"] = [nd.reserve_dynamic for nd in state.nodes]
    sys_dict["nodes"]["reserve_duration"] = [nd.reserve_duration for nd in state.nodes]
    sys_dict["nodes"]["losses"] = [nd.losses for nd in state.nodes]

    # --- Generators: aggregate instances back to per-node arrays ---
    # Remove old unit_* top-level keys (legacy format)
    keys_to_remove = [k for k in sys_dict if k.startswith("unit_")]
    for k in keys_to_remove:
        del sys_dict[k]
    # Remove normalized generators/batteries dicts if present
    sys_dict.pop("generators", None)
    sys_dict.pop("batteries", None)
    # Remove any leftover top-level keys from previous buggy serialization
    # (generators/batteries written at top level instead of inside dicts)
    _gen_unit_keys = {inst.unit_key for inst in state.generators.values()}
    _bat_unit_keys = {inst.unit_key for inst in state.batteries.values()}
    for k in list(sys_dict.keys()):
        if k in _gen_unit_keys or k in _bat_unit_keys:
            del sys_dict[k]

    # Group generator instances by unit_key
    gen_groups: dict[str, dict[int, GuiGeneratorInstance]] = defaultdict(dict)
    for inst in state.generators.values():
        gen_groups[inst.unit_key][inst.node] = inst

    generators_dict: dict[str, dict[str, Any]] = {}
    for unit_key, node_instances in gen_groups.items():
        rep = next(iter(node_instances.values()))
        gen_dict: dict[str, Any] = {
            "name": rep.name,
            "type": rep.gen_type,
            "fuel": rep.fuel,
            "reservable": rep.reservable,
        }
        # Physical bus anchor per node: each per-node instance carries the
        # real bus_id it connects to. Emitting this lets the operational
        # DC-OPF inject each node's capacity at its true physical bus
        # instead of falling back to node aggregation.
        _bipn = {
            int(nd): inst.bus
            for nd, inst in node_instances.items()
            if getattr(inst, "bus", None)
        }
        if _bipn:
            gen_dict["bus_id_per_node"] = _bipn
        if rep.technology_id:
            gen_dict["technology"] = rep.technology_id
        if rep.availability_file:
            gen_dict["Availability"] = rep.availability_file
        gen_dict["frequency_hz"] = getattr(rep, 'frequency_hz', 50.0)
        gen_dict["current_type"] = getattr(rep, 'current_type', 'AC')
        if rep.reservoir_inflow_file:
            gen_dict["reservoir_inflow_file"] = rep.reservoir_inflow_file
        gen_dict["reservoir_spillage_allowed"] = getattr(rep, 'reservoir_spillage_allowed', True)
        _casc = getattr(rep, 'cascade_downstream', '') or ''
        if _casc:
            gen_dict["cascade_downstream"] = _casc
            gen_dict["cascade_delay_hours"] = int(getattr(rep, 'cascade_delay_hours', 0) or 0)
        # Rebuild per-node arrays
        for field_name in _GEN_SCALAR_FIELDS:
            arr = [0] * n if field_name in ("life_time", "initial_age", "min_up", "min_down") else [0.0] * n
            for node_idx, inst in node_instances.items():
                arr[node_idx] = getattr(inst, field_name)
            gen_dict[field_name] = arr
        # Add invest fields from portfolio
        inv_cost_arr = [0.0] * n
        inv_max_arr = [0.0] * n
        for entry in state.investment_portfolio.values():
            if entry.technology_type == "generator" and entry.target_key == unit_key:
                for nd in entry.node_data:
                    if 0 <= nd.node_index < n:
                        inv_cost_arr[nd.node_index] = nd.invest_cost
                        inv_max_arr[nd.node_index] = nd.invest_max
        gen_dict["invest_cost"] = inv_cost_arr
        gen_dict["invest_max_power"] = inv_max_arr
        # Export fuel cost curve if any instance uses non-flat
        _has_curve = any(
            getattr(inst, 'fuel_cost_curve_type', 'flat') != 'flat'
            for inst in node_instances.values()
        )
        if _has_curve:
            _curve_arr = [None] * n
            for _ni, _inst in node_instances.items():
                _ct = getattr(_inst, 'fuel_cost_curve_type', 'flat')
                _cd = getattr(_inst, 'fuel_cost_curve_data', None)
                _cfg = _gui_data_to_cost_curve_config(_ct, _cd)
                _curve_arr[_ni] = _cfg if _cfg else {"curve_type": "flat"}
            gen_dict["fuel_cost_curve"] = _curve_arr
        generators_dict[unit_key] = gen_dict
    sys_dict["generators"] = generators_dict

    # Group battery instances by unit_key
    bat_groups: dict[str, dict[int, GuiBatteryInstance]] = defaultdict(dict)
    for inst in state.batteries.values():
        bat_groups[inst.unit_key][inst.node] = inst

    batteries_dict: dict[str, dict[str, Any]] = {}
    for unit_key, node_instances in bat_groups.items():
        rep = next(iter(node_instances.values()))
        bat_dict: dict[str, Any] = {
            "name": rep.name,
            "type": "Storage",
            "fuel": rep.fuel,
            "reservable": rep.reservable,
            "spillage": rep.spillage,
        }
        # Physical bus anchor per node (see generator block above).
        _bipn = {
            int(nd): inst.bus
            for nd, inst in node_instances.items()
            if getattr(inst, "bus", None)
        }
        if _bipn:
            bat_dict["bus_id_per_node"] = _bipn
        if rep.min_duration_hours is not None:
            bat_dict["min_duration_hours"] = rep.min_duration_hours
        if rep.max_duration_hours is not None:
            bat_dict["max_duration_hours"] = rep.max_duration_hours
        if rep.availability_file:
            bat_dict["Availability"] = rep.availability_file
        bat_dict["current_type"] = getattr(rep, 'current_type', 'DC')
        for field_name in _BAT_SCALAR_FIELDS:
            arr = [0] * n if field_name in ("life_time", "initial_age", "min_up", "min_down") else [0.0] * n
            for node_idx, inst in node_instances.items():
                arr[node_idx] = getattr(inst, field_name)
            bat_dict[field_name] = arr
        # Add invest fields from portfolio
        inv_cost_arr = [0.0] * n
        inv_max_arr = [0.0] * n
        inv_cost_energy_arr = [0.0] * n
        inv_max_cap_arr = [0.0] * n
        for entry in state.investment_portfolio.values():
            if entry.technology_type == "battery" and entry.target_key == unit_key:
                for nd in entry.node_data:
                    if 0 <= nd.node_index < n:
                        inv_cost_arr[nd.node_index] = nd.invest_cost
                        inv_max_arr[nd.node_index] = nd.invest_max
                for ni, val in entry.invest_cost_energy.items():
                    if 0 <= ni < n:
                        inv_cost_energy_arr[ni] = val
                for ni, val in entry.invest_max_capacity.items():
                    if 0 <= ni < n:
                        inv_max_cap_arr[ni] = val
        bat_dict["invest_cost"] = inv_cost_arr
        bat_dict["invest_max_power"] = inv_max_arr
        bat_dict["invest_cost_energy"] = inv_cost_energy_arr
        bat_dict["invest_max_capacity"] = inv_max_cap_arr
        # Export discharge cost curve if any instance uses non-flat
        _has_dc_curve = any(
            getattr(inst, 'discharge_cost_curve_type', 'flat') != 'flat'
            for inst in node_instances.values()
        )
        if _has_dc_curve:
            _dc_arr = [None] * n
            for _ni, _inst in node_instances.items():
                _ct = getattr(_inst, 'discharge_cost_curve_type', 'flat')
                _cd = getattr(_inst, 'discharge_cost_curve_data', None)
                _cfg = _gui_data_to_cost_curve_config(_ct, _cd)
                _dc_arr[_ni] = _cfg if _cfg else {"curve_type": "flat"}
            bat_dict["discharge_cost_curve"] = _dc_arr
        batteries_dict[unit_key] = bat_dict
    sys_dict["batteries"] = batteries_dict

    # Build ID normalization map: GUI instance_id → canonical "{unit_key}_n{node}"
    # The READ side always produces IDs in canonical format, so we must write
    # using the same format for gui_layout keys and endpoint refs to survive
    # a round-trip.
    _id_remap: dict[tuple[str, str], str] = {}
    for gid, inst in state.generators.items():
        canonical = f"{inst.unit_key}_n{inst.node}"
        if gid != canonical:
            _id_remap[("generator", gid)] = canonical
    for bid, inst in state.batteries.items():
        canonical = f"{inst.unit_key}_n{inst.node}"
        if bid != canonical:
            _id_remap[("battery", bid)] = canonical
    for eid, inst in state.electrolyzers.items():
        canonical = f"{inst.unit_key}_n{inst.node}"
        if eid != canonical:
            _id_remap[("electrolyzer", eid)] = canonical

    def _remap_ep_id(ep: EndpointRef | None) -> str | None:
        """Return the canonical element_id for an EndpointRef, or None."""
        if ep is None:
            return None
        return _id_remap.get((ep.element_type, ep.element_id), ep.element_id)

    # Build bus_id (string) → 0-based output index mapping for persisting bus indices
    _bus_id_to_idx = {bid: idx for idx, bid in enumerate(state.buses.keys())} if state.buses else {}

    # --- Clear structural element keys so deletions are persisted ---
    # (generators/batteries/electrolyzers already cleared individually above/below)
    for _clear_key in (
        "transmission_lines_geo", "transmission_lines",
        "buses",
        "transformers",
        "acdc_converters",
        "freq_converters",
        "development_zones",
        "fuel_entry_points",
        "primary_energy_sources",
        "fuel_infrastructure",
        "fuels",
        "ev_categories", "ev_quantity", "base_patterns", "ev_initial_soc",
        "rooftop_solar_config",
        "electric_demand",
        "sector_distribution",
        "non_electric_demand",
    ):
        sys_dict.pop(_clear_key, None)

    # Transmission lines geo (always emit with line_id for round-trip).
    # We exclude *visual wire-lines* — decorative segments connecting
    # equipment / transformers to their bus on the map. They have
    # zero capacity and are regenerated on load by
    # ``rebuild_visual_wire_lines``.
    #
    # CRUCIAL: a line is treated as wire-line only when BOTH conditions
    # hold (non-bus endpoint AND zero capacity). Otherwise a legitimate
    # transmission line that happens to terminate at a transformer node
    # (e.g. a line into a substation modelled with explicit transformer
    # endpoints) would be silently lost on save → distorted reload.
    def _is_wire_line(ln) -> bool:
        if getattr(ln, "decorative", False):
            return True
        wire_types = {
            "generator", "battery", "electrolyzer",
            "transformer", "acdc_converter", "freq_converter",
        }
        has_eq_endpoint = (
            (ln.from_endpoint and ln.from_endpoint.element_type in wire_types)
            or (ln.to_endpoint and ln.to_endpoint.element_type in wire_types)
        )
        zero_capacity = (getattr(ln, "capacity_mw", 0) or 0) <= 0
        return has_eq_endpoint and zero_capacity

    real_lines = [
        ln for ln in state.transmission_lines if not _is_wire_line(ln)
    ]
    if real_lines:
        sys_dict["transmission_lines_geo"] = [
            {
                k: v
                for k, v in {
                    "line_id": ln.line_id,
                    "from_node": ln.from_node,
                    "to_node": ln.to_node,
                    "from_bus": _bus_id_to_idx.get(ln.from_bus) if ln.from_bus else None,
                    "to_bus": _bus_id_to_idx.get(ln.to_bus) if ln.to_bus else None,
                    "capacity_mw": ln.capacity_mw,
                    "waypoints": [{"latitude": wp.lat, "longitude": wp.lng} for wp in (ln.waypoints or [])],
                    "voltage_kv": ln.voltage_kv,
                    "line_type": ln.line_type,
                    "length_km": ln.length_km,
                    "base_impedance": ln.base_impedance,
                    "reactance_per_km": ln.reactance_per_km,
                    "reactance_pu": ln.reactance_pu,
                    "resistance_pu": ln.resistance_pu,
                    "susceptance_pu": ln.susceptance_pu,
                    "num_circuits": ln.num_circuits if ln.num_circuits != 1 else None,
                    "frequency_hz": getattr(ln, 'frequency_hz', 50.0) if getattr(ln, 'frequency_hz', 50.0) != 50.0 else None,
                    "current_type": getattr(ln, 'current_type', 'AC') if getattr(ln, 'current_type', 'AC') != 'AC' else None,
                    # Preserve endpoint references for spatial round-trip fidelity
                    "from_endpoint_type": ln.from_endpoint.element_type if ln.from_endpoint else None,
                    "from_endpoint_id": _remap_ep_id(ln.from_endpoint),
                    "to_endpoint_type": ln.to_endpoint.element_type if ln.to_endpoint else None,
                    "to_endpoint_id": _remap_ep_id(ln.to_endpoint),
                }.items()
                if v is not None
            }
            for ln in real_lines
        ]

    # Buses
    if state.buses:
        sys_dict["buses"] = [
            {
                k: v
                for k, v in {
                    "bus_id": bus.bus_id,
                    "name": bus.name,
                    "parent_node": bus.parent_node,
                    "voltage_kv": bus.voltage_kv,
                    "frequency_hz": bus.frequency_hz if bus.frequency_hz != 50.0 else None,
                    "current_type": bus.current_type if bus.current_type in ("DC",) else None,
                    "bus_type": bus.bus_type if bus.bus_type != "PQ" else None,
                    "role": bus.role,
                    "demand_fraction": bus.demand_fraction,
                }.items()
                if v is not None
            }
            for bus in state.buses.values()
        ]

    # Transformers
    if state.transformers:
        sys_dict["transformers"] = [
            {
                k: v
                for k, v in {
                    "name": t.name,
                    "from_node": t.from_node,
                    "to_node": t.to_node,
                    "from_bus": _bus_id_to_idx.get(t.from_bus) if getattr(t, 'from_bus', None) else None,
                    "to_bus": _bus_id_to_idx.get(t.to_bus) if getattr(t, 'to_bus', None) else None,
                    "from_voltage_kv": t.from_voltage_kv,
                    "to_voltage_kv": t.to_voltage_kv,
                    "rated_power_mva": t.rated_power_mva,
                    "impedance_pu": t.impedance_pu,
                    "losses_fraction": t.losses_fraction,
                }.items()
                if v is not None
            }
            for t in state.transformers
        ]

    # AC/DC Converters
    if state.acdc_converters:
        def _acdc_dict(c):
            d = {
                "name": c.name,
                "converter_type": c.converter_type,
                "from_node": c.from_node,
                "to_node": c.to_node,
                "from_voltage_kv": c.from_voltage_kv,
                "dc_voltage_kv": c.dc_voltage_kv,
                "rated_power_mva": c.rated_power_mva,
                "min_power_mva": c.min_power_mva,
                "efficiency_rectify": c.efficiency_rectify,
                "efficiency_invert": c.efficiency_invert,
                "standby_losses_mw": c.standby_losses_mw,
                "reactive_power_min_mvar": c.reactive_power_min_mvar,
                "reactive_power_max_mvar": c.reactive_power_max_mvar,
                "power_factor": c.power_factor,
                "impedance_pu": c.impedance_pu,
                "resistance_pu": c.resistance_pu,
                "fixed_cost": c.fixed_cost,
                "variable_cost": c.variable_cost,
                "life_time": c.life_time,
                "initial_age": c.initial_age,
                "degradation_rate": c.degradation_rate,
            }
            fb = _bus_id_to_idx.get(c.from_bus) if getattr(c, 'from_bus', None) else None
            tb = _bus_id_to_idx.get(c.to_bus) if getattr(c, 'to_bus', None) else None
            if fb is not None:
                d["from_bus"] = fb
            if tb is not None:
                d["to_bus"] = tb
            return d
        sys_dict["acdc_converters"] = [_acdc_dict(c) for c in state.acdc_converters]
        # Inject invest fields from portfolio
        for entry in state.investment_portfolio.values():
            if entry.technology_type == "acdc_converter" and entry.target_key:
                try:
                    ci = int(entry.target_key)
                except ValueError:
                    continue
                if 0 <= ci < len(sys_dict["acdc_converters"]):
                    nd = entry.node_data[0] if entry.node_data else None
                    sys_dict["acdc_converters"][ci]["invest_cost"] = nd.invest_cost if nd else 0.0
                    sys_dict["acdc_converters"][ci]["invest_max_power"] = nd.invest_max if nd else 0.0

    # Frequency Converters
    if state.freq_converters:
        def _freq_dict(c):
            d = {
                "name": c.name,
                "from_node": c.from_node,
                "to_node": c.to_node,
                "from_frequency_hz": c.from_frequency_hz,
                "to_frequency_hz": c.to_frequency_hz,
                "rated_power_mva": c.rated_power_mva,
                "min_power_mva": c.min_power_mva,
                "efficiency_a_to_b": c.efficiency_a_to_b,
                "efficiency_b_to_a": c.efficiency_b_to_a,
                "standby_losses_mw": c.standby_losses_mw,
                "reactive_power_min_mvar": c.reactive_power_min_mvar,
                "reactive_power_max_mvar": c.reactive_power_max_mvar,
                "impedance_pu": c.impedance_pu,
                "resistance_pu": c.resistance_pu,
                "fixed_cost": c.fixed_cost,
                "variable_cost": c.variable_cost,
                "life_time": c.life_time,
                "initial_age": c.initial_age,
                "degradation_rate": c.degradation_rate,
            }
            fb = _bus_id_to_idx.get(c.from_bus) if getattr(c, 'from_bus', None) else None
            tb = _bus_id_to_idx.get(c.to_bus) if getattr(c, 'to_bus', None) else None
            if fb is not None:
                d["from_bus"] = fb
            if tb is not None:
                d["to_bus"] = tb
            return d
        sys_dict["freq_converters"] = [_freq_dict(c) for c in state.freq_converters]
        # Inject invest fields from portfolio
        for entry in state.investment_portfolio.values():
            if entry.technology_type == "freq_converter" and entry.target_key:
                try:
                    ci = int(entry.target_key)
                except ValueError:
                    continue
                if 0 <= ci < len(sys_dict["freq_converters"]):
                    nd = entry.node_data[0] if entry.node_data else None
                    sys_dict["freq_converters"][ci]["invest_cost"] = nd.invest_cost if nd else 0.0
                    sys_dict["freq_converters"][ci]["invest_max_power"] = nd.invest_max if nd else 0.0

    # Development zones
    if state.development_zones:
        sys_dict["development_zones"] = []
        for z in state.development_zones:
            zd = {
                "name": z.name,
                "technology": z.technology,
                "layer": z.layer,
                "polygon": [{"latitude": p.lat, "longitude": p.lng} for p in z.polygon],
                "max_capacity_mw": z.max_capacity_mw,
                "notes": z.notes,
                "line_cost_per_mw_km": z.line_cost_per_mw_km,
                "transformer_cost_per_mw": z.transformer_cost_per_mw,
            }
            if z.target_bus_override is not None:
                zd["target_bus"] = z.target_bus_override
            if z.allowed_generators:
                zd["allowed_generators"] = z.allowed_generators
            if z.allowed_technologies:
                zd["allowed_technologies"] = z.allowed_technologies
            if z.exclusive:
                zd["exclusive"] = True
            sys_dict["development_zones"].append(zd)

    # Fuel entry points
    if state.fuel_entry_points:
        sys_dict["fuel_entry_points"] = [
            {
                "name": fe.name,
                "fuel": fe.fuels[0] if fe.fuels else "",
                "fuels": fe.fuels,
                "node": fe.node,
                "coordinate": {
                    "latitude": fe.coordinate.lat,
                    "longitude": fe.coordinate.lng,
                },
                "max_import_rate": (fe.fuel_params[fe.fuels[0]].max_import_rate
                                    if fe.fuels and fe.fuels[0] in fe.fuel_params else 0.0),
                "import_cost": (fe.fuel_params[fe.fuels[0]].import_cost
                                if fe.fuels and fe.fuels[0] in fe.fuel_params else 0.0),
                "fuel_params": {
                    fname: {
                        "max_import_rate": fp.max_import_rate,
                        "import_cost": fp.import_cost,
                    }
                    for fname, fp in fe.fuel_params.items()
                },
            }
            for fe in state.fuel_entry_points
        ]

    # Primary energy sources
    if state.fuel_sources:
        sys_dict["primary_energy_sources"] = {
            src_id: {
                "name": src.name,
                "unit": src.unit,
                "max_availability": src.max_availability,
                "import_cost": src.import_cost,
                "storage_capacity": src.storage_capacity,
                "initial_storage_level": src.initial_storage_level,
                "min_storage_level": src.min_storage_level,
                "storage_investment_cost": src.storage_investment_cost,
                "transport_cost": src.transport_cost,
                "transport_losses": src.transport_losses,
                "max_storage_investment_per_node": src.max_storage_investment_per_node,
                "max_transport_investment_per_arc": src.max_transport_investment_per_arc,
            }
            for src_id, src in state.fuel_sources.items()
        }

    # Fuel transport routes
    if state.fuel_transport_routes:
        sys_dict.setdefault("fuel_infrastructure", {})
        sys_dict["fuel_infrastructure"]["transport_pipelines"] = {
            rt.route_id: {
                "route_id": rt.route_id,
                "fuel": rt.fuels[0] if rt.fuels else "",
                "fuels": rt.fuels,
                "from_node": rt.from_node,
                "to_node": rt.to_node,
                "capacity": (rt.fuel_params[rt.fuels[0]].capacity
                             if rt.fuels and rt.fuels[0] in rt.fuel_params else rt.capacity),
                "transport_cost": (rt.fuel_params[rt.fuels[0]].transport_cost
                                   if rt.fuels and rt.fuels[0] in rt.fuel_params else rt.transport_cost),
                "losses_fraction": (rt.fuel_params[rt.fuels[0]].losses_fraction
                                    if rt.fuels and rt.fuels[0] in rt.fuel_params else rt.losses_fraction),
                "fuel_params": {
                    fname: {
                        "capacity": fp.capacity,
                        "transport_cost": fp.transport_cost,
                        "losses_fraction": fp.losses_fraction,
                    }
                    for fname, fp in rt.fuel_params.items()
                },
                "length_km": rt.length_km,
                "waypoints": [
                    {"latitude": wp.lat, "longitude": wp.lng}
                    for wp in rt.waypoints
                ] if rt.waypoints else [],
                **({"from_endpoint_type": rt.from_endpoint.element_type,
                    "from_endpoint_id": _remap_ep_id(rt.from_endpoint)}
                   if rt.from_endpoint else {}),
                **({"to_endpoint_type": rt.to_endpoint.element_type,
                    "to_endpoint_id": _remap_ep_id(rt.to_endpoint)}
                   if rt.to_endpoint else {}),
            }
            for rt in state.fuel_transport_routes
        }

    # Fuel storage facilities
    if state.fuel_storages:
        sys_dict.setdefault("fuel_infrastructure", {})
        sys_dict["fuel_infrastructure"]["storage_facilities"] = {
            fs.storage_id: {
                "name": fs.name,
                "fuel": fs.fuels[0] if fs.fuels else "",
                "fuels": list(fs.fuels),
                "node": fs.node,
                "capacity": (fs.fuel_params[fs.fuels[0]].capacity
                             if fs.fuels and fs.fuels[0] in fs.fuel_params else 0.0),
                "initial_level": (fs.fuel_params[fs.fuels[0]].initial_level
                                  if fs.fuels and fs.fuels[0] in fs.fuel_params else 0.5),
                "min_level": (fs.fuel_params[fs.fuels[0]].min_level
                              if fs.fuels and fs.fuels[0] in fs.fuel_params else 0.1),
                "fuel_params": {
                    fname: {
                        "capacity": fp.capacity,
                        "initial_level": fp.initial_level,
                        "min_level": fp.min_level,
                    }
                    for fname, fp in fs.fuel_params.items()
                },
            }
            for fs in state.fuel_storages.values()
        }
        # Inject invest fields from portfolio
        for entry in state.investment_portfolio.values():
            if entry.technology_type == "fuel_storage" and entry.target_key:
                sid = entry.target_key
                if sid in sys_dict["fuel_infrastructure"]["storage_facilities"]:
                    nd = entry.node_data[0] if entry.node_data else None
                    sys_dict["fuel_infrastructure"]["storage_facilities"][sid]["invest_cost"] = nd.invest_cost if nd else 0.0
                    sys_dict["fuel_infrastructure"]["storage_facilities"][sid]["invest_max_capacity"] = nd.invest_max if nd else 0.0

    # Route-based fuel transport model
    sys_dict["fuel_transport_routes"] = _build_fuel_transport_routes(state)
    # Keep NxN distance matrix for backward compatibility
    sys_dict["fuel_transport_distances"] = _build_fuel_transport_distances(state)

    # Map center / zoom
    if state.map_center:
        sys_dict["map_center"] = {
            "latitude": state.map_center.lat,
            "longitude": state.map_center.lng,
        }
    if state.map_zoom:
        sys_dict["map_zoom"] = state.map_zoom

    # Demand path(s) — always emit a per-node ``demand_paths`` list
    # built from each node's ``demand.csv_path``. The legacy singular
    # ``demand_path`` is dropped on save (loader still accepts it for
    # back-compat). Where a node has no path stored on the model we
    # fall back to the corresponding entry in ``state.demand_paths`` /
    # ``state.demand_path`` so a YAML referencing files that didn't
    # exist on disk at load time still round-trips intact.
    sys_dict.pop("demand_path", None)
    sys_dict.pop("demand_paths", None)
    legacy_paths = state.demand_paths or []
    fallback_single = state.demand_path or ""
    node_paths: list[str] = []
    for i, nd in enumerate(state.nodes):
        cp = (nd.demand.csv_path if nd.demand else None) or ""
        if not cp and i < len(legacy_paths):
            cp = legacy_paths[i] or ""
        if not cp:
            cp = fallback_single
        node_paths.append(cp)
    if any(node_paths):
        sys_dict["demand_paths"] = node_paths

    # Fuels (FuelConfig) — skip renewable defaults (auto-added on load)
    exportable_fuels = {
        fid: f for fid, f in state.fuels.items()
        if fid not in RENEWABLE_FUELS
    }
    if exportable_fuels:
        sys_dict["fuels"] = {
            fid: {
                k: v for k, v in {
                    "name": f.name,
                    "unit": f.unit,
                    "emission_factor": f.emission_factor,
                    "energy_content": f.energy_content,
                    "price_base": f.price_base,
                    "price_growth_rate": f.price_growth_rate,
                }.items() if v is not None
            }
            for fid, f in exportable_fuels.items()
        }

    # System settings
    s = state.settings
    sys_dict["demand_scale"] = s.demand_scale
    sys_dict["discount_rate"] = s.discount_rate
    sys_dict["base_lcoe"] = s.base_lcoe
    sys_dict["target_re_penetration"] = s.target_re_penetration
    sys_dict["min_annual_increment"] = s.min_annual_increment
    sys_dict["max_annual_increment"] = s.max_annual_increment
    sys_dict["max_annual_system_cost"] = s.max_annual_system_cost
    sys_dict["max_npv_penalty_per_mw"] = s.max_npv_penalty_per_mw
    sys_dict["max_decommission_cost_per_mw"] = s.max_decommission_cost_per_mw
    sys_dict["force_replacement"] = s.force_replacement
    sys_dict["life_extension_cost_factor"] = s.life_extension_cost_factor
    sys_dict["LOSS_DEMAND_TRHESHOLD"] = s.loss_demand_threshold
    sys_dict["inertia_limit_threshold"] = s.inertia_limit_threshold
    sys_dict["sim_rooftop"] = s.sim_rooftop

    # Penalties
    p = state.penalties
    sys_dict["penalties"] = {
        "LOSS_OF_LOAD": p.loss_of_load,
        "LOSS_OF_RESERVE_STATIC": p.loss_of_reserve_static,
        "LOSS_OF_RESERVE_DYNAMIC": p.loss_of_reserve_dynamic,
        "LOSS_OF_INERTIA": p.loss_of_inertia,
        "TransferMargin": p.transfer_margin,
        "Curtailment": p.curtailment,
        "max_curtailment_ratio": p.max_curtailment_ratio,
        "rooftop_curtailment": p.rooftop_curtailment,
        "CO2_cost": p.co2_cost,
        "CO2_budget_violation": p.co2_budget_violation,
        "FRE_penetration_loss": p.fre_penetration_loss,
        "EV_loss": p.ev_loss,
        "loss_of_fuel_supply": p.loss_of_fuel_supply,
        "coupling_slack_penalty": p.coupling_slack_penalty,
        "transport_congestion": p.transport_congestion,
        "storage_violation": p.storage_violation,
        "non_electric_demand_loss": p.non_electric_demand_loss,
        "curtailment_cost": p.curtailment_cost,
        "curtailment_excess_penalty": p.curtailment_excess_penalty,
        "re_excess_penalty": p.re_excess_penalty,
    }

    # CO2 Budget (from settings)
    sys_dict["co2_budget"] = {
        "enabled": s.co2_budget_enabled,
        "annual_budget": s.co2_annual_budget,
    }

    # Power flow mode
    sys_dict["power_flow_mode"] = state.power_flow_mode

    # DC Power Flow (system-level: angle limits + slack bus)
    dc = state.dc_power_flow
    sys_dict["dc_power_flow"] = {
        "max_angle_diff_deg": dc.max_angle_diff_deg,
        "slack_bus": dc.slack_bus,
    }

    # AC Power Flow
    ac = state.ac_power_flow
    sys_dict["ac_power_flow"] = {
        "base_mva": ac.base_mva,
        "voltage_min_pu": ac.voltage_min_pu,
        "voltage_max_pu": ac.voltage_max_pu,
        "default_power_factor": ac.default_power_factor,
        "load_power_factor": ac.load_power_factor,
        "q_slack_penalty": ac.q_slack_penalty,
        "min_reactance_pu": ac.min_reactance_pu,
        "tap_ratio_min": ac.tap_ratio_min,
        "tap_ratio_max": ac.tap_ratio_max,
        "q_min_ratio": ac.q_min_ratio,
    }

    # Criticality penalties (from penalties)
    sys_dict["criticality_penalties"] = {
        "critical": p.criticality_critical,
        "high": p.criticality_high,
        "medium": p.criticality_medium,
        "low": p.criticality_low,
    }

    # Electrolyzers — single ElectrolyzerConfig (not a dict of configs)
    # Remove old singular electrolyzer key
    sys_dict.pop("electrolyzer", None)
    sys_dict.pop("electrolyzers", None)
    if state.electrolyzers:
        # Group electrolyzer instances by unit_key
        el_groups: dict[str, dict[int, GuiElectrolyzerInstance]] = defaultdict(dict)
        for inst in state.electrolyzers.values():
            el_groups[inst.unit_key][inst.node] = inst
        el_dict: dict[str, dict[str, Any]] = {}
        for unit_key, node_insts in el_groups.items():
            rep = next(iter(node_insts.values()))
            entry: dict[str, Any] = {
                "name": rep.name,
                "fuel": rep.fuel,
                "technology": rep.technology,
            }
            for fld in (
                'life_time', 'initial_age', 'degradation_rate',
                'rated_power', 'min_power', 'ramp_up', 'ramp_down',
                'eff_at_rated', 'eff_at_min',
                'fixed_cost', 'variable_cost',
            ):
                arr = [0.0] * n
                for ni, inst_n in node_insts.items():
                    if ni < n:
                        arr[ni] = getattr(inst_n, fld)
                entry[fld] = arr
            # Invest fields from portfolio
            inv_cost_arr = [0.0] * n
            inv_max_arr = [0.0] * n
            for pentry in state.investment_portfolio.values():
                if pentry.technology_type == "electrolyzer" and pentry.target_key == unit_key:
                    for nd in pentry.node_data:
                        if 0 <= nd.node_index < n:
                            inv_cost_arr[nd.node_index] = nd.invest_cost
                            inv_max_arr[nd.node_index] = nd.invest_max
            entry["invest_cost"] = inv_cost_arr
            entry["invest_max_power"] = inv_max_arr
            # Scalar fields
            entry["energy_per_kg_h2"] = rep.energy_per_kg_h2
            entry["water_cost"] = rep.water_cost
            el_dict[unit_key] = entry
        sys_dict["electrolyzers"] = el_dict

    # EV configuration
    ev = state.ev_config
    if ev.categories:
        sys_dict["ev_initial_soc"] = ev.initial_soc
        ev_cats: dict[str, Any] = {}
        ev_qty: dict[str, list] = {}
        ev_pat: dict[str, list] = {}
        for cid, cat in ev.categories.items():
            ev_cats[cid] = {
                "battery_capacity": cat.battery_capacity,
                "charging_power": cat.charging_power,
                "v2g_power": cat.v2g_power,
                "v2g_participation": cat.v2g_participation,
                "efficiency_charge": cat.efficiency_charge,
                "efficiency_discharge": cat.efficiency_discharge,
                "min_soc": cat.min_soc,
                "max_adoption": cat.max_adoption,
                "growth_rate": cat.growth_rate,
                "mid_point_fraction": cat.mid_point_fraction,
            }
            ev_qty[cid] = cat.quantity
            ev_pat[cid] = cat.base_pattern
        sys_dict["ev_categories"] = ev_cats
        sys_dict["ev_quantity"] = ev_qty
        sys_dict["base_patterns"] = ev_pat

    # Rooftop solar
    if state.rooftop_solar:
        rt = state.rooftop_solar
        sys_dict["rooftop_solar_config"] = {
            "adoption_scenario": rt.adoption_scenario,
            "weather_variability": rt.weather_variability,
            "simulation_seed": rt.simulation_seed,
            "performance_ratio": rt.performance_ratio,
            "degradation_rate": rt.degradation_rate,
            "cost_per_kw": rt.cost_per_kw,
            "cost_reduction_rate": rt.cost_reduction_rate,
            "o_and_m_cost": rt.o_and_m_cost,
            "base_year": rt.base_year,
            "target_year": rt.target_year,
            "systems_per_node": rt.systems_per_node,
            "avg_system_size": rt.avg_system_size,
            "initial_adoption": rt.initial_adoption,
            "max_adoption": rt.max_adoption,
            "adoption_rates": rt.adoption_rates,
        }

    # Demand sectors
    if state.demand_sectors:
        sys_dict["electric_demand"] = {
            sid: {
                "is_flexible": sec.is_flexible,
                "flexibility_ratio": sec.flexibility_ratio,
                "criticality": sec.criticality,
                "delay_tolerance": sec.delay_tolerance,
                "price_sensitivity": sec.price_sensitivity,
            }
            for sid, sec in state.demand_sectors.items()
        }

    # Sector distribution
    if state.sector_distribution:
        sys_dict["sector_distribution"] = {
            int(k): v for k, v in state.sector_distribution.items()
        }

    # Non-electric demand
    if state.non_electric_demand:
        sys_dict["non_electric_demand"] = {
            did: {
                "fuel": ned.fuel,
                "unit": ned.unit,
                "is_flexible": ned.is_flexible,
                "flexibility_ratio": ned.flexibility_ratio,
                "criticality": ned.criticality,
                "delay_tolerance": ned.delay_tolerance,
                "price_sensitivity": ned.price_sensitivity,
                "demand": ned.demand,
            }
            for did, ned in state.non_electric_demand.items()
        }

    # GUI equipment layout (absolute coordinates for spatial round-trip)
    layout: dict[str, dict[str, list[float]]] = {}
    for gid, inst in state.generators.items():
        if inst.latitude != 0.0 or inst.longitude != 0.0:
            cid = _id_remap.get(("generator", gid), gid)
            layout.setdefault("generators", {})[cid] = [inst.latitude, inst.longitude]
    for bid, inst in state.batteries.items():
        if inst.latitude != 0.0 or inst.longitude != 0.0:
            cid = _id_remap.get(("battery", bid), bid)
            layout.setdefault("batteries", {})[cid] = [inst.latitude, inst.longitude]
    for eid, inst in state.electrolyzers.items():
        if inst.latitude != 0.0 or inst.longitude != 0.0:
            cid = _id_remap.get(("electrolyzer", eid), eid)
            layout.setdefault("electrolyzers", {})[cid] = [inst.latitude, inst.longitude]
    for i, tr in enumerate(state.transformers):
        if tr.latitude != 0.0 or tr.longitude != 0.0:
            layout.setdefault("transformers", {})[str(i)] = [tr.latitude, tr.longitude]
    if state.buses:
        for bus_id, bus in state.buses.items():
            if bus.latitude != 0.0 or bus.longitude != 0.0:
                layout.setdefault("buses", {})[bus_id] = [bus.latitude, bus.longitude]
    for i, conv in enumerate(state.acdc_converters):
        if conv.latitude != 0.0 or conv.longitude != 0.0:
            layout.setdefault("acdc_converters", {})[str(i)] = [conv.latitude, conv.longitude]
    for i, conv in enumerate(state.freq_converters):
        if conv.latitude != 0.0 or conv.longitude != 0.0:
            layout.setdefault("freq_converters", {})[str(i)] = [conv.latitude, conv.longitude]
    for sid, fst in state.fuel_storages.items():
        if fst.latitude != 0.0 or fst.longitude != 0.0:
            layout.setdefault("fuel_storages", {})[sid] = [fst.latitude, fst.longitude]
    for i, fe in enumerate(state.fuel_entry_points):
        if fe.coordinate and (fe.coordinate.lat != 0.0 or fe.coordinate.lng != 0.0):
            layout.setdefault("fuel_entries", {})[str(i)] = [fe.coordinate.lat, fe.coordinate.lng]
    if layout:
        sys_dict["_gui_layout"] = layout

    # Per-element visual styles (color/size/shape/opacity/width). Stored
    # under ``_gui_styles`` so map customization survives a GUI round-trip.
    sys_dict.pop("_gui_styles", None)
    styles: dict[str, dict] = {}

    def _add_style(section: str, key, style):
        d = _style_to_dict(style)
        if d:
            styles.setdefault(section, {})[str(key)] = d

    for nd in state.nodes:
        _add_style("nodes", nd.index, nd.style)
    for bus_id, bus in state.buses.items():
        _add_style("buses", bus_id, bus.style)
    for gid, inst in state.generators.items():
        _add_style("generators", _id_remap.get(("generator", gid), gid), inst.style)
    for bid, inst in state.batteries.items():
        _add_style("batteries", _id_remap.get(("battery", bid), bid), inst.style)
    for eid, inst in state.electrolyzers.items():
        _add_style("electrolyzers", _id_remap.get(("electrolyzer", eid), eid), inst.style)
    for i, tr in enumerate(state.transformers):
        _add_style("transformers", i, tr.style)
    for i, conv in enumerate(state.acdc_converters):
        _add_style("acdc_converters", i, conv.style)
    for i, conv in enumerate(state.freq_converters):
        _add_style("freq_converters", i, conv.style)
    for ln in state.transmission_lines:
        _add_style("transmission_lines", ln.line_id, ln.style)
    for sid, fst in state.fuel_storages.items():
        _add_style("fuel_storages", sid, fst.style)
    for i, fe in enumerate(state.fuel_entry_points):
        _add_style("fuel_entries", i, fe.style)
    for rt in state.fuel_transport_routes:
        _add_style("fuel_routes", rt.route_id, rt.style)
    for i, zone in enumerate(state.development_zones):
        _add_style("zones", i, zone.style)
    if styles:
        sys_dict["_gui_styles"] = styles

    # Technologies
    if state.technologies:
        techs_dict = {}
        for tid, tech in state.technologies.items():
            td = {
                "name": tech.name,
                "category": tech.category,
                "fuel": tech.fuel,
                "life_time": tech.life_time,
                "degradation_rate": tech.degradation_rate,
                "eff_at_rated": tech.eff_at_rated,
                "eff_at_min": tech.eff_at_min,
                "invest_cost": tech.invest_cost,
                "invest_max_power": tech.invest_max_power,
                "invest_cost_energy": tech.invest_cost_energy,
                "invest_max_capacity": tech.invest_max_capacity,
            }
            if tech.style.color:
                td["color"] = tech.style.color
            techs_dict[tid] = td
        sys_dict["_technologies"] = techs_dict

        # Propagate color to optimizer technologies/battery_technologies sections
        opt_techs = sys_dict.get("technologies", {})
        for tid, tech in state.technologies.items():
            if tech.style.color and tid in opt_techs and isinstance(opt_techs[tid], dict):
                opt_techs[tid]["color"] = tech.style.color
        opt_bt = sys_dict.get("battery_technologies", {})
        for tid, tech in state.technologies.items():
            if tech.style.color and tech.category == "Storage":
                if tid in opt_bt and isinstance(opt_bt[tid], dict):
                    opt_bt[tid]["color"] = tech.style.color
