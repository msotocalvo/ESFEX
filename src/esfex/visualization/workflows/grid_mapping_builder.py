"""Build GUI network elements from normalized grid features.

Takes a list of :class:`GridFeature` (produced by the fetchers after
deduplication) and creates buses, generators, batteries, transmission
lines, transformers, and converters in the active
:class:`GuiSystemState`.

Reuses helpers from :mod:`esfex.visualization.data.geo_asset_parser`.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiModel, GuiSystemState

from esfex.visualization.data.geo_asset_parser import (
    ParseResult,
    _compute_node_centroids,
    _ensure_bus_at,
    _find_nearest_bus,
    _find_nearest_node_idx,
    _haversine_km,
    _make_instance_id,
    _normalize_voltage_kv,
    _unique_unit_key,
)
from esfex.visualization.data.gui_model import (
    EndpointRef,
    GeoPoint,
    GuiACDCConverter,
    GuiBatteryInstance,
    GuiBus,
    GuiFuel,
    GuiGeneratorInstance,
    GuiTechnology,
    GuiTransformer,
    GuiTransmissionLine,
    VisualStyle,
)
from esfex.visualization.workflows.grid_mapping_fetchers import GridFeature
from esfex.visualization.workflows.grid_mapping_quality import (
    estimate_battery_efficiencies,
    estimate_generator_defaults,
    estimate_line_pu_params,
    estimate_transformer_impedance_pu,
    estimate_transformer_losses_fraction,
)

logger = logging.getLogger(__name__)


# ── Fuel / technology normalization and defaults ─────────────────────

# Maps lowercased, whitespace-stripped fuel name variants to a canonical
# key used for deduplication and default-property lookup.
_FUEL_ALIASES: dict[str, str] = {
    "solar": "sun", "solarpv": "sun", "pv": "sun", "photovoltaic": "sun",
    "sun": "sun",
    "wind": "wind", "eolica": "wind", "eolico": "wind",
    "hydro": "water", "hydroelectric": "water", "hydropower": "water",
    "water": "water",
    "naturalgas": "naturalgas", "gas": "naturalgas", "ng": "naturalgas",
    "gasnatural": "naturalgas",
    # ── Liquid fossil fuels ──
    # "diesel" = reciprocating engines (small/fast).
    # "fuel_oil" = steam-cycle plants burning HFO/heavy oil (large, slow,
    # behaves like coal in commitment terms). Splitting them lets the
    # defaults table give very different min_up / ramp values.
    "diesel": "diesel", "gasoil": "diesel", "dieselengine": "diesel",
    "oil": "fuel_oil", "fueloil": "fuel_oil", "hfo": "fuel_oil",
    "heavyfueloil": "fuel_oil", "residualfueloil": "fuel_oil",
    "petroleum": "fuel_oil", "crude": "fuel_oil", "bunker": "fuel_oil",
    "mazut": "fuel_oil", "steamoil": "fuel_oil",
    "coal": "coal", "carbon": "coal", "petcoke": "coal",
    "nuclear": "nuclear", "uranium": "nuclear",
    "biomass": "biomass", "biomasa": "biomass",
    "biogas": "biogas",
    "waste": "waste", "residuos": "waste",
    "geothermal": "geothermal", "geotermica": "geothermal",
    "otec": "otec",
    "other": "other", "otro": "other", "otros": "other",
    "none": "none",
}


def _normalize_fuel_key(name: str) -> str:
    """Reduce any fuel name variant to a canonical key for deduplication."""
    s = name.lower().strip().replace("_", "").replace("-", "").replace(" ", "")
    return _FUEL_ALIASES.get(s, s)


# Canonical fuel key → GuiFuel constructor kwargs.
# Renewables use unit=None / energy_content=None (always present, zero cost).
_FUEL_DEFAULTS: dict[str, dict] = {
    "sun":        {"fuel_id": "Sun",         "name": "Sun",         "unit": None,   "emission_factor": 0.0,   "energy_content": None,    "price_base": 0.0},
    "wind":       {"fuel_id": "Wind",        "name": "Wind",        "unit": None,   "emission_factor": 0.0,   "energy_content": None,    "price_base": 0.0},
    "water":      {"fuel_id": "Water",       "name": "Water",       "unit": None,   "emission_factor": 0.0,   "energy_content": None,    "price_base": 0.0},
    "geothermal": {"fuel_id": "Geothermal",  "name": "Geothermal",  "unit": None,   "emission_factor": 0.0,   "energy_content": None,    "price_base": 0.0},
    "otec":       {"fuel_id": "OTEC",        "name": "OTEC",        "unit": None,   "emission_factor": 0.0,   "energy_content": None,    "price_base": 0.0},
    "naturalgas": {"fuel_id": "Natural_gas", "name": "Natural Gas", "unit": "MMBTU","emission_factor": 0.202, "energy_content": 0.293,   "price_base": 4.0},
    "coal":       {"fuel_id": "Coal",        "name": "Coal",        "unit": "kTon", "emission_factor": 0.341, "energy_content": 8.14,    "price_base": 60.0},
    "diesel":     {"fuel_id": "Diesel",      "name": "Diesel",      "unit": "kTon", "emission_factor": 0.267, "energy_content": 11.63,   "price_base": 600.0},
    "fuel_oil":   {"fuel_id": "Fuel_oil",    "name": "Fuel Oil",    "unit": "kTon", "emission_factor": 0.279, "energy_content": 11.30,   "price_base": 450.0},
    "nuclear":    {"fuel_id": "Nuclear",     "name": "Nuclear",     "unit": "kgU",  "emission_factor": 0.0,   "energy_content": 45000.0, "price_base": 0.006},
    "biomass":    {"fuel_id": "Biomass",     "name": "Biomass",     "unit": "kTon", "emission_factor": 0.0,   "energy_content": 4.5,     "price_base": 40.0},
    "biogas":     {"fuel_id": "Biogas",      "name": "Biogas",      "unit": "Nm3",  "emission_factor": 0.0,   "energy_content": 0.006,   "price_base": 0.3},
    "waste":      {"fuel_id": "Waste",       "name": "Waste",       "unit": "kTon", "emission_factor": 0.33,  "energy_content": 3.0,     "price_base": -20.0},
    "other":      {"fuel_id": "Other",       "name": "Other",       "unit": "MWh",  "emission_factor": 0.5,   "energy_content": 1.0,     "price_base": 50.0},
}

# Canonical fuel key → GuiTechnology constructor kwargs.
# invest_cost / invest_max_power left at 0 (user must configure manually).
_TECH_DEFAULTS: dict[str, dict] = {
    "sun":        {"name": "Solar PV",          "category": "Renewable",     "eff_at_rated": 1.0,  "eff_at_min": 1.0,  "life_time": 25},
    "wind":       {"name": "Wind Turbine",      "category": "Renewable",     "eff_at_rated": 1.0,  "eff_at_min": 1.0,  "life_time": 25},
    "water":      {"name": "Hydroelectric",     "category": "Renewable",     "eff_at_rated": 0.90, "eff_at_min": 0.85, "life_time": 50},
    "geothermal": {"name": "Geothermal",        "category": "Renewable",     "eff_at_rated": 0.90, "eff_at_min": 0.85, "life_time": 30},
    "naturalgas": {"name": "Gas Turbine",       "category": "Non-renewable", "eff_at_rated": 0.45, "eff_at_min": 0.30, "life_time": 30},
    "coal":       {"name": "Coal Plant",        "category": "Non-renewable", "eff_at_rated": 0.38, "eff_at_min": 0.28, "life_time": 40},
    "diesel":     {"name": "Diesel Generator",  "category": "Non-renewable", "eff_at_rated": 0.40, "eff_at_min": 0.25, "life_time": 25},
    "fuel_oil":   {"name": "Fuel-Oil Steam",    "category": "Non-renewable", "eff_at_rated": 0.34, "eff_at_min": 0.28, "life_time": 35},
    "nuclear":    {"name": "Nuclear Plant",     "category": "Non-renewable", "eff_at_rated": 0.33, "eff_at_min": 0.33, "life_time": 60},
    "biomass":    {"name": "Biomass Plant",     "category": "Non-renewable", "eff_at_rated": 0.30, "eff_at_min": 0.20, "life_time": 25},
    "biogas":     {"name": "Biogas Generator",  "category": "Non-renewable", "eff_at_rated": 0.35, "eff_at_min": 0.25, "life_time": 20},
    "waste":      {"name": "Waste-to-Energy",   "category": "Non-renewable", "eff_at_rated": 0.25, "eff_at_min": 0.18, "life_time": 25},
    "other":      {"name": "Other Generator",   "category": "Non-renewable", "eff_at_rated": 0.35, "eff_at_min": 0.25, "life_time": 25},
}


def _find_existing_fuel(state: GuiSystemState, canonical_key: str) -> str | None:
    """Find an existing fuel whose normalized name matches *canonical_key*."""
    for fid, fuel in state.fuels.items():
        if _normalize_fuel_key(fid) == canonical_key:
            return fid
        if _normalize_fuel_key(fuel.name) == canonical_key:
            return fid
    return None


def _find_existing_technology(
    state: GuiSystemState, fuel_id: str, canonical_key: str,
) -> str | None:
    """Find an existing technology that matches the given fuel.

    Search priority:
    1. Exact ``tech.fuel == fuel_id``
    2. Normalized ``tech.fuel`` matches *canonical_key*
    """
    for tid, tech in state.technologies.items():
        if tech.fuel == fuel_id:
            return tid
    for tid, tech in state.technologies.items():
        if tech.fuel and _normalize_fuel_key(tech.fuel) == canonical_key:
            return tid
    return None


def _create_fuels_and_technologies(
    model: GuiModel,
    generators: list[GridFeature],
    result: ParseResult,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Auto-create fuels and technologies for each unique fuel found.

    Fuel-first approach with consistency guarantee: every technology
    created will reference an existing fuel, and every fuel found in
    generators will have a corresponding technology.

    Uses ``model.add_fuel()`` / ``model.add_technology()`` so the proper
    Qt signals are emitted and the element tree updates automatically.

    Returns ``(fuel_remap, tech_remap)`` where:
    - ``fuel_remap[fetcher_fuel]`` = canonical ``fuel_id`` to use
    - ``tech_remap[fetcher_fuel]`` = ``tech_id`` (or None)
    """
    state = model.state
    fuel_remap: dict[str, str] = {}
    tech_remap: dict[str, str | None] = {}

    unique_fuels = {g.fuel for g in generators if g.fuel and g.fuel != "None"}

    for raw_fuel in sorted(unique_fuels):
        canonical = _normalize_fuel_key(raw_fuel)
        if canonical == "none":
            continue

        # ── Step 1: Ensure fuel exists ────────────────────────────
        fuel_id = _find_existing_fuel(state, canonical)
        if fuel_id:
            logger.debug("Fuel '%s' → existing '%s'", raw_fuel, fuel_id)
        elif canonical in _FUEL_DEFAULTS:
            d = _FUEL_DEFAULTS[canonical]
            fuel_id = d["fuel_id"]
            model.add_fuel(
                fuel_id, d["name"],
                unit=d.get("unit"),
                emission_factor=d.get("emission_factor", 0.0),
                energy_content=d.get("energy_content"),
                price_base=d.get("price_base", 0.0),
            )
            result.fuels_created += 1
            logger.info("Created fuel '%s' from '%s'", fuel_id, raw_fuel)
        else:
            fuel_id = raw_fuel.replace(" ", "_")
            model.add_fuel(fuel_id, raw_fuel)
            result.fuels_created += 1
            logger.info("Created generic fuel '%s' from '%s'", fuel_id, raw_fuel)
        fuel_remap[raw_fuel] = fuel_id

        # ── Step 2: Ensure technology exists (referencing fuel_id) ─
        tech_id = _find_existing_technology(state, fuel_id, canonical)
        if tech_id:
            # Ensure the existing technology's fuel reference is consistent
            tech = state.technologies[tech_id]
            if tech.fuel != fuel_id:
                model.update_technology(tech_id, fuel=fuel_id)
                logger.info(
                    "Updated tech '%s' fuel: '%s' → '%s'",
                    tech_id, tech.fuel, fuel_id,
                )
            logger.debug("Tech for '%s' → existing '%s'", raw_fuel, tech_id)
        elif canonical in _TECH_DEFAULTS:
            tdef = _TECH_DEFAULTS[canonical]
            tech_id = model.add_technology(
                name=tdef["name"],
                category=tdef["category"],
                fuel=fuel_id,
                life_time=tdef.get("life_time", 25),
                eff_at_rated=tdef.get("eff_at_rated", 0.35),
                eff_at_min=tdef.get("eff_at_min", 0.25),
            )
            result.technologies_created += 1
            logger.info("Created technology '%s' (%s)", tech_id, tdef["name"])
        else:
            tech_id = None
        tech_remap[raw_fuel] = tech_id

    return fuel_remap, tech_remap


# ── Public entry point ───────────────────────────────────────────────


def build_grid_from_features(
    model: GuiModel,
    features: list[GridFeature],
    bus_strategy: str = "per_voltage",
    snap_threshold_km: float = 5.0,
    target_node: int | None = None,
) -> ParseResult:
    """Create GUI elements from fetched grid features.

    Parameters
    ----------
    model : GuiModel
        The active GUI model (mutations will be signalled).
    features : list[GridFeature]
        Normalized, de-duplicated features from the fetchers.
    bus_strategy : str
        ``"per_voltage"`` — create one bus per voltage level at each
        substation (with auto-transformers between levels).
        ``"per_substation"`` — one bus at the highest voltage.
    snap_threshold_km : float
        Distance threshold for snapping new elements to existing buses.
    target_node : int | None
        Force all elements onto this node (``None`` = auto-nearest).

    Returns
    -------
    ParseResult
        Summary counts of created elements.
    """
    state = model.state
    result = ParseResult()
    centroids = _compute_node_centroids(state)

    # Only include features the user toggled on
    active = [f for f in features if f.include]

    # Separate by type
    substations = [f for f in active if f.feature_type == "substation"]
    generators = [f for f in active if f.feature_type == "generator"]
    batteries = [f for f in active if f.feature_type == "battery"]
    lines = [f for f in active if f.feature_type == "line"]
    transformers = [f for f in active if f.feature_type == "transformer"]
    converters = [f for f in active if f.feature_type == "converter"]
    fuel_entries = [f for f in active if f.feature_type == "fuel_entry"]
    fuel_storages = [f for f in active if f.feature_type == "fuel_storage"]

    # ── Phase 1: Substations → Buses ──────────────────────────────
    # Maps osm_id → list of bus_ids created for that substation
    substation_buses: dict[str, list[str]] = {}

    for sub in substations:
        try:
            buses_created = _create_buses_from_substation(
                state, sub, bus_strategy, snap_threshold_km,
                result, centroids, target_node,
            )
            if sub.osm_id:
                substation_buses[sub.osm_id] = buses_created
        except Exception as exc:
            result.warnings.append(f"Substation '{sub.name}': {exc}")

    # ── Phase 1.5: Auto-create fuels & technologies ────────────────
    fuel_remap, tech_remap = _create_fuels_and_technologies(
        model, generators, result,
    )

    # ── Phase 2: Generators → GuiGeneratorInstance ─────────────────

    for gen in generators:
        try:
            _create_generator(
                state, gen, snap_threshold_km,
                result, centroids, target_node,
                fuel_remap, tech_remap,
            )
        except Exception as exc:
            result.warnings.append(f"Generator '{gen.name}': {exc}")

    # ── Phase 3: Batteries → GuiBatteryInstance ────────────────────

    for bat in batteries:
        try:
            _create_battery(
                state, bat, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Battery '{bat.name}': {exc}")

    # ── Phase 4: Lines → GuiTransmissionLine ──────────────────────

    for line in lines:
        try:
            _create_line(
                state, line, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Line '{line.name}': {exc}")

    # ── Phase 5: Transformers → GuiTransformer ─────────────────────

    for tr in transformers:
        try:
            _create_transformer(
                state, tr, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Transformer '{tr.name}': {exc}")

    # ── Phase 6: Converters → GuiACDCConverter ─────────────────────

    for conv in converters:
        try:
            _create_converter(
                state, conv, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Converter '{conv.name}': {exc}")

    # ── Phase 7: Fuel Entries → GuiFuelEntryPoint ─────────────────

    for fe in fuel_entries:
        try:
            _create_fuel_entry(
                model, fe, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Fuel Entry '{fe.name}': {exc}")

    # ── Phase 8: Fuel Storage → GuiFuelStorage ────────────────────

    for fs in fuel_storages:
        try:
            _create_fuel_storage(
                model, fs, snap_threshold_km,
                result, centroids, target_node,
            )
        except Exception as exc:
            result.warnings.append(f"Fuel Storage '{fs.name}': {exc}")

    # ── Phase 9: Fuel/tech catalog + supply consistency ───────────
    # Backstop: even if a generator was created with a fuel that didn't
    # go through _create_fuels_and_technologies (e.g. a manual edit),
    # ensure every gen.fuel exists in the catalog and is supplied.
    try:
        from esfex.visualization.workflows.grid_mapping_quality import (
            repair_bus_roles_and_demand,
            repair_fuel_consistency,
            repair_node_internal_coupling,
        )
        fc = repair_fuel_consistency(model.state)
        result.fuels_created += fc.get("fuels_added", 0)
        result.technologies_created += fc.get("techs_added", 0)

        # Phase 10: Infer bus roles + redistribute demand_fraction.
        # Without this, every bus is "load" with full demand fraction,
        # producing physically nonsensical bus-balance constraints in
        # the operational LP (HV junctions forced to serve load they
        # have no path to).
        br = repair_bus_roles_and_demand(model.state)
        if br.get("buses_role_changed"):
            result.warnings.append(
                f"Bus roles inferred: {br['buses_role_changed']} buses re-assigned"
                f" (load → connection / mixed) across "
                f"{br.get('nodes_redistributed', 0)} nodes"
            )

        # Phase 11: Guarantee short electrical coupling demand → supply
        # inside each node.  OSM imports leave some demand buses 10+
        # zig-zag hops from generation (no direct step-up), which makes
        # the DC-OPF keep angles at 0 and shed at VOLL despite ample
        # in-node generation.  Insert a direct transformer/line where the
        # path is too long.  Runs AFTER role/demand repair so supply and
        # demand buses are correctly identified.
        nc = repair_node_internal_coupling(model.state)
        if nc.get("buses_coupled"):
            result.transformers_added += nc.get("transformers_added", 0)
            result.warnings.append(
                f"Node star-coupling: {nc['buses_coupled']} bus(es) tied to "
                f"their node hub across {nc.get('nodes_restructured',0)} node(s) "
                f"(+{nc.get('transformers_added',0)} TR, "
                f"+{nc.get('lines_added',0)} line) — were >2 hops from the hub"
            )
    except Exception as exc:
        result.warnings.append(f"Fuel/bus consistency repair: {exc}")

    return result


# ── Phase 1: Substation → Buses ──────────────────────────────────────


def _create_buses_from_substation(
    state: GuiSystemState,
    sub: GridFeature,
    bus_strategy: str,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> list[str]:
    """Create bus(es) from a substation feature. Returns list of bus_ids."""
    bus_ids: list[str] = []

    if bus_strategy == "per_voltage" and sub.voltage_kv_secondary > 0:
        # Multi-voltage substation: create one bus per voltage + transformer
        v_high = sub.voltage_kv
        v_low = sub.voltage_kv_secondary

        # High-voltage bus
        props_hv = {
            "voltage_kv": v_high,
            "frequency_hz": sub.frequency_hz,
            "bus_name": f"{sub.name} {v_high:.0f}kV",
        }
        node_idx, bus_hv = _ensure_bus_at(
            state, sub.latitude, sub.longitude,
            f"{sub.name} {v_high:.0f}kV",
            snap_km, result, props=props_hv,
            centroids=centroids, force_node=force_node,
        )
        bus_ids.append(bus_hv)

        # Low-voltage bus (slightly offset for visual distinction)
        offset = 0.0005  # ~55 m
        props_lv = {
            "voltage_kv": v_low,
            "frequency_hz": sub.frequency_hz,
            "bus_name": f"{sub.name} {v_low:.0f}kV",
        }
        node_idx_lv, bus_lv = _ensure_bus_at(
            state, sub.latitude + offset, sub.longitude + offset,
            f"{sub.name} {v_low:.0f}kV",
            snap_km * 0.5, result, props=props_lv,
            centroids=centroids, force_node=force_node,
        )
        bus_ids.append(bus_lv)

        if bus_hv == bus_lv:
            # Snap collapsed both voltage levels onto a single bus —
            # don't create a transformer to itself.
            return bus_ids

        # Auto-create transformer between the two buses
        auto_mva = 100.0
        ratio = (v_high / v_low) if v_low > 0 else 2.0
        state.transformers.append(GuiTransformer(
            name=f"{sub.name} TR {v_high:.0f}/{v_low:.0f}kV",
            from_bus=bus_hv,
            to_bus=bus_lv,
            from_node=node_idx,
            to_node=node_idx_lv,
            from_voltage_kv=v_high,
            to_voltage_kv=v_low,
            rated_power_mva=auto_mva,
            impedance_pu=estimate_transformer_impedance_pu(auto_mva, ratio),
            losses_fraction=estimate_transformer_losses_fraction(auto_mva),
            latitude=sub.latitude + offset * 0.5,
            longitude=sub.longitude + offset * 0.5,
        ))
        result.transformers_added += 1

    else:
        # Single bus (per_substation or single-voltage)
        v = sub.voltage_kv if sub.voltage_kv > 0 else 220.0
        props = {
            "voltage_kv": v,
            "frequency_hz": sub.frequency_hz,
            "bus_name": sub.name or f"Substation Bus",
        }
        _, bus_id = _ensure_bus_at(
            state, sub.latitude, sub.longitude,
            sub.name or "Substation",
            snap_km, result, props=props,
            centroids=centroids, force_node=force_node,
        )
        bus_ids.append(bus_id)

    return bus_ids


# ── Phase 2: Generator ───────────────────────────────────────────────


def _ensure_generator_stepup(
    state: GuiSystemState,
    gen: GridFeature,
    node_idx: int,
    gen_bus_id: str,
    result: ParseResult,
) -> None:
    """Ensure a generator step-up (GSU) transformer to the node's HV backbone.

    A real power plant connects at generator/MV voltage and steps up to the
    transmission backbone through ONE dedicated transformer.  Without it the
    generator is electrically stranded on whatever bus it snapped to, with
    only a long zig-zag path (or none) to the transmission lines that carry
    inter-node power — the operational DC-OPF then cannot dispatch the plant
    and the node sheds at VOLL despite having generation.

    This finds the node's highest-voltage bus nearest the plant (the
    transmission backbone connection point) and, if the generator snapped to
    a strictly lower-voltage bus with no transformer already bridging the
    two, creates the missing GSU transformer (1 hop).
    """
    gen_bus = state.buses.get(gen_bus_id)
    if gen_bus is None:
        return
    gen_v = gen_bus.voltage_kv or 0.0

    # Highest-voltage bus in this node, nearest to the plant.
    hv_bus_id = None
    hv_v = gen_v
    hv_d = float("inf")
    for bid, bus in state.buses.items():
        if bus.parent_node != node_idx or bid == gen_bus_id:
            continue
        bv = bus.voltage_kv or 0.0
        if bv <= gen_v:
            continue  # not a step-up target
        d = _haversine_km(gen.latitude, gen.longitude,
                           bus.latitude, bus.longitude)
        # Prefer the highest voltage; break ties by proximity.
        if bv > hv_v or (bv == hv_v and d < hv_d):
            hv_v, hv_d, hv_bus_id = bv, d, bid

    if hv_bus_id is None:
        return  # generator already on the node's top voltage level

    # Skip if a transformer already bridges these two buses.
    for tr in state.transformers:
        if {tr.from_bus, tr.to_bus} == {gen_bus_id, hv_bus_id}:
            return

    mva = max(float(gen.capacity_mw or 0.0), 100.0)
    v_lo = gen_v if gen_v > 0 else 34.5
    v_hi = hv_v
    ratio = (v_hi / v_lo) if v_lo > 0 else 2.0
    state.transformers.append(GuiTransformer(
        name=f"{gen.name} GSU {v_hi:.0f}/{v_lo:.0f}kV",
        from_bus=hv_bus_id,
        to_bus=gen_bus_id,
        from_node=node_idx,
        to_node=node_idx,
        from_voltage_kv=v_hi,
        to_voltage_kv=v_lo,
        rated_power_mva=mva,
        impedance_pu=estimate_transformer_impedance_pu(mva, ratio),
        losses_fraction=estimate_transformer_losses_fraction(mva),
        latitude=gen.latitude,
        longitude=gen.longitude,
    ))
    result.transformers_added += 1


def _create_generator(
    state: GuiSystemState,
    gen: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
    fuel_remap: dict[str, str] | None = None,
    tech_remap: dict[str, str | None] | None = None,
) -> None:
    """Create a generator instance from a GridFeature."""
    node_idx, bus_id = _ensure_bus_at(
        state, gen.latitude, gen.longitude,
        f"{gen.name} Bus",
        snap_km, result,
        centroids=centroids, force_node=force_node,
    )
    # NOTE: the generator step-up to the transmission backbone is created
    # in Phase 11 (`repair_generator_backbone_connectivity`), NOT here —
    # Phase 2 runs before lines (Phase 4) exist, so the true backbone
    # (inter-node line endpoints) is unknown at this point.

    # Map fuel to canonical fuel_id and resolve technology
    mapped_fuel = (fuel_remap or {}).get(gen.fuel, gen.fuel) or "Other"
    mapped_tech = (tech_remap or {}).get(gen.fuel)

    # Determine unit_key from mapped fuel
    fuel_key = mapped_fuel.lower().replace(" ", "_") if mapped_fuel else "gen_mapped"
    unit_key = _unique_unit_key(fuel_key, node_idx, state.generators)
    inst_id = _make_instance_id("gen", unit_key, node_idx, state.generators)

    # Compute initial age from commissioning year if available
    initial_age = 0
    if gen.commissioning_year:
        initial_age = max(0, datetime.date.today().year - gen.commissioning_year)

    # Pull in operating defaults by canonical fuel; never override
    # explicit values from the source.
    #   - min_power: scaled to MW from rated × min_power_frac.
    #   - ramp_up / ramp_down: kept as fraction/hour (Julia semantics).
    #   - start_up_cost: scaled to absolute $ per cold start.
    gen_defaults = estimate_generator_defaults(
        _normalize_fuel_key(mapped_fuel)
    )
    rp = float(gen.capacity_mw or 0)
    min_power_mw = rp * gen_defaults.get("min_power_frac", 0.0)
    start_up_cost = rp * gen_defaults.get("start_up_cost_per_mw", 0.0)

    state.generators[inst_id] = GuiGeneratorInstance(
        instance_id=inst_id,
        unit_key=unit_key,
        name=gen.name,
        gen_type=gen.gen_type or "Non-renewable",
        fuel=mapped_fuel,
        technology_id=mapped_tech,
        bus=bus_id,
        node=node_idx,
        rated_power=rp,
        initial_age=initial_age,
        latitude=gen.latitude,
        longitude=gen.longitude,
        eff_at_rated=gen_defaults.get("eff_at_rated", 0.35),
        eff_at_min=gen_defaults.get("eff_at_min", 0.25),
        min_power=min_power_mw,
        ramp_up=gen_defaults.get("ramp_up_frac", 0.0),
        ramp_down=gen_defaults.get("ramp_down_frac", 0.0),
        min_up=int(gen_defaults.get("min_up_h", 0)),
        min_down=int(gen_defaults.get("min_down_h", 0)),
        start_up_cost=start_up_cost,
        inertia=gen_defaults.get("inertia_s", 0.0),
        life_time=int(gen_defaults.get("life_time_yr", 25)),
        degradation_rate=gen_defaults.get("degradation_rate", 0.0),
    )
    result.generators_added += 1


# ── Phase 3: Battery ─────────────────────────────────────────────────


def _create_battery(
    state: GuiSystemState,
    bat: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create a battery instance from a GridFeature."""
    node_idx, bus_id = _ensure_bus_at(
        state, bat.latitude, bat.longitude,
        f"{bat.name} Bus",
        snap_km, result,
        centroids=centroids, force_node=force_node,
    )

    unit_key = _unique_unit_key("bat_mapped", node_idx, state.batteries)
    inst_id = _make_instance_id("bat", unit_key, node_idx, state.batteries)

    # Use explicit energy capacity if available, else default 4h duration
    energy = bat.energy_mwh if bat.energy_mwh > 0 else bat.capacity_mw * 4

    eff_chg, eff_dis = estimate_battery_efficiencies(bat.fuel)

    state.batteries[inst_id] = GuiBatteryInstance(
        instance_id=inst_id,
        unit_key=unit_key,
        name=bat.name,
        bus=bus_id,
        node=node_idx,
        rated_power=bat.capacity_mw,
        capacity=energy,
        efficiency_charge=eff_chg,
        efficiency_discharge=eff_dis,
        eff_at_rated=eff_dis,
        eff_at_min=eff_dis,
        latitude=bat.latitude,
        longitude=bat.longitude,
    )
    result.batteries_added += 1


# ── Phase 4: Line ────────────────────────────────────────────────────


def _create_line(
    state: GuiSystemState,
    line: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create a transmission line from a GridFeature."""
    if not line.line_coords or len(line.line_coords) < 2:
        result.warnings.append(f"Line '{line.name}': no geometry, skipped")
        return

    lat1, lng1 = line.line_coords[0]
    lat2, lng2 = line.line_coords[-1]

    # Find/create buses at endpoints
    from_idx, from_bus = _ensure_bus_at(
        state, lat1, lng1, f"{line.name} Start",
        snap_km, result,
        centroids=centroids, force_node=force_node,
    )
    to_idx, to_bus = _ensure_bus_at(
        state, lat2, lng2, f"{line.name} End",
        snap_km, result,
        centroids=centroids, force_node=force_node,
    )

    if from_bus == to_bus:
        result.warnings.append(
            f"Line '{line.name}': endpoints snap to same bus ({from_bus}), skipped"
        )
        return

    # Intermediate waypoints (skip first and last, those are endpoints)
    waypoints = []
    if len(line.line_coords) > 2:
        for lat, lng in line.line_coords[1:-1]:
            waypoints.append(GeoPoint(lat, lng))

    lid = f"line_{state._next_line_id}"
    state._next_line_id += 1

    v = line.voltage_kv if line.voltage_kv > 0 else None

    # Compute effective capacity: explicit > SIL estimate, scaled by num_circuits
    if line.capacity_mw > 0:
        per_circuit = line.capacity_mw
    else:
        per_circuit = _estimate_line_capacity(line.voltage_kv)
    effective_cap = per_circuit * max(1, line.num_circuits)

    # Compute geometric length and physical impedance/susceptance.
    # ``length_km`` left None for cases without an explicit voltage —
    # the model will fall back to system defaults.
    length_km: float | None = None
    if v and v > 0:
        coords = line.line_coords
        length_km = sum(
            _haversine_km(coords[i][0], coords[i][1],
                          coords[i + 1][0], coords[i + 1][1])
            for i in range(len(coords) - 1)
        )
        r_pu, x_pu, b_pu = estimate_line_pu_params(v, length_km)
        # Per-circuit values in parallel: divide series by N, multiply
        # shunt by N (standard parallel-line composition).
        n = max(1, line.num_circuits)
        r_pu /= n
        x_pu /= n
        b_pu *= n
    else:
        r_pu = x_pu = b_pu = None  # type: ignore[assignment]

    state.transmission_lines.append(GuiTransmissionLine(
        line_id=lid,
        from_bus=from_bus,
        to_bus=to_bus,
        from_node=from_idx,
        to_node=to_idx,
        capacity_mw=effective_cap,
        voltage_kv=v,
        waypoints=waypoints,
        from_endpoint=EndpointRef("bus", from_bus),
        to_endpoint=EndpointRef("bus", to_bus),
        num_circuits=line.num_circuits,
        frequency_hz=line.frequency_hz,
        current_type=line.current_type,
        length_km=length_km,
        resistance_pu=r_pu,
        reactance_pu=x_pu,
        susceptance_pu=b_pu,
    ))
    result.lines_added += 1


def _estimate_line_capacity(voltage_kv: float) -> float:
    """Rough capacity estimate from voltage level (MW).

    Uses typical SIL (surge impedance loading) approximations.
    """
    if voltage_kv >= 500:
        return 2000.0
    if voltage_kv >= 345:
        return 1000.0
    if voltage_kv >= 220:
        return 500.0
    if voltage_kv >= 110:
        return 200.0
    if voltage_kv >= 33:
        return 50.0
    return 10.0


# ── Phase 5: Transformer ────────────────────────────────────────────


def _create_transformer(
    state: GuiSystemState,
    tr: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create a transformer from a GridFeature."""
    v_high = tr.voltage_kv if tr.voltage_kv > 0 else 220.0
    v_low = tr.voltage_kv_secondary if tr.voltage_kv_secondary > 0 else 110.0

    # HV side bus
    props_hv = {"voltage_kv": v_high}
    node_idx, from_bus = _ensure_bus_at(
        state, tr.latitude, tr.longitude,
        f"{tr.name} HV",
        snap_km, result, props=props_hv,
        centroids=centroids, force_node=force_node,
    )

    # LV side bus (slight offset)
    offset = 0.0003
    props_lv = {"voltage_kv": v_low}
    _, to_bus = _ensure_bus_at(
        state, tr.latitude + offset, tr.longitude + offset,
        f"{tr.name} LV",
        snap_km * 0.5, result, props=props_lv,
        centroids=centroids, force_node=force_node,
    )

    if from_bus == to_bus:
        # Both sides snapped to the same existing bus (typically when
        # the source feature has no usable voltage info so HV and LV
        # default to 220/110 but the snap matches an existing nearby
        # bus on either side). A transformer with both terminals on
        # the same bus is electrically meaningless.
        result.warnings.append(
            f"Transformer '{tr.name}': both sides snapped to "
            f"{from_bus}, skipped (self-loop)"
        )
        return

    cap = tr.capacity_mw if tr.capacity_mw > 0 else 100.0
    ratio = (v_high / v_low) if v_low > 0 else 2.0
    z_pu = estimate_transformer_impedance_pu(cap, ratio)
    losses = estimate_transformer_losses_fraction(cap)

    state.transformers.append(GuiTransformer(
        name=tr.name,
        from_bus=from_bus,
        to_bus=to_bus,
        from_voltage_kv=v_high,
        to_voltage_kv=v_low,
        rated_power_mva=cap,
        impedance_pu=z_pu,
        losses_fraction=losses,
        latitude=tr.latitude,
        longitude=tr.longitude,
    ))
    result.transformers_added += 1


# ── Phase 6: Converter ──────────────────────────────────────────────


def _create_converter(
    state: GuiSystemState,
    conv: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create an AC/DC converter from a GridFeature."""
    v = conv.voltage_kv if conv.voltage_kv > 0 else 220.0

    # AC side bus
    props_ac = {"voltage_kv": v, "current_type": "AC"}
    node_idx, ac_bus = _ensure_bus_at(
        state, conv.latitude, conv.longitude,
        f"{conv.name} AC",
        snap_km, result, props=props_ac,
        centroids=centroids, force_node=force_node,
    )

    # DC side bus (slight offset)
    offset = 0.0003
    props_dc = {"voltage_kv": v, "current_type": "DC"}
    _, dc_bus = _ensure_bus_at(
        state, conv.latitude + offset, conv.longitude + offset,
        f"{conv.name} DC",
        snap_km * 0.5, result, props=props_dc,
        centroids=centroids, force_node=force_node,
    )

    if ac_bus == dc_bus:
        result.warnings.append(
            f"AC/DC converter '{conv.name}': both sides snapped to "
            f"{ac_bus}, skipped (self-loop)"
        )
        return

    cap = conv.capacity_mw if conv.capacity_mw > 0 else 100.0

    state.acdc_converters.append(GuiACDCConverter(
        name=conv.name,
        from_bus=ac_bus,
        to_bus=dc_bus,
        from_voltage_kv=v,
        dc_voltage_kv=v,
        rated_power_mva=cap,
        latitude=conv.latitude,
        longitude=conv.longitude,
    ))
    result.acdc_converters_added += 1


# ── Phase 7: Fuel Entry ────────────────────────────────────────────


def _create_fuel_entry(
    model: GuiModel,
    fe: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create a fuel entry point from a GridFeature."""
    state = model.state
    node_idx = _find_nearest_node_idx(
        state, fe.latitude, fe.longitude, centroids,
    ) if force_node is None else force_node

    # Map fuel name using normalization
    fuel = fe.fuel or ""
    canonical = _normalize_fuel_key(fuel) if fuel else ""
    if canonical and canonical != "none":
        existing = _find_existing_fuel(state, canonical)
        if existing:
            fuel = existing
        elif canonical in _FUEL_DEFAULTS:
            fuel = _FUEL_DEFAULTS[canonical]["fuel_id"]

    fuels = [fuel] if fuel else []
    model.add_fuel_entry(
        name=fe.name, fuels=fuels,
        node=node_idx, lat=fe.latitude, lng=fe.longitude,
    )
    result.fuel_entries_added += 1


# ── Phase 8: Fuel Storage ──────────────────────────────────────────


def _create_fuel_storage(
    model: GuiModel,
    fs: GridFeature,
    snap_km: float,
    result: ParseResult,
    centroids: dict,
    force_node: int | None,
) -> None:
    """Create a fuel storage from a GridFeature."""
    state = model.state
    node_idx = _find_nearest_node_idx(
        state, fs.latitude, fs.longitude, centroids,
    ) if force_node is None else force_node

    # Map fuel name using normalization
    fuel = fs.fuel or ""
    canonical = _normalize_fuel_key(fuel) if fuel else ""
    if canonical and canonical != "none":
        existing = _find_existing_fuel(state, canonical)
        if existing:
            fuel = existing
        elif canonical in _FUEL_DEFAULTS:
            fuel = _FUEL_DEFAULTS[canonical]["fuel_id"]

    model.add_fuel_storage(
        name=fs.name, fuel=fuel,
        node=node_idx, latitude=fs.latitude, longitude=fs.longitude,
    )
    result.fuel_storages_added += 1
