"""OTEC computation models — pure functions, no Qt dependencies.

Provides:
- Ocean temperature characterization (ΔT, monthly profiles, Carnot efficiency)
- Daily capacity factor from temperature difference
- Hourly profile expansion (daily → 8760h)
- Cold water pipe (CWP) & transmission analysis

Financial/economics analysis is handled by the OTEX library directly
(capex_opex_lcoe, MonteCarloAnalysis, etc.).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import numpy as np


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class DailyOTECData:
    """Daily ocean temperature data for one grid cell."""

    timestamps: list[str]   # ISO date strings (n_days entries)
    t_warm: Any             # np.ndarray (n_days,) warm water °C
    t_cold: Any             # np.ndarray (n_days,) cold water °C


@dataclass
class PipeAnalysisResult:
    """Cold water pipe and transmission analysis result."""

    pipe_length_m: float = 0.0
    pipe_diameter_m: float = 0.0
    pumping_power_kw: float = 0.0
    pumping_fraction: float = 0.0
    net_power_after_pumping_kw: float = 0.0
    eff_trans: float = 0.0
    transmission_loss_kw: float = 0.0


# =====================================================================
# Ocean Temperature Characterization
# =====================================================================


def compute_monthly_delta_t(
    daily_data: DailyOTECData,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute monthly mean ΔT statistics.

    Returns (months[12], mean_dt[12], std_dt[12]).
    """
    t_warm = np.asarray(daily_data.t_warm, dtype=float)
    t_cold = np.asarray(daily_data.t_cold, dtype=float)
    delta_t = t_warm - t_cold

    # Parse months from timestamps
    months_arr = np.array([int(ts[5:7]) for ts in daily_data.timestamps])

    mean_dt = np.zeros(12)
    std_dt = np.zeros(12)
    for m in range(1, 13):
        mask = months_arr == m
        if np.any(mask):
            mean_dt[m - 1] = float(np.nanmean(delta_t[mask]))
            std_dt[m - 1] = float(np.nanstd(delta_t[mask]))

    return np.arange(1, 13), mean_dt, std_dt


def compute_monthly_temperatures(
    daily_data: DailyOTECData,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute monthly mean warm and cold water temperatures.

    Returns (months[12], mean_warm[12], mean_cold[12]).
    """
    t_warm = np.asarray(daily_data.t_warm, dtype=float)
    t_cold = np.asarray(daily_data.t_cold, dtype=float)

    months_arr = np.array([int(ts[5:7]) for ts in daily_data.timestamps])

    mean_warm = np.zeros(12)
    mean_cold = np.zeros(12)
    for m in range(1, 13):
        mask = months_arr == m
        if np.any(mask):
            mean_warm[m - 1] = float(np.nanmean(t_warm[mask]))
            mean_cold[m - 1] = float(np.nanmean(t_cold[mask]))

    return np.arange(1, 13), mean_warm, mean_cold


def compute_carnot_efficiency(t_warm: float, t_cold: float) -> float:
    """Compute Carnot efficiency η = 1 - T_cold_K / T_warm_K."""
    t_warm_k = t_warm + 273.15
    t_cold_k = t_cold + 273.15
    if t_warm_k <= 0:
        return 0.0
    return max(0.0, 1.0 - t_cold_k / t_warm_k)


# =====================================================================
# Capacity Factor from Temperature
# =====================================================================


def compute_daily_cf(
    daily_data: DailyOTECData,
    cf_nominal: float,
    delta_t_design: float,
) -> np.ndarray:
    """Compute daily capacity factor from temperature difference.

    CF scales linearly with ΔT relative to the design-point ΔT.
    Clipped to [0, 1.2 × cf_nominal] to allow slight over-performance
    on days warmer than design conditions.
    """
    t_warm = np.asarray(daily_data.t_warm, dtype=float)
    t_cold = np.asarray(daily_data.t_cold, dtype=float)
    delta_t = t_warm - t_cold

    if delta_t_design <= 0:
        return np.full(len(delta_t), cf_nominal)

    ratio = np.clip(delta_t / delta_t_design, 0.0, 1.2)
    return cf_nominal * ratio


def expand_daily_to_hourly(daily_cf: np.ndarray) -> np.ndarray:
    """Expand daily CF values to 8760 hourly values.

    Each daily value is repeated 24 times. If data has fewer than 365 days,
    the last value is padded; if more, trimmed to 365.
    """
    daily = np.asarray(daily_cf, dtype=float)
    daily = daily[:365]  # trim to max 365 days
    hourly = np.repeat(daily, 24)
    if len(hourly) < 8760:
        hourly = np.pad(
            hourly, (0, 8760 - len(hourly)),
            constant_values=float(hourly[-1]) if len(hourly) > 0 else 0.0,
        )
    return hourly[:8760]


# =====================================================================
# Cold Water Pipe & Transmission Analysis
# =====================================================================


def compute_pipe_analysis(
    depth_m: float,
    dist_shore_km: float,
    gross_power_kw: float,
    pipe_diameter_m: float = 10.0,
    slope_angle_deg: float = 7.0,
    pump_efficiency: float = 0.80,
    seawater_density: float = 1025.0,
    threshold_ac_dc_km: float = 50.0,
) -> PipeAnalysisResult:
    """Analyze cold water pipe sizing, pumping losses, and transmission.

    Parameters
    ----------
    depth_m : float
        Cold water intake depth (m).
    dist_shore_km : float
        Distance from plant to shore (km).
    gross_power_kw : float
        Plant gross power output (kW, positive).
    pipe_diameter_m : float
        CWP internal diameter (m).
    slope_angle_deg : float
        CWP slope angle from horizontal (°).
    pump_efficiency : float
        CWP pump efficiency (0-1).
    seawater_density : float
        Seawater density (kg/m³).
    threshold_ac_dc_km : float
        Distance threshold for AC vs DC transmission.
    """
    g = 9.81  # m/s²

    # Pipe length from depth and slope angle
    slope_rad = math.radians(max(slope_angle_deg, 1.0))
    pipe_length_m = depth_m / math.sin(slope_rad)

    # Flow rate estimate from thermodynamic cycle
    # Typical OTEC: ~3-5 m³/s per MW gross power (cold water flow)
    cw_flow_rate = abs(gross_power_kw) / 1000.0 * 4.0  # m³/s

    # Cross-section area and velocity
    area = math.pi * (pipe_diameter_m / 2.0) ** 2
    velocity = cw_flow_rate / area if area > 0 else 0.0

    # Darcy-Weisbach head loss
    # f ≈ 0.015 for large-diameter smooth pipe (Re >> 10^6)
    f_darcy = 0.015
    if pipe_diameter_m > 0 and velocity > 0:
        h_f = f_darcy * pipe_length_m * velocity ** 2 / (2.0 * pipe_diameter_m * g)
    else:
        h_f = 0.0

    # Pumping power
    pumping_power_kw = (
        seawater_density * g * cw_flow_rate * h_f / (pump_efficiency * 1000.0)
    )

    parasitic_fraction = (
        pumping_power_kw / abs(gross_power_kw)
        if gross_power_kw != 0 else 0.0
    )

    net_after_pump = abs(gross_power_kw) - pumping_power_kw

    # Transmission efficiency (same as OTEX formula)
    if dist_shore_km <= threshold_ac_dc_km:
        eff_trans = (
            0.979
            - 1e-6 * dist_shore_km ** 2
            - 9e-5 * dist_shore_km
        )
    else:
        eff_trans = 0.964 - 8e-5 * dist_shore_km
    eff_trans = max(eff_trans, 0.01)

    transmission_loss_kw = net_after_pump * (1.0 - eff_trans)

    return PipeAnalysisResult(
        pipe_length_m=pipe_length_m,
        pipe_diameter_m=pipe_diameter_m,
        pumping_power_kw=pumping_power_kw,
        pumping_fraction=parasitic_fraction,
        net_power_after_pumping_kw=net_after_pump,
        eff_trans=eff_trans,
        transmission_loss_kw=transmission_loss_kw,
    )


def compute_pipe_diameter_sweep(
    depth_m: float,
    dist_shore_km: float,
    gross_power_kw: float,
    diameters: list[float] | None = None,
    max_workers: int = 0,
) -> tuple[list[float], list[float]]:
    """Compute net power for a range of pipe diameters.

    Returns (diameters, net_powers_kw).
    """
    from concurrent.futures import ThreadPoolExecutor

    if diameters is None:
        diameters = [float(d) for d in np.linspace(4.0, 16.0, 30)]

    def _eval(d):
        r = compute_pipe_analysis(depth_m, dist_shore_km, gross_power_kw, d)
        return r.net_power_after_pumping_kw * r.eff_trans

    workers = max_workers if max_workers > 0 else (os.cpu_count() or 4)
    n_workers = min(workers, len(diameters))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        nets = list(pool.map(_eval, diameters))

    return diameters, nets
