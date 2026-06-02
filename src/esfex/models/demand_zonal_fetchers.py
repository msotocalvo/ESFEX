"""Zonal demand fetchers (Option B, Phase 1 greenfield).

These fetchers produce **per-zone** hourly load data instead of a single
national total. Each zone becomes its own TFT training sample with
features sampled at the zone's population-weighted centroid.

Output: zonal Parquet files at
``{DEMAND_DATASET_DIR}/{ISO3}/{ISO3}_{ZONE}_{YEAR}.parquet`` plus an
update to the manifest's ``zones`` dict for each country.

Unlike ``demand_real_data.fetch_*`` these fetchers are standalone — they
write directly to disk and update the manifest, because the legacy
``fetch_all_real_load()`` return type is flat {iso3: {year: array}} and
can't carry the zone dimension cleanly.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from esfex.models.demand_dataset import write_zonal_parquet, _fetch_era5_year
from esfex.models.zone_centroids import (
    AUS_NEM_MEMBERS,
    BRA_SUBSYSTEM_MEMBERS,
    CHN_PROVINCE_CODES,
    THA_ZONE_MEMBERS,
    TWN_ZONE_MEMBERS,
    resolve_zones,
)
from esfex.paths import DEMAND_DATASET_DIR, DEMAND_ERA5_DIR, NEW_SOURCES_DIR

logger = logging.getLogger(__name__)


def _resample_hourly(df: pd.DataFrame, dt_col: str = "datetime") -> pd.DataFrame:
    """Force hourly resolution by taking the mean over each clock hour."""
    df = df.copy()
    df[dt_col] = pd.to_datetime(df[dt_col])
    df = df.set_index(dt_col).sort_index()
    # Bucket timestamps into clock-hours, then mean.
    return df.resample("1h", label="left").mean()


def _align_year_8760(series: pd.Series, year: int) -> Optional[np.ndarray]:
    """Clip a datetime-indexed Series to the year, drop Feb 29, pad to 8760."""
    start = pd.Timestamp(f"{year}-01-01 00:00:00")
    end = pd.Timestamp(f"{year}-12-31 23:00:00")
    hourly_index = pd.date_range(start, end, freq="1h")
    series = series.reindex(hourly_index)
    # Remove Feb 29 for leap years (dataset-wide convention)
    if pd.Timestamp(f"{year}-01-01").is_leap_year:
        mask = ~((hourly_index.month == 2) & (hourly_index.day == 29))
        series = series[mask]
    arr = series.astype(float).values
    if len(arr) < 8760 - 48:
        return None
    if len(arr) < 8760:
        arr = np.pad(arr, (0, 8760 - len(arr)), constant_values=np.nan)
    arr = arr[:8760]
    # Forward-fill short gaps (<= 6h)
    s = pd.Series(arr).ffill(limit=6).bfill(limit=6)
    if s.isna().any():
        return None
    return s.values.astype(np.float64)


def _ensure_era5_for_zone(
    iso3: str, zone: str, year: int, lat: float, lon: float,
    era5_dir: Optional[Path] = None,
) -> Optional[np.ndarray]:
    """Fetch ERA5 temperature for (iso3, zone, year) if not already cached.

    Uses the same open-meteo archive API the national fetcher uses, but
    writes to ``era5_{zone}_{year}.npy`` so it survives alongside the
    national cache.
    """
    if era5_dir is None:
        era5_dir = DEMAND_ERA5_DIR
    country_dir = era5_dir / iso3
    country_dir.mkdir(parents=True, exist_ok=True)
    zone_cache = country_dir / f"era5_{zone}_{year}.npy"
    if zone_cache.exists():
        return np.load(zone_cache)

    # _fetch_era5_year writes to era5_{year}.npy — we need era5_{zone}_{year}.npy.
    # Reuse its HTTP logic but write to the zonal filename.
    import requests
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={year}-01-01&end_date={year}-12-31"
        "&hourly=temperature_2m&timezone=UTC"
    )
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        temps = resp.json().get("hourly", {}).get("temperature_2m", [])
        if len(temps) >= 8760:
            arr = np.array(temps[:8760], dtype=np.float64)
            np.save(zone_cache, arr)
            return arr
    except Exception as exc:
        logger.warning("ERA5 %s/%s %d (%.2f, %.2f) failed: %s",
                       iso3, zone, year, lat, lon, exc)
    return None


def _update_manifest_zones(
    iso3: str,
    zones: dict,
    country_meta: Optional[dict] = None,
    cache_dir: Optional[Path] = None,
) -> None:
    """Merge zonal information into manifest.json.

    ``zones`` is a dict: zone_id -> {lat, lon, years, source}
    The manifest keeps its flat per-country layout; zones go under
    manifest[iso3]["zones"]. Years listed in zones are the zonal-year
    coverage, separate from the national ``years`` list.
    """
    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR
    manifest_path = cache_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = {}
    entry = manifest.get(iso3, {})
    if country_meta:
        for k, v in country_meta.items():
            entry.setdefault(k, v)
    z = entry.get("zones", {})
    for zone_id, info in zones.items():
        z[zone_id] = info
    entry["zones"] = z
    manifest[iso3] = entry
    # Backup then overwrite atomically.
    bak = manifest_path.with_suffix(".json.bak")
    if manifest_path.exists():
        bak.write_text(manifest_path.read_text())
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    tmp.replace(manifest_path)


# ── Taiwan (Taipower 4 zones) ──────────────────────────────────────────────


def fetch_taiwan_zonal(
    csv_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
    era5_workers: int = 4,
) -> dict:
    """Parse Taipower 10-min loadarea CSV and emit per-zone parquets.

    CSV columns: datetime, south, north, east, central (MW).
    Written to ``TWN/{ZONE}/{TWN_{zone}_{year}.parquet}``.

    Returns manifest["TWN"]["zones"] entry.
    """
    if csv_path is None:
        csv_path = NEW_SOURCES_DIR / "taiwan" / "taiwan_loadarea_10min.csv"
    if not csv_path.exists():
        logger.warning("Taiwan CSV not found at %s", csv_path)
        return {}

    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR

    logger.info("Reading Taiwan CSV: %s", csv_path)
    raw = pd.read_csv(csv_path)
    hourly = _resample_hourly(raw, "datetime")

    centroids = resolve_zones("TWN", TWN_ZONE_MEMBERS, year=2020)
    logger.info("TWN centroids: %s",
                {z: (c.lat, c.lon) for z, c in centroids.items()})

    zones_out: dict = {}

    # Discover complete years in the data
    years = sorted(set(hourly.index.year.tolist()))
    for year in years:
        for zone_id in ("north", "central", "south", "east"):
            centroid = centroids.get(zone_id)
            if centroid is None:
                continue
            series = hourly[zone_id] if zone_id in hourly.columns else None
            if series is None:
                continue
            arr = _align_year_8760(series, year)
            if arr is None or np.nanmean(arr) <= 0:
                logger.info("TWN %s %d: insufficient data, skipping", zone_id, year)
                continue
            # ERA5 at zone centroid
            temp = None
            if download_era5:
                temp = _ensure_era5_for_zone("TWN", zone_id, year,
                                             centroid.lat, centroid.lon)
            write_zonal_parquet("TWN", zone_id, year, arr, temp, cache_dir)
            zones_out.setdefault(zone_id, {"lat": centroid.lat,
                                           "lon": centroid.lon,
                                           "source": "Taipower",
                                           "centroid_method": centroid.method,
                                           "years": []})["years"].append(year)
            logger.info("TWN %s %d: wrote parquet (mean=%.1f MW)",
                        zone_id, year, float(np.nanmean(arr)))

    # Country-level metadata for manifest: Taipei capital
    country_meta = {"lat": 25.03, "lon": 121.57, "source": "Taipower"}
    _update_manifest_zones("TWN", zones_out, country_meta, cache_dir)
    return zones_out


# ── Thailand (EGAT 5 regions) ──────────────────────────────────────────────


def fetch_thailand_zonal(
    src_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
) -> dict:
    """Parse EGAT system_{YEAR}.csv files, emit per-zone parquets.

    CSV columns include {region}_demand for region in
    (north, south, metropolitan, central, northeast).
    """
    if src_dir is None:
        src_dir = NEW_SOURCES_DIR / "thailand"
    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR
    csvs = sorted(src_dir.glob("system_*.csv"))
    if not csvs:
        logger.warning("No Thailand system_*.csv files in %s", src_dir)
        return {}

    centroids = resolve_zones("THA", THA_ZONE_MEMBERS, year=2020)
    logger.info("THA centroids: %s",
                {z: (c.lat, c.lon) for z, c in centroids.items()})

    zones_out: dict = {}

    for csv in csvs:
        # Filename: system_{YEAR}.csv — year comes from stem.
        try:
            year = int(csv.stem.split("_")[-1])
        except ValueError:
            continue
        logger.info("Reading THA %s", csv.name)
        raw = pd.read_csv(csv)
        # Date format in the file is DD/MM/YYYY HH:MM
        raw["datetime"] = pd.to_datetime(raw["datetime"],
                                         format="%d/%m/%Y %H:%M")
        hourly = _resample_hourly(raw, "datetime")

        for zone_id in THA_ZONE_MEMBERS.keys():
            col = f"{zone_id}_demand"
            centroid = centroids.get(zone_id)
            if col not in hourly.columns or centroid is None:
                continue
            arr = _align_year_8760(hourly[col], year)
            if arr is None or np.nanmean(arr) <= 0:
                logger.info("THA %s %d: insufficient data, skipping",
                            zone_id, year)
                continue
            temp = None
            if download_era5:
                temp = _ensure_era5_for_zone("THA", zone_id, year,
                                             centroid.lat, centroid.lon)
            write_zonal_parquet("THA", zone_id, year, arr, temp, cache_dir)
            zones_out.setdefault(zone_id, {"lat": centroid.lat,
                                           "lon": centroid.lon,
                                           "source": "EGAT",
                                           "centroid_method": centroid.method,
                                           "years": []})["years"].append(year)
            logger.info("THA %s %d: wrote parquet (mean=%.1f MW)",
                        zone_id, year, float(np.nanmean(arr)))

    country_meta = {"lat": 13.75, "lon": 100.50, "source": "EGAT"}
    _update_manifest_zones("THA", zones_out, country_meta, cache_dir)
    return zones_out


# ── China (31 provinces, hourly, single year) ──────────────────────────────


def fetch_china_zonal(
    csv_path: Optional[Path] = None,
    year: int = 2018,
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
) -> dict:
    """Parse the China 31-province hourly load CSV.

    Header format: ``Time Series(unit:MWh);BJ;TJ;HB;SX;NM;LN;JL;HL;SH;JS;
    ZJ;AH;FJ;JX;SD;HA;HB;HN;GD;GX;HI;CQ;SC;GZ;YN;XZ;SN;GS;QH;NX;XJ``.
    Note that ``HB`` is duplicated (Hebei and Hubei). Pandas renames the
    second to ``HB.1`` automatically; we remap that to ``HU`` for Hubei.

    The CSV has no explicit year column. Based on totals (~6900 TWh)
    this matches 2018 China national consumption. Caller can override
    via ``year`` param.
    """
    if csv_path is None:
        csv_path = NEW_SOURCES_DIR / "china" / "china_hourly_load.csv"
    if not csv_path.exists():
        logger.warning("China CSV not found at %s", csv_path)
        return {}
    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR

    raw = pd.read_csv(csv_path, sep=";")
    # First col is the 1..8760 hour index — drop it.
    raw = raw.iloc[:, 1:]
    # Pandas renames duplicate col "HB" → "HB.1" (Hubei). Map to "HU".
    if "HB.1" in raw.columns:
        raw = raw.rename(columns={"HB.1": "HU"})

    # MWh (hourly) is numerically equivalent to average MW for that hour.
    # Align to 8760 — already 8760 rows, but sanity check.
    if len(raw) < 8760:
        logger.warning("China CSV has only %d rows (<8760); skipping",
                       len(raw))
        return {}
    raw = raw.iloc[:8760].reset_index(drop=True)

    centroids = resolve_zones("CHN", {
        code: [name] for code, name in CHN_PROVINCE_CODES.items()
    }, year=year)
    logger.info("CHN centroids resolved: %d zones",
                sum(1 for c in centroids.values() if c.method != "missing"))

    zones_out: dict = {}

    for code in CHN_PROVINCE_CODES:
        centroid = centroids.get(code)
        if centroid is None or centroid.method == "missing":
            continue
        if code not in raw.columns:
            # Disambiguated Hubei — check the original "HB" was Hubei via position
            continue
        arr = raw[code].astype(float).values
        if np.nanmean(arr) <= 0:
            continue
        temp = None
        if download_era5:
            temp = _ensure_era5_for_zone("CHN", code, year,
                                         centroid.lat, centroid.lon)
        write_zonal_parquet("CHN", code, year, arr, temp, cache_dir)
        zones_out[code] = {"lat": centroid.lat, "lon": centroid.lon,
                           "source": "CSG/SGCC (2018 est.)",
                           "centroid_method": centroid.method,
                           "years": [year]}
        logger.info("CHN %s %d: wrote parquet (mean=%.1f MW)",
                    code, year, float(np.nanmean(arr)))

    country_meta = {"lat": 39.90, "lon": 116.40, "source": "CSG/SGCC"}
    _update_manifest_zones("CHN", zones_out, country_meta, cache_dir)
    return zones_out


# ── Brazil ONS (4 subsistemas N/NE/SE/S, HF parquet) ─────────────────────


_BRAZIL_HF_FULL_URL = (
    "https://huggingface.co/datasets/SamuelM0422/"
    "Hourly-Electricity-Demand-Brazil-Dataset/resolve/main/"
    "data/train-00000-of-00001.parquet"
)


def fetch_brazil_zonal(
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
) -> dict:
    """Download Brazil ONS per-subsystem hourly data from HuggingFace.

    The HF parquet ``data/train-00000-of-00001.parquet`` has one row per
    (subsystem, hour) with columns ``id_subsistema``, ``nom_subsistema``,
    ``din_instante``, ``val_cargaenergiahomwmed``. Unlike the legacy
    ``fetch_brazil_ons`` which groups by timestamp and sums, we pivot by
    subsystem to preserve the 4 zones (N, NE, SE, S). Subsistema CO
    (Centro-Oeste) was merged into SE in 2001.

    Years covered as of 2026-04-18: 2020–2025 (2025 partial).
    """
    import io
    import requests

    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR

    raw_cache = cache_dir / "_raw" / "brazil"
    raw_cache.mkdir(parents=True, exist_ok=True)
    local_pq = raw_cache / "brazil_ons_hf_bulk.parquet"
    if not local_pq.exists():
        logger.info("Downloading Brazil HF parquet (all years)…")
        resp = requests.get(_BRAZIL_HF_FULL_URL, timeout=120)
        resp.raise_for_status()
        local_pq.write_bytes(resp.content)
    df = pd.read_parquet(local_pq)
    df["din_instante"] = pd.to_datetime(df["din_instante"])
    df = df.dropna(subset=["din_instante", "id_subsistema",
                           "val_cargaenergiahomwmed"])

    centroids = resolve_zones("BRA", BRA_SUBSYSTEM_MEMBERS, year=2020)
    logger.info("BRA centroids: %s",
                {z: (c.lat, c.lon) for z, c in centroids.items()})

    zones_out: dict = {}

    for zone_id in ("N", "NE", "SE", "S"):
        centroid = centroids.get(zone_id)
        if centroid is None or centroid.method == "missing":
            logger.warning("BRA %s: no centroid, skipping", zone_id)
            continue
        sub_df = df[df["id_subsistema"] == zone_id].copy()
        for yr in sorted(sub_df["din_instante"].dt.year.unique()):
            year_df = sub_df[sub_df["din_instante"].dt.year == yr]
            if len(year_df) < 8000:
                logger.info("BRA %s %d: only %d rows, skipping",
                            zone_id, yr, len(year_df))
                continue
            # Build hourly series, drop Feb 29 for leap consistency.
            series = (year_df.set_index("din_instante")
                      ["val_cargaenergiahomwmed"].sort_index())
            arr = _align_year_8760(series, int(yr))
            if arr is None or np.nanmean(arr) <= 0:
                continue
            temp = None
            if download_era5:
                temp = _ensure_era5_for_zone("BRA", zone_id, int(yr),
                                             centroid.lat, centroid.lon)
            write_zonal_parquet("BRA", zone_id, int(yr), arr, temp, cache_dir)
            zones_out.setdefault(zone_id, {"lat": centroid.lat,
                                           "lon": centroid.lon,
                                           "source": "ONS (HF)",
                                           "centroid_method": centroid.method,
                                           "years": []})["years"].append(int(yr))
            logger.info("BRA %s %d: wrote parquet (mean=%.1f MW)",
                        zone_id, int(yr), float(np.nanmean(arr)))

    country_meta = {"lat": -15.79, "lon": -47.88, "source": "ONS"}
    _update_manifest_zones("BRA", zones_out, country_meta, cache_dir)
    return zones_out


# ── AEMO NEM (5 Australian NEM regions) ───────────────────────────────────


_AEMO_NEM_URL = (
    "https://aemo.com.au/aemo/data/nem/priceanddemand/"
    "PRICE_AND_DEMAND_{yyyymm}_{region}.csv"
)


def fetch_aemo_nem_zonal(
    years: range = range(2015, 2026),
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
    http_workers: int = 6,
) -> dict:
    """Fetch AEMO NEM 5-region demand (NSW1, VIC1, QLD1, SA1, TAS1).

    AEMO publishes monthly CSVs at
    ``/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{YYYYMM}_{REGION}.csv``.
    Each has 5-minute TOTALDEMAND values; we resample to hourly mean.

    Note: national AUS entry in the current dataset actually contains
    Western Australia (SWIS) data from ``fetch_aemo_australia`` — which
    is a separate grid from the NEM. The 5 NEM regions here cover the
    eastern states, not WA. So AUS national remains meaningful
    alongside the zonal entries for the 5 NEM regions.
    """
    import io
    import requests

    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR
    raw_dir = cache_dir / "_raw" / "aemo_nem"
    raw_dir.mkdir(parents=True, exist_ok=True)

    centroids = resolve_zones("AUS", AUS_NEM_MEMBERS, year=2020)

    def _download_month(region: str, year: int, month: int) -> Optional[pd.DataFrame]:
        yyyymm = f"{year}{month:02d}"
        cache = raw_dir / f"pd_{yyyymm}_{region}.csv"
        if cache.exists() and cache.stat().st_size > 0:
            return pd.read_csv(cache)
        url = _AEMO_NEM_URL.format(yyyymm=yyyymm, region=region)
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code != 200 or len(resp.content) < 100:
                return None
            cache.write_bytes(resp.content)
            return pd.read_csv(io.StringIO(resp.text))
        except Exception as exc:
            logger.debug("AEMO %s %s failed: %s", region, yyyymm, exc)
            return None

    zones_out: dict = {}

    for region in ("NSW1", "VIC1", "QLD1", "SA1", "TAS1"):
        centroid = centroids.get(region)
        if centroid is None or centroid.method == "missing":
            continue
        for year in years:
            months_ok: list[pd.DataFrame] = []
            # Parallelize 12 months per (region, year)
            with ThreadPoolExecutor(max_workers=http_workers) as pool:
                futures = {pool.submit(_download_month, region, year, m): m
                           for m in range(1, 13)}
                for fut in as_completed(futures):
                    df_month = fut.result()
                    if df_month is not None and len(df_month) > 0:
                        months_ok.append(df_month)
            if len(months_ok) < 10:
                logger.info("AEMO %s %d: only %d months, skipping",
                            region, year, len(months_ok))
                continue
            all_df = pd.concat(months_ok, ignore_index=True)
            all_df["SETTLEMENTDATE"] = pd.to_datetime(
                all_df["SETTLEMENTDATE"], format="%Y/%m/%d %H:%M:%S",
                errors="coerce")
            all_df = all_df.dropna(subset=["SETTLEMENTDATE"])
            series = (all_df.set_index("SETTLEMENTDATE")
                      ["TOTALDEMAND"].astype(float).sort_index())
            # 5-min → 1h
            hourly = series.resample("1h", label="left").mean()
            arr = _align_year_8760(hourly, year)
            if arr is None or np.nanmean(arr) <= 0:
                continue
            temp = None
            if download_era5:
                temp = _ensure_era5_for_zone("AUS", region, year,
                                             centroid.lat, centroid.lon)
            write_zonal_parquet("AUS", region, year, arr, temp, cache_dir)
            zones_out.setdefault(region, {"lat": centroid.lat,
                                          "lon": centroid.lon,
                                          "source": "AEMO NEM",
                                          "centroid_method": centroid.method,
                                          "years": []})["years"].append(year)
            logger.info("AEMO %s %d: wrote parquet (mean=%.1f MW)",
                        region, year, float(np.nanmean(arr)))

    _update_manifest_zones("AUS", zones_out,
                           {"source": "AEMO WA (SWIS) + NEM"},
                           cache_dir)
    return zones_out


# ── IESO Ontario (10 zones) ────────────────────────────────────────────────


_IESO_ZONAL_URL = (
    "https://reports-public.ieso.ca/public/DemandZonal/"
    "PUB_DemandZonal_{year}.csv"
)

# IESO Ontario zones — approximate population-weighted centroids (manual,
# since these zones don't map cleanly to admin_1 polygons). Sourced from
# IESO zone map + population distribution references.
IESO_ZONE_CENTROIDS: dict[str, tuple[float, float]] = {
    "Northwest": (48.38, -89.28),   # Thunder Bay
    "Northeast": (46.49, -81.00),   # Sudbury
    "Ottawa":    (45.42, -75.70),
    "East":      (44.23, -76.48),   # Kingston
    "Toronto":   (43.65, -79.38),
    "Essa":      (44.39, -79.69),   # Barrie
    "Bruce":     (44.74, -81.30),   # Kincardine
    "Southwest": (42.98, -81.25),   # London
    "Niagara":   (43.09, -79.08),
    "West":      (43.46, -80.52),   # Kitchener
}


def fetch_ieso_ontario_zonal(
    years: range = range(2015, 2025),
    cache_dir: Optional[Path] = None,
    download_era5: bool = True,
) -> dict:
    """Fetch IESO Ontario hourly zonal demand for 10 zones.

    Each year's CSV has header ``Date,Hour,Ontario Demand,Northwest,
    Northeast,Ottawa,East,Toronto,Essa,Bruce,Southwest,Niagara,West,...``
    with one row per hour (1-24) of each day. Written under ISO3 ``CAN``
    since Ontario is part of Canada.
    """
    import io
    import requests

    if cache_dir is None:
        cache_dir = DEMAND_DATASET_DIR
    raw_dir = cache_dir / "_raw" / "ieso"
    raw_dir.mkdir(parents=True, exist_ok=True)

    zones_out: dict = {}

    for year in years:
        cache = raw_dir / f"ieso_zonal_{year}.csv"
        if not cache.exists():
            url = _IESO_ZONAL_URL.format(year=year)
            try:
                resp = requests.get(url, timeout=60)
                if resp.status_code != 200 or len(resp.content) < 1000:
                    logger.info("IESO %d: HTTP %d, skipping",
                                year, resp.status_code)
                    continue
                cache.write_bytes(resp.content)
            except Exception as exc:
                logger.warning("IESO %d fetch failed: %s", year, exc)
                continue

        # Skip metadata lines (start with backslashes) then parse header.
        with open(cache) as f:
            lines = f.readlines()
        header_idx = None
        for i, ln in enumerate(lines):
            if ln.startswith("Date,") and "Hour" in ln:
                header_idx = i
                break
        if header_idx is None:
            logger.warning("IESO %d: no header found", year)
            continue
        df = pd.read_csv(io.StringIO("".join(lines[header_idx:])))
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Hour"])
        df["timestamp"] = df["Date"] + pd.to_timedelta(df["Hour"] - 1, unit="h")

        for zone_id, (lat, lon) in IESO_ZONE_CENTROIDS.items():
            if zone_id not in df.columns:
                logger.warning("IESO %d: zone %s not in CSV", year, zone_id)
                continue
            series = df.set_index("timestamp")[zone_id].astype(float).sort_index()
            arr = _align_year_8760(series, year)
            if arr is None or np.nanmean(arr) <= 0:
                continue
            temp = None
            if download_era5:
                temp = _ensure_era5_for_zone("CAN", zone_id, year, lat, lon)
            write_zonal_parquet("CAN", zone_id, year, arr, temp, cache_dir)
            zones_out.setdefault(zone_id, {"lat": lat, "lon": lon,
                                           "source": "IESO",
                                           "centroid_method": "hardcoded",
                                           "years": []})["years"].append(year)
            logger.info("IESO CAN %s %d: wrote parquet (mean=%.1f MW)",
                        zone_id, year, float(np.nanmean(arr)))

    _update_manifest_zones("CAN", zones_out,
                           {"source": "IESO"}, cache_dir)
    return zones_out


# ── Batch runner ───────────────────────────────────────────────────────────


def run_all_phase1(download_era5: bool = True) -> dict:
    """Run the three Phase-1 greenfield fetchers."""
    logger.info("Running Taiwan zonal fetcher")
    twn = fetch_taiwan_zonal(download_era5=download_era5)
    logger.info("Running Thailand zonal fetcher")
    tha = fetch_thailand_zonal(download_era5=download_era5)
    logger.info("Running China zonal fetcher")
    chn = fetch_china_zonal(download_era5=download_era5)
    return {"TWN": twn, "THA": tha, "CHN": chn}


def run_phase2_bra(download_era5: bool = True) -> dict:
    """Run the Phase-2 Brazil ONS subsystem migration."""
    logger.info("Running Brazil ONS zonal migration")
    return {"BRA": fetch_brazil_zonal(download_era5=download_era5)}


def run_phase2b(
    years: range = range(2015, 2026),
    download_era5: bool = True,
) -> dict:
    """Run Phase 2b migrations (AEMO NEM + IESO Ontario)."""
    logger.info("Running AEMO NEM zonal migration")
    aus = fetch_aemo_nem_zonal(years=years, download_era5=download_era5)
    logger.info("Running IESO Ontario zonal migration")
    ieso = fetch_ieso_ontario_zonal(years=years, download_era5=download_era5)
    return {"AUS_NEM": aus, "CAN_IESO": ieso}
