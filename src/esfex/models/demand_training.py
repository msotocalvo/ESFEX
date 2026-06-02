"""Unified training pipeline for demand estimation models.

Builds a single hourly dataset from real demand data (Parquet files),
trains XGBoost or TFT using temporal cross-validation, and reports
multi-resolution validation metrics.

No synthetic data — models learn exclusively from real demand.

Usage:
    esfex train-demand-model [--engine xgboost|tft]
"""

from __future__ import annotations

import datetime
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from esfex.paths import DEMAND_DATASET_DIR, MODELS_DIR

logger = logging.getLogger(__name__)

# Feature names for the unified hourly dataset (pixel-aware since 2026-04-16,
# pruned after multicollinearity audit 2026-04-18).
# Static: log_gdp_per_cap, log_pop_density, urbanization come from
# pixel-sampled rasters (GHSL SMOD, GDP 0.25°, GPW v4 population density).
# Dropped features (2026-04-18):
#   - elec_access (HREA): only 10% of samples have it; always 1.0 where present.
#   - utci/utci_hdd/utci_cdd: Pearson r ≥ 0.91 with temperature/hdd/cdd
#     (VIF > 700); the marginal thermal info UTCI carries beyond temperature
#     is too small to justify the redundancy.
FEATURE_COLS = [
    "country_id",
    "log_gdp_per_cap", "log_pop_density", "urbanization",
    "temperature", "hdd", "cdd",
    "hour_of_day", "month", "day_of_week",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "month_sin", "month_cos", "is_weekend",
    "is_holiday", "days_to_next_holiday", "days_from_prev_holiday",
    "latitude", "longitude",
    # Temperature lag/memory features (daily-resolution, broadcast to hours).
    # Training: computed from ERA5 hourly.
    # Inference (ECVI): precomputed from CMIP6 3hr subdaily data.
    "temp_1d_lag", "temp_7d_mean", "temp_30d_mean", "temp_trend_7d",
    "temp_daily_max", "temp_daily_min", "temp_diurnal_range",
    # Demand-shape autoregressive lags: DISABLED for ECVI-compatible model
    # (no past demand available at grid pixels). The 32-feat parity variant
    # ("demand_model_ar.xgb") keeps them for TFT comparison only.
    # "shape_lag_1h", "shape_lag_24h", "shape_lag_168h",
]
TARGET_COL = "shape_factor"


# ── Holiday features ────────────────────────────────────────────────────────

_ISO3_TO_ISO2_FALLBACK = {"BGR": "BG", "GTM": "GT", "MAR": "MA", "XKX": "XK"}


def _holiday_features(iso3: str, year: int, hours: int = 8760,
                      clip_days: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute hourly holiday features for (iso3, year).

    Returns
    -------
    is_holiday : (hours,) float array of 0/1
    days_to_next_holiday : (hours,) float array, clipped at ``clip_days``
    days_from_prev_holiday : (hours,) float array, clipped at ``clip_days``

    If the country is not supported by the ``holidays`` package, returns zeros
    for is_holiday and ``clip_days`` for both distance arrays.
    """
    try:
        import holidays as _holidays
    except ImportError:
        return (np.zeros(hours, dtype=np.float32),
                np.full(hours, clip_days, dtype=np.float32),
                np.full(hours, clip_days, dtype=np.float32))

    # Use an extended 3-year window so days_to/from distances see holidays
    # that fall just before Jan 1 or just after Dec 31 of `year`.
    hol = None
    for code in (iso3, _ISO3_TO_ISO2_FALLBACK.get(iso3)):
        if code is None:
            continue
        try:
            hol = _holidays.country_holidays(
                code, years=[year - 1, year, year + 1])
            break
        except (NotImplementedError, KeyError, AttributeError, TypeError):
            continue

    n_days = hours // 24
    jan1 = datetime.date(year, 1, 1)
    is_hday_day = np.zeros(n_days, dtype=np.float32)
    hol_offsets: list[int] = []
    if hol is not None:
        for d in hol.keys():
            offset = (d - jan1).days
            hol_offsets.append(offset)
            if 0 <= offset < n_days:
                is_hday_day[offset] = 1.0

    days_arr = np.arange(n_days)
    if hol_offsets:
        hol_offsets_arr = np.array(sorted(set(hol_offsets)))
        diffs = hol_offsets_arr[None, :] - days_arr[:, None]
        future = np.where(diffs >= 0, diffs, 10_000)
        past = np.where(diffs <= 0, -diffs, 10_000)
        days_to_next = future.min(axis=1).astype(np.float32)
        days_from_prev = past.min(axis=1).astype(np.float32)
    else:
        days_to_next = np.full(n_days, clip_days, dtype=np.float32)
        days_from_prev = np.full(n_days, clip_days, dtype=np.float32)

    days_to_next = np.minimum(days_to_next, float(clip_days))
    days_from_prev = np.minimum(days_from_prev, float(clip_days))

    return (np.repeat(is_hday_day, 24),
            np.repeat(days_to_next, 24),
            np.repeat(days_from_prev, 24))


# ── Unified dataset builder ─────────────────────────────────────────────────


def _cyclical(values: np.ndarray, period: float):
    angle = 2.0 * math.pi * values / period
    return np.sin(angle), np.cos(angle)


def _temperature_lag_features(temp_hourly: np.ndarray) -> dict[str, np.ndarray]:
    """Compute daily-resolution lag/memory features broadcast to hourly.

    Parameters
    ----------
    temp_hourly : (n,) array
        Hourly temperature in °C (typically n=8760).

    Returns dict with arrays of same length as ``temp_hourly``. Features live
    at daily resolution (constant within each 24h block) so they match the
    CMIP6 3hr-derived features used at ECVI inference time.
    """
    n = len(temp_hourly)
    n_days = (n + 23) // 24
    # Reshape to (days, 24) padding the last day if needed
    pad = n_days * 24 - n
    t = np.concatenate([temp_hourly, np.full(pad, np.nan)])
    t_daily = t.reshape(n_days, 24)

    daily_mean = np.nanmean(t_daily, axis=1)
    daily_max = np.nanmax(t_daily, axis=1)
    daily_min = np.nanmin(t_daily, axis=1)

    # Expanding-window rolling means (first (window-1) days use available data)
    def _rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
        c = np.cumsum(np.nan_to_num(x, nan=0.0), dtype=np.float64)
        out = np.empty_like(x)
        for d in range(len(x)):
            lo = max(0, d - window + 1)
            out[d] = (c[d] - (c[lo - 1] if lo > 0 else 0.0)) / (d - lo + 1)
        return out

    temp_7d = _rolling_mean(daily_mean, 7)
    temp_30d = _rolling_mean(daily_mean, 30)

    lag_1d = np.empty_like(daily_mean)
    lag_1d[0] = daily_mean[0]
    lag_1d[1:] = daily_mean[:-1]

    trend_7d = daily_mean - temp_7d
    diurnal_range = daily_max - daily_min

    # Broadcast each daily feature to hourly (repeat each value 24×)
    def _bcast(arr_d: np.ndarray) -> np.ndarray:
        return np.repeat(arr_d, 24)[:n]

    return {
        "temp_1d_lag":        _bcast(lag_1d).astype(np.float32),
        "temp_7d_mean":       _bcast(temp_7d).astype(np.float32),
        "temp_30d_mean":      _bcast(temp_30d).astype(np.float32),
        "temp_trend_7d":      _bcast(trend_7d).astype(np.float32),
        "temp_daily_max":     _bcast(daily_max).astype(np.float32),
        "temp_daily_min":     _bcast(daily_min).astype(np.float32),
        "temp_diurnal_range": _bcast(diurnal_range).astype(np.float32),
    }


def build_unified_dataset(
    dataset_dir: Path,
    hdd_base: float = 18.0,
    cdd_base: float = 24.0,
    real_only: bool = True,
    normalize_per_country: bool = True,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> pd.DataFrame:
    """Build a single hourly DataFrame from all Parquet country-years.

    Each row is one hour of one country-year, with features and target.

    Parameters
    ----------
    real_only : bool
        If True, exclude PLEXOS synthetic data (only use real observations).
    normalize_per_country : bool
        If True, shape_factor is normalized per country (across all years),
        so interannual temperature effects are preserved. If False, normalized
        per country-year (traditional, mean exactly 1.0 per year).

    Returns DataFrame with columns:
        group_id, year, time_idx, shape_factor,
        + all FEATURE_COLS (pixel-aware, 22 features as of 2026-04-16)
    """
    from esfex.models.demand_dataset import (
        load_manifest, iter_manifest_entries)
    from esfex.models.pixel_features import build_features_for_point

    manifest = load_manifest(dataset_dir)
    # Support both flat manifest ({ISO3: {lat, lon, years, zones?}}) and
    # the legacy entries-list format. Always route through
    # iter_manifest_entries so zonal samples are included uniformly.
    if isinstance(manifest, dict) and any(
        isinstance(v, dict) and "years" in v for v in manifest.values()
    ):
        entries = iter_manifest_entries(manifest)
    else:
        legacy_entries = manifest.get("entries", []) if isinstance(
            manifest, dict) else []
        entries = [{"iso3": e["iso3"], "zone": None, "year": e["year"],
                    "lat": 0.0, "lon": 0.0}
                   for e in legacy_entries]
    if not entries:
        raise ValueError(f"No data in dataset at {dataset_dir}")

    # Filter to real data only if requested
    if real_only:
        real_countries = set()
        for d in dataset_dir.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            meta_file = d / "metadata.json"
            if meta_file.exists():
                with open(meta_file) as f:
                    meta = json.load(f)
                if "PLEXOS" not in meta.get("source", ""):
                    real_countries.add(d.name)
        entries = [e for e in entries if e["iso3"] in real_countries]
        logger.info("Filtered to %d real country-years (%d countries)",
                    len(entries), len(real_countries))

    # Pass 1: compute per-group mean demand for normalization.
    # When zones are present, the group key is (iso3, zone); otherwise (iso3, None).
    # This keeps the shape factor interpretable at whichever spatial scale the
    # training sample lives at.
    group_means: dict[tuple[str, Optional[str]], float] = {}
    if normalize_per_country:
        group_demand: dict[tuple[str, Optional[str]], list[float]] = {}
        for entry in entries:
            iso3 = entry["iso3"]
            zone = entry.get("zone")
            pq_name = (f"{iso3}_{zone}_{entry['year']}.parquet" if zone
                       else f"{iso3}_{entry['year']}.parquet")
            pq_path = dataset_dir / iso3 / pq_name
            if not pq_path.exists():
                continue
            df = pd.read_parquet(pq_path, columns=["demand_mw"])
            demand = df["demand_mw"].values.astype(np.float64)
            if len(demand) >= 8760 and demand.mean() > 0:
                group_demand.setdefault((iso3, zone), []).append(
                    demand[:8760].mean())
        for key, means in group_demand.items():
            group_means[key] = float(np.mean(means))
        logger.info("Normalization groups: %d (countries + zones)",
                    len(group_means))

    # Pass 2: build feature rows
    dfs: list[pd.DataFrame] = []

    for i, entry in enumerate(entries):
        iso3 = entry["iso3"]
        yr = entry["year"]
        zone = entry.get("zone")

        pq_name = (f"{iso3}_{zone}_{yr}.parquet" if zone
                   else f"{iso3}_{yr}.parquet")
        pq_path = dataset_dir / iso3 / pq_name
        if not pq_path.exists():
            continue

        df = pd.read_parquet(pq_path)
        demand = df["demand_mw"].values.astype(np.float64)
        if len(demand) < 8760 or demand.mean() <= 0:
            continue

        annual_mean = demand[:8760].mean()
        norm_mean = (group_means[(iso3, zone)]
                     if (normalize_per_country
                         and (iso3, zone) in group_means)
                     else annual_mean)
        shape = demand[:8760] / norm_mean

        # Lat/lon: zone centroid from manifest entry when zonal, else
        # fall back to metadata.json (capital coords) for backwards compat.
        lat = entry.get("lat", 0.0)
        lon = entry.get("lon", 0.0)
        if (lat == 0.0 and lon == 0.0) or zone is None:
            meta_path = dataset_dir / iso3 / "metadata.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
                if zone is None:
                    lat = meta.get("lat", lat)
                    lon = meta.get("lon", lon)

        # Pixel-aware features at this sample's location. Zone param ensures
        # ERA5/UTCI lookups go to the zone-specific cache when available.
        feats = build_features_for_point(
            lat, lon, yr, iso3,
            hdd_base=hdd_base, cdd_base=cdd_base,
            zone=zone,
        )
        temp = feats.temperature

        n = 8760
        hours = np.arange(n)
        hour_of_day = hours % 24
        day_of_year = hours // 24

        jan1 = datetime.date(yr, 1, 1)
        dow = np.array([(jan1 + datetime.timedelta(days=int(d))).weekday()
                         for d in day_of_year])

        _month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        month = np.zeros(n, dtype=int)
        h = 0
        for m, md in enumerate(_month_days):
            month[h:h + md * 24] = m
            h += md * 24

        h_sin, h_cos = _cyclical(hour_of_day.astype(float), 24.0)
        d_sin, d_cos = _cyclical(dow.astype(float), 7.0)
        m_sin, m_cos = _cyclical(month.astype(float), 12.0)

        is_hday, d_next_hday, d_prev_hday = _holiday_features(iso3, yr, hours=n)

        # Temperature lag features (daily-res, broadcast to hourly)
        lag_feats = _temperature_lag_features(feats.temperature[:n])

        row = pd.DataFrame({
            "group_id": (f"{iso3}_{zone}_{yr}" if zone else f"{iso3}_{yr}"),
            "country": iso3,
            "zone": zone if zone else "",
            "year": yr,
            "time_idx": hours,
            TARGET_COL: shape,
            "demand_mw": demand[:n],
            "annual_mean_mw": annual_mean,
            # Static pixel features (constant across the 8760 rows of this CY)
            "log_gdp_per_cap": feats.log_gdp_per_cap,
            "log_pop_density": feats.log_pop_density,
            "urbanization": feats.urbanization,
            # Time-varying thermal features
            "temperature": feats.temperature[:n],
            "hdd": feats.hdd[:n],
            "cdd": feats.cdd[:n],
            # Calendar features
            "hour_of_day": hour_of_day,
            "month": month,
            "day_of_week": dow,
            "hour_sin": h_sin,
            "hour_cos": h_cos,
            "dow_sin": d_sin,
            "dow_cos": d_cos,
            "month_sin": m_sin,
            "month_cos": m_cos,
            "is_weekend": (dow >= 5).astype(float),
            "is_holiday": is_hday,
            "days_to_next_holiday": d_next_hday,
            "days_from_prev_holiday": d_prev_hday,
            "latitude": lat,
            "longitude": lon,
            # Temperature lag features (daily-resolution, broadcast to hourly)
            **lag_feats,
        })
        dfs.append(row)

        if progress_cb and (i + 1) % 20 == 0:
            progress_cb(int(80 * (i + 1) / len(entries)),
                        f"Loading {i + 1}/{len(entries)}: {iso3}_{yr}")

    if not dfs:
        raise ValueError("No valid country-years found in dataset.")

    full = pd.concat(dfs, ignore_index=True)

    # Impute static-feature NaNs. When the GDP or population raster misses a
    # zone/country (small islands, remote peninsulas) `build_features_for_point`
    # returns NaN for log_gdp_per_cap / log_pop_density. TimeSeriesDataSet
    # refuses to train on NaN; fill with per-country median, falling back to
    # the global median. This keeps the sample and leaves the rest of its
    # features intact.
    for col in ("log_gdp_per_cap", "log_pop_density", "urbanization"):
        if col not in full.columns:
            continue
        full[col] = full[col].replace([np.inf, -np.inf], np.nan)
        if full[col].isna().any():
            global_med = float(full[col].median(skipna=True))
            per_country = full.groupby("country")[col].transform(
                lambda s: s.fillna(s.median(skipna=True)))
            full[col] = per_country.fillna(global_med)

    # Shape factor (target) and thermal features can have isolated NaN/Inf
    # from gaps in the source demand parquets. Forward-fill within each
    # group_id (limit 6h) then back-fill, and as a last resort set to 1.0
    # (the expected mean of a normalized shape factor).
    for col in (TARGET_COL, "temperature", "hdd", "cdd"):
        if col not in full.columns:
            continue
        full[col] = full[col].replace([np.inf, -np.inf], np.nan)
        if full[col].isna().any():
            full[col] = (full.groupby("group_id")[col]
                         .transform(lambda s: s.ffill(limit=6).bfill(limit=6)))
    if full[TARGET_COL].isna().any():
        full[TARGET_COL] = full[TARGET_COL].fillna(1.0)
    for col in ("temperature", "hdd", "cdd"):
        if col not in full.columns:
            continue
        if full[col].isna().any():
            full[col] = full[col].fillna(full[col].median(skipna=True))

    # Demand-shape autoregressive lags (1h, 24h, 168h) within each group_id.
    # Matches TFT's time_varying_unknown_real access to shape_factor in the
    # encoder window. Fills NaN at the start of each group with the per-group
    # median (so first 168h remain usable). Only usable for training-time
    # model comparison — inference on arbitrary pixels has no past demand.
    full = full.sort_values(["group_id", "time_idx"]).reset_index(drop=True)
    for lag_h, col in [(1, "shape_lag_1h"), (24, "shape_lag_24h"),
                        (168, "shape_lag_168h")]:
        full[col] = full.groupby("group_id")[TARGET_COL].shift(lag_h)
        # Fallback for initial NaN: per-group median (reasonable since shape
        # has mean ≈ 1.0 already)
        grp_med = full.groupby("group_id")[TARGET_COL].transform("median")
        full[col] = full[col].fillna(grp_med)
        full[col] = full[col].astype(np.float32)

    # country_id: stable integer encoding of ISO3 (sorted alphabetically so
    # new countries get a unique id and can be mapped to -1/unknown at inference)
    country_codes = sorted(full["country"].unique())
    country_to_id = {c: i for i, c in enumerate(country_codes)}
    full["country_id"] = full["country"].map(country_to_id).astype(np.float32)

    # Persist the mapping alongside the model so inference can encode new countries
    import json as _json
    from esfex.paths import MODELS_DIR
    _country_map_path = MODELS_DIR / "demand_xgb_country_map.json"
    _country_map_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_country_map_path, "w") as _f:
        _json.dump(country_to_id, _f)

    # Sample weighting: inverse of n_zones(country) clipped to [0.3, 1.0]
    # so that countries with many zones (e.g. USA 60+ BAs, CHN 31 provs) do
    # not overwhelm the training signal from countries with one sample.
    n_zones_per_country = (full.groupby("country")["zone"].nunique()
                           .replace(0, 1).to_dict())
    full["sample_weight"] = full["country"].map(
        lambda c: float(np.clip(1.0 / max(n_zones_per_country.get(c, 1), 1),
                                0.3, 1.0))
    )

    logger.info(
        "Unified dataset: %d rows, %d groups, %d countries, %d zonal rows",
        len(full), full["group_id"].nunique(), full["country"].nunique(),
        int((full["zone"] != "").sum()),
    )
    return full


# ── Temporal cross-validation ────────────────────────────────────────────────


def temporal_cv_splits(
    df: pd.DataFrame,
    n_folds: int = 3,
    strategy: str = "expanding",
) -> list[tuple[pd.DataFrame, pd.DataFrame, int]]:
    """Generate temporal cross-validation splits.

    Parameters
    ----------
    df : DataFrame
        Must have a 'year' column.
    n_folds : int
        Number of CV folds.
    strategy : str
        'expanding' — train grows each fold (2015-16→2017, 2015-17→2018, ...)
        'rolling'  — fixed train window that slides

    Returns
    -------
    list of (train_df, val_df, val_year) tuples
    """
    years = sorted(df["year"].unique())
    if len(years) < n_folds + 1:
        n_folds = len(years) - 1

    splits = []
    for fold in range(n_folds):
        if strategy == "expanding":
            val_year = years[-(n_folds - fold)]
            train_years = [y for y in years if y < val_year]
        else:  # rolling
            val_year = years[-(n_folds - fold)]
            train_years = [y for y in years if y < val_year][-3:]  # 3-year window

        if not train_years:
            continue

        train_df = df[df["year"].isin(train_years)].reset_index(drop=True)
        val_df = df[df["year"] == val_year].reset_index(drop=True)
        splits.append((train_df, val_df, val_year))

    return splits


def leave_country_out_split(
    df: pd.DataFrame,
    exclude_iso3: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by excluding one country entirely."""
    train = df[df["country"] != exclude_iso3].reset_index(drop=True)
    test = df[df["country"] == exclude_iso3].reset_index(drop=True)
    return train, test


def loco_splits(
    df: pd.DataFrame,
    n_folds: int = 10,
    seed: int = 42,
    min_cy_per_country: int = 3,
) -> list[tuple[pd.DataFrame, pd.DataFrame, str]]:
    """Leave-one-country-out cross-validation.

    Randomly selects ``n_folds`` countries to hold out, each as its own
    fold. **All zones** of the held-out country go to validation to
    prevent leakage when a country has multiple zonal samples.

    Parameters
    ----------
    df : DataFrame
        Output of build_unified_dataset (must have 'country' column).
    n_folds : int
        Number of LOCO folds.
    seed : int
        RNG seed for country selection.
    min_cy_per_country : int
        Only eligible hold-out candidates with at least this many CY or
        zone-years, so the validation fold has enough data to produce
        reliable metrics.

    Returns
    -------
    list of (train_df, val_df, held_out_country) tuples.
    """
    rng = np.random.default_rng(seed)
    cy_per_country = df.groupby("country")["group_id"].nunique()
    eligible = cy_per_country[cy_per_country >= min_cy_per_country].index.tolist()
    if len(eligible) < n_folds:
        n_folds = len(eligible)
    chosen = rng.choice(eligible, size=n_folds, replace=False)
    out = []
    for country in chosen:
        train = df[df["country"] != country].reset_index(drop=True)
        val = df[df["country"] == country].reset_index(drop=True)
        out.append((train, val, str(country)))
    return out


# ── Multi-resolution evaluation ──────────────────────────────────────────────


def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    resolutions: list[tuple[str, int]] = None,
) -> list[dict]:
    """Compute metrics at multiple temporal resolutions.

    Parameters
    ----------
    y_true, y_pred : ndarray
        Hourly values (same length).
    resolutions : list of (name, block_hours)

    Returns list of dicts with resolution, R², RMSE, MAE, MAPE, corr, n.
    """
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    if resolutions is None:
        resolutions = [
            ("Hourly", 1), ("3-hourly", 3), ("6-hourly", 6),
            ("Daily", 24), ("Weekly", 168), ("Monthly", 730),
        ]

    results = []
    for name, block in resolutions:
        n = len(y_true) // block
        if n < 2:
            continue
        t = y_true[:n * block].reshape(n, block).mean(axis=1)
        p = y_pred[:n * block].reshape(n, block).mean(axis=1)

        r2 = r2_score(t, p)
        rmse = float(np.sqrt(mean_squared_error(t, p)))
        mae = float(mean_absolute_error(t, p))
        mape = float(np.mean(np.abs(t - p) / np.clip(np.abs(t), 1e-6, None)) * 100)
        corr = float(np.corrcoef(t, p)[0, 1]) if n > 1 else 0.0

        results.append({
            "resolution": name, "block_hours": block,
            "n": n, "r2": r2, "rmse": rmse, "mae": mae,
            "mape": mape, "corr": corr,
        })

    return results


# ── XGBoost training ─────────────────────────────────────────────────────────


def train_xgboost(
    df: pd.DataFrame,
    output_path: Optional[Path] = None,
    n_cv_folds: int = 3,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """Train XGBoost on unified hourly dataset with temporal CV.

    Returns dict with model, metrics, and CV results.
    """
    try:
        import xgboost as xgb
    except ImportError:
        raise ImportError("xgboost required. Install with: pip install 'esfex[ml]'")
    from sklearn.metrics import r2_score

    if output_path is None:
        output_path = MODELS_DIR / "demand_model.xgb"

    def emit(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    # ── Temporal CV ──
    emit(10, "Running temporal cross-validation...")
    splits = temporal_cv_splits(df, n_folds=n_cv_folds, strategy="expanding")

    cv_results = []
    for fold_i, (train_df, val_df, val_year) in enumerate(splits):
        X_tr = train_df[FEATURE_COLS].values
        y_tr = train_df[TARGET_COL].values
        X_va = val_df[FEATURE_COLS].values
        y_va = val_df[TARGET_COL].values

        dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=FEATURE_COLS)
        dval = xgb.DMatrix(X_va, label=y_va, feature_names=FEATURE_COLS)

        params = {
            "objective": "reg:squarederror",
            "max_depth": 8, "learning_rate": 0.05,
            "subsample": 0.8, "colsample_bytree": 0.8,
            "reg_alpha": 0.1, "reg_lambda": 1.0,
            "tree_method": "hist", "verbosity": 0,
        }
        booster = xgb.train(
            params, dtrain, num_boost_round=500,
            evals=[(dval, "val")], verbose_eval=False,
            early_stopping_rounds=30,
        )
        y_pred = booster.predict(dval)
        fold_r2 = float(r2_score(y_va, y_pred))
        fold_metrics = evaluate_predictions(y_va, y_pred)

        # Save fold predictions for parity plots
        preds_dir = output_path.parent / "cv_predictions"
        preds_dir.mkdir(exist_ok=True)
        np.savez_compressed(
            preds_dir / f"xgboost_fold{fold_i}.npz",
            y_true=y_va, y_pred=y_pred,
            countries=val_df["country"].values,
        )

        cv_results.append({
            "fold": fold_i, "val_year": int(val_year),
            "n_train": len(X_tr), "n_val": len(X_va),
            "r2": fold_r2,
            "best_iteration": int(booster.best_iteration),
            "multi_res": fold_metrics,
        })
        emit(10 + int(40 * (fold_i + 1) / len(splits)),
             f"Fold {fold_i + 1}: val_year={val_year}, R²={fold_r2:.4f}")

    avg_r2 = np.mean([f["r2"] for f in cv_results])
    emit(55, f"CV average R²={avg_r2:.4f}")

    # ── Train final model on ALL data ──
    emit(60, "Training final model on all data...")
    X_all = df[FEATURE_COLS].values
    y_all = df[TARGET_COL].values
    dtrain_all = xgb.DMatrix(X_all, label=y_all, feature_names=FEATURE_COLS)

    final_booster = xgb.train(
        params, dtrain_all, num_boost_round=500, verbose_eval=False,
    )

    # Feature importance
    importance = final_booster.get_score(importance_type="gain")

    # Save
    emit(90, "Saving model...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_booster.save_model(str(output_path))

    metrics = {
        "engine": "xgboost",
        "n_samples": len(X_all),
        "n_features": len(FEATURE_COLS),
        "n_countries": int(df["country"].nunique()),
        "n_country_years": int(df["group_id"].nunique()),
        "cv_folds": len(cv_results),
        "cv_avg_r2": avg_r2,
        "cv_results": cv_results,
        "feature_importance": {
            k: float(v) for k, v in sorted(importance.items(), key=lambda x: -x[1])
        },
    }

    metrics_path = output_path.parent / "demand_model_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    emit(100, f"XGBoost trained: CV R²={avg_r2:.4f}, saved to {output_path}")
    return metrics


# ── Unified entry point ──────────────────────────────────────────────────────


def train_demand_model(
    engine: str = "xgboost",
    dataset_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
    n_cv_folds: int = 3,
    exclude_country: Optional[str] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    **kwargs,
) -> dict:
    """Train demand model from the unified Parquet dataset.

    Parameters
    ----------
    engine : str
        'xgboost' or 'tft'.
    dataset_dir : Path
        Path to Parquet dataset (from build-demand-dataset).
    output_path : Path
        Where to save the trained model.
    n_cv_folds : int
        Number of temporal CV folds.
    exclude_country : str, optional
        ISO3 code to exclude (leave-one-country-out validation).
    """
    if dataset_dir is None:
        dataset_dir = DEMAND_DATASET_DIR

    def emit(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    # Build unified dataset
    emit(5, "Building unified hourly dataset...")
    df = build_unified_dataset(
        dataset_dir,
        progress_cb=lambda p, m: emit(int(5 + p * 0.3), m),
    )
    emit(40, f"Dataset: {len(df):,} rows, {df['country'].nunique()} countries, "
         f"{df['group_id'].nunique()} country-years")

    # Exclude country if requested
    if exclude_country:
        n_before = len(df)
        df = df[df["country"] != exclude_country].reset_index(drop=True)
        emit(42, f"Excluded {exclude_country}: {n_before - len(df):,} rows removed")

    if engine == "xgboost":
        if output_path is None:
            output_path = MODELS_DIR / "demand_model.xgb"
        return train_xgboost(df, output_path, n_cv_folds, progress_cb=emit)
    elif engine == "tft":
        emit(45, "Launching TFT training...")
        from esfex.models.demand_tft import train_tft_model
        return train_tft_model(
            dataset_dir=dataset_dir,
            progress_cb=emit,
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown engine: {engine}")
