"""Solar PV computation models — pure functions, no Qt dependencies.

Provides:
- Solar resource characterization (clearness index, peak sun hours, diurnal/monthly)
- Temperature analysis (NOCT cell temp, derating)
- Financial analysis (LCOE, NPV, IRR, sensitivity)
- Array analysis (GCR shading loss, bifacial gain, spacing optimization)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class HourlyIrradianceData:
    """Hourly solar irradiance and temperature for one grid cell."""

    timestamps: list[str]     # ISO-8601 hourly timestamps
    ghi: Any                  # np.ndarray W/m²
    temperature: Any          # np.ndarray °C


@dataclass
class SolarFinancialInputs:
    """Input parameters for solar PV financial analysis."""

    capacity_mw: float = 10.0
    capacity_factor: float = 0.20
    capex_per_kw: float = 1000.0      # $/kW
    opex_per_kw_yr: float = 15.0      # $/kW/year
    discount_rate: float = 0.08
    lifetime_years: int = 25
    electricity_price: float = 50.0    # $/MWh
    degradation_rate: float = 0.005    # /year


@dataclass
class SolarFinancialResults:
    """Output of solar PV financial analysis."""

    lcoe: float = 0.0             # $/MWh
    npv: float = 0.0              # $
    irr: float = 0.0              # fraction
    payback_years: float = 0.0
    annual_revenue: float = 0.0   # $/year (year 1)
    annual_opex: float = 0.0      # $/year
    total_generation_mwh: float = 0.0  # lifetime
    capex_total: float = 0.0      # $


# =====================================================================
# Solar Resource Characterization
# =====================================================================


def compute_peak_sun_hours(ghi_hourly: np.ndarray) -> float:
    """Compute peak sun hours (PSH) from hourly GHI.

    PSH = total irradiation (Wh/m²) / 1000 W/m² expressed as hours/day.
    """
    ghi = np.asarray(ghi_hourly, dtype=float)
    total_wh = float(np.nansum(ghi))  # each element is W/m² × 1h
    n_days = max(len(ghi) / 24.0, 1.0)
    return total_wh / 1000.0 / n_days


def compute_performance_ratio(
    ghi_hourly: np.ndarray,
    temp_hourly: np.ndarray,
    efficiency: float,
    gamma_pmax: float,
    t_noct: float,
) -> float:
    """Compute performance ratio (PR) = actual yield / reference yield.

    Reference yield = GHI / G_STC (1000 W/m²).
    Actual yield includes temperature derating via NOCT model.
    """
    ghi = np.asarray(ghi_hourly, dtype=float)
    temp = np.asarray(temp_hourly, dtype=float)

    mask = ghi > 0
    if not np.any(mask):
        return 0.0

    ghi_pos = ghi[mask]
    temp_pos = temp[mask] if len(temp) == len(ghi) else np.full(mask.sum(), 25.0)

    # Cell temperature (NOCT model)
    t_cell = temp_pos + (t_noct - 20.0) / 800.0 * ghi_pos

    # Temperature derating
    temp_factor = 1.0 + (gamma_pmax / 100.0) * (t_cell - 25.0)
    temp_factor = np.clip(temp_factor, 0.0, 1.5)

    # PR = mean(temp_factor) for hours with sunlight
    return float(np.mean(temp_factor))


def compute_clearness_index(
    ghi_hourly: np.ndarray,
    latitude: float,
    timestamps: list[str],
) -> float:
    """Compute clearness index Kt = GHI / extraterrestrial irradiance.

    Uses daily totals averaged over the year.
    """
    from datetime import datetime

    ghi = np.asarray(ghi_hourly, dtype=float)
    if len(ghi) == 0 or len(timestamps) == 0:
        return 0.0

    G_sc = 1361.0  # Solar constant W/m²
    lat_rad = math.radians(latitude)

    daily_ghi = {}
    daily_et = {}

    for i, ts in enumerate(timestamps):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        doy = dt.timetuple().tm_yday
        hour = dt.hour + dt.minute / 60.0
        day_key = (dt.year, doy)

        # Extraterrestrial irradiance for this hour
        # Declination (Cooper's equation)
        decl = 23.45 * math.sin(math.radians(360 * (284 + doy) / 365))
        decl_rad = math.radians(decl)

        # Hour angle
        omega = math.radians(15.0 * (hour - 12.0))

        # Solar zenith angle
        cos_z = (
            math.sin(lat_rad) * math.sin(decl_rad)
            + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(omega)
        )

        # Eccentricity correction
        E0 = 1.0 + 0.033 * math.cos(math.radians(360 * doy / 365))

        et_irrad = max(0.0, G_sc * E0 * cos_z)

        daily_ghi.setdefault(day_key, 0.0)
        daily_et.setdefault(day_key, 0.0)
        if i < len(ghi):
            daily_ghi[day_key] += max(0.0, float(ghi[i]))
        daily_et[day_key] += et_irrad

    total_ghi = sum(daily_ghi.values())
    total_et = sum(daily_et.values())

    if total_et <= 0:
        return 0.0
    return min(1.0, total_ghi / total_et)


def compute_diurnal_irradiance(
    ghi_hourly: np.ndarray,
    timestamps: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Compute mean hourly GHI pattern over 24 hours.

    Returns (hours[0..23], mean_ghi[24]) in W/m².
    """
    from datetime import datetime

    ghi = np.asarray(ghi_hourly, dtype=float)
    hours = np.arange(24)
    sums = np.zeros(24)
    counts = np.zeros(24)

    for i, ts in enumerate(timestamps):
        if i >= len(ghi):
            break
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        h = dt.hour
        sums[h] += ghi[i]
        counts[h] += 1

    means = np.where(counts > 0, sums / counts, 0.0)
    return hours, means


def compute_monthly_irradiance(
    ghi_hourly: np.ndarray,
    timestamps: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Compute monthly total irradiance.

    Returns (months[1..12], totals[12]) in kWh/m²/month.
    """
    from datetime import datetime

    ghi = np.asarray(ghi_hourly, dtype=float)
    months = np.arange(1, 13)
    totals = np.zeros(12)

    for i, ts in enumerate(timestamps):
        if i >= len(ghi):
            break
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        m = dt.month - 1  # 0-indexed
        totals[m] += max(0.0, ghi[i]) / 1000.0  # W·h → kWh

    return months, totals


def compute_temp_analysis(
    ghi_hourly: np.ndarray,
    temp_ambient: np.ndarray,
    t_noct: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute cell temperature and derating factor arrays.

    Returns (cell_temp_c, derating_factor) arrays.
    """
    ghi = np.asarray(ghi_hourly, dtype=float)
    temp = np.asarray(temp_ambient, dtype=float)

    t_cell = temp + (t_noct - 20.0) / 800.0 * ghi
    derating = 1.0 + (-0.40 / 100.0) * (t_cell - 25.0)  # typical gamma
    derating = np.clip(derating, 0.0, 1.5)

    return t_cell, derating


# =====================================================================
# Financial Analysis
# =====================================================================


def _crf(rate: float, years: int) -> float:
    """Capital Recovery Factor."""
    if rate <= 0:
        return 1.0 / max(years, 1)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def _compute_irr(cash_flows: list[float], tol: float = 1e-6) -> float:
    """Internal Rate of Return via bisection."""
    lo, hi = -0.5, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        npv = sum(cf / (1 + mid) ** t for t, cf in enumerate(cash_flows))
        if abs(npv) < tol:
            return mid
        if npv > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def compute_pv_financials(
    inputs: SolarFinancialInputs,
) -> SolarFinancialResults:
    """Compute LCOE, NPV, IRR, and payback for a solar PV project."""
    cap_kw = inputs.capacity_mw * 1000.0
    capex_total = cap_kw * inputs.capex_per_kw
    annual_opex = cap_kw * inputs.opex_per_kw_yr

    # Year-1 generation
    annual_gen_yr1 = inputs.capacity_mw * inputs.capacity_factor * 8760.0  # MWh

    # LCOE via CRF
    crf = _crf(inputs.discount_rate, inputs.lifetime_years)
    annual_capex_equiv = capex_total * crf
    lcoe = (
        (annual_capex_equiv + annual_opex) / annual_gen_yr1
        if annual_gen_yr1 > 0
        else float("inf")
    )

    # NPV + total generation + payback
    total_gen = 0.0
    cumulative_cf = -capex_total
    payback = float(inputs.lifetime_years)
    payback_found = False

    cash_flows = [-capex_total]
    for t in range(1, inputs.lifetime_years + 1):
        deg = (1.0 - inputs.degradation_rate) ** (t - 1)
        gen_t = annual_gen_yr1 * deg
        total_gen += gen_t
        revenue_t = gen_t * inputs.electricity_price
        cf_t = revenue_t - annual_opex
        cash_flows.append(cf_t)

        cumulative_cf += cf_t
        if not payback_found and cumulative_cf >= 0:
            # Linear interpolation
            prev = cumulative_cf - cf_t
            frac = -prev / cf_t if cf_t != 0 else 0
            payback = (t - 1) + frac
            payback_found = True

    npv = sum(
        cf / (1 + inputs.discount_rate) ** t
        for t, cf in enumerate(cash_flows)
    )

    irr = _compute_irr(cash_flows) if len(cash_flows) > 1 else 0.0

    year1_revenue = annual_gen_yr1 * inputs.electricity_price

    return SolarFinancialResults(
        lcoe=lcoe,
        npv=npv,
        irr=irr,
        payback_years=payback,
        annual_revenue=year1_revenue,
        annual_opex=annual_opex,
        total_generation_mwh=total_gen,
        capex_total=capex_total,
    )


def compute_pv_lcoe_sensitivity(
    inputs: SolarFinancialInputs,
    param_name: str,
    values: list[float],
    max_workers: int = 0,
) -> list[float]:
    """Sweep a parameter and compute LCOE for each value.

    Uses ThreadPoolExecutor for parallelization.
    """
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict

    def _eval(val):
        d = asdict(inputs)
        d[param_name] = val
        modified = SolarFinancialInputs(**d)
        return compute_pv_financials(modified).lcoe

    workers = max_workers if max_workers > 0 else (os.cpu_count() or 4)
    n_workers = min(workers, len(values))

    if n_workers <= 1 or len(values) <= 3:
        return [_eval(v) for v in values]

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        return list(pool.map(_eval, values))


# =====================================================================
# Array / Shading Analysis
# =====================================================================


def compute_gcr_shading_loss(
    latitude: float,
    tilt: float,
    gcr: float,
    module_height: float = 2.0,
) -> float:
    """Estimate inter-row shading loss fraction for a ground-mount array.

    Uses winter solstice solar geometry for worst-case analysis.

    Parameters
    ----------
    latitude : float
        Site latitude in degrees.
    tilt : float
        Module tilt angle in degrees from horizontal.
    gcr : float
        Ground Coverage Ratio (0-1). GCR = module_width / row_pitch.
    module_height : float
        Module height (collector width) in meters.

    Returns
    -------
    float
        Shading loss fraction (0-1). 0 = no shading, 1 = fully shaded.
    """
    if gcr <= 0 or gcr > 1:
        return 0.0

    tilt_rad = math.radians(tilt)
    lat_rad = math.radians(abs(latitude))

    # Winter solstice declination (-23.45° for NH, +23.45° for SH)
    decl_rad = math.radians(-23.45) if latitude >= 0 else math.radians(23.45)

    # Solar altitude at noon on winter solstice
    sin_alt = (
        math.sin(lat_rad) * math.sin(decl_rad)
        + math.cos(lat_rad) * math.cos(decl_rad)
    )
    solar_alt = math.asin(max(-1.0, min(1.0, sin_alt)))

    if solar_alt <= 0:
        # Sun doesn't rise — full shading
        return 1.0

    # Shadow length cast by back edge of module
    module_top_height = module_height * math.sin(tilt_rad)
    shadow_length = module_top_height / math.tan(solar_alt)

    # Row pitch from GCR
    row_pitch = module_height / gcr if gcr > 0 else float("inf")

    # Module horizontal footprint
    module_footprint = module_height * math.cos(tilt_rad)

    # Available gap between rows
    gap = row_pitch - module_footprint

    if gap <= 0:
        return min(1.0, shadow_length / module_footprint) if module_footprint > 0 else 0.0

    # Shading occurs when shadow exceeds the gap
    if shadow_length <= gap:
        return 0.0

    shaded_portion = shadow_length - gap
    shading_loss = min(1.0, shaded_portion / module_footprint) if module_footprint > 0 else 0.0

    # Weight by approximate fraction of day affected
    # (shading worst at low sun angles; ~30% of daily energy at risk)
    weighted_loss = shading_loss * 0.30

    return min(1.0, weighted_loss)


def compute_gcr_curve(
    latitude: float,
    tilt: float,
    gcrs: list[float] | None = None,
    module_height: float = 2.0,
    max_workers: int = 0,
) -> tuple[list[float], list[float]]:
    """Compute shading loss vs GCR curve.

    Returns (gcr_values, loss_fractions).
    """
    from concurrent.futures import ThreadPoolExecutor

    if gcrs is None:
        gcrs = [round(0.15 + 0.05 * i, 2) for i in range(15)]  # 0.15 to 0.85

    def _eval(g):
        return compute_gcr_shading_loss(latitude, tilt, g, module_height)

    workers = max_workers if max_workers > 0 else (os.cpu_count() or 4)
    n_workers = min(workers, len(gcrs))

    if n_workers <= 1 or len(gcrs) <= 3:
        losses = [_eval(g) for g in gcrs]
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            losses = list(pool.map(_eval, gcrs))

    return gcrs, losses


def compute_bifacial_gain(
    albedo: float,
    gcr: float,
    module_height: float,
    tilt: float,
    bifaciality: float = 0.70,
) -> float:
    """Estimate bifacial energy gain fraction.

    Uses simplified view-factor model for rear-side irradiance.

    Parameters
    ----------
    albedo : float
        Ground albedo (0-1). Typical: 0.25 grass, 0.60 sand, 0.80 snow.
    gcr : float
        Ground Coverage Ratio.
    module_height : float
        Module height in meters.
    tilt : float
        Module tilt in degrees.
    bifaciality : float
        Module bifaciality factor (typical 0.65-0.80).

    Returns
    -------
    float
        Bifacial gain fraction (e.g. 0.10 = 10% more energy).
    """
    if albedo <= 0 or gcr <= 0 or bifaciality <= 0:
        return 0.0

    tilt_rad = math.radians(tilt)

    # Clearance height (bottom of module above ground)
    clearance = max(0.3, module_height * math.sin(tilt_rad) * 0.3)

    # View factor from ground to rear of module (simplified)
    # Higher clearance and lower GCR → more ground visible → higher view factor
    row_pitch = module_height / gcr if gcr > 0 else 10.0
    module_footprint = module_height * math.cos(tilt_rad)
    open_fraction = max(0.0, 1.0 - module_footprint / row_pitch)

    # View factor approximation (0-1)
    vf = open_fraction * min(1.0, clearance / module_height)

    # Rear irradiance fraction = albedo × view_factor
    rear_fraction = albedo * vf

    # Bifacial gain
    gain = rear_fraction * bifaciality

    return min(0.50, gain)  # Cap at 50% gain
