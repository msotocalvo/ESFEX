"""
EV charging, V2G, degradation, and grid impact analysis.

This module re-exports from the evrex standalone library.
"""

from evrex import (
    DEFAULT_CONNECTED_PROFILE,
    ChargingProfile,
    ChargingScenarioResult,
    DegradationResult,
    GridImpactResult,
    V2GPotential,
    assess_grid_impact,
    compute_battery_degradation,
    compute_fleet_evolution_metrics,
    compute_v2g_potential,
    generate_all_scenarios,
    generate_charging_profiles,
)

__all__ = [
    "DEFAULT_CONNECTED_PROFILE",
    "ChargingProfile",
    "ChargingScenarioResult",
    "DegradationResult",
    "GridImpactResult",
    "V2GPotential",
    "assess_grid_impact",
    "compute_battery_degradation",
    "compute_fleet_evolution_metrics",
    "compute_v2g_potential",
    "generate_all_scenarios",
    "generate_charging_profiles",
]
