"""Global electricity demand projection (2025-2050).

Projects hourly electricity demand for all countries using the trained
ML model (XGBoost or TFT) and macroeconomic/climate inputs.

Usage:
    from esfex.models.demand_projection import GlobalDemandProjector
    projector = GlobalDemandProjector(ssp="SSP2")
    projector.run(output_dir=Path("output/"))

Or via CLI:
    esfex project-global-demand --ssp SSP2
"""

from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from esfex.paths import PROJECT_DATA as _DEFAULT_DATA_DIR, OUTPUT_DIR as _DEFAULT_OUTPUT_DIR

logger = logging.getLogger(__name__)


# ── SSP growth data (imported from fetchers or bundled) ─────────────────

_SSP_GDP_MULTIPLIERS = {
    "SSP1": {2025: 1.12, 2030: 1.29, 2035: 1.49, 2040: 1.72, 2045: 1.99, 2050: 2.30},
    "SSP2": {2025: 1.10, 2030: 1.24, 2035: 1.40, 2040: 1.58, 2045: 1.79, 2050: 2.03},
    "SSP3": {2025: 1.06, 2030: 1.14, 2035: 1.22, 2040: 1.31, 2045: 1.41, 2050: 1.52},
    "SSP4": {2025: 1.09, 2030: 1.21, 2035: 1.35, 2040: 1.50, 2045: 1.68, 2050: 1.87},
    "SSP5": {2025: 1.14, 2030: 1.36, 2035: 1.62, 2040: 1.93, 2045: 2.30, 2050: 2.74},
}

_SSP_POP_MULTIPLIERS = {
    "SSP1": {2025: 1.04, 2030: 1.07, 2035: 1.09, 2040: 1.11, 2045: 1.12, 2050: 1.12},
    "SSP2": {2025: 1.05, 2030: 1.10, 2035: 1.15, 2040: 1.20, 2045: 1.25, 2050: 1.29},
    "SSP3": {2025: 1.07, 2030: 1.14, 2035: 1.21, 2040: 1.28, 2045: 1.36, 2050: 1.49},
    "SSP4": {2025: 1.05, 2030: 1.10, 2035: 1.16, 2040: 1.21, 2045: 1.26, 2050: 1.32},
    "SSP5": {2025: 1.04, 2030: 1.07, 2035: 1.09, 2040: 1.11, 2045: 1.12, 2050: 1.14},
}


def _interpolate_5yr_to_annual(data_5yr: dict[int, float]) -> dict[int, float]:
    """Linearly interpolate 5-year interval data to annual resolution."""
    years = sorted(data_5yr.keys())
    result = {}
    for i in range(len(years) - 1):
        y0, y1 = years[i], years[i + 1]
        v0, v1 = data_5yr[y0], data_5yr[y1]
        for y in range(y0, y1 + 1):
            t = (y - y0) / (y1 - y0)
            result[y] = v0 + t * (v1 - v0)
    return result


def _levels_to_growth_rates(levels: dict[int, float]) -> dict[int, float]:
    """Convert absolute multiplier levels to year-on-year growth rates."""
    years = sorted(levels.keys())
    rates = {}
    for i in range(1, len(years)):
        rates[years[i]] = (levels[years[i]] / levels[years[i - 1]]) - 1.0
    return rates


def _ssp_growth_rates(
    ssp: str, kind: str = "gdp",
) -> dict[int, float]:
    """Get annual growth rates for an SSP scenario."""
    mults = _SSP_GDP_MULTIPLIERS if kind == "gdp" else _SSP_POP_MULTIPLIERS
    if ssp not in mults:
        raise ValueError(f"Unknown SSP: {ssp}. Choose from {list(mults)}")
    annual = _interpolate_5yr_to_annual(mults[ssp])
    return _levels_to_growth_rates(annual)


# ── Annual demand trajectory ────────────────────────────────────────────

def compute_annual_trajectory(
    base_gwh: float,
    base_year: int,
    end_year: int,
    gdp_growth_by_year: dict[int, float],
    elasticity: float = 0.8,
    efficiency_rate: float = 0.005,
    electrification_rate: float = 0.01,
    efficiency_saturation: float = 0.50,
    electrification_saturation: float = 1.0,
) -> dict[int, float]:
    """Project annual electricity demand with logistic saturation.

    Returns {year: annual_gwh} from base_year to end_year inclusive.
    """
    trajectory = {base_year: base_gwh}
    eff_level = 0.0
    elec_level = 0.0
    current = base_gwh

    for year in range(base_year + 1, end_year + 1):
        gdp_g = gdp_growth_by_year.get(year, 0.02)

        # Logistic saturation
        eff_r = efficiency_rate * (1.0 - eff_level / efficiency_saturation)
        eff_r = max(eff_r, 0.0)
        eff_level += eff_r

        elec_r = electrification_rate * (1.0 - elec_level / electrification_saturation)
        elec_r = max(elec_r, 0.0)
        elec_level += elec_r

        factor = (1.0 + gdp_g * elasticity) * (1.0 - eff_r) * (1.0 + elec_r)
        current *= factor
        trajectory[year] = current

    return trajectory


def _gdp_elasticity(gdp_per_capita: float) -> float:
    """Development-dependent demand-GDP elasticity.

    High GDP → low elasticity (~0.3 for OECD).
    Low GDP → high elasticity (~1.0 for developing).
    """
    if gdp_per_capita <= 0:
        return 0.8
    return max(0.2, min(1.2, 1.4 - 0.12 * math.log10(gdp_per_capita)))


# ── CMIP6 temperature projections ──────────────────────────────────────

def _daily_to_hourly_temperature(
    tmin: np.ndarray, tmax: np.ndarray, n_days: int,
) -> np.ndarray:
    """Convert daily Tmin/Tmax to hourly using sinusoidal interpolation.

    Assumes Tmin at 06:00, Tmax at 14:00 (standard diurnal cycle).
    Returns (n_days * 24,) array.
    """
    hourly = np.empty(n_days * 24, dtype=np.float64)
    for d in range(n_days):
        tmn = tmin[d]
        tmx = tmax[d]
        tavg = (tmn + tmx) / 2.0
        amp = (tmx - tmn) / 2.0
        for h in range(24):
            # Phase: min at 6h, max at 14h → period 24h, offset 14h
            angle = 2.0 * np.pi * (h - 14.0) / 24.0
            hourly[d * 24 + h] = tavg + amp * np.cos(angle)
    return hourly


def fetch_cmip6_temperature(
    lat: float, lon: float,
    start_year: int, end_year: int,
    cache_dir: Path,
    model: str = "CMCC_CM2_VHR4",
) -> Optional[dict[int, np.ndarray]]:
    """Download CMIP6 daily temperature and convert to hourly.

    Uses Open-Meteo Climate API (same provider as ERA5).
    Returns {year: hourly_temperature(8760)} or None on failure.
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"cmip6_{model}_{lat:.2f}_{lon:.2f}_{start_year}_{end_year}.npz"

    if cache_file.exists():
        data = np.load(cache_file)
        return {int(k): data[k] for k in data.files}

    url = (
        f"https://climate-api.open-meteo.com/v1/climate?"
        f"latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={start_year}-01-01&end_date={end_year}-12-31"
        f"&models={model}"
        f"&daily=temperature_2m_max,temperature_2m_min"
    )
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning("CMIP6 failed for (%.2f, %.2f): %s", lat, lon, e)
        return None

    daily = payload.get("daily", {})
    times = daily.get("time", [])
    tmax_raw = daily.get("temperature_2m_max", [])
    tmin_raw = daily.get("temperature_2m_min", [])

    if not times:
        return None

    # Group by year
    result = {}
    year_days: dict[int, list] = {}
    for i, t in enumerate(times):
        yr = int(t[:4])
        year_days.setdefault(yr, []).append(i)

    for yr in range(start_year, end_year + 1):
        indices = year_days.get(yr, [])
        n = len(indices)
        if n < 360:
            logger.warning("CMIP6 year %d: only %d days, skipping", yr, n)
            continue

        tmn = np.array([tmin_raw[i] if tmin_raw[i] is not None
                        else 15.0 for i in indices])
        tmx = np.array([tmax_raw[i] if tmax_raw[i] is not None
                        else 25.0 for i in indices])

        hourly = _daily_to_hourly_temperature(tmn, tmx, n)

        # Pad/trim to 8760
        if len(hourly) < 8760:
            hourly = np.pad(hourly, (0, 8760 - len(hourly)), mode="edge")
        hourly = hourly[:8760]

        # Replace NaN
        mask = np.isnan(hourly)
        if mask.any():
            hourly[mask] = np.nanmean(hourly)

        result[yr] = hourly

    # Cache
    np.savez_compressed(cache_file, **{str(k): v for k, v in result.items()})
    return result


# ── ERA5 temperature download ──────────────────────────────────────────

def _fetch_era5_temperature(
    lat: float, lon: float, year: int, cache_dir: Path,
) -> Optional[np.ndarray]:
    """Download hourly ERA5 temperature for one location/year.

    Returns (8760,) array in °C, or None on failure.
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"era5_{lat:.2f}_{lon:.2f}_{year}.npy"

    if cache_file.exists():
        return np.load(cache_file)

    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={year}-01-01&end_date={year}-12-31"
        f"&hourly=temperature_2m&timezone=UTC"
    )
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        temps = data["hourly"]["temperature_2m"]
        arr = np.array(temps, dtype=np.float64)
        # Pad/trim to exactly 8760
        if len(arr) < 8760:
            arr = np.pad(arr, (0, 8760 - len(arr)), mode="edge")
        arr = arr[:8760]
        # Replace NaN with mean
        mask = np.isnan(arr)
        if mask.any():
            arr[mask] = np.nanmean(arr)
        np.save(cache_file, arr)
        return arr
    except Exception as e:
        logger.warning("ERA5 failed for (%.2f, %.2f) %d: %s", lat, lon, year, e)
        return None


# ── Main projector ──────────────────────────────────────────────────────

class GlobalDemandProjector:
    """Batch projector for all countries, 2025-2050."""

    def __init__(
        self,
        ssp: str = "SSP2",
        base_year: int = 2025,
        end_year: int = 2050,
        data_dir: Path = _DEFAULT_DATA_DIR,
        ml_engine: str = "tft",
        era5_workers: int = 8,
        era5_reference_year: int = 2023,
    ):
        self.ssp = ssp
        self.base_year = base_year
        self.end_year = end_year
        self.data_dir = data_dir
        self.ml_engine = ml_engine
        self.era5_workers = era5_workers
        self.era5_reference_year = era5_reference_year

        self.n_years = end_year - base_year + 1
        self.gdp_growth = _ssp_growth_rates(ssp, "gdp")
        self.pop_growth = _ssp_growth_rates(ssp, "pop")

        self.cmip6_model = "CMCC_CM2_VHR4"
        self._model = None

    def _load_model(self):
        if self._model is None:
            if self.ml_engine == "tft":
                # Use the dedicated inference backend (sliding-window TFT
                # annual projection) instead of the incomplete
                # DemandTFTModel.predict_hourly stub.
                from esfex.models.tft_inference import TFTSensitivityBackend
                self._model = TFTSensitivityBackend()
                logger.info("Loaded ML model (engine=tft, TFTSensitivityBackend)")
            else:
                from esfex.models.demand_ml import DemandMLModel
                self._model = DemandMLModel.load_bundled(engine=self.ml_engine)
                logger.info("Loaded ML model (engine=%s)", self.ml_engine)

    def download_temperatures(
        self,
        registry: dict,
        progress_cb=None,
    ) -> None:
        """Download CMIP6 projected temperatures for all countries.

        Falls back to ERA5 (historical) if CMIP6 fails.
        """
        cmip6_dir = self.data_dir / "cmip6"
        era5_dir = self.data_dir / "era5"
        cmip6_dir.mkdir(parents=True, exist_ok=True)
        era5_dir.mkdir(parents=True, exist_ok=True)

        # Filter to countries that need CMIP6 downloading
        tasks = []
        for iso3, rec in registry.items():
            cache_file = (cmip6_dir /
                          f"cmip6_{self.cmip6_model}_{rec.latitude:.2f}"
                          f"_{rec.longitude:.2f}_{self.base_year}_{self.end_year}.npz")
            if not cache_file.exists():
                tasks.append((iso3, rec.latitude, rec.longitude))

        if not tasks:
            logger.info("All CMIP6 temperatures already cached.")
            return

        logger.info("Downloading CMIP6 (%s) for %d countries...",
                     self.cmip6_model, len(tasks))
        done = 0

        for iso3, lat, lon in tasks:
            fetch_cmip6_temperature(
                lat, lon, self.base_year, self.end_year,
                cmip6_dir, model=self.cmip6_model,
            )
            done += 1
            if done % 10 == 0:
                logger.info("  CMIP6: %d/%d", done, len(tasks))
            time.sleep(0.5)  # rate limit

        logger.info("CMIP6 download complete.")

        # Also ensure ERA5 baseline exists (for countries where CMIP6 fails)
        era5_tasks = []
        for iso3, rec in registry.items():
            cache_file = era5_dir / f"era5_{rec.latitude:.2f}_{rec.longitude:.2f}_{self.era5_reference_year}.npy"
            if not cache_file.exists():
                era5_tasks.append((iso3, rec.latitude, rec.longitude))

        if era5_tasks:
            logger.info("Downloading ERA5 fallback for %d countries...", len(era5_tasks))
            for iso3, lat, lon in era5_tasks:
                _fetch_era5_temperature(lat, lon, self.era5_reference_year, era5_dir)
                time.sleep(0.3)

    def _get_temperature(self, rec) -> dict[int, np.ndarray]:
        """Load temperature data for a country.

        Returns {year: hourly_8760} dict. Tries CMIP6 first,
        falls back to ERA5 (repeated for all years).
        """
        cmip6_dir = self.data_dir / "cmip6"
        cache_file = (cmip6_dir /
                      f"cmip6_{self.cmip6_model}_{rec.latitude:.2f}"
                      f"_{rec.longitude:.2f}_{self.base_year}_{self.end_year}.npz")

        if cache_file.exists():
            data = np.load(cache_file)
            result = {int(k): data[k] for k in data.files}
            if len(result) >= self.n_years - 1:
                return result
            # Partial — fill gaps with ERA5
            logger.warning("%s: CMIP6 partial (%d/%d years), filling with ERA5",
                           rec.iso3, len(result), self.n_years)

        # Fallback: ERA5 repeated for all years
        era5_dir = self.data_dir / "era5"
        era5_file = era5_dir / f"era5_{rec.latitude:.2f}_{rec.longitude:.2f}_{self.era5_reference_year}.npy"
        if era5_file.exists():
            era5 = np.load(era5_file)
        else:
            logger.warning("%s: no temperature data, using 20°C fallback", rec.iso3)
            era5 = np.full(8760, 20.0)

        # Build full dict, using CMIP6 where available, ERA5 elsewhere
        result = {}
        cmip6_data = {}
        if cache_file.exists():
            data = np.load(cache_file)
            cmip6_data = {int(k): data[k] for k in data.files}

        for year in range(self.base_year, self.end_year + 1):
            result[year] = cmip6_data.get(year, era5)

        return result

    def project_country(self, rec, temperatures: dict[int, np.ndarray]) -> dict:
        """Project hourly demand for one country.

        Args:
            rec: CountryRecord with baseline data
            temperatures: {year: hourly_temperature(8760)} per year

        Returns dict with:
          - 'hourly': dict[int, np.ndarray] — {year: demand_mw(8760)}
          - 'annual_gwh': dict[int, float]
          - 'peak_mw': dict[int, float]
        """
        from esfex.models.demand_ml import build_hourly_features

        self._load_model()

        # Elasticity based on development level
        elasticity = _gdp_elasticity(rec.gdp_per_capita)

        # GDP growth: use SSP global rates (could be refined per-country)
        gdp_growth = dict(self.gdp_growth)

        # Population growth: prefer UN WPP if available
        pop_growth = {}
        if rec.pop_projections:
            years_sorted = sorted(rec.pop_projections.keys())
            for i in range(1, len(years_sorted)):
                y = years_sorted[i]
                p_prev = rec.pop_projections[years_sorted[i - 1]]
                p_curr = rec.pop_projections[y]
                if p_prev > 0:
                    pop_growth[y] = (p_curr / p_prev) - 1.0
        if not pop_growth:
            pop_growth = dict(self.pop_growth)

        # Annual trajectory
        annual = compute_annual_trajectory(
            base_gwh=rec.annual_gwh,
            base_year=self.base_year,
            end_year=self.end_year,
            gdp_growth_by_year=gdp_growth,
            elasticity=elasticity,
        )

        # Build features and predict year by year (each year has its own temperature)
        hourly = {}
        peaks = {}

        # Track evolving GDP/pop for feature construction
        gdp = rec.gdp_per_capita
        pop = rec.population

        for year in range(self.base_year, self.end_year + 1):
            # Get temperature for this specific year
            temp = temperatures.get(year, temperatures.get(self.base_year,
                                    np.full(8760, 20.0)))

            if self.ml_engine == "tft":
                # TFT takes per-year country static features + 8760h
                # temperature, predicts shape factors directly. The
                # climate multiplier is applied by the sensitivity
                # elsewhere — here raw shape already reflects climate.
                log_gdp = math.log(max(gdp, 1.0))
                log_pop = math.log(max(pop, 1.0))
                raw_sf = self._model.predict_annual_shape(
                    iso3=rec.iso3,
                    lat=rec.latitude,
                    lon=rec.longitude,
                    log_gdp_per_cap=log_gdp,
                    log_pop_density=log_pop,
                    urbanization=rec.urbanization_pct,
                    temperature_hourly=temp,
                    year=year,
                )
            else:
                # Legacy XGBoost path.
                features = build_hourly_features(
                    gdp_per_capita=gdp,
                    population=pop,
                    urbanization_pct=rec.urbanization_pct,
                    electricity_access_pct=rec.electricity_access_pct,
                    temperature_hourly=temp,
                    latitude=rec.latitude,
                    longitude=rec.longitude,
                    base_year=year,
                    simulation_years=1,
                )
                raw_sf = self._model.predict_raw(features)
            raw_mean = raw_sf.mean()

            # Normalize shape for hourly distribution, but keep the
            # climate multiplier separate
            shape_factors = raw_sf / raw_mean if raw_mean > 0 else raw_sf

            # Scale by annual trajectory × climate multiplier
            annual_gwh = annual[year] * raw_mean
            annual_avg_mw = annual_gwh * 1000.0 / 8760.0
            demand_mw = shape_factors * annual_avg_mw
            hourly[year] = demand_mw.astype(np.float32)
            peaks[year] = float(demand_mw.max())
            annual[year] = annual_gwh  # update with climate correction

            # Evolve GDP/pop for next year
            gdp *= (1.0 + gdp_growth.get(year + 1, 0.02))
            pop *= (1.0 + pop_growth.get(year + 1, 0.01))

        return {"hourly": hourly, "annual_gwh": annual, "peak_mw": peaks}

    def run(
        self,
        registry: dict,
        output_dir: Path = _DEFAULT_OUTPUT_DIR,
        countries: Optional[list[str]] = None,
    ) -> Path:
        """Run full projection pipeline.

        Args:
            registry: from build_country_registry()
            output_dir: root output directory
            countries: ISO3 filter (None = all)
        """
        import pandas as pd

        output_dir.mkdir(parents=True, exist_ok=True)
        self._load_model()

        # Filter countries
        if countries:
            targets = {k: v for k, v in registry.items() if k in countries}
        else:
            targets = {k: v for k, v in registry.items() if v.annual_gwh > 0}

        logger.info("Projecting %d countries, %s, %d-%d...",
                     len(targets), self.ssp, self.base_year, self.end_year)

        # Summary accumulator
        summary_rows = []
        n_done = 0

        for iso3, rec in targets.items():
            try:
                temperatures = self._get_temperature(rec)
                result = self.project_country(rec, temperatures)

                # Write per-year Parquet files
                country_dir = output_dir / iso3
                country_dir.mkdir(exist_ok=True)

                for year in range(self.base_year, self.end_year + 1):
                    demand_mw = result["hourly"][year]
                    ts_start = pd.Timestamp(f"{year}-01-01")
                    timestamps = pd.date_range(ts_start, periods=8760, freq="h")
                    df = pd.DataFrame({
                        "timestamp": timestamps,
                        "demand_mw": demand_mw,
                    })
                    pq_path = country_dir / f"{iso3}_{year}.parquet"
                    df.to_parquet(pq_path, index=False, engine="pyarrow")

                    annual_gwh = result["annual_gwh"][year]
                    peak_mw = result["peak_mw"][year]
                    avg_mw = annual_gwh * 1000 / 8760
                    lf = avg_mw / peak_mw if peak_mw > 0 else 0

                    summary_rows.append({
                        "iso3": iso3,
                        "year": year,
                        "annual_gwh": round(annual_gwh, 1),
                        "peak_mw": round(peak_mw, 1),
                        "avg_mw": round(avg_mw, 1),
                        "load_factor": round(lf, 4),
                        "data_quality": rec.data_quality,
                    })

                n_done += 1
                if n_done % 10 == 0 or n_done == len(targets):
                    logger.info("  Progress: %d/%d countries", n_done, len(targets))

            except Exception as e:
                logger.error("  %s FAILED: %s", iso3, e)
                continue

        # Write summary
        summary_df = pd.DataFrame(summary_rows)
        summary_path = output_dir / "summary_annual.parquet"
        summary_df.to_parquet(summary_path, index=False)
        logger.info("Summary: %s", summary_path)

        # Manifest
        manifest = {
            "ssp": self.ssp,
            "base_year": self.base_year,
            "end_year": self.end_year,
            "ml_engine": self.ml_engine,
            "n_countries": n_done,
            "total_countries_attempted": len(targets),
            "era5_reference_year": self.era5_reference_year,
        }
        with open(output_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        logger.info("Done. %d countries projected to %s", n_done, output_dir)
        return output_dir
