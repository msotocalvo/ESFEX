"""Climate-adjusted availability profiles and demand curves.

Generates scenario-specific capacity factor profiles and electricity demand
under different Shared Socioeconomic Pathways (SSPs).  The module implements
three levels of sophistication:

1. **Delta-based scaling** — simple multiplicative adjustments to existing
   availability profiles using user-specified fractional changes.  No external
   data required.

2. **Capacity-factor recomputation** — given raw meteorological variables
   (GHI, temperature, wind speed) from NEX-GDDP-CMIP6 or ERA5, recompute
   technology-specific capacity factors with physics-based models.

3. **Full pipeline** — download, bias-correct, convert, and cache
   climate-adjusted profiles for every scenario × year × node combination.

Mathematical foundations are documented in
``docs/formulation/risk-resilience.md``, equations RISK-7 through RISK-11b.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


# =============================================================================
# Bias Correction (RISK-7)
# =============================================================================


def quantile_mapping(
    raw: np.ndarray,
    model_hist: np.ndarray,
    obs_hist: np.ndarray,
    n_quantiles: int = 100,
) -> np.ndarray:
    """Bias-correct climate model output via quantile mapping.

    Implements equation RISK-7:
        x_corrected = F_obs^{-1}( F_model(x_raw) )

    This ensures that the statistical distribution of the corrected
    projections matches the observed distribution while preserving the
    climate change signal (trend).

    Parameters
    ----------
    raw : ndarray
        Raw climate model projection values (future period).
    model_hist : ndarray
        Climate model values for the historical reference period.
    obs_hist : ndarray
        Observed historical values (ERA5, MERRA-2, or station data).
    n_quantiles : int
        Number of quantile bins for the transfer function.

    Returns
    -------
    ndarray
        Corrected projection values with shape matching *raw*.
    """
    quantiles = np.linspace(0, 1, n_quantiles + 1)

    # Build empirical CDFs via quantile bins
    model_q = np.quantile(model_hist, quantiles)
    obs_q = np.quantile(obs_hist, quantiles)

    # Map raw values through the transfer function:
    #   1. Find the quantile of each raw value in the model distribution
    #   2. Apply the corresponding observed quantile
    corrected = np.interp(raw, model_q, obs_q)

    return corrected


# =============================================================================
# Solar PV Capacity Factor (RISK-8)
# =============================================================================


def compute_solar_cf_climate(
    ghi: np.ndarray,
    temperature: np.ndarray,
    module_efficiency: float = 0.20,
    temp_coefficient: float = 0.004,
    noct: float = 45.0,
    ghi_stc: float = 1000.0,
) -> np.ndarray:
    """Solar PV capacity factor with temperature derating.

    Implements equation RISK-8:
        CF = η · (1 − γ·(T_cell − 25)) · GHI / GHI_STC

    Cell temperature is estimated from the NOCT model:
        T_cell = T_amb + (NOCT − 20) · GHI / 800

    Parameters
    ----------
    ghi : ndarray
        Global horizontal irradiance in W/m², shape ``(hours,)`` or
        ``(hours, nodes)``.
    temperature : ndarray
        Ambient temperature in °C, same shape as *ghi*.
    module_efficiency : float
        Module efficiency at Standard Test Conditions (STC).
    temp_coefficient : float
        Temperature coefficient of power (1/°C), typically 0.003–0.005 for
        crystalline silicon.
    noct : float
        Nominal Operating Cell Temperature (°C).
    ghi_stc : float
        Reference irradiance at STC (W/m²).

    Returns
    -------
    ndarray
        Capacity factors clipped to [0, 1].
    """
    # Cell temperature via NOCT model
    t_cell = temperature + (noct - 20.0) * ghi / 800.0

    # Capacity factor with temperature derating
    cf = module_efficiency * (1.0 - temp_coefficient * (t_cell - 25.0)) * ghi / ghi_stc

    return np.clip(cf, 0.0, 1.0)


# =============================================================================
# Wind Capacity Factor (RISK-9)
# =============================================================================


_DEFAULT_POWER_CURVE = np.array([
    # (wind_speed_m_s, normalized_power)
    [0.0, 0.0],
    [3.0, 0.0],       # cut-in
    [4.0, 0.02],
    [5.0, 0.05],
    [6.0, 0.10],
    [7.0, 0.18],
    [8.0, 0.29],
    [9.0, 0.43],
    [10.0, 0.58],
    [11.0, 0.74],
    [12.0, 0.87],
    [13.0, 0.95],
    [14.0, 0.99],
    [15.0, 1.00],      # rated
    [25.0, 1.00],      # rated plateau
    [25.01, 0.0],      # cut-out
    [50.0, 0.0],
])


def compute_wind_cf_climate(
    wind_speed: np.ndarray,
    temperature: np.ndarray,
    power_curve: np.ndarray | None = None,
    hub_height: float = 80.0,
    reference_height: float = 10.0,
    hellmann_exponent: float = 0.143,
    air_density_ref: float = 1.225,
) -> np.ndarray:
    """Wind capacity factor with air density correction.

    Implements equation RISK-9:
        CF = P_curve(v) · (ρ_s / ρ_0)

    If wind speed is at reference height, it is extrapolated to hub height
    using the power-law profile:
        v_hub = v_ref · (hub_height / reference_height)^α

    Air density correction accounts for temperature effects:
        ρ = ρ_0 · 288.15 / (T + 273.15)

    Parameters
    ----------
    wind_speed : ndarray
        Wind speed at *reference_height* (m/s).
    temperature : ndarray
        Ambient temperature (°C), same shape as *wind_speed*.
    power_curve : ndarray, optional
        Turbine power curve as ``(n, 2)`` array of ``(speed, power)`` pairs.
        Uses a generic IEC Class II curve if not provided.
    hub_height : float
        Turbine hub height (m).
    reference_height : float
        Height of wind speed measurement (m).
    hellmann_exponent : float
        Hellmann exponent for wind shear (default: 0.143 for open terrain).
    air_density_ref : float
        Reference air density at sea level, 15°C (kg/m³).

    Returns
    -------
    ndarray
        Capacity factors clipped to [0, 1].
    """
    if power_curve is None:
        power_curve = _DEFAULT_POWER_CURVE

    # Extrapolate to hub height
    if abs(hub_height - reference_height) > 1.0:
        wind_speed = wind_speed * (hub_height / reference_height) ** hellmann_exponent

    # Interpolate power curve
    cf = np.interp(wind_speed, power_curve[:, 0], power_curve[:, 1])

    # Air density correction (temperature-dependent)
    rho = air_density_ref * 288.15 / (temperature + 273.15)
    density_factor = rho / air_density_ref
    cf = cf * density_factor

    return np.clip(cf, 0.0, 1.0)


# =============================================================================
# Temperature-Dependent Demand (RISK-10, RISK-11a/b)
# =============================================================================


def compute_climate_demand(
    base_demand: np.ndarray,
    temperature: np.ndarray,
    base_temp: float = 24.0,
    alpha_heat: float = 0.5,
    alpha_cool: float = 2.5,
) -> np.ndarray:
    """Adjust demand for temperature via HDD/CDD.

    Implements equations RISK-10, RISK-11a, RISK-11b:
        D_s(t) = D_base(t) × (1 + α_cool/100 · CDD(t) + α_heat/100 · HDD(t))
        HDD(t) = max(T_base − T(t), 0)
        CDD(t) = max(T(t) − T_base, 0)

    Coefficients are in **percent per degree** (%/°C): ``alpha_cool=2.5``
    means demand increases by 2.5% per degree above *base_temp*.

    Parameters
    ----------
    base_demand : ndarray
        Weather-normalised base demand profile (MW).
    temperature : ndarray
        Ambient temperature (°C), same shape as *base_demand*.
    base_temp : float
        Base temperature for HDD/CDD calculation (°C).
        Default 24°C for tropical SIDS; 18°C for temperate.
    alpha_heat : float
        Heating demand sensitivity (%/°C below base_temp).
        Refs: Sailor & Munoz (1997). Typical 0.5 for tropical SIDS.
    alpha_cool : float
        Cooling demand sensitivity (%/°C above base_temp).
        Refs: Lam et al. (2018), IRENA (2019). Typical 2–4 for Caribbean.

    Returns
    -------
    ndarray
        Adjusted demand profile (MW).  Clipped to be non-negative.
    """
    hdd = np.maximum(base_temp - temperature, 0.0)
    cdd = np.maximum(temperature - base_temp, 0.0)

    # Fractional adjustment: α is %/°C, so divide by 100
    multiplier = 1.0 + (alpha_cool / 100.0) * cdd + (alpha_heat / 100.0) * hdd
    adjusted = base_demand * multiplier

    return np.maximum(adjusted, 0.0)


# =============================================================================
# Delta-Based Adjustment (simple mode)
# =============================================================================


def apply_climate_deltas(
    base_availability: np.ndarray,
    gen_type: str,
    ghi_delta: float = 0.0,
    wind_delta: float = 0.0,
    year: int = 0,
    temperature_delta: float = 0.0,
    temp_coefficient: float = 0.004,
) -> np.ndarray:
    """Apply simple fractional scaling to existing availability profiles.

    This is the "no external data" mode: the user specifies percentage
    changes per year via ``ClimateScenarioConfig.ghi_delta_fraction`` and
    ``wind_speed_delta_fraction``, and these are applied multiplicatively.

    For solar, an additional temperature derating is applied if
    *temperature_delta* is non-zero.

    Parameters
    ----------
    base_availability : ndarray
        Original availability profile, shape ``(hours,)`` or ``(hours, nodes)``.
    gen_type : str
        Generator technology type (``"solar"``, ``"wind"``, or other).
    ghi_delta : float
        Fractional change in GHI (e.g. −0.02 = −2%).
    wind_delta : float
        Fractional change in wind speed (e.g. −0.05 = −5%).
    year : int
        Simulation year (for logging).
    temperature_delta : float
        Temperature increase from baseline (°C).
    temp_coefficient : float
        PV temperature coefficient (1/°C).

    Returns
    -------
    ndarray
        Adjusted availability, clipped to [0, 1].
    """
    adjusted = base_availability.copy()

    gen_lower = gen_type.lower()
    if "solar" in gen_lower or "sun" in gen_lower or "pv" in gen_lower:
        # GHI scaling
        adjusted = adjusted * (1.0 + ghi_delta)
        # Temperature derating: higher temperature → lower efficiency
        if temperature_delta != 0.0:
            # Approximate derating: CF_new ≈ CF_old × (1 − γ × ΔT)
            adjusted = adjusted * (1.0 - temp_coefficient * temperature_delta)

    elif "wind" in gen_lower:
        # Wind speed affects power cubically, but CF already integrates the
        # power curve.  For small deltas, a linear approximation is adequate:
        # CF_new ≈ CF_old × (1 + wind_delta)
        # For larger changes, the exponent could be raised to ~1.5–2.0 to
        # account for the non-linear power curve, but this simple mode
        # intentionally avoids that complexity.
        adjusted = adjusted * (1.0 + wind_delta)

    return np.clip(adjusted, 0.0, 1.0)


# =============================================================================
# Scenario Profile Pipeline
# =============================================================================


def _interpolate_delta(delta_map: dict[int, float], year: int) -> float:
    """Linearly interpolate a year→value dict at a given year."""
    if not delta_map:
        return 0.0
    years = sorted(delta_map.keys())
    if year <= years[0]:
        return delta_map[years[0]]
    if year >= years[-1]:
        return delta_map[years[-1]]
    # Find bracketing years
    for i in range(len(years) - 1):
        if years[i] <= year <= years[i + 1]:
            y0, y1 = years[i], years[i + 1]
            v0, v1 = delta_map[y0], delta_map[y1]
            frac = (year - y0) / (y1 - y0) if y1 != y0 else 0.0
            return v0 + frac * (v1 - v0)
    return 0.0


def generate_scenario_profiles(
    base_availability: dict[str, np.ndarray],
    base_demand: np.ndarray,
    climate_scenario: Any,
    year: int,
    generator_types: dict[str, str],
    risk_config: Any,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Generate climate-adjusted profiles for one scenario and year.

    Uses the delta-based approach: scales existing availability profiles
    according to the fractional changes specified in the
    ``ClimateScenarioConfig``.

    Parameters
    ----------
    base_availability : dict
        Mapping of ``gen_key → availability_array`` (original profiles).
    base_demand : ndarray
        Original demand profile, shape ``(hours, nodes)``.
    climate_scenario : ClimateScenarioConfig
        The climate scenario definition.
    year : int
        The simulation year to generate profiles for.
    generator_types : dict
        Mapping of ``gen_key → fuel_type`` (e.g. ``"Sun"``, ``"Wind"``).
    risk_config : RiskConfig
        Top-level risk configuration (for temperature coefficients).

    Returns
    -------
    tuple
        ``(adjusted_availability, adjusted_demand)`` where
        *adjusted_availability* has the same structure as *base_availability*
        and *adjusted_demand* has the same shape as *base_demand*.
    """
    ghi_delta = _interpolate_delta(climate_scenario.ghi_delta_fraction, year)
    wind_delta = _interpolate_delta(climate_scenario.wind_speed_delta_fraction, year)
    temp_delta = _interpolate_delta(climate_scenario.temperature_delta, year)
    demand_mult = _interpolate_delta(climate_scenario.demand_scale, year)
    if demand_mult == 0.0:
        demand_mult = 1.0

    adjusted_avail: dict[str, np.ndarray] = {}
    for gen_key, avail in base_availability.items():
        fuel = generator_types.get(gen_key, "")
        adjusted_avail[gen_key] = apply_climate_deltas(
            base_availability=avail,
            gen_type=fuel,
            ghi_delta=ghi_delta,
            wind_delta=wind_delta,
            year=year,
            temperature_delta=temp_delta,
        )

    # Demand adjustment
    adjusted_demand = base_demand * demand_mult
    # Additional temperature-based demand adjustment
    if temp_delta != 0.0 and (
        risk_config.demand_heating_coefficient > 0
        or risk_config.demand_cooling_coefficient > 0
    ):
        # Create a synthetic temperature offset array
        # (uniform ΔT across all hours — simplification for delta mode)
        temp_offset = np.full_like(base_demand, temp_delta, dtype=float)
        base_temp = risk_config.demand_base_temperature
        # HDD decreases, CDD increases with positive ΔT
        hdd_reduction = risk_config.demand_heating_coefficient * np.maximum(-temp_offset, 0.0)
        cdd_increase = risk_config.demand_cooling_coefficient * np.maximum(temp_offset, 0.0)
        adjusted_demand = adjusted_demand + cdd_increase - hdd_reduction

    adjusted_demand = np.maximum(adjusted_demand, 0.0)

    logger.info(
        "Climate scenario '%s' year %d: GHI=%.1f%%, wind=%.1f%%, "
        "ΔT=%.1f°C, demand×%.3f",
        climate_scenario.name,
        year,
        ghi_delta * 100,
        wind_delta * 100,
        temp_delta,
        demand_mult,
    )

    return adjusted_avail, adjusted_demand


# =============================================================================
# NEX-GDDP-CMIP6 Fetch (optional, requires xarray + cdsapi)
# =============================================================================


def fetch_nex_gddp(
    bounds: tuple[float, float, float, float],
    variables: list[str],
    scenario: str,
    gcm_model: str,
    years: list[int],
    on_progress: Any = None,
) -> Any:
    """Download NEX-GDDP-CMIP6 data for the study area.

    Requires ``xarray`` and access to NASA's NEX-GDDP-CMIP6 dataset
    (available on AWS S3 or Google Earth Engine).

    Parameters
    ----------
    bounds : tuple
        ``(south, west, north, east)`` bounding box in decimal degrees.
    variables : list of str
        Climate variables to fetch, e.g. ``["rsds", "sfcWind", "tas"]``.
    scenario : str
        SSP-RCP pathway, e.g. ``"ssp245"``.
    gcm_model : str
        GCM model name, e.g. ``"ACCESS-CM2"``.
    years : list of int
        Years to download.
    on_progress : callable, optional
        ``(percent, message)`` callback for progress tracking.

    Returns
    -------
    xarray.Dataset or None
        Dataset with requested variables, or None if dependencies are
        unavailable.

    Notes
    -----
    This function is a convenience wrapper.  For production use, consider
    downloading data separately and pointing availability profiles to the
    pre-processed files.
    """
    try:
        import xarray as xr
    except ImportError:
        logger.warning(
            "xarray not installed — cannot fetch NEX-GDDP-CMIP6 data.  "
            "It ships with esfex; reinstall with: "
            "pip install --upgrade --force-reinstall esfex"
        )
        return None

    south, west, north, east = bounds

    datasets = []
    total = len(years) * len(variables)
    done = 0

    for year in years:
        for var in variables:
            url = (
                f"https://nex-gddp-cmip6.s3.us-west-2.amazonaws.com/"
                f"NEX-GDDP-CMIP6/{gcm_model}/{scenario}/r1i1p1f1/"
                f"{var}/{var}_day_{gcm_model}_{scenario}_r1i1p1f1_gn_{year}.nc"
            )
            try:
                ds = xr.open_dataset(url, engine="netcdf4")
                # Subset to bounding box
                ds = ds.sel(
                    lat=slice(south, north),
                    lon=slice(west, east),
                )
                datasets.append(ds)
            except Exception as exc:
                logger.warning("Failed to fetch %s %s %d: %s", gcm_model, var, year, exc)

            done += 1
            if on_progress:
                on_progress(int(100 * done / total), f"{var} {year}")

    if not datasets:
        return None

    return xr.merge(datasets)


def extract_site_deltas(
    dataset,  # xarray.Dataset from fetch_nex_gddp
    site_coords: list[tuple[float, float]],
    baseline_years: range | None = None,
) -> dict[str, dict[int, float]]:
    """Extract site-specific climate deltas from NEX-GDDP gridded data.

    For each site coordinate, finds the nearest grid cell and computes
    yearly anomalies relative to the baseline period.

    Parameters
    ----------
    dataset : xarray.Dataset
        NEX-GDDP-CMIP6 dataset with variables 'tas', 'rsds', 'sfcWind'.
    site_coords : list of (lat, lon)
        Coordinates of power system nodes.
    baseline_years : range, optional
        Historical baseline years (default: 1985-2014).

    Returns
    -------
    dict
        {"temperature_delta": {year: dT}, "ghi_delta_fraction": {year: frac},
         "wind_speed_delta_fraction": {year: frac}}
    """
    if baseline_years is None:
        baseline_years = range(1985, 2015)

    try:
        import xarray as xr
    except ImportError:
        logger.warning("xarray not installed — cannot extract site deltas")
        return {}

    result: dict[str, dict[int, float]] = {
        "temperature_delta": {},
        "ghi_delta_fraction": {},
        "wind_speed_delta_fraction": {},
    }

    # Average across all site coordinates (nearest grid cell)
    site_values: dict[str, list] = {"tas": [], "rsds": [], "sfcWind": []}
    for lat, lon in site_coords:
        # NEX-GDDP uses 0-360 longitude convention
        lon_360 = lon % 360
        for var in site_values:
            if var in dataset:
                try:
                    vals = dataset[var].sel(lat=lat, lon=lon_360, method="nearest")
                    site_values[var].append(vals)
                except Exception:
                    pass

    if not any(site_values.values()):
        return result

    # Compute baseline means and yearly anomalies
    for var, key_name, is_fraction in [
        ("tas", "temperature_delta", False),
        ("rsds", "ghi_delta_fraction", True),
        ("sfcWind", "wind_speed_delta_fraction", True),
    ]:
        if not site_values[var]:
            continue
        try:
            combined = xr.concat(site_values[var], dim="site").mean(dim="site")
            yearly = combined.groupby("time.year").mean()

            # Baseline mean
            baseline_mask = yearly.year.isin(list(baseline_years))
            if baseline_mask.any():
                baseline_mean = float(yearly.sel(year=baseline_mask).mean())
            else:
                baseline_mean = float(yearly.mean())

            if baseline_mean == 0:
                continue

            for yr in yearly.year.values:
                yr_val = float(yearly.sel(year=yr))
                if is_fraction:
                    result[key_name][int(yr)] = round((yr_val - baseline_mean) / baseline_mean, 4)
                else:
                    # tas is in Kelvin in CMIP6; delta is same in K and °C
                    result[key_name][int(yr)] = round(yr_val - baseline_mean, 2)
        except Exception as exc:
            logger.warning("Failed to extract %s deltas: %s", var, exc)

    return result


def synthesize_hourly_temperature(
    base_temp_mean: float,
    diurnal_range: float = 6.0,
    seasonal_range: float = 3.0,
    delta_t: float = 0.0,
    hours: int = 8760,
) -> np.ndarray:
    """Generate a synthetic hourly temperature profile.

    Combines sinusoidal diurnal and seasonal cycles.  Used as a fallback
    when no real temperature data is available (e.g. from NEX-GDDP).

    Parameters
    ----------
    base_temp_mean : float
        Annual mean temperature (°C).
    diurnal_range : float
        Peak-to-trough daily temperature swing (°C).  Typical: 5-8 for
        tropical SIDS, 10-15 for continental.
    seasonal_range : float
        Peak-to-trough seasonal temperature swing (°C).  Typical: 2-4 for
        tropics, 15-25 for continental.
    delta_t : float
        Climate warming offset added to the mean (°C).
    hours : int
        Number of hours in the profile (default 8760 = 1 year).

    Returns
    -------
    ndarray
        Hourly temperature in °C, shape ``(hours,)``.
    """
    t = np.arange(hours, dtype=float)
    # Seasonal cycle: coldest at hour ~1080 (mid-Feb NH), warmest ~5520 (mid-Jul)
    seasonal = (seasonal_range / 2) * np.cos(2 * np.pi * (t - 4380) / 8760)
    # Diurnal cycle: coldest ~5am (hour 5), warmest ~3pm (hour 15)
    diurnal = (diurnal_range / 2) * np.cos(2 * np.pi * (t % 24 - 15) / 24)
    return base_temp_mean + delta_t + seasonal + diurnal
