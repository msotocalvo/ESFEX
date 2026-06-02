"""Development zone preprocessor.

Expands a system configuration by adding virtual nodes and buses for each
development zone, enabling the optimizer to invest in zone generation with
explicit interconnection costs (transmission line + transformer).
"""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from esfex.config.schema import (
    BatteryConfig,
    BatteryTechnologyConfig,
    BusConfig,
    DevelopmentZoneConfig,
    GeoCoordinate,
    GeneratorConfig,
    SystemConfig,
    TechnologyConfig,
)

logger = logging.getLogger(__name__)

# Per-node array field names for generators
_GEN_PER_NODE_FIELDS = [
    "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
    "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
    "eff_at_rated", "eff_at_min", "inertia", "start_up_cost", "fuel_cost",
    "fixed_cost", "maintenance_cost", "invest_cost", "invest_max_power",
]

# Per-node array field names for batteries
_BAT_PER_NODE_FIELDS = [
    "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
    "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
    "eff_at_rated", "eff_at_min", "inertia", "start_up_cost", "fuel_cost",
    "fixed_cost", "maintenance_cost", "invest_cost", "invest_cost_energy",
    "invest_max_power", "invest_max_capacity",
    "efficiency_charge", "efficiency_discharge", "soc_initial", "max_DoD",
    "capacity", "MaxChargePower", "MaxDischargePower",
]

# Per-node array field names for technology investment candidates
_TECH_PER_NODE_FIELDS = [
    "invest_cost", "invest_max_power", "eff_at_rated", "degradation_rate",
    "min_output", "ramp_up", "ramp_down", "fuel_cost", "fixed_cost",
    "maintenance_cost", "inertia", "start_up_cost", "eff_at_min",
    "decommissioning_cost",
]

# Per-node array field names for battery technology investment candidates
_BAT_TECH_PER_NODE_FIELDS = [
    "invest_cost_power", "invest_cost_energy", "invest_max_power",
    "invest_max_capacity", "efficiency_charge", "efficiency_discharge",
    "degradation_rate", "soc_initial", "max_DoD", "maintenance_cost",
    "inertia", "throughput_degradation_cost", "decommissioning_cost",
]


@dataclass
class ZoneMapping:
    """Maps a development zone to its virtual node/bus in the expanded config."""

    zone_name: str
    technology: str
    virtual_node_idx: int
    virtual_bus_idx: int
    nearest_bus_idx: int
    nearest_bus_parent_node: int
    distance_km: float
    interconnection_cost_per_mw: float
    matched_generators: list[str] = field(default_factory=list)
    matched_batteries: list[str] = field(default_factory=list)
    matched_technologies: list[str] = field(default_factory=list)
    matched_battery_technologies: list[str] = field(default_factory=list)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _polygon_centroid(polygon: list[GeoCoordinate]) -> tuple[float, float]:
    """Compute centroid of a polygon as mean of vertices."""
    if not polygon:
        return (0.0, 0.0)
    lat = sum(p.latitude for p in polygon) / len(polygon)
    lon = sum(p.longitude for p in polygon) / len(polygon)
    return (lat, lon)


def _find_nearest_bus(
    centroid: tuple[float, float],
    buses: list[BusConfig],
    node_coords: list[GeoCoordinate],
) -> tuple[int, int, float]:
    """Find the nearest bus to a centroid.

    Returns (bus_index, parent_node_index, distance_km).
    Bus position is approximated by its parent node's coordinates.
    """
    best_idx = 0
    best_node = 0
    best_dist = float("inf")
    clat, clon = centroid

    for i, bus in enumerate(buses):
        pn = bus.parent_node
        if pn < len(node_coords):
            coord = node_coords[pn]
            d = _haversine(clat, clon, coord.latitude, coord.longitude)
            if d < best_dist:
                best_dist = d
                best_idx = i
                best_node = pn
    return (best_idx, best_node, best_dist)


def _match_generators(
    zone: DevelopmentZoneConfig,
    generators: dict[str, GeneratorConfig],
) -> list[str]:
    """Match zone technology to generator keys."""
    if zone.allowed_generators:
        return [k for k in zone.allowed_generators if k in generators]

    tech_lower = zone.technology.lower()
    matched = []
    for key, gen in generators.items():
        if (tech_lower in key.lower()
                or tech_lower in gen.fuel.lower()
                or (gen.technology and tech_lower in gen.technology.lower())):
            matched.append(key)
    return matched


def _match_batteries(
    zone: DevelopmentZoneConfig,
    batteries: dict[str, BatteryConfig],
) -> list[str]:
    """Match zone technology to battery keys (for Storage/Battery zones)."""
    tech_lower = zone.technology.lower()
    if tech_lower not in ("battery", "storage", "bess", "ess"):
        return []
    return list(batteries.keys())


def _match_technologies(
    zone: DevelopmentZoneConfig,
    technologies: dict[str, TechnologyConfig],
) -> list[str]:
    """Match zone to technology investment candidate keys.

    Uses zone.allowed_technologies if set, otherwise fuzzy-matches
    zone.technology against tech name, fuel, and key.
    """
    if zone.allowed_technologies:
        return [k for k in zone.allowed_technologies if k in technologies]

    tech_lower = zone.technology.lower()
    matched = []
    for key, tech in technologies.items():
        if (tech_lower in key.lower()
                or tech_lower in tech.name.lower()
                or tech_lower in tech.fuel.lower()):
            matched.append(key)
    return matched


def _match_battery_technologies(
    zone: DevelopmentZoneConfig,
    battery_technologies: dict[str, BatteryTechnologyConfig],
) -> list[str]:
    """Match zone to battery technology investment candidate keys.

    Only matches if the zone technology string is battery-like.
    Uses zone.allowed_technologies if set.
    """
    tech_lower = zone.technology.lower()
    if tech_lower not in ("battery", "storage", "bess", "ess"):
        return []

    if zone.allowed_technologies:
        return [k for k in zone.allowed_technologies if k in battery_technologies]

    return list(battery_technologies.keys())


def expand_config_with_zones(
    config: SystemConfig,
) -> tuple[SystemConfig, list[ZoneMapping]]:
    """Expand a system config by adding virtual nodes/buses for development zones.

    Each zone becomes a virtual node+bus connected to the nearest existing bus
    via a candidate transmission line. The interconnection cost (line + transformer)
    is encoded in ``transference_invest_cost`` for the zone's bus.

    Args:
        config: Original system configuration with development zones.

    Returns:
        Tuple of (expanded_config, zone_mappings).
        If no zones, returns (config, []).
    """
    zones = config.development_zones
    if not zones:
        return (config, [])

    cfg = deepcopy(config)
    num_nodes_orig = cfg.nodes.num_nodes
    num_zones = len(zones)

    # Resolve existing buses (auto-create 1-per-node if none defined)
    existing_buses: list[BusConfig] = []
    if hasattr(cfg, "buses") and cfg.buses:
        existing_buses = list(cfg.buses)
    else:
        existing_buses = [
            BusConfig(bus_id=f"bus_{i}", parent_node=i, demand_fraction=1.0)
            for i in range(num_nodes_orig)
        ]
    num_buses_orig = len(existing_buses)

    # Node coordinates for distance calculations
    node_coords = cfg.nodes.node_coordinates or []

    mappings: list[ZoneMapping] = []

    # ── Phase 1: Compute zone mappings ────────────────────────────────
    for z_idx, zone in enumerate(zones):
        centroid = _polygon_centroid(zone.polygon)
        virt_node = num_nodes_orig + z_idx
        virt_bus = num_buses_orig + z_idx

        if zone.target_bus is not None and zone.target_bus < num_buses_orig:
            bus_idx = zone.target_bus
            parent_node = existing_buses[bus_idx].parent_node
            if parent_node < len(node_coords):
                coord = node_coords[parent_node]
                dist = _haversine(
                    centroid[0], centroid[1],
                    coord.latitude, coord.longitude,
                )
            else:
                dist = 0.0
        elif node_coords:
            bus_idx, parent_node, dist = _find_nearest_bus(
                centroid, existing_buses, node_coords,
            )
        else:
            bus_idx, parent_node, dist = 0, 0, 0.0

        intercon_cost = zone.line_cost_per_mw_km * dist + zone.transformer_cost_per_mw

        matched_gens = _match_generators(zone, cfg.generators)
        matched_bats = _match_batteries(zone, cfg.batteries)
        matched_techs = _match_technologies(zone, cfg.technologies)
        matched_bat_techs = _match_battery_technologies(
            zone, cfg.battery_technologies,
        )

        mapping = ZoneMapping(
            zone_name=zone.name,
            technology=zone.technology,
            virtual_node_idx=virt_node,
            virtual_bus_idx=virt_bus,
            nearest_bus_idx=bus_idx,
            nearest_bus_parent_node=parent_node,
            distance_km=dist,
            interconnection_cost_per_mw=intercon_cost,
            matched_generators=matched_gens,
            matched_batteries=matched_bats,
            matched_technologies=matched_techs,
            matched_battery_technologies=matched_bat_techs,
        )
        mappings.append(mapping)

        logger.info(
            "Zone '%s' (%s): virtual node %d, bus %d → nearest bus %d "
            "(node %d, %.1f km), interconnection cost $%.0f/MW, "
            "matched gens: %s, matched bats: %s, "
            "matched techs: %s, matched bat_techs: %s",
            zone.name, zone.technology, virt_node, virt_bus,
            bus_idx, parent_node, dist, intercon_cost,
            matched_gens or "(none)", matched_bats or "(none)",
            matched_techs or "(none)", matched_bat_techs or "(none)",
        )

    # ── Phase 2: Expand node config ───────────────────────────────────
    new_num_nodes = num_nodes_orig + num_zones

    # Expand adjacency matrix (NxN → (N+Z)x(N+Z))
    old_conn = np.array(cfg.nodes.nodes_connections).reshape(
        num_nodes_orig, num_nodes_orig,
    )
    new_conn = np.zeros((new_num_nodes, new_num_nodes))
    new_conn[:num_nodes_orig, :num_nodes_orig] = old_conn
    for m in mappings:
        # Epsilon connection to trigger variable creation in Julia
        new_conn[m.virtual_node_idx, m.nearest_bus_parent_node] = 0.001
        new_conn[m.nearest_bus_parent_node, m.virtual_node_idx] = 0.001
    cfg.nodes.nodes_connections = new_conn.flatten().tolist()

    # Expand the fuel transport distance matrix (N×N → (N+Z)×(N+Z)). Zones are
    # electrical, so they carry no fuel transport — zone rows/cols stay zero.
    # Without this the matrix keeps its original N² size while num_nodes grows,
    # and convert_network_config's reshape(num_nodes, num_nodes) crashes.
    ftd = getattr(cfg, "fuel_transport_distances", None)
    if ftd:
        old_ftd = np.array(ftd, dtype=float)
        if old_ftd.size == num_nodes_orig ** 2:
            old_ftd = old_ftd.reshape(num_nodes_orig, num_nodes_orig)
            new_ftd = np.zeros((new_num_nodes, new_num_nodes))
            new_ftd[:num_nodes_orig, :num_nodes_orig] = old_ftd
            cfg.fuel_transport_distances = new_ftd.tolist()

    # Expand per-node arrays
    for m in mappings:
        ref = m.nearest_bus_parent_node  # reference node for defaults

        cfg.nodes.reserve_static.append(0.0)
        cfg.nodes.reserve_dynamic.append(0.0)
        cfg.nodes.reserve_duration.append(1)
        cfg.nodes.losses.append(0.0)

    # Expand transference invest cost/max (currently per-node vectors in schema)
    # Pad existing arrays to new_num_nodes, then set zone values
    while len(cfg.nodes.transference_invest_cost) < new_num_nodes:
        cfg.nodes.transference_invest_cost.append(0.0)
    while len(cfg.nodes.transference_invest_max) < new_num_nodes:
        cfg.nodes.transference_invest_max.append(0.0)

    for m in mappings:
        cfg.nodes.transference_invest_cost[m.virtual_node_idx] = (
            m.interconnection_cost_per_mw
        )
        max_cap = zones[mappings.index(m)].max_capacity_mw
        cfg.nodes.transference_invest_max[m.virtual_node_idx] = (
            max_cap if max_cap is not None else 1e6
        )

    # Expand node coordinates
    if cfg.nodes.node_coordinates is None:
        cfg.nodes.node_coordinates = []
    for z_idx, zone in enumerate(zones):
        centroid = _polygon_centroid(zone.polygon)
        cfg.nodes.node_coordinates.append(
            GeoCoordinate(latitude=centroid[0], longitude=centroid[1]),
        )

    # Expand node names
    if cfg.nodes.node_names is None:
        cfg.nodes.node_names = [f"node_{i}" for i in range(num_nodes_orig)]
    for zone in zones:
        cfg.nodes.node_names.append(f"zone_{zone.name}")

    cfg.nodes.num_nodes = new_num_nodes

    # ── Phase 3: Expand generator per-node arrays ─────────────────────
    for gen_key, gen in cfg.generators.items():
        for m in mappings:
            ref = m.nearest_bus_parent_node
            is_matched = gen_key in m.matched_generators

            for field_name in _GEN_PER_NODE_FIELDS:
                arr = getattr(gen, field_name)
                ref_val = arr[ref] if ref < len(arr) else 0

                if field_name == "invest_max_power":
                    zone = zones[mappings.index(m)]
                    val = (zone.max_capacity_mw or 0.0) if is_matched else 0.0
                elif field_name == "rated_power":
                    val = 0.0  # nothing built yet
                elif field_name == "invest_cost":
                    val = ref_val if is_matched else 0.0
                elif is_matched:
                    val = ref_val  # copy from reference node
                else:
                    # Non-matching generator: zero out investment, copy safe defaults
                    if field_name in ("life_time", "min_up", "min_down"):
                        val = int(ref_val) if ref_val else 1
                    else:
                        val = 0.0

                arr.append(type(arr[0])(val) if arr else val)

    # ── Phase 4: Expand battery per-node arrays ───────────────────────
    for bat_key, bat in cfg.batteries.items():
        for m in mappings:
            ref = m.nearest_bus_parent_node
            is_matched = bat_key in m.matched_batteries

            for field_name in _BAT_PER_NODE_FIELDS:
                arr = getattr(bat, field_name)
                ref_val = arr[ref] if ref < len(arr) else 0

                if field_name == "invest_max_power":
                    zone = zones[mappings.index(m)]
                    val = (zone.max_capacity_mw or 0.0) if is_matched else 0.0
                elif field_name in ("rated_power", "MaxChargePower",
                                     "MaxDischargePower", "capacity"):
                    val = 0.0
                elif field_name == "invest_cost":
                    val = ref_val if is_matched else 0.0
                elif field_name == "invest_cost_energy":
                    val = ref_val if is_matched else 0.0
                elif field_name == "invest_max_capacity":
                    val = ref_val if is_matched else 0.0
                elif is_matched:
                    val = ref_val
                else:
                    if field_name in ("life_time", "min_up", "min_down"):
                        val = int(ref_val) if ref_val else 1
                    else:
                        val = 0.0

                arr.append(type(arr[0])(val) if arr else val)

    # ── Phase 5: Expand technology per-node arrays ───────────────────
    for tech_key, tech in cfg.technologies.items():
        for m in mappings:
            ref = m.nearest_bus_parent_node
            is_matched = tech_key in m.matched_technologies
            zone_obj = zones[mappings.index(m)]

            # Per-tech max invest: check allowed_technologies dict first,
            # then fall back to zone.max_capacity_mw
            tech_max_cap = None
            if is_matched and zone_obj.allowed_technologies:
                tech_max_cap = zone_obj.allowed_technologies.get(tech_key, 0.0)

            for field_name in _TECH_PER_NODE_FIELDS:
                arr = getattr(tech, field_name)
                ref_val = arr[ref] if ref < len(arr) else 0.0

                if field_name == "invest_max_power":
                    if not is_matched:
                        val = 0.0
                    elif tech_max_cap is not None and tech_max_cap > 0:
                        val = tech_max_cap
                    else:
                        max_cap = zone_obj.max_capacity_mw
                        val = max_cap if max_cap is not None else 1e6
                elif field_name == "invest_cost":
                    val = ref_val if is_matched else 0.0
                elif is_matched:
                    val = ref_val
                else:
                    val = 0.0

                arr.append(float(val))

    # ── Phase 5b: Expand battery technology per-node arrays ────────
    for bt_key, bat_tech in cfg.battery_technologies.items():
        for m in mappings:
            ref = m.nearest_bus_parent_node
            is_matched = bt_key in m.matched_battery_technologies
            zone_obj = zones[mappings.index(m)]

            # Per-tech max invest for battery technologies
            bt_max_cap = None
            if is_matched and zone_obj.allowed_technologies:
                bt_max_cap = zone_obj.allowed_technologies.get(bt_key, 0.0)

            for field_name in _BAT_TECH_PER_NODE_FIELDS:
                arr = getattr(bat_tech, field_name)
                ref_val = arr[ref] if ref < len(arr) else 0.0

                if field_name == "invest_max_power":
                    if not is_matched:
                        val = 0.0
                    elif bt_max_cap is not None and bt_max_cap > 0:
                        val = bt_max_cap
                    else:
                        max_cap = zone_obj.max_capacity_mw
                        val = max_cap if max_cap is not None else 1e6
                elif field_name == "invest_max_capacity":
                    val = ref_val if is_matched else 0.0
                elif field_name in ("invest_cost_power", "invest_cost_energy"):
                    val = ref_val if is_matched else 0.0
                elif is_matched:
                    val = ref_val
                else:
                    val = 0.0

                arr.append(float(val))

    # ── Phase 5c: Exclusive mode — zero out original nodes ─────────
    for m in mappings:
        zone_obj = zones[mappings.index(m)]
        if not zone_obj.exclusive:
            continue

        for tech_key in m.matched_technologies:
            tech = cfg.technologies[tech_key]
            for orig_node in range(num_nodes_orig):
                tech.invest_max_power[orig_node] = 0.0

        for bt_key in m.matched_battery_technologies:
            bat_tech = cfg.battery_technologies[bt_key]
            for orig_node in range(num_nodes_orig):
                bat_tech.invest_max_power[orig_node] = 0.0
                bat_tech.invest_max_capacity[orig_node] = 0.0

    # ── Phase 6: Create virtual buses ──────────────────────────────
    if not hasattr(cfg, "buses") or cfg.buses is None:
        cfg.buses = existing_buses
    for z_idx, zone in enumerate(zones):
        m = mappings[z_idx]
        cfg.buses.append(
            BusConfig(
                bus_id=f"zone_bus_{z_idx}",
                parent_node=m.virtual_node_idx,
                demand_fraction=0.0,  # no demand at zone
            ),
        )

    return (cfg, mappings)
