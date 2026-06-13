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
    estimate_line_capacity_mw,
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

# Technology catalog keyed by the powerplantmatching-style technology label.
# Each entry binds a distinct generation technology to its canonical fuel and
# realistic round-trip efficiencies, so the builder no longer collapses every
# generator of a fuel into one technology (e.g. CCGT vs OCGT, steam turbine vs
# reciprocating engine, run-of-river vs reservoir).
#   tech_key -> {name, category, fuel(canonical key), eff_at_rated, eff_at_min, life_time}
_TECH_CATALOG: dict[str, dict] = {
    "PV":            {"name": "Solar PV",                "category": "Renewable",     "fuel": "sun",        "eff_at_rated": 1.00, "eff_at_min": 1.00, "life_time": 25},
    "Onshore Wind":  {"name": "Onshore Wind",            "category": "Renewable",     "fuel": "wind",       "eff_at_rated": 1.00, "eff_at_min": 1.00, "life_time": 25},
    "Offshore Wind": {"name": "Offshore Wind",           "category": "Renewable",     "fuel": "wind",       "eff_at_rated": 1.00, "eff_at_min": 1.00, "life_time": 27},
    "Run-Of-River":  {"name": "Run-of-River Hydro",      "category": "Renewable",     "fuel": "water",      "eff_at_rated": 0.90, "eff_at_min": 0.85, "life_time": 60},
    "Reservoir":     {"name": "Reservoir Hydro",         "category": "Renewable",     "fuel": "water",      "eff_at_rated": 0.90, "eff_at_min": 0.85, "life_time": 60},
    "Pumped Storage":{"name": "Pumped-Storage Hydro",    "category": "Renewable",     "fuel": "water",      "eff_at_rated": 0.80, "eff_at_min": 0.75, "life_time": 60},
    "Geothermal":    {"name": "Geothermal",              "category": "Renewable",     "fuel": "geothermal", "eff_at_rated": 0.90, "eff_at_min": 0.85, "life_time": 30},
    "CCGT":          {"name": "CCGT",                    "category": "Non-renewable", "fuel": "naturalgas", "eff_at_rated": 0.58, "eff_at_min": 0.45, "life_time": 30},
    "OCGT":          {"name": "OCGT",                    "category": "Non-renewable", "fuel": "naturalgas", "eff_at_rated": 0.40, "eff_at_min": 0.28, "life_time": 25},
    "Coal ST":       {"name": "Coal Steam Turbine",      "category": "Non-renewable", "fuel": "coal",       "eff_at_rated": 0.40, "eff_at_min": 0.30, "life_time": 40},
    "Oil ST":        {"name": "Fuel-Oil Steam Turbine",  "category": "Non-renewable", "fuel": "fuel_oil",   "eff_at_rated": 0.35, "eff_at_min": 0.28, "life_time": 35},
    "Combustion Engine": {"name": "Diesel Engine",       "category": "Non-renewable", "fuel": "diesel",     "eff_at_rated": 0.42, "eff_at_min": 0.30, "life_time": 25},
    "Nuclear":       {"name": "Nuclear",                 "category": "Non-renewable", "fuel": "nuclear",    "eff_at_rated": 0.33, "eff_at_min": 0.33, "life_time": 60},
    "Biomass ST":    {"name": "Biomass Steam Turbine",   "category": "Non-renewable", "fuel": "biomass",    "eff_at_rated": 0.30, "eff_at_min": 0.20, "life_time": 25},
    "Biogas Engine": {"name": "Biogas Engine",           "category": "Non-renewable", "fuel": "biogas",     "eff_at_rated": 0.40, "eff_at_min": 0.30, "life_time": 20},
    "Waste-to-Energy": {"name": "Waste-to-Energy",       "category": "Non-renewable", "fuel": "waste",      "eff_at_rated": 0.25, "eff_at_min": 0.18, "life_time": 25},
    "Other":         {"name": "Other Generator",         "category": "Non-renewable", "fuel": "other",      "eff_at_rated": 0.35, "eff_at_min": 0.25, "life_time": 25},
}

# Capacity (MW) below which an unlabelled gas plant is assumed to be an open-
# cycle peaker rather than a combined-cycle plant (powerplantmatching heuristic).
_OCGT_MAX_MW = 100.0

# Tight cap (km) for snapping a transmission-line endpoint to a bus. A real
# endpoint sits on the substation it terminates at or on a shared junction
# node, so a small tolerance captures the true topology; the old 5 km Bus snap
# over-reached and collapsed whole short lines onto a single bus (dropped as
# self-loops). Applied only after merge + split have captured the real
# connections, so tightening removes the over-reach without fragmenting. (#16)
_LINE_ENDPOINT_SNAP_CAP_KM = 0.5

# Faithful mode: line endpoints only share a bus when they are the SAME OSM
# node (coincident within this tiny tolerance, ~OSM coordinate precision).
# Everything else becomes its own bus and is merged later by clustering.
_FAITHFUL_JUNCTION_SNAP_KM = 0.05

# Faithful mode: buses within this distance and at the same voltage are the
# same physical station and are clustered into one. A "same station" radius,
# not a reach distance — exposed in the GUI with this sensible default so the
# user can widen it if large substations still fragment, or tighten it if two
# distinct stations merge.
_FAITHFUL_STATION_CLUSTER_KM = 1.0


def classify_technology(
    canonical_fuel: str,
    gen_type: str = "",
    tech_hint: str = "",
    capacity_mw: float = 0.0,
) -> str:
    """Map a generator to a powerplantmatching-style technology label.

    Uses the canonical fuel plus any technology hint string (from GEM's
    Technology column / OSM generator:method) and a capacity heuristic to
    distinguish technologies that share a fuel (CCGT vs OCGT, steam turbine vs
    reciprocating engine, hydro sub-types). Always returns a key present in
    ``_TECH_CATALOG``.
    """
    t = (tech_hint or "").lower()
    if canonical_fuel == "sun":
        return "PV"
    if canonical_fuel == "wind":
        return "Offshore Wind" if "offshore" in t else "Onshore Wind"
    if canonical_fuel == "water":
        if "pump" in t:
            return "Pumped Storage"
        if "run" in t or "ror" in t:
            return "Run-Of-River"
        return "Reservoir"
    if canonical_fuel == "naturalgas":
        if "ocgt" in t or "open" in t or "peak" in t:
            return "OCGT"
        if "ccgt" in t or "combined" in t:
            return "CCGT"
        return "OCGT" if (0 < capacity_mw < _OCGT_MAX_MW) else "CCGT"
    if canonical_fuel == "coal":
        return "Coal ST"
    if canonical_fuel == "fuel_oil":
        return "Oil ST"
    if canonical_fuel == "diesel":
        # Large/steam-tagged oil units are steam turbines, not engines.
        if "steam" in t or "boiler" in t:
            return "Oil ST"
        return "Combustion Engine"
    if canonical_fuel == "nuclear":
        return "Nuclear"
    if canonical_fuel == "biomass":
        return "Biomass ST"
    if canonical_fuel == "biogas":
        return "Biogas Engine"
    if canonical_fuel == "waste":
        return "Waste-to-Energy"
    if canonical_fuel == "geothermal":
        return "Geothermal"
    return "Other"


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


def _find_tech_by_name(state: GuiSystemState, name: str) -> str | None:
    """Find an existing technology by its display name."""
    for tid, tech in state.technologies.items():
        if tech.name == name:
            return tid
    return None


def _gen_raw_fuel(g) -> str:
    """The generator's fuel, routing undetected fuels to the 'Other' bucket."""
    return g.fuel if (g.fuel and g.fuel not in ("None", "none")) else "Other"


def _create_fuels_and_technologies(
    model: GuiModel,
    generators: list[GridFeature],
    result: ParseResult,
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Auto-create fuels and per-technology generation technologies.

    Each generator is classified into a powerplantmatching-style technology
    (CCGT/OCGT, coal/oil steam turbine, combustion engine, hydro sub-types, …)
    via :func:`classify_technology`, so generators sharing a fuel still map to
    distinct technologies with realistic efficiencies — instead of collapsing
    every generator of a fuel into one technology.

    Returns ``(fuel_remap, tech_remap)`` where:
    - ``fuel_remap[raw_fuel]`` = canonical ``fuel_id``
    - ``tech_remap[(tech_key, fuel_id)]`` = ``tech_id``

    Every generator is guaranteed both a fuel and a technology.
    """
    state = model.state
    fuel_remap: dict[str, str] = {}
    tech_remap: dict[tuple[str, str], str] = {}

    # ── Pass 1: ensure a fuel exists for every raw fuel ──────────────
    for raw_fuel in sorted({_gen_raw_fuel(g) for g in generators}):
        canonical = _normalize_fuel_key(raw_fuel)
        fuel_id = _find_existing_fuel(state, canonical)
        if fuel_id:
            logger.debug("Fuel '%s' → existing '%s'", raw_fuel, fuel_id)
        elif canonical in _FUEL_DEFAULTS:
            d = _FUEL_DEFAULTS[canonical]
            fuel_id = d["fuel_id"]
            model.add_fuel(
                fuel_id, d["name"], unit=d.get("unit"),
                emission_factor=d.get("emission_factor", 0.0),
                energy_content=d.get("energy_content"),
                price_base=d.get("price_base", 0.0),
            )
            result.fuels_created += 1
            logger.info("Created fuel '%s' from '%s'", fuel_id, raw_fuel)
        else:
            # Unknown fuel: keep its name but give it the catalog "Other"
            # numeric defaults so it is solvable (non-zero energy/price).
            fuel_id = raw_fuel.replace(" ", "_")
            od = _FUEL_DEFAULTS["other"]
            model.add_fuel(
                fuel_id, raw_fuel, unit=od["unit"],
                emission_factor=od["emission_factor"],
                energy_content=od["energy_content"],
                price_base=od["price_base"],
            )
            result.fuels_created += 1
            logger.info("Created generic fuel '%s' from '%s'", fuel_id, raw_fuel)
        fuel_remap[raw_fuel] = fuel_id

    # ── Pass 2: ensure a technology exists per (tech_key, fuel) ───────
    for g in generators:
        raw_fuel = _gen_raw_fuel(g)
        fuel_id = fuel_remap[raw_fuel]
        canonical = _normalize_fuel_key(raw_fuel)
        tech_key = classify_technology(
            canonical, g.gen_type, g.technology, g.capacity_mw)
        key = (tech_key, fuel_id)
        if key in tech_remap:
            continue

        spec = _TECH_CATALOG.get(tech_key, _TECH_CATALOG["Other"])
        # A generic "Other" tech on an unknown fuel should name and reference
        # that fuel, not the catalog 'Other' fuel.
        if tech_key == "Other" and canonical not in _FUEL_DEFAULTS:
            fuel_name = (state.fuels[fuel_id].name
                         if fuel_id in state.fuels else fuel_id)
            tech_name = f"{fuel_name} Generator"
        else:
            tech_name = spec["name"]

        tech_id = _find_tech_by_name(state, tech_name)
        if tech_id:
            if state.technologies[tech_id].fuel != fuel_id:
                model.update_technology(tech_id, fuel=fuel_id)
        else:
            tech_id = model.add_technology(
                name=tech_name, category=spec["category"], fuel=fuel_id,
                life_time=spec.get("life_time", 25),
                eff_at_rated=spec.get("eff_at_rated", 0.35),
                eff_at_min=spec.get("eff_at_min", 0.25),
            )
            result.technologies_created += 1
            logger.info("Created technology '%s' (%s/%s)",
                        tech_id, tech_key, fuel_id)
        tech_remap[key] = tech_id

    return fuel_remap, tech_remap


# ── Public entry point ───────────────────────────────────────────────


def build_grid_from_features(
    model: GuiModel,
    features: list[GridFeature],
    bus_strategy: str = "per_voltage",
    snap_threshold_km: float = 5.0,
    target_node: int | None = None,
    faithful: bool = False,
    station_radius_km: float = _FAITHFUL_STATION_CLUSTER_KM,
    min_capacity_mw: float = 0.0,
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

    # Enforce the "Min gen capacity" filter as a firm contract on the built
    # network. The fetch-time filter keeps generators of unknown capacity
    # (capacity_mw == 0, common in OSM); those would otherwise enter the model
    # at 0 MW and violate the minimum. When the minimum is > 0, drop every
    # generator that cannot be shown to meet it (including unknown = 0 MW).
    # A minimum of 0 means "include all" (the documented behaviour).
    if min_capacity_mw > 0:
        n_before = len(generators)
        generators = [
            g for g in generators if float(g.capacity_mw or 0.0) >= min_capacity_mw
        ]
        n_dropped = n_before - len(generators)
        if n_dropped:
            result.warnings.append(
                f"Min gen capacity: dropped {n_dropped} generator(s) below "
                f"{min_capacity_mw:g} MW (including unknown-capacity units)."
            )
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
    # Capture real topology first (#16): split any line where a substation
    # bus sits ON it (an OSM way passing through a station without a node
    # there). This connects mid-line substations from the real geometry, so
    # the network no longer fragments into pieces the auto-connect would have
    # to bridge with fabricated straight lines.
    try:
        from esfex.visualization.workflows.grid_mapping_topology import (
            merge_contiguous_line_segments,
            split_lines_at_substations,
        )
        # 1) Merge OSM's fragmented segments into whole lines, so short
        #    segments don't collapse onto a single bus (→ self-loop → dropped),
        #    which was silently deleting real connectivity.
        n_segments = len(lines)
        lines = merge_contiguous_line_segments(lines)
        if len(lines) != n_segments:
            result.warnings.append(
                f"Topology: merged {n_segments} segments → "
                f"{len(lines)} contiguous lines."
            )
        # 2) Split the merged lines where a substation sits on them.
        sub_buses = [
            (bid, b.latitude, b.longitude, b.voltage_kv)
            for bid, b in state.buses.items()
        ]
        n_before_split = len(lines)
        lines = split_lines_at_substations(lines, sub_buses)
        if len(lines) != n_before_split:
            result.warnings.append(
                f"Topology: split overpassing lines "
                f"({n_before_split} → {len(lines)} segments)."
            )
    except Exception as exc:
        logger.warning("Line topology pre-processing skipped (non-fatal): %s", exc)

    # Line endpoints snap with a TIGHT tolerance, independent of the general
    # substation Bus snap. With merge (whole lines) + split (substations on
    # lines) already done, the real connections are captured, so a line
    # endpoint only connects to a bus it genuinely sits on (a substation
    # termination or an exact junction) — otherwise it becomes its own bus.
    # The previous 5 km snap collapsed any line spanning <5 km near a single
    # bus onto that bus (from_bus == to_bus → dropped), silently deleting real
    # lines. (#16)
    # Faithful mode: a tiny snap so each line endpoint becomes its own bus
    # (only EXACTLY-coincident OSM junction nodes share); a later clustering
    # pass merges the ones that are the same physical station. This removes the
    # magic "reach" distance entirely — connectivity comes from coincidence,
    # not from how far a line is allowed to reach for a bus.
    line_snap_km = (
        _FAITHFUL_JUNCTION_SNAP_KM if faithful
        else min(snap_threshold_km, _LINE_ENDPOINT_SNAP_CAP_KM)
    )
    for line in lines:
        try:
            _create_line(
                state, line, line_snap_km,
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

        # Faithful mode (#16): STOP here. The following passes fabricate
        # connectivity to make the network electrically solvable — they
        # re-route buses, tie buses to a per-node "hub" (a star-coupling that
        # can span the whole node, i.e. hundreds of km, ignoring any distance
        # limit), and add reserves. That is the opposite of representing the
        # real OSM grid. In faithful mode the built network is exactly the OSM
        # topology (substations + real line traces); turning it into a solvable
        # model is a separate, explicit step the user opts into later.
        if not faithful:
            # Phase 9b: Tie co-located voltage levels with an auto-transformer.
            # Voltage-aware line endpoints (#18) can leave a substation with a
            # separate bus per voltage level; connect adjacent levels so a bus
            # never sits next to a different voltage without a transformer.
            vt = _connect_colocated_voltage_levels(
                model.state, result, snap_threshold_km,
            )
            if vt:
                result.warnings.append(
                    f"Voltage consistency: inserted {vt} auto-transformer(s) "
                    f"between co-located voltage levels at substations"
                )

            # Phase 10: Infer bus roles + redistribute demand_fraction.
            # Without this, every bus is "load" with full demand fraction,
            # producing physically nonsensical bus-balance constraints in
            # the operational LP (HV junctions forced to serve load they
            # have no path to).
            br = repair_bus_roles_and_demand(model.state)
            if br.get("buses_role_changed"):
                result.warnings.append(
                    f"Bus roles inferred: {br['buses_role_changed']} buses "
                    f"re-assigned (load → connection / mixed) across "
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
                    f"their node hub across {nc.get('nodes_restructured',0)} "
                    f"node(s) (+{nc.get('transformers_added',0)} TR, "
                    f"+{nc.get('lines_added',0)} line) — were >2 hops from hub"
                )
    except Exception as exc:
        result.warnings.append(f"Fuel/bus consistency repair: {exc}")

    # ── Phase 12: Node operating defaults (reserves + losses) ─────
    # Fill the operational fields the build leaves at 0 so the produced
    # network is complete and behaves with a security margin. Skipped in
    # faithful mode (that is a solvability concern, not real OSM topology).
    if not faithful:
        try:
            from esfex.visualization.workflows.grid_mapping_quality import (
                apply_node_operational_defaults,
            )
            nd = apply_node_operational_defaults(model.state)
            if nd.get("reserves") or nd.get("losses"):
                result.warnings.append(
                    f"Node defaults: reserves set on {nd.get('reserves', 0)} "
                    f"node(s), losses on {nd.get('losses', 0)} node(s)"
                )
        except Exception as exc:
            result.warnings.append(f"Node operational defaults: {exc}")

    # ── Faithful topology finalize: cluster same-station buses ────────────
    # Merge buses that are the same physical node (coincident junctions, a line
    # endpoint sitting on a substation, duplicate substation buses) so the real
    # topology is connected — without ever snapping a line to a far bus.
    if faithful:
        try:
            from esfex.visualization.workflows.grid_mapping_topology import (
                cluster_nearby_buses,
            )
            cl = cluster_nearby_buses(
                model.state, tol_m=station_radius_km * 1000.0)
            if cl.get("merged"):
                result.warnings.append(
                    f"Topology: clustered {cl['merged']} bus(es) into their "
                    f"station, removed {cl.get('selfloops_dropped', 0)} "
                    f"internal self-loop line(s)."
                )
        except Exception as exc:
            result.warnings.append(f"Bus clustering: {exc}")

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

    # Map fuel to canonical fuel_id and resolve the classified technology.
    # A generator with no detected fuel is routed through the catalog "Other"
    # entry (created in _create_fuels_and_technologies) so it gets a real fuel
    # AND technology instead of a dangling reference with technology_id=None.
    raw_fuel = _gen_raw_fuel(gen)
    mapped_fuel = (fuel_remap or {}).get(raw_fuel, raw_fuel) or "Other"
    tech_key = classify_technology(
        _normalize_fuel_key(raw_fuel), gen.gen_type, gen.technology,
        gen.capacity_mw)
    mapped_tech = (tech_remap or {}).get((tech_key, mapped_fuel))
    if mapped_tech is None:
        # Last-resort: reuse any technology already bound to the mapped fuel.
        mapped_tech = _find_existing_technology(
            state, mapped_fuel, _normalize_fuel_key(mapped_fuel))

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
    #   - min_power: kept as fraction of rated (schema + Julia expect
    #     a fraction; multiplying by rated produces MW absolute, which
    #     Julia then re-multiplies by rated giving an impossible floor
    #     and forcing gen_status=0 in UC mode → silent 70% load shed).
    #   - ramp_up / ramp_down: kept as fraction/hour (Julia semantics).
    #   - start_up_cost: scaled to absolute $ per cold start (schema
    #     expects $/node).
    gen_defaults = estimate_generator_defaults(
        _normalize_fuel_key(mapped_fuel)
    )
    rp = float(gen.capacity_mw or 0)
    min_power_frac = gen_defaults.get("min_power_frac", 0.0)
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
        min_power=min_power_frac,
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

    # The line's voltage. A bus has a SINGLE voltage, so a line must terminate
    # on a bus of its OWN voltage (different voltages require a transformer).
    # OSM frequently omits the line voltage; a line still has one, so for a
    # voltage-less line infer ONE voltage from its endpoints — the higher of the
    # two nearest substation voltages (the lower end steps down through a
    # transformer) — and snap BOTH ends to it. This applies the same-voltage
    # rule to voltage-less lines too, instead of letting them span e.g. 110 kV
    # to 220 kV (which used to attach a line straight across both levels). (#18)
    v_line = float(line.voltage_kv) if (line.voltage_kv and line.voltage_kv > 0) else 0.0
    if v_line <= 0:
        from esfex.visualization.data.geo_asset_parser import _find_nearest_bus
        ends: list[float] = []
        for la, lo in ((lat1, lng1), (lat2, lng2)):
            nb, nd = _find_nearest_bus(la, lo, state, _snap_km=snap_km, voltage_kv=0.0)
            if nb is not None and nd < snap_km and (state.buses[nb].voltage_kv or 0) > 0:
                ends.append(float(state.buses[nb].voltage_kv))
        if ends:
            v_line = max(ends)

    line_props: dict = {
        "frequency_hz": line.frequency_hz,
        "current_type": line.current_type,
    }
    if v_line > 0:
        line_props["voltage_kv"] = v_line
    from_idx, from_bus = _ensure_bus_at(
        state, lat1, lng1, f"{line.name} Start",
        snap_km, result, props=line_props,
        centroids=centroids, force_node=force_node,
    )
    to_idx, to_bus = _ensure_bus_at(
        state, lat2, lng2, f"{line.name} End",
        snap_km, result, props=line_props,
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

    v = v_line if v_line > 0 else None

    # Effective capacity: explicit OSM rating if present, else the thermal
    # rating of the nearest standard line type (consistent with the impedance
    # derived from the same type, with the N-1 security derate applied).
    if line.capacity_mw > 0:
        effective_cap = line.capacity_mw * max(1, line.num_circuits)
    else:
        effective_cap = estimate_line_capacity_mw(
            v_line, line.num_circuits)

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


def _connect_colocated_voltage_levels(
    state: GuiSystemState, result: ParseResult, radius_km: float,
) -> int:
    """Tie co-located different-voltage buses with an auto-transformer.

    Voltage-aware line endpoints (#18) can leave a substation with a separate
    bus per voltage level — correct, but electrically disconnected. For each
    cluster of buses that sit at the same place (same node, within
    ``radius_km``), connect *adjacent* voltage levels (sorted high→low) with a
    transformer, unless one already bridges those two levels. This avoids both
    a full mesh and duplicates. Non-faithful mode only.
    """
    if not state.buses:
        return 0

    by_node: dict[int, list[str]] = {}
    for bid, b in state.buses.items():
        if (b.voltage_kv or 0) > 0:
            by_node.setdefault(int(b.parent_node), []).append(bid)

    added = 0
    for node, bids in by_node.items():
        # Union-find: cluster buses that are co-located (≤ radius_km apart).
        parent = {bid: bid for bid in bids}

        def _find(x, parent=parent):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(bids)):
            ba = state.buses[bids[i]]
            for j in range(i + 1, len(bids)):
                bb = state.buses[bids[j]]
                if _haversine_km(ba.latitude, ba.longitude,
                                 bb.latitude, bb.longitude) <= radius_km:
                    parent[_find(bids[i])] = _find(bids[j])

        clusters: dict[str, list[str]] = {}
        for bid in bids:
            clusters.setdefault(_find(bid), []).append(bid)

        for members in clusters.values():
            # One representative bus per distinct (normalized) voltage.
            by_v: dict[float, str] = {}
            for bid in members:
                v = _normalize_voltage_kv(state.buses[bid].voltage_kv)
                by_v.setdefault(v, bid)
            if len(by_v) < 2:
                continue
            mset = set(members)
            # Voltage-level pairs already bridged by a transformer in-cluster.
            bridged: set[frozenset] = set()
            for tr in state.transformers:
                if tr.from_bus in mset and tr.to_bus in mset:
                    bridged.add(frozenset((
                        _normalize_voltage_kv(state.buses[tr.from_bus].voltage_kv),
                        _normalize_voltage_kv(state.buses[tr.to_bus].voltage_kv),
                    )))
            volts = sorted(by_v.keys(), reverse=True)
            for k in range(len(volts) - 1):
                v_hi, v_lo = volts[k], volts[k + 1]
                if frozenset((v_hi, v_lo)) in bridged:
                    continue
                bus_hi, bus_lo = by_v[v_hi], by_v[v_lo]
                auto_mva = 100.0
                ratio = v_hi / v_lo if v_lo > 0 else 2.0
                bh = state.buses[bus_hi]
                state.transformers.append(GuiTransformer(
                    name=f"Auto TR {v_hi:.0f}/{v_lo:.0f}kV",
                    from_bus=bus_hi, to_bus=bus_lo,
                    from_node=node, to_node=node,
                    from_voltage_kv=v_hi, to_voltage_kv=v_lo,
                    rated_power_mva=auto_mva,
                    impedance_pu=estimate_transformer_impedance_pu(auto_mva, ratio),
                    losses_fraction=estimate_transformer_losses_fraction(auto_mva),
                    latitude=bh.latitude, longitude=bh.longitude,
                ))
                bridged.add(frozenset((v_hi, v_lo)))
                added += 1

    if added:
        result.transformers_added += added
    return added


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
