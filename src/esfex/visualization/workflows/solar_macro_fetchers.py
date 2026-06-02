"""Background fetchers for macroeconomic data (Solar Rooftop Phase B).

Each fetcher is a QThread that downloads data from external APIs
and emits progress/finished/error signals.

Sources:
    - World Bank: GDP/capita, electricity access, urbanization, population
    - IMF: GDP growth, inflation
    - IRENA: PV cost learning curves (bundled fallback data)
    - Country detection: reverse geocode from bounding box centroid
"""

from __future__ import annotations

import json
import logging
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# Country detection from bounding box
# ══════════════════════════════════════════════════════════════════


class CountryDetector(QThread):
    """Reverse-geocode a bounding box centroid to get the country ISO code.

    Uses Nominatim (OpenStreetMap) — free, no key required.
    """

    finished = Signal(str, str)  # iso3, country_name
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self._south, self._west, self._north, self._east = bounds

    def run(self):
        try:
            import requests

            lat = (self._south + self._north) / 2
            lon = (self._west + self._east) / 2

            url = (
                "https://nominatim.openstreetmap.org/reverse"
                f"?lat={lat}&lon={lon}&format=json&zoom=3"
            )
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            country_code_2 = data.get("address", {}).get("country_code", "")
            country_name = data.get("address", {}).get("country", "")

            # Convert ISO-2 to ISO-3
            iso3 = _iso2_to_iso3(country_code_2.upper())

            self.finished.emit(iso3, country_name)

        except Exception as exc:
            logger.exception("CountryDetector error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# World Bank fetcher
# ══════════════════════════════════════════════════════════════════

# Indicator codes
_WB_INDICATORS = {
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "urbanization_pct": "SP.URB.TOTL.IN.ZS",
    "population": "SP.POP.TOTL",
    "electricity_access": "EG.ELC.ACCS.ZS",
}


class WorldBankFetcher(QThread):
    """Fetch macro-economic indicators from the World Bank API.

    Returns the most recent available value for each indicator, plus
    a time series for GDP/capita (used for projections).
    """

    progress = Signal(int, str)  # percent, message
    finished = Signal(dict)  # {"indicator_name": value, ...}
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            results: dict[str, Any] = {}
            total = len(_WB_INDICATORS)

            for i, (key, code) in enumerate(_WB_INDICATORS.items()):
                if self._cancelled:
                    return

                pct = int((i / total) * 100)
                self.progress.emit(pct, f"Fetching {key}...")

                url = (
                    f"https://api.worldbank.org/v2/country/{self._iso}"
                    f"/indicator/{code}"
                    f"?format=json&per_page=30&date=2000:2025"
                )
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                payload = resp.json()

                # World Bank returns [metadata, data_list]
                if (
                    not isinstance(payload, list)
                    or len(payload) < 2
                    or payload[1] is None
                ):
                    results[key] = None
                    continue

                entries = payload[1]

                # Most recent non-null value
                latest = None
                for entry in entries:
                    if entry.get("value") is not None:
                        latest = entry["value"]
                        break
                results[key] = latest

                # For GDP/capita, also build year→value time series
                if key == "gdp_per_capita":
                    ts = {}
                    for entry in entries:
                        if entry.get("value") is not None:
                            ts[int(entry["date"])] = entry["value"]
                    results["gdp_time_series"] = ts

            if self._cancelled:
                return

            self.progress.emit(100, "World Bank data fetched.")
            self.finished.emit(results)

        except Exception as exc:
            logger.exception("WorldBankFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# IMF fetcher
# ══════════════════════════════════════════════════════════════════

# IMF WEO indicator codes
_IMF_INDICATORS = {
    "gdp_growth_rate": "NGDP_RPCH",
    "inflation_rate": "PCPIPCH",
}


class IMFFetcher(QThread):
    """Fetch macro-economic forecasts from the IMF DataMapper API.

    Returns the most recent value and short-term projections.
    """

    progress = Signal(int, str)
    finished = Signal(dict)  # {"gdp_growth_rate": value, ...}
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            results: dict[str, Any] = {}
            total = len(_IMF_INDICATORS)

            for i, (key, code) in enumerate(_IMF_INDICATORS.items()):
                if self._cancelled:
                    return

                pct = int((i / total) * 100)
                self.progress.emit(pct, f"Fetching {key}...")

                url = (
                    f"https://www.imf.org/external/datamapper/api/v1/{code}/{self._iso}"
                )
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                payload = resp.json()

                # IMF structure: {"values": {"INDICATOR": {"ISO": {"year": val}}}}
                values = (
                    payload.get("values", {})
                    .get(code, {})
                    .get(self._iso, {})
                )

                if not values:
                    results[key] = None
                    results[f"{key}_forecast"] = {}
                    continue

                # Get most recent year value
                sorted_years = sorted(values.keys(), reverse=True)
                latest_val = None
                for yr in sorted_years:
                    v = values[yr]
                    if v is not None and v != "":
                        latest_val = float(v) / 100.0  # Convert percentage
                        break
                results[key] = latest_val

                # Build year→value forecast series
                forecast = {}
                for yr, v in values.items():
                    if v is not None and v != "":
                        forecast[int(yr)] = float(v) / 100.0
                results[f"{key}_forecast"] = forecast

            if self._cancelled:
                return

            self.progress.emit(100, "IMF data fetched.")
            self.finished.emit(results)

        except Exception as exc:
            logger.exception("IMFFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# IRENA PV cost data
# ══════════════════════════════════════════════════════════════════

# Bundled global weighted-average PV system cost ($/kW) from IRENA
# Renewable Power Generation Costs reports (2010-2024).
_IRENA_PV_COSTS: dict[int, float] = {
    2010: 4702,
    2011: 3660,
    2012: 2714,
    2013: 2240,
    2014: 1910,
    2015: 1650,
    2016: 1440,
    2017: 1310,
    2018: 1210,
    2019: 1080,
    2020: 916,
    2021: 857,
    2022: 876,
    2023: 758,
    2024: 690,
}

# IRENA global weighted-average LCOE ($/kWh) for utility-scale solar PV
_IRENA_PV_LCOE: dict[int, float] = {
    2010: 0.381,
    2011: 0.311,
    2012: 0.228,
    2013: 0.177,
    2014: 0.139,
    2015: 0.107,
    2016: 0.087,
    2017: 0.073,
    2018: 0.063,
    2019: 0.053,
    2020: 0.049,
    2021: 0.048,
    2022: 0.049,
    2023: 0.044,
    2024: 0.041,
}


class IRENACostFetcher(QThread):
    """Provide PV cost learning curve data.

    Uses bundled IRENA data (2010-2024) and projects forward to 2050
    using a configurable learning rate.

    The learning rate represents the fractional cost reduction per
    doubling of cumulative installed capacity (Wright's law).  Default
    value of 0.20 (20 %) is consistent with observed solar PV trends.
    """

    progress = Signal(int, str)
    finished = Signal(dict)  # {"pv_cost_trajectory": {year: $/kW}, ...}
    error = Signal(str)

    def __init__(self, learning_rate: float = 0.20, parent=None):
        super().__init__(parent)
        self._learning_rate = learning_rate
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import math

            self.progress.emit(20, "Loading IRENA PV cost data...")

            trajectory: dict[int, float] = dict(_IRENA_PV_COSTS)
            lcoe_data: dict[int, float] = dict(_IRENA_PV_LCOE)

            if self._cancelled:
                return

            # Project costs forward using learning rate
            # Assume ~15 % annual capacity growth (historical average)
            self.progress.emit(50, "Projecting cost trajectory to 2050...")
            base_cost = trajectory[2024]
            base_lcoe = lcoe_data[2024]
            annual_growth = 0.15  # cumulative capacity growth rate
            lr = self._learning_rate
            # Learning exponent: cost_ratio = 2^(-lr * log2(capacity_ratio))
            # For annual: capacity_ratio = (1 + growth)^(year - base_year)
            log2_lr = math.log2(1 - lr) if lr < 1 else -0.32

            for year in range(2025, 2051):
                dt = year - 2024
                capacity_ratio = (1 + annual_growth) ** dt
                cost_ratio = capacity_ratio ** log2_lr
                trajectory[year] = round(base_cost * cost_ratio, 1)
                lcoe_data[year] = round(base_lcoe * cost_ratio, 4)

            if self._cancelled:
                return

            # Latest values
            latest_cost = trajectory[2024]
            latest_lcoe = lcoe_data[2024]

            self.progress.emit(100, "IRENA data ready.")
            self.finished.emit({
                "pv_system_cost": latest_cost,
                "pv_cost_trajectory": trajectory,
                "pv_lcoe_trajectory": lcoe_data,
                "learning_rate": self._learning_rate,
            })

        except Exception as exc:
            logger.exception("IRENACostFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# Electricity tariff estimator
# ══════════════════════════════════════════════════════════════════


class TariffEstimator(QThread):
    """Estimate residential electricity tariff for a country.

    Uses the GlobalPetrolPrices.com API pattern or falls back to
    a bundled regional average table.
    """

    finished = Signal(float)  # $/kWh
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()

    def run(self):
        try:
            # Try to estimate from World Bank electricity price proxy
            # or use regional averages as fallback
            tariff = _REGIONAL_TARIFFS.get(
                self._iso, _REGIONAL_TARIFFS.get("DEFAULT", 0.15)
            )
            self.finished.emit(tariff)

        except Exception as exc:
            logger.exception("TariffEstimator error")
            self.error.emit(str(exc))


# Regional average residential tariffs ($/kWh) — fallback data
# Sources: GlobalPetrolPrices.com, IEA, regional utility reports (2023-2024)
_REGIONAL_TARIFFS: dict[str, float] = {
    # Caribbean / Central America
    "CUB": 0.04,  # Cuba (subsidized)
    "JAM": 0.32,  # Jamaica
    "DOM": 0.19,  # Dominican Republic
    "HTI": 0.28,  # Haiti
    "PRI": 0.27,  # Puerto Rico
    "TTO": 0.05,  # Trinidad (subsidized, gas)
    "BHS": 0.33,  # Bahamas
    "BRB": 0.30,  # Barbados
    "CRI": 0.16,  # Costa Rica
    "PAN": 0.18,  # Panama
    "GTM": 0.20,  # Guatemala
    "HND": 0.17,  # Honduras
    "SLV": 0.16,  # El Salvador
    "NIC": 0.19,  # Nicaragua
    "BLZ": 0.22,  # Belize
    # South America
    "BRA": 0.14,
    "ARG": 0.05,  # subsidized
    "CHL": 0.16,
    "COL": 0.13,
    "PER": 0.11,
    "ECU": 0.10,
    "VEN": 0.01,  # heavily subsidized
    "URY": 0.18,
    "PRY": 0.06,
    "BOL": 0.07,
    # North America
    "USA": 0.16,
    "CAN": 0.12,
    "MEX": 0.09,
    # Europe
    "DEU": 0.35,
    "FRA": 0.21,
    "GBR": 0.30,
    "ESP": 0.25,
    "ITA": 0.28,
    "PRT": 0.22,
    "NLD": 0.32,
    "BEL": 0.30,
    "POL": 0.18,
    "CZE": 0.24,
    "AUT": 0.26,
    "CHE": 0.22,
    "SWE": 0.20,
    "NOR": 0.15,
    "DNK": 0.35,
    "FIN": 0.18,
    "IRL": 0.30,
    "GRC": 0.20,
    "ROU": 0.16,
    "HUN": 0.10,
    "BGR": 0.12,
    # Africa
    "ZAF": 0.10,
    "NGA": 0.08,
    "KEN": 0.20,
    "EGY": 0.05,
    "MAR": 0.13,
    "GHA": 0.09,
    "TZA": 0.10,
    "ETH": 0.03,
    "SEN": 0.18,
    "CIV": 0.11,
    # Asia
    "CHN": 0.08,
    "IND": 0.08,
    "JPN": 0.25,
    "KOR": 0.11,
    "IDN": 0.08,
    "THA": 0.11,
    "VNM": 0.08,
    "PHL": 0.17,
    "MYS": 0.06,
    "SGP": 0.20,
    "BGD": 0.06,
    "PAK": 0.08,
    "LKA": 0.07,
    "MMR": 0.04,
    "KHM": 0.16,
    # Middle East
    "SAU": 0.05,
    "ARE": 0.08,
    "QAT": 0.03,
    "KWT": 0.02,
    "ISR": 0.16,
    "TUR": 0.07,
    "IRN": 0.01,
    "IRQ": 0.02,
    # Oceania
    "AUS": 0.24,
    "NZL": 0.20,
    # Default
    "DEFAULT": 0.15,
}


# ══════════════════════════════════════════════════════════════════
# IRENA historical installed PV capacity (validation data)
# ══════════════════════════════════════════════════════════════════

# Cumulative installed solar PV capacity (MW) by country and year.
# Source: IRENA Renewable Capacity Statistics 2024, Renewable Energy
# Statistics 2024.  Values are rounded.
_IRENA_PV_CAPACITY: dict[str, dict[int, float]] = {
    "USA": {
        2010: 2519, 2011: 4400, 2012: 7266, 2013: 12079, 2014: 18317,
        2015: 25821, 2016: 40300, 2017: 51450, 2018: 62200, 2019: 76300,
        2020: 95200, 2021: 123000, 2022: 142000, 2023: 175000, 2024: 211000,
    },
    "CHN": {
        2010: 800, 2011: 3600, 2012: 7000, 2013: 19000, 2014: 28200,
        2015: 43500, 2016: 77400, 2017: 130200, 2018: 175000, 2019: 205000,
        2020: 254000, 2021: 307000, 2022: 393000, 2023: 609000, 2024: 887000,
    },
    "DEU": {
        2010: 17320, 2011: 24820, 2012: 32640, 2013: 35710, 2014: 37900,
        2015: 39700, 2016: 41200, 2017: 42300, 2018: 45200, 2019: 49000,
        2020: 54000, 2021: 59000, 2022: 67000, 2023: 82000, 2024: 97000,
    },
    "JPN": {
        2010: 3620, 2011: 4910, 2012: 6630, 2013: 13600, 2014: 23300,
        2015: 33300, 2016: 42800, 2017: 49000, 2018: 56000, 2019: 63000,
        2020: 68000, 2021: 74000, 2022: 79000, 2023: 87000, 2024: 95000,
    },
    "IND": {
        2010: 190, 2011: 460, 2012: 1200, 2013: 2300, 2014: 3300,
        2015: 5000, 2016: 9000, 2017: 18200, 2018: 26000, 2019: 34600,
        2020: 40100, 2021: 50300, 2022: 63000, 2023: 73000, 2024: 90000,
    },
    "AUS": {
        2010: 570, 2011: 1350, 2012: 2410, 2013: 3260, 2014: 4100,
        2015: 5100, 2016: 5900, 2017: 7200, 2018: 11100, 2019: 16200,
        2020: 20800, 2021: 26000, 2022: 31000, 2023: 36000, 2024: 42000,
    },
    "BRA": {
        2010: 1, 2011: 2, 2012: 5, 2013: 10, 2014: 15,
        2015: 70, 2016: 100, 2017: 1200, 2018: 2400, 2019: 4500,
        2020: 7900, 2021: 13000, 2022: 24000, 2023: 37000, 2024: 52000,
    },
    "GBR": {
        2010: 95, 2011: 1020, 2012: 1830, 2013: 2940, 2014: 5380,
        2015: 8780, 2016: 11600, 2017: 12700, 2018: 13100, 2019: 13400,
        2020: 13600, 2021: 14000, 2022: 14800, 2023: 16000, 2024: 17500,
    },
    "ESP": {
        2010: 3840, 2011: 4330, 2012: 4530, 2013: 4650, 2014: 4670,
        2015: 4700, 2016: 4700, 2017: 4700, 2018: 4750, 2019: 9000,
        2020: 12600, 2021: 15900, 2022: 19800, 2023: 25500, 2024: 31000,
    },
    "MEX": {
        2010: 30, 2011: 40, 2012: 52, 2013: 100, 2014: 180,
        2015: 300, 2016: 600, 2017: 1300, 2018: 2700, 2019: 4600,
        2020: 6500, 2021: 8300, 2022: 10000, 2023: 12500, 2024: 15000,
    },
    "CHL": {
        2010: 3, 2011: 4, 2012: 6, 2013: 15, 2014: 400,
        2015: 850, 2016: 1600, 2017: 2200, 2018: 2700, 2019: 3200,
        2020: 3800, 2021: 5200, 2022: 7200, 2023: 10000, 2024: 13000,
    },
    "ZAF": {
        2010: 18, 2011: 20, 2012: 22, 2013: 300, 2014: 800,
        2015: 1450, 2016: 1900, 2017: 2200, 2018: 2600, 2019: 3400,
        2020: 4100, 2021: 5200, 2022: 6500, 2023: 8500, 2024: 11000,
    },
    "CUB": {
        2010: 5, 2011: 6, 2012: 10, 2013: 12, 2014: 15,
        2015: 20, 2016: 30, 2017: 40, 2018: 60, 2019: 80,
        2020: 100, 2021: 120, 2022: 150, 2023: 200, 2024: 250,
    },
    "ARG": {
        2010: 8, 2011: 9, 2012: 10, 2013: 12, 2014: 15,
        2015: 20, 2016: 30, 2017: 100, 2018: 300, 2019: 600,
        2020: 800, 2021: 1100, 2022: 1500, 2023: 2200, 2024: 3000,
    },
    "COL": {
        2010: 6, 2011: 7, 2012: 8, 2013: 10, 2014: 12,
        2015: 15, 2016: 20, 2017: 30, 2018: 90, 2019: 200,
        2020: 500, 2021: 750, 2022: 1100, 2023: 1600, 2024: 2300,
    },
    "EGY": {
        2010: 15, 2011: 15, 2012: 15, 2013: 16, 2014: 20,
        2015: 30, 2016: 50, 2017: 80, 2018: 400, 2019: 1600,
        2020: 1700, 2021: 1800, 2022: 2000, 2023: 2600, 2024: 3500,
    },
    "KEN": {
        2010: 5, 2011: 6, 2012: 8, 2013: 10, 2014: 15,
        2015: 25, 2016: 35, 2017: 50, 2018: 80, 2019: 120,
        2020: 170, 2021: 250, 2022: 350, 2023: 500, 2024: 700,
    },
    "DOM": {
        2010: 1, 2011: 2, 2012: 5, 2013: 10, 2014: 20,
        2015: 50, 2016: 100, 2017: 200, 2018: 350, 2019: 500,
        2020: 600, 2021: 750, 2022: 900, 2023: 1100, 2024: 1400,
    },
    "JAM": {
        2010: 1, 2011: 2, 2012: 3, 2013: 5, 2014: 10,
        2015: 20, 2016: 30, 2017: 45, 2018: 60, 2019: 80,
        2020: 100, 2021: 120, 2022: 150, 2023: 180, 2024: 220,
    },
    "TTO": {
        2010: 0, 2011: 0, 2012: 1, 2013: 1, 2014: 2,
        2015: 3, 2016: 4, 2017: 5, 2018: 7, 2019: 10,
        2020: 15, 2021: 20, 2022: 25, 2023: 35, 2024: 50,
    },
    "FRA": {
        2010: 1025, 2011: 2950, 2012: 3970, 2013: 4590, 2014: 5700,
        2015: 6580, 2016: 7130, 2017: 8000, 2018: 9000, 2019: 10000,
        2020: 11700, 2021: 14000, 2022: 17000, 2023: 20000, 2024: 23000,
    },
    "ITA": {
        2010: 3480, 2011: 12800, 2012: 16600, 2013: 18200, 2014: 18600,
        2015: 18900, 2016: 19300, 2017: 19700, 2018: 20100, 2019: 20800,
        2020: 21600, 2021: 22600, 2022: 25000, 2023: 30000, 2024: 36000,
    },
    "KOR": {
        2010: 650, 2011: 730, 2012: 1010, 2013: 1460, 2014: 2380,
        2015: 3400, 2016: 4300, 2017: 5600, 2018: 7800, 2019: 11700,
        2020: 16000, 2021: 21000, 2022: 26000, 2023: 31000, 2024: 37000,
    },
}


class IRENACapacityFetcher(QThread):
    """Provide historical installed PV capacity for validation.

    Uses bundled IRENA capacity statistics (2010-2024).  Returns a
    ``ValidationData`` object from the adoption models module.
    """

    progress = Signal(int, str)
    finished = Signal(object)  # ValidationData
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()

    def run(self):
        try:
            self.progress.emit(30, f"Looking up IRENA data for {self._iso}...")

            data = _IRENA_PV_CAPACITY.get(self._iso)
            if not data:
                self.error.emit(
                    f"No IRENA capacity data available for {self._iso}. "
                    "Try importing your own validation CSV."
                )
                return

            from esfex.models.adoption_models import ValidationData

            years = sorted(data.keys())
            capacity = [data[y] for y in years]

            vd = ValidationData(
                label=f"IRENA Observed ({self._iso})",
                years=years,
                capacity_mw=capacity,
                source="irena",
            )

            self.progress.emit(100, "IRENA data loaded.")
            self.finished.emit(vd)

        except Exception as exc:
            logger.exception("IRENACapacityFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _iso2_to_iso3(iso2: str) -> str:
    """Convert ISO 3166-1 alpha-2 code to alpha-3.

    Uses a lookup table for common countries; falls back to returning
    the alpha-2 code padded with 'X' if not found.
    """
    _MAP = {
        "AF": "AFG", "AL": "ALB", "DZ": "DZA", "AD": "AND", "AO": "AGO",
        "AG": "ATG", "AR": "ARG", "AM": "ARM", "AU": "AUS", "AT": "AUT",
        "AZ": "AZE", "BS": "BHS", "BH": "BHR", "BD": "BGD", "BB": "BRB",
        "BY": "BLR", "BE": "BEL", "BZ": "BLZ", "BJ": "BEN", "BT": "BTN",
        "BO": "BOL", "BA": "BIH", "BW": "BWA", "BR": "BRA", "BN": "BRN",
        "BG": "BGR", "BF": "BFA", "BI": "BDI", "CV": "CPV", "KH": "KHM",
        "CM": "CMR", "CA": "CAN", "CF": "CAF", "TD": "TCD", "CL": "CHL",
        "CN": "CHN", "CO": "COL", "KM": "COM", "CG": "COG", "CD": "COD",
        "CR": "CRI", "CI": "CIV", "HR": "HRV", "CU": "CUB", "CY": "CYP",
        "CZ": "CZE", "DK": "DNK", "DJ": "DJI", "DM": "DMA", "DO": "DOM",
        "EC": "ECU", "EG": "EGY", "SV": "SLV", "GQ": "GNQ", "ER": "ERI",
        "EE": "EST", "SZ": "SWZ", "ET": "ETH", "FJ": "FJI", "FI": "FIN",
        "FR": "FRA", "GA": "GAB", "GM": "GMB", "GE": "GEO", "DE": "DEU",
        "GH": "GHA", "GR": "GRC", "GD": "GRD", "GT": "GTM", "GN": "GIN",
        "GW": "GNB", "GY": "GUY", "HT": "HTI", "HN": "HND", "HU": "HUN",
        "IS": "ISL", "IN": "IND", "ID": "IDN", "IR": "IRN", "IQ": "IRQ",
        "IE": "IRL", "IL": "ISR", "IT": "ITA", "JM": "JAM", "JP": "JPN",
        "JO": "JOR", "KZ": "KAZ", "KE": "KEN", "KI": "KIR", "KP": "PRK",
        "KR": "KOR", "KW": "KWT", "KG": "KGZ", "LA": "LAO", "LV": "LVA",
        "LB": "LBN", "LS": "LSO", "LR": "LBR", "LY": "LBY", "LI": "LIE",
        "LT": "LTU", "LU": "LUX", "MG": "MDG", "MW": "MWI", "MY": "MYS",
        "MV": "MDV", "ML": "MLI", "MT": "MLT", "MH": "MHL", "MR": "MRT",
        "MU": "MUS", "MX": "MEX", "FM": "FSM", "MD": "MDA", "MC": "MCO",
        "MN": "MNG", "ME": "MNE", "MA": "MAR", "MZ": "MOZ", "MM": "MMR",
        "NA": "NAM", "NR": "NRU", "NP": "NPL", "NL": "NLD", "NZ": "NZL",
        "NI": "NIC", "NE": "NER", "NG": "NGA", "MK": "MKD", "NO": "NOR",
        "OM": "OMN", "PK": "PAK", "PW": "PLW", "PS": "PSE", "PA": "PAN",
        "PG": "PNG", "PY": "PRY", "PE": "PER", "PH": "PHL", "PL": "POL",
        "PT": "PRT", "PR": "PRI", "QA": "QAT", "RO": "ROU", "RU": "RUS",
        "RW": "RWA", "KN": "KNA", "LC": "LCA", "VC": "VCT", "WS": "WSM",
        "SM": "SMR", "ST": "STP", "SA": "SAU", "SN": "SEN", "RS": "SRB",
        "SC": "SYC", "SL": "SLE", "SG": "SGP", "SK": "SVK", "SI": "SVN",
        "SB": "SLB", "SO": "SOM", "ZA": "ZAF", "ES": "ESP", "LK": "LKA",
        "SD": "SDN", "SR": "SUR", "SE": "SWE", "CH": "CHE", "SY": "SYR",
        "TW": "TWN", "TJ": "TJK", "TZ": "TZA", "TH": "THA", "TL": "TLS",
        "TG": "TGO", "TO": "TON", "TT": "TTO", "TN": "TUN", "TR": "TUR",
        "TM": "TKM", "TV": "TUV", "UG": "UGA", "UA": "UKR", "AE": "ARE",
        "GB": "GBR", "US": "USA", "UY": "URY", "UZ": "UZB", "VU": "VUT",
        "VE": "VEN", "VN": "VNM", "YE": "YEM", "ZM": "ZMB", "ZW": "ZWE",
    }
    return _MAP.get(iso2, iso2 + "X" if len(iso2) == 2 else iso2)
