"""Wind hourly capacity factor computation.

Computes hourly CF time series from wind speed reanalysis data
using turbine power curves and hub-height wind speed extrapolation.

Adapted from esfex.visualization.workflows.wind_analysis helpers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_HOURS_PER_YEAR = 8760
_FEB29_START = 24 * (31 + 28)
_FEB29_END = _FEB29_START + 24

# Default turbine: Vestas V112 3.0 MW
_DEFAULT_WIND_SPEEDS = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
]
_DEFAULT_POWER_CURVE_MW = [
    0, 0, 0, 0.032, 0.16, 0.36, 0.67, 1.08, 1.58, 2.12,
    2.58, 2.85, 2.98, 3.0, 3.0, 3.0,
    3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 0,
]
_DEFAULT_RATED_MW = 3.0


@dataclass
class TurbineSpec:
    """Technical specification of a wind turbine."""

    key: str
    name: str
    manufacturer: str
    rated_power_mw: float
    rotor_diameter_m: float
    hub_height_m: float
    source: str = "atlite"
    wind_speeds: list[float] = field(default_factory=list)
    power_curve: list[float] = field(default_factory=list)


def load_atlite_builtin_turbines() -> list[TurbineSpec]:
    """Load turbine specs from atlite's bundled YAML resource files.

    Returns a list sorted by manufacturer then rated power.
    Falls back to an empty list if atlite is not installed.
    """
    try:
        import importlib.resources
        import yaml
    except ImportError:
        return []

    turbines: list[TurbineSpec] = []

    try:
        resource_dir = importlib.resources.files("atlite") / "resources" / "windturbine"
        yaml_files = [
            f for f in resource_dir.iterdir()
            if str(f).endswith((".yaml", ".yml"))
        ]
    except Exception:
        return []

    for yf in yaml_files:
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue

            key = yf.name.rsplit(".", 1)[0]
            v_arr = data.get("V", [])
            pow_arr = data.get("POW", [])
            if not v_arr or not pow_arr:
                continue

            rated_mw = max(pow_arr) if pow_arr else 0
            hub_h = data.get("HUB_HEIGHT", data.get("hub_height", 80))
            rotor_d = data.get("rotor_diameter", 0)
            if not rotor_d:
                # Try to parse from key name (e.g. Vestas_V112_3MW)
                parts = key.replace("-", "_").split("_")
                for p in parts:
                    if p.startswith("V") and p[1:].isdigit():
                        rotor_d = float(p[1:])
                        break

            turbines.append(TurbineSpec(
                key=key,
                name=data.get("name", key),
                manufacturer=data.get("manufacturer", _guess_manufacturer(key)),
                rated_power_mw=rated_mw,
                rotor_diameter_m=rotor_d,
                hub_height_m=hub_h,
                source="atlite",
                wind_speeds=list(v_arr),
                power_curve=list(pow_arr),
            ))
        except Exception:
            continue

    turbines.sort(key=lambda t: (t.manufacturer.lower(), t.rated_power_mw))
    return turbines


def _guess_manufacturer(key: str) -> str:
    """Guess manufacturer name from turbine key."""
    parts = key.replace("-", "_").split("_")
    if parts:
        return parts[0]
    return "Unknown"


def compute_wind_hourly_cf(
    lat: float,
    lon: float,
    year: int,
    data_source: str = "open_meteo",
    wind_speeds: Optional[list[float]] = None,
    power_curve: Optional[list[float]] = None,
    rated_power_mw: float = _DEFAULT_RATED_MW,
    hub_height: int = 80,
    turbine_key: Optional[str] = None,
) -> np.ndarray:
    """Compute hourly wind capacity factor for a single location.

    Parameters
    ----------
    lat, lon : float
        Geographic coordinates (WGS84).
    year : int
        Calendar year for weather data.
    data_source : str
        One of ``"open_meteo"``, ``"nasa_power"``, ``"era5_atlite"``.
    wind_speeds : list[float] or None
        Power curve wind speeds (m/s).  None = use default or turbine_key.
    power_curve : list[float] or None
        Power curve output (MW).
    rated_power_mw : float
        Turbine rated power (MW).
    hub_height : int
        Hub height in meters.
    turbine_key : str or None
        Atlite turbine key to look up power curve.

    Returns
    -------
    np.ndarray
        Hourly capacity factors with shape ``(8760,)`` and values in [0, 1].
    """
    # Resolve power curve
    pc_ws, pc_mw, rated = _resolve_power_curve(
        wind_speeds, power_curve, rated_power_mw, turbine_key,
    )

    if data_source == "era5_atlite":
        return _compute_atlite_wind(lat, lon, year, turbine_key, hub_height)

    if data_source == "nasa_power":
        ws_hourly = _fetch_nasa_power_wind(lat, lon, year, hub_height)
    else:
        ws_hourly = _fetch_open_meteo_wind(lat, lon, year, hub_height)

    if ws_hourly is None:
        logger.warning(
            "Failed to fetch wind data for (%.4f, %.4f) year %d, "
            "returning zeros.",
            lat, lon, year,
        )
        return np.zeros(_HOURS_PER_YEAR)

    cf = _wind_speed_to_hourly_cf(ws_hourly, pc_ws, pc_mw, rated)
    return _normalize_to_8760(cf)


# ------------------------------------------------------------------
# CF computation from wind speed
# ------------------------------------------------------------------


def _wind_speed_to_hourly_cf(
    ws_hourly: np.ndarray,
    pc_wind_speeds: list[float],
    pc_power_mw: list[float],
    rated_mw: float,
) -> np.ndarray:
    """Convert hourly wind speeds to capacity factors using power curve."""
    if not pc_wind_speeds or not pc_power_mw or rated_mw <= 0:
        return np.zeros(len(ws_hourly))

    ws = np.asarray(ws_hourly, dtype=float)
    power_out = np.interp(ws, pc_wind_speeds, pc_power_mw, left=0.0, right=0.0)
    cf = power_out / rated_mw
    return np.clip(cf, 0.0, 1.0)


def _resolve_power_curve(
    wind_speeds: Optional[list[float]],
    power_curve: Optional[list[float]],
    rated_power_mw: float,
    turbine_key: Optional[str],
) -> tuple[list[float], list[float], float]:
    """Resolve power curve from explicit values or turbine database."""
    if wind_speeds and power_curve:
        return wind_speeds, power_curve, rated_power_mw

    if turbine_key:
        turbines = load_atlite_builtin_turbines()
        for t in turbines:
            if t.key == turbine_key:
                return t.wind_speeds, t.power_curve, t.rated_power_mw
        logger.warning(
            "Turbine '%s' not found in database, using default.",
            turbine_key,
        )

    return _DEFAULT_WIND_SPEEDS, _DEFAULT_POWER_CURVE_MW, _DEFAULT_RATED_MW


# ------------------------------------------------------------------
# Open-Meteo Historical API
# ------------------------------------------------------------------


def _fetch_open_meteo_wind(
    lat: float, lon: float, year: int, hub_height: int,
) -> "np.ndarray | None":
    """Fetch hourly wind speed at hub height from Open-Meteo.

    Provides 10m and 100m wind; extrapolated to hub height via power law.
    """
    import requests

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "wind_speed_10m,wind_speed_100m",
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }

    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        ws10 = np.array(hourly.get("wind_speed_10m", []), dtype=float)
        ws100 = np.array(hourly.get("wind_speed_100m", []), dtype=float)

        if len(ws100) == 0:
            return None

        if hub_height == 100 or len(ws10) == 0:
            return ws100

        # Power-law extrapolation: alpha = ln(ws100/ws10) / ln(100/10)
        ws10_safe = np.maximum(ws10, 0.01)
        alpha = np.log(np.maximum(ws100, 0.01) / ws10_safe) / np.log(100.0 / 10.0)
        alpha = np.clip(alpha, 0.05, 0.50)

        ws_hub = ws100 * (hub_height / 100.0) ** alpha
        return ws_hub

    except Exception as exc:
        logger.debug(
            "Open-Meteo wind fetch failed for (%.2f, %.2f): %s",
            lat, lon, exc,
        )
        return None


# ------------------------------------------------------------------
# NASA POWER API (MERRA-2)
# ------------------------------------------------------------------


def _fetch_nasa_power_wind(
    lat: float, lon: float, year: int, hub_height: int,
) -> "np.ndarray | None":
    """Fetch hourly wind speed from NASA POWER API.

    Provides WS10M and WS50M; extrapolated to hub height via power law.
    """
    import requests

    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    params = {
        "parameters": "WS10M,WS50M",
        "community": "RE",
        "longitude": round(lon, 4),
        "latitude": round(lat, 4),
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "JSON",
    }

    try:
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        props = data.get("properties", {}).get("parameter", {})
        ws10_dict = props.get("WS10M", {})
        ws50_dict = props.get("WS50M", {})

        ws10 = np.array([v for v in ws10_dict.values() if v != -999], dtype=float)
        ws50 = np.array([v for v in ws50_dict.values() if v != -999], dtype=float)

        if len(ws50) == 0:
            return None

        ws10_safe = np.maximum(ws10[: len(ws50)], 0.01)
        alpha = np.log(np.maximum(ws50, 0.01) / ws10_safe) / np.log(50.0 / 10.0)
        alpha = np.clip(alpha, 0.05, 0.50)

        ws_hub = ws50 * (hub_height / 50.0) ** alpha
        return ws_hub

    except Exception as exc:
        logger.debug(
            "NASA POWER wind fetch failed for (%.2f, %.2f): %s",
            lat, lon, exc,
        )
        return None


# ------------------------------------------------------------------
# ERA5 via atlite (optional)
# ------------------------------------------------------------------


def _compute_atlite_wind(
    lat: float, lon: float, year: int,
    turbine_key: Optional[str], hub_height: int,
) -> np.ndarray:
    """Download ERA5 via atlite and compute wind capacity factors."""
    try:
        import atlite
    except ImportError:
        logger.error("atlite is not installed. Install with: pip install atlite")
        return np.zeros(_HOURS_PER_YEAR)

    import tempfile
    from pathlib import Path

    tmpdir = Path(tempfile.mkdtemp(prefix="avail_wind_era5_"))
    cutout_path = tmpdir / "cutout.nc"

    delta = 0.05
    cutout = atlite.Cutout(
        path=cutout_path,
        module="era5",
        x=slice(lon - delta, lon + delta),
        y=slice(lat - delta, lat + delta),
        time=str(year),
    )
    cutout.prepare()

    turbine = turbine_key or "Vestas_V112_3MW"
    cf_ts = cutout.wind(turbine=turbine, capacity_factor_timeseries=True)

    cf_values = cf_ts.values
    if cf_values.ndim == 3:
        cf_hourly = cf_values[:, 0, 0]
    elif cf_values.ndim == 2:
        cf_hourly = cf_values[:, 0]
    else:
        cf_hourly = cf_values

    cf_hourly = np.clip(cf_hourly, 0.0, 1.0)
    return _normalize_to_8760(cf_hourly)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize_to_8760(cf: np.ndarray) -> np.ndarray:
    """Ensure output has exactly 8760 hours (remove Feb 29 for leap years)."""
    n = len(cf)
    if n == _HOURS_PER_YEAR:
        return cf
    if n == 8784:
        return np.concatenate([cf[:_FEB29_START], cf[_FEB29_END:]])
    if n > _HOURS_PER_YEAR:
        return cf[:_HOURS_PER_YEAR]
    padded = np.zeros(_HOURS_PER_YEAR)
    padded[:n] = cf
    return padded
