"""Download sub-daily CMIP6 tas from Pangeo and pre-compute lag features.

Downloads 3-hourly near-surface air temperature (tas) from CMIP6 ScenarioMIP
ssp245 via the Pangeo zarr store on Google Cloud. Computes lag features
(daily mean, 7d mean, 30d mean, trend, diurnal range) at daily resolution
on the native grid, then regrids to the 0.25° grid used by the ECVI.

Output: one NetCDF per (model, year) under GRIDDED_CMIP6_DIR/subdaily/
with variables at (day, lat, lon) resolution on the 0.25° grid:
    temp_daily_mean      — daily mean T (°C)
    temp_1d_lag          — previous day's mean
    temp_7d_mean         — 7-day rolling mean
    temp_30d_mean        — 30-day rolling mean
    temp_trend_7d        — temp_daily_mean - temp_7d_mean
    temp_daily_max       — daily max T
    temp_daily_min       — daily min T
    temp_diurnal_range   — max - min
"""
from __future__ import annotations

import logging
from pathlib import Path

import fsspec
import numpy as np
import pandas as pd
import xarray as xr

from esfex.paths import GRIDDED_CMIP6_DIR

logger = logging.getLogger(__name__)

SUBDAILY_DIR = GRIDDED_CMIP6_DIR / "subdaily"
CATALOG_URL = "https://storage.googleapis.com/cmip6/pangeo-cmip6.csv"

# Models with reliable 3hr tas for ssp245 (+ historical for pre-2015 reference)
MODELS = [
    ("GFDL-ESM4",      "r1i1p1f1", "gr1"),
    ("MPI-ESM1-2-HR",  "r1i1p1f1", "gn"),
]

TARGET_YEARS = [2025, 2050]

# Target 0.25° grid matching existing ECVI inputs (tas_2025_ssp245.nc)
# lat: -59.875 .. 89.875  (600 rows, ascending)
# lon: 0.125 .. 359.875   (1440 cols, ascending, 0-360 convention)
TARGET_LAT = np.arange(-59.875, 90.0, 0.25)
TARGET_LON = np.arange(0.125, 360.0, 0.25)


def _find_zstore(catalog: pd.DataFrame, source_id: str, member_id: str,
                  grid_label: str, experiment: str) -> str:
    sub = catalog[
        (catalog.source_id == source_id)
        & (catalog.member_id == member_id)
        & (catalog.grid_label == grid_label)
        & (catalog.table_id == "3hr")
        & (catalog.variable_id == "tas")
        & (catalog.experiment_id == experiment)
    ]
    if len(sub) == 0:
        raise RuntimeError(
            f"No zstore for {source_id}/{member_id}/{grid_label}/3hr/tas/{experiment}"
        )
    return sub.iloc[0].zstore


def _open_zstore(zstore: str) -> xr.Dataset:
    mapper = fsspec.get_mapper(zstore)
    return xr.open_zarr(mapper, consolidated=True)


def _regrid_bilinear(data: np.ndarray, src_lat: np.ndarray, src_lon: np.ndarray,
                     dst_lat: np.ndarray, dst_lon: np.ndarray) -> np.ndarray:
    """Bilinear regridding along (lat, lon). data shape: (..., lat, lon).

    Uses scipy RegularGridInterpolator; lon assumed 0-360 both sides.
    """
    from scipy.interpolate import RegularGridInterpolator

    # Ensure ascending lat
    if src_lat[0] > src_lat[-1]:
        src_lat = src_lat[::-1]
        data = data[..., ::-1, :]

    # Build interpolator
    dst_lon_g, dst_lat_g = np.meshgrid(dst_lon, dst_lat)
    pts = np.stack([dst_lat_g.ravel(), dst_lon_g.ravel()], axis=-1)

    flat_shape = data.shape[:-2]
    out = np.empty((*flat_shape, len(dst_lat), len(dst_lon)), dtype=np.float32)
    it = np.ndindex(*flat_shape) if flat_shape else [()]
    for idx in it:
        sub = data[idx] if flat_shape else data
        interp = RegularGridInterpolator(
            (src_lat, src_lon), sub,
            method="linear", bounds_error=False, fill_value=np.nan,
        )
        out[idx] = interp(pts).reshape(len(dst_lat), len(dst_lon))
    return out


def _compute_lag_features(tas_3h: np.ndarray) -> dict[str, np.ndarray]:
    """Compute daily lag features from 3-hourly temperature (°C).

    tas_3h: (n_3h_steps, lat, lon) where n_3h_steps = 8 * n_days.
    Returns dict of (n_days, lat, lon) arrays.
    """
    n_3h, nlat, nlon = tas_3h.shape
    assert n_3h % 8 == 0, f"Expected multiple of 8 (3hr steps/day), got {n_3h}"
    n_days = n_3h // 8
    t_daily = tas_3h.reshape(n_days, 8, nlat, nlon)

    daily_mean = t_daily.mean(axis=1)
    daily_max = t_daily.max(axis=1)
    daily_min = t_daily.min(axis=1)

    # Rolling means using cumulative sum (fast, NaN-free since tas has no NaN)
    def _rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
        c = np.cumsum(x, axis=0, dtype=np.float64)
        out = np.empty_like(x)
        # First (window-1) days: expanding window
        for d in range(n_days):
            lo = max(0, d - window + 1)
            out[d] = (c[d] - (c[lo - 1] if lo > 0 else 0.0)) / (d - lo + 1)
        return out

    temp_7d = _rolling_mean(daily_mean, 7)
    temp_30d = _rolling_mean(daily_mean, 30)

    # 1-day lag (shift by 1; fill day 0 with its own value)
    lag_1d = np.empty_like(daily_mean)
    lag_1d[0] = daily_mean[0]
    lag_1d[1:] = daily_mean[:-1]

    return {
        "temp_daily_mean": daily_mean.astype(np.float32),
        "temp_daily_max": daily_max.astype(np.float32),
        "temp_daily_min": daily_min.astype(np.float32),
        "temp_diurnal_range": (daily_max - daily_min).astype(np.float32),
        "temp_1d_lag": lag_1d.astype(np.float32),
        "temp_7d_mean": temp_7d.astype(np.float32),
        "temp_30d_mean": temp_30d.astype(np.float32),
        "temp_trend_7d": (daily_mean - temp_7d).astype(np.float32),
    }


def download_and_process(source_id: str, member_id: str, grid_label: str,
                          year: int, catalog: pd.DataFrame) -> Path:
    """Download 3hr tas, compute lag features, regrid to 0.25°, save NC."""
    SUBDAILY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SUBDAILY_DIR / f"tas_lags_{year}_ssp245_{source_id}.nc"
    if out_path.exists():
        logger.info("Already have %s", out_path.name)
        return out_path

    zstore = _find_zstore(catalog, source_id, member_id, grid_label, "ssp245")
    logger.info("Opening %s", zstore)
    ds = _open_zstore(zstore)

    # Use the dataset's native calendar (CMIP6 often NoLeap / 360_day)
    cal_class = type(ds.time.values[0])
    # Include prev 30 days for rolling-window warmup
    t0 = cal_class(year - 1, 12, 1)
    t1 = cal_class(year + 1, 1, 1)
    sub = ds.tas.sel(time=slice(t0, t1))
    logger.info("Loading %d 3h timesteps from %s to %s...",
                sub.shape[0], sub.time.values[0], sub.time.values[-1])

    # Trigger actual download (lazy → eager)
    tas_k = sub.load().values  # (time, lat, lon) in K
    tas_c = (tas_k - 273.15).astype(np.float32)

    # Align to day boundaries (expect 8 samples/day). Times are cftime objects.
    times = sub.time.values  # array of cftime objects
    first_day_idx = 0
    while first_day_idx < 8 and times[first_day_idx].hour != 0:
        first_day_idx += 1
    # Truncate to whole days
    n_aligned = ((tas_c.shape[0] - first_day_idx) // 8) * 8
    tas_c = tas_c[first_day_idx:first_day_idx + n_aligned]
    times = times[first_day_idx:first_day_idx + n_aligned]
    logger.info("Aligned: %d timesteps (%d days)", n_aligned, n_aligned // 8)

    logger.info("Computing lag features on native grid...")
    lags_native = _compute_lag_features(tas_c)

    # Day years (one per day): sample every 8th timestep
    day_years = np.array([times[i * 8].year for i in range(n_aligned // 8)])
    day_months = np.array([times[i * 8].month for i in range(n_aligned // 8)])
    day_days = np.array([times[i * 8].day for i in range(n_aligned // 8)])
    # Use ISO strings (safe across calendars) as the day coordinate
    day_dates = np.array([f"{y:04d}-{m:02d}-{d:02d}" for y, m, d in
                           zip(day_years, day_months, day_days)])

    # Extract only the requested year
    year_mask = day_years == year
    day_dates = day_dates[year_mask]
    for k in lags_native:
        lags_native[k] = lags_native[k][year_mask]
    logger.info("Trimmed to year %d: %d days", year, len(day_dates))

    src_lat = sub.lat.values
    src_lon = sub.lon.values
    # Ensure src_lon in 0-360
    if src_lon.min() < 0:
        shift = np.argmin(src_lon)
        src_lon = np.concatenate([src_lon[shift:], src_lon[:shift] + 360])
        for k, v in lags_native.items():
            lags_native[k] = np.concatenate(
                [v[:, :, shift:], v[:, :, :shift]], axis=2
            )

    logger.info("Regridding to 0.25° (%d lat × %d lon)...",
                len(TARGET_LAT), len(TARGET_LON))
    lags_regridded = {}
    for k, v in lags_native.items():
        logger.info("  %s", k)
        lags_regridded[k] = _regrid_bilinear(
            v, src_lat, src_lon, TARGET_LAT, TARGET_LON,
        )

    logger.info("Writing %s", out_path)
    coords = {
        "day": day_dates,
        "lat": TARGET_LAT.astype(np.float32),
        "lon": TARGET_LON.astype(np.float32),
    }
    data_vars = {
        k: (("day", "lat", "lon"), v) for k, v in lags_regridded.items()
    }
    out = xr.Dataset(data_vars=data_vars, coords=coords,
                      attrs={"source": f"CMIP6/{source_id}/ssp245/{member_id}",
                             "year": year})
    enc = {k: {"zlib": True, "complevel": 4, "dtype": "float32"}
           for k in data_vars}
    out.to_netcdf(out_path, encoding=enc)
    logger.info("Done: %s (%.1f MB)",
                out_path.name, out_path.stat().st_size / 1e6)
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(message)s")
    logger.info("Loading Pangeo CMIP6 catalog...")
    catalog = pd.read_csv(CATALOG_URL)

    for source_id, member_id, grid_label in MODELS:
        for year in TARGET_YEARS:
            logger.info("=== %s %d ===", source_id, year)
            try:
                download_and_process(source_id, member_id, grid_label,
                                     year, catalog)
            except Exception as exc:
                logger.error("FAILED %s %d: %s", source_id, year, exc)
                import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
