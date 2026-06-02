# -*- coding: utf-8 -*-
"""OTEC Studio — engineering & resource features ported from OTEC Analysis (M8).

Thin, GUI-independent wrappers over the reusable, already-headless functions in
``esfex.models.otec_models`` (cold-water pipe sizing, transmission, Carnot
benchmark, seasonal characterization, annual capacity-factor profile) plus a
development-zone clustering adapter for the Regional panel.

These complement the OTEX-based panels: the pipe model surfaces the CWP
diameter / pumping-parasitic tradeoff the optimizer does not expose; Carnot
gives the theoretical efficiency ceiling; the 8760-h profile is the annual
operating curve other tools consume; zones turn per-site regional results into
deployable clusters.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from esfex.models.otec_models import (
    DailyOTECData,
    PipeAnalysisResult,
    compute_carnot_efficiency,
    compute_daily_cf,
    compute_monthly_delta_t,
    compute_monthly_temperatures,
    compute_pipe_analysis,
    compute_pipe_diameter_sweep,
    expand_daily_to_hourly,
)

# Re-export the pure functions so panels import from one place.
__all__ = [
    "PipeAnalysisResult",
    "carnot_efficiency",
    "pipe_analysis",
    "pipe_diameter_sweep",
    "synthetic_daily",
    "monthly_characterization",
    "annual_cf_profile",
    "zones_from_regional",
]


def carnot_efficiency(t_warm: float, t_cold: float) -> float:
    """Theoretical Carnot ceiling η = 1 − T_cold_K / T_warm_K."""
    return compute_carnot_efficiency(t_warm, t_cold)


def pipe_analysis(
    depth_m: float, dist_shore_km: float, gross_power_kw: float,
    pipe_diameter_m: float = 10.0, slope_angle_deg: float = 7.0,
) -> PipeAnalysisResult:
    """CWP sizing, pumping loss, parasitic fraction, transmission efficiency."""
    return compute_pipe_analysis(
        depth_m=depth_m, dist_shore_km=dist_shore_km,
        gross_power_kw=abs(gross_power_kw), pipe_diameter_m=pipe_diameter_m,
        slope_angle_deg=slope_angle_deg,
    )


def pipe_diameter_sweep(
    depth_m: float, dist_shore_km: float, gross_power_kw: float,
    diameters: list[float] | None = None,
) -> dict:
    """Net delivered power (after pumping × transmission) vs CWP diameter.

    Returns ``{diameters, net_kw, best_diameter, best_net_kw}``.
    """
    ds, nets = compute_pipe_diameter_sweep(
        depth_m, dist_shore_km, abs(gross_power_kw), diameters=diameters,
        max_workers=1,  # single-threaded: the worker already offloads the GUI
    )
    best_i = int(np.argmax(nets)) if nets else 0
    return {
        "diameters": list(ds),
        "net_kw": list(nets),
        "best_diameter": float(ds[best_i]) if ds else 0.0,
        "best_net_kw": float(nets[best_i]) if nets else 0.0,
    }


def synthetic_daily(
    t_ww_mean: float, t_cw_mean: float,
    ww_amp: float = 2.0, cw_amp: float = 0.5, n_days: int = 365,
) -> DailyOTECData:
    """A synthetic year of daily warm/cold temperatures (seasonal sinusoid).

    Used for offline characterization / annual-CF when no real CMEMS daily
    series is attached to the resource. Replace with real ``daily_data`` when
    the Site & Resource panel has downloaded it.
    """
    days = np.arange(n_days)
    # peak warm-season near day ~210 (NH summer); cold resource nearly steady
    t_ww = t_ww_mean + ww_amp * np.sin(2 * np.pi * (days - 120) / 365.0)
    t_cw = t_cw_mean + cw_amp * np.sin(2 * np.pi * (days - 120) / 365.0)
    timestamps = []
    months = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    d = 0
    for mi, dim in enumerate(months, start=1):
        for day in range(1, dim + 1):
            if d >= n_days:
                break
            timestamps.append(f"2020-{mi:02d}-{day:02d}")
            d += 1
    while len(timestamps) < n_days:
        timestamps.append("2020-12-31")
    return DailyOTECData(
        timestamps=timestamps[:n_days], t_warm=t_ww, t_cold=t_cw,
    )


def monthly_characterization(daily: DailyOTECData) -> dict:
    """Monthly ΔT and warm/cold temperature patterns + Carnot at mean point."""
    months, mean_dt, std_dt = compute_monthly_delta_t(daily)
    _m, mean_warm, mean_cold = compute_monthly_temperatures(daily)
    mw = float(np.nanmean(mean_warm))
    mc = float(np.nanmean(mean_cold))
    return {
        "months": months,
        "mean_dt": mean_dt, "std_dt": std_dt,
        "mean_warm": mean_warm, "mean_cold": mean_cold,
        "carnot_mean": carnot_efficiency(mw, mc),
        "dt_min": float(np.nanmin(mean_dt)),
        "dt_max": float(np.nanmax(mean_dt)),
    }


def annual_cf_profile(
    daily: DailyOTECData, cf_nominal: float, delta_t_design: float,
) -> dict:
    """8760-hour capacity-factor profile from a daily temperature series."""
    daily_cf = compute_daily_cf(daily, cf_nominal, delta_t_design)
    hourly = expand_daily_to_hourly(daily_cf)
    return {
        "daily_cf": daily_cf,
        "hourly_cf": hourly,
        "annual_mean_cf": float(np.mean(hourly)),
        "annual_energy_fraction": float(np.mean(hourly)),  # vs nameplate-year
    }


# ---------------------------------------------------------------------------
# Development zones (Regional) — adapts a regional result frame to the
# GeoDataFrame the clustering routine expects.
# ---------------------------------------------------------------------------


def zones_from_regional(
    df,
    lcoe_threshold: float,
    buffer_km: float = 10.0,
    grid_resolution_deg: float = 0.25,
    installation_type: str = "offshore",
) -> Any:
    """Cluster feasible regional sites into development zones (DBSCAN).

    Adapts the regional result frame (``longitude``/``latitude``/``lcoe_min``/
    ``feasible``/``p_net_kW``) to the GeoDataFrame schema the wizard's
    ``generate_development_zones`` consumes (``geometry``/``lcoe``/``feasible``/
    ``capacity_mw``), then delegates to it.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    from esfex.visualization.workflows.otec_studio.zones import (
        generate_development_zones,
    )

    work = df.copy()
    if "lcoe" not in work.columns and "lcoe_min" in work.columns:
        work["lcoe"] = work["lcoe_min"]
    if "feasible" not in work.columns:
        work["feasible"] = True
    # generate_development_zones reads "net_power" (kW; it /1000 → MW capacity)
    if "net_power" not in work.columns and "p_net_kW" in work.columns:
        work["net_power"] = work["p_net_kW"].abs()
    geom = [Point(lo, la) for lo, la in zip(work["longitude"], work["latitude"])]
    gdf = gpd.GeoDataFrame(work, geometry=geom, crs="EPSG:4326")
    return generate_development_zones(
        gdf, lcoe_threshold=lcoe_threshold, buffer_km=buffer_km,
        grid_resolution_deg=grid_resolution_deg,
        installation_type=installation_type,
    )
