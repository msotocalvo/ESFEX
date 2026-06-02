"""OTEC hourly capacity factor computation.

Computes hourly CF time series from CMEMS ocean temperature data
using temperature-difference-driven capacity factor scaling.

For standalone use outside the OTEC wizard (e.g., CLI availability generator).
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_HOURS_PER_YEAR = 8760


def compute_otec_hourly_cf(
    lat: float,
    lon: float,
    year: int,
    cf_nominal: float = 0.914,
    gross_power_kw: float = 136000,
    cycle_type: str = "rankine_closed",
    fluid_type: str = "ammonia",
    cost_level: str = "low_cost",
) -> np.ndarray:
    """Compute 8760-hour OTEC capacity factor from CMEMS ocean temperatures.

    Downloads daily warm-water (~20 m) and cold-water (~1000 m) temperatures
    from CMEMS for the given point, computes daily ΔT, scales CF linearly
    with ΔT relative to median ΔT, and expands to hourly resolution.

    Parameters
    ----------
    lat, lon : float
        Geographic coordinates of the OTEC plant.
    year : int
        Analysis year (for CMEMS data query).
    cf_nominal : float
        Nominal capacity factor at design-point ΔT (default 0.914).
    gross_power_kw : float
        Gross power output in kW (used to derive intake depths from OTEX).
    cycle_type, fluid_type, cost_level : str
        OTEX plant configuration parameters.

    Returns
    -------
    np.ndarray
        Array of shape (8760,) with hourly capacity factor values in [0, 1].
    """
    from esfex.models.otec_models import compute_daily_cf, expand_daily_to_hourly

    # Try to get intake depths from OTEX config
    try:
        from otex.config import get_default_config
        otex_cfg = get_default_config(
            gross_power=-abs(gross_power_kw),
            cycle_type=cycle_type,
            fluid_type=fluid_type,
            cost_level=cost_level,
            year=year,
        )
        inputs = otex_cfg.to_legacy_dict()
        depth_ww = inputs["length_WW_inlet"]   # ~21.6 m
        depth_cw = inputs["length_CW_inlet"]   # ~1062.4 m
    except Exception:
        depth_ww = 20.0
        depth_cw = 1000.0

    # Download CMEMS data for a small box around the point
    margin = 0.5  # degrees
    south, north = lat - margin, lat + margin
    west, east = lon - margin, lon + margin
    date_start = f"{year}-01-01"
    date_end = f"{year}-12-31"

    tmpdir = Path(tempfile.mkdtemp(prefix="otec_cf_"))

    try:
        ww_series = _download_point_series(
            tmpdir, south, west, north, east,
            depth_ww, "warm", date_start, date_end,
        )
        cw_series = _download_point_series(
            tmpdir, south, west, north, east,
            depth_cw, "cold", date_start, date_end,
        )
    except Exception as exc:
        logger.warning(
            "CMEMS download failed for (%.4f, %.4f): %s. Using constant CF.",
            lat, lon, exc,
        )
        return np.full(_HOURS_PER_YEAR, cf_nominal)

    if ww_series is None or cw_series is None:
        return np.full(_HOURS_PER_YEAR, cf_nominal)

    # Align lengths
    n = min(len(ww_series), len(cw_series))
    ww_series = ww_series[:n]
    cw_series = cw_series[:n]

    # Design-point ΔT = median
    delta_t = ww_series - cw_series
    delta_t_design = float(np.nanmedian(delta_t))
    if delta_t_design <= 0:
        return np.full(_HOURS_PER_YEAR, cf_nominal)

    # Build DailyOTECData and compute CF
    from esfex.models.otec_models import DailyOTECData

    timestamps = [f"{year}-{1 + d // 30:02d}-{1 + d % 30:02d}" for d in range(n)]
    daily_data = DailyOTECData(
        timestamps=timestamps, t_warm=ww_series, t_cold=cw_series,
    )
    daily_cf = compute_daily_cf(daily_data, cf_nominal, delta_t_design)
    return expand_daily_to_hourly(daily_cf)


def _download_point_series(
    tmpdir: Path,
    south: float, west: float, north: float, east: float,
    depth: float,
    label: str,
    date_start: str,
    date_end: str,
) -> np.ndarray | None:
    """Download CMEMS temperature and return nearest-point daily series."""
    import copernicusmarine

    filename = f"otec_{label}_{depth:.0f}m.nc"
    output_dir = str(tmpdir)

    try:
        copernicusmarine.subset(
            dataset_id="cmems_mod_glo_phy_my_0.083deg_P1D-m",
            dataset_version="202311",
            variables=["thetao"],
            minimum_longitude=west,
            maximum_longitude=east,
            minimum_latitude=south,
            maximum_latitude=north,
            minimum_depth=depth,
            maximum_depth=depth,
            start_datetime=date_start,
            end_datetime=date_end,
            force_download=True,
            output_directory=output_dir,
            output_filename=filename,
            netcdf3_compatible=True,
        )
    except RuntimeError as e:
        if "H5DSis_scale" in str(e):
            copernicusmarine.subset(
                dataset_id="cmems_mod_glo_phy_my_0.083deg_P1D-m",
                dataset_version="202311",
                variables=["thetao"],
                minimum_longitude=west,
                maximum_longitude=east,
                minimum_latitude=south,
                maximum_latitude=north,
                minimum_depth=depth,
                maximum_depth=depth,
                start_datetime=date_start,
                end_datetime=date_end,
                force_download=True,
                output_directory=output_dir,
                output_filename=filename,
                netcdf_compression_enabled=False,
            )
        else:
            raise

    filepath = tmpdir / filename
    return _extract_center_series(filepath)


def _extract_center_series(filepath: Path) -> np.ndarray | None:
    """Read NetCDF and extract time series at the center grid point."""
    import netCDF4

    nc = netCDF4.Dataset(str(filepath), "r")
    try:
        lats = np.array(nc.variables["latitude"][:])
        lons = np.array(nc.variables["longitude"][:])
        thetao = nc.variables["thetao"][:]
    finally:
        nc.close()

    if thetao.ndim == 4:
        thetao = thetao[:, 0, :, :]  # squeeze depth

    if hasattr(thetao, "filled"):
        thetao = thetao.filled(np.nan)
    thetao = np.array(thetao, dtype=np.float64)
    thetao[thetao <= 0] = np.nan
    thetao[thetao > 50] = np.nan

    # Get center point
    lat_mid = len(lats) // 2
    lon_mid = len(lons) // 2
    series = thetao[:, lat_mid, lon_mid]

    if np.all(np.isnan(series)):
        return None
    return series
