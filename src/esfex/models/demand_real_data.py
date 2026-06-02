"""Fetchers for real hourly electricity demand data from public sources.

Each fetcher downloads hourly load data for one or more countries/regions,
returning a standardized dict: {iso3: {year: ndarray(8760)}} in MW.

Sources:
  - OPSD: ~30 European countries (2015-2020), CSV direct download
  - ENTSO-E: ~30 European countries (2015-2024), via entsoe-py (API key)
  - Brazil ONS: 4 subsystems (2000-2024), ONS S3 / Hugging Face parquet
  - Colombia XM: system total (REST API, no auth)
  - Japan TEPCO: Tokyo area (CSV direct download)
  - RTE France: national consumption (2012-2023), eco2mix ZIP/CSV
  - UK National Grid ESO: national demand (2017-2024), CSV download
  - Eskom South Africa: system hourly demand, data portal
  - Australia AEMO: Western Australia (CSV direct download)
  - USA GridStatus: aggregated ISO demand (gridstatus library)
  - Local files: user-provided Excel/CSV (e.g. Cuba UNE)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Standard output format: {iso3: {year: ndarray(8760)}} in MW
RealLoadData = dict[str, dict[int, np.ndarray]]


def _pad_or_trim(arr: np.ndarray, target: int = 8760) -> Optional[np.ndarray]:
    """Ensure array is exactly 8760. Return None if too short."""
    if len(arr) >= target:
        return arr[:target].astype(np.float64)
    if len(arr) >= target - 48:  # allow up to 2 days missing
        return np.pad(arr, (0, target - len(arr)),
                      constant_values=arr[-1]).astype(np.float64)
    return None


# ── OPSD (Europe, ~30 countries) ─────────────────────────────────────────────

_OPSD_URL = (
    "https://data.open-power-system-data.org/time_series/"
    "2020-10-06/time_series_60min_singleindex.csv"
)

# OPSD column suffix → ISO3 mapping
_OPSD_COUNTRY_MAP = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "CH": "CHE", "CZ": "CZE",
    "DE": "DEU", "DK": "DNK", "EE": "EST", "ES": "ESP", "FI": "FIN",
    "FR": "FRA", "GB": "GBR", "GR": "GRC", "HR": "HRV", "HU": "HUN",
    "IE": "IRL", "IT": "ITA", "LT": "LTU", "LU": "LUX", "LV": "LVA",
    "ME": "MNE", "MK": "MKD", "NL": "NLD", "NO": "NOR", "PL": "POL",
    "PT": "PRT", "RO": "ROU", "RS": "SRB", "SE": "SWE", "SI": "SVN",
    "SK": "SVK",
}


def fetch_opsd(cache_dir: Path) -> RealLoadData:
    """Download OPSD European hourly load data.

    Returns {ISO3: {year: ndarray(8760)}} for ~30 countries.
    """
    import pandas as pd
    import requests

    cache_file = cache_dir / "opsd_60min.csv"

    if not cache_file.exists():
        logger.info("Downloading OPSD time series (~100 MB)...")
        cache_dir.mkdir(parents=True, exist_ok=True)
        resp = requests.get(_OPSD_URL, timeout=300)
        resp.raise_for_status()
        cache_file.write_bytes(resp.content)
        logger.info("OPSD data cached at %s", cache_file)

    logger.info("Loading OPSD data...")
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True, low_memory=False)

    result: RealLoadData = {}

    for iso2, iso3 in _OPSD_COUNTRY_MAP.items():
        col = f"{iso2}_load_actual_entsoe_transparency"
        if col not in df.columns:
            continue

        series = df[col].dropna()
        if series.empty:
            continue

        years = series.index.year.unique()
        for yr in years:
            yr_data = series[series.index.year == yr].values
            arr = _pad_or_trim(yr_data)
            if arr is not None and arr.mean() > 0:
                result.setdefault(iso3, {})[int(yr)] = arr

    n_countries = len(result)
    n_years = sum(len(v) for v in result.values())
    logger.info("OPSD: loaded %d countries, %d country-years", n_countries, n_years)
    return result


# ── Brazil ONS (S3 / Hugging Face fallback) ─────────────────────────────────

_BRAZIL_S3_URL = (
    "https://ons-dl-prod-opendata.s3.amazonaws.com/"
    "dataset/carga_energia_di/CARGA_ENERGIA_{year}.csv"
)

_BRAZIL_HF_URL = (
    "https://huggingface.co/datasets/SamuelM0422/"
    "Hourly-Electricity-Demand-Brazil-Dataset/resolve/"
    "refs/convert/parquet/CurvaCarga-{year}.parquet"
)

_BRAZIL_API_URL = "https://dados.ons.org.br/dataset/carga-energia"


def _try_brazil_s3(yr: int, cache_dir: Path) -> Optional[np.ndarray]:
    """Try fetching Brazil ONS data from S3 bucket (CSV)."""
    import pandas as pd
    import requests
    import io

    url = _BRAZIL_S3_URL.format(year=yr)
    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        return None

    df = pd.read_csv(io.StringIO(resp.text), sep=";")
    # S3 CSV typically has date/time columns and load values
    # Look for columns with 'carga' or 'mwmed' in the name
    load_col = None
    for col in df.columns:
        cl = col.lower()
        if "cargaenergia" in cl or "mwmed" in cl or "val_carga" in cl:
            load_col = col
            break
    if load_col is None:
        # Try the last numeric column
        for col in reversed(list(df.columns)):
            try:
                pd.to_numeric(df[col], errors="raise")
                load_col = col
                break
            except (ValueError, TypeError):
                continue
    if load_col is None:
        return None

    # Try to parse timestamps and sum subsystems
    date_col = None
    for col in df.columns:
        cl = col.lower()
        if "instante" in cl or "data" in cl or "date" in cl:
            date_col = col
            break
    if date_col is not None:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        total = df.groupby(date_col)[load_col].sum().sort_index()
        vals = pd.to_numeric(total, errors="coerce").dropna().values
    else:
        vals = pd.to_numeric(df[load_col], errors="coerce").dropna().values

    return _pad_or_trim(vals.astype(np.float64))


def _try_brazil_hf(yr: int, cache_dir: Path) -> Optional[np.ndarray]:
    """Try fetching Brazil ONS data from Hugging Face parquet."""
    import pandas as pd
    import requests

    url = _BRAZIL_HF_URL.format(year=yr)
    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        return None

    tmp = cache_dir / f"brazil_ons_{yr}.parquet"
    tmp.write_bytes(resp.content)

    df = pd.read_parquet(tmp)
    tmp.unlink(missing_ok=True)

    if "din_instante" in df.columns and "val_cargaenergiahomwmed" in df.columns:
        df["din_instante"] = pd.to_datetime(df["din_instante"])
        total = df.groupby("din_instante")["val_cargaenergiahomwmed"].sum()
        total = total.sort_index()
        arr = _pad_or_trim(total.values)
        if arr is not None and arr.mean() > 0:
            return arr
    return None


def fetch_brazil_ons(cache_dir: Path, years: range = range(2010, 2024)) -> RealLoadData:
    """Download Brazil ONS hourly demand.

    Tries ONS S3 bucket first, then Hugging Face parquet as fallback.
    Returns {\"BRA\": {year: ndarray(8760)}} with total system demand.
    """
    result: RealLoadData = {"BRA": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"brazil_ons_{yr}.npy"
        if cache_file.exists():
            result["BRA"][yr] = np.load(cache_file)
            continue

        arr = None
        # Try S3 bucket first
        try:
            arr = _try_brazil_s3(yr, cache_dir)
        except Exception as exc:
            logger.debug("Brazil ONS S3 %d failed: %s", yr, exc)

        # Fallback to Hugging Face
        if arr is None:
            try:
                arr = _try_brazil_hf(yr, cache_dir)
            except Exception as exc:
                logger.debug("Brazil ONS HF %d failed: %s", yr, exc)

        if arr is not None and arr.mean() > 0:
            result["BRA"][yr] = arr
            np.save(cache_file, arr)

        time.sleep(0.5)

    n = len(result.get("BRA", {}))
    logger.info("Brazil ONS: loaded %d years", n)
    return result


# ── Colombia XM (REST API) ───────────────────────────────────────────────────

_XM_URL = "https://servapibi.xm.com.co/hourly"


def fetch_colombia_xm(
    cache_dir: Path,
    years: range = range(2018, 2024),
) -> RealLoadData:
    """Download Colombia XM hourly demand via REST API.

    Returns {\"COL\": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"COL": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"colombia_xm_{yr}.npy"
        if cache_file.exists():
            result["COL"][yr] = np.load(cache_file)
            continue

        # XM API: max 30 days per request → 12 monthly requests per year
        hourly_vals = []
        ok = True
        for month in range(1, 13):
            import calendar
            last_day = calendar.monthrange(yr, month)[1]
            start = f"{yr}-{month:02d}-01"
            end = f"{yr}-{month:02d}-{last_day:02d}"

            payload = {
                "MetricId": "DemaSIN",
                "StartDate": start,
                "EndDate": end,
                "Entity": "Sistema",
                "Filter": [],
            }
            try:
                resp = requests.post(
                    _XM_URL, json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )
                if resp.status_code != 200:
                    ok = False
                    break
                data = resp.json()
                # Parse XM response format
                items = data.get("Items", [])
                for item in items:
                    values = item.get("HourlyEntities", [])
                    for v in values:
                        hourly_vals.append(float(v.get("Value", 0)))
            except Exception as exc:
                logger.debug("Colombia XM %d-%02d failed: %s", yr, month, exc)
                ok = False
                break
            time.sleep(0.3)

        if ok and hourly_vals:
            arr = _pad_or_trim(np.array(hourly_vals, dtype=np.float64))
            if arr is not None and arr.mean() > 0:
                result["COL"][yr] = arr
                np.save(cache_file, arr)

    n = len(result.get("COL", {}))
    logger.info("Colombia XM: loaded %d years", n)
    return result


# ── Japan TEPCO (CSV download) ───────────────────────────────────────────────

_TEPCO_URL = "https://www.tepco.co.jp/forecast/html/images/juyo-{year}.csv"


def fetch_tepco_japan(
    cache_dir: Path,
    years: range = range(2016, 2024),
) -> RealLoadData:
    """Download TEPCO (Tokyo) hourly demand.

    Returns {\"JPN\": {year: ndarray(8760)}}.
    Note: This is Tokyo area only, not all of Japan.
    """
    import requests

    result: RealLoadData = {"JPN": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"tepco_{yr}.npy"
        if cache_file.exists():
            result["JPN"][yr] = np.load(cache_file)
            continue

        url = _TEPCO_URL.format(year=yr)
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                continue

            # TEPCO CSV: Shift-JIS encoding, demand in column 2 (×10 MW)
            import io
            text = resp.content.decode("shift_jis", errors="replace")
            lines = text.strip().split("\n")

            vals = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 3:
                    try:
                        demand = float(parts[2].strip())
                        vals.append(demand * 10.0)  # ×10 MW → MW
                    except ValueError:
                        continue

            if vals:
                arr = _pad_or_trim(np.array(vals, dtype=np.float64))
                if arr is not None and arr.mean() > 0:
                    result["JPN"][yr] = arr
                    np.save(cache_file, arr)
        except Exception as exc:
            logger.debug("TEPCO %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("JPN", {}))
    logger.info("TEPCO Japan: loaded %d years", n)
    return result


# ── Australia AEMO (WA, CSV download) ────────────────────────────────────────

_AEMO_URL = (
    "https://data.wa.aemo.com.au/public/public-data/datafiles/"
    "load-summary/load-summary-{year}.csv"
)


def fetch_aemo_australia(
    cache_dir: Path,
    years: range = range(2018, 2024),
) -> RealLoadData:
    """Download AEMO Western Australia hourly demand.

    Returns {\"AUS\": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"AUS": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"aemo_{yr}.npy"
        if cache_file.exists():
            result["AUS"][yr] = np.load(cache_file)
            continue

        url = _AEMO_URL.format(year=yr)
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code != 200:
                continue

            import pandas as pd
            import io
            df = pd.read_csv(io.StringIO(resp.text))

            # AEMO load-summary has columns like 'Trading Interval', 'Total Sent Out Generation'
            # or 'Operational Demand'. Find the demand column.
            demand_col = None
            for col in df.columns:
                cl = col.lower()
                if "demand" in cl or "sent out" in cl or "load" in cl:
                    demand_col = col
                    break
            if demand_col is None and len(df.columns) >= 2:
                # Try second numeric column
                for col in df.columns[1:]:
                    if df[col].dtype in ("float64", "int64", "float32"):
                        demand_col = col
                        break

            if demand_col is None:
                logger.debug("AEMO %d: no demand column found in %s", yr, list(df.columns))
                continue

            vals = df[demand_col].dropna().values.astype(np.float64)
            # AEMO may be half-hourly (17520 points) → aggregate to hourly
            if len(vals) > 10000:
                n_hours = len(vals) // 2
                vals = vals[: n_hours * 2].reshape(n_hours, 2).mean(axis=1)

            arr = _pad_or_trim(vals)
            if arr is not None and arr.mean() > 0:
                result["AUS"][yr] = arr
                np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("AEMO %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("AUS", {}))
    logger.info("AEMO Australia: loaded %d years", n)
    return result


# ── USA PJM/EIA (via gridstatus if available) ───────────────────────────────

def fetch_usa_gridstatus(
    cache_dir: Path,
    years: range = range(2019, 2024),
) -> RealLoadData:
    """Download US ISO hourly demand via gridstatus library.

    Tries PJM, NYISO, CAISO, ERCOT, MISO, ISONE.
    Returns {\"USA\": {year: ndarray(8760)}} with sum of available ISOs.
    Falls back gracefully if gridstatus is not installed.
    """
    try:
        import gridstatus
    except ImportError:
        logger.info("gridstatus not installed — skipping US ISO data")
        return {}

    result: RealLoadData = {"USA": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Try EIA (aggregated US demand)
    for yr in years:
        cache_file = cache_dir / f"usa_eia_{yr}.npy"
        if cache_file.exists():
            result["USA"][yr] = np.load(cache_file)
            continue

        try:
            eia = gridstatus.EIA()
            df = eia.get_load(
                start=f"{yr}-01-01",
                end=f"{yr}-12-31",
            )
            if df is not None and len(df) > 0:
                # Aggregate to hourly if needed
                load_col = None
                for col in df.columns:
                    if "load" in col.lower() or "demand" in col.lower():
                        load_col = col
                        break
                if load_col:
                    vals = df[load_col].dropna().values.astype(np.float64)
                    arr = _pad_or_trim(vals)
                    if arr is not None and arr.mean() > 0:
                        result["USA"][yr] = arr
                        np.save(cache_file, arr)
        except Exception as exc:
            logger.debug("GridStatus EIA %d failed: %s", yr, exc)

    n = len(result.get("USA", {}))
    logger.info("USA GridStatus: loaded %d years", n)
    return result


# ── ENTSO-E (Europe, via entsoe-py) ──────────────────────────────────────────

# ENTSO-E bidding zone codes → ISO3 mapping
_ENTSOE_ZONE_MAP = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "CH": "CHE", "CZ": "CZE",
    "DE_LU": "DEU", "DK_1": "DNK", "DK_2": "DNK", "EE": "EST", "ES": "ESP",
    "FI": "FIN", "FR": "FRA", "GB": "GBR", "GR": "GRC", "HR": "HRV",
    "HU": "HUN", "IE": "IRL", "IT": "ITA", "LT": "LTU", "LU": "LUX",
    "LV": "LVA", "ME": "MNE", "MK": "MKD", "NL": "NLD", "NO_1": "NOR",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "RS": "SRB", "SE_1": "SWE",
    "SI": "SVN", "SK": "SVK",
}


def fetch_entsoe_europe(
    cache_dir: Path,
    years: range = range(2015, 2025),
) -> RealLoadData:
    """Download ENTSO-E actual total load via entsoe-py.

    Requires the ENTSOE_API_KEY environment variable (free token from
    https://transparency.entsoe.eu/).
    Returns {ISO3: {year: ndarray(8760)}} for ~30 European countries.
    """
    import os

    api_key = os.environ.get("ENTSOE_API_KEY", "")
    if not api_key:
        logger.info(
            "ENTSOE_API_KEY not set — skipping ENTSO-E data. "
            "Get a free token at https://transparency.entsoe.eu/"
        )
        return {}

    try:
        from entsoe import EntsoePandasClient
    except ImportError:
        logger.info("entsoe-py not installed — skipping ENTSO-E data")
        return {}

    import pandas as pd

    client = EntsoePandasClient(api_key=api_key)
    result: RealLoadData = {}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for zone, iso3 in _ENTSOE_ZONE_MAP.items():
        for yr in years:
            cache_file = cache_dir / f"entsoe_{zone}_{yr}.npy"
            if cache_file.exists():
                arr = np.load(cache_file)
                result.setdefault(iso3, {})[yr] = arr
                continue

            try:
                start = pd.Timestamp(f"{yr}-01-01", tz="Europe/Brussels")
                end = pd.Timestamp(f"{yr + 1}-01-01", tz="Europe/Brussels")
                ts = client.query_load(zone, start=start, end=end)

                # query_load may return a DataFrame or Series
                if hasattr(ts, "columns"):
                    # Take first column (Actual Load)
                    ts = ts.iloc[:, 0]

                ts = ts.dropna()
                if ts.empty:
                    continue

                # Resample to hourly mean if sub-hourly (e.g. 15-min)
                if hasattr(ts.index, "freq") and ts.index.freq is not None:
                    freq_minutes = ts.index.freq.delta.total_seconds() / 60  # type: ignore[union-attr]
                    if freq_minutes < 60:
                        ts = ts.resample("1h").mean()
                elif len(ts) > 8800:
                    # No explicit freq but more points than hourly → resample
                    ts = ts.resample("1h").mean()

                vals = ts.values.astype(np.float64)
                arr = _pad_or_trim(vals)
                if arr is not None and arr.mean() > 0:
                    # For zones that map to the same ISO3 (e.g. DK_1, DK_2),
                    # sum the values if we already have data for that year
                    if iso3 in result and yr in result[iso3]:
                        result[iso3][yr] = result[iso3][yr] + arr
                    else:
                        result.setdefault(iso3, {})[yr] = arr
                    np.save(cache_file, arr)
            except Exception as exc:
                logger.debug("ENTSO-E %s %d failed: %s", zone, yr, exc)
            time.sleep(0.5)

    n_countries = len(result)
    n_years = sum(len(v) for v in result.values())
    logger.info("ENTSO-E: loaded %d countries, %d country-years", n_countries, n_years)
    return result


# ── RTE France (eco2mix) ────────────────────────────────────────────────────

_RTE_URL = (
    "https://eco2mix.rte-france.com/download/eco2mix/"
    "eCO2mix_RTE_Annuel-Definitif_{year}.zip"
)


def fetch_rte_france(
    cache_dir: Path,
    years: range = range(2012, 2024),
) -> RealLoadData:
    """Download RTE eco2mix French national consumption data.

    Each year is a ZIP containing a CSV with half-hourly or hourly consumption.
    Returns {\"FRA\": {year: ndarray(8760)}}.
    """
    import requests
    import zipfile
    import io

    result: RealLoadData = {"FRA": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"rte_france_{yr}.npy"
        if cache_file.exists():
            result["FRA"][yr] = np.load(cache_file)
            continue

        url = _RTE_URL.format(year=yr)
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                logger.debug("RTE France %d: HTTP %d", yr, resp.status_code)
                continue

            import pandas as pd

            # Extract CSV from ZIP
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            csv_names = [n for n in zf.namelist() if n.endswith(".csv") or n.endswith(".CSV")]
            if not csv_names:
                logger.debug("RTE France %d: no CSV in ZIP", yr)
                continue

            csv_data = zf.read(csv_names[0])
            # RTE files use tab or semicolon separator, and may have encoding issues
            for sep in ["\t", ";"]:
                try:
                    df = pd.read_csv(
                        io.BytesIO(csv_data),
                        sep=sep,
                        encoding="utf-8",
                        low_memory=False,
                    )
                    if len(df.columns) > 2:
                        break
                except Exception:
                    try:
                        df = pd.read_csv(
                            io.BytesIO(csv_data),
                            sep=sep,
                            encoding="latin-1",
                            low_memory=False,
                        )
                        if len(df.columns) > 2:
                            break
                    except Exception:
                        continue

            # Find consumption column
            conso_col = None
            for col in df.columns:
                cl = col.lower()
                if "consommation" in cl or "conso" in cl:
                    conso_col = col
                    break
            if conso_col is None:
                logger.debug("RTE France %d: no consumption column in %s", yr, list(df.columns)[:10])
                continue

            vals = pd.to_numeric(df[conso_col], errors="coerce").dropna().values.astype(np.float64)

            # If half-hourly (>10000 points), aggregate to hourly
            if len(vals) > 10000:
                n_hours = len(vals) // 2
                vals = vals[: n_hours * 2].reshape(n_hours, 2).mean(axis=1)

            arr = _pad_or_trim(vals)
            if arr is not None and arr.mean() > 0:
                result["FRA"][yr] = arr
                np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("RTE France %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("FRA", {}))
    logger.info("RTE France: loaded %d years", n)
    return result


# ── UK National Grid ESO ────────────────────────────────────────────────────

_UK_ESO_URLS = [
    "https://data.nationalgrideso.com/system/demand/demand_data_{year}.csv",
    "https://data.nationalgrideso.com/backend/dataset/demand/resource/demand_data_{year}.csv",
]


def fetch_uk_nationalgrid(
    cache_dir: Path,
    years: range = range(2017, 2025),
) -> RealLoadData:
    """Download UK National Grid ESO demand data.

    Tries primary and alternative URL patterns. Data is in half-hourly
    settlement periods (48/day), aggregated to hourly.
    Returns {\"GBR\": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"GBR": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"uk_eso_{yr}.npy"
        if cache_file.exists():
            result["GBR"][yr] = np.load(cache_file)
            continue

        resp = None
        for url_tmpl in _UK_ESO_URLS:
            url = url_tmpl.format(year=yr)
            try:
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    break
                resp = None
            except Exception:
                resp = None

        if resp is None:
            logger.debug("UK ESO %d: all URLs failed", yr)
            continue

        try:
            import pandas as pd
            import io

            df = pd.read_csv(io.StringIO(resp.text), low_memory=False)

            # Look for "ND" (National Demand) column
            nd_col = None
            for col in df.columns:
                if col.strip().upper() == "ND":
                    nd_col = col
                    break
            if nd_col is None:
                # Try broader search
                for col in df.columns:
                    cl = col.lower()
                    if "national" in cl and "demand" in cl:
                        nd_col = col
                        break
            if nd_col is None:
                logger.debug("UK ESO %d: no ND column in %s", yr, list(df.columns)[:10])
                continue

            vals = pd.to_numeric(df[nd_col], errors="coerce").dropna().values.astype(np.float64)

            # Data is half-hourly (48 settlement periods/day) → aggregate to hourly
            if len(vals) > 10000:
                n_hours = len(vals) // 2
                vals = vals[: n_hours * 2].reshape(n_hours, 2).mean(axis=1)

            arr = _pad_or_trim(vals)
            if arr is not None and arr.mean() > 0:
                result["GBR"][yr] = arr
                np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("UK ESO %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("GBR", {}))
    logger.info("UK National Grid ESO: loaded %d years", n)
    return result


# ── Eskom South Africa ──────────────────────────────────────────────────────

_ESKOM_PORTAL_URL = (
    "https://www.eskom.co.za/dataportal/wp-content/uploads/"
    "2023/08/System_hourly_actual_and_forecasted_demand.csv"
)


def fetch_eskom_southafrica(
    cache_dir: Path,
) -> RealLoadData:
    """Download Eskom (South Africa) system hourly demand.

    Tries the Eskom data portal CSV download. The portal publishes a single
    CSV file with multi-year hourly data.
    Returns {\"ZAF\": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"ZAF": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_csv = cache_dir / "eskom_demand.csv"

    # Try downloading if not cached
    if not cache_csv.exists():
        urls_to_try = [
            _ESKOM_PORTAL_URL,
            "https://www.eskom.co.za/dataportal/wp-content/uploads/"
            "System_hourly_actual_and_forecasted_demand.csv",
        ]
        downloaded = False
        for url in urls_to_try:
            try:
                resp = requests.get(url, timeout=120)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    cache_csv.write_bytes(resp.content)
                    downloaded = True
                    break
            except Exception as exc:
                logger.debug("Eskom URL failed (%s): %s", url, exc)
            time.sleep(0.5)

        if not downloaded:
            logger.info(
                "Eskom data portal not reachable — skipping South Africa data. "
                "You can manually download from "
                "https://www.eskom.co.za/dataportal/demand-side/"
                "system-hourly-actual-and-forecasted-demand/"
            )
            return result

    try:
        import pandas as pd

        df = pd.read_csv(cache_csv, low_memory=False)

        # Find timestamp column
        date_col = None
        for col in df.columns:
            cl = col.lower()
            if "date" in cl or "time" in cl or "timestamp" in cl:
                date_col = col
                break
        if date_col is None:
            date_col = df.columns[0]

        # Find actual demand column
        demand_col = None
        for col in df.columns:
            cl = col.lower()
            if "actual" in cl and "demand" in cl:
                demand_col = col
                break
        if demand_col is None:
            for col in df.columns:
                cl = col.lower()
                if "demand" in cl or "load" in cl:
                    demand_col = col
                    break
        if demand_col is None:
            logger.debug("Eskom: no demand column found in %s", list(df.columns)[:10])
            return result

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        df[demand_col] = pd.to_numeric(df[demand_col], errors="coerce")
        df = df.dropna(subset=[demand_col])

        for yr in df[date_col].dt.year.unique():
            yr = int(yr)
            cache_file = cache_dir / f"eskom_{yr}.npy"
            if cache_file.exists():
                result["ZAF"][yr] = np.load(cache_file)
                continue

            mask = df[date_col].dt.year == yr
            vals = df.loc[mask, demand_col].values.astype(np.float64)

            # If sub-hourly, resample
            if len(vals) > 10000:
                n_hours = len(vals) // 2
                vals = vals[: n_hours * 2].reshape(n_hours, 2).mean(axis=1)

            arr = _pad_or_trim(vals)
            if arr is not None and arr.mean() > 0:
                result["ZAF"][yr] = arr
                np.save(cache_file, arr)

    except Exception as exc:
        logger.debug("Eskom parsing failed: %s", exc)

    n = len(result.get("ZAF", {}))
    logger.info("Eskom South Africa: loaded %d years", n)
    return result


# ── Turkey EPIAS (REST API) ─────────────────────────────────────────────────

def fetch_turkey_epias(
    cache_dir: Path,
    years: range = range(2018, 2025),
) -> RealLoadData:
    """Download Turkey hourly demand from EPIAS transparency platform.

    Returns {"TUR": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"TUR": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"turkey_epias_{yr}.npy"
        if cache_file.exists():
            result["TUR"][yr] = np.load(cache_file)
            continue

        hourly_vals = []
        ok = True
        for month in range(1, 13):
            import calendar
            last_day = calendar.monthrange(yr, month)[1]
            start = f"{yr}-{month:02d}-01T00:00:00+03:00"
            end = f"{yr}-{month:02d}-{last_day:02d}T23:00:00+03:00"

            url = "https://seffaflik.epias.com.tr/transparency/service/consumption/real-time-consumption"
            try:
                resp = requests.post(url, json={
                    "startDate": start, "endDate": end,
                }, headers={"Content-Type": "application/json"}, timeout=60)
                if resp.status_code != 200:
                    ok = False
                    break
                data = resp.json()
                body = data.get("body", {})
                items = body.get("hourlyConsumptions", [])
                for item in items:
                    hourly_vals.append(float(item.get("consumption", 0)))
            except Exception as exc:
                logger.debug("Turkey EPIAS %d-%02d failed: %s", yr, month, exc)
                ok = False
                break
            time.sleep(0.5)

        if ok and hourly_vals:
            arr = _pad_or_trim(np.array(hourly_vals, dtype=np.float64))
            if arr is not None and arr.mean() > 0:
                result["TUR"][yr] = arr
                np.save(cache_file, arr)

    n = len(result.get("TUR", {}))
    logger.info("Turkey EPIAS: loaded %d years", n)
    return result


# ── Chile CEN (REST API) ───────────────────────────────────────────────────

def fetch_chile_cen(
    cache_dir: Path,
    years: range = range(2018, 2025),
) -> RealLoadData:
    """Download Chile hourly demand from CEN transparency API.

    Returns {"CHL": {year: ndarray(8760)}}.
    """
    import requests
    import pandas as pd

    result: RealLoadData = {"CHL": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"chile_cen_{yr}.npy"
        if cache_file.exists():
            result["CHL"][yr] = np.load(cache_file)
            continue

        # CEN API: demanda real (system total)
        url = (
            f"https://sipub.coordinador.cl/api/v1/recursos/demanda_real"
            f"?fecha_inicio={yr}-01-01&fecha_fin={yr}-12-31&formato=json"
        )
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                logger.debug("Chile CEN %d: HTTP %d", yr, resp.status_code)
                continue
            data = resp.json()
            # Parse response — varies by API version
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = data.get("datos", data.get("data", data.get("apidata", [])))
            else:
                continue

            if not records:
                continue

            df = pd.DataFrame(records)
            # Find demand column
            demand_col = None
            for col in df.columns:
                if "demanda" in col.lower() or "demand" in col.lower() or "mw" in col.lower():
                    demand_col = col
                    break
            if demand_col is None:
                demand_col = df.columns[-1]

            vals = pd.to_numeric(df[demand_col], errors="coerce").dropna().values
            arr = _pad_or_trim(np.array(vals, dtype=np.float64))
            if arr is not None and arr.mean() > 0:
                result["CHL"][yr] = arr
                np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("Chile CEN %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("CHL", {}))
    logger.info("Chile CEN: loaded %d years", n)
    return result


# ── Argentina CAMMESA (Excel download) ─────────────────────────────────────

def fetch_argentina_cammesa(
    cache_dir: Path,
    years: range = range(2018, 2025),
) -> RealLoadData:
    """Download Argentina hourly demand from CAMMESA.

    Returns {"ARG": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"ARG": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"argentina_cammesa_{yr}.npy"
        if cache_file.exists():
            result["ARG"][yr] = np.load(cache_file)
            continue

        # CAMMESA real-time API for historical demand
        hourly_vals = []
        ok = True
        for month in range(1, 13):
            import calendar
            last_day = calendar.monthrange(yr, month)[1]

            url = (
                f"https://api.cammesa.com/demanda-svc/demanda/"
                f"ObtieneDemandaYTemperatura?"
                f"fechaDesde={yr}-{month:02d}-01"
                f"&fechaHasta={yr}-{month:02d}-{last_day:02d}"
            )
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code != 200:
                    ok = False
                    break
                data = resp.json()
                for item in data:
                    dem = item.get("dem", item.get("demanda", 0))
                    if dem:
                        hourly_vals.append(float(dem))
            except Exception as exc:
                logger.debug("Argentina CAMMESA %d-%02d failed: %s", yr, month, exc)
                ok = False
                break
            time.sleep(0.3)

        if ok and len(hourly_vals) >= 8000:
            arr = _pad_or_trim(np.array(hourly_vals, dtype=np.float64))
            if arr is not None and arr.mean() > 0:
                result["ARG"][yr] = arr
                np.save(cache_file, arr)

    n = len(result.get("ARG", {}))
    logger.info("Argentina CAMMESA: loaded %d years", n)
    return result


# ── Mexico CENACE (CSV download) ───────────────────────────────────────────

def fetch_mexico_cenace(
    cache_dir: Path,
    years: range = range(2018, 2025),
) -> RealLoadData:
    """Download Mexico hourly demand from CENACE.

    Returns {"MEX": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"MEX": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"mexico_cenace_{yr}.npy"
        if cache_file.exists():
            result["MEX"][yr] = np.load(cache_file)
            continue

        # CENACE SIM: bulk download by year
        url = (
            f"https://www.cenace.gob.mx/DocsMEM/"
            f"DemandaReal/DemandaReal_{yr}.csv"
        )
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                # Alt URL pattern
                url = (
                    f"https://www.cenace.gob.mx/SIM/VISTA/PAGINASPUBLICAS/"
                    f"Informacion/DemandaReal_{yr}.csv"
                )
                resp = requests.get(url, timeout=120)
                if resp.status_code != 200:
                    continue

            import pandas as pd
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text), encoding="latin-1")

            # Sum all regions for national total
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                vals = df[numeric_cols].sum(axis=1).values
                arr = _pad_or_trim(np.array(vals, dtype=np.float64))
                if arr is not None and arr.mean() > 0:
                    result["MEX"][yr] = arr
                    np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("Mexico CENACE %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("MEX", {}))
    logger.info("Mexico CENACE: loaded %d years", n)
    return result


# ── South Korea KPX (web scraping) ─────────────────────────────────────────

def fetch_korea_kpx(
    cache_dir: Path,
    years: range = range(2019, 2025),
) -> RealLoadData:
    """Download South Korea hourly demand from KPX EPSIS.

    Returns {"KOR": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"KOR": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"korea_kpx_{yr}.npy"
        if cache_file.exists():
            result["KOR"][yr] = np.load(cache_file)
            continue

        # KPX provides hourly demand via their statistics page
        url = (
            f"https://epsis.kpx.or.kr/epsisnew/selectEkmaUpsBftGrid.do"
            f"?menuId=040202&locale=eng"
        )
        try:
            # POST request with date range
            import calendar
            hourly_vals = []
            ok = True
            for month in range(1, 13):
                last_day = calendar.monthrange(yr, month)[1]
                resp = requests.post(url, data={
                    "startDt": f"{yr}{month:02d}01",
                    "endDt": f"{yr}{month:02d}{last_day:02d}",
                }, timeout=30)
                if resp.status_code != 200:
                    ok = False
                    break
                # Parse HTML table or JSON
                try:
                    data = resp.json()
                    for row in data.get("list", []):
                        for h in range(24):
                            key = f"h{h+1:02d}"
                            val = row.get(key, 0)
                            if val:
                                hourly_vals.append(float(val))
                except Exception:
                    pass
                time.sleep(0.5)

            if ok and len(hourly_vals) >= 8000:
                arr = _pad_or_trim(np.array(hourly_vals, dtype=np.float64))
                if arr is not None and arr.mean() > 0:
                    result["KOR"][yr] = arr
                    np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("Korea KPX %d failed: %s", yr, exc)

    n = len(result.get("KOR", {}))
    logger.info("Korea KPX: loaded %d years", n)
    return result


# ── Taiwan Taipower (open data CSV) ────────────────────────────────────────

def fetch_taiwan_taipower(
    cache_dir: Path,
    years: range = range(2019, 2025),
) -> RealLoadData:
    """Download Taiwan hourly demand from Taipower open data.

    Returns {"TWN": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"TWN": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"taiwan_taipower_{yr}.npy"
        if cache_file.exists():
            result["TWN"][yr] = np.load(cache_file)
            continue

        # Taipower open data portal
        url = f"https://data.taipower.com.tw/opendata/apply/file/d006001/{yr}0101-{yr}1231.csv"
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                # Alt: government open data
                url = f"https://data.gov.tw/dataset/19995/resource/{yr}"
                resp = requests.get(url, timeout=60)
                if resp.status_code != 200:
                    continue

            import pandas as pd
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text), encoding="utf-8-sig")

            # Find demand column (varies: 尖峰負載, 系統負載, etc.)
            demand_col = None
            for col in df.columns:
                if any(k in str(col).lower() for k in ["load", "demand", "負載", "用電"]):
                    demand_col = col
                    break
            if demand_col is None and len(df.columns) > 1:
                demand_col = df.columns[1]

            if demand_col:
                vals = pd.to_numeric(df[demand_col], errors="coerce").dropna().values
                arr = _pad_or_trim(np.array(vals, dtype=np.float64))
                if arr is not None and arr.mean() > 0:
                    result["TWN"][yr] = arr
                    np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("Taiwan Taipower %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("TWN", {}))
    logger.info("Taiwan Taipower: loaded %d years", n)
    return result


# ── Peru COES (web download) ──────────────────────────────────────────────

def fetch_peru_coes(
    cache_dir: Path,
    years: range = range(2019, 2025),
) -> RealLoadData:
    """Download Peru hourly demand from COES SINAC.

    Returns {"PER": {year: ndarray(8760)}}.
    """
    import requests

    result: RealLoadData = {"PER": {}}
    cache_dir.mkdir(parents=True, exist_ok=True)

    for yr in years:
        cache_file = cache_dir / f"peru_coes_{yr}.npy"
        if cache_file.exists():
            result["PER"][yr] = np.load(cache_file)
            continue

        # COES API endpoint for demand data
        url = (
            f"https://www.coes.org.pe/Portal/portalinformacion/demanda?"
            f"anioInicio={yr}&anioFin={yr}"
        )
        try:
            resp = requests.get(url, timeout=120,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue

            import pandas as pd
            from io import StringIO
            # Try parsing as CSV or JSON
            try:
                data = resp.json()
                if isinstance(data, list):
                    df = pd.DataFrame(data)
                elif isinstance(data, dict) and "data" in data:
                    df = pd.DataFrame(data["data"])
                else:
                    continue
            except Exception:
                df = pd.read_csv(StringIO(resp.text))

            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                vals = df[numeric_cols[0]].values
                arr = _pad_or_trim(np.array(vals, dtype=np.float64))
                if arr is not None and arr.mean() > 0:
                    result["PER"][yr] = arr
                    np.save(cache_file, arr)

        except Exception as exc:
            logger.debug("Peru COES %d failed: %s", yr, exc)
        time.sleep(0.5)

    n = len(result.get("PER", {}))
    logger.info("Peru COES: loaded %d years", n)
    return result


# ── PLEXOS World 2015 (Harvard Dataverse — synthetic profiles) ─────────────

_PLEXOS_URL = (
    "https://dataverse.harvard.edu/api/access/datafile/"
    ":persistentId?persistentId=doi:10.7910/DVN/CBYXBY/"
)


def fetch_plexos_world(
    cache_dir: Path,
    countries: Optional[list[str]] = None,
) -> RealLoadData:
    """Load synthetic hourly demand profiles from PLEXOS World 2015.

    The PLEXOS World dataset provides synthetic hourly load shapes
    for every country. These are modeled profiles (not measured),
    useful for countries with no public data (Africa, Middle East, etc.).

    Returns {iso3: {2015: ndarray(8760)}} — shapes to be scaled.
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    result: RealLoadData = {}

    # PLEXOS World loads are in a ZIP file on Harvard Dataverse
    zip_path = cache_dir / "plexos_world_load.zip"

    if not zip_path.exists():
        logger.info("Downloading PLEXOS World 2015 dataset...")
        # The dataset DOI resolves to files; try the main data file
        url = (
            "https://dataverse.harvard.edu/api/access/datafile/"
            ":persistentId?persistentId=doi:10.7910/DVN/CBYXBY"
        )
        try:
            resp = requests.get(url, timeout=300, allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                zip_path.write_bytes(resp.content)
                logger.info("PLEXOS World downloaded (%d MB)",
                            len(resp.content) // 1e6)
            else:
                logger.warning("PLEXOS download failed: HTTP %d", resp.status_code)
                return result
        except Exception as exc:
            logger.warning("PLEXOS download failed: %s", exc)
            return result

    # Extract and parse load profiles
    try:
        import zipfile
        import pandas as pd

        with zipfile.ZipFile(zip_path) as zf:
            # Find load/demand CSV files
            load_files = [f for f in zf.namelist()
                          if "load" in f.lower() or "demand" in f.lower()]
            if not load_files:
                load_files = [f for f in zf.namelist() if f.endswith(".csv")]

            for fname in load_files:
                try:
                    with zf.open(fname) as f:
                        df = pd.read_csv(f)
                    # Parse country code from filename or content
                    # PLEXOS uses region names; map to ISO3
                    # This is best-effort parsing
                    for col in df.columns:
                        if len(col) == 3 and col.isupper():
                            if countries and col not in countries:
                                continue
                            vals = pd.to_numeric(df[col], errors="coerce").dropna().values
                            arr = _pad_or_trim(vals)
                            if arr is not None and arr.mean() > 0:
                                result[col] = {2015: arr}
                except Exception:
                    continue

    except Exception as exc:
        logger.warning("PLEXOS parsing failed: %s", exc)

    logger.info("PLEXOS World: loaded %d countries", len(result))
    return result


# ── Local files (user-provided) ──────────────────────────────────────────────

def load_local_demand(
    path: str | Path,
    iso3: str,
    start_year: int,
) -> RealLoadData:
    """Load hourly demand from a local Excel or CSV file.

    The file should contain a single column of hourly MW values,
    with consecutive years of 8760 hours each.
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Local demand file not found: %s", path)
        return {}

    if path.suffix in (".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_excel(path)
        values = df.iloc[:, 0].values.astype(np.float64)
    elif path.suffix == ".csv":
        values = np.loadtxt(path, dtype=np.float64)
    else:
        logger.warning("Unsupported file format: %s", path.suffix)
        return {}

    result: RealLoadData = {iso3: {}}
    n_years = len(values) // 8760
    for y in range(n_years):
        yr = start_year + y
        yr_vals = values[y * 8760: (y + 1) * 8760]
        arr = _pad_or_trim(yr_vals)
        if arr is not None and arr.mean() > 0:
            result[iso3][yr] = arr

    n = len(result.get(iso3, {}))
    logger.info("Local %s: loaded %d years from %s", iso3, n, path)
    return result


# ── Unified fetcher ──────────────────────────────────────────────────────────

def fetch_all_real_load(
    cache_dir: Optional[Path] = None,
    include_opsd: bool = True,
    include_entsoe: bool = True,
    include_brazil: bool = True,
    include_colombia: bool = True,
    include_japan: bool = True,
    include_rte: bool = True,
    include_uk: bool = True,
    include_eskom: bool = True,
    include_australia: bool = True,
    include_usa: bool = True,
    include_turkey: bool = True,
    include_chile: bool = True,
    include_argentina: bool = True,
    include_mexico: bool = True,
    include_korea: bool = True,
    include_taiwan: bool = True,
    include_peru: bool = True,
    local_files: Optional[dict[str, dict]] = None,
    progress_cb=None,
) -> tuple[RealLoadData, dict[str, dict]]:
    """Fetch real hourly load data from all available sources.

    Parameters
    ----------
    local_files : dict, optional
        {iso3: {"path": str, "start_year": int, "lat": float, "lon": float,
                "population": {year: float}, "gdp_per_capita": {year: float},
                "urbanization_pct": float, "electricity_access_pct": float}}

    Returns
    -------
    (load_data, metadata)
        load_data: {iso3: {year: ndarray(8760)}} — MW values
        metadata: {iso3: {"lat": float, "lon": float, ...}} — per-country info
    """
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "esfex" / "real_load_data"
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_data: RealLoadData = {}
    metadata: dict[str, dict] = {}

    def _emit(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    # Country coordinates for metadata (capital cities)
    _COORDS = {
        "AUT": (48.2, 16.4), "BEL": (50.8, 4.4), "BGR": (42.7, 23.3),
        "CHE": (46.9, 7.4), "CZE": (50.1, 14.4), "DEU": (52.5, 13.4),
        "DNK": (55.7, 12.6), "EST": (59.4, 24.7), "ESP": (40.4, -3.7),
        "FIN": (60.2, 24.9), "FRA": (48.9, 2.3), "GBR": (51.5, -0.1),
        "GRC": (37.9, 23.7), "HRV": (45.8, 16.0), "HUN": (47.5, 19.1),
        "IRL": (53.3, -6.3), "ITA": (41.9, 12.5), "LTU": (54.7, 25.3),
        "LUX": (49.6, 6.1), "LVA": (56.9, 24.1), "MNE": (42.4, 19.3),
        "MKD": (42.0, 21.4), "NLD": (52.4, 4.9), "NOR": (59.9, 10.7),
        "POL": (52.2, 21.0), "PRT": (38.7, -9.1), "ROU": (44.4, 26.1),
        "SRB": (44.8, 20.5), "SWE": (59.3, 18.1), "SVN": (46.1, 14.5),
        "SVK": (48.1, 17.1), "BRA": (-15.8, -47.9), "COL": (4.7, -74.1),
        "JPN": (35.7, 139.7), "CUB": (23.1, -82.4),
        "AUS": (-33.9, 151.2), "USA": (38.9, -77.0),
        "ZAF": (-33.9, 18.4),
    }

    step = 0
    total_steps = sum([include_opsd, include_entsoe, include_brazil, include_colombia,
                       include_japan, include_rte, include_uk, include_eskom,
                       include_australia, include_usa, include_turkey, include_chile,
                       include_argentina, include_mexico, include_korea,
                       include_taiwan, include_peru, bool(local_files)])

    # OPSD (Europe)
    if include_opsd:
        _emit(int(step / total_steps * 80), "Fetching OPSD (Europe)...")
        try:
            opsd = fetch_opsd(cache_dir / "opsd")
            all_data.update(opsd)
        except Exception as exc:
            logger.warning("OPSD fetch failed: %s", exc)
        step += 1

    # ENTSO-E (Europe, via entsoe-py)
    if include_entsoe:
        _emit(int(step / total_steps * 80), "Fetching ENTSO-E (Europe)...")
        try:
            entsoe = fetch_entsoe_europe(cache_dir / "entsoe")
            # Merge: ENTSO-E data supplements OPSD (prefer longer series)
            for iso3, years_data in entsoe.items():
                for yr, arr in years_data.items():
                    if iso3 not in all_data or yr not in all_data.get(iso3, {}):
                        all_data.setdefault(iso3, {})[yr] = arr
        except Exception as exc:
            logger.warning("ENTSO-E fetch failed: %s", exc)
        step += 1

    # Brazil ONS
    if include_brazil:
        _emit(int(step / total_steps * 80), "Fetching Brazil ONS...")
        try:
            brazil = fetch_brazil_ons(cache_dir / "brazil")
            all_data.update(brazil)
        except Exception as exc:
            logger.warning("Brazil ONS fetch failed: %s", exc)
        step += 1

    # Colombia XM
    if include_colombia:
        _emit(int(step / total_steps * 80), "Fetching Colombia XM...")
        try:
            colombia = fetch_colombia_xm(cache_dir / "colombia")
            all_data.update(colombia)
        except Exception as exc:
            logger.warning("Colombia XM fetch failed: %s", exc)
        step += 1

    # Japan TEPCO
    if include_japan:
        _emit(int(step / total_steps * 80), "Fetching TEPCO Japan...")
        try:
            japan = fetch_tepco_japan(cache_dir / "japan")
            all_data.update(japan)
        except Exception as exc:
            logger.warning("TEPCO Japan fetch failed: %s", exc)
        step += 1

    # RTE France (eco2mix)
    if include_rte:
        _emit(int(step / total_steps * 80), "Fetching RTE France...")
        try:
            rte = fetch_rte_france(cache_dir / "rte")
            # RTE supplements OPSD/ENTSO-E for France — fill missing years
            for yr, arr in rte.get("FRA", {}).items():
                if "FRA" not in all_data or yr not in all_data.get("FRA", {}):
                    all_data.setdefault("FRA", {})[yr] = arr
        except Exception as exc:
            logger.warning("RTE France fetch failed: %s", exc)
        step += 1

    # UK National Grid ESO
    if include_uk:
        _emit(int(step / total_steps * 80), "Fetching UK National Grid ESO...")
        try:
            uk = fetch_uk_nationalgrid(cache_dir / "uk_eso")
            # UK ESO supplements OPSD/ENTSO-E for GBR — fill missing years
            for yr, arr in uk.get("GBR", {}).items():
                if "GBR" not in all_data or yr not in all_data.get("GBR", {}):
                    all_data.setdefault("GBR", {})[yr] = arr
        except Exception as exc:
            logger.warning("UK National Grid ESO fetch failed: %s", exc)
        step += 1

    # Eskom South Africa
    if include_eskom:
        _emit(int(step / total_steps * 80), "Fetching Eskom South Africa...")
        try:
            eskom = fetch_eskom_southafrica(cache_dir / "eskom")
            all_data.update(eskom)
        except Exception as exc:
            logger.warning("Eskom South Africa fetch failed: %s", exc)
        step += 1

    # Australia AEMO
    if include_australia:
        _emit(int(step / total_steps * 80), "Fetching AEMO Australia...")
        try:
            aus = fetch_aemo_australia(cache_dir / "aemo")
            all_data.update(aus)
        except Exception as exc:
            logger.warning("AEMO Australia fetch failed: %s", exc)
        step += 1

    # USA (via gridstatus, optional)
    if include_usa:
        _emit(int(step / total_steps * 80), "Fetching USA GridStatus...")
        try:
            usa = fetch_usa_gridstatus(cache_dir / "usa")
            all_data.update(usa)
        except Exception as exc:
            logger.warning("USA GridStatus fetch failed: %s", exc)
        step += 1

    # Turkey (EPIAS)
    if include_turkey:
        _emit(int(step / total_steps * 80), "Fetching Turkey EPIAS...")
        try:
            tur = fetch_turkey_epias(cache_dir / "turkey")
            all_data.update(tur)
        except Exception as exc:
            logger.warning("Turkey EPIAS fetch failed: %s", exc)
        step += 1

    # Chile (CEN)
    if include_chile:
        _emit(int(step / total_steps * 80), "Fetching Chile CEN...")
        try:
            chl = fetch_chile_cen(cache_dir / "chile")
            all_data.update(chl)
        except Exception as exc:
            logger.warning("Chile CEN fetch failed: %s", exc)
        step += 1

    # Argentina (CAMMESA)
    if include_argentina:
        _emit(int(step / total_steps * 80), "Fetching Argentina CAMMESA...")
        try:
            arg = fetch_argentina_cammesa(cache_dir / "argentina")
            all_data.update(arg)
        except Exception as exc:
            logger.warning("Argentina CAMMESA fetch failed: %s", exc)
        step += 1

    # Mexico (CENACE)
    if include_mexico:
        _emit(int(step / total_steps * 80), "Fetching Mexico CENACE...")
        try:
            mex = fetch_mexico_cenace(cache_dir / "mexico")
            all_data.update(mex)
        except Exception as exc:
            logger.warning("Mexico CENACE fetch failed: %s", exc)
        step += 1

    # South Korea (KPX)
    if include_korea:
        _emit(int(step / total_steps * 80), "Fetching South Korea KPX...")
        try:
            kor = fetch_korea_kpx(cache_dir / "korea")
            all_data.update(kor)
        except Exception as exc:
            logger.warning("Korea KPX fetch failed: %s", exc)
        step += 1

    # Taiwan (Taipower)
    if include_taiwan:
        _emit(int(step / total_steps * 80), "Fetching Taiwan Taipower...")
        try:
            twn = fetch_taiwan_taipower(cache_dir / "taiwan")
            all_data.update(twn)
        except Exception as exc:
            logger.warning("Taiwan Taipower fetch failed: %s", exc)
        step += 1

    # Peru (COES)
    if include_peru:
        _emit(int(step / total_steps * 80), "Fetching Peru COES...")
        try:
            per = fetch_peru_coes(cache_dir / "peru")
            all_data.update(per)
        except Exception as exc:
            logger.warning("Peru COES fetch failed: %s", exc)
        step += 1

    # Local files
    if local_files:
        _emit(int(step / total_steps * 80), "Loading local demand files...")
        for iso3, info in local_files.items():
            try:
                local = load_local_demand(info["path"], iso3, info["start_year"])
                all_data.update(local)
                # Store metadata from local_files
                metadata[iso3] = {k: v for k, v in info.items() if k != "path"}
            except Exception as exc:
                logger.warning("Local file %s failed: %s", iso3, exc)

    # Build metadata for all countries
    for iso3 in all_data:
        if iso3 not in metadata:
            lat, lon = _COORDS.get(iso3, (0.0, 0.0))
            metadata[iso3] = {"lat": lat, "lon": lon}

    n_countries = len(all_data)
    n_years = sum(len(v) for v in all_data.values())
    _emit(100, f"Real load data: {n_countries} countries, {n_years} country-years")

    return all_data, metadata
