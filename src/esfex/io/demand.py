"""
Demand data loading and processing for ESFEX.

Provides functions to load demand data from files and create
sectoral demand distributions.
"""

import gc
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import h5py
import numpy as np
import pandas as pd

from esfex.utils.temporal import HOURS_STD_YEAR

logger = logging.getLogger(__name__)


def _read_tabular(file_path: Path, **kwargs) -> pd.DataFrame:
    """Read a tabular file (Excel or CSV) into a DataFrame.

    Dispatches to ``pd.read_excel`` or ``pd.read_csv`` based on the file
    extension.  CSV files are assumed to have **no header row** (pure
    numeric data), consistent with per-node demand CSVs.
    """
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file_path, header=None, **kwargs)
    # Default: treat as Excel (.xlsx, .xls, …) — no header row (pure numeric data)
    return pd.read_excel(file_path, header=None, **kwargs)


def load_demand_data(
    file_path: Union[str, Path],
    date_start: str = "01/01/2025 00:00",
    year_to_load: Optional[int] = None,
) -> Tuple[np.ndarray, int, int, List[int], List[datetime]]:
    """
    Load demand data from Excel file.

    Parameters
    ----------
    file_path : str or Path
        Path to demand Excel file
    date_start : str
        Start date and time in format "DD/MM/YYYY HH:MM"
    year_to_load : int, optional
        If specified, loads only data for this year. If None, loads all data.

    Returns
    -------
    tuple
        (demand_array, hours, num_nodes, years_list, time_index)
        - demand_array: numpy array with shape (hours, num_nodes)
        - hours: number of hours
        - num_nodes: number of nodes
        - years_list: list of years in the data
        - time_index: list of datetime objects for each hour
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Demand file not found: {file_path}")

    if year_to_load is None:
        # Load all data
        demand_df = _read_tabular(file_path)
        demand = demand_df.values
        hours, num_nodes = demand.shape

        start_date = datetime.strptime(date_start, "%d/%m/%Y %H:%M")
        end_date = start_date + timedelta(hours=(hours - 1))

        start_year = start_date.year
        end_year = end_date.year
        years_to_analyze = list(range(start_year, end_year + 1))

        time_index = [start_date + timedelta(hours=i) for i in range(hours)]

        logger.info(f"Loaded demand: {hours} hours, {num_nodes} nodes")

        return demand, hours, num_nodes, years_to_analyze, time_index

    else:
        # Load only a specific year
        start_date = datetime.strptime(date_start, "%d/%m/%Y %H:%M")

        # Get basic info
        demand_df_info = _read_tabular(file_path, nrows=2)
        num_nodes = demand_df_info.shape[1]

        # Calculate row range for the specific year
        year_start = datetime(year_to_load, 1, 1, 0, 0)
        year_end = datetime(year_to_load + 1, 1, 1, 0, 0)

        hours_from_start = int((year_start - start_date).total_seconds() / 3600)
        hours_in_year = int((year_end - year_start).total_seconds() / 3600)

        start_row = max(0, hours_from_start)

        # Get total rows
        total_rows_df = _read_tabular(file_path, usecols=[0])
        total_rows = len(total_rows_df)

        total_hours = total_rows
        end_date_full = start_date + timedelta(hours=total_hours)
        years_to_analyze = list(range(start_date.year, end_date_full.year + 1))

        end_row = min(start_row + hours_in_year, total_rows)
        year_hours = end_row - start_row

        # Load only the year's rows
        if start_row > 0:
            demand_df = _read_tabular(
                file_path,
                skiprows=range(1, start_row + 1),
                nrows=year_hours
            )
        else:
            demand_df = _read_tabular(file_path, nrows=year_hours)

        demand = demand_df.values
        hours = demand.shape[0]

        time_index = [year_start + timedelta(hours=i) for i in range(hours)]

        # Clean up
        del total_rows_df
        del demand_df_info
        gc.collect()

        logger.info(f"Loaded demand for year {year_to_load}: {hours} hours, {num_nodes} nodes")

        return demand, hours, num_nodes, years_to_analyze, time_index


def create_sectoral_demand(
    base_demand: np.ndarray,
    sector_distribution: Dict[int, Dict[str, float]],
    sectors_list: Optional[List[str]] = None,
) -> Dict[str, np.ndarray]:
    """
    Divide base demand into sectoral demands.

    Parameters
    ----------
    base_demand : np.ndarray
        Base demand array with shape (hours, num_nodes)
    sector_distribution : dict
        Distribution percentages by node and sector
        Format: {node_idx: {sector_name: proportion}}
    sectors_list : list, optional
        List of sectors to include. If None, uses all sectors.

    Returns
    -------
    dict
        Sectoral demand by sector name, each with shape (hours, num_nodes)

    Notes
    -----
    Proportions are normalized per node to ensure sum(sectoral_demand) == base_demand.
    """
    hours = base_demand.shape[0]
    num_nodes = base_demand.shape[1] if base_demand.ndim > 1 else 1

    # Get all sectors if not specified
    if sectors_list is None:
        all_sectors = set()
        for node_dist in sector_distribution.values():
            all_sectors.update(node_dist.keys())
        sectors_list = list(all_sectors)

    # Initialize sectoral demand
    sectoral_demand = {}
    for sector in sectors_list:
        sectoral_demand[sector] = np.zeros((hours, num_nodes))

    # Pre-calculate normalized proportions per node
    normalized_proportions = {}
    normalization_warnings = []

    for node in range(num_nodes):
        node_distribution = sector_distribution.get(node, sector_distribution.get(0, {}))

        # Calculate sum of proportions for sectors in sectors_list
        total_proportion = sum(
            prop for sector, prop in node_distribution.items()
            if sector in sectors_list
        )

        # Warn if proportions don't sum to ~1.0
        if abs(total_proportion - 1.0) > 0.01 and total_proportion > 0:
            normalization_warnings.append(
                f"Node {node}: sector proportions sum to {total_proportion:.3f}"
            )

        # Normalize proportions
        if total_proportion > 0:
            normalized_proportions[node] = {
                sector: prop / total_proportion
                for sector, prop in node_distribution.items()
                if sector in sectors_list
            }
        else:
            # Distribute equally if no valid proportions
            n_sectors = len(sectors_list)
            normalized_proportions[node] = {
                sector: 1.0 / n_sectors for sector in sectors_list
            }

    # Print warnings once
    if normalization_warnings:
        logger.warning(
            f"Sector distribution normalized for {len(normalization_warnings)} nodes"
        )

    # Distribute demand by sector
    for t in range(hours):
        for node in range(num_nodes):
            node_props = normalized_proportions[node]
            base_value = base_demand[t, node] if base_demand.ndim > 1 else base_demand[t]

            for sector, proportion in node_props.items():
                sectoral_demand[sector][t, node] = base_value * proportion

    return sectoral_demand


class DemandDataManager:
    """
    Manager for efficient demand data access using HDF5.

    Converts Excel demand files to HDF5 for faster year-by-year loading.
    """

    def __init__(
        self,
        excel_path: Union[str, Path],
        date_start: str = "01/01/2025 00:00",
        time_step: int = 1,
    ):
        """
        Initialize the demand data manager.

        Parameters
        ----------
        excel_path : str or Path
            Path to the Excel demand file
        date_start : str
            Start date in format "DD/MM/YYYY HH:MM"
        time_step : int
            Time step in hours (default 1)
        """
        self.excel_path = Path(excel_path)
        self.date_start = date_start
        self.time_step = time_step
        self.hdf5_path: Optional[Path] = None
        self.metadata: Dict[str, Any] = {}

    def prepare_hdf5_storage(self) -> Path:
        """
        Convert Excel file to HDF5 for efficient access.

        Returns
        -------
        Path
            Path to the created HDF5 file
        """
        logger.info("Converting demand from Excel to HDF5...")

        # Read all Excel data
        demand_df = _read_tabular(self.excel_path)
        demand_array = demand_df.values
        total_hours, num_nodes = demand_array.shape

        # Calculate temporal metadata
        start_date = datetime.strptime(self.date_start, "%d/%m/%Y %H:%M")
        end_date = start_date + timedelta(hours=(total_hours - 1) * self.time_step)

        # Create temporary HDF5 file
        temp_dir = tempfile.gettempdir()
        self.hdf5_path = Path(temp_dir) / f"demand_data_{os.getpid()}.h5"

        with h5py.File(self.hdf5_path, "w") as f:
            # Store demand data
            f.create_dataset(
                "demand",
                data=demand_array,
                dtype="float32",
                compression="gzip"
            )

            # Store metadata
            f.attrs["total_hours"] = int(total_hours)
            f.attrs["num_nodes"] = int(num_nodes)
            f.attrs["start_date"] = start_date.strftime("%Y-%m-%d %H:%M:%S")
            f.attrs["end_date"] = end_date.strftime("%Y-%m-%d %H:%M:%S")
            f.attrs["start_year"] = int(start_date.year)
            f.attrs["end_year"] = int(end_date.year)
            f.attrs["time_step"] = int(self.time_step)

            # Create year index for fast access
            year_index = f.create_group("year_index")
            current_year = start_date.year
            year_start_idx = 0

            for hour in range(total_hours):
                hour_date = start_date + timedelta(hours=hour * self.time_step)
                if hour_date.year != current_year or hour == total_hours - 1:
                    year_end_idx = hour if hour_date.year != current_year else hour + 1
                    year_index.create_dataset(
                        str(current_year),
                        data=[year_start_idx, year_end_idx]
                    )
                    if hour < total_hours - 1:
                        current_year = hour_date.year
                        year_start_idx = hour

        # Store metadata in memory
        self.metadata = {
            "total_hours": int(total_hours),
            "num_nodes": int(num_nodes),
            "start_date": start_date,
            "end_date": end_date,
            "years": list(range(start_date.year, end_date.year + 1))
        }

        logger.info(f"HDF5 created: {self.hdf5_path}")
        logger.info(f"Years available: {self.metadata['years']}")

        return self.hdf5_path

    def load_year_data(
        self,
        year: int,
    ) -> Tuple[np.ndarray, int, int, List[datetime]]:
        """
        Load data for a specific year from HDF5.

        Parameters
        ----------
        year : int
            Year to load

        Returns
        -------
        tuple
            (demand_array, hours, num_nodes, time_index)
        """
        if self.hdf5_path is None or not self.hdf5_path.exists():
            raise ValueError("HDF5 not prepared. Run prepare_hdf5_storage() first.")

        with h5py.File(self.hdf5_path, "r") as f:
            if str(year) not in f["year_index"]:
                raise ValueError(f"Year {year} not found in data")

            # Get year indices
            start_idx, end_idx = f["year_index"][str(year)][:]
            start_idx = int(start_idx)
            end_idx = int(end_idx)

            # Load year data
            year_demand = f["demand"][start_idx:end_idx, :]

            # Calculate time index
            start_date = datetime.strptime(
                f.attrs["start_date"],
                "%Y-%m-%d %H:%M:%S"
            )
            year_start_date = start_date + timedelta(hours=start_idx * self.time_step)
            hours_in_year = end_idx - start_idx

            time_index = [
                year_start_date + timedelta(hours=i * self.time_step)
                for i in range(hours_in_year)
            ]

            num_nodes = int(f.attrs["num_nodes"])

        logger.info(f"Loaded year {year}: {hours_in_year} hours, {num_nodes} nodes")

        return year_demand, hours_in_year, num_nodes, time_index

    def cleanup(self) -> None:
        """Remove temporary HDF5 file."""
        if self.hdf5_path and self.hdf5_path.exists():
            os.remove(self.hdf5_path)
            logger.info(f"Cleaned up: {self.hdf5_path}")


def extract_year_profile(
    full_profile: Union[pd.DataFrame, np.ndarray],
    time_index: List[datetime],
    hours: int,
) -> np.ndarray:
    """
    Extract profile data for a specific year.

    Parameters
    ----------
    full_profile : DataFrame or ndarray
        Full profile data
    time_index : list
        Time index for the target year
    hours : int
        Number of hours to extract

    Returns
    -------
    np.ndarray
        Profile data for the year
    """
    if isinstance(full_profile, pd.DataFrame):
        year = time_index[0].year
        year_profile = full_profile[full_profile.index.year == year]
        return year_profile.values[:hours]
    elif isinstance(full_profile, np.ndarray):
        return full_profile[:hours]
    else:
        raise ValueError(f"Unsupported profile type: {type(full_profile)}")


def load_availability_profile(
    file_path: Union[str, Path],
    temporal_resolution_hours: int = 1,
    num_nodes: Optional[int] = None,
) -> np.ndarray:
    """
    Load availability profile from Excel file.

    Parameters
    ----------
    file_path : str or Path
        Path to availability Excel file
    temporal_resolution_hours : int
        Temporal resolution for aggregation (1 = hourly, 6 = 6-hourly, etc.)
    num_nodes : int, optional
        Expected number of nodes. If provided, validates the file.

    Returns
    -------
    np.ndarray
        Availability array with shape (hours, nodes), values in [0, 1]
    """
    from esfex.utils.temporal import aggregate_to_resolution

    file_path = Path(file_path)
    if not file_path.exists():
        logger.warning(f"Availability file not found: {file_path}, using default 1.0")
        hours = HOURS_STD_YEAR // temporal_resolution_hours
        nodes = num_nodes or 10
        return np.ones((hours, nodes))

    try:
        df = _read_tabular(file_path)
        availability = df.values.astype(float)

        # Adjust columns to match num_nodes.
        # Availability is a per-generator temporal profile, so a single-column
        # file is the common case — broadcast it to all nodes.
        if num_nodes is not None and availability.shape[1] != num_nodes:
            if availability.shape[1] == 1:
                # Single profile → replicate for every node
                availability = np.tile(availability, (1, num_nodes))
            elif availability.shape[1] < num_nodes:
                # Partial columns → replicate the last column for missing nodes
                padding = np.tile(
                    availability[:, -1:],
                    (1, num_nodes - availability.shape[1]),
                )
                availability = np.hstack([availability, padding])
            else:
                # More columns than nodes → truncate
                availability = availability[:, :num_nodes]

        # Clip to [0, 1]
        availability = np.clip(availability, 0.0, 1.0)

        # Apply temporal aggregation using MEAN (availability is a capacity factor)
        if temporal_resolution_hours > 1:
            availability = aggregate_to_resolution(availability, temporal_resolution_hours)

        logger.debug(
            f"Loaded availability from {file_path}: shape={availability.shape}, "
            f"mean={availability.mean():.3f}"
        )
        return availability

    except Exception as e:
        logger.error(f"Error loading availability from {file_path}: {e}")
        hours = HOURS_STD_YEAR // temporal_resolution_hours
        nodes = num_nodes or 10
        return np.ones((hours, nodes))
