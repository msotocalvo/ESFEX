"""Gridded Energy Climate Vulnerability Index (ECVI).

Computes a spatially-resolved vulnerability metric at 0.25° resolution
combining climate hazard, demand sensitivity, population exposure,
and adaptive capacity.

ECVI(i,j) = H × η × PE × AC_inv   (normalized, 0-100)

Data sources:
  - NEX-GDDP-CMIP6: gridded warming (0.25°)
  - Wang et al. 2022: population projections (1km, SSP1-5)
  - Kummu et al.: GDP gridded (0.25°)
  - XGBoost model: demand sensitivity to temperature
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
import pandas as pd
from pathlib import Path
from typing import Optional

import numpy as np

from esfex.paths import GRIDDED_DIR as _DATA_DIR

logger = logging.getLogger(__name__)


@dataclass
class ECVIConfig:
    """Configuration for gridded ECVI computation."""

    cmip6_2025: Path = _DATA_DIR / "cmip6" / "tas_2025_ssp245.nc"
    cmip6_2050: Path = _DATA_DIR / "cmip6" / "tas_2050_ssp245.nc"
    gdp_2025: Path = _DATA_DIR / "gdp" / "025d" / "GDP2025_ssp2.tif"
    gdp_2050: Path = _DATA_DIR / "gdp" / "025d" / "GDP2050_ssp2.tif"
    pop_2025: Path = _DATA_DIR / "population_ssp2" / "SPP2" / "SSP2_2025.tif"
    pop_2050: Path = _DATA_DIR / "population_ssp2" / "SPP2" / "SSP2_2050.tif"
    output_dir: Path = _DATA_DIR / ".." / ".." / "output" / "ecvi"

    # ECVI component weights (geometric mean exponents, sum=1)
    w_hazard: float = 0.25
    w_sensitivity: float = 0.25
    w_exposure: float = 0.25
    w_capacity: float = 0.25


class GriddedECVI:
    """Compute ECVI on a global 0.25° grid."""

    def __init__(self, config: Optional[ECVIConfig] = None):
        self.cfg = config or ECVIConfig()
        # Target grid: 0.25° matching CMIP6
        self.res = 0.25
        self.lat = None  # set when loading CMIP6
        self.lon = None

    # ── Component 1: Climate Hazard (ΔT) ──────────────────────────────

    def _load_cmip6_nc(self, path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Load NEX-GDDP-CMIP6 NetCDF via h5py (more robust than netCDF4).

        Handles the _FillValue (1e20) used by NASA NEX-GDDP to mark
        ocean/missing pixels, converting them to NaN before averaging.

        Returns (tas_annual_mean_celsius, lat, lon).
        """
        import h5py

        with h5py.File(str(path), "r") as f:
            tas_var = f["tas"]
            tas = tas_var[:]  # (time, lat, lon) in K
            # Extract fill value from attributes
            fill = tas_var.attrs.get("_FillValue",
                   tas_var.attrs.get("missing_value", None))
            if fill is not None:
                fill_val = float(np.asarray(fill).flatten()[0])
            else:
                fill_val = 1e20
            lat = f["lat"][:]
            lon = f["lon"][:]

        # Replace fill values with NaN
        tas = np.where(tas >= 0.9 * fill_val, np.nan, tas)

        # Annual mean, convert K → °C
        tas_mean = np.nanmean(tas, axis=0) - 273.15
        return tas_mean, lat, lon

    def compute_climate_hazard(self) -> np.ndarray:
        """ΔT = mean_annual_T(2050) - mean_annual_T(2025) in °C.

        Returns (nlat, nlon) grid.
        """
        logger.info("Computing climate hazard (ΔT)...")

        tas_2025, lat, lon = self._load_cmip6_nc(self.cfg.cmip6_2025)
        tas_2050, _, _ = self._load_cmip6_nc(self.cfg.cmip6_2050)

        self.lat = lat
        self.lon = lon

        # Convert lon from 0-360 to -180/180
        if self.lon.max() > 180:
            self.lon = np.where(self.lon > 180, self.lon - 360, self.lon)
            sort_idx = np.argsort(self.lon)
            self.lon = self.lon[sort_idx]
            tas_2025 = tas_2025[:, sort_idx]
            tas_2050 = tas_2050[:, sort_idx]

        self.tas_2025 = tas_2025  # cache for sensitivity computation

        delta_t = tas_2050 - tas_2025
        delta_t = np.nan_to_num(delta_t, nan=0.0)

        logger.info("  ΔT: mean=%.2f°C, max=%.2f°C, shape=%s",
                     np.nanmean(delta_t), np.nanmax(delta_t), delta_t.shape)
        return delta_t

    # ── Component 2: Demand Sensitivity (η) ───────────────────────────

    def compute_demand_sensitivity(
        self, delta_t: np.ndarray,
        gdp_grid: np.ndarray,
        pop_grid: np.ndarray,
        engine: str = "tft",
    ) -> np.ndarray:
        """η = marginal demand response to +1°C at each pixel.

        Pixel-level climate (temperature, lat, lon) + country-level
        socioeconomics (GDP per capita, urbanization) per pixel.

        Parameters
        ----------
        engine : {"tft", "xgboost"}
            Backend model. "tft" (default) uses
            :class:`esfex.models.tft_inference.TFTSensitivityBackend`
            for sequence-based prediction (consistent with the paper's
            validation model). "xgboost" is the legacy single-point
            backend retained for comparison/debugging.

        Returns (nlat, nlon) grid in %/°C.
        """
        if engine == "tft":
            return self._compute_sensitivity_tft(delta_t, gdp_grid, pop_grid)
        return self._compute_sensitivity_xgboost(delta_t, gdp_grid, pop_grid)

    def _compute_sensitivity_tft(
        self, delta_t: np.ndarray,
        gdp_grid: np.ndarray,
        pop_grid: np.ndarray,
    ) -> np.ndarray:
        """TFT-based sensitivity: sequence model, 72/24h synthetic window."""
        logger.info("Computing demand sensitivity grid (TFT backend)...")

        from esfex.models.tft_inference import TFTSensitivityBackend

        tas_annual = self.tas_2025
        nlat, nlon = len(self.lat), len(self.lon)
        land_mask = (pop_grid > 0) & np.isfinite(tas_annual)

        # Country masks for socioeconomic assignment (reuses XGBoost helper).
        country_masks = self._build_country_masks_for_sensitivity()
        from esfex.paths import WORLDBANK_ALL as wb_path
        with open(wb_path) as f:
            wb_data = json.load(f)

        gdp_pc = np.full((nlat, nlon), 5000.0, dtype=np.float64)
        urban_pct = np.full((nlat, nlon), 50.0, dtype=np.float64)
        log_pop_country = np.full((nlat, nlon),
                                   math.log(1e7), dtype=np.float64)
        iso_grid = np.full((nlat, nlon), "", dtype=object)
        for iso3, mask in country_masks.items():
            ind = wb_data.get(iso3, {})
            if ind.get("gdp_per_capita", 0) > 0:
                gdp_pc[mask] = ind["gdp_per_capita"]
            if ind.get("population", 0) > 0:
                log_pop_country[mask] = math.log(max(ind["population"], 1))
            if ind.get("urbanization", 0) > 0:
                urban_pct[mask] = ind["urbanization"]
            iso_grid[mask] = iso3
        logger.info("  Assigned WB values to %d countries", len(country_masks))

        # Extract per-pixel inputs. build a flat DataFrame of land pixels.
        land_idx = np.argwhere(land_mask)
        n_px = len(land_idx)
        logger.info("  %d land pixels for TFT sensitivity", n_px)

        rows = []
        for ii, jj in land_idx:
            rows.append({
                "iso3": iso_grid[ii, jj] or "???",
                "lat": float(self.lat[ii]),
                "lon": float(self.lon[jj]),
                "log_gdp_per_cap": math.log(max(gdp_pc[ii, jj], 1.0)),
                "log_pop_density": log_pop_country[ii, jj],
                "urbanization": urban_pct[ii, jj],
            })
        pixels = pd.DataFrame(rows)
        temp_annual = np.asarray(
            [tas_annual[i, j] for i, j in land_idx], dtype=np.float64)
        temp_annual[~np.isfinite(temp_annual)] = 20.0

        backend = TFTSensitivityBackend()

        # Batch predict: chunk to stay under GPU memory.
        chunk = 1024
        sens_flat = np.zeros(n_px, dtype=np.float32)
        for start in range(0, n_px, chunk):
            end = min(start + chunk, n_px)
            logger.info("  pixels %d-%d / %d", start, end, n_px)
            sens_flat[start:end] = backend.sensitivity_at_pixels(
                pixels.iloc[start:end].reset_index(drop=True),
                temp_annual[start:end],
                delta_t=1.0,
            )

        sensitivity = np.zeros((nlat, nlon), dtype=np.float64)
        for k, (ii, jj) in enumerate(land_idx):
            sensitivity[ii, jj] = sens_flat[k]
        logger.info(
            "  TFT sensitivity range: %.2f to %.2f %%/°C",
            sens_flat.min(), sens_flat.max(),
        )
        return sensitivity

    def _compute_sensitivity_xgboost(
        self, delta_t: np.ndarray,
        gdp_grid: np.ndarray,
        pop_grid: np.ndarray,
    ) -> np.ndarray:
        """Legacy XGBoost backend (retained for comparison)."""
        import math

        logger.info("Computing demand sensitivity grid...")

        tas_annual = self.tas_2025  # °C, lon reordered

        # Load XGBoost model + country encoding map
        from esfex.models.demand_ml import FEATURE_COLS
        import xgboost as xgb
        from esfex.models.demand_ml import DemandMLModel
        from esfex.paths import MODELS_DIR
        model = DemandMLModel.load_bundled(engine="xgboost")
        booster = model._model

        _country_map_path = MODELS_DIR / "demand_xgb_country_map.json"
        country_to_id: dict = {}
        if _country_map_path.exists():
            with open(_country_map_path) as _f:
                country_to_id = json.load(_f)
        _unknown_id = float(len(country_to_id))  # unseen countries → out-of-range id

        # ── Load CMIP6 3hr-derived lag features (per mid-month day) ─────────
        # Produced by download_cmip6_subdaily.py. 8 lag/memory features at
        # 0.25° daily resolution. We sample mid-month days (15th) for each
        # representative time point in the sensitivity calculation.
        from esfex.paths import GRIDDED_CMIP6_DIR
        _lag_path = GRIDDED_CMIP6_DIR / "subdaily" / "tas_lags_2025_ssp245_GFDL-ESM4.nc"
        if not _lag_path.exists():
            raise FileNotFoundError(
                f"Subdaily lag features not found at {_lag_path}. "
                "Run download_cmip6_subdaily.py first."
            )
        logger.info("  Loading subdaily lag features: %s", _lag_path.name)
        import h5py as _h5
        _lag_feats = [
            "temp_1d_lag", "temp_7d_mean", "temp_30d_mean", "temp_trend_7d",
            "temp_daily_max", "temp_daily_min", "temp_diurnal_range",
        ]
        lag_by_month: dict[int, dict[str, np.ndarray]] = {}
        with _h5.File(_lag_path, "r") as _f:
            _lag_day_dates = _f["day"][:].astype(str)
            _lag_lon = _f["lon"][:]
            _lon_reorder = None
            if _lag_lon.max() > 180 and self.lon.min() < 0:
                shifted = np.where(_lag_lon > 180, _lag_lon - 360, _lag_lon)
                _lon_reorder = np.argsort(shifted)
            _month_to_day_idx = {}
            for m in [1, 3, 6, 9, 12]:
                tgt = f"-{m:02d}-15"
                idxs = np.where(np.char.find(_lag_day_dates, tgt) >= 0)[0]
                _month_to_day_idx[m] = int(idxs[0]) if len(idxs) else 0
            for m, d_idx in _month_to_day_idx.items():
                lag_by_month[m] = {}
                for fname in _lag_feats:
                    arr = _f[fname][d_idx].astype(np.float32)
                    if _lon_reorder is not None:
                        arr = arr[:, _lon_reorder]
                    lag_by_month[m][fname] = arr
        logger.info("  Loaded lag features for months %s",
                    sorted(_month_to_day_idx.keys()))

        nlat, nlon = len(self.lat), len(self.lon)
        # Valid pixels: have population AND valid CMIP6 temperature
        land_mask = (pop_grid > 0) & np.isfinite(tas_annual)

        # ── Build country-level GDP per capita and urbanization ──
        # Use country masks to assign each pixel the country's GDP_pc
        logger.info("  Building country masks for socioeconomic inputs...")
        country_masks = self._build_country_masks_for_sensitivity()

        # Load World Bank data
        from esfex.paths import WORLDBANK_ALL as wb_path
        with open(wb_path) as f:
            wb_data = json.load(f)

        # Per-pixel arrays initialized with global medians as defaults
        gdp_pc = np.full((nlat, nlon), 5000.0, dtype=np.float64)
        urban_pct = np.full((nlat, nlon), 50.0, dtype=np.float64)
        log_pop_country = np.full((nlat, nlon), math.log(1e7), dtype=np.float64)

        for iso3, mask in country_masks.items():
            indicators = wb_data.get(iso3, {})
            gdp_val = indicators.get("gdp_per_capita", 0)
            pop_val = indicators.get("population", 0)
            urb_val = indicators.get("urbanization", 50)

            if gdp_val > 0:
                gdp_pc[mask] = gdp_val
            if pop_val > 0:
                log_pop_country[mask] = math.log(max(pop_val, 1))
            if urb_val > 0:
                urban_pct[mask] = urb_val

        logger.info("  Assigned WB values to %d countries", len(country_masks))

        # Build pixel → country_id lookup for XGBoost feature
        iso_id_grid = np.full((nlat, nlon), _unknown_id, dtype=np.float64)
        for iso3, mask in country_masks.items():
            cid = country_to_id.get(iso3, _unknown_id)
            iso_id_grid[mask] = cid

        # 8 representative time points: 4 seasons × day/night
        sample_hours = [
            (3, 6, 2),    # Jan, 6AM, Wed (winter morning)
            (3, 14, 2),   # Jan, 2PM, Wed (winter afternoon)
            (6, 6, 2),    # Jun, 6AM, Wed (summer morning)
            (6, 14, 2),   # Jun, 2PM, Wed (summer afternoon)
            (9, 6, 5),    # Sep, 6AM, Sat (shoulder weekend)
            (9, 18, 5),   # Sep, 6PM, Sat (shoulder evening)
            (12, 6, 0),   # Dec, 6AM, Mon (winter morning)
            (12, 14, 0),  # Dec, 2PM, Mon (winter afternoon)
        ]

        sensitivity_accum = np.zeros((nlat, nlon), dtype=np.float64)

        for sample_idx, (month, hour, dow) in enumerate(sample_hours):
            h_sin = math.sin(2 * math.pi * hour / 24)
            h_cos = math.cos(2 * math.pi * hour / 24)
            d_sin = math.sin(2 * math.pi * dow / 7)
            d_cos = math.cos(2 * math.pi * dow / 7)
            m_sin = math.sin(2 * math.pi * month / 12)
            m_cos = math.cos(2 * math.pi * month / 12)
            is_weekend = 1.0 if dow >= 5 else 0.0

            # Pick closest month with lag features available
            lag_m = min(lag_by_month.keys(), key=lambda k: abs(k - month))
            lag_this = lag_by_month[lag_m]

            # Process in latitude bands
            band_size = 20
            for i0 in range(0, nlat, band_size):
                i1 = min(i0 + band_size, nlat)

                # Count land pixels in this band
                band_land = land_mask[i0:i1, :]
                land_idx = np.argwhere(band_land)
                if len(land_idx) == 0:
                    continue

                n_px = len(land_idx)
                # 29 features matching FEATURE_COLS (country_id first, +7 lag)
                features_base = np.zeros((n_px, 29), dtype=np.float64)
                features_warm = np.zeros((n_px, 29), dtype=np.float64)

                for k, (ii, jj) in enumerate(land_idx):
                    i_abs = i0 + ii
                    t = tas_annual[i_abs, jj]
                    if np.isnan(t):
                        t = 20.0

                    lgdp = math.log(max(gdp_pc[i_abs, jj], 1.0))
                    lpop = log_pop_country[i_abs, jj]
                    urb = urban_pct[i_abs, jj]

                    hdd = max(18.0 - t, 0.0)
                    cdd = max(t - 24.0, 0.0)
                    hdd_w = max(18.0 - (t + 1), 0.0)
                    cdd_w = max((t + 1) - 24.0, 0.0)

                    # Lag features at this pixel/month
                    t1d   = float(lag_this["temp_1d_lag"][i_abs, jj])
                    t7d   = float(lag_this["temp_7d_mean"][i_abs, jj])
                    t30d  = float(lag_this["temp_30d_mean"][i_abs, jj])
                    tr7d  = float(lag_this["temp_trend_7d"][i_abs, jj])
                    tmax  = float(lag_this["temp_daily_max"][i_abs, jj])
                    tmin  = float(lag_this["temp_daily_min"][i_abs, jj])
                    trng  = float(lag_this["temp_diurnal_range"][i_abs, jj])
                    if np.isnan(t1d):   t1d = t
                    if np.isnan(t7d):   t7d = t
                    if np.isnan(t30d):  t30d = t
                    if np.isnan(tr7d):  tr7d = 0.0
                    if np.isnan(tmax):  tmax = t + trng / 2 if not np.isnan(trng) else t + 2
                    if np.isnan(tmin):  tmin = t - trng / 2 if not np.isnan(trng) else t - 2
                    if np.isnan(trng):  trng = 4.0

                    features_base[k] = [
                        iso_id_grid[i_abs, jj],              # country_id
                        lgdp, lpop, urb,                      # socio
                        t, hdd, cdd,                          # thermal instant
                        hour, month, dow,                     # calendar
                        h_sin, h_cos, d_sin, d_cos, m_sin, m_cos,
                        is_weekend, 0.0, 7.0, 7.0,            # weekend + holiday
                        self.lat[i_abs], self.lon[jj],        # spatial
                        t1d, t7d, t30d, tr7d,                 # lag means
                        tmax, tmin, trng,                     # daily stats
                    ]
                    features_warm[k] = features_base[k].copy()
                    # Apply +ΔT uniformly to current T AND all T-derived lags
                    features_warm[k, 4]  = t + 1           # temperature
                    features_warm[k, 5]  = hdd_w
                    features_warm[k, 6]  = cdd_w
                    features_warm[k, 22] = t1d + 1         # temp_1d_lag
                    features_warm[k, 23] = t7d + 1         # temp_7d_mean
                    features_warm[k, 24] = t30d + 1        # temp_30d_mean
                    # temp_trend_7d (idx 25) and diurnal_range (idx 28) are
                    # differences → unchanged under uniform warming
                    features_warm[k, 26] = tmax + 1        # temp_daily_max
                    features_warm[k, 27] = tmin + 1        # temp_daily_min

                # Batch predict
                dm_b = xgb.DMatrix(features_base, feature_names=FEATURE_COLS)
                dm_w = xgb.DMatrix(features_warm, feature_names=FEATURE_COLS)
                pred_b = np.maximum(booster.predict(dm_b), 1e-6)
                pred_w = booster.predict(dm_w)
                eta = (pred_w - pred_b) / pred_b * 100

                for k, (ii, jj) in enumerate(land_idx):
                    sensitivity_accum[i0 + ii, jj] += eta[k]

            if sample_idx == 0:
                logger.info("  Sensitivity: sample 1/8 done")

        # Average over 8 samples
        sensitivity = sensitivity_accum / len(sample_hours)
        sensitivity[~land_mask] = 0

        logger.info("  η: mean=%.3f%%/°C, range=[%.3f, %.3f], land pixels=%d",
                     np.nanmean(sensitivity[land_mask]),
                     np.nanmin(sensitivity[land_mask]),
                     np.nanmax(sensitivity[land_mask]),
                     land_mask.sum())
        return sensitivity

    def _build_country_masks_for_sensitivity(self) -> dict[str, np.ndarray]:
        """Rasterize country polygons to the CMIP6 grid.

        Returns dict[iso3, bool_mask] where mask is shape (nlat, nlon).
        Cached on self for reuse.
        """
        if hasattr(self, "_country_masks_cache"):
            return self._country_masks_cache

        import cartopy.io.shapereader as shpreader
        from shapely.geometry import Point
        from shapely.prepared import prep

        shp_path = shpreader.natural_earth(
            resolution="110m", category="cultural", name="admin_0_countries"
        )
        reader = shpreader.Reader(shp_path)

        lon2d, lat2d = np.meshgrid(self.lon, self.lat)

        country_masks = {}
        for record in reader.records():
            iso3 = (record.attributes.get("ADM0_A3")
                    or record.attributes.get("ISO_A3", ""))
            if not iso3 or len(iso3) != 3:
                continue
            geom = record.geometry
            if geom is None:
                continue

            minx, miny, maxx, maxy = geom.bounds
            in_bbox = (lon2d >= minx) & (lon2d <= maxx) & \
                      (lat2d >= miny) & (lat2d <= maxy)
            if not in_bbox.any():
                continue

            prepared = prep(geom)
            mask = np.zeros(lat2d.shape, dtype=bool)
            rows, cols = np.where(in_bbox)
            for r, c in zip(rows, cols):
                if prepared.contains(Point(self.lon[c], self.lat[r])):
                    mask[r, c] = True
            if mask.any():
                country_masks[iso3] = mask

        self._country_masks_cache = country_masks
        return country_masks

    # ── Regridding helpers ──────────────────────────────────────────────

    def _regrid_025_to_cmip6(self, data_025: np.ndarray) -> np.ndarray:
        """Regrid a 720×1440 (lat -90..90, lon -180..180) array
        to the CMIP6 grid (600×1440, lat -59.88..89.88, lon -180..180).

        The GDP grid is 0.25° with origin at (-180, 90) covering full globe.
        CMIP6 starts at lat -59.875 (row 0). We need to extract the
        matching rows.
        """
        # GDP lat: 90, 89.75, ..., -89.75, -90  (720 rows, descending)
        # CMIP6 lat: -59.875, -59.625, ..., 89.875  (600 rows, ascending)
        nlat_cmip = len(self.lat)
        nlon_cmip = len(self.lon)

        # GDP grid covers -180 to 180, CMIP6 lon is already shifted to -180/180
        # Both have 1440 columns at 0.25° — lon alignment is direct

        # GDP lat is descending from +90: row i → lat = 90 - i*0.25 - 0.125
        # We need CMIP6 lat range: ~-59.875 to ~89.875
        # GDP row for lat L = (90 - L) / 0.25 = (90 - L) * 4
        result = np.zeros((nlat_cmip, nlon_cmip), dtype=np.float64)
        for i, target_lat in enumerate(self.lat):
            # Find nearest row in GDP grid
            src_row = int(round((90.0 - target_lat) / 0.25 - 0.5))
            src_row = max(0, min(src_row, data_025.shape[0] - 1))
            result[i, :] = data_025[src_row, :]

        return result

    def _aggregate_1km_to_025(self, src_path, operation="sum") -> np.ndarray:
        """Aggregate a 30 arc-sec (~1km) GeoTIFF to the CMIP6 0.25° grid.

        Uses proper block summation (for population) or averaging (for density).
        Only reads the portion that overlaps the CMIP6 lat range.
        """
        import rasterio
        from rasterio.windows import Window

        nlat_cmip = len(self.lat)
        nlon_cmip = len(self.lon)
        result = np.zeros((nlat_cmip, nlon_cmip), dtype=np.float64)

        with rasterio.open(src_path) as ds:
            # Source pixel size in degrees
            px_deg = abs(ds.transform.a)  # ~0.00833°
            block = int(round(0.25 / px_deg))  # ~30 pixels per 0.25° cell

            src_nlat, src_nlon = ds.shape

            # For each CMIP6 row, read the corresponding block of source pixels
            for i, target_lat in enumerate(self.lat):
                # Center of CMIP6 cell
                lat_top = target_lat + 0.125
                lat_bot = target_lat - 0.125

                # Source row for lat_top (source origin is at +90° or similar)
                src_origin_lat = ds.bounds.top
                row_top = int(round((src_origin_lat - lat_top) / px_deg))
                row_bot = int(round((src_origin_lat - lat_bot) / px_deg))

                row_top = max(0, row_top)
                row_bot = min(src_nlat, row_bot)

                if row_top >= row_bot or row_top >= src_nlat:
                    continue

                # Read this strip
                window = Window(0, row_top, src_nlon, row_bot - row_top)
                strip = ds.read(1, window=window).astype(np.float64)

                # Handle nodata
                nodata = ds.nodata
                if nodata is not None:
                    strip[strip == nodata] = 0
                strip[strip < 0] = 0
                strip = np.nan_to_num(strip, nan=0.0)

                # Aggregate columns: sum blocks of ~30 pixels along lon
                # Source lon: -180 to +180 (for GPW) — 43200 cols
                # CMIP6 lon: -180 to +180 — 1440 cols
                for j in range(nlon_cmip):
                    lon_left = self.lon[j] - 0.125
                    # Source col for lon_left
                    src_origin_lon = ds.bounds.left
                    col_left = int(round((lon_left - src_origin_lon) / px_deg))
                    col_right = col_left + block

                    col_left = max(0, col_left)
                    col_right = min(src_nlon, col_right)

                    if col_left >= col_right:
                        continue

                    block_data = strip[:, col_left:col_right]
                    if operation == "sum":
                        result[i, j] = block_data.sum()
                    else:
                        valid = block_data[block_data > 0]
                        result[i, j] = valid.mean() if len(valid) > 0 else 0

                if i % 100 == 0:
                    logger.info("    Aggregating: %d/%d rows", i, nlat_cmip)

        return result

    # ── Component 3: Population Exposure ──────────────────────────────

    def compute_population_exposure(self) -> np.ndarray:
        """Population at 0.25° resolution.

        Uses GPW v4 (SEDAC) which has correct georeference at 30 arc-sec.
        Aggregates by SUM to the CMIP6 grid.
        Returns (nlat, nlon) grid in people per 0.25° cell.
        """
        logger.info("Computing population exposure...")

        # Use GPW v4 (properly georeferenced) instead of SSP2 forecast
        from esfex.paths import POP_DENSITY_HIST_DIR
        gpw_path = POP_DENSITY_HIST_DIR / "gpw_v4_population_density_rev11_2020_30_sec_2020.tif"

        if gpw_path.exists():
            pop_grid = self._aggregate_1km_to_025(gpw_path, operation="sum")
        else:
            # Fallback to SSP2 forecast
            logger.warning("GPW v4 not found, using SSP2 forecast")
            pop_grid = self._aggregate_1km_to_025(self.cfg.pop_2025, operation="sum")

        logger.info("  Population grid: shape=%s, total=%.2fB, non-zero=%d",
                     pop_grid.shape, pop_grid.sum() / 1e9,
                     (pop_grid > 0).sum())
        return pop_grid

    # ── Component 4: Adaptive Capacity (inverse) ─────────────────────

    def compute_adaptive_capacity_inv(self) -> tuple[np.ndarray, np.ndarray]:
        """Inverse adaptive capacity from GDP grid.

        GDP grid (Kummu) is 720×1440 at 0.25° covering -90..90, -180..180.
        Regrids to match CMIP6 grid (600×1440, -59.88..89.88).

        Returns (ac_inv, gdp_grid). Higher ac_inv = more vulnerable.
        """
        import rasterio

        logger.info("Computing adaptive capacity (inverse GDP)...")

        with rasterio.open(self.cfg.gdp_2025) as ds:
            gdp_full = ds.read(1).astype(np.float64)
            nodata = ds.nodata
            if nodata is not None:
                gdp_full[gdp_full == nodata] = 0
            gdp_full[gdp_full < 0] = 0
            gdp_full = np.nan_to_num(gdp_full, nan=0.0)

        # Regrid from 720×1440 to CMIP6 600×1440
        gdp = self._regrid_025_to_cmip6(gdp_full)

        # Inverse: higher GDP = more adaptive capacity = less vulnerable
        gdp_median = np.median(gdp[gdp > 0]) if (gdp > 0).any() else 1.0
        ac_inv = gdp_median / (gdp + gdp_median * 0.01)
        ac_inv[gdp <= 0] = 0  # ocean/uninhabited

        logger.info("  AC_inv grid: shape=%s, GDP non-zero=%d",
                     ac_inv.shape, (gdp > 0).sum())
        return ac_inv, gdp

    # ── Composite ECVI ────────────────────────────────────────────────

    def _robust_normalize(self, arr: np.ndarray) -> np.ndarray:
        """Min-max normalize to [0,1], clipping at 5th/95th percentiles."""
        valid = arr[arr > 0]
        if len(valid) == 0:
            return np.zeros_like(arr)
        p5 = np.percentile(valid, 5)
        p95 = np.percentile(valid, 95)
        if p95 <= p5:
            return np.zeros_like(arr)
        clipped = np.clip(arr, p5, p95)
        normed = (clipped - p5) / (p95 - p5)
        normed[arr <= 0] = 0
        return normed

    def compute(self, engine: str = "tft") -> dict[str, np.ndarray]:
        """Compute all ECVI components and composite.

        Returns dict with keys: 'hazard', 'sensitivity', 'exposure',
        'capacity_inv', 'ecvi', plus 'lat', 'lon'.
        """
        # 1. Climate hazard
        hazard = self.compute_climate_hazard()

        # 2. Population exposure (needed before sensitivity)
        exposure = self.compute_population_exposure()

        # 3. Adaptive capacity / GDP (needed before sensitivity)
        ac_inv, gdp_grid = self.compute_adaptive_capacity_inv()

        # 4. Demand sensitivity (uses GDP and pop per pixel)
        sensitivity = self.compute_demand_sensitivity(hazard, gdp_grid, exposure,
                                                      engine=engine)
        # Use absolute value — both heating and cooling sensitivity are risks
        sensitivity_abs = np.abs(sensitivity)

        # Land mask: only pixels with population > 0
        land_mask = exposure > 0

        # Apply land mask to all components before normalization
        hazard[~land_mask] = 0
        sensitivity_abs[~land_mask] = 0
        ac_inv[~land_mask] = 0

        # Normalize each to [0, 1] using only land pixels
        H = self._robust_normalize(np.abs(hazard))
        S = self._robust_normalize(sensitivity_abs)
        PE = self._robust_normalize(exposure)
        AC = self._robust_normalize(ac_inv)

        # Composite: geometric mean (land only)
        w = self.cfg
        ecvi = np.zeros_like(H)
        ecvi[land_mask] = (
            np.power(np.maximum(H[land_mask], 1e-10), w.w_hazard)
            * np.power(np.maximum(S[land_mask], 1e-10), w.w_sensitivity)
            * np.power(np.maximum(PE[land_mask], 1e-10), w.w_exposure)
            * np.power(np.maximum(AC[land_mask], 1e-10), w.w_capacity)
        )
        # Scale to 0-100
        ecvi_land = ecvi[land_mask]
        ecvi_max = np.percentile(ecvi_land, 99) if len(ecvi_land) > 0 else 1
        ecvi[land_mask] = np.clip(ecvi_land / ecvi_max * 100, 0, 100)

        logger.info("ECVI computed: mean=%.1f, max=%.1f, >50: %d pixels",
                     ecvi[land_mask].mean() if land_mask.any() else 0,
                     ecvi.max(),
                     (ecvi > 50).sum())

        return {
            "hazard": hazard,
            "sensitivity": sensitivity,
            "exposure": exposure,
            "capacity_inv": ac_inv,
            "ecvi": ecvi,
            "lat": self.lat,
            "lon": self.lon,
        }

    def export_netcdf(self, result: dict, path: Path) -> None:
        """Save ECVI grid as NetCDF."""
        import xarray as xr

        path.parent.mkdir(parents=True, exist_ok=True)
        ds = xr.Dataset(
            {
                "ecvi": (["lat", "lon"], result["ecvi"]),
                "hazard": (["lat", "lon"], result["hazard"]),
                "sensitivity": (["lat", "lon"], result["sensitivity"]),
                "exposure": (["lat", "lon"], result["exposure"]),
                "capacity_inv": (["lat", "lon"], result["capacity_inv"]),
            },
            coords={"lat": result["lat"], "lon": result["lon"]},
        )
        ds.attrs["title"] = "Energy Climate Vulnerability Index (ECVI)"
        ds.attrs["resolution"] = "0.25 degrees"
        ds.attrs["source_cmip6"] = "GFDL-ESM4 SSP2-4.5"
        ds.to_netcdf(path)
        logger.info("Saved NetCDF: %s", path)
