"""
Simulation models for ESFEX.

Provides EV charging/V2G models, rooftop solar generation models,
climate-adjusted availability profiles, and natural hazard risk assessment.
"""

from esfex.models.ev import (
    aggregate_ev_profiles,
    calculate_v2g_compensation,
    generate_electricity_prices,
    generate_ev_profiles,
    generate_v2g_availability,
    load_ev_profiles_hdf5,
    save_ev_profiles_hdf5,
)
from rooftex import calculate_potential as calculate_rooftop_potential
from rooftex import generate_profiles as generate_rooftop_solar_profiles

from esfex.models.solar_rooftop import integrate_rooftop_solar

# Climate profiles and risk assessment (lazy-load friendly — no heavy deps)
from esfex.models.climate_profiles import (
    apply_climate_deltas,
    compute_climate_demand,
    compute_solar_cf_climate,
    compute_wind_cf_climate,
    generate_scenario_profiles,
    quantile_mapping,
)
from esfex.models.hazard_assessment import (
    CompositeRiskAssessment,
    FragilityCurve,
    FragilityLibrary,
    HazardFetcher,
    HazardIntensityMap,
    NodeRiskProfile,
    ScenarioGenerator,
    create_fetcher,
)

__all__ = [
    # EV models
    "aggregate_ev_profiles",
    "calculate_v2g_compensation",
    "generate_electricity_prices",
    "generate_ev_profiles",
    "generate_v2g_availability",
    "load_ev_profiles_hdf5",
    "save_ev_profiles_hdf5",
    # Solar rooftop models
    "calculate_rooftop_potential",
    "generate_rooftop_solar_profiles",
    "integrate_rooftop_solar",
    # Climate profiles
    "quantile_mapping",
    "compute_solar_cf_climate",
    "compute_wind_cf_climate",
    "compute_climate_demand",
    "generate_scenario_profiles",
    "apply_climate_deltas",
    # Hazard assessment
    "FragilityCurve",
    "FragilityLibrary",
    "HazardFetcher",
    "HazardIntensityMap",
    "NodeRiskProfile",
    "CompositeRiskAssessment",
    "ScenarioGenerator",
    "create_fetcher",
]
