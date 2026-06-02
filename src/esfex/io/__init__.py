"""
Input/Output utilities for ESFEX.

Provides functions for:
- Loading demand data from files
- Creating sectoral demand distributions
- Exporting results to HDF5, CSV, Excel, JSON
"""

from esfex.io.demand import (
    DemandDataManager,
    create_sectoral_demand,
    extract_year_profile,
    load_demand_data,
)
from esfex.io.exporter import (
    ResultsExporter,
    export_system_results,
    read_results,
)

__all__ = [
    # Demand
    "DemandDataManager",
    "create_sectoral_demand",
    "extract_year_profile",
    "load_demand_data",
    # Exporter
    "ResultsExporter",
    "export_system_results",
    "read_results",
]
