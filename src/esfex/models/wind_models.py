"""Wind resource analysis models.

Pure computation functions for:
- Weibull distribution fitting and wind statistics
- Wind rose computation
- Wind shear (power-law) analysis
- Diurnal and seasonal temporal patterns
- Financial analysis (LCOE, NPV, IRR)
- Jensen/Park wake effect modeling
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Weibull Analysis
# ---------------------------------------------------------------------------


def fit_weibull(speeds: np.ndarray) -> tuple[float, float]:
    """Fit Weibull distribution to wind speed data using MLE.

    Returns (k, A) where k = shape parameter, A = scale parameter.
    """
    speeds = np.asarray(speeds, dtype=float)
    speeds = speeds[speeds > 0]
    if len(speeds) < 10:
        return 2.0, float(np.mean(speeds)) if len(speeds) > 0 else 6.0

    n = len(speeds)
    ln_v = np.log(speeds)

    # Iterative MLE (Newton-Raphson for k)
    k = 2.0  # initial guess
    for _ in range(50):
        v_k = speeds ** k
        ln_v_vk = ln_v * v_k
        sum_vk = np.sum(v_k)
        if sum_vk == 0:
            break
        k_new_inv = np.sum(ln_v_vk) / sum_vk - np.sum(ln_v) / n
        if abs(k_new_inv) < 1e-12:
            break
        k_new = 1.0 / k_new_inv
        if k_new <= 0 or not np.isfinite(k_new):
            break
        if abs(k_new - k) < 1e-6:
            k = k_new
            break
        k = k_new

    k = max(0.5, min(k, 10.0))  # clamp
    A = (np.sum(speeds ** k) / n) ** (1.0 / k)
    return float(k), float(A)


def weibull_pdf(x: np.ndarray, k: float, A: float) -> np.ndarray:
    """Weibull probability density function.

    f(v) = (k/A)(v/A)^(k-1) * exp(-(v/A)^k)
    """
    x = np.asarray(x, dtype=float)
    result = np.zeros_like(x)
    mask = x > 0
    xm = x[mask]
    result[mask] = (k / A) * (xm / A) ** (k - 1) * np.exp(-((xm / A) ** k))
    return result


def weibull_mean_power_density(k: float, A: float, rho: float = 1.225) -> float:
    """Mean wind power density (W/m^2) from Weibull parameters.

    P = 0.5 * rho * A^3 * Gamma(1 + 3/k)
    """
    from math import gamma

    return 0.5 * rho * A ** 3 * gamma(1.0 + 3.0 / k)


# ---------------------------------------------------------------------------
# Wind Rose
# ---------------------------------------------------------------------------


@dataclass
class WindRoseData:
    """Wind rose computation results."""

    sectors: np.ndarray  # sector center angles (degrees)
    frequencies: np.ndarray  # fraction of time in each sector
    mean_speeds: np.ndarray  # mean speed per sector (m/s)


def compute_wind_rose(
    speeds: np.ndarray,
    directions: np.ndarray,
    n_sectors: int = 16,
) -> WindRoseData:
    """Compute wind rose from speed and direction arrays."""
    speeds = np.asarray(speeds, dtype=float)
    directions = np.asarray(directions, dtype=float) % 360.0
    sector_width = 360.0 / n_sectors
    sector_centers = np.arange(n_sectors) * sector_width

    frequencies = np.zeros(n_sectors)
    mean_speeds = np.zeros(n_sectors)

    for i in range(n_sectors):
        center = sector_centers[i]
        lo = (center - sector_width / 2) % 360.0
        hi = (center + sector_width / 2) % 360.0
        if lo < hi:
            mask = (directions >= lo) & (directions < hi)
        else:  # wraps around 0/360
            mask = (directions >= lo) | (directions < hi)
        count = np.sum(mask)
        frequencies[i] = count
        if count > 0:
            mean_speeds[i] = np.mean(speeds[mask])

    total = np.sum(frequencies)
    if total > 0:
        frequencies /= total

    return WindRoseData(
        sectors=sector_centers,
        frequencies=frequencies,
        mean_speeds=mean_speeds,
    )


# ---------------------------------------------------------------------------
# Wind Shear
# ---------------------------------------------------------------------------


def compute_wind_shear(
    speeds_low: np.ndarray,
    speeds_high: np.ndarray,
    h_low: float,
    h_high: float,
) -> float:
    """Compute power-law wind shear exponent alpha.

    v2/v1 = (h2/h1)^alpha  →  alpha = ln(v2/v1) / ln(h2/h1)
    """
    speeds_low = np.asarray(speeds_low, dtype=float)
    speeds_high = np.asarray(speeds_high, dtype=float)

    # Filter valid pairs
    mask = (speeds_low > 0.5) & (speeds_high > 0.5)
    if np.sum(mask) < 5:
        return 0.143  # IEC default (open terrain)

    ratio = np.mean(speeds_high[mask]) / np.mean(speeds_low[mask])
    if ratio <= 0:
        return 0.143

    alpha = np.log(ratio) / np.log(h_high / h_low)
    return float(np.clip(alpha, 0.0, 0.6))


def extrapolate_speed(
    speed_ref: float, h_ref: float, h_target: float, alpha: float,
) -> float:
    """Extrapolate wind speed to different height using power law."""
    return speed_ref * (h_target / h_ref) ** alpha


# ---------------------------------------------------------------------------
# Temporal Patterns
# ---------------------------------------------------------------------------


def compute_diurnal_pattern(
    speeds: np.ndarray,
    timestamps: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Compute mean wind speed by hour of day.

    Returns (hours[0..23], mean_speeds[24]).
    """
    from datetime import datetime

    speeds = np.asarray(speeds, dtype=float)
    hours_of_day = np.zeros(len(timestamps), dtype=int)
    for i, ts in enumerate(timestamps):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hours_of_day[i] = dt.hour
        except (ValueError, AttributeError):
            hours_of_day[i] = i % 24

    hourly_means = np.zeros(24)
    for h in range(24):
        mask = hours_of_day == h
        if np.any(mask):
            hourly_means[h] = np.mean(speeds[mask])

    return np.arange(24), hourly_means


def compute_seasonal_pattern(
    speeds: np.ndarray,
    timestamps: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Compute mean wind speed by month.

    Returns (months[1..12], mean_speeds[12]).
    """
    from datetime import datetime

    speeds = np.asarray(speeds, dtype=float)
    months = np.zeros(len(timestamps), dtype=int)
    for i, ts in enumerate(timestamps):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            months[i] = dt.month
        except (ValueError, AttributeError):
            months[i] = (i // 730) % 12 + 1  # rough fallback

    monthly_means = np.zeros(12)
    for m in range(1, 13):
        mask = months == m
        if np.any(mask):
            monthly_means[m - 1] = np.mean(speeds[mask])

    return np.arange(1, 13), monthly_means


# ---------------------------------------------------------------------------
# Financial Analysis
# ---------------------------------------------------------------------------


@dataclass
class WindFinancialInputs:
    """Input parameters for wind project financial analysis."""

    capacity_mw: float = 10.0
    capacity_factor: float = 0.30
    capex_per_kw: float = 1300.0  # $/kW
    opex_per_kw_yr: float = 25.0  # $/kW/year
    discount_rate: float = 0.08  # 8%
    lifetime_years: int = 25
    electricity_price: float = 50.0  # $/MWh
    degradation_rate: float = 0.005  # 0.5%/year


@dataclass
class WindFinancialResults:
    """Output of wind project financial analysis."""

    lcoe: float = 0.0  # $/MWh
    npv: float = 0.0  # $
    irr: float = 0.0  # fraction
    annual_revenue: float = 0.0  # $/year (year 1)
    annual_opex: float = 0.0  # $/year
    payback_years: float = 0.0
    total_generation_mwh: float = 0.0  # lifetime total
    capex_total: float = 0.0  # $


def _crf(r: float, n: int) -> float:
    """Capital Recovery Factor: r(1+r)^n / ((1+r)^n - 1)."""
    if r <= 0:
        return 1.0 / n if n > 0 else 1.0
    factor = (1 + r) ** n
    return r * factor / (factor - 1)


def compute_wind_financials(inputs: WindFinancialInputs) -> WindFinancialResults:
    """Compute LCOE, NPV, IRR for a wind project."""
    cap_kw = inputs.capacity_mw * 1000.0
    capex_total = cap_kw * inputs.capex_per_kw
    annual_opex = cap_kw * inputs.opex_per_kw_yr

    # Annual generation (MWh), year 1
    annual_gen_yr1 = inputs.capacity_mw * inputs.capacity_factor * 8760.0
    annual_revenue_yr1 = annual_gen_yr1 * inputs.electricity_price

    # LCOE
    crf = _crf(inputs.discount_rate, inputs.lifetime_years)
    annual_capex_equiv = capex_total * crf
    lcoe = (annual_capex_equiv + annual_opex) / annual_gen_yr1 if annual_gen_yr1 > 0 else 0.0

    # NPV
    npv = -capex_total
    total_gen = 0.0
    cumulative_cf = 0.0
    payback_years = float(inputs.lifetime_years)
    payback_found = False

    for t in range(1, inputs.lifetime_years + 1):
        degradation_factor = (1 - inputs.degradation_rate) ** (t - 1)
        gen_t = annual_gen_yr1 * degradation_factor
        revenue_t = gen_t * inputs.electricity_price
        cash_flow_t = revenue_t - annual_opex
        npv += cash_flow_t / (1 + inputs.discount_rate) ** t
        total_gen += gen_t
        cumulative_cf += cash_flow_t
        if not payback_found and cumulative_cf >= capex_total:
            # Linear interpolation within this year
            prev_cum = cumulative_cf - cash_flow_t
            frac = (capex_total - prev_cum) / cash_flow_t if cash_flow_t > 0 else 1.0
            payback_years = t - 1 + frac
            payback_found = True

    # IRR via bisection
    irr = _compute_irr(capex_total, annual_gen_yr1, annual_opex,
                       inputs.electricity_price, inputs.degradation_rate,
                       inputs.lifetime_years)

    return WindFinancialResults(
        lcoe=lcoe,
        npv=npv,
        irr=irr,
        annual_revenue=annual_revenue_yr1,
        annual_opex=annual_opex,
        payback_years=payback_years,
        total_generation_mwh=total_gen,
        capex_total=capex_total,
    )


def _compute_irr(
    capex: float,
    annual_gen_yr1: float,
    annual_opex: float,
    price: float,
    degradation: float,
    lifetime: int,
) -> float:
    """Compute IRR via bisection."""

    def npv_at_rate(r: float) -> float:
        val = -capex
        for t in range(1, lifetime + 1):
            deg = (1 - degradation) ** (t - 1)
            cf = annual_gen_yr1 * deg * price - annual_opex
            val += cf / (1 + r) ** t
        return val

    lo, hi = -0.5, 2.0
    if npv_at_rate(lo) < 0:
        return lo
    if npv_at_rate(hi) > 0:
        return hi

    for _ in range(100):
        mid = (lo + hi) / 2.0
        if npv_at_rate(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2.0


def compute_lcoe_sensitivity(
    inputs: WindFinancialInputs,
    param_name: str,
    values: list[float],
    max_workers: int = 0,
) -> list[float]:
    """Compute LCOE for a range of values of one parameter.

    Uses ThreadPoolExecutor to evaluate sweep values in parallel.
    param_name must match a WindFinancialInputs field name.
    max_workers: 0 = auto (cpu_count).
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    def _eval(val):
        modified = WindFinancialInputs(
            capacity_mw=inputs.capacity_mw,
            capacity_factor=inputs.capacity_factor,
            capex_per_kw=inputs.capex_per_kw,
            opex_per_kw_yr=inputs.opex_per_kw_yr,
            discount_rate=inputs.discount_rate,
            lifetime_years=inputs.lifetime_years,
            electricity_price=inputs.electricity_price,
            degradation_rate=inputs.degradation_rate,
        )
        if hasattr(modified, param_name):
            setattr(modified, param_name, val)
        return compute_wind_financials(modified).lcoe

    workers = max_workers if max_workers > 0 else (os.cpu_count() or 4)
    n_workers = min(workers, len(values))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        return list(pool.map(_eval, values))


# ---------------------------------------------------------------------------
# Jensen / Park Wake Model
# ---------------------------------------------------------------------------


def jensen_wake_deficit(
    x_downstream: float,
    rotor_diameter: float,
    thrust_ct: float,
    wake_decay: float = 0.075,
) -> float:
    """Jensen single-wake velocity deficit.

    deltaV/V = (1 - sqrt(1 - Ct)) / (1 + 2*k*x/D)^2

    Returns fractional velocity deficit (0 to 1).
    """
    if x_downstream <= 0 or rotor_diameter <= 0:
        return 0.0

    a = 1.0 - np.sqrt(1.0 - thrust_ct)
    denom = (1.0 + 2.0 * wake_decay * x_downstream / rotor_diameter) ** 2
    deficit = a / denom
    return float(min(deficit, 1.0))


def compute_array_efficiency(
    n_turbines: int,
    spacing_diameters: float,
    rotor_diameter: float,
    thrust_ct: float,
    wind_rose: WindRoseData,
    wake_decay: float = 0.075,
) -> float:
    """Estimate array efficiency for a regular grid layout.

    Vectorized with numpy: pairwise displacement matrix computed once,
    then wake deficits for all turbine pairs evaluated per sector in bulk.

    Returns efficiency as fraction (0 to 1).
    """
    if n_turbines <= 1:
        return 1.0

    spacing_m = spacing_diameters * rotor_diameter
    n_side = max(1, int(np.ceil(np.sqrt(n_turbines))))

    # Generate grid positions
    positions = []
    for row in range(n_side):
        for col in range(n_side):
            if len(positions) >= n_turbines:
                break
            positions.append((col * spacing_m, row * spacing_m))
        if len(positions) >= n_turbines:
            break

    pos = np.array(positions)  # (n, 2)
    n = len(pos)

    # Pairwise displacement: dx[i,j] = pos[i] - pos[j]
    dx = pos[:, None, 0] - pos[None, :, 0]  # (n, n)
    dy = pos[:, None, 1] - pos[None, :, 1]  # (n, n)

    # Self-mask: exclude i==j
    not_self = ~np.eye(n, dtype=bool)

    # Jensen wake parameter
    a = 1.0 - np.sqrt(1.0 - thrust_ct)
    D = rotor_diameter

    total_efficiency = 0.0
    total_weight = 0.0

    for sector_idx in range(len(wind_rose.sectors)):
        freq = wind_rose.frequencies[sector_idx]
        if freq < 1e-6:
            continue

        wind_dir_rad = np.radians(wind_rose.sectors[sector_idx])
        wx = np.sin(wind_dir_rad)
        wy = np.cos(wind_dir_rad)

        # Downstream distance for each (i, j) pair
        downstream = dx * wx + dy * wy  # (n, n)
        lateral = np.abs(-dx * wy + dy * wx)  # (n, n)
        wake_radius = D / 2.0 + wake_decay * downstream  # (n, n)

        # Mask: j is upwind of i, inside wake cone, not self
        mask = not_self & (downstream > 0) & (lateral < wake_radius)

        # Jensen deficit: a / (1 + 2*k*x/D)^2, clamped to [0, 1]
        denom = np.where(mask, (1.0 + 2.0 * wake_decay * downstream / D) ** 2, 1.0)
        deficit = np.where(mask, np.minimum(a / denom, 1.0), 0.0)

        # RSS superposition per turbine: sqrt(sum of deficit^2 from all upwind)
        deficit_sq_sum = np.sum(deficit ** 2, axis=1)  # (n,)
        speed_ratio = np.maximum(0.0, 1.0 - np.sqrt(deficit_sq_sum))
        sector_power = np.mean(speed_ratio ** 3)

        total_efficiency += freq * sector_power
        total_weight += freq

    if total_weight > 0:
        return float(total_efficiency / total_weight)
    return 1.0


def compute_spacing_curve(
    rotor_diameter: float,
    thrust_ct: float,
    wind_rose: WindRoseData,
    spacings: list[float] | None = None,
    n_turbines: int = 25,
    wake_decay: float = 0.075,
    max_workers: int = 0,
) -> tuple[list[float], list[float]]:
    """Compute array efficiency vs turbine spacing.

    Uses ThreadPoolExecutor to evaluate spacings in parallel.
    Returns (spacings_D, efficiencies) where spacings are in rotor diameters.
    max_workers: 0 = auto (cpu_count).
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    if spacings is None:
        spacings = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]

    def _eval(sp):
        return compute_array_efficiency(
            n_turbines, sp, rotor_diameter, thrust_ct, wind_rose, wake_decay,
        )

    workers = max_workers if max_workers > 0 else (os.cpu_count() or 4)
    n_workers = min(workers, len(spacings))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        efficiencies = list(pool.map(_eval, spacings))

    return list(spacings), efficiencies
