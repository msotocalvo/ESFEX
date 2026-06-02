# -*- coding: utf-8 -*-
"""OTEC Studio — site & resource engine (M6).

The site layer of the project: manage candidate sites, push one into the shared
``ResourceData`` (which auto-fills the Optimization / Economics / Operation
panels), apply CMIP6/SSP climate deltas to the design temperatures, and enrich
sites with OTEX's siting hazard layers.

Network boundary: the climate-delta and hazard-enrichment *fetches* require
network/credentials (CMEMS, siting layers) and are thin wrappers run off-thread
with graceful failure. The pure logic — site bookkeeping, ResourceData mapping,
and the delta-application math — is GUI- and network-free, and unit-tested.
"""

from __future__ import annotations

from typing import Any, Optional

from esfex.visualization.workflows.otec_studio.project import ResourceData

# CMIP6 emission scenarios OTEX supports (excluding the historical baseline).
SSP_SCENARIOS = ("ssp126", "ssp245", "ssp370", "ssp585")

# Columns added by enrich_sites (for display/labelling).
HAZARD_COLUMNS = ("in_mpa_strict", "ais_density_pct", "pga", "cyclone_freq_per_yr")


# ---------------------------------------------------------------------------
# Pure logic (testable, no network)
# ---------------------------------------------------------------------------


def make_site(
    name: str, longitude: float, latitude: float,
    t_ww: float, t_cw: float, dist_shore: float = 20.0,
) -> dict:
    """A plain site record used by the site table and ResourceData."""
    return {
        "name": name,
        "longitude": float(longitude),
        "latitude": float(latitude),
        "t_ww": float(t_ww),
        "t_cw": float(t_cw),
        "dist_shore": float(dist_shore),
    }


def site_to_resource(site: dict) -> ResourceData:
    """Map a site record into the project's shared ResourceData."""
    return ResourceData(
        name=site.get("name", ""),
        longitude=site.get("longitude"),
        latitude=site.get("latitude"),
        t_ww=site.get("t_ww"),
        t_cw=site.get("t_cw"),
    )


def apply_climate_delta(
    site: dict, delta_ww: float, delta_cw: float, label: str = "",
) -> dict:
    """Return a new site with the SSP temperature deltas applied.

    Warm- and cold-water deltas can differ (surface warms faster than the
    deep cold-water resource), so they are applied independently.
    """
    out = dict(site)
    out["t_ww"] = site["t_ww"] + float(delta_ww)
    out["t_cw"] = site["t_cw"] + float(delta_cw)
    if label:
        out["name"] = f"{site.get('name', 'site')} [{label}]"
    return out


def sites_dataframe(sites: list[dict]):
    """Sites as a pandas DataFrame (for enrich_sites / display)."""
    import pandas as pd

    rows = [
        {
            "site_id": i,
            "longitude": s["longitude"],
            "latitude": s["latitude"],
            **{k: s[k] for k in ("name", "t_ww", "t_cw", "dist_shore") if k in s},
        }
        for i, s in enumerate(sites)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Network-bound wrappers (run off-thread; not unit-tested against live network)
# ---------------------------------------------------------------------------


def fetch_climate_delta(
    scenario: str,
    target_year: int,
    longitude: float,
    latitude: float,
    depth_m: float,
    *,
    span_deg: float = 2.0,
) -> dict:
    """Fetch an ensemble SSP temperature delta at a point (NETWORK).

    Returns ``{delta_mean, delta_std, models}``. Raises on network/credential
    failure — callers should surface the error rather than silently zero it.
    """
    import numpy as np
    from otex.data.climate import BBox, delta_at_points, ensemble_delta

    bbox = BBox(
        north=latitude + span_deg, south=latitude - span_deg,
        east=longitude + span_deg, west=longitude - span_deg,
    )
    ens = ensemble_delta(scenario, int(target_year), float(depth_m), bbox)
    vals = delta_at_points(
        np.array([float(longitude)]), np.array([float(latitude)]), ens,
    )
    return {
        "delta_mean": float(np.ravel(vals)[0]),
        "models": getattr(ens, "models", None),
    }


def enrich_hazards(
    sites: list[dict],
    *,
    mpa_buffer_km: float = 5.0,
    ais_buffer_km: float = 5.0,
) -> Any:
    """Enrich sites with siting hazard layers (NETWORK) → DataFrame."""
    from otex.data.siting import enrich_sites

    df = sites_dataframe(sites)
    return enrich_sites(
        df, mpa_buffer_km=mpa_buffer_km, ais_buffer_km=ais_buffer_km,
    )
