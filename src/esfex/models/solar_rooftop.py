"""
Rooftop Solar model for ESFEX.

Provides functions to generate stochastic rooftop solar availability profiles
with S-curve adoption dynamics.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def generate_rooftop_solar_profiles(
    num_nodes: int,
    hours: int = 24,
    base_year: int = 2024,
    target_year: int = 2050,
    adoption_scenario: str = "medium",
    weather_variability: str = "normal",
    seed: Optional[int] = None,
    config: Optional[dict] = None,
) -> Tuple[np.ndarray, np.ndarray, List[float]]:
    """
    Generate stochastic rooftop solar availability profiles.

    Parameters
    ----------
    num_nodes : int
        Number of nodes in the system
    hours : int
        Number of hours to simulate
    base_year : int
        Base year for adoption calculation
    target_year : int
        Target year for projections
    adoption_scenario : str
        Scenario of adoption: 'low', 'medium', 'high'
    weather_variability : str
        Level of weather variability: 'low', 'normal', 'high'
    seed : int, optional
        Seed for reproducibility
    config : dict, optional
        Configuration dictionary with custom parameters

    Returns
    -------
    tuple
        (availability_matrix, adoption_factors, max_potential)
        - availability_matrix: np.ndarray with shape (hours, num_nodes)
        - adoption_factors: np.ndarray with adoption factors by node
        - max_potential: list with maximum potential by node in MW
    """
    config = config or {}

    if seed is not None:
        np.random.seed(seed)

    # 1. Create base solar production profile
    # Use modular hour-of-day so each day gets a proper bell curve
    hours_array = np.arange(hours) % 24
    base_profile = np.zeros(hours)

    # Bell-shaped curve centered at noon (repeats daily)
    daylight_hours = np.logical_and(hours_array >= 6, hours_array <= 18)
    base_profile[daylight_hours] = np.sin(
        np.pi * (hours_array[daylight_hours] - 6) / 12
    )

    # Apply performance ratio
    performance_ratio = config.get("performance_ratio", 0.75)
    base_profile = base_profile * performance_ratio

    # 2. Weather variability parameters
    weather_variance_map = {
        "low": 0.05,
        "normal": 0.15,
        "high": 0.25,
    }
    weather_variance = weather_variance_map[weather_variability]

    node_variance_map = {
        "low": 0.10,
        "normal": 0.20,
        "high": 0.30,
    }
    node_variance = node_variance_map[weather_variability]

    # 3. Adoption parameters
    adoption_rates = config.get("adoption_rates", {
        "low": 0.05,
        "medium": 0.08,
        "high": 0.12,
    })
    adoption_rate = adoption_rates.get(adoption_scenario, 0.08)

    # Urban vs rural factors
    urbanization_factor = np.random.beta(2, 2, num_nodes)

    # 4. Calculate max potential by node
    if "systems_per_node" in config and "avg_system_size" in config:
        systems_per_node = list(config.get("systems_per_node", []))
        avg_system_size = list(config.get("avg_system_size", []))

        # Ensure we have values for all nodes
        while len(systems_per_node) < num_nodes:
            systems_per_node.append(systems_per_node[-1] if systems_per_node else 5000)
        while len(avg_system_size) < num_nodes:
            avg_system_size.append(avg_system_size[-1] if avg_system_size else 5.0)

        max_potential = [
            systems_per_node[i] * avg_system_size[i] / 1000  # Convert kW to MW
            for i in range(num_nodes)
        ]
    else:
        # Use base value with urbanization adjustment
        base_potential = 50 + np.random.gamma(shape=2.0, scale=30.0, size=num_nodes)
        max_potential = list(base_potential * (0.5 + urbanization_factor))

    # 5. Create stochastic availability matrix
    availability_matrix = np.zeros((hours, num_nodes))
    num_days = max(1, hours // 24)

    # Common daily weather component (one value per day, tiled to hours)
    daily_weather_per_day = np.random.normal(1.0, weather_variance, num_days)
    daily_weather_per_day = np.clip(daily_weather_per_day, 0.2, 1.8)
    daily_weather = np.repeat(daily_weather_per_day, 24)[:hours]

    for node in range(num_nodes):
        # Node-specific variability
        node_factor = np.random.normal(1.0, node_variance)
        node_factor = max(0.6, min(1.4, node_factor))

        hourly_noise = np.random.normal(0, 0.05, hours)

        # Model cloud patterns (per-day cloud events)
        cloud_pattern = np.zeros(hours)
        for day in range(num_days):
            if np.random.random() < 0.3:  # 30% probability of clouds per day
                cloud_start = np.random.randint(6, 16)
                cloud_duration = np.random.randint(1, 4)
                cloud_intensity = np.random.uniform(0.3, 0.7)
                day_offset = day * 24
                for h in range(cloud_start, min(cloud_start + cloud_duration, 24)):
                    idx = day_offset + h
                    if idx < hours:
                        cloud_pattern[idx] = cloud_intensity

        # Combine factors
        for h in range(hours):
            raw_value = (
                base_profile[h]
                * node_factor
                * daily_weather[h]
                * (1 - cloud_pattern[h])
                + hourly_noise[h]
            )
            availability_matrix[h, node] = max(0, min(1, raw_value))

    # 6. Calculate adoption factors
    years_diff = target_year - base_year

    initial_adoption = list(config.get("initial_adoption", [0.05] * num_nodes))
    while len(initial_adoption) < num_nodes:
        initial_adoption.append(initial_adoption[-1] if initial_adoption else 0.05)

    max_adoption_map = config.get("max_adoption", {
        "low": 0.30,
        "medium": 0.50,
        "high": 0.70,
    })
    max_adoption_val = max_adoption_map.get(adoption_scenario, 0.5)

    adoption_factors = []
    for node in range(num_nodes):
        node_max_adoption = max_adoption_val * (0.8 + 0.4 * urbanization_factor[node])
        node_max_adoption = min(0.9, node_max_adoption)

        mid_point = base_year + years_diff * (0.4 + 0.2 * np.random.random())
        growth_rate = adoption_rate * (0.8 + 0.4 * urbanization_factor[node])

        target_adoption = node_max_adoption / (
            1 + np.exp(-growth_rate * (target_year - mid_point))
        )
        adoption_factors.append(target_adoption)

    adoption_factors = np.array(adoption_factors)

    return availability_matrix, adoption_factors, max_potential


def integrate_rooftop_solar(
    units_config: Dict[str, dict],
    num_nodes: int,
    year: int,
    base_year: int,
    availability_matrix: np.ndarray,
    adoption_factors: np.ndarray,
    max_potential: List[float],
    co2_reduction: float = 0.7,
    cost_reduction_rate: float = 0.08,
    min_capacity_threshold: float = 1.0,
    config: Optional[dict] = None,
) -> Optional[dict]:
    """
    Integrate rooftop solar generation into the model.

    Parameters
    ----------
    units_config : dict
        Dictionary with unit configurations (modified in place)
    num_nodes : int
        Number of nodes
    year : int
        Current modeling year
    base_year : int
        Base year for projections
    availability_matrix : np.ndarray
        Hourly availability by node
    adoption_factors : np.ndarray
        Adoption factors by node
    max_potential : list
        Maximum potential by node in MW
    co2_reduction : float
        CO2 reduction factor vs conventional mix
    cost_reduction_rate : float
        Annual cost reduction rate
    min_capacity_threshold : float
        Minimum total capacity (MW) to add unit
    config : dict, optional
        Configuration dictionary

    Returns
    -------
    dict or None
        Rooftop solar unit configuration, or None if below threshold
    """
    config = config or {}

    years_diff = year - base_year

    # Configuration parameters
    o_and_m_cost = config.get("o_and_m_cost", 20)
    performance_ratio = config.get("performance_ratio", 0.75)
    degradation_rate = config.get("degradation_rate", 0.005)
    target_year = config.get("target_year", 2050)
    base_cost_per_kw = config.get("cost_per_kw", 1200)

    # S-curve progress factor
    progress_factor = min(1.0, years_diff / (target_year - base_year))
    s_curve_factor = 1 / (1 + np.exp(-10 * (progress_factor - 0.5)))

    current_adoption = adoption_factors * s_curve_factor

    # Calculate installed capacity
    installed_capacity = np.array(max_potential) * current_adoption

    # Apply degradation
    degradation_factor = 1.0 - (degradation_rate * years_diff / 2)
    installed_capacity = installed_capacity * degradation_factor

    # Check minimum threshold
    total_installed = np.sum(installed_capacity)
    if total_installed < min_capacity_threshold:
        logger.debug(f"Rooftop solar below threshold: {total_installed:.2f} MW")
        return None

    # Cost reduction (learning curve)
    cost_factor = (1 - cost_reduction_rate) ** years_diff
    investment_cost = base_cost_per_kw * cost_factor

    # Calculate remaining investment potential
    invest_max_power = [
        max(0.0, max_potential[i] * (1 - current_adoption[i]))
        for i in range(len(max_potential))
    ]

    # Create unit configuration
    rooftop_unit = {
        "name": "Rooftop_Solar",
        "type": "Renewable",
        "fuel": "Sun",
        "rated_power": installed_capacity.tolist(),
        "fuel_cost": [0.0] * num_nodes,
        "fixed_cost": [o_and_m_cost * cost_factor] * num_nodes,
        "maintenance_cost": [5 * cost_factor] * num_nodes,
        "invest_cost": [investment_cost] * num_nodes,
        "invest_max_power": invest_max_power,
        "reservable": False,
        "ramp_up": [1.0] * num_nodes,
        "ramp_down": [1.0] * num_nodes,
        "min_up": [1] * num_nodes,
        "min_down": [1] * num_nodes,
        "start_up_cost": [0.0] * num_nodes,
        "inertia": [0.0] * num_nodes,
        "Availability": availability_matrix,
        "min_power": [0.0] * num_nodes,
        "eff_at_rated": [performance_ratio] * num_nodes,
        "eff_at_min": [performance_ratio] * num_nodes,
    }

    # Add unit to configuration
    try:
        existing_ids = [
            int(k.split("_")[1])
            for k in units_config.keys()
            if k.startswith("unit_")
        ]
        unit_id = max(existing_ids) + 1 if existing_ids else 1
    except (ValueError, IndexError):
        unit_id = len(units_config) + 1

    units_config[f"unit_{unit_id}"] = rooftop_unit

    logger.info(
        f"Added rooftop solar: {total_installed:.2f} MW installed, "
        f"adoption {np.mean(current_adoption)*100:.1f}%"
    )

    return rooftop_unit


def calculate_rooftop_potential(
    population: List[float],
    dwelling_density: float = 0.35,
    avg_roof_area: float = 50.0,
    suitable_fraction: float = 0.3,
    panel_efficiency: float = 0.20,
    solar_irradiance: float = 1000.0,
) -> List[float]:
    """
    Calculate rooftop solar potential based on population.

    Parameters
    ----------
    population : list
        Population per node
    dwelling_density : float
        Dwellings per capita
    avg_roof_area : float
        Average roof area in m²
    suitable_fraction : float
        Fraction of roof suitable for solar
    panel_efficiency : float
        Solar panel efficiency
    solar_irradiance : float
        Peak solar irradiance in W/m²

    Returns
    -------
    list
        Maximum potential in MW per node
    """
    max_potential = []
    for pop in population:
        num_dwellings = pop * dwelling_density
        total_roof_area = num_dwellings * avg_roof_area * suitable_fraction
        peak_power_kw = total_roof_area * panel_efficiency * solar_irradiance / 1000
        max_potential.append(peak_power_kw / 1000)  # Convert to MW

    return max_potential
