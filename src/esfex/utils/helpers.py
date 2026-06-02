"""
Helper functions for ESFEX optimization.

Provides utility functions for:
- System and unit configuration gathering
- Boundary conditions management
- Rolling horizon support
- Results consolidation
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class BoundaryConditions:
    """Boundary conditions for rolling horizon optimization."""

    battery_soc: Dict[int, Dict[int, float]] = field(default_factory=dict)
    generator_status: Dict[int, Dict[int, int]] = field(default_factory=dict)
    ev_soc: Dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "battery_soc": self.battery_soc,
            "generator_status": self.generator_status,
            "ev_soc": self.ev_soc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BoundaryConditions":
        """Create from dictionary."""
        return cls(
            battery_soc=data.get("battery_soc", {}),
            generator_status=data.get("generator_status", {}),
            ev_soc=data.get("ev_soc", {}),
        )


def initialize_battery_soc(
    batteries: List[dict],
    num_nodes: int,
) -> Dict[int, Dict[int, float]]:
    """
    Initialize battery SOC to initial values from configuration.

    Args:
        batteries: List of battery configurations
        num_nodes: Number of nodes in the system

    Returns:
        Dictionary with initial SOC values {bat_idx: {node: soc}}
    """
    battery_soc = {}
    for bat_idx, battery in enumerate(batteries):
        battery_soc[bat_idx] = {}
        for node in range(num_nodes):
            soc_initial = battery.get("soc_initial", [0.5] * num_nodes)
            if node < len(soc_initial):
                battery_soc[bat_idx][node] = soc_initial[node]
            else:
                battery_soc[bat_idx][node] = 0.5
    return battery_soc


def initialize_generator_status(
    generators: List[dict],
    num_nodes: int,
) -> Dict[int, Dict[int, int]]:
    """
    Initialize generator status based on installed capacity.

    Generators with installed capacity start ON, others start OFF.
    This avoids unrealistic startup transients at the beginning of simulation.

    Args:
        generators: List of generator configurations
        num_nodes: Number of nodes in the system

    Returns:
        Dictionary with initial status values {gen_idx: {node: status}}
    """
    generator_status = {}
    for gen_idx, gen in enumerate(generators):
        generator_status[gen_idx] = {}
        for node in range(num_nodes):
            rated_power = gen.get("rated_power", [0] * num_nodes)
            if node < len(rated_power) and rated_power[node] > 0:
                generator_status[gen_idx][node] = 1  # Start ON
            else:
                generator_status[gen_idx][node] = 0  # Start OFF
    return generator_status


def initialize_ev_soc(
    num_nodes: int,
    ev_initial_soc: Optional[List[float]] = None,
) -> Dict[int, float]:
    """
    Initialize EV SOC to default values.

    Args:
        num_nodes: Number of nodes in the system
        ev_initial_soc: Optional list of initial SOC values per node

    Returns:
        Dictionary with initial EV SOC values {node: soc}
    """
    ev_soc = {}
    for node in range(num_nodes):
        if ev_initial_soc is not None and node < len(ev_initial_soc):
            ev_soc[node] = ev_initial_soc[node]
        else:
            ev_soc[node] = 0.5  # Default to 50% SOC
    return ev_soc


def extract_inertia_limit(
    inertia_limit: dict,
    start_hour: int,
    window_hours: int,
) -> Dict[int, float]:
    """
    Extract INERTIA_LIMIT for a specific window.

    Args:
        inertia_limit: Dictionary of inertia limits by hour
        start_hour: Start hour of the window
        window_hours: Number of hours in the window

    Returns:
        Dictionary with inertia limits for the window {t: limit}
    """
    window_inertia_limit = {}

    if isinstance(inertia_limit, dict):
        for t in range(window_hours):
            window_inertia_limit[t] = inertia_limit.get(
                t + start_hour,
                inertia_limit.get(0, 0)
            )
    else:
        for t in range(window_hours):
            window_inertia_limit[t] = 0

    return window_inertia_limit


def extract_sectoral_demand(
    sectoral_demand: Optional[Dict[str, np.ndarray]],
    start_hour: int,
    end_hour: int,
) -> Optional[Dict[str, np.ndarray]]:
    """
    Extract sectoral demand for the current window.

    Args:
        sectoral_demand: Dictionary with sector demand data
        start_hour: Start hour of the window
        end_hour: End hour of the window

    Returns:
        Dictionary with extracted demand for the window
    """
    if sectoral_demand is None:
        return None

    window_sectoral_demand = {}
    for sector, demand_array in sectoral_demand.items():
        if isinstance(demand_array, np.ndarray):
            window_sectoral_demand[sector] = demand_array[start_hour:end_hour, :]

    return window_sectoral_demand


def extract_ev_profiles(
    ev_profiles: dict,
    ev_charging: Any,
    v2g_availability: Any,
    start_hour: int,
    end_hour: int,
) -> dict:
    """
    Extract EV profiles for the current window.

    Args:
        ev_profiles: Dictionary with EV profile configurations
        ev_charging: DataFrame or array with EV charging data
        v2g_availability: DataFrame or array with V2G availability data
        start_hour: Start hour of the window
        end_hour: End hour of the window

    Returns:
        Dictionary with extracted EV profiles for the window
    """
    window_ev_profiles = deepcopy(ev_profiles)

    try:
        # Adjust charging profile
        if "standard_charging" in window_ev_profiles:
            if hasattr(ev_charging, "iloc"):
                charging_df = ev_charging.iloc[start_hour:end_hour, :]
                window_ev_profiles["standard_charging"]["charging_profile"] = charging_df.values
            elif isinstance(ev_charging, np.ndarray):
                window_ev_profiles["standard_charging"]["charging_profile"] = ev_charging[start_hour:end_hour]

        # Adjust V2G availability
        if "V2G" in window_ev_profiles:
            if hasattr(v2g_availability, "iloc"):
                v2g_df = v2g_availability.iloc[start_hour:end_hour, :]
                window_ev_profiles["V2G"]["availability_profile"] = v2g_df.values
            elif isinstance(v2g_availability, np.ndarray):
                window_ev_profiles["V2G"]["availability_profile"] = v2g_availability[start_hour:end_hour]

    except Exception as e:
        print(f"Error extracting EV profiles: {e}")

    return window_ev_profiles


def extract_boundary_conditions(
    solution: dict,
    num_batteries: int,
    num_generators: int,
    num_nodes: int,
    default_battery_soc: Optional[List[dict]] = None,
    default_ev_soc: Optional[List[float]] = None,
) -> BoundaryConditions:
    """
    Extract boundary conditions from window solution for the next window.

    Args:
        solution: Solution from the current window
        num_batteries: Number of batteries
        num_generators: Number of generators
        num_nodes: Number of nodes
        default_battery_soc: Default battery SOC values
        default_ev_soc: Default EV SOC values

    Returns:
        BoundaryConditions for next window
    """
    # Extract battery SOC at the end of the window
    battery_soc = {}
    for bat_idx in range(num_batteries):
        battery_soc[bat_idx] = {}
        for node in range(num_nodes):
            if "bat_soc" in solution and bat_idx < len(solution["bat_soc"]):
                if node < len(solution["bat_soc"][bat_idx]) and solution["bat_soc"][bat_idx][node]:
                    battery_soc[bat_idx][node] = solution["bat_soc"][bat_idx][node][-1]
                elif default_battery_soc:
                    battery_soc[bat_idx][node] = default_battery_soc[bat_idx].get("soc_initial", [0.5])[node]
                else:
                    battery_soc[bat_idx][node] = 0.5
            else:
                battery_soc[bat_idx][node] = 0.5

    # Extract generator status at the end of the window
    generator_status = {}
    for gen_idx in range(num_generators):
        generator_status[gen_idx] = {}
        for node in range(num_nodes):
            if ("gen_status" in solution and
                gen_idx < len(solution["gen_status"]) and
                node < len(solution["gen_status"][gen_idx]) and
                solution["gen_status"][gen_idx][node]):
                generator_status[gen_idx][node] = solution["gen_status"][gen_idx][node][-1]
            else:
                generator_status[gen_idx][node] = 0

    # Extract EV SOC at the end of the window
    ev_soc = {}
    for node in range(num_nodes):
        if ("EV_soc" in solution and
            node < len(solution["EV_soc"]) and
            solution["EV_soc"][node]):
            ev_soc[node] = solution["EV_soc"][node][-1]
        elif default_ev_soc and node < len(default_ev_soc):
            ev_soc[node] = default_ev_soc[node]
        else:
            ev_soc[node] = 0.5

    return BoundaryConditions(
        battery_soc=battery_soc,
        generator_status=generator_status,
        ev_soc=ev_soc,
    )


def adjust_investment_limits(
    unit_data: dict,
    year: int,
    base_year: int,
    growth_rate: float = 0.5,
) -> None:
    """
    Adjust investment limits for units based on year progression.

    Args:
        unit_data: Unit configuration dictionary (modified in place)
        year: Current simulation year
        base_year: Base year for calculations
        growth_rate: Annual growth rate (default 50%)
    """
    years_diff = year - base_year
    growth_factor = (1 + growth_rate) ** years_diff

    if unit_data.get("type") in ("Renewable", "Storage"):
        if "invest_max_power" in unit_data:
            unit_data["invest_max_power"] = [
                x * growth_factor for x in unit_data["invest_max_power"]
            ]
        if unit_data.get("type") == "Storage" and "invest_max_capacity" in unit_data:
            unit_data["invest_max_capacity"] = [
                x * growth_factor for x in unit_data["invest_max_capacity"]
            ]


def adjust_transmission_parameters(
    nodes: dict,
    year: int,
    base_year: int,
    cost_reduction_rate: float = 0.03,
    capacity_growth_rate: float = 0.5,
) -> None:
    """
    Adjust transmission parameters based on year progression.

    Args:
        nodes: Node configuration dictionary (modified in place)
        year: Current simulation year
        base_year: Base year for calculations
        cost_reduction_rate: Annual cost reduction rate (default 3%)
        capacity_growth_rate: Annual capacity growth rate (default 50%)
    """
    years_diff = year - base_year
    cost_reduction = (1 - cost_reduction_rate) ** years_diff
    capacity_growth = (1 + capacity_growth_rate) ** years_diff

    if "transference_invest_cost" in nodes:
        nodes["transference_invest_cost"] = [
            x * cost_reduction for x in nodes["transference_invest_cost"]
        ]
    if "transference_invest_max" in nodes:
        nodes["transference_invest_max"] = [
            x * capacity_growth for x in nodes["transference_invest_max"]
        ]


def calculate_renewable_penetration(
    gen_output: np.ndarray,
    generators: List[dict],
) -> Tuple[float, float, float]:
    """
    Calculate renewable penetration metrics.

    Args:
        gen_output: Generation output array [gen_idx, node, hour]
        generators: List of generator configurations

    Returns:
        Tuple of (total_generation, renewable_generation, penetration_ratio)
    """
    total_generation = np.sum(gen_output)
    renewable_generation = 0.0

    for gen_idx, gen in enumerate(generators):
        if gen.get("type") == "Renewable":
            renewable_generation += np.sum(gen_output[gen_idx, :, :])

    penetration = renewable_generation / total_generation if total_generation > 0 else 0.0

    return total_generation, renewable_generation, penetration


def calculate_co2_emissions(
    gen_output: np.ndarray,
    generators: List[dict],
    fuel_co2: Dict[str, float],
) -> float:
    """
    Calculate total CO2 emissions from generation.

    Args:
        gen_output: Generation output array [gen_idx, node, hour]
        generators: List of generator configurations
        fuel_co2: CO2 emission factors by fuel type (tonnes/MWh)

    Returns:
        Total CO2 emissions in tonnes
    """
    total_emissions = 0.0

    for gen_idx, gen in enumerate(generators):
        if gen.get("type") != "Renewable":
            fuel_type = gen.get("fuel", "Natural Gas")
            co2_factor = fuel_co2.get(fuel_type, 0)
            gen_total = np.sum(gen_output[gen_idx, :, :])
            total_emissions += gen_total * co2_factor

    return total_emissions
