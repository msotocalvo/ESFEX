"""Parse geo asset features into GUI system elements."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from esfex.visualization.data.gui_model import (
    EndpointRef,
    FuelStorageParams,
    GeoPoint,
    GuiACDCConverter,
    GuiBatteryInstance,
    GuiBus,
    GuiDevelopmentZone,
    GuiElectrolyzerInstance,
    GuiFrequencyConverter,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiFuelTransportRoute,
    GuiGeneratorInstance,
    GuiNode,
    GuiSystemState,
    GuiTransformer,
    GuiTransmissionLine,
    VisualStyle,
)
from esfex.visualization.panels.parse_geo_asset_dialog import ParseAssignment


@dataclass
class ParseResult:
    """Result of a geo asset parse operation."""

    buses_added: int = 0
    generators_added: int = 0
    batteries_added: int = 0
    lines_added: int = 0
    fuel_entries_added: int = 0
    zones_added: int = 0
    electrolyzers_added: int = 0
    transformers_added: int = 0
    acdc_converters_added: int = 0
    freq_converters_added: int = 0
    fuel_routes_added: int = 0
    fuel_storages_added: int = 0
    fuels_created: int = 0
    technologies_created: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.buses_added:
            parts.append(f"{self.buses_added} bus(es)")
        if self.generators_added:
            parts.append(f"{self.generators_added} generator(s)")
        if self.batteries_added:
            parts.append(f"{self.batteries_added} battery(ies)")
        if self.lines_added:
            parts.append(f"{self.lines_added} transmission line(s)")
        if self.fuel_entries_added:
            parts.append(f"{self.fuel_entries_added} fuel entry(ies)")
        if self.fuel_routes_added:
            parts.append(f"{self.fuel_routes_added} fuel route(s)")
        if self.zones_added:
            parts.append(f"{self.zones_added} development zone(s)")
        if self.electrolyzers_added:
            parts.append(f"{self.electrolyzers_added} electrolyzer(s)")
        if self.transformers_added:
            parts.append(f"{self.transformers_added} transformer(s)")
        if self.acdc_converters_added:
            parts.append(f"{self.acdc_converters_added} AC/DC converter(s)")
        if self.freq_converters_added:
            parts.append(f"{self.freq_converters_added} freq. converter(s)")
        if self.fuel_storages_added:
            parts.append(f"{self.fuel_storages_added} fuel storage(s)")
        if self.fuels_created:
            parts.append(f"{self.fuels_created} fuel(s)")
        if self.technologies_created:
            parts.append(f"{self.technologies_created} technology(ies)")
        msg = "Created: " + ", ".join(parts) if parts else "No elements created."
        if self.warnings:
            msg += f"\n\nWarnings ({len(self.warnings)}):\n"
            msg += "\n".join(f"  - {w}" for w in self.warnings)
        return msg


# ── GeoJSON property extraction ──────────────────────────────────


def _prop(props: dict, *keys: str, default: Any = None) -> Any:
    """Return the first matching property value from a dict, case-insensitive."""
    for k in keys:
        if k in props:
            return props[k]
    # Fallback: case-insensitive search
    lower_map = {pk.lower(): pk for pk in props}
    for k in keys:
        real = lower_map.get(k.lower())
        if real is not None:
            return props[real]
    return default


def _prop_float(props: dict, *keys: str, default: float = 0.0) -> float:
    val = _prop(props, *keys, default=default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _prop_int(props: dict, *keys: str, default: int = 0) -> int:
    val = _prop(props, *keys, default=default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _prop_str(props: dict, *keys: str, default: str = "") -> str:
    val = _prop(props, *keys, default=default)
    return str(val) if val is not None else default


def _normalize_voltage_kv(value: float) -> float:
    """Normalize a voltage to kilo-volts.

    GeoJSON data often stores voltage in *Volts* (e.g. 220 000) while the
    data-model expects kilo-volts.  Any value > 1 200 is assumed to be in
    Volts and divided by 1 000.  (The highest real-world transmission
    voltage is ~1 100 kV, so 1 200 is a safe threshold.)
    """
    if value > 1200:
        return value / 1000.0
    return value


def _feature_name(props: dict, fallback: str = "") -> str:
    """Extract a human-readable name from GeoJSON properties."""
    return _prop_str(props, "name", "Name", "NAME", "label", "Label",
                     "title", "Title", "id", "ID", default=fallback)


# ── Distance helpers ─────────────────────────────────────────────


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    la1, lo1 = math.radians(lat1), math.radians(lng1)
    la2, lo2 = math.radians(lat2), math.radians(lng2)
    dlat = la2 - la1
    dlng = lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _compute_node_centroids(state: GuiSystemState) -> dict[int, tuple[float, float]]:
    """Return spatial position for each node.

    Priority:
    1. Stored centroid on ``GuiNode`` (from config ``node_coordinates``).
    2. Fall back to average of bus positions belonging to that node.
    """
    result: dict[int, tuple[float, float]] = {}
    # 1. Prefer stored node centroid
    for node in state.nodes:
        if node.centroid_lat != 0.0 or node.centroid_lng != 0.0:
            result[node.index] = (node.centroid_lat, node.centroid_lng)
    # 2. Fall back to bus-position average for nodes without centroid
    sums: dict[int, list] = {}  # node_idx -> [sum_lat, sum_lng, count]
    for bus in state.buses.values():
        n = bus.parent_node
        if n in result:
            continue  # already have stored centroid
        if bus.latitude == 0.0 and bus.longitude == 0.0:
            continue
        if n not in sums:
            sums[n] = [0.0, 0.0, 0]
        sums[n][0] += bus.latitude
        sums[n][1] += bus.longitude
        sums[n][2] += 1
    for n, s in sums.items():
        if s[2] > 0 and n not in result:
            result[n] = (s[0] / s[2], s[1] / s[2])
    return result


def _find_nearest_node(
    lat: float, lng: float, nodes: list[GuiNode],
    centroids: dict[int, tuple[float, float]] | None = None,
) -> tuple[Optional[int], float]:
    """Find nearest node by bus-centroid distance.

    Falls back to node 0 when no centroids are available (e.g. empty system).
    """
    if centroids:
        best_idx: Optional[int] = None
        best_dist = float("inf")
        for node in nodes:
            if node.index in centroids:
                clat, clng = centroids[node.index]
                d = _haversine_km(lat, lng, clat, clng)
                if d < best_dist:
                    best_dist = d
                    best_idx = node.index
        if best_idx is not None:
            return best_idx, best_dist
    # Fallback: first node (no buses exist yet)
    if nodes:
        return nodes[0].index, 0.0
    return None, float("inf")


def _find_nearest_bus(
    lat: float, lng: float, state: GuiSystemState,
    _snap_km: float = 50.0,
    voltage_kv: float | None = None,
    voltage_tolerance: float = 0.1,
) -> tuple[Optional[str], float]:
    """Find nearest existing bus by geographic distance. Returns (bus_id, dist_km).

    Uses a bounding-box pre-filter to avoid computing haversine for
    distant buses — significant speedup for large networks.

    When *voltage_kv* is given, only buses whose voltage is within
    *voltage_tolerance* (relative) of the requested voltage are considered.
    This prevents snapping a 110 kV bus to a nearby 220 kV bus.
    """
    best_id: Optional[str] = None
    best_dist = float("inf")
    # Approximate degree threshold for pre-filter (~111 km/deg)
    deg_thresh = _snap_km / 111.0 + 0.5  # generous margin
    for bid, bus in state.buses.items():
        # Voltage filter: skip buses with incompatible voltage
        if voltage_kv is not None and voltage_kv > 0 and bus.voltage_kv > 0:
            ratio = bus.voltage_kv / voltage_kv
            if ratio < (1 - voltage_tolerance) or ratio > (1 + voltage_tolerance):
                continue
        blat = bus.latitude
        blng = bus.longitude
        # Quick rectangular pre-filter (avoids expensive haversine)
        if abs(blat - lat) > deg_thresh or abs(blng - lng) > deg_thresh:
            continue
        d = _haversine_km(lat, lng, blat, blng)
        if d < best_dist:
            best_dist = d
            best_id = bid
    return best_id, best_dist


def _find_nearest_fuel_point(
    lat: float, lng: float, state: GuiSystemState,
) -> tuple[Optional[str], float]:
    """Find nearest fuel entry or fuel storage. Returns (entry_id, dist_km)."""
    best_id: Optional[str] = None
    best_dist = float("inf")
    for i, fe in enumerate(state.fuel_entry_points):
        if fe.coordinate:
            d = _haversine_km(lat, lng, fe.coordinate.lat, fe.coordinate.lng)
            if d < best_dist:
                best_dist = d
                best_id = f"fuel_entry_{i}"
    return best_id, best_dist


# ── Ensure-at helpers ────────────────────────────────────────────


def _find_nearest_node_idx(
    state: GuiSystemState,
    lat: float, lng: float,
    centroids: dict[int, tuple[float, float]] | None = None,
) -> int:
    """Return the index of the nearest existing node (by bus centroid).

    Nodes are never created during geo-asset parsing — they are abstract
    network regions, not geographic elements.  If no node exists, returns 0
    (the caller should have ensured at least one node exists beforehand).
    """
    if not state.nodes:
        return 0
    nearest_idx, _ = _find_nearest_node(lat, lng, state.nodes, centroids)
    return nearest_idx if nearest_idx is not None else state.nodes[0].index


def _ensure_bus_at(
    state: GuiSystemState,
    lat: float, lng: float, name: str,
    snap_km: float, result: ParseResult,
    props: dict | None = None,
    centroids: dict[int, tuple[float, float]] | None = None,
    force_node: int | None = None,
) -> tuple[int, str]:
    """Find existing bus or create bus at nearest node.

    Returns (node_index, bus_id).
    Extracts voltage_kv, frequency_hz, current_type from *props* if available.

    When *force_node* is given, the bus is attached to that node regardless
    of geographic proximity.  Otherwise the nearest node (by bus centroid)
    is used.
    """
    # Extract requested voltage for voltage-aware snapping
    p = props or {}
    req_voltage = _prop_float(p, "voltage_kv", default=0.0)

    # 1. Check existing buses by proximity (voltage-aware)
    best_bus, bus_dist = _find_nearest_bus(
        lat, lng, state, _snap_km=snap_km, voltage_kv=req_voltage,
    )
    if best_bus is not None and bus_dist < snap_km:
        bus = state.buses[best_bus]
        # Respect force_node even when snapping to existing bus
        if force_node is not None and bus.parent_node != force_node:
            pass  # don't snap — need a bus on the forced node
        else:
            return bus.parent_node, best_bus

    # 2. Find the nearest existing node (or use forced node)
    node_idx = force_node if force_node is not None else _find_nearest_node_idx(state, lat, lng, centroids)

    # Check if this node already has a bus within snap distance (voltage-aware)
    for bid, bus in state.buses.items():
        if bus.parent_node == node_idx:
            # Skip buses with incompatible voltage
            if req_voltage > 0 and bus.voltage_kv > 0:
                ratio = bus.voltage_kv / req_voltage
                if ratio < 0.9 or ratio > 1.1:
                    continue
            d = _haversine_km(lat, lng, bus.latitude, bus.longitude)
            if d < snap_km:
                return node_idx, bid

    # 3. Create a bus on this node with properties from GeoJSON
    #    Compute lat/lng offset from the parent node so the bus appears
    #    at the correct geographic location on the map.
    bus_name = _prop_str(p, "bus_name", "station", "Station",
                         "substation", "Substation", default=name)
    bus_id = f"bus_{state._next_bus_id}"
    state._next_bus_id += 1
    # Frequency: use the source value when present, else infer from
    # geographic location (most of the world is 50 Hz; Americas /
    # Korea / Philippines / Taiwan / Saudi Arabia / east Japan are
    # 60 Hz). This avoids hard-coding 50 Hz on a Cuban network.
    src_freq = _prop_float(
        p, "frequency_hz", "frequency", "Frequency", default=0.0,
    )
    if src_freq <= 0:
        try:
            from esfex.visualization.workflows.grid_mapping_quality import (
                infer_frequency_hz,
            )
            src_freq = infer_frequency_hz(lat, lng)
        except Exception:
            src_freq = 50.0

    state.buses[bus_id] = GuiBus(
        bus_id=bus_id,
        name=bus_name,
        parent_node=node_idx,
        voltage_kv=_normalize_voltage_kv(
            _prop_float(p, "voltage_kv", "voltage", "Voltage",
                        "kV", "kv", default=220.0)),
        frequency_hz=src_freq,
        current_type=_prop_str(p, "current_type", default="AC"),
        latitude=lat,
        longitude=lng,
    )
    result.buses_added += 1
    return node_idx, bus_id


def _ensure_fuel_entry_at(
    state: GuiSystemState,
    lat: float, lng: float, name: str,
    snap_km: float, result: ParseResult,
    props: dict | None = None,
    centroids: dict[int, tuple[float, float]] | None = None,
    force_node: int | None = None,
) -> tuple[int, str]:
    """Find existing fuel entry or create fuel entry at nearest node.

    Returns (node_index, entry_id).
    Extracts fuels, max_import_rate, import_cost from *props* if available.

    When *force_node* is given, the entry is attached to that node.
    """
    # 1. Check existing fuel entries by proximity
    best_fe, fe_dist = _find_nearest_fuel_point(lat, lng, state)
    if best_fe is not None and fe_dist < snap_km:
        idx = int(best_fe.split("_")[-1])
        fe = state.fuel_entry_points[idx]
        if force_node is not None and fe.node != force_node:
            pass  # don't snap — need entry on forced node
        else:
            return fe.node, best_fe

    # 2. Find nearest existing node (or use forced node)
    node_idx = force_node if force_node is not None else _find_nearest_node_idx(state, lat, lng, centroids)

    # Check if this node already has a fuel entry within snap distance
    for i, fe in enumerate(state.fuel_entry_points):
        if fe.node == node_idx and fe.coordinate:
            d = _haversine_km(lat, lng, fe.coordinate.lat, fe.coordinate.lng)
            if d < snap_km:
                return node_idx, f"fuel_entry_{i}"

    p = props or {}
    fuels = _prop(p, "fuels", "fuel", "Fuel", "Fuels", default=[])
    if isinstance(fuels, str):
        fuels = [fuels] if fuels else []

    entry_id = f"fuel_entry_{len(state.fuel_entry_points)}"
    state.fuel_entry_points.append(GuiFuelEntryPoint(
        name=name,
        fuels=fuels,
        node=node_idx,
        coordinate=GeoPoint(lat, lng, name),
        max_import_rate=_prop_float(p, "max_import_rate", "import_rate",
                                    "capacity", default=0.0),
        import_cost=_prop_float(p, "import_cost", "cost", default=0.0),
    ))
    result.fuel_entries_added += 1
    return node_idx, entry_id


# ── Other helpers ────────────────────────────────────────────────


def _point_coords(coordinates: list) -> tuple[float, float]:
    """Extract (lat, lng) from GeoJSON Point coordinates [lng, lat]."""
    return coordinates[1], coordinates[0]


def _unique_unit_key(base_key: str, node: int, existing: dict) -> str:
    """Return a unit_key that doesn't collide with existing instances at the same node.

    The serializer groups instances by ``(unit_key, node)`` into per-node
    arrays.  If two instances share the same pair, the second overwrites
    the first.  This helper appends ``_1``, ``_2``, … when needed.
    """
    # Check if any existing instance already occupies (base_key, node)
    occupied = {
        inst.unit_key
        for inst in existing.values()
        if getattr(inst, "node", None) == node
    }
    if base_key not in occupied:
        return base_key
    i = 1
    while f"{base_key}_{i}" in occupied:
        i += 1
    return f"{base_key}_{i}"


def _make_instance_id(prefix: str, unit_key: str, node: int, existing: dict) -> str:
    """Generate a unique instance ID like 'gen_solar_0_0'."""
    base = f"{prefix}_{unit_key}_{node}"
    if base not in existing:
        return base
    i = 1
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


# ── Main entry point ─────────────────────────────────────────────


def apply_assignments(
    state: GuiSystemState,
    assignments: list[ParseAssignment],
    snap_threshold_km: float = 5.0,
) -> ParseResult:
    """Create system elements from geo asset parse assignments.

    Modifies *state* in place and returns a result summary.
    Properties are extracted from GeoJSON feature attributes before
    falling back to defaults.
    """
    result = ParseResult()

    # Pre-compute node centroids from existing buses (used for nearest-node lookup)
    centroids = _compute_node_centroids(state)

    # Process assignments: nodes first, then equipment, then lines, then zones
    point_assignments = [a for a in assignments if a.geometry_type == "Point"]
    line_assignments = [a for a in assignments
                        if a.geometry_type in ("LineString", "MultiLineString")]
    polygon_assignments = [a for a in assignments
                           if a.geometry_type in ("Polygon", "MultiPolygon")]

    # --- Pass 1: Points ---
    for a in point_assignments:
        try:
            lat, lng = _point_coords(a.coordinates)
            p = a.properties
            name = _feature_name(p, f"Parsed {a.target_type}")
            _fn = a.target_node  # user override (None = auto)

            if a.target_type == "generator":
                node_idx, bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} Bus",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                unit_key = _prop_str(p, "unit_key", "unit", "Unit",
                                     default="gen_parsed")
                # Ensure unit_key is unique per (unit_key, node) to avoid
                # overwrite when the serializer groups by unit_key + node.
                unit_key = _unique_unit_key(unit_key, node_idx, state.generators)
                _valid_gen_types = {"Renewable", "Non-renewable", "Storage", "Electrolyzer"}
                raw_type = _prop_str(p, "gen_type", "type", "Type",
                                     default="Non-renewable")
                gen_type = raw_type if raw_type in _valid_gen_types else "Non-renewable"
                fuel = _prop_str(p, "fuel", "Fuel", default="Natural Gas")
                inst_id = _make_instance_id("gen", unit_key, node_idx, state.generators)
                state.generators[inst_id] = GuiGeneratorInstance(
                    instance_id=inst_id,
                    unit_key=unit_key,
                    name=name,
                    gen_type=gen_type,
                    fuel=fuel,
                    bus=bus_id,
                    node=node_idx,
                    rated_power=_prop_float(p, "rated_power", "capacity_mw",
                                            "capacity", "MW"),
                    min_power=_prop_float(p, "min_power"),
                    life_time=_prop_int(p, "life_time", "lifetime", default=25),
                    initial_age=_prop_int(p, "initial_age", "age"),
                    fuel_cost=_prop_float(p, "fuel_cost"),
                    fixed_cost=_prop_float(p, "fixed_cost"),
                    maintenance_cost=_prop_float(p, "maintenance_cost"),
                    eff_at_rated=_prop_float(p, "eff_at_rated", "efficiency",
                                             default=0.35),
                    technology_id=_prop_str(p, "technology", "tech") or None,
                    availability_file=_prop_str(p, "availability_file",
                                                "availability") or None,
                    latitude=lat,
                    longitude=lng,
                )
                result.generators_added += 1

            elif a.target_type == "battery":
                node_idx, bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} Bus",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                unit_key = _prop_str(p, "unit_key", "unit", "Unit",
                                     default="bat_parsed")
                unit_key = _unique_unit_key(unit_key, node_idx, state.batteries)
                inst_id = _make_instance_id("bat", unit_key, node_idx, state.batteries)
                state.batteries[inst_id] = GuiBatteryInstance(
                    instance_id=inst_id,
                    unit_key=unit_key,
                    name=name,
                    bus=bus_id,
                    node=node_idx,
                    rated_power=_prop_float(p, "rated_power", "power_mw",
                                            "power", "MW"),
                    capacity=_prop_float(p, "capacity_mwh", "capacity",
                                         "energy_mwh", "MWh"),
                    efficiency_charge=_prop_float(p, "efficiency_charge",
                                                  "eff_charge", default=0.9),
                    efficiency_discharge=_prop_float(p, "efficiency_discharge",
                                                     "eff_discharge",
                                                     default=0.9),
                    life_time=_prop_int(p, "life_time", "lifetime", default=20),
                    initial_age=_prop_int(p, "initial_age", "age"),
                    latitude=lat,
                    longitude=lng,
                )
                result.batteries_added += 1

            elif a.target_type == "fuel_entry":
                node_idx = _fn if _fn is not None else _find_nearest_node_idx(state, lat, lng, centroids)
                fuels = _prop(p, "fuels", "fuel", "Fuel", "Fuels", default=[])
                if isinstance(fuels, str):
                    fuels = [fuels] if fuels else []
                state.fuel_entry_points.append(GuiFuelEntryPoint(
                    name=name,
                    fuels=fuels,
                    node=node_idx,
                    coordinate=GeoPoint(lat, lng, name),
                    max_import_rate=_prop_float(p, "max_import_rate",
                                                "import_rate", "capacity"),
                    import_cost=_prop_float(p, "import_cost", "cost"),
                ))
                result.fuel_entries_added += 1

            elif a.target_type == "electrolyzer":
                node_idx, bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} Bus",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                unit_key = _prop_str(p, "unit_key", "unit", "Unit",
                                     default="elz_parsed")
                unit_key = _unique_unit_key(unit_key, node_idx, state.electrolyzers)
                inst_id = _make_instance_id(
                    "elz", unit_key, node_idx, state.electrolyzers,
                )
                state.electrolyzers[inst_id] = GuiElectrolyzerInstance(
                    instance_id=inst_id,
                    unit_key=unit_key,
                    name=name,
                    bus=bus_id,
                    node=node_idx,
                    rated_power=_prop_float(p, "rated_power", "capacity_mw",
                                            "capacity", "MW"),
                    technology=_prop_str(p, "technology", "tech",
                                         default="PEM"),
                    latitude=lat,
                    longitude=lng,
                )
                result.electrolyzers_added += 1

            elif a.target_type == "bus":
                _ensure_bus_at(state, lat, lng, name,
                               snap_threshold_km, result, props=p,
                               centroids=centroids, force_node=_fn)

            elif a.target_type == "transformer":
                # Transformers connect two buses — ensure from/to buses exist
                from_bus_name = _prop_str(p, "from_bus", "from_station",
                                          "from", default=f"{name} - Primary")
                to_bus_name = _prop_str(p, "to_bus", "to_station",
                                        "to", default=f"{name} - Secondary")
                _, from_bus_id = _ensure_bus_at(
                    state, lat, lng, from_bus_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                _, to_bus_id = _ensure_bus_at(
                    state, lat, lng, to_bus_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                state.transformers.append(GuiTransformer(
                    name=name,
                    from_bus=from_bus_id,
                    to_bus=to_bus_id,
                    from_voltage_kv=_normalize_voltage_kv(
                        _prop_float(p, "from_voltage_kv",
                                    "primary_kv", "voltage_kv",
                                    "voltage", default=220.0)),
                    to_voltage_kv=_normalize_voltage_kv(
                        _prop_float(p, "to_voltage_kv",
                                    "secondary_kv", default=110.0)),
                    rated_power_mva=_prop_float(p, "rated_power_mva",
                                                "rated_mva", "capacity_mva",
                                                "MVA", default=100.0),
                    impedance_pu=_prop_float(p, "impedance_pu", "x_pu",
                                             default=0.1),
                    losses_fraction=_prop_float(p, "losses_fraction", "losses",
                                                default=0.005),
                    latitude=lat,
                    longitude=lng,
                ))
                result.transformers_added += 1

            elif a.target_type == "acdc_converter":
                _, from_bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} - AC",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                _, to_bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} - DC",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                state.acdc_converters.append(GuiACDCConverter(
                    name=name,
                    converter_type=_prop_str(p, "converter_type", "type",
                                             default="VSC"),
                    from_bus=from_bus_id,
                    to_bus=to_bus_id,
                    from_voltage_kv=_normalize_voltage_kv(
                        _prop_float(p, "from_voltage_kv",
                                    "ac_voltage_kv", "voltage_kv",
                                    default=220.0)),
                    dc_voltage_kv=_normalize_voltage_kv(
                        _prop_float(p, "dc_voltage_kv", "dc_kv",
                                    default=320.0)),
                    rated_power_mva=_prop_float(p, "rated_power_mva",
                                                "rated_mva", "capacity_mva",
                                                "MVA", default=100.0),
                    efficiency_rectify=_prop_float(p, "efficiency_rectify",
                                                    "eff_rectify", default=0.98),
                    efficiency_invert=_prop_float(p, "efficiency_invert",
                                                   "eff_invert", default=0.98),
                    latitude=lat,
                    longitude=lng,
                ))
                result.acdc_converters_added += 1

            elif a.target_type == "freq_converter":
                _, from_bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} - Side A",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                _, to_bus_id = _ensure_bus_at(
                    state, lat, lng, f"{name} - Side B",
                    snap_threshold_km, result, props=p,
                    centroids=centroids, force_node=_fn,
                )
                state.freq_converters.append(GuiFrequencyConverter(
                    name=name,
                    from_bus=from_bus_id,
                    to_bus=to_bus_id,
                    from_frequency_hz=_prop_float(p, "from_frequency_hz",
                                                   "from_freq", default=50.0),
                    to_frequency_hz=_prop_float(p, "to_frequency_hz",
                                                 "to_freq", default=60.0),
                    rated_power_mva=_prop_float(p, "rated_power_mva",
                                                "rated_mva", "capacity_mva",
                                                "MVA", default=100.0),
                    efficiency_a_to_b=_prop_float(p, "efficiency_a_to_b",
                                                    "eff_a_to_b", default=0.98),
                    efficiency_b_to_a=_prop_float(p, "efficiency_b_to_a",
                                                    "eff_b_to_a", default=0.98),
                    latitude=lat,
                    longitude=lng,
                ))
                result.freq_converters_added += 1

            elif a.target_type == "fuel_storage":
                node_idx = _fn if _fn is not None else _find_nearest_node_idx(state, lat, lng, centroids)
                fuel = _prop_str(p, "fuel", "Fuel", default="Natural Gas")
                idx = len(state.fuel_storages)
                storage_id = f"fuel_storage_{idx}"
                while storage_id in state.fuel_storages:
                    idx += 1
                    storage_id = f"fuel_storage_{idx}"
                cap = _prop_float(p, "capacity", "storage_capacity",
                                  default=0.0)
                init_lvl = _prop_float(p, "initial_level", "level",
                                       default=0.5)
                min_lvl = _prop_float(p, "min_level", default=0.1)
                fuel_params = {}
                if fuel:
                    fuel_params[fuel] = FuelStorageParams(
                        capacity=cap,
                        initial_level=init_lvl,
                        min_level=min_lvl,
                    )
                state.fuel_storages[storage_id] = GuiFuelStorage(
                    storage_id=storage_id,
                    name=name,
                    fuels=[fuel] if fuel else [],
                    fuel_params=fuel_params,
                    node=node_idx,
                    latitude=lat,
                    longitude=lng,
                )
                result.fuel_storages_added += 1

        except Exception as exc:
            result.warnings.append(f"Point '{a.properties}': {exc}")

    # --- Pass 2: LineStrings ---
    for a in line_assignments:
        try:
            coords = a.coordinates
            p = a.properties
            name = _feature_name(p, f"Parsed {a.target_type}")

            # For MultiLineString, use first line segment
            if a.geometry_type == "MultiLineString" and coords:
                coords = coords[0]

            if len(coords) < 2:
                result.warnings.append(f"Line '{name}': fewer than 2 points, skipped")
                continue

            # GeoJSON is [lng, lat]
            lat1, lng1 = coords[0][1], coords[0][0]
            lat2, lng2 = coords[-1][1], coords[-1][0]

            # Intermediate waypoints
            waypoints = []
            if len(coords) > 2:
                for c in coords[1:-1]:
                    waypoints.append(GeoPoint(c[1], c[0]))

            if a.target_type == "line":
                # Transmission lines connect bus-to-bus
                from_name = _prop_str(p, "from_name", "from_station",
                                      "from", default=f"{name} - Start")
                to_name = _prop_str(p, "to_name", "to_station",
                                    "to", default=f"{name} - End")
                from_idx, from_bus = _ensure_bus_at(
                    state, lat1, lng1, from_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids,
                )
                to_idx, to_bus = _ensure_bus_at(
                    state, lat2, lng2, to_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids,
                )
                if from_bus == to_bus:
                    result.warnings.append(
                        f"Line '{name}': endpoints snap to same bus ({from_bus}), skipped"
                    )
                    continue

                capacity = _prop_float(p, "capacity_mw", "capacity", "MW",
                                       "rating", default=100.0)
                voltage = _normalize_voltage_kv(
                    _prop_float(p, "voltage_kv", "voltage", "kV",
                                default=0.0))
                lid = f"line_{state._next_line_id}"
                state._next_line_id += 1
                state.transmission_lines.append(GuiTransmissionLine(
                    line_id=lid,
                    from_bus=from_bus,
                    to_bus=to_bus,
                    from_node=from_idx,
                    to_node=to_idx,
                    capacity_mw=capacity,
                    voltage_kv=voltage if voltage > 0 else None,
                    line_type=_prop_str(p, "line_type", "type") or None,
                    waypoints=waypoints,
                    from_endpoint=EndpointRef("bus", from_bus),
                    to_endpoint=EndpointRef("bus", to_bus),
                    length_km=_prop_float(p, "length_km", "length",
                                          default=0.0) or None,
                    reactance_pu=_prop_float(p, "reactance_pu", "x_pu",
                                             default=0.0) or None,
                    resistance_pu=_prop_float(p, "resistance_pu", "r_pu",
                                              default=0.0) or None,
                    num_circuits=_prop_int(p, "num_circuits", "circuits",
                                           default=1),
                    current_type=_prop_str(p, "current_type", default="AC"),
                ))
                result.lines_added += 1

            elif a.target_type == "fuel_route":
                # Fuel routes connect fuel_entry-to-fuel_entry
                from_name = _prop_str(p, "from_name", "from",
                                      default=f"{name} - Start")
                to_name = _prop_str(p, "to_name", "to",
                                    default=f"{name} - End")
                from_idx, from_entry = _ensure_fuel_entry_at(
                    state, lat1, lng1, from_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids,
                )
                to_idx, to_entry = _ensure_fuel_entry_at(
                    state, lat2, lng2, to_name,
                    snap_threshold_km, result, props=p,
                    centroids=centroids,
                )
                if from_entry == to_entry:
                    result.warnings.append(
                        f"Route '{name}': endpoints snap to same fuel entry ({from_entry}), skipped"
                    )
                    continue

                capacity = _prop_float(p, "capacity", "transport_capacity",
                                       default=0.0)
                fuels = _prop(p, "fuels", "fuel", "Fuel", "Fuels", default=[])
                if isinstance(fuels, str):
                    fuels = [fuels] if fuels else []

                rid = f"fuel_route_{state._next_fuel_route_id}"
                state._next_fuel_route_id += 1

                # Calculate route length
                length_km = _prop_float(p, "length_km", "length", default=0.0)
                if not length_km:
                    all_pts = ([(lat1, lng1)]
                               + [(wp.lat, wp.lng) for wp in waypoints]
                               + [(lat2, lng2)])
                    for i in range(len(all_pts) - 1):
                        length_km += _haversine_km(
                            all_pts[i][0], all_pts[i][1],
                            all_pts[i + 1][0], all_pts[i + 1][1],
                        )

                # EndpointRef for fuel_entry expects the list index as
                # string (e.g. "0"), not the full "fuel_entry_0" id.
                from_entry_idx = from_entry.split("_")[-1]
                to_entry_idx = to_entry.split("_")[-1]

                state.fuel_transport_routes.append(GuiFuelTransportRoute(
                    route_id=rid,
                    fuels=fuels,
                    from_node=from_idx,
                    to_node=to_idx,
                    capacity=capacity,
                    transport_cost=_prop_float(p, "transport_cost", "cost"),
                    losses_fraction=_prop_float(p, "losses_fraction", "losses"),
                    length_km=length_km if length_km > 0 else None,
                    waypoints=waypoints,
                    from_endpoint=EndpointRef("fuel_entry", from_entry_idx),
                    to_endpoint=EndpointRef("fuel_entry", to_entry_idx),
                ))
                result.fuel_routes_added += 1

        except Exception as exc:
            result.warnings.append(f"Line feature: {exc}")

    # --- Pass 3: Polygons ---
    for a in polygon_assignments:
        try:
            p = a.properties
            name = _feature_name(p, f"Zone {len(state.development_zones)}")
            technology = _prop_str(p, "technology", "tech", "Technology",
                                   default="Solar")
            max_cap = _prop(p, "max_capacity_mw", "max_capacity", "capacity")

            rings = a.coordinates
            # For MultiPolygon, use first polygon
            if a.geometry_type == "MultiPolygon" and rings:
                rings = rings[0]

            if not rings or not rings[0] if isinstance(rings[0], list) and isinstance(rings[0][0], list) else not rings:
                result.warnings.append(f"Polygon '{name}': no coordinates, skipped")
                continue

            # Outer ring
            outer = rings[0] if isinstance(rings[0][0], list) else rings
            polygon = [GeoPoint(c[1], c[0]) for c in outer]

            state.development_zones.append(GuiDevelopmentZone(
                name=name,
                technology=technology,
                polygon=polygon,
                max_capacity_mw=float(max_cap) if max_cap is not None else None,
                style=VisualStyle(
                    color=_prop_str(p, "color", "Color") or None,
                    opacity=_prop_float(p, "opacity", "Opacity", default=0.15),
                ),
            ))
            result.zones_added += 1

        except Exception as exc:
            result.warnings.append(f"Polygon feature: {exc}")

    return result
