"""Completeness predicates and physical-default heuristics for Grid Builder.

Two responsibilities:

* :func:`is_feature_complete` — decides whether a fetched
  :class:`GridFeature` carries enough information to be turned into a
  meaningful network element. Used to drive the optional
  *Skip incomplete* toggle in :class:`GridMappingBuildStep`.
* The ``estimate_*`` helpers — fill in physically-reasonable defaults
  (R/X/B per-unit, transformer impedance, battery efficiency by
  chemistry) for fields that the source databases routinely lack.

Heuristics are deliberately rough; they are calibrated to typical AC
transmission practice (50/60 Hz, overhead lines) rather than any
specific utility's spec. The point is to replace zero-defaults with
values that won't blow up downstream load-flow / dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esfex.visualization.workflows.grid_mapping_fetchers import (
        GridFeature,
    )


# ── Completeness ─────────────────────────────────────────────────────


def is_feature_complete(f: "GridFeature") -> bool:
    """Return True if *f* carries enough info to model a useful element.

    The criteria are intentionally lenient — we only reject features
    that would produce an element with all-zero physics. Borderline
    cases (capacity from heuristic, voltage from substation type) are
    accepted; the heuristics module fills the rest.
    """
    t = f.feature_type
    if t == "generator":
        # Need positive capacity AND some idea of fuel/type
        return f.capacity_mw > 0 and bool(f.fuel or f.gen_type)
    if t == "battery":
        # Need either power or energy capacity
        return f.capacity_mw > 0 or f.energy_mwh > 0
    if t == "line":
        # Need a real geometry AND a voltage (capacity is derivable)
        return (
            len(f.line_coords) >= 2
            and f.voltage_kv > 0
        )
    if t == "transformer":
        # Need at least one of the two sides; the other is inferable
        return f.voltage_kv > 0 or f.voltage_kv_secondary > 0
    if t == "substation":
        return f.voltage_kv > 0
    if t == "converter":
        return f.voltage_kv > 0
    if t in ("fuel_entry", "fuel_storage"):
        # These come from OSM tagging; presence of a name is the floor
        return bool(f.name)
    # Unknown type — don't filter
    return True


def reason_incomplete(f: "GridFeature") -> str:
    """Human-readable explanation of why *f* fails ``is_feature_complete``.

    Returns "" when the feature is complete.
    """
    t = f.feature_type
    if t == "generator":
        if f.capacity_mw <= 0:
            return "no capacity"
        if not (f.fuel or f.gen_type):
            return "no fuel/type"
    elif t == "battery":
        if f.capacity_mw <= 0 and f.energy_mwh <= 0:
            return "no power or energy capacity"
    elif t == "line":
        if len(f.line_coords) < 2:
            return "no geometry"
        if f.voltage_kv <= 0:
            return "no voltage"
    elif t == "transformer":
        if f.voltage_kv <= 0 and f.voltage_kv_secondary <= 0:
            return "no voltage on either side"
    elif t == "substation":
        if f.voltage_kv <= 0:
            return "no voltage"
    elif t == "converter":
        if f.voltage_kv <= 0:
            return "no voltage"
    elif t in ("fuel_entry", "fuel_storage"):
        if not f.name:
            return "no name"
    return ""


# ── Physical defaults ────────────────────────────────────────────────


# Typical per-km positive-sequence parameters for overhead AC lines, by
# voltage class. Values are order-of-magnitude figures from utility
# planning handbooks; calibrated for ACSR conductors and standard
# tower geometry. (R, X) in ohm/km, B in microsiemens/km.
# Canonical overhead AC standard line types per nominal voltage level,
# derived from the PyPSA standard line-type library (MIT-licensed). Each row is
# the representative bundled conductor for that level. A feature's line is
# mapped to the type with the *closest* nominal voltage (PyPSA-Earth's
# approach), and both the per-km impedance AND the thermal current rating come
# from the same row, so capacity and impedance stay mutually consistent.
#   (V_nom_kV, r_ohm/km, x_ohm/km, c_nF/km, i_nom_kA per circuit)
_STD_LINE_TYPES: list[tuple[float, float, float, float, float]] = [
    (33.0,  0.250, 0.400, 8.5,  0.40),
    (66.0,  0.150, 0.400, 8.8,  0.50),
    (110.0, 0.095, 0.380, 9.2,  0.74),   # 305-AL1/39-ST1A 110
    (132.0, 0.090, 0.370, 9.3,  0.74),
    (220.0, 0.060, 0.301, 12.5, 1.29),   # Al/St 240/40 2-bundle 220
    (300.0, 0.040, 0.265, 13.2, 1.935),  # Al/St 240/40 3-bundle 300
    (380.0, 0.030, 0.246, 13.8, 2.58),   # Al/St 240/40 4-bundle 380
    (500.0, 0.020, 0.270, 13.0, 3.00),
    (750.0, 0.013, 0.276, 13.1, 4.16),   # Al/St 560/50 4-bundle 750
]

# N-1 security derate applied to the thermal rating (PyPSA-Earth s_max_pu).
_LINE_SECURITY_FACTOR = 0.7
_OMEGA_50HZ = 2.0 * 3.141592653589793 * 50.0  # rad/s, for shunt susceptance


def _nearest_std_line_type(
    voltage_kv: float,
) -> tuple[float, float, float, float, float]:
    """Standard line type whose nominal voltage is closest to *voltage_kv*."""
    v = voltage_kv if voltage_kv and voltage_kv > 0 else 110.0
    return min(_STD_LINE_TYPES, key=lambda t: abs(t[0] - v))


def estimate_line_rxb_per_km(
    voltage_kv: float,
) -> tuple[float, float, float]:
    """Return (R, X, B) per km for an overhead AC line at *voltage_kv*.

    R, X in ohm/km, B in microsiemens/km, taken from the nearest standard
    line type. Shunt susceptance B = omega * C is derived from the type's
    per-km capacitance at 50 Hz.
    """
    _v, r, x, c_nf, _i = _nearest_std_line_type(voltage_kv)
    b_uS_km = _OMEGA_50HZ * c_nf * 1e-3  # omega * C(nF) -> microsiemens/km
    return (r, x, b_uS_km)


def estimate_line_capacity_mw(
    voltage_kv: float,
    num_circuits: int = 1,
) -> float:
    """Thermal capacity (MW) from the nearest standard line type.

    Apparent power per circuit S = sqrt(3) * V * I_nom (MVA), derated by the
    N-1 security factor and scaled by the number of parallel circuits. This
    replaces the previous voltage-step lookup so capacity is consistent with
    the impedance taken from the same line type.
    """
    v_nom, _r, _x, _c, i_nom = _nearest_std_line_type(voltage_kv)
    v = voltage_kv if voltage_kv and voltage_kv > 0 else v_nom
    s_per_circuit = (3.0 ** 0.5) * v * i_nom  # MVA (MW at unity power factor)
    return s_per_circuit * _LINE_SECURITY_FACTOR * max(1, num_circuits)


def estimate_line_pu_params(
    voltage_kv: float,
    length_km: float,
    base_mva: float = 100.0,
) -> tuple[float, float, float]:
    """Per-unit (R, X, B) for a line of given V/length on *base_mva* base.

    ``Z_base = V^2 / S_base`` (ohm). Series R/X are divided; shunt
    susceptance is multiplied by ``Z_base``. Returns (r_pu, x_pu, b_pu).
    Returns zeros if ``voltage_kv`` ≤ 0 or ``length_km`` ≤ 0.
    """
    if voltage_kv <= 0 or length_km <= 0:
        return (0.0, 0.0, 0.0)
    r_km, x_km, b_uS_km = estimate_line_rxb_per_km(voltage_kv)
    z_base = (voltage_kv ** 2) / base_mva  # ohm (line-line, 3φ)
    r_pu = (r_km * length_km) / z_base
    x_pu = (x_km * length_km) / z_base
    # B in pu: b_S = b_uS * 1e-6 ; multiply by Z_base to get pu
    b_pu = (b_uS_km * 1e-6 * length_km) * z_base
    return (r_pu, x_pu, b_pu)


def estimate_transformer_impedance_pu(
    rated_mva: float,
    ratio: float = 2.0,
    system_base_mva: float = 100.0,
) -> float:
    """Typical short-circuit impedance referred to the system base.

    Power transformers have a near-constant impedance on their *own*
    base (~10 %).  To express it on the system base ``S_base`` we scale
    by ``S_base / S_trafo``.  This keeps small/medium units at sensible
    values (≤10 MVA → 6 %, 10–100 MVA → 10 %) and prevents the previous
    flat 14 % from being applied to very large units, which would
    over-couple buses and inflate flow_angle shadow prices.

    Resulting impedance is clamped to ``[0.005, 0.14]`` for numerical
    stability and to avoid unphysically rigid coupling on 1 GVA+
    transformers.  ``ratio`` is reserved for future autotransformer
    refinement (typical ~8 %, GSU ~14 %, etc.).
    """
    del ratio  # reserved
    if rated_mva <= 0:
        return 0.10
    if rated_mva < 10.0:
        return 0.06
    if rated_mva < 100.0:
        return 0.10
    # Large units: scale own-base 10 % to system base.
    x_sys = 0.10 * system_base_mva / rated_mva
    return max(0.005, min(0.14, x_sys))


def estimate_transformer_losses_fraction(rated_mva: float) -> float:
    """Total losses (load + no-load) as fraction of rated power.

    Modern power transformers: ~0.3–0.7 %. Smaller units leak more.
    """
    if rated_mva <= 0:
        return 0.005
    if rated_mva < 10.0:
        return 0.008
    if rated_mva < 100.0:
        return 0.005
    return 0.004


def estimate_battery_efficiencies(
    fuel_or_chemistry: str,
) -> tuple[float, float]:
    """Return (charge_eff, discharge_eff) by chemistry hint.

    Recognises common labels: lithium / li-ion, lead-acid, flow,
    sodium-sulfur, pumped-hydro. Falls back to li-ion-like values.
    """
    s = (fuel_or_chemistry or "").lower().replace("-", "").replace("_", "")
    if "pumped" in s or "phs" in s:
        return (0.85, 0.90)        # round-trip ~76 %
    if "lead" in s:
        return (0.85, 0.85)        # ~72 %
    if "flow" in s or "vrfb" in s or "vanadium" in s:
        return (0.80, 0.85)        # ~68 %
    if "sodium" in s or "nas" in s:
        return (0.85, 0.90)        # ~76 %
    # Default: lithium-ion-class
    return (0.95, 0.95)            # ~90 %


# ── Generator defaults by fuel ───────────────────────────────────────


# Per-fuel operating defaults.  Sources: NREL ATB 2024, IEA WEO 2023,
# EIA Form-860 / 923 statistics for US fleet, EPRI dynamics database
# for inertia.  These are *fleet averages* meant to give a usable
# starting point for screening studies; project-specific values from
# the source database always take precedence.
#
# Field meanings (consumed by ``GuiGeneratorInstance``):
#   eff_at_rated   – LHV electrical efficiency at full load
#   eff_at_min     – ditto at min_power
#   min_power_frac – min_power / rated_power (technical minimum)
#   ramp_up_frac   – fraction of rated_power per HOUR (matches Julia
#                    solver semantics).  CCGT/diesel/hydro can ramp
#                    full capacity in <1 h so they're effectively
#                    unconstrained at typical hourly resolution.
#   ramp_down_frac – idem
#   min_up_h       – minimum on-time after start (hours)
#   min_down_h     – minimum off-time after shutdown (hours)
#   start_up_cost_per_mw – $ per MW of capacity per cold start
#   inertia_s      – synchronous inertia constant H (seconds)
#   life_time_yr   – book life (years)
#   degradation_rate – annual capacity degradation (fraction)
_GEN_DEFAULTS_BY_FUEL: dict[str, dict] = {
    # key matches _FUEL_ALIASES canonical keys in grid_mapping_builder
    "sun": {
        "eff_at_rated": 1.0, "eff_at_min": 1.0,
        "min_power_frac": 0.0, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 0, "min_down_h": 0,
        "start_up_cost_per_mw": 0.0, "inertia_s": 0.0,
        "life_time_yr": 25, "degradation_rate": 0.005,
    },
    "wind": {
        "eff_at_rated": 1.0, "eff_at_min": 1.0,
        "min_power_frac": 0.0, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 0, "min_down_h": 0,
        "start_up_cost_per_mw": 0.0, "inertia_s": 0.0,
        "life_time_yr": 25, "degradation_rate": 0.003,
    },
    "water": {
        "eff_at_rated": 0.90, "eff_at_min": 0.85,
        "min_power_frac": 0.10, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 1, "min_down_h": 1,
        "start_up_cost_per_mw": 0.0, "inertia_s": 3.0,
        "life_time_yr": 50, "degradation_rate": 0.002,
    },
    "geothermal": {
        "eff_at_rated": 0.15, "eff_at_min": 0.12,
        "min_power_frac": 0.50, "ramp_up_frac": 0.40, "ramp_down_frac": 0.40,
        "min_up_h": 24, "min_down_h": 24,
        "start_up_cost_per_mw": 80.0, "inertia_s": 4.0,
        "life_time_yr": 30, "degradation_rate": 0.005,
    },
    # CCGT (combined cycle, default for "naturalgas" — most common)
    "naturalgas": {
        "eff_at_rated": 0.55, "eff_at_min": 0.40,
        "min_power_frac": 0.40, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 4, "min_down_h": 2,
        "start_up_cost_per_mw": 60.0, "inertia_s": 5.0,
        "life_time_yr": 30, "degradation_rate": 0.005,
    },
    "coal": {
        "eff_at_rated": 0.38, "eff_at_min": 0.30,
        "min_power_frac": 0.40, "ramp_up_frac": 0.60, "ramp_down_frac": 0.60,
        "min_up_h": 8, "min_down_h": 8,
        "start_up_cost_per_mw": 90.0, "inertia_s": 6.0,
        "life_time_yr": 40, "degradation_rate": 0.005,
    },
    # Reciprocating diesel engines: small, fast, no real commitment cost.
    "diesel": {
        "eff_at_rated": 0.42, "eff_at_min": 0.32,
        "min_power_frac": 0.30, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 0, "min_down_h": 0,
        "start_up_cost_per_mw": 30.0, "inertia_s": 2.0,
        "life_time_yr": 25, "degradation_rate": 0.010,
    },
    # Steam-cycle plants burning HFO / heavy fuel oil (large central
    # stations like Cuba's Termoeléctricas). Closer to coal behaviour
    # than to diesel engines: long start-up, high min, slow ramps.
    "fuel_oil": {
        "eff_at_rated": 0.34, "eff_at_min": 0.28,
        "min_power_frac": 0.40, "ramp_up_frac": 0.50, "ramp_down_frac": 0.50,
        "min_up_h": 6, "min_down_h": 6,
        "start_up_cost_per_mw": 80.0, "inertia_s": 5.5,
        "life_time_yr": 35, "degradation_rate": 0.006,
    },
    "nuclear": {
        "eff_at_rated": 0.33, "eff_at_min": 0.30,
        "min_power_frac": 0.50, "ramp_up_frac": 0.30, "ramp_down_frac": 0.30,
        "min_up_h": 168, "min_down_h": 168,
        "start_up_cost_per_mw": 250.0, "inertia_s": 6.0,
        "life_time_yr": 60, "degradation_rate": 0.002,
    },
    "biomass": {
        "eff_at_rated": 0.28, "eff_at_min": 0.22,
        "min_power_frac": 0.40, "ramp_up_frac": 0.50, "ramp_down_frac": 0.50,
        "min_up_h": 6, "min_down_h": 4,
        "start_up_cost_per_mw": 70.0, "inertia_s": 5.0,
        "life_time_yr": 30, "degradation_rate": 0.008,
    },
    "biogas": {
        "eff_at_rated": 0.38, "eff_at_min": 0.30,
        "min_power_frac": 0.30, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 1, "min_down_h": 1,
        "start_up_cost_per_mw": 30.0, "inertia_s": 3.0,
        "life_time_yr": 25, "degradation_rate": 0.008,
    },
    "waste": {
        "eff_at_rated": 0.25, "eff_at_min": 0.20,
        "min_power_frac": 0.50, "ramp_up_frac": 0.40, "ramp_down_frac": 0.40,
        "min_up_h": 12, "min_down_h": 12,
        "start_up_cost_per_mw": 80.0, "inertia_s": 5.0,
        "life_time_yr": 25, "degradation_rate": 0.008,
    },
    "otec": {
        "eff_at_rated": 0.05, "eff_at_min": 0.04,
        "min_power_frac": 0.30, "ramp_up_frac": 1.0, "ramp_down_frac": 1.0,
        "min_up_h": 2, "min_down_h": 2,
        "start_up_cost_per_mw": 40.0, "inertia_s": 2.0,
        "life_time_yr": 30, "degradation_rate": 0.005,
    },
}


def estimate_generator_defaults(canonical_fuel: str) -> dict:
    """Return a kwargs dict with sensible defaults for a generator.

    Returns the full operating profile for the canonical fuel (efficiencies,
    min power fraction, ramp rates, on/off times, start-up cost, inertia,
    lifetime, degradation). Callers should apply these only when the
    source data lacks an explicit value.
    """
    return _GEN_DEFAULTS_BY_FUEL.get(canonical_fuel, {})


# ── Frequency inference ─────────────────────────────────────────────


# Approximate bounding boxes for 60 Hz regions worldwide.
# Anywhere outside these is 50 Hz (the global default — Europe,
# Africa, India, China, Australia, most of Asia).
# Source: World map of mains electricity systems.
_60HZ_REGIONS: list[tuple[float, float, float, float]] = [
    # (lat_min, lat_max, lng_min, lng_max)
    # North America (USA, Canada, most of Mexico)
    (15.0, 72.0, -169.0, -52.0),
    # Central America & Caribbean (Cuba, Panama, Costa Rica, Honduras, etc.)
    (7.0, 23.5, -92.0, -59.0),
    # Northern South America (Colombia, Venezuela, Ecuador, Peru, Brazil)
    (-35.0, 13.0, -82.0, -34.0),
    # Saudi Arabia, Bahrain, Kuwait, parts of Yemen
    (12.0, 33.0, 34.0, 56.0),
    # South Korea
    (33.0, 39.0, 124.0, 132.0),
    # Philippines
    (4.5, 21.5, 116.0, 127.0),
    # Taiwan
    (21.5, 25.5, 119.5, 122.5),
    # Japan east of ~136 °E (60 Hz half)
    (24.0, 46.0, 136.0, 146.0),
    # Liberia
    (4.0, 9.0, -12.0, -7.0),
]


def infer_frequency_hz(lat: float, lng: float) -> float:
    """Return the standard mains frequency (50 or 60 Hz) for a location.

    Uses rough country bounding boxes for the (relatively few) 60 Hz
    regions; everywhere else defaults to 50 Hz, the world standard.
    Japan straddles both: east of ≈ 136 °E is 60 Hz, west is 50 Hz —
    captured by the box for that country.
    """
    for la_min, la_max, lo_min, lo_max in _60HZ_REGIONS:
        if la_min <= lat <= la_max and lo_min <= lng <= lo_max:
            return 60.0
    return 50.0


# ── Node operating defaults (reserves / losses) ────────────────────

# A Grid-Builder network leaves node reserves and transmission losses at 0,
# i.e. no security margin and a lossless grid. Fill them with conservative,
# standard defaults so the produced config is operationally complete.
_DEFAULT_NODE_LOSS = 0.02       # 2% transmission losses per node
_STATIC_RESERVE_FRAC = 0.10     # contingency reserve = 10% of the basis
_DYNAMIC_RESERVE_FRAC = 0.05    # spinning/frequency reserve = 5% of the basis


def apply_node_operational_defaults(state) -> dict[str, int]:
    """Fill node operating reserves and transmission losses where still unset.

    Reserves are sized off the node's peak demand when a forecast is present,
    otherwise off its installed generation capacity:
      - static (contingency) reserve = 10% of the basis,
      - dynamic (spinning) reserve   = 5% of the basis,
      - transmission losses          = 2%.
    Only zero fields are filled, so explicit/user values are preserved.
    Returns a count of nodes whose reserves / losses were filled.
    """
    changed = {"reserves": 0, "losses": 0}
    cap_by_node: dict[int, float] = {}
    for g in state.generators.values():
        cap_by_node[g.node] = cap_by_node.get(g.node, 0.0) + (g.rated_power or 0.0)

    for node in state.nodes:
        if node.losses <= 0:
            node.losses = _DEFAULT_NODE_LOSS
            changed["losses"] += 1

        peak = 0.0
        if node.demand is not None and node.demand.peak_mw:
            peak = float(node.demand.peak_mw)
        basis = peak if peak > 0 else cap_by_node.get(node.index, 0.0)
        if basis > 0 and node.reserve_static <= 0 and node.reserve_dynamic <= 0:
            node.reserve_static = round(_STATIC_RESERVE_FRAC * basis, 2)
            node.reserve_dynamic = round(_DYNAMIC_RESERVE_FRAC * basis, 2)
            if node.reserve_duration < 1:
                node.reserve_duration = 1
            changed["reserves"] += 1
    return changed


# ── Retroactive realistic-defaults application ─────────────────────


def apply_realistic_generator_defaults(
    state, force: bool = False,
) -> dict[str, int]:
    """Re-apply per-fuel defaults to generators already in *state*.

    Used by the auto-fix tool to repair YAMLs created before the default
    table existed (or with bad legacy values like min_power = rated_power
    on a thermal plant). For each generator we leave any value the user
    *plausibly* set on purpose, and only fix obviously degenerate ones:

    * ``min_power == rated_power`` on a unit whose technology has a
      published technical minimum below 100 % (e.g. a 500 MW CCGT being
      forced to operate at 100 % always).
    * ``min_power == 0`` on a unit whose technology requires a non-zero
      technical minimum (a 500 MW coal plant cannot turn down to 0 MW
      while staying online).
    * ``ramp_up == 0`` or ``ramp_down == 0`` (a unit that physically
      cannot change output between time steps would be infeasible).
    * ``min_up == 0`` and ``min_down == 0`` on units whose technology has
      published commitment constraints (e.g. nuclear: 168 h on/off).
    * ``inertia == 0`` on a synchronous machine.

    With ``force=True``, all of the above fields are reset from the
    defaults regardless of current value (useful as an "apply realistic
    defaults" button on the auto-fix dialog).

    Returns a dict with counts per field that was touched.
    """
    counts = {
        "min_power": 0, "ramp_up": 0, "ramp_down": 0,
        "min_up": 0, "min_down": 0,
        "inertia": 0, "start_up_cost": 0,
        "eff_at_rated": 0, "eff_at_min": 0,
    }
    if not state.generators:
        return counts

    # Lazy import to avoid the QApplication cost during pure data tests.
    from esfex.visualization.workflows.grid_mapping_builder import (
        _normalize_fuel_key,
    )

    for gid, gen in state.generators.items():
        canonical = _normalize_fuel_key(gen.fuel) if gen.fuel else ""
        d = _GEN_DEFAULTS_BY_FUEL.get(canonical)
        if not d:
            continue
        rp = float(getattr(gen, "rated_power", 0) or 0)
        if rp <= 0:
            continue

        # NOTE: min_power on the schema / Julia model is a *fraction* of
        # rated, not MW absolute. Storing MW here caused 70% UC-mode load
        # shed on cuba.yaml because Julia re-multiplies by rated and gets
        # an impossible floor.
        target_min_frac = d.get("min_power_frac", 0.0)
        target_ru = d.get("ramp_up_frac", 0.0)
        target_rd = d.get("ramp_down_frac", 0.0)
        target_mup = int(d.get("min_up_h", 0))
        target_mdn = int(d.get("min_down_h", 0))
        target_inertia = d.get("inertia_s", 0.0)
        target_su = rp * d.get("start_up_cost_per_mw", 0.0)
        target_e_rated = d.get("eff_at_rated", 0.35)
        target_e_min = d.get("eff_at_min", 0.25)

        # min_power (as a fraction 0–1): fix if 0 and the tech requires
        # a non-zero technical minimum, OR if the unit is forced to run
        # at 100 % (cur_min == 1.0) while the tech can turn down lower.
        cur_min = float(getattr(gen, "min_power", 0) or 0)
        bad_min = (
            (cur_min == 0 and target_min_frac > 0)
            or (cur_min >= 1.0 - 1e-6 and target_min_frac < 1.0)
        )
        if force or bad_min:
            gen.min_power = target_min_frac
            counts["min_power"] += 1

        # ramp rates: a literal 0 is infeasible (the unit can't move).
        cur_ru = float(getattr(gen, "ramp_up", 0) or 0)
        if force or (cur_ru == 0 and target_ru > 0):
            gen.ramp_up = target_ru
            counts["ramp_up"] += 1
        cur_rd = float(getattr(gen, "ramp_down", 0) or 0)
        if force or (cur_rd == 0 and target_rd > 0):
            gen.ramp_down = target_rd
            counts["ramp_down"] += 1

        # commitment constraints: only fix if BOTH are zero AND the
        # tech has non-trivial defaults (don't touch peakers / VRE).
        cur_mup = int(getattr(gen, "min_up", 0) or 0)
        cur_mdn = int(getattr(gen, "min_down", 0) or 0)
        if force or (cur_mup == 0 and cur_mdn == 0
                     and (target_mup > 0 or target_mdn > 0)):
            gen.min_up = target_mup
            gen.min_down = target_mdn
            counts["min_up"] += 1
            counts["min_down"] += 1

        # inertia: 0 on a synchronous machine is wrong (but VRE has 0).
        cur_h = float(getattr(gen, "inertia", 0) or 0)
        if force or (cur_h == 0 and target_inertia > 0):
            gen.inertia = target_inertia
            counts["inertia"] += 1

        # start-up cost: 0 with a non-VRE tech is unrealistic
        cur_su = float(getattr(gen, "start_up_cost", 0) or 0)
        if force or (cur_su == 0 and target_su > 0):
            gen.start_up_cost = target_su
            counts["start_up_cost"] += 1

        # efficiencies: only fix if both are at the GuiGeneratorInstance
        # defaults (0.35 / 0.25), which is the smell of "never set".
        if force or (
            abs(getattr(gen, "eff_at_rated", 0) - 0.35) < 1e-6
            and abs(getattr(gen, "eff_at_min", 0) - 0.25) < 1e-6
        ):
            gen.eff_at_rated = target_e_rated
            gen.eff_at_min = target_e_min
            counts["eff_at_rated"] += 1
            counts["eff_at_min"] += 1

    return counts


def repair_fuel_consistency(state) -> dict[str, int]:
    """Make the fuel catalog and supply network consistent with generators.

    Three repairs, applied in order:

    1. **Catalog**: every distinct ``gen.fuel`` referenced by a generator
       must exist in ``state.fuels``. If missing and we have defaults
       for the canonical key in ``_FUEL_DEFAULTS``, create it; otherwise
       create a minimal generic entry.
    2. **Technologies**: every fuel in the catalog needs at least one
       technology in ``state.technologies`` so the GUI element tree
       stays browsable. Created from ``_TECH_DEFAULTS`` when available.
    3. **Supply**: every non-renewable fuel referenced by a generator
       must be supplied by at least one ``fuel_entry_point``. The fix
       attaches the fuel to an existing entry point if one exists,
       otherwise creates a single new entry on the largest-demand node.

    Returns counts of items created so the caller can summarise the fix.
    """
    from esfex.visualization.data.gui_model import (
        FuelEntryParams, GeoPoint, GuiFuel, GuiFuelEntryPoint, GuiTechnology,
        RENEWABLE_FUELS,
    )
    from esfex.visualization.workflows.grid_mapping_builder import (
        _FUEL_DEFAULTS, _TECH_DEFAULTS, _normalize_fuel_key,
    )

    counts = {"fuels_added": 0, "techs_added": 0, "fuel_entries_updated": 0}

    if not state.generators:
        return counts

    # ── 1. Catalog: register missing fuels ───────────────────────────
    catalog_keys: dict[str, str] = {}  # canonical_key → fuel_id
    for fid, fuel in state.fuels.items():
        catalog_keys[_normalize_fuel_key(fid)] = fid
        if fuel.name:
            catalog_keys.setdefault(_normalize_fuel_key(fuel.name), fid)

    referenced: dict[str, str] = {}  # canonical → original gen.fuel string
    for gen in state.generators.values():
        if gen.fuel and gen.fuel != "None":
            referenced.setdefault(_normalize_fuel_key(gen.fuel), gen.fuel)

    for canonical, raw in referenced.items():
        if canonical in catalog_keys:
            continue
        if canonical in _FUEL_DEFAULTS:
            d = _FUEL_DEFAULTS[canonical]
            fuel_id = d["fuel_id"]
            state.fuels[fuel_id] = GuiFuel(
                fuel_id=fuel_id, name=d["name"],
                unit=d.get("unit"),
                emission_factor=d.get("emission_factor", 0.0),
                energy_content=d.get("energy_content"),
                price_base=d.get("price_base", 0.0),
            )
        else:
            fuel_id = raw.replace(" ", "_")
            state.fuels[fuel_id] = GuiFuel(fuel_id=fuel_id, name=raw)
        catalog_keys[canonical] = fuel_id
        counts["fuels_added"] += 1

    # ── 2. Technologies: ensure each catalog fuel has one ────────────
    tech_fuel_keys: set[str] = set()
    for tech in state.technologies.values():
        if tech.fuel:
            tech_fuel_keys.add(_normalize_fuel_key(tech.fuel))

    for canonical, fuel_id in catalog_keys.items():
        if canonical in tech_fuel_keys or canonical not in _TECH_DEFAULTS:
            continue
        tdef = _TECH_DEFAULTS[canonical]
        # Pick a unique tech_id
        base = tdef["name"].replace(" ", "_")
        tech_id = base
        n = 1
        while tech_id in state.technologies:
            tech_id = f"{base}_{n}"
            n += 1
        state.technologies[tech_id] = GuiTechnology(
            tech_id=tech_id,
            name=tdef["name"],
            category=tdef["category"],
            fuel=fuel_id,
            life_time=tdef.get("life_time", 25),
            eff_at_rated=tdef.get("eff_at_rated", 0.35),
            eff_at_min=tdef.get("eff_at_min", 0.25),
        )
        tech_fuel_keys.add(canonical)
        counts["techs_added"] += 1

    # ── 3. Supply: ensure every non-renewable fuel has an entry ──────
    # Build per-fuel index of which fuel_entry_points already supply it
    supplied: set[str] = set()
    for fe in state.fuel_entry_points:
        for f in fe.fuels:
            supplied.add(_normalize_fuel_key(f))

    needed_for_supply: list[str] = []  # fuel_id values, deduplicated
    for canonical, fuel_id in catalog_keys.items():
        if canonical in supplied:
            continue
        # Skip renewables — they don't need a supply chain
        fuel_obj = state.fuels.get(fuel_id)
        names = {fuel_id}
        if fuel_obj and fuel_obj.name:
            names.add(fuel_obj.name)
        if any(n in RENEWABLE_FUELS for n in names) or canonical in (
            "sun", "wind", "water", "geothermal", "otec", "none"
        ):
            continue
        # Only add supply if some generator actually references it
        if canonical in referenced:
            needed_for_supply.append(fuel_id)

    if needed_for_supply:
        # Attach to existing entry, or create one on the largest node
        if state.fuel_entry_points:
            entry = state.fuel_entry_points[0]
        else:
            # Pick the node with the highest demand (or first node)
            target_node = 0
            best_demand = -1.0
            target_lat, target_lng = 0.0, 0.0
            for node in state.nodes:
                d = float(getattr(node.demand, "peak_mw", 0) or 0)
                if d > best_demand:
                    best_demand = d
                    target_node = node.index
                    target_lat = node.centroid_lat
                    target_lng = node.centroid_lng
            coord = GeoPoint(target_lat, target_lng)
            entry = GuiFuelEntryPoint(
                name="Auto Fuel Import",
                fuels=[],
                node=target_node,
                coordinate=coord,
            )
            state.fuel_entry_points.append(entry)
        for fid in needed_for_supply:
            if fid not in entry.fuels:
                entry.fuels.append(fid)
                entry.fuel_params.setdefault(fid, FuelEntryParams())
                counts["fuel_entries_updated"] += 1

    return counts


def repair_bus_roles_and_demand(state) -> dict[str, int]:
    """Assign bus roles + demand_fraction based on physical topology.

    Rationale: with the legacy default ``role="load", demand_fraction=1.0``
    on every bus, the operational LP fragments node demand equally across
    *all* buses in the node — including HV transmission junctions that
    physically have no consumers attached. The bus-balance constraints
    then force phantom demand at those junctions, requiring transit
    through internal lines that often have no capacity for it. The model
    becomes spuriously infeasible.

    Physical heuristic (PowerFactory / ETAP convention):

    * **mixed** — bus has any supply-side equipment connected (generator,
      battery, electrolyzer, fuel entry point). It both injects supply
      and may host load; it gets a non-zero demand_fraction.
    * **load** — bus is a feeder terminal: low-voltage (< ``HV_THRESHOLD``)
      OR is the *to_bus* of a step-down transformer. Hosts demand only.
    * **connection** — pure transmission junction. No equipment, no
      transformer terminal pointing to it (or HV with only line
      connections). Carries no demand_fraction.

    After role assignment the function redistributes ``demand_fraction``
    across each node's ``load`` + ``mixed`` buses, weighted by the
    rated_power_mva of any step-down transformer ending at that bus
    (its rough downstream capacity) or equally if no such transformer
    is found.

    Returns counts of buses whose role / demand changed.
    """
    HV_THRESHOLD_KV = 100.0  # Above this, presume transmission-only by default

    counts = {
        "buses_role_changed": 0,
        "buses_demand_changed": 0,
        "nodes_redistributed": 0,
    }
    if not state.buses:
        return counts

    # Index supply-equipment buses
    supply_buses: set[str] = set()
    for gen in state.generators.values():
        if gen.bus:
            supply_buses.add(gen.bus)
    for bat in state.batteries.values():
        if bat.bus:
            supply_buses.add(bat.bus)
    for el in state.electrolyzers.values():
        if el.bus:
            supply_buses.add(el.bus)
    for fe in state.fuel_entry_points:
        # FE is anchored at a node, but if any storage/route lands at a
        # specific bus we'd index it; for now node-level entry points
        # don't pin a bus.
        pass

    # Index transformer endpoints. The `to_bus` end is the downstream
    # (LV) side and is where feeders / loads typically hang.
    transformer_to_bus_capacity: dict[str, float] = {}
    transformer_from_bus: set[str] = set()
    for tr in state.transformers:
        if tr.from_bus:
            transformer_from_bus.add(tr.from_bus)
        if tr.to_bus:
            cap = float(getattr(tr, "rated_power_mva", 0) or 0)
            transformer_to_bus_capacity[tr.to_bus] = (
                transformer_to_bus_capacity.get(tr.to_bus, 0) + cap
            )

    # Index buses that are endpoints of real transmission lines
    transmission_endpoints: set[str] = set()
    for ln in state.transmission_lines:
        if ln.from_bus:
            transmission_endpoints.add(ln.from_bus)
        if ln.to_bus:
            transmission_endpoints.add(ln.to_bus)

    # ── Phase 1: assign role per bus ──
    new_roles: dict[str, str] = {}
    for bid, bus in state.buses.items():
        v = float(bus.voltage_kv or 0)
        has_supply = bid in supply_buses
        is_xfm_to = bid in transformer_to_bus_capacity
        is_xfm_from = bid in transformer_from_bus

        if has_supply:
            # Bus hosts generation / storage — mixed (also serves load).
            role = "mixed"
        elif is_xfm_to and v <= HV_THRESHOLD_KV:
            # Down-stream side of a step-down transformer, LV side.
            # Feeders attach here → load.
            role = "load"
        elif v < HV_THRESHOLD_KV and not is_xfm_from:
            # Low-voltage, not a primary side → likely distribution feeder.
            role = "load"
        else:
            # HV junction or pure transit node.
            role = "connection"

        new_roles[bid] = role

    # ── Phase 2: apply role changes (preserve existing demand_fraction) ──
    # IMPORTANT: do NOT reset demand_fraction here.  A non-trivial
    # existing distribution (e.g. ``bus_14=0.7, bus_17=0.2, bus_18=0.1``)
    # encodes Grid Builder / GIS knowledge about feeder sizes that
    # equal-split would destroy.  Phase 3 only touches nodes whose
    # current demand_fraction sum does not reconcile to 1.0.
    for bid, bus in state.buses.items():
        if bus.role != new_roles[bid]:
            bus.role = new_roles[bid]
            counts["buses_role_changed"] += 1

    # ── Phase 3: validate / repair demand_fraction per node ──
    from collections import defaultdict
    buses_by_node: dict[int, list] = defaultdict(list)
    for bid, bus in state.buses.items():
        buses_by_node[bus.parent_node].append(bus)

    SUM_TOL = 0.01  # accept node distributions summing to 1 ± 1 %

    for node_idx, bs in buses_by_node.items():
        # Demand only goes to true load buses (feeders / distribution
        # terminals). "mixed" buses host equipment too but their primary
        # role in the planning model is supply, so we exclude them from
        # the demand split — otherwise an HV bus where Termoeléctrica
        # connects would soak up most of the node's residential load
        # despite physically being a generation point.
        loads = [b for b in bs if b.role == "load"]
        if not loads:
            # No load bus → fall back to "mixed" buses.
            loads = [b for b in bs if b.role == "mixed"]
        if not loads:
            # Still nothing — promote the first reasonable bus to load.
            fallback = next((b for b in bs if b.voltage_kv > 0), None)
            if fallback is None and bs:
                fallback = bs[0]
            if fallback is not None:
                # Clear stale fractions on other buses of the node.
                for b in bs:
                    if b is not fallback and b.demand_fraction != 0.0:
                        b.demand_fraction = 0.0
                        counts["buses_demand_changed"] += 1
                fallback.role = "load"
                if abs(fallback.demand_fraction - 1.0) > 1e-9:
                    fallback.demand_fraction = 1.0
                    counts["buses_demand_changed"] += 1
                counts["buses_role_changed"] += 1
                counts["nodes_redistributed"] += 1
            continue

        # If the existing distribution is plausible (sum ≈ 1.0 and all
        # demand falls on load buses), preserve it — the source carries
        # information about feeder weights that equal-split would erase.
        sum_load_df = sum(b.demand_fraction for b in loads)
        # Stale demand on buses outside the chosen demand-carrier set:
        # cleared here so the load split is the only contribution to the
        # node KCL. Using `loads` as the source of truth covers both
        # branches above — when `loads` is the fallback list of `mixed`
        # buses, those `mixed` are inside `loads` and survive; when true
        # `load` buses exist, any `mixed` with residual df from the
        # importer is treated as stale (mixed buses host generation, not
        # demand — see rationale above).
        loads_ids = {b.bus_id for b in loads}
        non_load_with_df = [b for b in bs
                            if b.bus_id not in loads_ids
                            and b.demand_fraction > 0.0]
        for b in non_load_with_df:
            b.demand_fraction = 0.0
            counts["buses_demand_changed"] += 1

        if abs(sum_load_df - 1.0) <= SUM_TOL and sum_load_df > 0:
            # Distribution already valid — leave it intact.
            continue

        # Otherwise, repair: equal split as a last-resort prior when
        # the Grid Builder hasn't supplied feeder weights.  Real
        # consumer-population weights would require population/GIS
        # data unavailable at planning resolution.
        equal_share = 1.0 / len(loads)
        for b in loads:
            if abs(b.demand_fraction - equal_share) > 1e-9:
                b.demand_fraction = equal_share
                counts["buses_demand_changed"] += 1
        counts["nodes_redistributed"] += 1

    return counts


def repair_node_internal_coupling(state) -> dict[str, int]:
    """Make every node a coherent electrical star (hub-and-spoke).

    Root cause of massive spurious load shedding in OSM-imported configs
    (e.g. Cuba): the importer creates plant / substation buses but never
    ties them together coherently *within a node*.  Generation and demand
    end up scattered across many buses that are 10+ zig-zag hops apart
    (some paths even leave and re-enter the node).  The DC-OPF then keeps
    voltage angles at 0, refuses the long multi-hop paths, and sheds at
    VOLL despite ample in-node generation.  Where a gen and a demand bus
    happen to share one direct step-up transformer (the lucky nodes), the
    node serves 0% shed — proof the physics is fine and only the topology
    is broken.

    This post-pass (run after all elements + the role/demand repair)
    rebuilds each node as a star: it picks a **hub** bus (an inter-node
    transmission endpoint on the backbone, else the highest-voltage /
    best-connected bus) and connects every *significant* bus
    (generation-bearing or demand-bearing) that sits farther than
    ``MAX_HOPS`` from the hub directly to it:

    * different voltage → a transformer (mirrors the substation Auto-TR
      that already makes the lucky nodes work);
    * same voltage → a short low-impedance line.

    Result: any generator is ≤MAX_HOPS+1 from the hub and any load is
    ≤MAX_HOPS+1 from the hub, so gen→load is bounded and the DC-OPF can
    actually dispatch in-node generation instead of shedding.  Connectors
    are sized generously (they stand in for the intra-node transmission
    the importer failed to build, not a real single device).
    """
    from esfex.visualization.data.gui_model import (
        GuiTransformer,
        GuiTransmissionLine,
    )

    MAX_HOPS = 2  # significant bus must reach the node hub within this

    counts = {
        "transformers_added": 0,
        "lines_added": 0,
        "buses_coupled": 0,
        "nodes_restructured": 0,
    }
    if not state.buses:
        return counts

    buses = state.buses
    node_of = {bid: int(b.parent_node) for bid, b in buses.items()}

    # Generation/storage-bearing buses.
    gen_buses: set[str] = set()
    for g in state.generators.values():
        if g.bus:
            gen_buses.add(g.bus)
    for bt in state.batteries.values():
        if getattr(bt, "bus", None):
            gen_buses.add(bt.bus)

    # Inter-node transmission endpoints = backbone import points.
    inter_endpoints: set[str] = set()
    for ln in state.transmission_lines:
        fb, tb = ln.from_bus, ln.to_bus
        if fb in node_of and tb in node_of and node_of[fb] != node_of[tb]:
            inter_endpoints.add(fb)
            inter_endpoints.add(tb)

    # Undirected electrical adjacency (lines + transformers) + degree.
    adj: dict[str, set[str]] = {}
    def _link(a, b):
        if a and b and a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    for ln in state.transmission_lines:
        _link(ln.from_bus, ln.to_bus)
    for tr in state.transformers:
        _link(tr.from_bus, tr.to_bus)

    def _hops(start: str, targets: set[str]) -> int:
        if start in targets:
            return 0
        seen = {start}
        frontier = [start]
        for dist in range(1, MAX_HOPS + 1):
            nxt = []
            for u in frontier:
                for v in adj.get(u, ()):  # noqa: E1133
                    if v in seen:
                        continue
                    if v in targets:
                        return dist
                    seen.add(v)
                    nxt.append(v)
            if not nxt:
                break
            frontier = nxt
        return MAX_HOPS + 1

    pair_exists: set[frozenset] = set()
    for ln in state.transmission_lines:
        pair_exists.add(frozenset((ln.from_bus, ln.to_bus)))
    for tr in state.transformers:
        pair_exists.add(frozenset((tr.from_bus, tr.to_bus)))

    # Group buses per node.
    buses_by_node: dict[int, list[str]] = {}
    for bid in buses:
        buses_by_node.setdefault(node_of[bid], []).append(bid)

    gen_mva_by_node: dict[int, float] = {}
    for g in state.generators.values():
        n = node_of.get(g.bus, -1)
        gen_mva_by_node[n] = gen_mva_by_node.get(n, 0.0) + float(
            getattr(g, "rated_power", 0.0) or 0.0
        )

    def _haversine_km(la1, lo1, la2, lo2):
        from math import radians, sin, cos, asin, sqrt
        p1, p2 = radians(la1), radians(la2)
        dp, dl = radians(la2 - la1), radians(lo2 - lo1)
        a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
        return 2 * 6371.0 * asin(sqrt(a))

    for node, node_buses in buses_by_node.items():
        # Significant buses: hold generation or demand — these must all
        # be electrically close to the hub.
        sig = [
            b for b in node_buses
            if b in gen_buses
            or (buses[b].role in ("load", "mixed")
                and (buses[b].demand_fraction or 0.0) > 0.0)
        ]
        if len(sig) < 2:
            continue  # nothing to couple (0 or 1 significant bus)

        # Hub = backbone endpoint if any (highest V, then degree),
        # else highest-V / best-connected bus in the node.
        def _hub_key(b):
            return (
                b in inter_endpoints,
                float(buses[b].voltage_kv or 0.0),
                len(adj.get(b, ())),
            )
        hub = max(node_buses, key=_hub_key)

        node_restructured = False
        v_hub = float(buses[hub].voltage_kv or 0.0)
        mva = max(gen_mva_by_node.get(node, 0.0), 500.0)

        for b in sig:
            if b == hub:
                continue
            if _hops(b, {hub}) <= MAX_HOPS:
                continue  # already close to the hub
            if frozenset((b, hub)) in pair_exists:
                continue

            v_b = float(buses[b].voltage_kv or 0.0)
            if (v_b > 0 and v_hub > 0
                    and abs(v_hub - v_b) / max(v_hub, v_b) > 0.1):
                v_hi, v_lo = max(v_hub, v_b), min(v_hub, v_b)
                hi_bus = hub if v_hub >= v_b else b
                lo_bus = b if v_hub >= v_b else hub
                ratio = v_hi / v_lo if v_lo > 0 else 2.0
                state.transformers.append(GuiTransformer(
                    name=f"NodeHub TR {v_hi:.0f}/{v_lo:.0f}kV {b}",
                    from_bus=hi_bus, to_bus=lo_bus,
                    from_node=node, to_node=node,
                    from_voltage_kv=v_hi, to_voltage_kv=v_lo,
                    rated_power_mva=mva,
                    impedance_pu=estimate_transformer_impedance_pu(
                        mva, ratio),
                    losses_fraction=estimate_transformer_losses_fraction(
                        mva),
                    latitude=buses[b].latitude,
                    longitude=buses[b].longitude,
                ))
                counts["transformers_added"] += 1
            else:
                lid = f"line_hub_{state._next_line_id}"
                state._next_line_id += 1
                state.transmission_lines.append(GuiTransmissionLine(
                    line_id=lid,
                    from_bus=hub, to_bus=b,
                    from_node=node, to_node=node,
                    capacity_mw=max(mva, 500.0),
                    voltage_kv=v_b or v_hub or 220.0,
                    reactance_pu=0.01,
                    resistance_pu=0.001,
                    num_circuits=1,
                ))
                counts["lines_added"] += 1

            adj.setdefault(b, set()).add(hub)
            adj.setdefault(hub, set()).add(b)
            pair_exists.add(frozenset((b, hub)))
            counts["buses_coupled"] += 1
            node_restructured = True

        if node_restructured:
            counts["nodes_restructured"] += 1

    return counts
