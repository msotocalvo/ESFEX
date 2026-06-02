"""Extract pixel-level features at any (lat, lon, year) point.

Unified interface for Static (GDP, pop, urbanization) and Time-varying
(temperature) features. Used both at training time (sample at country
capital or zone centroid) and at inference time (sample at each grid pixel).

Key design decision: features are computed WITH THE SAME FORMULA at training
and inference. This eliminates covariate shift by construction.

Dropped 2026-04-18 after multicollinearity audit:
  - elec_access (HREA lightscore): 90% NaN, always 1.0 where present, VIF=0.
  - utci / utci_hdd / utci_cdd: Pearson r > 0.91 with temperature/hdd/cdd,
    VIF up to 2170; marginal info beyond air temperature is too small.

Outputs from build_features_for_point():
    PixelFeatures(
      log_gdp_per_cap: float,   # ln(GDP_total_pixel / pop_pixel) [$/person]
      log_pop_density: float,   # ln(pop_density_pixel) [hab/km²]
      urbanization: float,      # GHSL SMOD class (10-30), continuous
      latitude, longitude: float,
      temperature: np.ndarray,  # 8760 hourly values [°C]
      hdd: np.ndarray,          # 8760 values
      cdd: np.ndarray,          # 8760 values
      missing: list[str],
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from esfex.paths import (
    GHSL_SMOD_DIR as GHSL_DIR,
    DEMAND_ERA5_DIR as ERA5_CACHE,
    DEMAND_DATASET_DIR,
    POP_DENSITY_HIST_DIR,
    GRIDDED_GDP_025_DIR as GDP_DIR,
)

HDD_BASE = 18.0
CDD_BASE = 24.0


@lru_cache(maxsize=512)
def _open_raster(path: str):
    import rasterio
    return rasterio.open(path)


def _sample_point(ds, lat: float, lon: float) -> Optional[float]:
    from rasterio.windows import Window
    try:
        row, col = ds.index(lon, lat)
        if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
            return None
        val = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
        nodata = ds.nodata
        if nodata is not None and val == nodata:
            return None
        return float(val)
    except Exception:
        return None


def sample_gdp_total(lat: float, lon: float, year: int) -> Optional[float]:
    for yr_try in [year, year - 1, year + 1, year - 2, year + 2]:
        path = GDP_DIR / f"GDP{yr_try}.tif"
        if path.exists():
            ds = _open_raster(str(path))
            val = _sample_point(ds, lat, lon)
            if val is not None and val > 0:
                return val
    return None


def sample_pop_density(lat: float, lon: float, year: int) -> Optional[float]:
    for yr_try in [year, year - 1, year + 1, year - 2, year + 2, year - 3, year + 3]:
        if yr_try < 2000 or yr_try > 2025:
            continue
        for epoch in [2000, 2005, 2010, 2015, 2020]:
            if abs(epoch - yr_try) <= 2:
                path = POP_DENSITY_HIST_DIR / f"gpw_v4_population_density_rev11_{epoch}_30_sec_{epoch}.tif"
                if path.exists():
                    ds = _open_raster(str(path))
                    val = _sample_point(ds, lat, lon)
                    if val is not None and val > 0:
                        return val
    return None


def sample_ghsl_smod(lat: float, lon: float, year: int) -> Optional[float]:
    available_epochs = [1995, 2000, 2005, 2010, 2015, 2020, 2025]
    nearest = min(available_epochs, key=lambda e: abs(e - year))
    path = GHSL_DIR / f"GHS_SMOD_E{nearest}_GLOBE_R2023A_4326_30ss_V2_0.tif"
    if not path.exists():
        return None
    ds = _open_raster(str(path))
    val = _sample_point(ds, lat, lon)
    if val is None:
        return None
    if val < 0:  # water → treat as rural-low
        return 10.0
    return float(val)


def load_temperature(
    iso3: str, lat: float, lon: float, year: int,
    zone: Optional[str] = None,
) -> Optional[np.ndarray]:
    """Load ERA5 hourly temperature for a country-year.

    If ``zone`` is given, look up ``era5_{zone}_{year}.npy`` first and
    fall back to the national file. This allows zonal training samples
    to carry temperature at the zone centroid, while keeping backwards
    compatibility for country-level entries.
    """
    candidates = []
    if zone:
        candidates.append(ERA5_CACHE / iso3 / f"era5_{zone}_{year}.npy")
    candidates.append(ERA5_CACHE / iso3 / f"era5_{year}.npy")
    for era5 in candidates:
        if era5.exists():
            arr = np.load(era5)
            if len(arr) >= 8760:
                return arr[:8760].astype(np.float64)
    pq_name = (f"{iso3}_{zone}_{year}.parquet" if zone
               else f"{iso3}_{year}.parquet")
    pq = DEMAND_DATASET_DIR / iso3 / pq_name
    if pq.exists():
        import pandas as pd
        df = pd.read_parquet(pq)
        if 'temperature_c' in df.columns:
            t = df['temperature_c'].values.astype(np.float64)
            if not np.isnan(t).all():
                return t[:8760]
    return None


@dataclass
class PixelFeatures:
    lat: float
    lon: float
    year: int
    log_gdp_per_cap: float
    log_pop_density: float
    urbanization: float
    temperature: np.ndarray
    hdd: np.ndarray
    cdd: np.ndarray
    missing: list


def build_features_for_point(
    lat: float, lon: float, year: int, iso3: str,
    hdd_base: float = HDD_BASE, cdd_base: float = CDD_BASE,
    zone: Optional[str] = None,
) -> PixelFeatures:
    """Build feature vector at a given (lat, lon, year) sample.

    Used for:
    - Training: called once per country-year with lat/lon=capital coords
      or per zonal (country, zone, year) with lat/lon=zone centroid
    - Inference: called once per (pixel, year) with lat/lon=pixel centroid

    Both paths use the SAME function → no covariate shift.

    Parameters
    ----------
    zone : str, optional
        Zone identifier. When provided, ERA5 and UTCI are loaded from the
        per-zone cache (``era5_{zone}_{year}.npy``) first. Zone has no
        effect on static raster sampling — the lat/lon already encode the
        zone's geographic location.
    """
    missing: list[str] = []

    gdp_total = sample_gdp_total(lat, lon, year)
    pop_dens = sample_pop_density(lat, lon, year)
    if gdp_total is None:
        missing.append('gdp_total')
    if pop_dens is None:
        missing.append('pop_density')

    if gdp_total is not None and pop_dens is not None and pop_dens > 0.1:
        pixel_km2 = (0.25 ** 2) * np.cos(np.radians(lat)) * 111.0 ** 2
        pop_count = pop_dens * pixel_km2
        log_gdp = float(np.log(max(gdp_total / pop_count, 1.0))) if pop_count > 1 else np.nan
        if np.isnan(log_gdp):
            missing.append('gdp_per_cap_division')
    else:
        log_gdp = np.nan

    log_pop_density = float(np.log(max(pop_dens, 0.01))) if pop_dens is not None else np.nan

    urbanization = sample_ghsl_smod(lat, lon, year)
    if urbanization is None:
        urbanization = 10.0
        missing.append('urbanization')

    temp = load_temperature(iso3, lat, lon, year, zone=zone)
    if temp is None:
        temp = np.full(8760, 20.0)
        missing.append('temperature')

    hdd = np.maximum(hdd_base - temp, 0.0)
    cdd = np.maximum(temp - cdd_base, 0.0)

    return PixelFeatures(
        lat=lat, lon=lon, year=year,
        log_gdp_per_cap=log_gdp, log_pop_density=log_pop_density,
        urbanization=urbanization,
        temperature=temp, hdd=hdd, cdd=cdd,
        missing=missing,
    )
