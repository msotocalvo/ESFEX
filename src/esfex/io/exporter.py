"""
Results export utilities for ESFEX.

Provides classes and functions to export optimization results
to HDF5, CSV, Excel, and JSON formats.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import h5py
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ResultsExporter:
    """
    Export optimization results to various formats.

    Supports HDF5 (primary), CSV, Excel, and JSON exports.
    """

    def __init__(self, results_path: Union[str, Path]):
        """
        Initialize the exporter with an HDF5 results file.

        Parameters
        ----------
        results_path : str or Path
            Path to HDF5 results file
        """
        self.results_path = Path(results_path)
        if not self.results_path.exists():
            raise FileNotFoundError(f"Results file not found: {results_path}")

    def to_csv(self, output_dir: Union[str, Path]) -> None:
        """
        Export results to CSV files.

        Parameters
        ----------
        output_dir : str or Path
            Output directory for CSV files
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with h5py.File(self.results_path, "r") as f:
            # Export summary if exists
            if "summary_results" in f:
                self._export_group_to_csv(f["summary_results"], output_dir / "summary")

            # Export detailed results
            if "detailed_results" in f:
                for scenario_name in f["detailed_results"]:
                    scenario_dir = output_dir / scenario_name
                    self._export_scenario_to_csv(
                        f["detailed_results"][scenario_name],
                        scenario_dir
                    )

            # Export demand data if exists
            if "demand" in f:
                self._export_group_to_csv(f["demand"], output_dir / "demand")

        logger.info(f"Exported CSV to: {output_dir}")

    def to_excel(self, output_path: Union[str, Path]) -> None:
        """
        Export results to Excel file.

        Parameters
        ----------
        output_path : str or Path
            Output Excel file path
        """
        output_path = Path(output_path)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            with h5py.File(self.results_path, "r") as f:
                # Export summary
                if "summary_results" in f:
                    summary_df = self._group_to_dataframe(f["summary_results"])
                    if summary_df is not None:
                        summary_df.to_excel(writer, sheet_name="Summary")

                # Export selected detailed results (first scenario)
                if "detailed_results" in f:
                    scenarios = list(f["detailed_results"].keys())
                    if scenarios:
                        scenario = f["detailed_results"][scenarios[0]]
                        # Handle both formats
                        data_root = scenario["hourly_data"] if "hourly_data" in scenario else scenario
                        if "generation" in data_root:
                            gen_df = self._arrays_to_dataframe(
                                data_root["generation"]
                            )
                            if gen_df is not None:
                                gen_df.to_excel(writer, sheet_name="Generation")

        logger.info(f"Exported Excel to: {output_path}")

    def to_json(self, output_path: Union[str, Path]) -> None:
        """
        Export results to JSON file.

        Parameters
        ----------
        output_path : str or Path
            Output JSON file path
        """
        output_path = Path(output_path)

        results = {}

        with h5py.File(self.results_path, "r") as f:
            # Get metadata
            results["metadata"] = {
                key: str(f.attrs[key]) for key in f.attrs
            }

            # Export summary
            if "summary_results" in f:
                results["summary"] = self._group_to_dict(f["summary_results"])

            # Export scenario names and basic info
            if "detailed_results" in f:
                results["scenarios"] = {}
                for scenario_name in f["detailed_results"]:
                    scenario = f["detailed_results"][scenario_name]
                    results["scenarios"][scenario_name] = {
                        key: str(scenario.attrs[key])
                        for key in scenario.attrs
                    }

        with open(output_path, "w") as fp:
            json.dump(results, fp, indent=2, default=str)

        logger.info(f"Exported JSON to: {output_path}")

    def _export_group_to_csv(self, group: h5py.Group, output_dir: Path) -> None:
        """Export HDF5 group to CSV files."""
        output_dir.mkdir(parents=True, exist_ok=True)

        for key in group:
            if isinstance(group[key], h5py.Dataset):
                data = group[key][:]
                df = pd.DataFrame(data)
                df.to_csv(output_dir / f"{key}.csv", index=False)

    def _export_scenario_to_csv(self, scenario: h5py.Group, output_dir: Path) -> None:
        """Export scenario data to CSV files.

        Handles both formats:
        - Incremental format: data directly in scenario group (generation/, curtailment, etc.)
        - One-shot format: data nested in hourly_data/ subgroup
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine data root (incremental vs one-shot format)
        data_root = scenario["hourly_data"] if "hourly_data" in scenario else scenario

        for category in data_root:
            if isinstance(data_root[category], h5py.Group):
                cat_dir = output_dir / category
                cat_dir.mkdir(parents=True, exist_ok=True)

                for name in data_root[category]:
                    if isinstance(data_root[category][name], h5py.Dataset):
                        data = data_root[category][name][:]
                        df = pd.DataFrame(data)
                        df.to_csv(cat_dir / f"{name}.csv", index=False)

            elif isinstance(data_root[category], h5py.Dataset):
                data = data_root[category][:]
                df = pd.DataFrame(data)
                df.to_csv(output_dir / f"{category}.csv", index=False)

    def _group_to_dataframe(self, group: h5py.Group) -> Optional[pd.DataFrame]:
        """Convert HDF5 group to DataFrame."""
        data = {}
        for key in group:
            if isinstance(group[key], h5py.Dataset):
                data[key] = group[key][:]

        if data:
            return pd.DataFrame(data)
        return None

    def _arrays_to_dataframe(self, group: h5py.Group) -> Optional[pd.DataFrame]:
        """Convert group of arrays to DataFrame with total."""
        dfs = []
        for name in group:
            if isinstance(group[name], h5py.Dataset):
                data = group[name][:]
                # Sum across nodes if 2D
                if data.ndim == 2:
                    total = data.sum(axis=0)
                else:
                    total = data
                dfs.append(pd.Series(total, name=name))

        if dfs:
            return pd.concat(dfs, axis=1)
        return None

    def _group_to_dict(self, group: h5py.Group) -> dict:
        """Convert HDF5 group to dictionary."""
        result = {}
        for key in group:
            if isinstance(group[key], h5py.Dataset):
                data = group[key][:]
                # Convert to list for JSON serialization
                if isinstance(data, np.ndarray):
                    result[key] = data.tolist()
                else:
                    result[key] = data
        return result


def export_system_results(
    results_dict: Dict[str, Any],
    generators: List[dict],
    batteries: List[dict],
    hours: int,
    num_nodes: int,
    output_filename: Optional[str] = None,
    output_dir: Union[str, Path] = "results",
    temporal_resolution_hours: int = 1,
) -> Path:
    """
    Export optimization results to HDF5 file.

    Parameters
    ----------
    results_dict : dict
        Dictionary with optimization results
    generators : list
        List of generator configurations
    batteries : list
        List of battery configurations
    hours : int
        Number of hours in the simulation
    num_nodes : int
        Number of nodes
    output_filename : str, optional
        Output filename (auto-generated if None)
    output_dir : str or Path
        Output directory
    temporal_resolution_hours : int
        Temporal resolution of the data

    Returns
    -------
    Path
        Path to the created HDF5 file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if output_filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"results_{timestamp}.h5"

    filepath = output_dir / output_filename

    logger.info(f"Exporting results to: {filepath}")

    with h5py.File(filepath, "w") as hf:
        # Metadata
        hf.attrs["creation_date"] = datetime.now().isoformat()
        hf.attrs["hours"] = hours
        hf.attrs["num_nodes"] = num_nodes
        hf.attrs["temporal_resolution_hours"] = temporal_resolution_hours
        hf.attrs["num_generators"] = len(generators)
        hf.attrs["num_batteries"] = len(batteries)

        # Store generator names
        gen_names = [g.get("name", f"Gen_{i}") for i, g in enumerate(generators)]
        hf.attrs["generator_names"] = [n.encode("utf-8") for n in gen_names]

        # Store battery names
        bat_names = [b.get("name", f"Storage_{i}") for i, b in enumerate(batteries)]
        hf.attrs["battery_names"] = [n.encode("utf-8") for n in bat_names]

        # Export detailed results
        if results_dict:
            detailed = hf.create_group("detailed_results")

            for scenario_key, scenario_data in results_dict.items():
                # Create scenario group
                if isinstance(scenario_key, tuple):
                    year, threshold = scenario_key
                    scenario_name = f"year_{year}_threshold_{threshold}"
                else:
                    scenario_name = str(scenario_key).replace(" ", "_").replace(".", "_")

                scenario_grp = detailed.create_group(scenario_name)

                # Store scenario metadata
                if isinstance(scenario_key, tuple):
                    scenario_grp.attrs["year"] = scenario_key[0]
                    scenario_grp.attrs["threshold"] = scenario_key[1]

                # Store scalar results
                for key in ["objective", "total_generation", "renewable_generation",
                           "renewable_penetration", "co2_emissions"]:
                    if key in scenario_data:
                        scenario_grp.attrs[key] = float(scenario_data[key])

                # Store hourly data
                if "hourly_data" in scenario_data:
                    _export_hourly_data(
                        scenario_grp,
                        scenario_data["hourly_data"],
                        hours,
                        num_nodes,
                    )

    logger.info(f"Results exported successfully: {filepath}")
    return filepath


def _export_hourly_data(
    parent_group: h5py.Group,
    hourly_data: Dict[str, Any],
    hours: int,
    num_nodes: int,
) -> None:
    """Export hourly data to HDF5 group."""
    hourly_grp = parent_group.create_group("hourly_data")

    # Generation data
    if "generation" in hourly_data:
        gen_grp = hourly_grp.create_group("generation")
        gen_grp.attrs["description"] = "Generation output [nodes x hours]"

        for gen_name, gen_array in hourly_data["generation"].items():
            if isinstance(gen_array, np.ndarray):
                ds = gen_grp.create_dataset(
                    gen_name,
                    data=gen_array,
                    chunks=True,
                    compression="gzip"
                )
                ds.attrs["units"] = "MW"

    # Battery data
    for bat_key in ["battery_charge", "battery_discharge"]:
        if bat_key in hourly_data:
            bat_grp = hourly_grp.create_group(bat_key)

            for bat_name, bat_array in hourly_data[bat_key].items():
                if isinstance(bat_array, np.ndarray):
                    ds = bat_grp.create_dataset(
                        bat_name,
                        data=bat_array,
                        chunks=True,
                        compression="gzip"
                    )
                    ds.attrs["units"] = "MW"

    # Curtailment
    if "curtailment" in hourly_data:
        curt_data = hourly_data["curtailment"]
        if isinstance(curt_data, np.ndarray):
            ds = hourly_grp.create_dataset(
                "curtailment",
                data=curt_data,
                chunks=True,
                compression="gzip"
            )
            ds.attrs["units"] = "MW"

    # Electricity prices
    for price_key in ["electricity_prices", "nodal_electricity_prices"]:
        if price_key in hourly_data:
            price_data = hourly_data[price_key]
            if isinstance(price_data, np.ndarray):
                ds = hourly_grp.create_dataset(
                    price_key,
                    data=price_data,
                    chunks=True,
                    compression="gzip"
                )
                ds.attrs["units"] = "$/MWh"

    # CO2 emissions
    if "CO2_emissions" in hourly_data:
        co2_data = hourly_data["CO2_emissions"]
        if isinstance(co2_data, np.ndarray):
            ds = hourly_grp.create_dataset(
                "CO2_emissions",
                data=co2_data,
                chunks=True,
                compression="gzip"
            )
            ds.attrs["units"] = "tonnes"

    # Power flow
    if "power_flow" in hourly_data:
        flow_data = hourly_data["power_flow"]
        if isinstance(flow_data, np.ndarray):
            ds = hourly_grp.create_dataset(
                "power_flow",
                data=flow_data,
                chunks=True,
                compression="gzip"
            )
            ds.attrs["units"] = "MW"
            ds.attrs["shape"] = f"[{num_nodes} from x {num_nodes} to x {hours} hours]"

    # Per-generator grouped data (capacity_factor, lcoe, vallcoe, etc.)
    for grp_key in [
        "capacity_factor", "lcoe", "vallcoe",
        "battery_spillage", "battery_capacity_factor", "battery_lcoe", "battery_vallcoe",
    ]:
        if grp_key in hourly_data and isinstance(hourly_data[grp_key], dict):
            grp = hourly_grp.create_group(grp_key)
            units_map = {
                "capacity_factor": "dimensionless", "battery_capacity_factor": "dimensionless",
                "lcoe": "USD/MWh", "vallcoe": "USD/MWh",
                "battery_lcoe": "USD/MWh", "battery_vallcoe": "USD/MWh",
                "battery_spillage": "MW",
            }
            for name, arr in hourly_data[grp_key].items():
                if isinstance(arr, np.ndarray):
                    ds = grp.create_dataset(name, data=arr, chunks=True, compression="gzip")
                    ds.attrs["units"] = units_map.get(grp_key, "")

    # Scalar/2D arrays (EV, loss of inertia, price decomposition, etc.)
    for key, units in [
        ("EV_charging", "MW"), ("EV_V2G", "MW"), ("EV_soc", "MWh"), ("EV_loss", "MW"),
        ("loss_of_inertia", "GW*s"), ("transfer_margin", "MW"),
        ("electricity_prices_energy", "USD/MWh"),
        ("nodal_electricity_prices_congestion", "USD/MWh"),
    ]:
        if key in hourly_data:
            data = hourly_data[key]
            if isinstance(data, np.ndarray):
                ds = hourly_grp.create_dataset(key, data=data, chunks=True, compression="gzip")
                ds.attrs["units"] = units

    # Technology selling prices
    if "technology_selling_prices" in hourly_data:
        tsp = hourly_data["technology_selling_prices"]
        if isinstance(tsp, dict):
            tsp_grp = hourly_grp.create_group("technology_selling_prices")
            for tech_name, tech_data in tsp.items():
                if isinstance(tech_data, dict):
                    tg = tsp_grp.create_group(tech_name)
                    for attr_key in ["total_generation", "total_revenue", "average_selling_price", "technology_type"]:
                        if attr_key in tech_data:
                            tg.attrs[attr_key] = tech_data[attr_key]
                    if "prices_weights" in tech_data and isinstance(tech_data["prices_weights"], np.ndarray):
                        ds = tg.create_dataset("prices_weights", data=tech_data["prices_weights"],
                                               chunks=True, compression="gzip")
                        ds.attrs["columns"] = "price_USD_MWh, generation_MW, timestep"


def read_results(results_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Read results from HDF5 file.

    Parameters
    ----------
    results_path : str or Path
        Path to HDF5 results file

    Returns
    -------
    dict
        Dictionary with results data
    """
    results_path = Path(results_path)

    results = {"metadata": {}, "scenarios": {}}

    with h5py.File(results_path, "r") as f:
        # Read metadata
        for key in f.attrs:
            results["metadata"][key] = f.attrs[key]

        # Read detailed results
        if "detailed_results" in f:
            for scenario_name in f["detailed_results"]:
                scenario = f["detailed_results"][scenario_name]
                scenario_data = {
                    "attrs": {k: scenario.attrs[k] for k in scenario.attrs}
                }

                # Handle both formats: hourly_data subgroup or data directly in scenario
                data_root = scenario["hourly_data"] if "hourly_data" in scenario else scenario
                scenario_data["hourly_data"] = {}

                for category in data_root:
                    if isinstance(data_root[category], h5py.Group):
                        scenario_data["hourly_data"][category] = {
                            name: data_root[category][name][:]
                            for name in data_root[category]
                            if isinstance(data_root[category][name], h5py.Dataset)
                        }
                    elif isinstance(data_root[category], h5py.Dataset):
                        scenario_data["hourly_data"][category] = data_root[category][:]

                results["scenarios"][scenario_name] = scenario_data

    return results
