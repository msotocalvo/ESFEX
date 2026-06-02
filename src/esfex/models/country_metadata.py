"""Country metadata registry for global demand projection.

Provides per-country baseline data (coordinates, GDP, population,
electricity consumption) from World Bank, Our World in Data, and
UN World Population Prospects.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from esfex.paths import PROJECT_DATA as _DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)


@dataclass
class CountryRecord:
    """All metadata needed to project demand for one country."""

    iso3: str
    name: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    # World Bank indicators (most recent)
    gdp_per_capita: float = 0.0  # USD
    population: float = 0.0
    urbanization_pct: float = 50.0  # 0-100
    electricity_access_pct: float = 100.0  # 0-100
    kwh_per_capita: float = 0.0  # kWh/person/year
    # Baseline annual consumption
    annual_gwh: float = 0.0
    data_quality: str = "unknown"  # "observed", "estimated", "missing"
    # Population projections from UN WPP
    pop_projections: dict[int, float] = field(default_factory=dict)


# ── Capital city coordinates (lat, lon) for ~195 countries ──────────────

COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "AFG": (34.53, 69.17), "ALB": (41.33, 19.82), "DZA": (36.75, 3.04),
    "AND": (42.51, 1.52), "AGO": (-8.84, 13.23), "ATG": (17.12, -61.85),
    "ARG": (-34.60, -58.38), "ARM": (40.18, 44.51), "AUS": (-35.28, 149.13),
    "AUT": (48.21, 16.37), "AZE": (40.41, 49.87), "BHS": (25.05, -77.35),
    "BHR": (26.23, 50.59), "BGD": (23.81, 90.41), "BRB": (13.10, -59.61),
    "BLR": (53.90, 27.57), "BEL": (50.85, 4.35), "BLZ": (17.25, -88.77),
    "BEN": (6.50, 2.60), "BTN": (27.47, 89.64), "BOL": (-16.50, -68.15),
    "BIH": (43.86, 18.41), "BWA": (-24.65, 25.91), "BRA": (-15.79, -47.88),
    "BRN": (4.94, 114.95), "BGR": (42.70, 23.32), "BFA": (12.37, -1.52),
    "BDI": (-3.38, 29.36), "CPV": (14.93, -23.51), "KHM": (11.56, 104.93),
    "CMR": (3.87, 11.52), "CAN": (45.42, -75.70), "CAF": (4.36, 18.56),
    "TCD": (12.13, 15.05), "CHL": (-33.45, -70.67), "CHN": (39.90, 116.40),
    "COL": (4.71, -74.07), "COM": (-11.70, 43.26), "COG": (-4.27, 15.28),
    "COD": (-4.32, 15.31), "CRI": (9.93, -84.08), "CIV": (6.85, -5.30),
    "HRV": (45.81, 15.98), "CUB": (23.11, -82.37), "CYP": (35.17, 33.37),
    "CZE": (50.08, 14.43), "DNK": (55.68, 12.57), "DJI": (11.59, 43.15),
    "DMA": (15.30, -61.39), "DOM": (18.49, -69.90), "ECU": (-0.18, -78.47),
    "EGY": (30.04, 31.24), "SLV": (13.69, -89.19), "GNQ": (3.75, 8.78),
    "ERI": (15.34, 38.93), "EST": (59.44, 24.75), "SWZ": (-26.31, 31.13),
    "ETH": (9.02, 38.75), "FJI": (-18.14, 178.44), "FIN": (60.17, 24.94),
    "FRA": (48.86, 2.35), "GAB": (0.39, 9.45), "GMB": (13.45, -16.58),
    "GEO": (41.72, 44.79), "DEU": (52.52, 13.41), "GHA": (5.56, -0.19),
    "GRC": (37.98, 23.73), "GRD": (12.06, -61.75), "GTM": (14.63, -90.51),
    "GIN": (9.64, -13.58), "GNB": (11.86, -15.60), "GUY": (6.80, -58.16),
    "HTI": (18.54, -72.34), "HND": (14.07, -87.19), "HUN": (47.50, 19.04),
    "ISL": (64.15, -21.94), "IND": (28.61, 77.21), "IDN": (-6.21, 106.85),
    "IRN": (35.69, 51.42), "IRQ": (33.31, 44.37), "IRL": (53.35, -6.26),
    "ISR": (31.77, 35.22), "ITA": (41.90, 12.50), "JAM": (18.00, -76.79),
    "JPN": (35.68, 139.69), "JOR": (31.95, 35.93), "KAZ": (51.17, 71.45),
    "KEN": (-1.29, 36.82), "KIR": (1.45, 173.00), "PRK": (39.02, 125.75),
    "KOR": (37.57, 126.98), "KWT": (29.38, 47.99), "KGZ": (42.87, 74.59),
    "LAO": (17.97, 102.63), "LVA": (56.95, 24.11), "LBN": (33.89, 35.50),
    "LSO": (-29.31, 27.48), "LBR": (6.30, -10.80), "LBY": (32.90, 13.18),
    "LIE": (47.14, 9.52), "LTU": (54.69, 25.28), "LUX": (49.61, 6.13),
    "MDG": (-18.91, 47.52), "MWI": (-13.97, 33.79), "MYS": (3.14, 101.69),
    "MDV": (4.18, 73.51), "MLI": (12.64, -8.00), "MLT": (35.90, 14.51),
    "MHL": (7.09, 171.38), "MRT": (18.09, -15.98), "MUS": (-20.16, 57.50),
    "MEX": (19.43, -99.13), "FSM": (6.92, 158.16), "MDA": (47.01, 28.86),
    "MCO": (43.73, 7.42), "MNG": (47.91, 106.91), "MNE": (42.44, 19.26),
    "MAR": (34.02, -6.84), "MOZ": (-25.97, 32.57), "MMR": (19.76, 96.07),
    "NAM": (-22.56, 17.08), "NRU": (-0.55, 166.92), "NPL": (27.72, 85.32),
    "NLD": (52.37, 4.90), "NZL": (-41.29, 174.78), "NIC": (12.11, -86.27),
    "NER": (13.51, 2.11), "NGA": (9.06, 7.49), "MKD": (42.00, 21.43),
    "NOR": (59.91, 10.75), "OMN": (23.59, 58.54), "PAK": (33.69, 73.04),
    "PLW": (7.50, 134.62), "PAN": (8.98, -79.52), "PNG": (-6.31, 147.15),
    "PRY": (-25.26, -57.58), "PER": (-12.05, -77.04), "PHL": (14.60, 120.98),
    "POL": (52.23, 21.01), "PRT": (38.72, -9.14), "QAT": (25.29, 51.53),
    "ROU": (44.43, 26.10), "RUS": (55.76, 37.62), "RWA": (-1.95, 30.06),
    "KNA": (17.30, -62.72), "LCA": (14.01, -61.00), "VCT": (13.16, -61.22),
    "WSM": (-13.83, -171.74), "SMR": (43.94, 12.45), "STP": (0.34, 6.73),
    "SAU": (24.71, 46.68), "SEN": (14.72, -17.47), "SRB": (44.79, 20.47),
    "SYC": (-4.68, 55.45), "SLE": (8.48, -13.23), "SGP": (1.35, 103.82),
    "SVK": (48.15, 17.11), "SVN": (46.06, 14.51), "SLB": (-9.43, 160.03),
    "SOM": (2.05, 45.34), "ZAF": (-25.75, 28.19), "SSD": (4.85, 31.58),
    "ESP": (40.42, -3.70), "LKA": (6.93, 79.84), "SDN": (15.60, 32.53),
    "SUR": (5.85, -55.17), "SWE": (59.33, 18.07), "CHE": (46.95, 7.45),
    "SYR": (33.51, 36.29), "TWN": (25.03, 121.57), "TJK": (38.56, 68.77),
    "TZA": (-6.79, 39.28), "THA": (13.76, 100.50), "TLS": (-8.56, 125.57),
    "TGO": (6.17, 1.23), "TON": (-21.21, -175.20), "TTO": (10.66, -61.51),
    "TUN": (36.81, 10.18), "TUR": (39.93, 32.86), "TKM": (37.95, 58.38),
    "TUV": (-8.52, 179.22), "UGA": (0.35, 32.58), "UKR": (50.45, 30.52),
    "ARE": (24.45, 54.65), "GBR": (51.51, -0.13), "USA": (38.91, -77.04),
    "URY": (-34.88, -56.17), "UZB": (41.30, 69.28), "VUT": (-17.73, 168.32),
    "VEN": (10.49, -66.88), "VNM": (21.03, 105.85), "YEM": (15.35, 44.21),
    "ZMB": (-15.39, 28.32), "ZWE": (-17.83, 31.05),
    # Territories / special
    "PRI": (18.47, -66.11), "HKG": (22.32, 114.17), "MAC": (22.20, 113.55),
    "PSE": (31.90, 35.20), "XKX": (42.66, 21.17),
}


# ── World Bank batch fetcher ────────────────────────────────────────────

_WB_INDICATORS = {
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "population": "SP.POP.TOTL",
    "urbanization": "SP.URB.TOTL.IN.ZS",
    "electricity_access": "EG.ELC.ACCS.ZS",
    "kwh_per_capita": "EG.USE.ELEC.KH.PC",
}


def fetch_all_worldbank(
    cache_dir: Path = _DEFAULT_DATA_DIR / "worldbank",
) -> dict[str, dict[str, float]]:
    """Fetch World Bank indicators for ALL countries in batch.

    Uses ``country=all`` to minimize API calls (one per indicator).
    Results are cached to ``cache_dir/wb_all.json``.

    Returns dict[iso3, dict[indicator_name, value]].
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "wb_all.json"

    if cache_file.exists():
        with open(cache_file) as f:
            data = json.load(f)
        logger.info("Loaded World Bank cache (%d countries)", len(data))
        return data

    logger.info("Downloading World Bank indicators for all countries...")
    result: dict[str, dict[str, float]] = {}

    for name, indicator in _WB_INDICATORS.items():
        logger.info("  Fetching %s (%s)...", name, indicator)
        page = 1
        while True:
            url = (
                f"https://api.worldbank.org/v2/country/all/indicator/{indicator}"
                f"?format=json&per_page=300&date=2015:2025&page={page}"
                f"&source=2"
            )
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                logger.warning("  WB API error for %s page %d: %s", name, page, e)
                break

            if not isinstance(payload, list) or len(payload) < 2:
                break

            meta, records = payload[0], payload[1]
            if not records:
                break

            for rec in records:
                iso3 = rec.get("countryiso3code", "")
                val = rec.get("value")
                if not iso3 or val is None:
                    continue
                entry = result.setdefault(iso3, {})
                # Keep most recent non-null value
                if name not in entry:
                    entry[name] = float(val)

            total_pages = meta.get("pages", 1)
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.3)

        time.sleep(0.5)

    # Save cache
    with open(cache_file, "w") as f:
        json.dump(result, f, indent=2)
    logger.info("Cached World Bank data for %d countries", len(result))

    return result


# ── Our World in Data electricity ───────────────────────────────────────

def load_owid_electricity(
    cache_dir: Path = _DEFAULT_DATA_DIR / "owid",
) -> dict[str, float]:
    """Load annual electricity generation (GWh) per country from OWID.

    Downloads the OWID energy CSV if not cached. Returns most recent
    year's electricity generation per ISO3 country code.
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / "owid-energy-data.csv"

    if not csv_path.exists():
        logger.info("Downloading OWID energy dataset...")
        url = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        csv_path.write_bytes(resp.content)
        logger.info("Saved OWID data to %s", csv_path)

    import pandas as pd

    df = pd.read_csv(csv_path, usecols=["iso_code", "year", "electricity_generation"])
    df = df.dropna(subset=["iso_code", "electricity_generation"])
    df = df[df["iso_code"].str.len() == 3]  # filter out aggregates

    # Most recent year per country, convert TWh → GWh
    latest = df.sort_values("year").groupby("iso_code").last()
    result = {}
    for iso3, row in latest.iterrows():
        gwh = row["electricity_generation"] * 1000  # TWh → GWh
        if gwh > 0:
            result[iso3] = float(gwh)

    logger.info("OWID: %d countries with electricity data", len(result))
    return result


# ── UN World Population Prospects ───────────────────────────────────────

def load_un_wpp(
    csv_path: Path,
) -> dict[str, dict[int, float]]:
    """Parse UN WPP population projections CSV.

    Expects the standard WPP download with columns including
    ``ISO3_code`` (or ``ISO3 Alpha-code``), ``Time`` (year),
    and ``PopTotal`` (in thousands).

    Returns {ISO3: {year: population}}.
    """
    import pandas as pd

    logger.info("Loading UN WPP from %s ...", csv_path)
    df = pd.read_csv(csv_path, low_memory=False)

    # Find ISO3 column (naming varies between WPP editions)
    iso_col = None
    for candidate in ["ISO3_code", "ISO3 Alpha-code", "ISO3_Alpha", "iso3"]:
        if candidate in df.columns:
            iso_col = candidate
            break
    if iso_col is None:
        # Try partial match
        for col in df.columns:
            if "iso3" in col.lower() or "iso_a3" in col.lower():
                iso_col = col
                break
    if iso_col is None:
        raise ValueError(
            f"Cannot find ISO3 column in UN WPP CSV. Columns: {df.columns.tolist()}"
        )

    # Find year column
    year_col = "Time" if "Time" in df.columns else "Year"

    # Find population column
    pop_col = None
    for candidate in ["PopTotal", "TPopulation1Jan", "PopMid"]:
        if candidate in df.columns:
            pop_col = candidate
            break
    if pop_col is None:
        for col in df.columns:
            if "pop" in col.lower() and "total" in col.lower():
                pop_col = col
                break
    if pop_col is None:
        raise ValueError(
            f"Cannot find population column in UN WPP CSV. Columns: {df.columns.tolist()}"
        )

    # Filter to medium variant if column exists
    for var_col in ["Variant", "variant"]:
        if var_col in df.columns:
            medium_labels = ["Medium", "medium", "Median"]
            df = df[df[var_col].isin(medium_labels)]
            break

    result: dict[str, dict[int, float]] = {}
    for _, row in df.iterrows():
        iso3 = str(row[iso_col]).strip()
        if len(iso3) != 3:
            continue
        try:
            year = int(row[year_col])
            pop = float(row[pop_col]) * 1000  # thousands → persons
        except (ValueError, TypeError):
            continue
        if 2020 <= year <= 2100:
            result.setdefault(iso3, {})[year] = pop

    logger.info("UN WPP: %d countries, years %d-%d",
                len(result),
                min(min(v) for v in result.values()) if result else 0,
                max(max(v) for v in result.values()) if result else 0)
    return result


# ── Assemble all metadata ──────────────────────────────────────────────

def build_country_registry(
    un_wpp_csv: Optional[Path] = None,
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, CountryRecord]:
    """Assemble complete country metadata from all sources.

    Returns dict[iso3, CountryRecord] for all countries with
    at least coordinates and some economic data.
    """
    # 1. World Bank
    wb = fetch_all_worldbank(data_dir / "worldbank")

    # 2. OWID electricity
    owid = load_owid_electricity(data_dir / "owid")

    # 3. UN WPP (optional)
    wpp: dict[str, dict[int, float]] = {}
    if un_wpp_csv and un_wpp_csv.exists():
        wpp = load_un_wpp(un_wpp_csv)

    # 4. Merge into CountryRecord
    all_isos = set(COUNTRY_COORDS) | set(wb) | set(owid)
    registry: dict[str, CountryRecord] = {}

    for iso3 in sorted(all_isos):
        if iso3 not in COUNTRY_COORDS:
            continue  # skip if no coordinates

        lat, lon = COUNTRY_COORDS[iso3]
        indicators = wb.get(iso3, {})

        pop = indicators.get("population", 0.0)
        gdp = indicators.get("gdp_per_capita", 0.0)
        kwh_cap = indicators.get("kwh_per_capita", 0.0)
        urban = indicators.get("urbanization", 50.0)
        access = indicators.get("electricity_access", 100.0)

        # Annual GWh: prefer OWID, fallback to WB kWh×pop
        annual_gwh = owid.get(iso3, 0.0)
        quality = "observed" if annual_gwh > 0 else "missing"

        if annual_gwh <= 0 and kwh_cap > 0 and pop > 0:
            annual_gwh = kwh_cap * pop * (access / 100) / 1e6
            quality = "estimated_wb"

        if annual_gwh <= 0 and gdp > 0 and pop > 0:
            annual_gwh = gdp * pop * 0.4 / 1e6
            quality = "estimated_gdp"

        record = CountryRecord(
            iso3=iso3,
            latitude=lat,
            longitude=lon,
            gdp_per_capita=gdp,
            population=pop,
            urbanization_pct=urban,
            electricity_access_pct=access,
            kwh_per_capita=kwh_cap,
            annual_gwh=annual_gwh,
            data_quality=quality,
            pop_projections=wpp.get(iso3, {}),
        )
        registry[iso3] = record

    logger.info(
        "Registry: %d countries (%d observed, %d estimated, %d missing)",
        len(registry),
        sum(1 for r in registry.values() if r.data_quality == "observed"),
        sum(1 for r in registry.values() if "estimated" in r.data_quality),
        sum(1 for r in registry.values() if r.data_quality == "missing"),
    )
    return registry
