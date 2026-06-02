"""Central path registry for esfex datasets.

All data lives under ``ESFEX_DATA_ROOT`` (env var). Default:
``/media/manuel/New Volume/Demand_forecasting``.

Scripts, figures and code live in the repo (``.../Net_zero_horizon/esfex``);
only datasets are under ``DATA_ROOT``.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get(
    "ESFEX_DATA_ROOT",
    "/media/manuel/New Volume/Demand_forecasting",
))

PROJECT_DATA = DATA_ROOT / "data"

DEMAND_DATASET_DIR = PROJECT_DATA / "demand_dataset"
DEMAND_MANIFEST = DEMAND_DATASET_DIR / "manifest.json"
DEMAND_ERA5_DIR = DEMAND_DATASET_DIR / "_era5"
DEMAND_RAW_DIR = DEMAND_DATASET_DIR / "_raw"
# DEMAND_UTCI_DIR and HREA_DIR removed 2026-04-18: features dropped after
# multicollinearity audit. Data directories also deleted.

GRIDDED_DIR = PROJECT_DATA / "gridded"
GHSL_SMOD_DIR = GRIDDED_DIR / "ghsl_smod"
SHAPEFILES_DIR = GRIDDED_DIR / "shapefiles"
ADMIN1_SHP = SHAPEFILES_DIR / "ne_10m_admin_1" / "ne_10m_admin_1_states_provinces.shp"
GRIDDED_CMIP6_DIR = GRIDDED_DIR / "cmip6"
GRIDDED_GDP_DIR = GRIDDED_DIR / "gdp"
GRIDDED_GDP_025_DIR = GRIDDED_GDP_DIR / "025d"
GRIDDED_POP_DIR = GRIDDED_DIR / "population"
GRIDDED_POP_SSP2_DIR = GRIDDED_DIR / "population_ssp2"
GRIDDED_NIGHTLIGHTS_DIR = GRIDDED_DIR / "nightlights"

MODELS_DIR = PROJECT_DATA / "models"
CMIP6_DIR = PROJECT_DATA / "cmip6"
ERA5_DIR = PROJECT_DATA / "era5"
NEW_SOURCES_DIR = PROJECT_DATA / "new_sources"
OWID_DIR = PROJECT_DATA / "owid"
PLEXOS_DIR = PROJECT_DATA / "plexos"
UN_WPP_DIR = PROJECT_DATA / "un_wpp"
WORLDBANK_DIR = PROJECT_DATA / "worldbank"
WORLDBANK_ALL = WORLDBANK_DIR / "wb_all.json"
GEO_ASSETS_DIR = PROJECT_DATA / "geo_assets"

POP_DENSITY_HIST_DIR = DATA_ROOT / "Grided_population_historical"
POP_DENSITY_FUT_DIR = DATA_ROOT / "Grided_population_forecast"
GDP_FULL_DIR = DATA_ROOT / "Grided_GDP"
OUTPUT_DIR = DATA_ROOT / "output"

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results"
DEMAND_ESTIMATION_RESULTS = RESULTS_DIR / "demand_estimation"
