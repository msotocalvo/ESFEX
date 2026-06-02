"""Demand dataset builder — download, consolidate, and standardize.

Orchestrates all data fetchers from ``demand_real_data.py``, downloads
ERA5 temperature for every country-year, and outputs a standardized
Parquet dataset ready for model training.

Output structure::

    {cache_dir}/
        {ISO3}/
            {ISO3}_{year}.parquet     # timestamp, demand_mw, temperature_c
        metadata.json                 # catalog of all available data

Usage::

    esfex build-demand-dataset [--sources all] [--cache-dir PATH]
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from esfex.paths import DEMAND_DATASET_DIR as _DEFAULT_CACHE

logger = logging.getLogger(__name__)

# Capital city coordinates for ERA5 downloads
_COUNTRY_COORDS: dict[str, tuple[float, float]] = {
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
    "JPN": (35.7, 139.7), "CUB": (23.1, -82.4), "AUS": (-33.9, 151.2),
    "USA": (38.9, -77.0), "ZAF": (-33.9, 18.4), "MEX": (19.4, -99.1),
    "KOR": (37.6, 127.0), "IND": (28.6, 77.2), "ARG": (-34.6, -58.4),
    "CHL": (-33.4, -70.7), "PER": (-12.0, -77.0), "THA": (13.8, 100.5),
    "IDN": (-6.2, 106.8), "EGY": (30.0, 31.2), "NGA": (9.1, 7.5),
    "KEN": (-1.3, 36.8), "MAR": (34.0, -6.8), "TUR": (39.9, 32.9),
}


def _fetch_era5_year(
    lat: float, lon: float, year: int, cache_dir: Path,
) -> Optional[np.ndarray]:
    """Fetch ERA5 hourly temperature for one location and year."""
    cache_file = cache_dir / f"era5_{year}.npy"
    if cache_file.exists():
        return np.load(cache_file)

    import requests
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={year}-01-01&end_date={year}-12-31"
        "&hourly=temperature_2m&timezone=UTC"
    )
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        temps = resp.json().get("hourly", {}).get("temperature_2m", [])
        if len(temps) >= 8760:
            arr = np.array(temps[:8760], dtype=np.float64)
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(cache_file, arr)
            return arr
    except Exception as exc:
        logger.debug("ERA5 %d (%.1f,%.1f) failed: %s", year, lat, lon, exc)
    return None


def build_dataset(
    cache_dir: Optional[Path] = None,
    sources: Optional[list[str]] = None,
    local_files: Optional[dict[str, dict]] = None,
    era5_workers: int = 4,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Any]:
    """Build the comprehensive demand dataset.

    Parameters
    ----------
    cache_dir : Path
        Output directory for parquet files.
    sources : list[str], optional
        Which fetchers to use. None or ["all"] = use all available.
        Individual: "opsd", "entsoe", "brazil", "colombia", "japan",
                    "australia", "usa", "rte", "uk", "eskom"
    local_files : dict
        User-provided demand files (same format as demand_real_data).
    era5_workers : int
        Number of parallel threads for ERA5 downloads.
    progress_cb : callable
        Progress callback (pct, msg).

    Returns
    -------
    dict: manifest with dataset statistics.
    """
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)

    def emit(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    use_all = sources is None or "all" in sources
    use = set(sources or [])

    # ── Step 1: Fetch all demand data ────────────────────────────────────
    emit(5, "Fetching demand data from all sources...")

    from esfex.models.demand_real_data import fetch_all_real_load

    all_data, all_meta = fetch_all_real_load(
        cache_dir=cache_dir / "_raw",
        include_opsd=use_all or "opsd" in use,
        include_brazil=use_all or "brazil" in use,
        include_colombia=use_all or "colombia" in use,
        include_japan=use_all or "japan" in use,
        include_australia=use_all or "australia" in use,
        include_usa=use_all or "usa" in use,
        local_files=local_files,
        progress_cb=lambda p, m: emit(int(5 + p * 0.35), m),
    )

    n_countries = len(all_data)
    n_cy = sum(len(v) for v in all_data.values())
    emit(40, f"Demand data: {n_countries} countries, {n_cy} country-years")

    # ── Step 2: Download ERA5 temperature for all country-years ──────────
    emit(42, "Downloading ERA5 temperature for all country-years...")

    era5_cache = cache_dir / "_era5"
    era5_cache.mkdir(parents=True, exist_ok=True)

    # Build list of (iso3, year, lat, lon) to download
    era5_tasks = []
    for iso3, years_data in all_data.items():
        lat, lon = _COUNTRY_COORDS.get(iso3, (0.0, 0.0))
        meta = all_meta.get(iso3, {})
        lat = meta.get("lat", lat)
        lon = meta.get("lon", lon)
        if lat == 0.0 and lon == 0.0:
            continue
        country_era5_dir = era5_cache / iso3
        country_era5_dir.mkdir(parents=True, exist_ok=True)
        for yr in years_data:
            era5_tasks.append((iso3, yr, lat, lon, country_era5_dir))

    total_era5 = len(era5_tasks)
    era5_data: dict[str, dict[int, np.ndarray]] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=era5_workers) as pool:
        futures = {}
        for iso3, yr, lat, lon, d in era5_tasks:
            f = pool.submit(_fetch_era5_year, lat, lon, yr, d)
            futures[f] = (iso3, yr)
            time.sleep(0.2)  # stagger submissions

        for future in as_completed(futures):
            iso3, yr = futures[future]
            try:
                result = future.result()
                if result is not None:
                    era5_data.setdefault(iso3, {})[yr] = result
            except Exception:
                pass
            completed += 1
            if completed % 20 == 0:
                pct = 42 + int(38 * completed / max(total_era5, 1))
                emit(pct, f"ERA5: {completed}/{total_era5}")

    emit(80, f"ERA5: {sum(len(v) for v in era5_data.values())} country-years downloaded")

    # ── Step 3: Write standardized Parquet files ─────────────────────────
    emit(82, "Writing Parquet files...")

    import pandas as pd

    manifest_entries = []

    for iso3, years_data in all_data.items():
        iso_dir = cache_dir / iso3
        iso_dir.mkdir(parents=True, exist_ok=True)

        for yr, demand_arr in years_data.items():
            if len(demand_arr) < 8760:
                continue

            # Build DataFrame
            timestamps = pd.date_range(
                f"{yr}-01-01", periods=8760, freq="h", tz="UTC",
            )
            df = pd.DataFrame({
                "timestamp": timestamps,
                "demand_mw": demand_arr[:8760].astype(np.float64),
            })

            # Add temperature if available
            temp = era5_data.get(iso3, {}).get(yr)
            if temp is not None and len(temp) >= 8760:
                df["temperature_c"] = temp[:8760].astype(np.float64)
            else:
                df["temperature_c"] = np.nan

            # Write parquet
            pq_path = iso_dir / f"{iso3}_{yr}.parquet"
            df.to_parquet(pq_path, index=False)

            manifest_entries.append({
                "iso3": iso3,
                "year": yr,
                "file": str(pq_path.relative_to(cache_dir)),
                "peak_mw": float(demand_arr[:8760].max()),
                "mean_mw": float(demand_arr[:8760].mean()),
                "annual_gwh": float(demand_arr[:8760].sum() / 1000.0),
                "has_temperature": temp is not None,
            })

    # ── Step 4: Write manifest ───────────────────────────────────────────
    manifest = {
        "n_countries": len(set(e["iso3"] for e in manifest_entries)),
        "n_country_years": len(manifest_entries),
        "total_hours": len(manifest_entries) * 8760,
        "countries": sorted(set(e["iso3"] for e in manifest_entries)),
        "entries": manifest_entries,
    }

    manifest_path = cache_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    emit(95, f"Dataset: {manifest['n_countries']} countries, "
         f"{manifest['n_country_years']} country-years")

    # Country-level metadata
    for iso3 in all_data:
        meta = all_meta.get(iso3, {})
        lat, lon = _COUNTRY_COORDS.get(iso3, (0.0, 0.0))
        meta.setdefault("lat", lat)
        meta.setdefault("lon", lon)
        meta["years"] = sorted(all_data[iso3].keys())
        meta_path = cache_dir / iso3 / "metadata.json"
        try:
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    emit(100, f"Dataset built at {cache_dir}")
    return manifest


def load_manifest(cache_dir: Optional[Path] = None) -> dict[str, Any]:
    """Load dataset manifest from cache."""
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        return {"n_countries": 0, "n_country_years": 0, "entries": []}
    with open(manifest_path) as f:
        return json.load(f)


def load_country_year(
    iso3: str, year: int, cache_dir: Optional[Path] = None,
    zone: Optional[str] = None,
) -> Optional[tuple[np.ndarray, Optional[np.ndarray]]]:
    """Load demand + temperature for a specific country-year (or zone-year).

    Returns (demand_mw, temperature_c) or None if not available.
    temperature_c may be None if ERA5 was not downloaded.

    If ``zone`` is given, reads ``{ISO3}_{zone}_{year}.parquet`` instead.
    """
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE
    pq_name = (f"{iso3}_{zone}_{year}.parquet" if zone
               else f"{iso3}_{year}.parquet")
    pq_path = cache_dir / iso3 / pq_name
    if not pq_path.exists():
        return None

    import pandas as pd
    df = pd.read_parquet(pq_path)
    demand = df["demand_mw"].values.astype(np.float64)
    temp = None
    if "temperature_c" in df.columns and not df["temperature_c"].isna().all():
        temp = df["temperature_c"].values.astype(np.float64)
    return demand, temp


def iter_manifest_entries(manifest: dict) -> list[dict]:
    """Yield normalized (iso3, zone, year, lat, lon) entries from a manifest.

    For each country:
    - If ``zones`` is present, emit one entry per (zone, year) at the
      zone centroid.
    - Also emit national entries for years that are NOT covered by ANY
      zone (so historical years without zonal breakdown are not lost).

    This is the single traversal point used by both training and
    inference so zonal awareness stays consistent.
    """
    out = []
    for iso3, info in manifest.items():
        if not isinstance(info, dict):
            continue
        zones = info.get("zones") or {}
        zonal_years: set[int] = set()
        if zones:
            for zone_id, zinfo in zones.items():
                if not isinstance(zinfo, dict):
                    continue
                lat = float(zinfo.get("lat", 0.0))
                lon = float(zinfo.get("lon", 0.0))
                for yr in zinfo.get("years", []):
                    yr_i = int(yr)
                    zonal_years.add(yr_i)
                    out.append({
                        "iso3": iso3, "zone": zone_id, "year": yr_i,
                        "lat": lat, "lon": lon,
                    })
        # National entries for years without any zonal coverage.
        # Skipping years that are already represented zonally avoids
        # double-counting: national = sum of zones, so emitting both would
        # expose the model to the same physical demand twice.
        lat = float(info.get("lat", 0.0))
        lon = float(info.get("lon", 0.0))
        for yr in info.get("years", []):
            yr_i = int(yr)
            if yr_i in zonal_years:
                continue
            out.append({
                "iso3": iso3, "zone": None, "year": yr_i,
                "lat": lat, "lon": lon,
            })
    return out


def write_zonal_parquet(
    iso3: str, zone: str, year: int,
    demand_mw: np.ndarray,
    temperature_c: Optional[np.ndarray] = None,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Write a zonal parquet file following the {ISO3}_{ZONE}_{YEAR}.parquet
    convention. Same schema as the national format — timestamp, demand_mw,
    temperature_c. 8760 hours, UTC.

    Returns the path of the written file.
    """
    import pandas as pd
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE
    iso_dir = cache_dir / iso3
    iso_dir.mkdir(parents=True, exist_ok=True)
    timestamps = pd.date_range(
        f"{year}-01-01", periods=8760, freq="h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": timestamps,
        "demand_mw": demand_mw[:8760].astype(np.float64),
    })
    if temperature_c is not None and len(temperature_c) >= 8760:
        df["temperature_c"] = temperature_c[:8760].astype(np.float64)
    else:
        df["temperature_c"] = np.nan
    pq_path = iso_dir / f"{iso3}_{zone}_{year}.parquet"
    df.to_parquet(pq_path, index=False)
    return pq_path
