"""Download CMIP6 temperatures for all countries.

Handles Open-Meteo rate limits by retrying with exponential backoff.
Run as standalone script — designed to be left running unattended.

Usage:
    python src/esfex/models/download_cmip6.py
"""

import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from esfex.models.country_metadata import COUNTRY_COORDS
from esfex.models.demand_projection import _daily_to_hourly_temperature
from esfex.paths import CMIP6_DIR
MODEL = "CMCC_CM2_VHR4"
START_YEAR = 2025
END_YEAR = 2050
DELAY = 5  # seconds between requests


def _cache_path(lat, lon):
    return CMIP6_DIR / f"cmip6_{MODEL}_{lat:.2f}_{lon:.2f}_{START_YEAR}_{END_YEAR}.npz"


def download_one(iso3, lat, lon):
    """Download CMIP6 for one country. Returns True on success."""
    cache = _cache_path(lat, lon)
    if cache.exists():
        return True

    url = (
        f"https://climate-api.open-meteo.com/v1/climate?"
        f"latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={START_YEAR}-01-01&end_date={END_YEAR}-12-31"
        f"&models={MODEL}"
        f"&daily=temperature_2m_max,temperature_2m_min"
    )

    try:
        resp = requests.get(url, timeout=120)
        if resp.status_code == 429:
            return None  # rate limited
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"  {iso3}: ERROR {e}")
        return False

    daily = payload.get("daily", {})
    times = daily.get("time", [])
    tmax_raw = daily.get("temperature_2m_max", [])
    tmin_raw = daily.get("temperature_2m_min", [])

    if not times:
        print(f"  {iso3}: no data")
        return False

    # Group by year
    year_days = {}
    for i, t in enumerate(times):
        yr = int(t[:4])
        year_days.setdefault(yr, []).append(i)

    result = {}
    for yr in range(START_YEAR, END_YEAR + 1):
        indices = year_days.get(yr, [])
        n = len(indices)
        if n < 360:
            continue
        tmn = np.array([tmin_raw[i] if tmin_raw[i] is not None else 15.0
                        for i in indices])
        tmx = np.array([tmax_raw[i] if tmax_raw[i] is not None else 25.0
                        for i in indices])
        hourly = _daily_to_hourly_temperature(tmn, tmx, n)
        if len(hourly) < 8760:
            hourly = np.pad(hourly, (0, 8760 - len(hourly)), mode="edge")
        hourly = hourly[:8760]
        mask = np.isnan(hourly)
        if mask.any():
            hourly[mask] = np.nanmean(hourly)
        result[yr] = hourly

    if len(result) < 25:
        print(f"  {iso3}: only {len(result)} years")
        return False

    np.savez_compressed(cache, **{str(k): v for k, v in result.items()})
    return True


def main():
    CMIP6_DIR.mkdir(parents=True, exist_ok=True)

    # Find missing countries
    missing = []
    for iso3, (lat, lon) in COUNTRY_COORDS.items():
        if not _cache_path(lat, lon).exists():
            missing.append((iso3, lat, lon))

    if not missing:
        print("All countries already downloaded!")
        return

    print(f"Need to download: {len(missing)} countries")
    print(f"Delay: {DELAY}s between requests\n")

    done = 0
    failed = []

    for iso3, lat, lon in missing:
        result = download_one(iso3, lat, lon)

        if result is None:
            # Rate limited — wait and retry
            wait = 300  # 5 minutes
            print(f"\n  Rate limited at {iso3}. Waiting {wait}s...")
            time.sleep(wait)
            # Retry
            result = download_one(iso3, lat, lon)
            if result is None:
                # Still limited, wait longer
                wait = 900  # 15 minutes
                print(f"  Still limited. Waiting {wait}s...")
                time.sleep(wait)
                result = download_one(iso3, lat, lon)

        done += 1
        status = "OK" if result else "FAILED"
        if not result:
            failed.append(iso3)

        if done % 10 == 0 or done == len(missing):
            print(f"  [{done}/{len(missing)}] {iso3}: {status}")

        time.sleep(DELAY)

    print(f"\nDone. Success: {done - len(failed)}, Failed: {len(failed)}")
    if failed:
        print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
