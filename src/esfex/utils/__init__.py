"""
Utility functions for ESFEX.

Provides helper functions and temporal utilities.
"""

from esfex.utils.helpers import (
    BoundaryConditions,
    adjust_investment_limits,
    adjust_transmission_parameters,
    calculate_co2_emissions,
    calculate_renewable_penetration,
    extract_boundary_conditions,
    extract_ev_profiles,
    extract_inertia_limit,
    extract_sectoral_demand,
    initialize_battery_soc,
    initialize_ev_soc,
    initialize_generator_status,
)
from esfex.utils.temporal import (
    aggregate_demand_to_resolution,
    aggregate_to_resolution,
    calculate_rolling_horizon_windows,
    get_aggregated_timesteps,
    get_hours_per_year,
    validate_hourly_data,
)

__all__ = [
    # Helpers
    "BoundaryConditions",
    "adjust_investment_limits",
    "adjust_transmission_parameters",
    "calculate_co2_emissions",
    "calculate_renewable_penetration",
    "extract_boundary_conditions",
    "extract_ev_profiles",
    "extract_inertia_limit",
    "extract_sectoral_demand",
    "initialize_battery_soc",
    "initialize_ev_soc",
    "initialize_generator_status",
    # Temporal
    "aggregate_demand_to_resolution",
    "aggregate_to_resolution",
    "calculate_rolling_horizon_windows",
    "get_aggregated_timesteps",
    "get_hours_per_year",
    "validate_hourly_data",
]
