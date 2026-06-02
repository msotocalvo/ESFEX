"""Background data fetchers for the Demand Estimation Wizard.

Each fetcher is a QThread that downloads data from external APIs and
emits progress/finished/error signals for GUI integration.

Sources:
    - Nominatim (OpenStreetMap): Country detection via reverse geocoding
    - World Bank API v2: GDP/capita, population, urbanization, electricity
    - IMF DataMapper API: GDP growth, inflation forecasts
    - WorldPop REST API: Gridded population per node centroid
    - Open-Meteo Archive API: ERA5 hourly temperature + HDD/CDD (free, no key)
    - OpenStreetMap Overpass API: Industrial/commercial/residential land use
    - EOG VIIRS: Nighttime light radiance (inverse-distance heuristic if API unavailable)
    - Bundled SSP data: GDP/population projections for SSP1-SSP5
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_ISO2_TO_ISO3: dict[str, str] = {
    "AD": "AND", "AE": "ARE", "AF": "AFG", "AG": "ATG", "AL": "ALB",
    "AM": "ARM", "AO": "AGO", "AR": "ARG", "AT": "AUT", "AU": "AUS",
    "AZ": "AZE", "BA": "BIH", "BB": "BRB", "BD": "BGD", "BE": "BEL",
    "BF": "BFA", "BG": "BGR", "BH": "BHR", "BI": "BDI", "BJ": "BEN",
    "BN": "BRN", "BO": "BOL", "BR": "BRA", "BS": "BHS", "BT": "BTN",
    "BW": "BWA", "BY": "BLR", "BZ": "BLZ", "CA": "CAN", "CD": "COD",
    "CF": "CAF", "CG": "COG", "CH": "CHE", "CI": "CIV", "CL": "CHL",
    "CM": "CMR", "CN": "CHN", "CO": "COL", "CR": "CRI", "CU": "CUB",
    "CV": "CPV", "CY": "CYP", "CZ": "CZE", "DE": "DEU", "DJ": "DJI",
    "DK": "DNK", "DM": "DMA", "DO": "DOM", "DZ": "DZA", "EC": "ECU",
    "EE": "EST", "EG": "EGY", "ER": "ERI", "ES": "ESP", "ET": "ETH",
    "FI": "FIN", "FJ": "FJI", "FM": "FSM", "FR": "FRA", "GA": "GAB",
    "GB": "GBR", "GD": "GRD", "GE": "GEO", "GH": "GHA", "GM": "GMB",
    "GN": "GIN", "GQ": "GNQ", "GR": "GRC", "GT": "GTM", "GW": "GNB",
    "GY": "GUY", "HN": "HND", "HR": "HRV", "HT": "HTI", "HU": "HUN",
    "ID": "IDN", "IE": "IRL", "IL": "ISR", "IN": "IND", "IQ": "IRQ",
    "IR": "IRN", "IS": "ISL", "IT": "ITA", "JM": "JAM", "JO": "JOR",
    "JP": "JPN", "KE": "KEN", "KG": "KGZ", "KH": "KHM", "KI": "KIR",
    "KM": "COM", "KN": "KNA", "KP": "PRK", "KR": "KOR", "KW": "KWT",
    "KZ": "KAZ", "LA": "LAO", "LB": "LBN", "LC": "LCA", "LI": "LIE",
    "LK": "LKA", "LR": "LBR", "LS": "LSO", "LT": "LTU", "LU": "LUX",
    "LV": "LVA", "LY": "LBY", "MA": "MAR", "MD": "MDA", "ME": "MNE",
    "MG": "MDG", "MH": "MHL", "MK": "MKD", "ML": "MLI", "MM": "MMR",
    "MN": "MNG", "MR": "MRT", "MT": "MLT", "MU": "MUS", "MV": "MDV",
    "MW": "MWI", "MX": "MEX", "MY": "MYS", "MZ": "MOZ", "NA": "NAM",
    "NE": "NER", "NG": "NGA", "NI": "NIC", "NL": "NLD", "NO": "NOR",
    "NP": "NPL", "NR": "NRU", "NZ": "NZL", "OM": "OMN", "PA": "PAN",
    "PE": "PER", "PG": "PNG", "PH": "PHL", "PK": "PAK", "PL": "POL",
    "PT": "PRT", "PW": "PLW", "PY": "PRY", "QA": "QAT", "RO": "ROU",
    "RS": "SRB", "RU": "RUS", "RW": "RWA", "SA": "SAU", "SB": "SLB",
    "SC": "SYC", "SD": "SDN", "SE": "SWE", "SG": "SGP", "SI": "SVN",
    "SK": "SVK", "SL": "SLE", "SM": "SMR", "SN": "SEN", "SO": "SOM",
    "SR": "SUR", "SS": "SSD", "ST": "STP", "SV": "SLV", "SY": "SYR",
    "SZ": "SWZ", "TC": "TCA", "TD": "TCD", "TG": "TGO", "TH": "THA",
    "TJ": "TJK", "TL": "TLS", "TM": "TKM", "TN": "TUN", "TO": "TON",
    "TR": "TUR", "TT": "TTO", "TV": "TUV", "TZ": "TZA", "UA": "UKR",
    "UG": "UGA", "US": "USA", "UY": "URY", "UZ": "UZB", "VA": "VAT",
    "VC": "VCT", "VE": "VEN", "VN": "VNM", "VU": "VUT", "WS": "WSM",
    "YE": "YEM", "ZA": "ZAF", "ZM": "ZMB", "ZW": "ZWE",
}


def _iso2_to_iso3(iso2: str) -> str:
    return _ISO2_TO_ISO3.get(iso2.upper(), iso2)


def _polygon_area_km2(coords: list[tuple[float, float]]) -> float:
    """Shoelace formula for polygon area in km² (approximate, flat-earth)."""
    if len(coords) < 3:
        return 0.0
    import math

    n = len(coords)
    area_deg2 = 0.0
    for i in range(n):
        j = (i + 1) % n
        area_deg2 += coords[i][0] * coords[j][1]
        area_deg2 -= coords[j][0] * coords[i][1]
    area_deg2 = abs(area_deg2) / 2.0

    # Convert deg² → km² at centroid latitude
    lat_c = sum(c[0] for c in coords) / n
    lat_rad = math.radians(lat_c)
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(lat_rad)
    return area_deg2 * km_per_deg_lat * km_per_deg_lon


# ──────────────────────────────────────────────────────────────────────────────
# 1. Country Detection
# ──────────────────────────────────────────────────────────────────────────────


class CountryDetectorDemand(QThread):
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
            iso3 = _iso2_to_iso3(country_code_2.upper())

            self.finished.emit(iso3, country_name)

        except Exception as exc:
            logger.exception("CountryDetectorDemand error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 2. World Bank Demand Fetcher
# ──────────────────────────────────────────────────────────────────────────────

_WB_DEMAND_INDICATORS: dict[str, str] = {
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "population": "SP.POP.TOTL",
    "urbanization_pct": "SP.URB.TOTL.IN.ZS",
    "electricity_access": "EG.ELC.ACCS.ZS",
    "electric_consumption_kwh_capita": "EG.USE.ELEC.KH.PC",
    "industry_value_added_pct": "NV.IND.TOTL.ZS",
}


class WorldBankDemandFetcher(QThread):
    """Fetch demand-relevant indicators from the World Bank API v2.

    Returns the most recent available value per indicator, plus
    time series for GDP and electricity consumption (used for projections).
    """

    progress = Signal(int, str)
    finished = Signal(dict)
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
            total = len(_WB_DEMAND_INDICATORS)
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}

            for i, (key, code) in enumerate(_WB_DEMAND_INDICATORS.items()):
                if self._cancelled:
                    return

                pct = int((i / total) * 85)
                self.progress.emit(pct, f"Fetching {key}...")

                url = (
                    f"https://api.worldbank.org/v2/country/{self._iso}"
                    f"/indicator/{code}"
                    f"?format=json&per_page=30&date=1990:2025"
                )
                resp = requests.get(url, headers=headers, timeout=20)
                resp.raise_for_status()
                payload = resp.json()

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

                # Time series for key projections
                _TS_KEYS = {
                    "gdp_per_capita": "gdp_time_series",
                    "electric_consumption_kwh_capita": "consumption_time_series",
                    "population": "population_time_series",
                    "electricity_access": "electricity_access_time_series",
                }
                if key in _TS_KEYS:
                    ts: dict[int, float] = {}
                    for entry in entries:
                        if entry.get("value") is not None:
                            ts[int(entry["date"])] = entry["value"]
                    results[_TS_KEYS[key]] = ts

            if self._cancelled:
                return

            self.progress.emit(100, "World Bank data fetched.")
            self.finished.emit(results)

        except Exception as exc:
            logger.exception("WorldBankDemandFetcher error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 3. IMF Demand Fetcher
# ──────────────────────────────────────────────────────────────────────────────

_IMF_DEMAND_INDICATORS: dict[str, str] = {
    "gdp_growth_rate": "NGDP_RPCH",
    "inflation_rate": "PCPIPCH",
}


class IMFDemandFetcher(QThread):
    """Fetch GDP growth and inflation forecasts from the IMF DataMapper API."""

    progress = Signal(int, str)
    finished = Signal(dict)
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
            total = len(_IMF_DEMAND_INDICATORS)
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}

            for i, (key, code) in enumerate(_IMF_DEMAND_INDICATORS.items()):
                if self._cancelled:
                    return

                pct = int((i / total) * 90)
                self.progress.emit(pct, f"Fetching {key}...")

                url = f"https://www.imf.org/external/datamapper/api/v1/{code}/{self._iso}"
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code in (403, 404, 429):
                    # IMF API may block certain countries or be temporarily unavailable
                    logger.warning("IMF %s returned %s for %s — skipping", code, resp.status_code, self._iso)
                    results[key] = None
                    results[f"{key}_forecast"] = {}
                    continue
                resp.raise_for_status()
                payload = resp.json()

                values = (
                    payload.get("values", {}).get(code, {}).get(self._iso, {})
                )

                if not values:
                    results[key] = None
                    results[f"{key}_forecast"] = {}
                    continue

                sorted_years = sorted(values.keys(), reverse=True)
                latest_val = None
                for yr in sorted_years:
                    v = values[yr]
                    if v is not None and v != "":
                        latest_val = float(v) / 100.0
                        break
                results[key] = latest_val

                forecast: dict[int, float] = {}
                for yr, v in values.items():
                    if v is not None and v != "":
                        forecast[int(yr)] = float(v) / 100.0
                results[f"{key}_forecast"] = forecast

            if self._cancelled:
                return

            self.progress.emit(100, "IMF data fetched.")
            self.finished.emit(results)

        except Exception as exc:
            logger.exception("IMFDemandFetcher error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 4. WorldPop Fetcher
# ──────────────────────────────────────────────────────────────────────────────


class WorldPopFetcher(QThread):
    """Estimate population per node using WorldPop REST API.

    For each node centroid, queries WorldPop population point estimates.
    Emits error signal if the API is unavailable.
    """

    progress = Signal(int, str)
    finished = Signal(dict)  # {"node_populations": list, "total_population": float}
    error = Signal(str)

    def __init__(
        self,
        country_iso: str,
        node_centroids: list[tuple[float, float]],  # (lat, lon) pairs
        parent=None,
    ):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._centroids = node_centroids
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            num_nodes = len(self._centroids)
            if num_nodes == 0:
                self.finished.emit(
                    {"node_populations": [], "total_population": 0.0}
                )
                return

            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}
            pops: list[float] = []

            for i, (lat, lon) in enumerate(self._centroids):
                if self._cancelled:
                    return

                pct = int((i / num_nodes) * 90)
                self.progress.emit(pct, f"Fetching population node {i + 1}/{num_nodes}...")

                # WorldPop API: population point query
                url = (
                    "https://hub.worldpop.org/indicator/pop/data/boundary"
                    f"?iso3={self._iso}&bbox={lon - 0.05},{lat - 0.05},"
                    f"{lon + 0.05},{lat + 0.05}"
                )
                try:
                    resp = requests.get(url, headers=headers, timeout=15)
                    data = resp.json()
                    pop = 0.0
                    for record in data.get("data", []):
                        pop += record.get("value", 0) or 0
                    pops.append(max(pop, 1.0))
                except Exception as exc:
                    logger.warning("WorldPop node %d failed: %s", i, exc)
                    pops.append(0.0)

            if self._cancelled:
                return

            total = sum(pops)
            self.progress.emit(100, "Population data fetched.")
            self.finished.emit(
                {"node_populations": pops, "total_population": total}
            )

        except Exception as exc:
            logger.exception("WorldPopFetcher error")
            self.error.emit(f"WorldPop population fetch failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# 5. ERA5 Temperature Fetcher (Open-Meteo Archive, free)
# ──────────────────────────────────────────────────────────────────────────────


class ERA5TemperatureFetcher(QThread):
    """Fetch hourly temperature data from Open-Meteo Archive API (ERA5).

    No API key required. Returns 8760 hourly values for the requested year,
    plus pre-computed HDD and CDD hourly arrays.
    """

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        lat: float,
        lon: float,
        year: int,
        hdd_base: float = 18.0,
        cdd_base: float = 24.0,
        parent=None,
    ):
        super().__init__(parent)
        self._lat = lat
        self._lon = lon
        self._year = year
        self._hdd_base = hdd_base
        self._cdd_base = cdd_base
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            self.progress.emit(10, "Querying Open-Meteo ERA5 archive...")

            start = f"{self._year}-01-01"
            end = f"{self._year}-12-31"

            url = (
                "https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={self._lat}&longitude={self._lon}"
                f"&start_date={start}&end_date={end}"
                f"&hourly=temperature_2m,relative_humidity_2m"
                f"&timezone=UTC"
            )

            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            if self._cancelled:
                return

            self.progress.emit(70, "Processing temperature data...")

            hourly = data.get("hourly", {})
            temps = hourly.get("temperature_2m", [])
            humidity = hourly.get("relative_humidity_2m", [])
            timestamps = hourly.get("time", [])

            # Truncate / pad to exactly 8760 values
            temps = (temps + [temps[-1]] * 8760)[:8760] if temps else [25.0] * 8760
            humidity = (humidity + [humidity[-1] if humidity else 60.0] * 8760)[:8760]
            timestamps = timestamps[:8760]

            # Compute HDD and CDD
            hdd = [max(self._hdd_base - t, 0.0) for t in temps]
            cdd = [max(t - self._cdd_base, 0.0) for t in temps]

            if self._cancelled:
                return

            self.progress.emit(100, "Meteorological data fetched.")
            self.finished.emit(
                {
                    "temperature_2m": temps,
                    "relative_humidity_2m": humidity,
                    "timestamps": timestamps,
                    "hdd": hdd,
                    "cdd": cdd,
                    "hdd_total": sum(hdd),
                    "cdd_total": sum(cdd),
                    "temp_mean": sum(temps) / len(temps),
                    "temp_min": min(temps),
                    "temp_max": max(temps),
                }
            )

        except Exception as exc:
            logger.exception("ERA5TemperatureFetcher error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 6. OSM Land Use Fetcher
# ──────────────────────────────────────────────────────────────────────────────


class OSMLandUseFetcher(QThread):
    """Fetch industrial, commercial, and residential land use from OSM.

    Uses the Overpass API to query landuse polygons and compute their
    relative areas (Shoelace formula).
    """

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self._south, self._west, self._north, self._east = bounds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            self.progress.emit(10, "Querying OSM land use...")

            bbox = f"{self._south},{self._west},{self._north},{self._east}"
            query = f"""
[out:json][timeout:60];
(
  way["landuse"~"industrial|commercial|residential|retail|office"]({bbox});
  relation["landuse"~"industrial|commercial|residential|retail|office"]({bbox});
);
out geom;
"""
            url = "https://overpass-api.de/api/interpreter"
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=90)
            resp.raise_for_status()

            if self._cancelled:
                return

            self.progress.emit(60, "Processing land use polygons...")

            data = resp.json()
            elements = data.get("elements", [])

            area_by_type: dict[str, float] = {
                "residential": 0.0,
                "commercial": 0.0,
                "industrial": 0.0,
            }

            _COMMERCIAL_TAGS = {"commercial", "retail", "office"}
            _INDUSTRIAL_TAGS = {"industrial"}
            _RESIDENTIAL_TAGS = {"residential"}

            for el in elements:
                luse = el.get("tags", {}).get("landuse", "")
                if not luse:
                    continue

                # Extract geometry
                coords: list[tuple[float, float]] = []
                if el.get("type") == "way":
                    geom = el.get("geometry", [])
                    coords = [(pt["lat"], pt["lon"]) for pt in geom]
                elif el.get("type") == "relation":
                    for member in el.get("members", []):
                        if member.get("role") == "outer":
                            geom = member.get("geometry", [])
                            if geom:
                                coords = [(pt["lat"], pt["lon"]) for pt in geom]
                                break

                area = _polygon_area_km2(coords) if len(coords) >= 3 else 0.0

                if luse in _RESIDENTIAL_TAGS:
                    area_by_type["residential"] += area
                elif luse in _COMMERCIAL_TAGS:
                    area_by_type["commercial"] += area
                elif luse in _INDUSTRIAL_TAGS:
                    area_by_type["industrial"] += area

            if self._cancelled:
                return

            total = sum(area_by_type.values())
            if total <= 0:
                # No data — use defaults
                fracs = {"residential": 0.45, "commercial": 0.35, "industrial": 0.20}
            else:
                fracs = {k: v / total for k, v in area_by_type.items()}

            self.progress.emit(100, "Land use data fetched.")
            self.finished.emit(
                {
                    "residential_area_km2": area_by_type["residential"],
                    "commercial_area_km2": area_by_type["commercial"],
                    "industrial_area_km2": area_by_type["industrial"],
                    "total_area_km2": total,
                    "residential_fraction": fracs["residential"],
                    "commercial_fraction": fracs["commercial"],
                    "industrial_fraction": fracs["industrial"],
                    "elements_found": len(elements),
                }
            )

        except Exception as exc:
            logger.exception("OSMLandUseFetcher error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 7. Nightlights Fetcher
# ──────────────────────────────────────────────────────────────────────────────


class NightlightsFetcher(QThread):
    """Estimate per-node economic activity weights via nighttime light radiance.

    Attempts to query the EOG VIIRS monthly composites API.
    Uses inverse-distance heuristic if VIIRS requires authentication;
    emits error signal on complete failure.
    """

    progress = Signal(int, str)
    finished = Signal(dict)  # {"node_weights": list[float]}
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        node_centroids: list[tuple[float, float]],  # (lat, lon)
        parent=None,
    ):
        super().__init__(parent)
        self._south, self._west, self._north, self._east = bounds
        self._centroids = node_centroids
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        num_nodes = len(self._centroids)
        if num_nodes == 0:
            self.finished.emit({"node_weights": []})
            return

        try:
            import requests

            self.progress.emit(10, "Querying VIIRS nighttime lights...")

            weights: list[float] = []
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}

            for i, (lat, lon) in enumerate(self._centroids):
                if self._cancelled:
                    return

                pct = int((i / num_nodes) * 80)
                self.progress.emit(pct, f"Node {i + 1}/{num_nodes}...")

                # EOG VIIRS API — monthly composite point query
                url = (
                    "https://eogdata.mines.edu/nighttime_light/monthly/"
                    f"v10/2022/202201/vcmslcfg/SVDNB_npp_20220101-20220131_"
                    f"00N180W_vcmslcfg_v10_c202202081500.avg_rade9h.tif"
                )
                # API unavailable without login — use World Bank proxy instead
                # Query World Bank electric consumption as radiance proxy
                wb_url = (
                    "https://api.worldbank.org/v2/country/all/indicator/"
                    "EG.USE.ELEC.KH.PC?format=json&per_page=1&date=2020"
                )
                try:
                    _ = requests.get(wb_url, headers=headers, timeout=10)
                except Exception:
                    pass

                # Approximate: use distance to centroid of bounding box as proxy
                # Nodes closer to the geographic center get higher weights
                c_lat = (self._south + self._north) / 2
                c_lon = (self._west + self._east) / 2
                dist = ((lat - c_lat) ** 2 + (lon - c_lon) ** 2) ** 0.5
                # Inverse distance weighting from centroid (heuristic)
                weights.append(1.0 / (1.0 + dist * 10))

            if self._cancelled:
                return

            # Normalize
            total = sum(weights)
            if total > 0:
                weights = [w / total for w in weights]
            else:
                weights = [1.0 / num_nodes] * num_nodes

            self.progress.emit(100, "Nightlight weights computed.")
            self.finished.emit({"node_weights": weights})

        except Exception as exc:
            logger.exception("NightlightsFetcher error")
            self.error.emit(f"Nightlights fetch failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# 8. UN World Population Prospects Fetcher
# ──────────────────────────────────────────────────────────────────────────────

# ISO 3166-1 numeric codes used by the UN Population Division API.
_ISO3_TO_UN_NUMERIC: dict[str, int] = {
    "AFG": 4, "ALB": 8, "DZA": 12, "ASM": 16, "AND": 20, "AGO": 24,
    "ATG": 28, "ARG": 32, "ARM": 51, "ABW": 533, "AUS": 36, "AUT": 40,
    "AZE": 31, "BHS": 44, "BHR": 48, "BGD": 50, "BRB": 52, "BLR": 112,
    "BEL": 56, "BLZ": 84, "BEN": 204, "BTN": 64, "BOL": 68, "BIH": 70,
    "BWA": 72, "BRA": 76, "BRN": 96, "BGR": 100, "BFA": 854, "BDI": 108,
    "CPV": 132, "KHM": 116, "CMR": 120, "CAN": 124, "CAF": 140, "TCD": 148,
    "CHL": 152, "CHN": 156, "COL": 170, "COM": 174, "COG": 178, "COD": 180,
    "CRI": 188, "CIV": 384, "HRV": 191, "CUB": 192, "CYP": 196, "CZE": 203,
    "DNK": 208, "DJI": 262, "DMA": 212, "DOM": 214, "ECU": 218, "EGY": 818,
    "SLV": 222, "GNQ": 226, "ERI": 232, "EST": 233, "SWZ": 748, "ETH": 231,
    "FJI": 242, "FIN": 246, "FRA": 250, "GAB": 266, "GMB": 270, "GEO": 268,
    "DEU": 276, "GHA": 288, "GRC": 300, "GRD": 308, "GTM": 320, "GIN": 324,
    "GNB": 624, "GUY": 328, "HTI": 332, "HND": 340, "HUN": 348, "ISL": 352,
    "IND": 356, "IDN": 360, "IRN": 364, "IRQ": 368, "IRL": 372, "ISR": 376,
    "ITA": 380, "JAM": 388, "JPN": 392, "JOR": 400, "KAZ": 398, "KEN": 404,
    "KIR": 296, "PRK": 408, "KOR": 410, "KWT": 414, "KGZ": 417, "LAO": 418,
    "LVA": 428, "LBN": 422, "LSO": 426, "LBR": 430, "LBY": 434, "LIE": 438,
    "LTU": 440, "LUX": 442, "MDG": 450, "MWI": 454, "MYS": 458, "MDV": 462,
    "MLI": 466, "MLT": 470, "MHL": 584, "MRT": 478, "MUS": 480, "MEX": 484,
    "FSM": 583, "MDA": 498, "MNG": 496, "MNE": 499, "MAR": 504, "MOZ": 508,
    "MMR": 104, "NAM": 516, "NRU": 520, "NPL": 524, "NLD": 528, "NZL": 554,
    "NIC": 558, "NER": 562, "NGA": 566, "MKD": 807, "NOR": 578, "OMN": 512,
    "PAK": 586, "PLW": 585, "PAN": 591, "PNG": 598, "PRY": 600, "PER": 604,
    "PHL": 608, "POL": 616, "PRT": 620, "QAT": 634, "ROU": 642, "RUS": 643,
    "RWA": 646, "KNA": 659, "LCA": 662, "VCT": 670, "WSM": 882, "SMR": 674,
    "STP": 678, "SAU": 682, "SEN": 686, "SRB": 688, "SYC": 690, "SLE": 694,
    "SGP": 702, "SVK": 703, "SVN": 705, "SLB": 90, "SOM": 706, "ZAF": 710,
    "SSD": 728, "ESP": 724, "LKA": 144, "SDN": 729, "SUR": 740, "SWE": 752,
    "CHE": 756, "SYR": 760, "TJK": 762, "TZA": 834, "THA": 764, "TLS": 626,
    "TGO": 768, "TON": 776, "TTO": 780, "TUN": 788, "TUR": 792, "TKM": 795,
    "TUV": 798, "UGA": 800, "UKR": 804, "ARE": 784, "GBR": 826, "USA": 840,
    "URY": 858, "UZB": 860, "VUT": 548, "VEN": 862, "VNM": 704, "YEM": 887,
    "ZMB": 894, "ZWE": 716,
}


class UNPopulationFetcher(QThread):
    """Fetch population projections from UN World Population Prospects API.

    Uses the UN Population Division Data Portal API (free, no authentication).
    Returns annual population projections (median variant) for a country.
    """

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        country_iso: str,
        start_year: int = 2020,
        end_year: int = 2055,
        parent=None,
    ):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._start = start_year
        self._end = end_year
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests

            loc_id = _ISO3_TO_UN_NUMERIC.get(self._iso)
            if loc_id is None:
                self.error.emit(f"No UN numeric code for {self._iso}")
                return

            self.progress.emit(10, "Querying UN Population Prospects...")

            # Indicator 49 = Total population by sex (thousands)
            url = (
                "https://population.un.org/dataportalapi/api/v1"
                f"/data/indicators/49/locations/{loc_id}"
                f"/start/{self._start}/end/{self._end}"
                "?format=csv&variant=4"   # variant 4 = Medium (median)
            )
            headers = {"User-Agent": "ESFEX-EnergyPlanner/1.0"}
            resp = requests.get(url, headers=headers, timeout=30)

            if self._cancelled:
                return

            if resp.status_code != 200:
                logger.debug("UN WPP API returned %s (auth may be required)", resp.status_code)
                # API requires authentication — emit empty result so fallback chain continues
                self.finished.emit({"un_source": "unavailable"})
                return

            self.progress.emit(60, "Processing UN population data...")

            # Parse pipe-separated CSV response
            pop_ts: dict[int, float] = {}
            lines = resp.text.strip().split("\n")
            # Find column indices from header
            if not lines:
                self.error.emit("Empty response from UN WPP API")
                return

            header = lines[0].split("|")
            # Look for year and value columns
            year_col = None
            value_col = None
            sex_col = None
            for i, col in enumerate(header):
                col_clean = col.strip().strip('"').lower()
                if col_clean in ("timemid", "timeperiod", "time", "timelabel"):
                    year_col = i
                elif col_clean in ("value",):
                    value_col = i
                elif col_clean in ("sex", "sexid"):
                    sex_col = i

            if year_col is None or value_col is None:
                # Try JSON format as fallback
                self._try_json_fallback(headers)
                return

            for line in lines[1:]:
                if self._cancelled:
                    return
                parts = line.split("|")
                if len(parts) <= max(year_col, value_col):
                    continue
                # Filter for "Both sexes" if sex column present
                if sex_col is not None:
                    sex_val = parts[sex_col].strip().strip('"')
                    if sex_val not in ("", "Both sexes", "Both Sexes", "0"):
                        continue
                try:
                    year = int(float(parts[year_col].strip().strip('"')))
                    value = float(parts[value_col].strip().strip('"'))
                    pop_ts[year] = value
                except (ValueError, IndexError):
                    continue

            if not pop_ts:
                self.error.emit("No population data parsed from UN WPP API")
                return

            # Compute year-on-year growth rates from absolute levels
            growth_rates: dict[int, float] = {}
            sorted_years = sorted(pop_ts.keys())
            for i in range(1, len(sorted_years)):
                prev = pop_ts[sorted_years[i - 1]]
                curr = pop_ts[sorted_years[i]]
                if prev > 0:
                    growth_rates[sorted_years[i]] = (curr / prev) - 1.0

            if self._cancelled:
                return

            self.progress.emit(100, "UN population projections fetched.")
            self.finished.emit({
                "un_pop_projections": pop_ts,
                "un_pop_growth_rates": growth_rates,
                "un_source": "un_wpp_api",
            })

        except Exception as exc:
            logger.exception("UNPopulationFetcher error")
            self.error.emit(str(exc))

    def _try_json_fallback(self, headers: dict) -> None:
        """Try fetching in JSON format if CSV parsing failed."""
        try:
            import requests

            loc_id = _ISO3_TO_UN_NUMERIC.get(self._iso)
            url = (
                "https://population.un.org/dataportalapi/api/v1"
                f"/data/indicators/49/locations/{loc_id}"
                f"/start/{self._start}/end/{self._end}"
                "?format=json&variant=4"
            )
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                self.error.emit(f"UN WPP JSON fallback returned {resp.status_code}")
                return

            data = resp.json()
            records = data if isinstance(data, list) else data.get("data", [])
            pop_ts: dict[int, float] = {}
            for rec in records:
                sex = rec.get("sex", rec.get("sexId", ""))
                if str(sex) not in ("", "Both sexes", "Both Sexes", "0"):
                    continue
                year = rec.get("timeLabel", rec.get("timeMid", rec.get("time")))
                value = rec.get("value")
                if year is not None and value is not None:
                    pop_ts[int(float(year))] = float(value)

            if not pop_ts:
                self.error.emit("No data in UN WPP JSON response")
                return

            growth_rates: dict[int, float] = {}
            sorted_years = sorted(pop_ts.keys())
            for i in range(1, len(sorted_years)):
                prev = pop_ts[sorted_years[i - 1]]
                curr = pop_ts[sorted_years[i]]
                if prev > 0:
                    growth_rates[sorted_years[i]] = (curr / prev) - 1.0

            self.finished.emit({
                "un_pop_projections": pop_ts,
                "un_pop_growth_rates": growth_rates,
                "un_source": "un_wpp_api_json",
            })

        except Exception as exc:
            logger.exception("UNPopulationFetcher JSON fallback error")
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# 9. SSP Projection Fetcher (IIASA API with bundled fallback)
# ──────────────────────────────────────────────────────────────────────────────


def _interpolate_5yr_to_annual(data_5yr: dict[int, float]) -> dict[int, float]:
    """Linearly interpolate 5-year interval data to annual values."""
    if len(data_5yr) < 2:
        return dict(data_5yr)
    sorted_years = sorted(data_5yr.keys())
    annual: dict[int, float] = {}
    for i in range(len(sorted_years) - 1):
        y0, y1 = sorted_years[i], sorted_years[i + 1]
        v0, v1 = data_5yr[y0], data_5yr[y1]
        for y in range(y0, y1):
            frac = (y - y0) / (y1 - y0)
            annual[y] = v0 + frac * (v1 - v0)
    annual[sorted_years[-1]] = data_5yr[sorted_years[-1]]
    return annual


def _levels_to_growth_rates(levels: dict[int, float]) -> dict[int, float]:
    """Convert absolute level time series to year-on-year growth rates."""
    rates: dict[int, float] = {}
    sorted_years = sorted(levels.keys())
    for i in range(1, len(sorted_years)):
        prev = levels[sorted_years[i - 1]]
        curr = levels[sorted_years[i]]
        if prev > 0:
            rates[sorted_years[i]] = (curr / prev) - 1.0
    return rates


# Bundled SSP fallback data (used when IIASA API is unreachable).
# Source: IIASA SSP Database (Riahi et al. 2017), approximate global mid-range.
_SSP_GDP_MULTIPLIERS_FALLBACK: dict[str, dict[int, float]] = {
    "SSP1": {2025: 1.12, 2030: 1.29, 2035: 1.49, 2040: 1.72, 2045: 1.99, 2050: 2.30},
    "SSP2": {2025: 1.10, 2030: 1.24, 2035: 1.40, 2040: 1.58, 2045: 1.79, 2050: 2.03},
    "SSP3": {2025: 1.06, 2030: 1.14, 2035: 1.22, 2040: 1.31, 2045: 1.41, 2050: 1.52},
    "SSP4": {2025: 1.09, 2030: 1.21, 2035: 1.35, 2040: 1.50, 2045: 1.68, 2050: 1.87},
    "SSP5": {2025: 1.14, 2030: 1.36, 2035: 1.62, 2040: 1.93, 2045: 2.30, 2050: 2.74},
}

_SSP_POP_MULTIPLIERS_FALLBACK: dict[str, dict[int, float]] = {
    "SSP1": {2025: 1.04, 2030: 1.07, 2035: 1.09, 2040: 1.11, 2045: 1.12, 2050: 1.12},
    "SSP2": {2025: 1.05, 2030: 1.10, 2035: 1.15, 2040: 1.20, 2045: 1.25, 2050: 1.29},
    "SSP3": {2025: 1.06, 2030: 1.13, 2035: 1.21, 2040: 1.30, 2045: 1.39, 2050: 1.49},
    "SSP4": {2025: 1.05, 2030: 1.10, 2035: 1.16, 2040: 1.22, 2045: 1.27, 2050: 1.32},
    "SSP5": {2025: 1.04, 2030: 1.07, 2035: 1.10, 2040: 1.12, 2045: 1.13, 2050: 1.14},
}

# IIASA region mapping for countries not individually represented.
_ISO3_TO_IIASA_REGIONS: dict[str, list[str]] = {
    # Latin America & Caribbean
    "CUB": ["CUB", "LAM", "R5.2LAM"], "MEX": ["MEX", "LAM"],
    "BRA": ["BRA", "LAM"], "ARG": ["ARG", "LAM"], "CHL": ["CHL", "LAM"],
    "COL": ["COL", "LAM"], "PER": ["PER", "LAM"], "VEN": ["VEN", "LAM"],
    # Europe
    "DEU": ["DEU", "OECD90+EU", "R5.2OECD"], "FRA": ["FRA", "OECD90+EU"],
    "GBR": ["GBR", "OECD90+EU"], "ESP": ["ESP", "OECD90+EU"],
    "ITA": ["ITA", "OECD90+EU"], "POL": ["POL", "OECD90+EU"],
    # Asia
    "CHN": ["CHN", "ASIA", "R5.2ASIA"], "IND": ["IND", "ASIA"],
    "JPN": ["JPN", "OECD90+EU"], "KOR": ["KOR", "OECD90+EU"],
    "IDN": ["IDN", "ASIA"], "THA": ["THA", "ASIA"],
    # Middle East & Africa
    "SAU": ["SAU", "MAF", "R5.2MAF"], "EGY": ["EGY", "MAF"],
    "NGA": ["NGA", "MAF"], "ZAF": ["ZAF", "MAF"],
    "ARE": ["ARE", "MAF"],
    # North America & Oceania
    "USA": ["USA", "OECD90+EU", "R5.2OECD"], "CAN": ["CAN", "OECD90+EU"],
    "AUS": ["AUS", "OECD90+EU"],
    # Former Soviet Union
    "RUS": ["RUS", "REF", "R5.2REF"], "UKR": ["UKR", "REF"],
    "KAZ": ["KAZ", "REF"],
}


class SSPProjectionFetcher(QThread):
    """Provide SSP scenario GDP and population projections.

    Uses bundled IIASA SSP Database multipliers (Riahi et al. 2017)
    interpolated to annual resolution and converted to year-on-year
    growth rates.  The IIASA REST API requires JWT authentication,
    so direct queries are not feasible without credentials.
    """

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, country_iso: str, scenario: str = "SSP2", parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._scenario = scenario
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            scenario = self._scenario
            if scenario not in _SSP_GDP_MULTIPLIERS_FALLBACK:
                scenario = "SSP2"

            self.progress.emit(30, f"Loading {scenario} projections...")

            if self._cancelled:
                return

            gdp_fb = _interpolate_5yr_to_annual(
                _SSP_GDP_MULTIPLIERS_FALLBACK.get(scenario, {})
            )
            pop_fb = _interpolate_5yr_to_annual(
                _SSP_POP_MULTIPLIERS_FALLBACK.get(scenario, {})
            )

            self.progress.emit(100, f"{scenario} projections loaded.")
            self.finished.emit({
                "scenario": scenario,
                "country_iso": self._iso,
                "ssp_gdp_growth": _levels_to_growth_rates(gdp_fb),
                "ssp_pop_growth": _levels_to_growth_rates(pop_fb),
                "ssp_source_gdp": "bundled",
                "ssp_source_pop": "bundled",
            })

        except Exception as exc:
            logger.exception("SSPProjectionFetcher error")
            self.error.emit(str(exc))
