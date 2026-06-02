"""
Electric Vehicle (EV) model for ESFEX.

Provides functions to generate EV charging profiles and V2G availability
with logistic (S-curve) fleet growth.
"""

import logging
import os
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np
import pandas as pd
from scipy.stats import norm

from esfex.utils.temporal import HOURS_STD_YEAR

logger = logging.getLogger(__name__)


def generate_ev_profiles(
    num_nodes: int,
    num_hours: int,
    ev_categories: Dict[str, dict],
    ev_quantity: Dict[str, List[float]],
    base_patterns: Dict[str, List[float]],
    base_year: int = 2025,
    target_year: int = 2050,
    max_adoption: float = 30.0,
    growth_rate: float = 0.12,
) -> pd.DataFrame:
    """
    Generate EV charging profiles with S-curve fleet growth.

    Parameters
    ----------
    num_nodes : int
        Number of nodes in the system
    num_hours : int
        Number of hours in the planning horizon
    ev_categories : dict
        Dictionary with properties for each vehicle category
        Keys: category names, Values: dict with 'charging_power', 'v2g_participation', etc.
    ev_quantity : dict
        Initial number of vehicles per category and node (base year)
        Keys: category names, Values: list of quantities per node
    base_patterns : dict
        Base availability patterns per category (24-hour profiles)
        Keys: category names, Values: list of 24 hourly values (0-1)
    base_year : int
        Base year for adoption calculation
    target_year : int
        Target year for projections
    max_adoption : float
        Maximum growth factor (multiplier on initial fleet)
    growth_rate : float
        Logistic growth rate

    Returns
    -------
    pd.DataFrame
        Charging profiles with columns 'Node_{n}_{category}' and hourly values in MW
    """
    # Create columns
    columns = []
    for node in range(num_nodes):
        for category in ev_categories:
            columns.append(f"Node_{node+1}_{category}")

    profiles = pd.DataFrame(index=range(num_hours), columns=columns, dtype=float)

    total_years = target_year - base_year

    for node in range(num_nodes):
        for category in ev_categories:
            pattern = base_patterns[category]

            # Category-specific parameters
            category_max_adoption = ev_categories[category].get("max_adoption", max_adoption)
            category_growth_rate = ev_categories[category].get("growth_rate", growth_rate)
            category_mid_point_fraction = ev_categories[category].get("mid_point_fraction", 0.5)

            mid_point_year = base_year + total_years * category_mid_point_fraction

            # Energy normalization. ``pattern`` is an availability/plug-in
            # profile (fraction of fleet connected per hour), not a power
            # duty cycle. Reading it as continuous power draw overcounted
            # daily energy ~12×. Pin the pattern's daily integral to the
            # real per-vehicle daily charging energy so the shape sets WHEN
            # charging happens while the magnitude stays physical.
            daily_energy_kwh = ev_categories[category].get("daily_energy_kwh")
            if daily_energy_kwh is None:
                daily_energy_kwh = ev_categories[category]["battery_capacity"] * 0.12
            pattern_daily_sum = float(np.sum(pattern))
            # MWh charged per vehicle per hour = value/Σpattern × daily_energy.
            # charging_power no longer scales energy (it only caps the
            # instantaneous rate, enforced in the dispatch LP).
            per_vehicle_hour_mwh = (
                (daily_energy_kwh / pattern_daily_sum / 1000.0)
                if pattern_daily_sum > 0 else 0.0
            )

            profile = []
            for hour in range(num_hours):
                # Calculate current year
                year_index = hour // HOURS_STD_YEAR
                current_year = base_year + year_index

                # S-curve growth factor
                growth_factor = category_max_adoption / (
                    1 + np.exp(-category_growth_rate * (current_year - mid_point_year))
                )

                # Cyclic pattern
                hour_of_day = hour % 24
                base_value = pattern[hour_of_day]

                # Add noise
                noise = np.random.normal(0, 0.02)
                value = max(0, min(1, base_value + noise))

                # Scale by vehicle count
                num_vehicles_initial = (
                    ev_quantity[category][node]
                    if node < len(ev_quantity[category])
                    else 0
                )
                num_vehicles = num_vehicles_initial * growth_factor

                # MW for this hour = (energy share this hour) × fleet size
                demand_factor = value * num_vehicles * per_vehicle_hour_mwh
                profile.append(demand_factor)

            profiles[f"Node_{node+1}_{category}"] = profile

    return profiles


def generate_v2g_availability(
    num_nodes: int,
    num_hours: int,
    ev_categories: Dict[str, dict],
    ev_quantity: Dict[str, List[float]],
    base_patterns: Dict[str, List[float]],
    base_year: int = 2025,
    target_year: int = 2050,
    max_adoption: float = 30.0,
    growth_rate: float = 0.12,
) -> pd.DataFrame:
    """
    Generate V2G availability profiles with S-curve fleet growth.

    Parameters
    ----------
    num_nodes : int
        Number of nodes in the system
    num_hours : int
        Number of hours in the planning horizon
    ev_categories : dict
        Dictionary with properties for each vehicle category
    ev_quantity : dict
        Initial number of vehicles per category and node
    base_patterns : dict
        Base availability patterns per category
    base_year : int
        Base year for adoption calculation
    target_year : int
        Target year for projections
    max_adoption : float
        Maximum growth factor
    growth_rate : float
        Logistic growth rate

    Returns
    -------
    pd.DataFrame
        V2G availability profiles in MW
    """
    columns = []
    for node in range(num_nodes):
        for category in ev_categories:
            columns.append(f"Node_{node+1}_{category}")

    profiles = pd.DataFrame(index=range(num_hours), columns=columns, dtype=float)

    total_years = target_year - base_year

    for node in range(num_nodes):
        for category in ev_categories:
            pattern = base_patterns[category]
            v2g_participation = ev_categories[category]["v2g_participation"]

            category_max_adoption = ev_categories[category].get("max_adoption", max_adoption)
            category_growth_rate = ev_categories[category].get("growth_rate", growth_rate)
            category_mid_point_fraction = ev_categories[category].get("mid_point_fraction", 0.5)

            mid_point_year = base_year + total_years * category_mid_point_fraction

            profile = []
            for hour in range(num_hours):
                year_index = hour // HOURS_STD_YEAR
                current_year = base_year + year_index

                growth_factor = category_max_adoption / (
                    1 + np.exp(-category_growth_rate * (current_year - mid_point_year))
                )

                hour_of_day = hour % 24
                base_value = pattern[hour_of_day]

                noise = np.random.normal(0, 0.01)
                value = max(0, min(1, base_value + noise))

                v2g_value = value * v2g_participation

                num_vehicles_initial = (
                    ev_quantity[category][node]
                    if node < len(ev_quantity[category])
                    else 0
                )
                num_vehicles = num_vehicles_initial * growth_factor
                v2g_power = ev_categories[category]["v2g_power"]

                available_v2g = v2g_value * num_vehicles * v2g_power / 1000
                profile.append(available_v2g)

            profiles[f"Node_{node+1}_{category}"] = profile

    return profiles


def generate_electricity_prices(num_hours: int = 24) -> np.ndarray:
    """
    Generate synthetic electricity prices with typical daily patterns.

    Parameters
    ----------
    num_hours : int
        Number of hours to generate

    Returns
    -------
    np.ndarray
        Electricity prices in $/MWh
    """
    hours = np.linspace(0, 23, num_hours)

    # Morning and evening peaks
    morning_peak = norm.pdf(hours, loc=9, scale=1.5)
    evening_peak = norm.pdf(hours, loc=20, scale=2)
    base_price = morning_peak + evening_peak

    # Normalize to typical range (50-200 $/MWh)
    base_price = 50 + (base_price / np.max(base_price)) * 150

    # Add noise
    noise = np.random.normal(0, 5, num_hours)
    prices = base_price + noise

    return prices


def calculate_v2g_compensation(electricity_prices: np.ndarray) -> np.ndarray:
    """
    Calculate V2G compensation rates.

    Typically 80-90% of electricity price to incentivize participation.

    Parameters
    ----------
    electricity_prices : np.ndarray
        Electricity prices in $/MWh

    Returns
    -------
    np.ndarray
        V2G compensation rates in $/MWh
    """
    return electricity_prices * 0.85


def aggregate_ev_profiles(
    profiles: pd.DataFrame,
    num_nodes: int,
) -> np.ndarray:
    """
    Aggregate EV profiles by node (sum across categories).

    Parameters
    ----------
    profiles : pd.DataFrame
        EV profiles with columns 'Node_{n}_{category}'
    num_nodes : int
        Number of nodes

    Returns
    -------
    np.ndarray
        Aggregated profiles with shape (hours, num_nodes)
    """
    num_hours = len(profiles)
    aggregated = np.zeros((num_hours, num_nodes))

    for node in range(num_nodes):
        node_cols = [c for c in profiles.columns if c.startswith(f"Node_{node+1}_")]
        if node_cols:
            aggregated[:, node] = profiles[node_cols].sum(axis=1).values

    return aggregated


def save_ev_profiles_hdf5(
    ev_charging: pd.DataFrame,
    v2g_availability: pd.DataFrame,
    filepath: Optional[str] = None,
) -> str:
    """
    Save EV profiles to HDF5 file.

    Parameters
    ----------
    ev_charging : pd.DataFrame
        EV charging profiles
    v2g_availability : pd.DataFrame
        V2G availability profiles
    filepath : str, optional
        Output file path (auto-generated if None)

    Returns
    -------
    str
        Path to the created HDF5 file
    """
    if filepath is None:
        temp_dir = tempfile.gettempdir()
        unique_id = str(uuid.uuid4())
        filepath = os.path.join(temp_dir, f"ev_profiles_{unique_id}.h5")

    with h5py.File(filepath, "w") as f:
        # Charging data
        charging_group = f.create_group("charging")
        charging_group.create_dataset("data", data=ev_charging.values)
        charging_group.create_dataset(
            "index",
            data=np.array([str(i) for i in ev_charging.index], dtype="S")
        )
        charging_group.create_dataset(
            "columns",
            data=np.array([str(c) for c in ev_charging.columns], dtype="S")
        )

        # V2G data
        v2g_group = f.create_group("v2g")
        v2g_group.create_dataset("data", data=v2g_availability.values)
        v2g_group.create_dataset(
            "index",
            data=np.array([str(i) for i in v2g_availability.index], dtype="S")
        )
        v2g_group.create_dataset(
            "columns",
            data=np.array([str(c) for c in v2g_availability.columns], dtype="S")
        )

    logger.info(f"Saved EV profiles to: {filepath}")
    return filepath


def load_ev_profiles_hdf5(filepath: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load EV profiles from HDF5 file.

    Parameters
    ----------
    filepath : str
        Path to HDF5 file

    Returns
    -------
    tuple
        (ev_charging, v2g_availability) DataFrames
    """
    with h5py.File(filepath, "r") as f:
        # Load charging data
        charging_data = f["charging"]["data"][:]
        charging_index = [s.decode() for s in f["charging"]["index"][:]]
        charging_columns = [s.decode() for s in f["charging"]["columns"][:]]
        ev_charging = pd.DataFrame(
            charging_data,
            index=charging_index,
            columns=charging_columns
        )

        # Load V2G data
        v2g_data = f["v2g"]["data"][:]
        v2g_index = [s.decode() for s in f["v2g"]["index"][:]]
        v2g_columns = [s.decode() for s in f["v2g"]["columns"][:]]
        v2g_availability = pd.DataFrame(
            v2g_data,
            index=v2g_index,
            columns=v2g_columns
        )

    return ev_charging, v2g_availability
