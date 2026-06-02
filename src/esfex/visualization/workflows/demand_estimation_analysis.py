"""Demand estimation analysis engine.

Core computation combining spatial proxies, macroeconomic indicators,
and meteorological data to generate hourly demand time series per node.

Components:
    - DemandEstimationConfig: parameter container
    - ProxyData / MacroData / MeteoData: input data containers
    - DemandEstimationResult: output container
    - REFERENCE_PROFILES: synthetic hourly demand shapes
    - DemandProfileBuilder: main estimation algorithm
    - DemandEstimationWorker: QThread wrapper for GUI integration
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DemandEstimationConfig:
    """All parameters controlling the demand estimation pipeline."""

    base_year: int = 2025
    simulation_years: int = 25
    num_nodes: int = 1
    hours_per_year: int = 8760
    # Output temporal resolution in hours (1.0 = hourly, 0.5 = 30-min, 6.0 = 6-h)
    resolution_hours: float = 1.0

    # Spatial proxy weighting method: "manual" | "equal" | "entropy" | "pca"
    weight_method: str = "manual"

    # Spatial proxy weights — used only when weight_method == "manual"
    weight_buildings: float = 0.35
    weight_population: float = 0.30
    weight_nightlights: float = 0.20
    weight_landuse: float = 0.15

    # Total national/system annual demand (0 → auto-estimate from kWh/capita)
    national_demand_gwh: float = 0.0

    # Temporal shape
    reference_profile: str = "tropical_island"

    # Climate parameters
    hdd_base_temp: float = 18.0    # °C base for heating degree-hours
    cdd_base_temp: float = 24.0    # °C base for cooling degree-hours
    heating_sensitivity: float = 0.0    # MW / degree-hour (tropical → 0)
    cooling_sensitivity: float = 0.02   # MW / degree-hour

    # Projection
    gdp_growth_rate: float = 0.030          # Annual GDP growth
    demand_gdp_elasticity: float = 0.80     # Developing: ~0.8; developed: ~0.3
    efficiency_improvement: float = 0.005   # Annual energy intensity reduction
    electrification_growth: float = 0.010   # Annual new electrification

    # Logistic saturation parameters for efficiency and electrification.
    # These model physical limits: efficiency can't reduce intensity to zero,
    # electrification can't exceed 100%.
    # Logistic: rate(y) = base_rate × (1 - current_level / saturation_level)
    # where current_level accumulates from base_level over years.
    efficiency_saturation: float = 0.50     # max cumulative intensity reduction (50%)
    efficiency_base_level: float = 0.0      # current efficiency level (0 = no improvement yet)
    electrification_saturation: float = 1.0 # max electrification penetration (100%)
    electrification_base_level: float = 0.0 # current electrification level

    # Sectoral fractions (applied for sectoral output — don't have to sum to 1)
    residential_fraction: float = 0.40
    commercial_fraction: float = 0.35
    industrial_fraction: float = 0.25

    # Calibration
    known_peak_mw: float = 0.0
    known_annual_gwh: float = 0.0

    # GDP-to-demand conversion factor (kWh per USD of GDP, used when
    # electricity consumption per capita is unavailable)
    gdp_kwh_per_usd: float = 0.4

    # Monthly climate correction bounds (factor range around archetype prior)
    monthly_climate_clip_min: float = 0.5
    monthly_climate_clip_max: float = 2.0

    # Hourly weather perturbation bounds
    hourly_perturbation_clip_min: float = 0.5
    hourly_perturbation_clip_max: float = 2.0

    # Time-varying spatial weights (urban/rural growth differential)
    enable_time_varying_weights: bool = True
    urban_rural_threshold: float = 0.6  # (res+com) fraction above this = urban

    # CMIP6 climate projections
    cmip6_enabled: bool = False
    cmip6_ssp_pathway: str = "ssp245"
    cmip6_gcm_model: str = "ACCESS-CM2"

    # ML engine control
    force_archetype: bool = False   # True → skip ML even if model available
    ml_engine: str = "auto"         # "auto" | "tft" | "xgboost"


@dataclass
class ProxyData:
    """Collected spatial proxy data for all nodes."""

    # Per-node weights (lists of length num_nodes, need not sum to 1)
    building_weights: list[float] = field(default_factory=list)
    population_weights: list[float] = field(default_factory=list)
    nightlight_weights: list[float] = field(default_factory=list)
    landuse_weights: list[float] = field(default_factory=list)

    # Per-node sectoral composition (fractions, each node sums to ~1.0)
    # Used for hourly shape blending: residential→archetype, industrial→flat
    node_residential_fraction: list[float] = field(default_factory=list)
    node_commercial_fraction: list[float] = field(default_factory=list)
    node_industrial_fraction: list[float] = field(default_factory=list)

    # Node geography
    node_lats: list[float] = field(default_factory=list)
    node_lons: list[float] = field(default_factory=list)
    node_names: list[str] = field(default_factory=list)


@dataclass
class MacroData:
    """Macroeconomic indicators for demand estimation and projection."""

    country_iso: str = ""
    country_name: str = ""

    # Most recent values
    gdp_per_capita: float = 0.0              # USD
    population: float = 0.0
    urbanization_pct: float = 0.0             # % (0 = unknown)
    electricity_access_pct: float = 0.0      # % (0 = unknown, treated as 100%)
    electric_consumption_kwh_capita: float = 0.0
    industry_value_added_pct: float = 0.0    # % of GDP (0 = unknown)

    # Growth parameters (0.0 = no growth assumed if per-year dict has no entry)
    gdp_growth_rate: float = 0.0

    # Time series (year → value)
    gdp_time_series: dict[int, float] = field(default_factory=dict)
    consumption_time_series: dict[int, float] = field(default_factory=dict)
    gdp_growth_forecast: dict[int, float] = field(default_factory=dict)
    urbanization_time_series: dict[int, float] = field(default_factory=dict)

    # SSP multipliers (year → factor relative to base_year)
    ssp_gdp_multipliers: dict[int, float] = field(default_factory=dict)
    ssp_pop_multipliers: dict[int, float] = field(default_factory=dict)

    # Per-year projection rates from Step 3 table ({year: rate})
    gdp_growth_by_year: dict[int, float] = field(default_factory=dict)
    pop_growth_by_year: dict[int, float] = field(default_factory=dict)
    elasticity_by_year: dict[int, float] = field(default_factory=dict)
    efficiency_by_year: dict[int, float] = field(default_factory=dict)
    electrification_by_year: dict[int, float] = field(default_factory=dict)


@dataclass
class MeteoData:
    """Meteorological data for temporal demand shaping.

    Single-year fields (temperature_hourly, hdd_hourly, cdd_hourly) are used
    as the base weather year.  For multi-year simulations, per-year data can
    be supplied via ``hdd_by_year`` / ``cdd_by_year`` dicts (year → hourly
    list).  If a simulation year is not found in those dicts, the base-year
    data is reused.
    """

    temperature_hourly: list[float] = field(default_factory=list)   # hourly °C
    humidity_hourly: list[float] = field(default_factory=list)       # hourly %
    hdd_hourly: list[float] = field(default_factory=list)            # °C·h, ≥0
    cdd_hourly: list[float] = field(default_factory=list)            # °C·h, ≥0
    lat: float = 0.0
    lon: float = 0.0
    year: int = 0

    # Per-year overrides (year → hourly list); e.g. from CMIP6 projections
    hdd_by_year: dict[int, list[float]] = field(default_factory=dict)
    cdd_by_year: dict[int, list[float]] = field(default_factory=dict)

    # Per-node base-year climate (node_index → hourly list)
    node_hdd_hourly: dict[int, list[float]] = field(default_factory=dict)
    node_cdd_hourly: dict[int, list[float]] = field(default_factory=dict)

    # Per-node per-year overrides (node_index → {year → hourly list})
    node_hdd_by_year: dict[int, dict[int, list[float]]] = field(default_factory=dict)
    node_cdd_by_year: dict[int, dict[int, list[float]]] = field(default_factory=dict)


@dataclass
class DemandEstimationResult:
    """Output of the demand estimation pipeline."""

    demand: Any = None              # np.ndarray (steps, num_nodes) — MW
    demand_multi_year: Any = None   # np.ndarray (years*steps, num_nodes) — MW
    resolution_hours: float = 1.0   # Output resolution (same as config.resolution_hours)

    # System-wide metrics
    total_peak_mw: float = 0.0
    total_annual_gwh: float = 0.0
    total_load_factor: float = 0.0

    # Per-node metrics (lists of length num_nodes)
    peak_mw: list[float] = field(default_factory=list)
    annual_gwh: list[float] = field(default_factory=list)
    load_factor: list[float] = field(default_factory=list)

    # Derived
    spatial_weights: list[float] = field(default_factory=list)
    monthly_gwh: list[float] = field(default_factory=list)    # 12 values
    duration_curve: Any = None      # np.ndarray sorted descending

    # Hierarchical decomposition diagnostics
    level0_annual_mwh_by_year: list[float] = field(default_factory=list)

    # Diagnostics — demand source used ("user_override", "macro", "gdp_estimate")
    demand_source: str = ""
    warnings: list[str] = field(default_factory=list)

    config: DemandEstimationConfig = field(
        default_factory=DemandEstimationConfig
    )


# ──────────────────────────────────────────────────────────────────────────────
# Archetype-Based Profile Library
# ──────────────────────────────────────────────────────────────────────────────
#
# Each regional archetype encodes:
#   • Monthly energy fractions  — derived from real national utility statistics
#   • Intraday load shapes      — tabulated from real measured hourly load data
#                                 published by TSOs and IRENA/IEA regional reports
#   • Weather sensitivity (β)  — linear regression coefficients from peer-reviewed
#                                 studies (see individual source citations below)
#
# This replaces the previous purely-synthetic (Gaussian/sinusoidal) approach with
# parameter sets grounded in measured consumption patterns.
# ──────────────────────────────────────────────────────────────────────────────


def _norm24(raw: tuple) -> tuple:
    """Normalize 24 relative shape values so their mean = 1.0."""
    s = sum(raw)
    f = 24.0 / s if s > 0 else 1.0
    return tuple(v * f for v in raw)


def _norm12(raw: tuple) -> tuple:
    """Normalize 12 monthly factors so their mean = 1.0."""
    s = sum(raw)
    f = 12.0 / s if s > 0 else 1.0
    return tuple(v * f for v in raw)


@dataclass(frozen=True)
class ArchetypeProfile:
    """Compact, empirically-grounded regional demand archetype.

    Attributes
    ----------
    monthly_factors : tuple of 12 floats (Jan=0 … Dec=11), mean = 1.0
        Relative monthly energy fraction derived from real utility billing data.
    shapes : dict of str → tuple of 24 floats, each mean = 1.0
        Intraday load shapes keyed as "{season}_{day_type}".
        season   ∈ {"winter", "shoulder", "summer"}
        day_type ∈ {"weekday", "saturday", "sunday"}
    winter_months / summer_months : frozenset of 0-based month indices.
        Remaining months are classified as "shoulder".
    hdd_beta / cdd_beta : float  [fraction of mean demand / °C of hourly deviation]
        Linear weather-sensitivity coefficients from published regression studies.
        A value of 0.013 means a 10 °C departure from base → +13 % demand that hour.
    """
    name: str
    description: str
    monthly_factors: tuple
    shapes: dict
    winter_months: frozenset
    summer_months: frozenset
    hdd_beta: float = 0.0
    cdd_beta: float = 0.0


def _build_archetype(
    name: str, description: str,
    monthly_raw: tuple, shapes_raw: dict,
    winter_months: tuple, summer_months: tuple,
    hdd_beta: float = 0.0, cdd_beta: float = 0.0,
) -> ArchetypeProfile:
    return ArchetypeProfile(
        name=name, description=description,
        monthly_factors=_norm12(monthly_raw),
        shapes={k: _norm24(v) for k, v in shapes_raw.items()},
        winter_months=frozenset(winter_months),
        summer_months=frozenset(summer_months),
        hdd_beta=hdd_beta, cdd_beta=cdd_beta,
    )


# ── Day-of-year → month index mapping (non-leap year) ─────────────────────────
_DOY_TO_MONTH: list = (
    [0] * 31 + [1] * 28 + [2] * 31 + [3] * 30
    + [4] * 31 + [5] * 30 + [6] * 31 + [7] * 31
    + [8] * 30 + [9] * 31 + [10] * 30 + [11] * 31
)  # length 365


# ── TROPICAL ISLAND ───────────────────────────────────────────────────────────
# Sources:
#   Monthly factors  : PREPA (Puerto Rico) Integrated Resource Plan 2019,
#                      Table 3-2; IRENA Caribbean Energy Outlook 2016, Fig. 4.3.
#   Intraday shapes  : PREPA hourly load curve analysis (IRP 2019, Section 3);
#                      GRENLEC (Grenada) and LUCELEC (St. Lucia) load profiles
#                      from their respective annual reports.
#   CDD sensitivity  : Quayle & Lott (1980); Sailor & Muñoz (1997) "Sensitivity
#                      of electricity and natural gas consumption to climate in
#                      the USA" — tropical AC-dominated systems ~0.012-0.015/°C.
#
# Key features: AC-driven summer peak (May-Oct); dual daily peaks —
#   afternoon AC (14:30 h) + evening domestic (19:30 h); LF ≈ 0.62-0.65.

_TROP_SUM_WD  = (73, 69, 66, 64, 63, 65, 72, 85, 96, 104, 109, 112,
                 112, 115, 121, 124, 122, 117, 114, 112, 107, 100,  91,  82)
_TROP_SUM_SAT = (75, 71, 68, 66, 65, 66, 70,  78,  88,  97, 105, 111,
                 113, 116, 121, 123, 121, 117, 116, 114, 109, 102,  94,  85)
_TROP_SUM_SUN = (77, 73, 70, 68, 67, 68, 69,  74,  84,  93, 102, 109,
                 113, 116, 121, 124, 122, 118, 117, 115, 110, 102,  94,  85)
_TROP_WIN_WD  = (77, 73, 70, 68, 67, 68, 78,  91, 103, 109, 111, 110,
                 109, 109, 111, 114, 113, 111, 119, 122, 115, 105,  94,  85)
_TROP_WIN_SAT = (79, 75, 72, 70, 69, 70, 75,  84,  94, 102, 107, 110,
                 111, 111, 113, 115, 114, 112, 119, 121, 115, 106,  96,  86)
_TROP_WIN_SUN = (81, 77, 74, 72, 71, 72, 73,  80,  90,  99, 105, 109,
                 111, 112, 114, 116, 115, 113, 119, 121, 115, 107,  97,  87)

_TROPICAL_ISLAND = _build_archetype(
    name="tropical_island",
    description=(
        "Caribbean/Pacific island — cooling-dominated, dual daily peaks "
        "(afternoon AC + evening domestic). "
        "Basis: PREPA IRP (2019); IRENA Caribbean Outlook (2016)."
    ),
    # Monthly fractions from PREPA/Caribbean billing data (Jan-Dec)
    monthly_raw=(88, 86, 89, 93, 101, 109, 115, 115, 107, 99, 93, 90),
    shapes_raw={
        "summer_weekday":   _TROP_SUM_WD,
        "summer_saturday":  _TROP_SUM_SAT,
        "summer_sunday":    _TROP_SUM_SUN,
        "shoulder_weekday": _TROP_WIN_WD,   # tropics: shoulder ≈ winter
        "shoulder_saturday":_TROP_WIN_SAT,
        "shoulder_sunday":  _TROP_WIN_SUN,
        "winter_weekday":   _TROP_WIN_WD,
        "winter_saturday":  _TROP_WIN_SAT,
        "winter_sunday":    _TROP_WIN_SUN,
    },
    winter_months=(10, 11, 0, 1, 2, 3),   # Nov-Apr: mild, lower AC
    summer_months=(4,  5,  6, 7, 8, 9),   # May-Oct: peak cooling season
    hdd_beta=0.000,
    cdd_beta=0.013,   # Sailor & Muñoz (1997): tropical AC ~0.012-0.015/°C
)


# ── TEMPERATE URBAN ───────────────────────────────────────────────────────────
# Sources:
#   Monthly factors  : ENTSO-E Transparency Platform, France + Germany 2019-2023
#                      average monthly load (https://transparency.entsoe.eu).
#   Intraday shapes  : ENTSO-E hourly load data, seasonal averages FR+DE 2022;
#                      BDEW Standard Load Profile H0 (household) + G0 (commercial)
#                      shapes, normalized and averaged.
#   HDD/CDD betas    : Valor, Meneu & Caselles (2001) "Daily air temperature and
#                      electricity load in Spain", J. Applied Meteorology 40(8);
#                      Bessec & Fouquau (2008) European panel — hdd ≈ 0.008/°C,
#                      cdd ≈ 0.007/°C for Northwestern Europe.
#
# Key features: winter-dominant (Dec-Jan peak 1.22×); secondary summer shoulder;
#   double daily peak (morning 08 h + evening 19 h); LF ≈ 0.70-0.73.

_TEMP_WIN_WD  = (72, 67, 63, 61, 60, 63, 75, 94, 114, 110, 106, 104,
                 101, 101, 102, 103, 105, 112, 123, 122, 112, 100,  89,  79)
_TEMP_WIN_SAT = (77, 71, 67, 64, 63, 64, 70, 82,  95, 108, 113, 114,
                 112, 111, 110, 110, 111, 115, 121, 120, 113, 103,  92,  82)
_TEMP_WIN_SUN = (80, 74, 70, 67, 66, 67, 71, 80,  93, 106, 113, 115,
                 114, 113, 111, 110, 111, 115, 121, 120, 112, 102,  92,  84)
_TEMP_SHO_WD  = (70, 65, 62, 60, 59, 62, 73, 91, 108, 108, 106, 104,
                 102, 101, 103, 105, 108, 115, 123, 119, 108,  97,  86,  76)
_TEMP_SHO_SAT = (75, 70, 66, 63, 62, 63, 69, 80,  93, 105, 111, 114,
                 113, 112, 112, 113, 113, 116, 121, 118, 110,  99,  89,  80)
_TEMP_SHO_SUN = (78, 73, 69, 66, 65, 66, 69, 77,  91, 104, 112, 116,
                 116, 115, 114, 113, 114, 117, 121, 118, 109,  98,  89,  82)
_TEMP_SUM_WD  = (68, 63, 60, 59, 58, 61, 71, 88, 102, 106, 106, 105,
                 103, 102, 104, 107, 110, 117, 122, 116, 105,  95,  84,  74)
_TEMP_SUM_SAT = (73, 68, 64, 62, 61, 62, 67, 78,  92, 102, 109, 112,
                 112, 112, 113, 114, 115, 118, 120, 116, 107,  96,  86,  77)
_TEMP_SUM_SUN = (76, 71, 67, 65, 64, 65, 68, 76,  90, 101, 109, 114,
                 115, 115, 115, 115, 116, 118, 120, 115, 106,  96,  87,  79)

_TEMPERATE_URBAN = _build_archetype(
    name="temperate_urban",
    description=(
        "European temperate — winter heating dominant (Jan peak), "
        "secondary summer cooling shoulder (Jul). Double daily peak. "
        "Basis: ENTSO-E FR+DE 2019-2023; BDEW H0+G0 standard load profiles."
    ),
    # Monthly fractions — ENTSO-E France 2019-2023 normalized monthly averages
    monthly_raw=(122, 116, 103, 91, 84, 82, 84, 82, 90, 98, 109, 122),
    shapes_raw={
        "winter_weekday":   _TEMP_WIN_WD,
        "winter_saturday":  _TEMP_WIN_SAT,
        "winter_sunday":    _TEMP_WIN_SUN,
        "shoulder_weekday": _TEMP_SHO_WD,
        "shoulder_saturday":_TEMP_SHO_SAT,
        "shoulder_sunday":  _TEMP_SHO_SUN,
        "summer_weekday":   _TEMP_SUM_WD,
        "summer_saturday":  _TEMP_SUM_SAT,
        "summer_sunday":    _TEMP_SUM_SUN,
    },
    winter_months=(11, 0, 1),    # Dec, Jan, Feb
    summer_months=(5, 6, 7),     # Jun, Jul, Aug
    hdd_beta=0.008,   # Valor et al. (2001)
    cdd_beta=0.007,   # Bessec & Fouquau (2008)
)


# ── ARID INDUSTRIAL ───────────────────────────────────────────────────────────
# Sources:
#   Monthly factors  : Saudi Electricity Company Annual Statistical Booklet 2022
#                      (Table 2-3, monthly generation as proxy for load);
#                      IRENA Arabian Peninsula Renewable Energy Outlook (2016),
#                      Fig. 2.7 — seasonal demand index.
#   Intraday shapes  : KAPSARC "Electricity Demand in Saudi Arabia" (2015),
#                      Fig. 5 (summer/winter weekday load curves, normalized);
#                      UAE TRANSCO annual report hourly load curves.
#   CDD sensitivity  : Al-Sahlawi (1999) Saudi electricity demand regression;
#                      KAPSARC (2015): ~0.017-0.019/°C for GCC countries.
#
# Key features: extreme summer peak (Jul-Aug, 1.32-1.34×); high 24/7 industrial
#   base; broad midday AC Gaussian; LF ≈ 0.55-0.60.

_ARID_SUM_WD  = (80, 77, 75, 73, 72, 74, 82, 94, 106, 116, 122, 127,
                 130, 133, 136, 136, 133, 126, 120, 115, 111, 103,  95,  87)
_ARID_SUM_WE  = (82, 79, 77, 75, 74, 75, 80, 88,  99, 110, 118, 124,
                 128, 132, 136, 136, 133, 127, 122, 117, 112, 105,  97,  89)
_ARID_SHO_WD  = (79, 76, 73, 71, 70, 72, 82, 95, 108, 116, 120, 123,
                 124, 126, 128, 128, 126, 122, 117, 114, 109, 102,  94,  85)
_ARID_SHO_WE  = (81, 78, 75, 73, 72, 73, 79, 89, 101, 111, 118, 122,
                 123, 125, 128, 128, 126, 121, 118, 115, 110, 103,  95,  86)
_ARID_WIN_WD  = (78, 75, 72, 70, 69, 71, 81, 96, 110, 116, 118, 117,
                 115, 115, 117, 118, 117, 116, 116, 114, 109, 101,  93,  84)
_ARID_WIN_WE  = (80, 77, 74, 72, 71, 72, 78, 89, 101, 111, 117, 118,
                 117, 116, 117, 118, 116, 115, 116, 113, 108, 101,  94,  85)

_ARID_INDUSTRIAL = _build_archetype(
    name="arid_industrial",
    description=(
        "Middle East / Gulf arid — extreme summer cooling peak (Jul-Aug 1.33×), "
        "high industrial base load, broad midday AC peak. "
        "Basis: Saudi Electricity Co. (2022); KAPSARC (2015); IRENA Gulf (2016)."
    ),
    # Monthly fractions — Saudi Electricity Company 2022 monthly generation index
    monthly_raw=(83, 82, 87, 94, 107, 120, 132, 134, 120, 104, 89, 85),
    shapes_raw={
        "summer_weekday":   _ARID_SUM_WD,
        "summer_saturday":  _ARID_SUM_WE,
        "summer_sunday":    _ARID_SUM_WE,
        "shoulder_weekday": _ARID_SHO_WD,
        "shoulder_saturday":_ARID_SHO_WE,
        "shoulder_sunday":  _ARID_SHO_WE,
        "winter_weekday":   _ARID_WIN_WD,
        "winter_saturday":  _ARID_WIN_WE,
        "winter_sunday":    _ARID_WIN_WE,
    },
    winter_months=(11, 0, 1, 2),       # Dec-Mar: mild
    summer_months=(4, 5, 6, 7, 8, 9),  # May-Oct: extreme heat
    hdd_beta=0.003,
    cdd_beta=0.018,   # Al-Sahlawi (1999); KAPSARC (2015) GCC regression
)


# ── FLAT BASELOAD ─────────────────────────────────────────────────────────────
_FLAT_BASELOAD = _build_archetype(
    name="flat_baseload",
    description="24/7 industrial-dominated — minimal intraday and seasonal variation.",
    monthly_raw=(100,) * 12,
    shapes_raw={
        f"{s}_{d}": (100,) * 24
        for s in ("winter", "shoulder", "summer")
        for d in ("weekday", "saturday", "sunday")
    },
    winter_months=(11, 0, 1),
    summer_months=(5, 6, 7),
)


ARCHETYPE_LIBRARY: dict[str, ArchetypeProfile] = {
    a.name: a
    for a in (_TROPICAL_ISLAND, _TEMPERATE_URBAN, _ARID_INDUSTRIAL, _FLAT_BASELOAD)
}

_PROFILE_CACHE: dict[str, Any] = {}


def _profile_from_archetype(
    archetype: ArchetypeProfile,
    meteo: "MeteoData",
    cfg: "DemandEstimationConfig",
) -> "np.ndarray":  # type: ignore[name-defined]
    """Reconstruct normalized hourly demand profile from archetype + ERA5 data.

    Algorithm
    ---------
    1. For each hour, determine month, season, and day-type.
    2. Look up the intraday shape value for (season, day_type, hour_of_day)
       and multiply by the monthly scaling factor.
    3. Apply linear weather correction (regression-based):
           D_corr[t] = D_base[t] × (1 + β_HDD × HDD[t] + β_CDD × CDD[t])
       where HDD[t]/CDD[t] = ERA5 hourly departure from base temperature (°C).
       Config overrides (heating_sensitivity / cooling_sensitivity > 0) take
       precedence over the archetype defaults.
    4. Normalise to mean = 1.0.
    """
    import numpy as np

    hpy     = getattr(cfg, "hours_per_year", 8760)
    hours   = np.arange(hpy)
    doy     = hours // 24                                   # 0-364
    hod     = (hours % 24).astype(np.int32)                 # hour of day 0-23
    dow     = doy % 7                                       # 0=Mon … 6=Sun
    month   = np.array([_DOY_TO_MONTH[min(d, 364)] for d in doy], dtype=np.int32)

    # Monthly scale (hpy,)
    mf = np.array(archetype.monthly_factors, dtype=np.float64)
    monthly_scale = mf[month]

    # Season index: 0=winter, 1=shoulder, 2=summer
    win_set = np.array(sorted(archetype.winter_months), dtype=np.int32)
    sum_set = np.array(sorted(archetype.summer_months), dtype=np.int32)
    season_idx = np.where(
        np.isin(month, win_set), 0,
        np.where(np.isin(month, sum_set), 2, 1),
    )

    # Day-type index: 0=weekday, 1=saturday, 2=sunday
    day_type_idx = np.where(dow == 6, 2, np.where(dow == 5, 1, 0))

    # Build shape lookup matrix (3 seasons × 3 day-types × 24 hours)
    season_names   = ["winter",  "shoulder", "summer"]
    day_type_names = ["weekday", "saturday", "sunday"]
    shape_matrix   = np.ones((3, 3, 24), dtype=np.float64)
    for si, sn in enumerate(season_names):
        for di, dn in enumerate(day_type_names):
            key = f"{sn}_{dn}"
            vals = (
                archetype.shapes.get(key)
                or archetype.shapes.get(f"shoulder_{dn}")
                or archetype.shapes.get("shoulder_weekday")
            )
            if vals is not None:
                shape_matrix[si, di, :] = vals

    # Vectorised lookup: (hpy,)
    shape_values = shape_matrix[season_idx, day_type_idx, hod]
    profile = monthly_scale * shape_values

    # Weather correction — linear regression model
    β_hdd = cfg.heating_sensitivity if cfg.heating_sensitivity > 0 else archetype.hdd_beta
    β_cdd = cfg.cooling_sensitivity if cfg.cooling_sensitivity > 0 else archetype.cdd_beta

    if (β_hdd > 0 or β_cdd > 0) and (meteo.hdd_hourly or meteo.cdd_hourly):
        n = len(profile)
        hdd = np.array((list(meteo.hdd_hourly) + [0.0] * n)[:n], dtype=np.float64)
        cdd = np.array((list(meteo.cdd_hourly) + [0.0] * n)[:n], dtype=np.float64)
        profile *= 1.0 + β_hdd * hdd + β_cdd * cdd

    m = profile.mean()
    return (profile / m).astype(np.float64) if m > 0 else profile.astype(np.float64)


def _get_profile(name: str) -> "np.ndarray":  # type: ignore[name-defined]
    """Return normalized archetype profile (no weather correction).
    Result is cached after first call.
    """
    if name not in _PROFILE_CACHE:
        archetype = ARCHETYPE_LIBRARY.get(name, _TROPICAL_ISLAND)

        class _EmptyMeteo:  # lightweight stand-in
            hdd_hourly: list = []
            cdd_hourly: list = []

        class _DefaultCfg:
            heating_sensitivity: float = 0.0
            cooling_sensitivity: float = 0.0
            hours_per_year: int = 8760

        _PROFILE_CACHE[name] = _profile_from_archetype(
            archetype, _EmptyMeteo(), _DefaultCfg()  # type: ignore[arg-type]
        )
    return _PROFILE_CACHE[name]


def _resample_array(arr: "np.ndarray", resolution_hours: float) -> "np.ndarray":  # type: ignore[name-defined]
    """Resample a demand array (time × nodes) to target temporal resolution.

    Sub-hourly  (res < 1 h): linear interpolation — smooth, energy-conserving.
    Super-hourly (res > 1 h): block averaging   — energy-conserving.
    """
    import numpy as np

    if resolution_hours == 1.0:
        return arr

    n_in = len(arr)

    if resolution_hours < 1.0:
        factor = int(round(1.0 / resolution_hours))   # 2 for 30 min, 4 for 15 min
        n_out = n_in * factor
        x_in = np.arange(n_in, dtype=np.float64)
        x_out = np.linspace(0.0, n_in - 1, n_out)
        if arr.ndim == 2:
            return np.column_stack(
                [np.interp(x_out, x_in, arr[:, j]) for j in range(arr.shape[1])]
            )
        return np.interp(x_out, x_in, arr)
    else:
        factor = int(round(resolution_hours))          # 2, 3, or 6
        n_out = n_in // factor
        if arr.ndim == 2:
            return (
                arr[: n_out * factor]
                .reshape(n_out, factor, arr.shape[1])
                .mean(axis=1)
            )
        return arr[: n_out * factor].reshape(n_out, factor).mean(axis=1)


# ──────────────────────────────────────────────────────────────────────────────
# Main Builder
# ──────────────────────────────────────────────────────────────────────────────


class DemandProfileBuilder:
    """Combine proxy data, macro indicators, and meteo to estimate demand.

    Call `build()` to produce a `DemandEstimationResult`.

    Two engines available:
      - **ML** (default if trained model exists): XGBoost 3h envelope +
        Fourier harmonic reconstruction to hourly.
      - **Archetype** (fallback): hierarchical decomposition using
        regional archetype shapes + HDD/CDD corrections.
    """

    def __init__(self, config: DemandEstimationConfig):
        self._cfg = config
        self._demand_source: str = ""
        self._warnings: list[str] = []

    def build(
        self,
        proxy: ProxyData,
        macro: MacroData,
        meteo: MeteoData,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> DemandEstimationResult:
        """Dispatch to ML or archetype engine."""
        if not self._cfg.force_archetype and self._ml_available():
            try:
                return self._build_ml(proxy, macro, meteo, progress_callback)
            except Exception as exc:
                logger.warning("ML engine failed, falling back to archetype: %s", exc)
                self._warnings.append(f"ML fallback to archetype: {exc}")
        return self._build_archetype(proxy, macro, meteo, progress_callback)

    @staticmethod
    def _ml_available() -> bool:
        try:
            from esfex.models.demand_ml import DemandMLModel
            return DemandMLModel.is_available()
        except ImportError:
            return False

    # ── ML Engine ────────────────────────────────────────────────────────────

    def _build_ml(
        self,
        proxy: ProxyData,
        macro: MacroData,
        meteo: MeteoData,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> DemandEstimationResult:
        """ML pipeline: predict hourly shape factors directly."""
        import numpy as np
        from esfex.models.demand_ml import DemandMLModel, build_hourly_features

        def emit(pct: int, msg: str) -> None:
            if progress_callback:
                progress_callback(pct, msg)

        hpy = self._cfg.hours_per_year
        num_nodes = self._cfg.num_nodes
        if num_nodes <= 0:
            num_nodes = max(len(proxy.node_lats), len(proxy.building_weights), 1)
            self._cfg.num_nodes = num_nodes
        years = self._cfg.simulation_years

        engine_map = {"auto": "auto", "tft": "tft", "xgboost": "xgboost", "ml": "xgboost"}
        engine = engine_map.get(getattr(self._cfg, "ml_engine", "auto"), "auto")

        emit(5, f"Loading ML model ({engine})…")
        model = DemandMLModel.load_bundled(engine=engine)

        emit(10, "Computing spatial weights…")
        w_spatial = self._compute_spatial_weights(proxy)

        emit(15, "Estimating annual demand…")
        base_annual_mwh = self._estimate_annual_demand(w_spatial, proxy, macro)
        base_annual_mwh = self._calibrate_annual(base_annual_mwh)
        annual_traj = self._compute_annual_trajectory(base_annual_mwh, macro, proxy)

        # Hourly temperature
        emit(25, "Preparing features…")
        if len(meteo.temperature_hourly) >= hpy:
            temp_h = np.array(meteo.temperature_hourly[:hpy], dtype=np.float64)
        else:
            temp_h = np.zeros(hpy, dtype=np.float64)

        # Per-node ML inference (hourly, no Fourier)
        emit(35, f"ML inference ({model.engine})…")
        hourly_all = np.zeros((years, hpy, num_nodes), dtype=np.float64)

        for ni in range(num_nodes):
            node_pop = macro.population * w_spatial[ni]
            lat = proxy.node_lats[ni] if ni < len(proxy.node_lats) else 0.0
            lon = proxy.node_lons[ni] if ni < len(proxy.node_lons) else 0.0

            features = build_hourly_features(
                gdp_per_capita=macro.gdp_per_capita,
                population=node_pop,
                urbanization_pct=macro.urbanization_pct,
                electricity_access_pct=macro.electricity_access_pct,
                temperature_hourly=temp_h,
                latitude=lat,
                longitude=lon,
                base_year=self._cfg.base_year,
                simulation_years=years,
                hdd_base=self._cfg.hdd_base_temp,
                cdd_base=self._cfg.cdd_base_temp,
                gdp_growth_by_year=macro.gdp_growth_by_year or None,
                pop_growth_by_year=macro.pop_growth_by_year or None,
            )

            # Predict hourly shape factors
            shape_factors = model.predict(features)  # (years * 8760,)

            # Convert to MW per year
            for y in range(years):
                annual_avg_mw = annual_traj[y, ni] / hpy
                yr_start = y * hpy
                yr_end = yr_start + hpy
                hourly_all[y, :, ni] = shape_factors[yr_start:yr_end] * annual_avg_mw

            emit_pct = 35 + int(55 * (ni + 1) / num_nodes)
            emit(emit_pct, f"Node {ni + 1}/{num_nodes} complete")

        # Extract results
        demand = hourly_all[0]
        demand_my = hourly_all.reshape(-1, num_nodes)

        emit(92, "Computing metrics…")
        result = self._compute_metrics(demand, demand_my, w_spatial)
        result.config = self._cfg
        result.resolution_hours = self._cfg.resolution_hours
        self._demand_source = "ml_xgboost"
        result.demand_source = self._demand_source
        result.warnings = list(self._warnings)
        result.level0_annual_mwh_by_year = [
            float(annual_traj[y].sum()) for y in range(annual_traj.shape[0])
        ]

        if self._cfg.resolution_hours != 1.0:
            result.demand = _resample_array(result.demand, self._cfg.resolution_hours)
            if result.demand_multi_year is not None:
                result.demand_multi_year = _resample_array(
                    result.demand_multi_year, self._cfg.resolution_hours
                )

        emit(100, "ML demand estimation complete.")
        return result

    # ── Archetype Engine (original pipeline) ─────────────────────────────────

    def _build_archetype(
        self,
        proxy: ProxyData,
        macro: MacroData,
        meteo: MeteoData,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> DemandEstimationResult:
        """Hierarchical archetype-based estimation pipeline.

        Multi-resolution decomposition:
          Level 0  Annual energy      ← GDP elasticity + logistic saturation
          Level 1  Monthly distrib.   ← ERA5/CMIP6 HDD/CDD
          Level 2  Daily distrib.     ← Day-of-week factors
          Level 3  Hourly shape       ← Sectoral-blended archetype shapes
          Level 4  Weather perturb.   ← Hourly HDD/CDD anomalies

        Each level conserves the energy from the level above.
        """
        import numpy as np

        def emit(pct: int, msg: str) -> None:
            if progress_callback:
                progress_callback(pct, msg)

        hpy = self._cfg.hours_per_year

        num_nodes = self._cfg.num_nodes
        if num_nodes <= 0:
            num_nodes = max(
                len(proxy.node_lats),
                len(proxy.building_weights),
                1,
            )
            self._cfg.num_nodes = num_nodes

        archetype = ARCHETYPE_LIBRARY.get(
            self._cfg.reference_profile, _TROPICAL_ISLAND
        )

        emit(5, "Computing spatial weights…")
        w_spatial = self._compute_spatial_weights(proxy)

        emit(15, "Estimating annual demand…")
        base_annual_mwh = self._estimate_annual_demand(w_spatial, proxy, macro)

        emit(20, "Calibrating base-year estimate…")
        base_annual_mwh = self._calibrate_annual(base_annual_mwh)

        emit(30, "Level 0: Annual growth trajectory…")
        annual_traj = self._compute_annual_trajectory(base_annual_mwh, macro, proxy)

        emit(45, "Level 1: Monthly distribution…")
        monthly = self._distribute_monthly(annual_traj, meteo, archetype)

        emit(55, "Level 2: Daily distribution…")
        daily = self._distribute_daily(monthly, archetype)

        emit(65, "Level 3: Hourly shapes…")
        hourly = self._distribute_hourly(daily, archetype, proxy)

        emit(75, "Level 4: Weather perturbation…")
        hourly = self._apply_weather_perturbation(hourly, monthly, meteo, archetype)

        demand = hourly[0]
        demand_my = hourly.reshape(-1, num_nodes)

        emit(85, "Computing metrics…")
        result = self._compute_metrics(demand, demand_my, w_spatial)
        result.config = self._cfg
        result.resolution_hours = self._cfg.resolution_hours
        result.demand_source = self._demand_source
        result.warnings = list(self._warnings)
        result.level0_annual_mwh_by_year = [
            float(annual_traj[y].sum()) for y in range(annual_traj.shape[0])
        ]

        if self._cfg.resolution_hours != 1.0:
            res_label = (
                f"{int(round(self._cfg.resolution_hours * 60))} min"
                if self._cfg.resolution_hours < 1.0
                else f"{int(self._cfg.resolution_hours)} h"
            )
            emit(95, f"Resampling to {res_label} resolution…")
            result.demand = _resample_array(result.demand, self._cfg.resolution_hours)
            if result.demand_multi_year is not None:
                result.demand_multi_year = _resample_array(
                    result.demand_multi_year, self._cfg.resolution_hours
                )

        emit(100, "Demand estimation complete.")
        return result

    # ── Step 1: Spatial Weights ──────────────────────────────────────────────

    def _compute_spatial_weights(self, proxy: ProxyData) -> "np.ndarray":
        """Combine proxy datasets into per-node demand weights.

        Dispatches to the method chosen in DemandEstimationConfig.weight_method:
          manual  — user-supplied scalar weights (normalised automatically)
          equal   — equal weight for every available proxy
          entropy — CRITIC entropy method: w ∝ (1 − normalised Shannon entropy)
          pca     — PC1-loading magnitude as proxy importance
        """
        import numpy as np

        n = self._cfg.num_nodes

        raw: dict[str, tuple[float, list]] = {
            "buildings":  (self._cfg.weight_buildings,  proxy.building_weights),
            "population": (self._cfg.weight_population, proxy.population_weights),
            "nightlights":(self._cfg.weight_nightlights,proxy.nightlight_weights),
            "landuse":    (self._cfg.weight_landuse,     proxy.landuse_weights),
        }

        def _norm_vec(data: list) -> Optional["np.ndarray"]:
            """Pad / normalise proxy vector to length n, return None if empty/zero."""
            if not data or len(data) == 0:
                return None
            arr = np.array(data[:n], dtype=np.float64)
            if len(arr) < n:
                arr = np.pad(arr, (0, n - len(arr)), constant_values=arr.mean())
            s = arr.sum()
            if s <= 0:
                return None  # all-zero proxy → discard, don't fake uniform
            return arr / s

        # Build dict of available normalised vectors
        vecs: dict[str, "np.ndarray"] = {
            k: v for k, (_, data) in raw.items() if (v := _norm_vec(data)) is not None
        }

        if not vecs:
            logger.info(
                "No spatial proxy data — using uniform distribution across %d nodes.", n,
            )
            return np.ones(n) / n

        method = self._cfg.weight_method

        # ── Manual ────────────────────────────────────────────────────────────
        if method == "manual":
            combined = np.zeros(n)
            total_w = 0.0
            for k, arr in vecs.items():
                w = raw[k][0]
                combined += w * arr
                total_w += w
            if total_w > 0:
                combined /= total_w
            else:
                # All manual weights are zero — fall through to equal
                combined = sum(vecs.values()) / len(vecs)

        # ── Equal ─────────────────────────────────────────────────────────────
        elif method == "equal":
            combined = sum(vecs.values()) / len(vecs)

        # ── Entropy (CRITIC degree-of-variation method) ────────────────────────
        elif method == "entropy":
            EPS = 1e-12
            k = len(vecs)
            if n < 2 or k == 0:
                logger.info("Entropy method needs ≥2 nodes and ≥1 proxy; using equal weights.")
                combined = sum(vecs.values()) / max(k, 1)
            else:
                ln_n = math.log(n)
                degrees = {}
                for name, arr in vecs.items():
                    # Normalise so it sums to 1 (already done), treat as probability
                    p = np.clip(arr, EPS, None)
                    p = p / p.sum()
                    entropy = -np.sum(p * np.log(p)) / ln_n   # in [0, 1]
                    degrees[name] = 1.0 - entropy               # variation degree
                total_d = sum(degrees.values())
                if total_d <= 0:
                    logger.info("Entropy variation degrees all zero; using equal weights.")
                    combined = sum(vecs.values()) / k
                else:
                    combined = sum(
                        (degrees[name] / total_d) * arr
                        for name, arr in vecs.items()
                    )

        # ── PCA (PC1 loading magnitudes) ──────────────────────────────────────
        elif method == "pca":
            k = len(vecs)
            if n < 2 or k < 2:
                logger.info("PCA needs ≥2 nodes and ≥2 proxies; using equal weights.")
                combined = sum(vecs.values()) / max(k, 1)
            else:
                X = np.column_stack(list(vecs.values()))   # (n, k)
                # Standardise columns (z-score)
                mu = X.mean(axis=0)
                sigma = X.std(axis=0)
                sigma[sigma == 0] = 1.0
                Xz = (X - mu) / sigma
                # Covariance matrix and eigen-decomposition
                C = Xz.T @ Xz / (n - 1)           # (k, k)
                eigenvalues, eigenvectors = np.linalg.eigh(C)
                # PC1 = eigenvector with largest eigenvalue
                pc1 = eigenvectors[:, np.argmax(eigenvalues)]
                loadings = np.abs(pc1)
                load_sum = loadings.sum()
                if load_sum <= 0:
                    logger.info("PCA loadings all zero; using equal weights.")
                    combined = sum(vecs.values()) / k
                else:
                    names = list(vecs.keys())
                    combined = sum(
                        (loadings[i] / load_sum) * vecs[names[i]]
                        for i in range(k)
                    )

        else:
            raise ValueError(
                f"Unknown weight_method '{method}'. "
                "Valid options: manual, equal, entropy, pca."
            )

        s = combined.sum()
        if s <= 0:
            raise ValueError(
                "Spatial weight computation resulted in all-zero weights. "
                "Check proxy data and manual weight settings."
            )
        return combined / s

    # ── Step 2: Annual Demand ────────────────────────────────────────────────

    def _estimate_annual_demand(
        self,
        spatial_weights: "np.ndarray",
        proxy: ProxyData,
        macro: MacroData,
    ) -> "np.ndarray":
        """Estimate annual demand per node in MWh (top-down).

        D_national → split by spatial_weights.
        If national unknown → estimate from electricity consumption/capita.
        """
        import numpy as np

        n = self._cfg.num_nodes

        total_gwh = self._cfg.national_demand_gwh
        if total_gwh > 0:
            self._demand_source = "user_override"
            logger.info(
                "Annual demand from user override: %.1f GWh", total_gwh,
            )
        if total_gwh <= 0:
            # Estimate from macro data
            kwh_cap = macro.electric_consumption_kwh_capita
            pop = macro.population
            # 0 means unknown → assume full access
            access = macro.electricity_access_pct / 100.0 if macro.electricity_access_pct > 0 else 1.0
            if kwh_cap > 0 and pop > 0:
                total_gwh = kwh_cap * pop * access / 1e6   # kWh → GWh
                self._demand_source = "macro"
                logger.info(
                    "Annual demand from kWh/capita: %.0f kWh × %.0f pop × %.2f access = %.1f GWh",
                    kwh_cap, pop, access, total_gwh,
                )
            elif macro.gdp_per_capita > 0 and pop > 0:
                # Kaya-style estimate using configurable kWh/USD factor
                total_gwh = macro.gdp_per_capita * pop * self._cfg.gdp_kwh_per_usd / 1e6
                self._demand_source = "gdp_estimate"
                self._warnings.append(
                    f"No electricity consumption data — estimated from GDP: "
                    f"${macro.gdp_per_capita:.0f}/cap × {pop:.0f} pop → {total_gwh:.0f} GWh"
                )
                logger.info(
                    "Annual demand from GDP estimate: $%.0f/cap × %.0f pop = %.1f GWh",
                    macro.gdp_per_capita, pop, total_gwh,
                )
            else:
                raise ValueError(
                    "Cannot estimate demand: no macroeconomic data available "
                    f"(kWh/capita={kwh_cap}, population={pop}, "
                    f"GDP/capita={macro.gdp_per_capita}). "
                    "Please go back to Step 2 and fetch country data, "
                    "or set the annual demand manually in Step 1."
                )

        total_mwh = total_gwh * 1000.0   # GWh → MWh
        return spatial_weights * total_mwh

    # ── Calibration (annual level, before decomposition) ────────────────────

    def _calibrate_annual(self, annual_mwh: "np.ndarray") -> "np.ndarray":
        """Scale base-year annual demand to match known annual energy.

        Only scales if known_annual_gwh is set.  Peak-based calibration
        is handled post-build in the CalibrationStep where the actual
        load profile shape is available.
        """
        if self._cfg.known_annual_gwh <= 0:
            return annual_mwh
        a = annual_mwh.copy()
        total_gwh = a.sum() / 1000.0
        if total_gwh > 0:
            a *= self._cfg.known_annual_gwh / total_gwh
        return a

    # ── Level 0: Annual Growth Trajectory ─────────────────────────────────

    def _compute_time_varying_weights(
        self,
        base_weights: "np.ndarray",
        proxy: ProxyData,
        macro: MacroData,
    ) -> "np.ndarray":
        """Compute per-year spatial weights using urbanization trends.

        Classifies nodes as urban/rural by sectoral composition, then
        applies differential growth rates derived from the WB urbanization
        time series.

        Returns shape (years, num_nodes).
        """
        import numpy as np

        years = self._cfg.simulation_years
        n = self._cfg.num_nodes
        base_yr = self._cfg.base_year
        threshold = self._cfg.urban_rural_threshold

        # Classify nodes as urban/rural from sectoral fractions
        is_urban = np.ones(n, dtype=bool)  # default: urban
        if len(proxy.node_residential_fraction) == n:
            for i in range(n):
                rf = proxy.node_residential_fraction[i]
                cf = (proxy.node_commercial_fraction[i]
                      if i < len(proxy.node_commercial_fraction) else 0.0)
                is_urban[i] = (rf + cf) >= threshold

        # Derive urban/rural growth differential from WB urbanization series
        urb_ts = macro.urbanization_time_series
        if len(urb_ts) < 2:
            # No time series → static weights for all years
            return np.tile(base_weights, (years, 1))

        # Compute average annual urbanization rate from historical data
        sorted_years = sorted(urb_ts.keys())
        urb_rates = []
        for i in range(1, len(sorted_years)):
            prev = urb_ts[sorted_years[i - 1]]
            curr = urb_ts[sorted_years[i]]
            if prev > 0:
                urb_rates.append((curr / prev) - 1.0)
        if not urb_rates:
            return np.tile(base_weights, (years, 1))

        avg_urb_rate = sum(urb_rates) / len(urb_rates)

        # Urban nodes grow at population_growth + urbanization_rate
        # Rural nodes grow at population_growth - urbanization_rate (slower)
        weights = np.zeros((years, n), dtype=np.float64)
        weights[0] = base_weights
        for y in range(1, years):
            for i in range(n):
                if is_urban[i]:
                    weights[y, i] = weights[y - 1, i] * (1.0 + avg_urb_rate)
                else:
                    weights[y, i] = weights[y - 1, i] * (1.0 - avg_urb_rate)
            # Renormalize to sum = 1
            s = weights[y].sum()
            if s > 0:
                weights[y] /= s

        return weights

    def _compute_annual_trajectory(
        self,
        base_annual_mwh: "np.ndarray",
        macro: MacroData,
        proxy: ProxyData,
    ) -> "np.ndarray":
        """Compute year-specific annual demand using per-year projection rates.

        If time-varying weights are enabled (and urbanization data available),
        the spatial distribution shifts between urban and rural nodes over time
        while preserving total national demand growth.

        Returns shape (years, num_nodes) in MWh.
        """
        import numpy as np

        years = self._cfg.simulation_years
        n = self._cfg.num_nodes
        base_yr = self._cfg.base_year

        # Scalar rates from config (used when per-year dict has no entry)
        gdp_s = self._cfg.gdp_growth_rate
        elas_s = self._cfg.demand_gdp_elasticity
        eff_s = self._cfg.efficiency_improvement
        elec_s = self._cfg.electrification_growth

        # Logistic saturation state
        eff_sat = self._cfg.efficiency_saturation
        eff_level = self._cfg.efficiency_base_level
        elec_sat = self._cfg.electrification_saturation
        elec_level = self._cfg.electrification_base_level

        # Compute national-level trajectory first
        national_mwh = np.zeros(years, dtype=np.float64)
        national_mwh[0] = base_annual_mwh.sum()
        for y in range(1, years):
            year = base_yr + y
            gdp_g = macro.gdp_growth_by_year.get(year, gdp_s)
            elas = macro.elasticity_by_year.get(year, elas_s)
            eff_base = macro.efficiency_by_year.get(year, eff_s)
            elec_base = macro.electrification_by_year.get(year, elec_s)

            # Logistic saturation: rate decays as level approaches limit
            #   effective_rate = base_rate × (1 - level / saturation)
            eff_rate = eff_base * (1.0 - eff_level / eff_sat) if eff_sat > 0 else 0.0
            eff_rate = max(0.0, eff_rate)
            eff_level += eff_rate

            elec_rate = elec_base * (1.0 - elec_level / elec_sat) if elec_sat > 0 else 0.0
            elec_rate = max(0.0, elec_rate)
            elec_level += elec_rate

            factor = (1.0 + gdp_g * elas) * (1.0 - eff_rate) * (1.0 + elec_rate)
            national_mwh[y] = national_mwh[y - 1] * factor

        # Compute spatial weights per year
        base_weights = base_annual_mwh / base_annual_mwh.sum() if base_annual_mwh.sum() > 0 else np.ones(n) / n

        if self._cfg.enable_time_varying_weights:
            yr_weights = self._compute_time_varying_weights(
                base_weights, proxy, macro,
            )
        else:
            yr_weights = np.tile(base_weights, (years, 1))

        # Distribute national demand using per-year weights
        traj = np.zeros((years, n), dtype=np.float64)
        for y in range(years):
            traj[y] = national_mwh[y] * yr_weights[y]

        return traj

    # ── Level 1: Monthly Distribution (ERA5 temperature) ──────────────────

    def _distribute_monthly(
        self,
        annual_mwh: "np.ndarray",
        meteo: MeteoData,
        archetype: "ArchetypeProfile",
    ) -> "np.ndarray":
        """Distribute annual energy across 12 months.

        Uses archetype monthly_factors as prior, refined by HDD/CDD data.
        If per-year climate data is available (meteo.hdd_by_year /
        cdd_by_year, e.g. from CMIP6), each simulation year gets its own
        monthly factors.  Otherwise the base-year ERA5 data is reused.

        Returns shape (years, 12, num_nodes) in MWh.
        """
        import numpy as np

        years, n = annual_mwh.shape
        hpy = self._cfg.hours_per_year
        base_yr = self._cfg.base_year
        month_hours = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]
        avg_mh = sum(month_hours) / 12.0

        base_mf = np.array(archetype.monthly_factors, dtype=np.float64)
        alpha_hdd = archetype.hdd_beta / avg_mh
        alpha_cdd = archetype.cdd_beta / avg_mh

        def _monthly_factors_from_climate(hdd_h, cdd_h):
            """Compute refined monthly factors from hourly HDD/CDD arrays."""
            hdd_monthly = np.zeros(12)
            cdd_monthly = np.zeros(12)
            h = 0
            for m, mh in enumerate(month_hours):
                hdd_monthly[m] = hdd_h[h:h + mh].sum()
                cdd_monthly[m] = cdd_h[h:h + mh].sum()
                h += mh
            hdd_dev = hdd_monthly - hdd_monthly.mean()
            cdd_dev = cdd_monthly - cdd_monthly.mean()
            mf = base_mf * (1.0 + alpha_hdd * hdd_dev + alpha_cdd * cdd_dev)
            mf = np.clip(
                mf,
                self._cfg.monthly_climate_clip_min,
                self._cfg.monthly_climate_clip_max,
            )
            s = mf.sum()
            return mf / s if s > 0 else base_mf / base_mf.sum()

        # Base-year climate factors (from ERA5)
        has_base_meteo = (
            len(meteo.hdd_hourly) >= hpy and len(meteo.cdd_hourly) >= hpy
        )
        if has_base_meteo:
            base_climate_mf = _monthly_factors_from_climate(
                np.array(meteo.hdd_hourly[:hpy], dtype=np.float64),
                np.array(meteo.cdd_hourly[:hpy], dtype=np.float64),
            )
        else:
            # Pure archetype factors (no climate correction)
            s = base_mf.sum()
            base_climate_mf = base_mf / s if s > 0 else np.ones(12) / 12.0

        # Per-node base-year climate (if available)
        has_node_meteo = len(meteo.node_hdd_hourly) > 0
        node_base_mf: dict[int, "np.ndarray"] = {}
        if has_node_meteo:
            for ni in range(n):
                nh = meteo.node_hdd_hourly.get(ni)
                nc = meteo.node_cdd_hourly.get(ni)
                if nh is not None and nc is not None and len(nh) >= hpy:
                    node_base_mf[ni] = _monthly_factors_from_climate(
                        np.array(nh[:hpy], dtype=np.float64),
                        np.array(nc[:hpy], dtype=np.float64),
                    )

        monthly = np.zeros((years, 12, n), dtype=np.float64)
        for y in range(years):
            cal_year = base_yr + y
            for ni in range(n):
                # Priority: per-node per-year > per-node base > system per-year > system base
                yr_node_hdd = meteo.node_hdd_by_year.get(ni, {}).get(cal_year)
                yr_node_cdd = meteo.node_cdd_by_year.get(ni, {}).get(cal_year)
                if yr_node_hdd is not None and yr_node_cdd is not None and len(yr_node_hdd) >= hpy:
                    mf_ni = _monthly_factors_from_climate(
                        np.array(yr_node_hdd[:hpy], dtype=np.float64),
                        np.array(yr_node_cdd[:hpy], dtype=np.float64),
                    )
                elif ni in node_base_mf:
                    mf_ni = node_base_mf[ni]
                else:
                    # System-level (per-year or base)
                    yr_hdd = meteo.hdd_by_year.get(cal_year)
                    yr_cdd = meteo.cdd_by_year.get(cal_year)
                    if yr_hdd is not None and yr_cdd is not None and len(yr_hdd) >= hpy:
                        mf_ni = _monthly_factors_from_climate(
                            np.array(yr_hdd[:hpy], dtype=np.float64),
                            np.array(yr_cdd[:hpy], dtype=np.float64),
                        )
                    else:
                        mf_ni = base_climate_mf
                for m in range(12):
                    monthly[y, m, ni] = annual_mwh[y, ni] * mf_ni[m]

        return monthly

    # ── Level 2: Daily Distribution (day of week) ─────────────────────────

    def _distribute_daily(
        self,
        monthly_mwh: "np.ndarray",
        archetype: "ArchetypeProfile",
    ) -> "np.ndarray":
        """Distribute monthly energy across days within each month.

        Uses day-of-week factors: weekday > saturday > sunday.
        Normalizes within each month to conserve energy.

        Returns shape (years, 365, num_nodes) in MWh.
        """
        import numpy as np

        years, _, n = monthly_mwh.shape
        month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

        # Day-of-week factors from archetype shapes
        # Extract relative energy ratios from weekday/sat/sun shapes
        _season = "shoulder"
        wd_shape = archetype.shapes.get(f"{_season}_weekday")
        sa_shape = archetype.shapes.get(f"{_season}_saturday")
        su_shape = archetype.shapes.get(f"{_season}_sunday")
        if wd_shape and sa_shape and su_shape:
            wd_energy = sum(wd_shape)
            sa_energy = sum(sa_shape)
            su_energy = sum(su_shape)
            avg = (wd_energy * 5 + sa_energy + su_energy) / 7
            if avg > 0:
                dow_factor = {
                    0: wd_energy / avg, 1: wd_energy / avg,
                    2: wd_energy / avg, 3: wd_energy / avg,
                    4: wd_energy / avg,
                    5: sa_energy / avg,
                    6: su_energy / avg,
                }
            else:
                dow_factor = {d: 1.0 for d in range(7)}
        else:
            dow_factor = {d: 1.0 for d in range(7)}

        daily = np.zeros((years, 365, n), dtype=np.float64)

        for y in range(years):
            # Determine day-of-week for January 1 of this year
            cal_year = self._cfg.base_year + y
            # Zeller-like: Jan 1 day-of-week (0=Mon)
            import datetime
            jan1_dow = datetime.date(cal_year, 1, 1).weekday()

            day_idx = 0
            for m in range(12):
                days_in_m = month_days[m]
                # Compute raw weights for each day in this month
                raw = np.array([
                    dow_factor[(jan1_dow + day_idx + d) % 7]
                    for d in range(days_in_m)
                ])
                raw_sum = raw.sum()
                if raw_sum > 0:
                    fractions = raw / raw_sum
                else:
                    fractions = np.ones(days_in_m) / days_in_m

                for d in range(days_in_m):
                    daily[y, day_idx + d, :] = monthly_mwh[y, m, :] * fractions[d]
                day_idx += days_in_m

        return daily

    # ── Level 3: Hourly Shape (archetype intraday) ────────────────────────

    def _distribute_hourly(
        self,
        daily_mwh: "np.ndarray",
        archetype: "ArchetypeProfile",
        proxy: ProxyData,
    ) -> "np.ndarray":
        """Distribute daily energy across 24 hours using archetype shapes.

        If per-node sectoral fractions are available (from land-use data),
        each node gets a blended hourly shape:
            shape[node] = res × archetype + ind × flat_baseload + com × average

        Returns shape (years, hours_per_year, num_nodes) in MW (power).
        """
        import numpy as np

        hpy = self._cfg.hours_per_year
        years, _, n = daily_mwh.shape

        def _build_shape_matrix(arch: "ArchetypeProfile") -> "np.ndarray":
            """Build (3 seasons × 3 day_types × 24 hours) normalized shape."""
            season_names = ["winter", "shoulder", "summer"]
            daytype_names = ["weekday", "saturday", "sunday"]
            sm = np.ones((3, 3, 24), dtype=np.float64)
            for si, sn in enumerate(season_names):
                for di, dn in enumerate(daytype_names):
                    key = f"{sn}_{dn}"
                    sh = (arch.shapes.get(key)
                          or arch.shapes.get(f"shoulder_{dn}")
                          or arch.shapes.get("shoulder_weekday"))
                    if sh is not None:
                        arr = np.array(sh, dtype=np.float64)
                        s = arr.sum()
                        if s > 0:
                            sm[si, di, :] = arr / s * 24.0
                        else:
                            sm[si, di, :] = 1.0
            return sm

        # Build shape matrices: system archetype + flat baseload
        sm_arch = _build_shape_matrix(archetype)
        flat = ARCHETYPE_LIBRARY.get("flat_baseload")
        sm_flat = _build_shape_matrix(flat) if flat else np.ones((3, 3, 24))

        # Per-node blended shape matrices: (n, 3, 3, 24)
        has_sectoral = (
            len(proxy.node_residential_fraction) == n
            and len(proxy.node_industrial_fraction) == n
        )
        if has_sectoral:
            node_shapes = np.zeros((n, 3, 3, 24), dtype=np.float64)
            for i in range(n):
                rf = proxy.node_residential_fraction[i]
                inf = proxy.node_industrial_fraction[i]
                cf = proxy.node_commercial_fraction[i] if (
                    len(proxy.node_commercial_fraction) > i
                ) else max(0.0, 1.0 - rf - inf)
                # Normalize fractions
                total_f = rf + inf + cf
                if total_f > 0:
                    rf /= total_f
                    inf /= total_f
                    cf /= total_f
                else:
                    rf, inf, cf = 1.0, 0.0, 0.0
                # Blend: residential→archetype, industrial→flat,
                #         commercial→midpoint
                node_shapes[i] = (
                    rf * sm_arch
                    + inf * sm_flat
                    + cf * 0.5 * (sm_arch + sm_flat)
                )
        else:
            # All nodes use system archetype (backward compatible)
            node_shapes = np.tile(sm_arch, (n, 1, 1, 1))

        # Month → season mapping (from system archetype)
        month_to_season = np.zeros(12, dtype=int)
        for m in range(12):
            if m in archetype.winter_months:
                month_to_season[m] = 0
            elif m in archetype.summer_months:
                month_to_season[m] = 2
            else:
                month_to_season[m] = 1

        hourly = np.zeros((years, hpy, n), dtype=np.float64)
        month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

        for y in range(years):
            import datetime
            cal_year = self._cfg.base_year + y
            jan1_dow = datetime.date(cal_year, 1, 1).weekday()

            day_idx = 0
            for m in range(12):
                si = month_to_season[m]
                for d in range(month_days[m]):
                    global_day = day_idx + d
                    dow = (jan1_dow + global_day) % 7
                    if dow == 5:
                        di = 1
                    elif dow == 6:
                        di = 2
                    else:
                        di = 0

                    h_start = global_day * 24
                    h_end = h_start + 24
                    for ni in range(n):
                        shape_24 = node_shapes[ni, si, di, :]
                        hourly[y, h_start:h_end, ni] = (
                            daily_mwh[y, global_day, ni] * shape_24 / 24.0
                        )
                day_idx += month_days[m]

        return hourly

    # ── Level 4: Hourly Weather Perturbation ──────────────────────────────

    def _apply_weather_perturbation(
        self,
        hourly_mw: "np.ndarray",
        monthly_mwh: "np.ndarray",
        meteo: MeteoData,
        archetype: "ArchetypeProfile",
    ) -> "np.ndarray":
        """Apply hourly weather correction using HDD/CDD ANOMALIES.

        Uses deviations from monthly mean to avoid double-counting with
        Level 1.  Renormalizes within each month for strict energy
        conservation.

        If no meteo data available, returns input unchanged.
        """
        import numpy as np

        hpy = self._cfg.hours_per_year
        base_yr = self._cfg.base_year
        has_base = len(meteo.hdd_hourly) >= hpy and len(meteo.cdd_hourly) >= hpy

        if not has_base and not meteo.hdd_by_year:
            return hourly_mw

        beta_hdd = self._cfg.heating_sensitivity or archetype.hdd_beta
        beta_cdd = self._cfg.cooling_sensitivity or archetype.cdd_beta
        if beta_hdd == 0 and beta_cdd == 0:
            return hourly_mw

        years, _, n = hourly_mw.shape
        month_hours = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]

        # Base-year perturbation (reused for years without specific data)
        base_hdd = np.array(meteo.hdd_hourly[:hpy], dtype=np.float64) if has_base else None
        base_cdd = np.array(meteo.cdd_hourly[:hpy], dtype=np.float64) if has_base else None

        def _compute_perturbation(hdd_h, cdd_h):
            hdd_mm = np.zeros(hpy)
            cdd_mm = np.zeros(hpy)
            h = 0
            for m_idx, mh in enumerate(month_hours):
                hdd_mm[h:h + mh] = hdd_h[h:h + mh].mean()
                cdd_mm[h:h + mh] = cdd_h[h:h + mh].mean()
                h += mh
            p = 1.0 + beta_hdd * (hdd_h - hdd_mm) + beta_cdd * (cdd_h - cdd_mm)
            return np.clip(
                p,
                self._cfg.hourly_perturbation_clip_min,
                self._cfg.hourly_perturbation_clip_max,
            )

        # Per-node base-year climate
        has_node_meteo = len(meteo.node_hdd_hourly) > 0
        node_base_hdd: dict[int, "np.ndarray"] = {}
        node_base_cdd: dict[int, "np.ndarray"] = {}
        if has_node_meteo:
            for ni in range(n):
                nh = meteo.node_hdd_hourly.get(ni)
                nc = meteo.node_cdd_hourly.get(ni)
                if nh is not None and nc is not None and len(nh) >= hpy:
                    node_base_hdd[ni] = np.array(nh[:hpy], dtype=np.float64)
                    node_base_cdd[ni] = np.array(nc[:hpy], dtype=np.float64)

        def _get_node_climate(ni, cal_year):
            """Resolve climate data for a specific node and year."""
            # Per-node per-year (CMIP6 per-site)
            yr_nh = meteo.node_hdd_by_year.get(ni, {}).get(cal_year)
            yr_nc = meteo.node_cdd_by_year.get(ni, {}).get(cal_year)
            if yr_nh is not None and yr_nc is not None and len(yr_nh) >= hpy:
                return (np.array(yr_nh[:hpy], dtype=np.float64),
                        np.array(yr_nc[:hpy], dtype=np.float64))
            # Per-node base
            if ni in node_base_hdd:
                return node_base_hdd[ni], node_base_cdd[ni]
            # System per-year
            yr_hdd = meteo.hdd_by_year.get(cal_year)
            yr_cdd = meteo.cdd_by_year.get(cal_year)
            if yr_hdd is not None and yr_cdd is not None and len(yr_hdd) >= hpy:
                return (np.array(yr_hdd[:hpy], dtype=np.float64),
                        np.array(yr_cdd[:hpy], dtype=np.float64))
            # System base
            if base_hdd is not None:
                return base_hdd, base_cdd
            return None, None

        result = hourly_mw.copy()
        for y in range(years):
            cal_year = base_yr + y

            if has_node_meteo or meteo.node_hdd_by_year:
                # Per-node perturbation
                for ni in range(n):
                    h_ni, c_ni = _get_node_climate(ni, cal_year)
                    if h_ni is None:
                        continue
                    pert_ni = _compute_perturbation(h_ni, c_ni)
                    result[y, :, ni] *= pert_ni
            else:
                # System-level perturbation (broadcast to all nodes)
                yr_hdd = meteo.hdd_by_year.get(cal_year)
                yr_cdd = meteo.cdd_by_year.get(cal_year)
                if yr_hdd is not None and yr_cdd is not None and len(yr_hdd) >= hpy:
                    pert = _compute_perturbation(
                        np.array(yr_hdd[:hpy], dtype=np.float64),
                        np.array(yr_cdd[:hpy], dtype=np.float64),
                    )
                elif base_hdd is not None:
                    pert = _compute_perturbation(base_hdd, base_cdd)
                else:
                    continue
                result[y] *= pert[:, np.newaxis]

            # Renormalize within each month to preserve Level 1 energy
            h = 0
            for m_idx, mh in enumerate(month_hours):
                block = result[y, h:h + mh, :]
                current_energy = block.sum(axis=0)
                target_energy = monthly_mwh[y, m_idx, :]
                mask = current_energy > 0
                block[:, mask] *= (target_energy[mask] / current_energy[mask])
                h += mh

        return result

    # ── Step 6: Metrics ──────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        demand: "np.ndarray",
        demand_my: "np.ndarray",
        spatial_weights: "np.ndarray",
    ) -> DemandEstimationResult:
        import numpy as np

        n = demand.shape[1] if demand.ndim > 1 else 1

        peak_mw: list[float] = []
        annual_gwh: list[float] = []
        load_factor_list: list[float] = []

        for ni in range(n):
            col = demand[:, ni] if demand.ndim > 1 else demand
            pk = float(col.max())
            an = float(col.sum() / 1000.0)   # MWh → GWh
            lf = float(col.mean() / pk) if pk > 0 else 0.0
            peak_mw.append(pk)
            annual_gwh.append(an)
            load_factor_list.append(lf)

        total_demand = demand.sum(axis=1) if demand.ndim > 1 else demand
        total_peak = float(total_demand.max())
        total_annual = float(total_demand.sum() / 1000.0)   # GWh
        total_lf = float(total_demand.mean() / total_peak) if total_peak > 0 else 0.0

        # Monthly GWh (base year)
        month_hours = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]
        monthly_gwh: list[float] = []
        h = 0
        for mh in month_hours:
            end = min(h + mh, len(total_demand))
            monthly_gwh.append(float(total_demand[h:end].sum() / 1000.0))
            h = end

        # Duration curve
        duration = np.sort(total_demand)[::-1]

        return DemandEstimationResult(
            demand=demand,
            demand_multi_year=demand_my,
            total_peak_mw=total_peak,
            total_annual_gwh=total_annual,
            total_load_factor=total_lf,
            peak_mw=peak_mw,
            annual_gwh=annual_gwh,
            load_factor=load_factor_list,
            spatial_weights=spatial_weights.tolist(),
            monthly_gwh=monthly_gwh,
            duration_curve=duration,
        )


# ──────────────────────────────────────────────────────────────────────────────
# QThread Worker
# ──────────────────────────────────────────────────────────────────────────────


class DemandEstimationWorker(QThread):
    """Background worker that runs DemandProfileBuilder in a separate thread."""

    progress = Signal(int, str)
    finished = Signal(object)    # DemandEstimationResult
    error = Signal(str)

    def __init__(
        self,
        config: DemandEstimationConfig,
        proxy: ProxyData,
        macro: MacroData,
        meteo: MeteoData,
        parent=None,
    ):
        super().__init__(parent)
        self._config = config
        self._proxy = proxy
        self._macro = macro
        self._meteo = meteo
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            builder = DemandProfileBuilder(self._config)
            result = builder.build(
                self._proxy,
                self._macro,
                self._meteo,
                progress_callback=self._emit_progress,
            )
            if not self._cancelled:
                self.finished.emit(result)
        except Exception as exc:
            logger.exception("DemandEstimationWorker error")
            self.error.emit(str(exc))

    def _emit_progress(self, pct: int, msg: str) -> None:
        if not self._cancelled:
            self.progress.emit(pct, msg)
