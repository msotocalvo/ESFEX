"""Solar PV Potential Assessment engine with Multi-Criteria Decision Analysis.

Workflow:
1. Download ERA5 irradiance data via atlite -> compute PV capacity factors
2. Fetch terrain data (DEM -> elevation + slope)
3. Fetch LULC data (ESA WorldCover 2021)
4. Compute distance to existing grid (transmission lines)
5. Run MCDA (manual / entropy / PCA weighting)
6. Generate development zone polygons

Data sources:
- Solar irradiance: ERA5 reanalysis via atlite library
- Module database: pvlib CEC/NREL SAM (21,535+ modules)
- Terrain: Copernicus GLO-90 DEM (90m) via elevation package
- LULC: ESA WorldCover 2021 (10m GeoTIFF tiles from S3)
"""

from __future__ import annotations

import logging
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ESA WorldCover 2021 class codes -> default suitability scores for solar PV
DEFAULT_LULC_SCORES: dict[int, float] = {
    10: 0.1,   # Tree cover (shading)
    20: 0.5,   # Shrubland
    30: 0.9,   # Grassland (good for ground-mount)
    40: 0.6,   # Cropland (agrivoltaics possible)
    50: 0.0,   # Built-up
    60: 1.0,   # Bare / sparse vegetation (ideal)
    70: 0.0,   # Snow and ice
    80: 0.0,   # Permanent water bodies (ground default)
    90: 0.2,   # Herbaceous wetland
    95: 0.0,   # Mangroves
    100: 0.1,  # Moss and lichen
}

# Floating solar overrides
FLOATING_LULC_OVERRIDES: dict[int, float] = {
    80: 0.8,   # Water -> suitable for floating PV
    10: 0.0,   # Tree cover -> exclude
    50: 0.0,   # Built-up -> exclude
}


@dataclass
class ModuleSpec:
    """Technical specification of a PV module from the CEC/NREL SAM database."""

    key: str                # Full CEC key, e.g. "Canadian_Solar_Inc__CS6U_330P"
    manufacturer: str       # Extracted from key
    name: str               # Display name
    technology: str         # Mono-c-Si, Multi-c-Si, CdTe, CIGS, Thin Film, etc.
    stc_power_w: float      # STC rated power (W)
    ptc_power_w: float      # PTC power (W)
    area_m2: float          # Module area (m^2)
    efficiency: float       # STC/area/1000 (0-1)
    bifacial: bool
    v_oc: float             # Open-circuit voltage (V)
    i_sc: float             # Short-circuit current (A)
    v_mp: float             # Voltage at max power (V)
    i_mp: float             # Current at max power (A)
    gamma_pmax: float       # Temperature coefficient of power (%/C)
    t_noct: float           # NOCT (C)
    n_cells: int            # Number of cells in series
    length_m: float
    width_m: float


def load_module_database() -> list[ModuleSpec]:
    """Load CEC module database via pvlib.pvsystem.retrieve_sam('CECMod').

    Returns a list of ModuleSpec sorted by manufacturer then STC power.
    Typically ~21,535 modules from ~360 manufacturers.
    """
    import pvlib

    df = pvlib.pvsystem.retrieve_sam("CECMod")

    modules: list[ModuleSpec] = []
    for col_name in df.columns:
        try:
            mod = df[col_name]

            # Parse manufacturer from key (before double underscore)
            parts = col_name.split("__")
            manufacturer = parts[0].replace("_", " ") if len(parts) > 1 else "Unknown"

            # Parse display name (after double underscore)
            name = parts[1].replace("_", " ") if len(parts) > 1 else col_name.replace("_", " ")

            # Module area
            area = float(mod.get("A_c", 0) or 0)
            stc_w = float(mod.get("STC", 0) or 0)

            if stc_w <= 0:
                continue

            # Efficiency
            eff = stc_w / (area * 1000.0) if area > 0 else 0.0

            # Technology mapping
            tech_raw = str(mod.get("Technology", ""))
            tech = _normalize_technology(tech_raw)

            # Bifacial
            bifacial = bool(mod.get("Bifacial", 0))

            # Dimensions
            length = float(mod.get("Length", 0) or 0) / 1000.0  # mm -> m
            width = float(mod.get("Width", 0) or 0) / 1000.0

            modules.append(ModuleSpec(
                key=col_name,
                manufacturer=manufacturer,
                name=name,
                technology=tech,
                stc_power_w=stc_w,
                ptc_power_w=float(mod.get("PTC", 0) or 0),
                area_m2=area,
                efficiency=eff,
                bifacial=bifacial,
                v_oc=float(mod.get("V_oc_ref", 0) or 0),
                i_sc=float(mod.get("I_sc_ref", 0) or 0),
                v_mp=float(mod.get("V_mp_ref", 0) or 0),
                i_mp=float(mod.get("I_mp_ref", 0) or 0),
                gamma_pmax=float(mod.get("gamma_r", 0) or 0),
                t_noct=float(mod.get("T_NOCT", 45) or 45),
                n_cells=int(mod.get("N_s", 0) or 0),
                length_m=length,
                width_m=width,
            ))
        except Exception as exc:
            logger.debug("Skipping module %s: %s", col_name, exc)

    modules.sort(key=lambda m: (m.manufacturer.lower(), m.stc_power_w))
    return modules


def _normalize_technology(tech: str) -> str:
    """Normalize CEC technology string to readable form."""
    tech_lower = tech.lower().strip()
    if "mono" in tech_lower and "si" in tech_lower:
        return "Mono-c-Si"
    if "multi" in tech_lower and "si" in tech_lower:
        return "Multi-c-Si"
    if "cdte" in tech_lower or "cadmium" in tech_lower:
        return "CdTe"
    if "cigs" in tech_lower:
        return "CIGS"
    if "asi" in tech_lower or "amorphous" in tech_lower:
        return "a-Si"
    if "thin" in tech_lower:
        return "Thin Film"
    if tech:
        return tech
    return "Unknown"


@dataclass
class CriterionConfig:
    """Configuration for a single MCDA criterion."""

    enabled: bool = True
    weight: float = 0.2
    direction: str = "maximize"  # "maximize" or "minimize"


@dataclass
class MCDAConfig:
    """Multi-criteria decision analysis configuration."""

    method: str = "manual"  # "manual" | "entropy" | "pca"
    criteria: dict[str, CriterionConfig] = field(default_factory=lambda: {
        "capacity_factor": CriterionConfig(True, 0.40, "maximize"),
        "slope": CriterionConfig(True, 0.20, "minimize"),
        "elevation": CriterionConfig(True, 0.05, "minimize"),
        "lulc_score": CriterionConfig(True, 0.20, "maximize"),
        "dist_grid_km": CriterionConfig(True, 0.15, "minimize"),
    })
    lulc_scores: dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_LULC_SCORES),
    )


@dataclass
class SolarPVConfig:
    """User-configurable solar PV assessment parameters."""

    module_key: str = ""
    module_efficiency: float = 0.20
    module_gamma_pmax: float = -0.40  # %/C temperature coefficient
    module_stc_w: float = 400.0
    module_t_noct: float = 45.0  # NOCT (C)
    orientation: str = "latitude_optimal"  # "latitude_optimal" | "custom"
    tilt: float = 0.0       # degrees (used when orientation=custom)
    azimuth: float = 180.0  # degrees south-facing (used when orientation=custom)
    tracking: str = "none"   # "none" | "horizontal" | "vertical" | "dual"
    installation: str = "ground"  # "ground" | "floating"
    year: int = 2022
    grid_resolution: float = 0.25  # degrees
    min_capacity_factor: float = 0.15
    zone_buffer_km: float = 5.0
    module_capacity_kw: float = 0.4  # kW per module for capacity estimation
    data_source: str = "open_meteo"  # "open_meteo" | "nasa_power" | "era5_atlite"
    parallel_workers: int = 0  # 0 = auto (cpu_count)

    @property
    def effective_workers(self) -> int:
        import os
        if self.parallel_workers > 0:
            return self.parallel_workers
        return os.cpu_count() or 4


@dataclass
class HourlyIrradianceData:
    """Hourly irradiance and temperature for one grid cell."""

    timestamps: list[str]
    ghi: Any       # np.ndarray W/m²
    temperature: Any  # np.ndarray °C


@dataclass
class SolarPVAnalysisSummary:
    """Aggregated solar PV assessment results."""

    total_cells: int
    feasible_cells: int
    cf_min: float
    cf_max: float
    cf_avg: float
    ghi_avg: float  # kWh/m2/yr
    mcda_score_min: float
    mcda_score_max: float
    total_capacity_mw: float
    computed_weights: dict[str, float] = field(default_factory=dict)
    results_gdf: Any = None  # GeoDataFrame
    hourly_data: Any = None  # dict[(lat,lon)] -> HourlyIrradianceData


class SolarPVAnalyzer(QThread):
    """Run solar PV resource assessment in background.

    Phases:
    1. ERA5 download + PV capacity factor computation (0-30%)
    2. DEM -> elevation + slope (30-50%)
    3. LULC suitability (50-65%)
    4. Distance to grid (65-70%)
    5. MCDA scoring (70-90%)
    6. Zone generation (90-100%)
    """

    progress = Signal(int, str)  # percent, message
    finished = Signal(object)    # SolarPVAnalysisSummary
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        solar_config: SolarPVConfig,
        mcda_config: MCDAConfig,
        transmission_lines: list | None = None,
        parent=None,
        polygon: list | None = None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self.solar_config = solar_config
        self.mcda_config = mcda_config
        self.transmission_lines = transmission_lines or []
        # Optional precise domain: the bbox drives the fetch, the polygon
        # (from a drawn boundary or imported GeoAsset) clips the eval grid.
        self._polygon = polygon or []
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            result = self._analyze()
            if not self._cancelled:
                self.finished.emit(result)
        except Exception as exc:
            logger.exception("SolarPVAnalyzer error")
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------

    def _analyze(self) -> SolarPVAnalysisSummary:
        import geopandas as gpd
        import numpy as np
        from shapely.geometry import Point

        cfg = self.solar_config
        mcda = self.mcda_config

        # -- Phase 1: ERA5 solar capacity factors via atlite --
        self.progress.emit(2, "Creating atlite cutout for ERA5 irradiance data...")

        mean_cf, ghi_annual, cf_lats, cf_lons = self._compute_capacity_factors()
        if self._cancelled:
            return self._empty_summary()

        # Build evaluation grid. When a precise domain polygon is set, drop
        # grid cells outside it so results match the exact boundary, not the
        # bounding box.
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            _point_in_polygon,
        )
        grid_points: list[dict] = []
        for i, lat in enumerate(cf_lats):
            for j, lon in enumerate(cf_lons):
                if self._polygon and not _point_in_polygon(
                        float(lat), float(lon), self._polygon):
                    continue
                cf_val = float(mean_cf[i, j])
                ghi_val = float(ghi_annual[i, j]) if ghi_annual is not None else 0.0
                if not np.isnan(cf_val):
                    grid_points.append({
                        "lat": float(lat),
                        "lon": float(lon),
                        "capacity_factor": cf_val,
                        "ghi_kwh_m2": ghi_val,
                    })

        if not grid_points:
            self.progress.emit(100, "No valid grid points in domain")
            return self._empty_summary()

        self.progress.emit(
            30, f"Computed CF for {len(grid_points)} grid points"
        )
        if self._cancelled:
            return self._empty_summary()

        # -- Phase 2: Terrain (elevation + slope) --
        self.progress.emit(32, "Fetching terrain data (elevation + slope)...")
        try:
            elevations, slopes = self._fetch_terrain(grid_points)
        except Exception as exc:
            logger.warning("Terrain fetch failed: %s. Using defaults.", exc)
            elevations = [0.0] * len(grid_points)
            slopes = [0.0] * len(grid_points)

        for i, pt in enumerate(grid_points):
            pt["elevation"] = elevations[i]
            pt["slope"] = slopes[i]

        if self._cancelled:
            return self._empty_summary()
        self.progress.emit(50, "Terrain data processed")

        # -- Phase 3: LULC suitability --
        self.progress.emit(52, "Fetching LULC data...")
        try:
            lulc_scores = self._fetch_lulc(grid_points)
        except Exception as exc:
            logger.warning("LULC fetch failed: %s. Using defaults.", exc)
            lulc_scores = [0.5] * len(grid_points)

        for i, pt in enumerate(grid_points):
            pt["lulc_score"] = lulc_scores[i]

        if self._cancelled:
            return self._empty_summary()
        self.progress.emit(65, "LULC data processed")

        # -- Phase 4: Distance to grid --
        self.progress.emit(67, "Computing distance to transmission grid...")
        for pt in grid_points:
            pt["dist_grid_km"] = self._compute_dist_to_grid(
                pt["lat"], pt["lon"],
            )

        if self._cancelled:
            return self._empty_summary()
        self.progress.emit(70, "Grid distance computed")

        # -- Phase 5: MCDA scoring --
        self.progress.emit(72, f"Running MCDA ({mcda.method} weighting)...")

        enabled_criteria = {
            name: c for name, c in mcda.criteria.items() if c.enabled
        }

        # Build criteria matrix
        criteria_names = list(enabled_criteria.keys())
        n_cells = len(grid_points)
        n_criteria = len(criteria_names)

        raw_matrix = np.zeros((n_cells, n_criteria))
        for j, name in enumerate(criteria_names):
            for i, pt in enumerate(grid_points):
                raw_matrix[i, j] = pt.get(name, 0.0)

        # Normalize to [0, 1] with min-max
        norm_matrix = np.zeros_like(raw_matrix)
        for j in range(n_criteria):
            col = raw_matrix[:, j]
            col_min, col_max = col.min(), col.max()
            if col_max - col_min > 1e-10:
                norm_matrix[:, j] = (col - col_min) / (col_max - col_min)
            else:
                norm_matrix[:, j] = 0.5

            # Invert for "minimize" criteria
            if enabled_criteria[criteria_names[j]].direction == "minimize":
                norm_matrix[:, j] = 1.0 - norm_matrix[:, j]

        # Compute weights
        if mcda.method == "entropy":
            weights = self._entropy_weights(norm_matrix)
        elif mcda.method == "pca":
            weights = self._pca_weights(norm_matrix)
        else:
            raw_w = np.array([
                enabled_criteria[name].weight for name in criteria_names
            ])
            total_w = raw_w.sum()
            weights = raw_w / total_w if total_w > 0 else np.ones(n_criteria) / n_criteria

        computed_weights = {
            name: float(weights[j]) for j, name in enumerate(criteria_names)
        }

        # Composite score
        scores = norm_matrix @ weights
        for i, pt in enumerate(grid_points):
            pt["mcda_score"] = float(scores[i])

        self.progress.emit(90, "MCDA scoring complete")

        # -- Build GeoDataFrame --
        for pt in grid_points:
            pt["geometry"] = Point(pt["lon"], pt["lat"])

        results_gdf = gpd.GeoDataFrame(grid_points, crs="EPSG:4326")

        # Filter feasible
        feasible_mask = results_gdf["capacity_factor"] >= cfg.min_capacity_factor
        feasible = results_gdf[feasible_mask]

        cf_vals = results_gdf["capacity_factor"]
        ghi_vals = results_gdf["ghi_kwh_m2"]
        if len(feasible) > 0:
            mcda_scores = feasible["mcda_score"]
            # Estimate capacity: each grid cell ~ (grid_res * 111km)^2 area
            # Assume ~30 MW/km^2 for utility-scale solar
            cell_area_km2 = (
                cfg.grid_resolution * 111.32
                * cfg.grid_resolution * 111.32
                * math.cos(math.radians((self.south + self.north) / 2))
            )
            total_cap = len(feasible) * cell_area_km2 * 30.0  # MW
        else:
            mcda_scores = results_gdf["mcda_score"]
            total_cap = 0.0

        self.progress.emit(
            100,
            f"Analysis complete: {len(feasible)} feasible cells "
            f"(CF >= {cfg.min_capacity_factor:.0%})",
        )

        return SolarPVAnalysisSummary(
            total_cells=len(grid_points),
            feasible_cells=int(feasible_mask.sum()),
            cf_min=float(cf_vals.min()),
            cf_max=float(cf_vals.max()),
            cf_avg=float(cf_vals.mean()),
            ghi_avg=float(ghi_vals.mean()) if len(ghi_vals) > 0 else 0.0,
            mcda_score_min=float(mcda_scores.min()) if len(mcda_scores) > 0 else 0,
            mcda_score_max=float(mcda_scores.max()) if len(mcda_scores) > 0 else 0,
            total_capacity_mw=total_cap,
            computed_weights=computed_weights,
            results_gdf=results_gdf,
            hourly_data=getattr(self, "_hourly_data", None),
        )

    # ------------------------------------------------------------------
    # Phase 1: Solar PV capacity factors (dispatcher)
    # ------------------------------------------------------------------

    def _compute_capacity_factors(self):
        """Dispatch to the appropriate data source for solar CF computation."""
        src = self.solar_config.data_source
        if src == "era5_atlite":
            return self._compute_cf_atlite()
        elif src == "nasa_power":
            return self._compute_cf_nasa_power()
        else:
            return self._compute_cf_open_meteo()

    # -- ERA5 via atlite (slow, requires CDS API) --

    def _compute_cf_atlite(self):
        """Download ERA5 data via atlite and compute mean PV capacity factors."""
        import atlite
        import numpy as np

        tmpdir = Path(tempfile.mkdtemp(prefix="solar_pv_era5_"))
        cutout_path = tmpdir / "solar_pv_cutout.nc"

        self.progress.emit(
            5, "Downloading ERA5 irradiance data via CDS (this may take hours)..."
        )

        cutout = atlite.Cutout(
            path=cutout_path,
            module="era5",
            x=slice(self.west, self.east),
            y=slice(self.south, self.north),
            time=str(self.solar_config.year),
        )
        cutout.prepare()

        if self._cancelled:
            return np.array([]), None, np.array([]), np.array([])

        self.progress.emit(20, "Computing solar PV capacity factors...")

        cfg = self.solar_config

        panel_config = {
            "model": "huld",
            "efficiency": cfg.module_efficiency,
            "c_temp_amb": 1.0,
            "c_temp_irrad": 0.035,
            "r_tmod": 298.0,
            "r_tamb": 293.0,
            "r_irradiance": 1000.0,
            "inverter_efficiency": 0.96,
        }

        if cfg.orientation == "latitude_optimal":
            mid_lat = (self.south + self.north) / 2.0
            orientation = {
                "slope": abs(mid_lat),
                "azimuth": 180.0 if mid_lat >= 0 else 0.0,
            }
        else:
            orientation = {"slope": cfg.tilt, "azimuth": cfg.azimuth}

        pv_kwargs = {
            "panel": panel_config,
            "orientation": orientation,
            "capacity_factor_timeseries": True,
        }
        if cfg.tracking == "horizontal":
            pv_kwargs["tracking"] = "horizontal"
        elif cfg.tracking == "vertical":
            pv_kwargs["tracking"] = "vertical"
        elif cfg.tracking == "dual":
            pv_kwargs["tracking"] = "dual"

        cf_ts = cutout.pv(**pv_kwargs)

        mean_cf = cf_ts.mean(dim="time").values
        lats = cf_ts.coords["y"].values
        lons = cf_ts.coords["x"].values

        ghi_annual = None
        try:
            influx = cutout.data["influx_direct"] + cutout.data["influx_diffuse"]
            ghi_annual = influx.sum(dim="time").values / 1000.0
        except Exception:
            logger.debug("Could not compute GHI from cutout")

        return mean_cf, ghi_annual, lats, lons

    # -- Open-Meteo Historical API (fast, same ERA5 data) --

    def _compute_cf_open_meteo(self):
        """Fetch ERA5 irradiance from Open-Meteo and compute CF locally."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import numpy as np

        cfg = self.solar_config
        self.progress.emit(5, "Fetching solar data from Open-Meteo (ERA5)...")

        lats, lons = self._build_grid()
        n_lat, n_lon = len(lats), len(lons)
        mean_cf = np.full((n_lat, n_lon), np.nan)
        ghi_annual = np.full((n_lat, n_lon), np.nan)
        self._hourly_data = {}

        tasks = [(lat, lon) for lat in lats for lon in lons]
        total_pts = len(tasks)

        def _fetch_one(coords):
            la, lo = coords
            result = _fetch_open_meteo_solar(la, lo, cfg.year)
            if result is None:
                return la, lo, np.nan, np.nan, None
            ghi_w, temp_c, timestamps = result
            cf = _solar_cf_from_irradiance(
                ghi_w, temp_c,
                cfg.module_efficiency, cfg.module_gamma_pmax,
                cfg.module_t_noct,
            )
            ghi_ann = float(np.nansum(ghi_w)) / 1000.0
            return la, lo, cf, ghi_ann, (ghi_w, temp_c, timestamps)

        n_workers = min(cfg.effective_workers, total_pts)
        done = 0

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_fetch_one, t) for t in tasks]
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return mean_cf, ghi_annual, lats, lons

                la, lo, cf, ghi_ann, raw = future.result()
                i = int(np.searchsorted(lats, la))
                j = int(np.searchsorted(lons, lo))
                if i < n_lat and j < n_lon:
                    mean_cf[i, j] = cf
                    ghi_annual[i, j] = ghi_ann
                if raw is not None:
                    ghi_w, temp_c, timestamps = raw
                    self._hourly_data[(float(la), float(lo))] = (
                        HourlyIrradianceData(
                            timestamps=timestamps,
                            ghi=ghi_w,
                            temperature=temp_c,
                        )
                    )

                done += 1
                if done % max(1, total_pts // 20) == 0:
                    pct = 5 + int(25 * done / total_pts)
                    self.progress.emit(pct, f"Open-Meteo: {done}/{total_pts} points...")

        self.progress.emit(30, f"Open-Meteo: fetched {total_pts} grid points")
        return mean_cf, ghi_annual, lats, lons

    # -- NASA POWER API (fast, MERRA-2 reanalysis) --

    def _compute_cf_nasa_power(self):
        """Fetch MERRA-2 irradiance from NASA POWER and compute CF locally."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import numpy as np

        cfg = self.solar_config
        self.progress.emit(5, "Fetching solar data from NASA POWER (MERRA-2)...")

        lats, lons = self._build_grid()
        n_lat, n_lon = len(lats), len(lons)
        mean_cf = np.full((n_lat, n_lon), np.nan)
        ghi_annual = np.full((n_lat, n_lon), np.nan)
        if not hasattr(self, "_hourly_data"):
            self._hourly_data = {}

        tasks = [(lat, lon) for lat in lats for lon in lons]
        total_pts = len(tasks)

        def _fetch_one(coords):
            la, lo = coords
            result = _fetch_nasa_power_solar(la, lo, cfg.year)
            if result is None:
                return la, lo, np.nan, np.nan, None
            ghi_w, temp_c, timestamps = result
            cf = _solar_cf_from_irradiance(
                ghi_w, temp_c,
                cfg.module_efficiency, cfg.module_gamma_pmax,
                cfg.module_t_noct,
            )
            ghi_ann = float(np.nansum(ghi_w)) / 1000.0
            return la, lo, cf, ghi_ann, (ghi_w, temp_c, timestamps)

        n_workers = min(cfg.effective_workers, total_pts)
        done = 0

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_fetch_one, t) for t in tasks]
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return mean_cf, ghi_annual, lats, lons

                la, lo, cf, ghi_ann, raw = future.result()
                i = int(np.searchsorted(lats, la))
                j = int(np.searchsorted(lons, lo))
                if i < n_lat and j < n_lon:
                    mean_cf[i, j] = cf
                    ghi_annual[i, j] = ghi_ann
                if raw is not None:
                    ghi_w, temp_c, timestamps = raw
                    self._hourly_data[(float(la), float(lo))] = (
                        HourlyIrradianceData(
                            timestamps=timestamps,
                            ghi=ghi_w,
                            temperature=temp_c,
                        )
                    )

                done += 1
                if done % max(1, total_pts // 20) == 0:
                    pct = 5 + int(25 * done / total_pts)
                    self.progress.emit(pct, f"NASA POWER: {done}/{total_pts} points...")

        self.progress.emit(30, f"NASA POWER: fetched {total_pts} grid points")
        return mean_cf, ghi_annual, lats, lons

    # -- Grid builder --

    def _build_grid(self):
        """Build regular lat/lon grid within domain bounds."""
        import numpy as np
        res = self.solar_config.grid_resolution
        lats = np.arange(self.south + res / 2, self.north, res)
        lons = np.arange(self.west + res / 2, self.east, res)
        if len(lats) == 0:
            lats = np.array([(self.south + self.north) / 2])
        if len(lons) == 0:
            lons = np.array([(self.west + self.east) / 2])
        return lats, lons

    # ------------------------------------------------------------------
    # Phase 2: Terrain data (shared with wind)
    # ------------------------------------------------------------------

    def _fetch_terrain(self, grid_points: list[dict]) -> tuple[list, list]:
        """Fetch elevation and compute slope for grid points.

        Fallback chain: SRTM/rasterio -> Open-Meteo Elevation -> Open-Elevation API.
        """
        try:
            return self._fetch_terrain_rasterio(grid_points)
        except Exception as exc:
            logger.warning(
                "Rasterio terrain fetch failed (%s), trying Open-Meteo elevation...",
                exc,
            )

        try:
            return self._fetch_terrain_open_meteo(grid_points)
        except Exception as exc:
            logger.warning(
                "Open-Meteo elevation failed (%s), trying Open-Elevation API...",
                exc,
            )

        return self._fetch_terrain_api(grid_points)

    def _fetch_terrain_rasterio(
        self, grid_points: list[dict],
    ) -> tuple[list[float], list[float]]:
        """Fetch terrain using rasterio with SRTM tiles."""
        import numpy as np
        import rasterio

        tmpdir = Path(tempfile.mkdtemp(prefix="solar_dem_"))
        dem_path = tmpdir / "dem.tif"

        try:
            import elevation as elev
            elev.clip(
                bounds=(self.west, self.south, self.east, self.north),
                output=str(dem_path),
            )
        except (ImportError, Exception):
            raise RuntimeError("elevation package not available")

        with rasterio.open(dem_path) as src:
            dem_data = src.read(1)
            transform = src.transform

            elevations = []
            for pt in grid_points:
                row, col = rasterio.transform.rowcol(
                    transform, pt["lon"], pt["lat"],
                )
                row = min(max(row, 0), dem_data.shape[0] - 1)
                col = min(max(col, 0), dem_data.shape[1] - 1)
                elevations.append(float(dem_data[row, col]))

            # Compute slope from DEM
            res_y = abs(transform.e)
            res_x = abs(transform.a)
            mid_lat = (self.south + self.north) / 2
            cell_size_m = res_x * 111320 * math.cos(math.radians(mid_lat))

            dy, dx = np.gradient(dem_data.astype(float), cell_size_m)
            slope_rad = np.arctan(np.sqrt(dx ** 2 + dy ** 2))
            slope_deg = np.degrees(slope_rad)

            slopes = []
            for pt in grid_points:
                row, col = rasterio.transform.rowcol(
                    transform, pt["lon"], pt["lat"],
                )
                row = min(max(row, 0), slope_deg.shape[0] - 1)
                col = min(max(col, 0), slope_deg.shape[1] - 1)
                slopes.append(float(slope_deg[row, col]))

        return elevations, slopes

    def _fetch_terrain_open_meteo(
        self, grid_points: list[dict],
    ) -> tuple[list[float], list[float]]:
        """Fetch elevation from Open-Meteo Elevation API (fast, reliable).

        Endpoint: https://api.open-meteo.com/v1/elevation
        Supports batch queries (up to ~100 per request).
        """
        import numpy as np
        import requests

        elevations: list[float] = []
        batch_size = 100

        for start in range(0, len(grid_points), batch_size):
            batch = grid_points[start:start + batch_size]
            lats = ",".join(f"{pt['lat']:.4f}" for pt in batch)
            lons = ",".join(f"{pt['lon']:.4f}" for pt in batch)

            resp = requests.get(
                "https://api.open-meteo.com/v1/elevation",
                params={"latitude": lats, "longitude": lons},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            elev_list = data.get("elevation", [])
            if isinstance(elev_list, (int, float)):
                elev_list = [elev_list]
            for e in elev_list:
                elevations.append(float(e) if e is not None else 0.0)

        # Pad if response was shorter than expected
        while len(elevations) < len(grid_points):
            elevations.append(0.0)

        # Estimate slope from elevation differences between neighbours
        slopes = self._slope_from_elevations(grid_points, elevations)
        return elevations, slopes

    def _slope_from_elevations(
        self, grid_points: list[dict], elevations: list[float],
    ) -> list[float]:
        """Estimate slope from elevation differences between grid neighbours."""
        slopes: list[float] = []
        if len(grid_points) <= 1:
            return [0.0] * len(grid_points)

        res = self.solar_config.grid_resolution
        for i, pt in enumerate(grid_points):
            neighbours = []
            for j, other in enumerate(grid_points):
                if i == j:
                    continue
                dist_deg = math.sqrt(
                    (pt["lat"] - other["lat"]) ** 2
                    + (pt["lon"] - other["lon"]) ** 2,
                )
                if dist_deg < res * 1.5:
                    neighbours.append(j)

            if neighbours:
                elev_diffs = [
                    abs(elevations[j] - elevations[i]) for j in neighbours
                ]
                dist_m = res * 111320 * math.cos(math.radians(pt["lat"]))
                max_slope = max(elev_diffs) / max(dist_m, 1)
                slopes.append(math.degrees(math.atan(max_slope)))
            else:
                slopes.append(0.0)
        return slopes

    def _fetch_terrain_api(
        self, grid_points: list[dict],
    ) -> tuple[list[float], list[float]]:
        """Fetch elevation from Open-Elevation API as last-resort fallback."""
        import requests

        elevations = []

        batch_size = 256
        for start in range(0, len(grid_points), batch_size):
            batch = grid_points[start:start + batch_size]
            locations = "|".join(
                f"{pt['lat']},{pt['lon']}" for pt in batch
            )
            try:
                resp = requests.get(
                    "https://api.open-elevation.com/api/v1/lookup",
                    params={"locations": locations},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                for r in data.get("results", []):
                    elevations.append(float(r.get("elevation", 0)))
            except Exception:
                elevations.extend([0.0] * len(batch))

        slopes = self._slope_from_elevations(grid_points, elevations)
        return elevations, slopes

    # ------------------------------------------------------------------
    # Phase 3: LULC data
    # ------------------------------------------------------------------

    def _fetch_lulc(self, grid_points: list[dict]) -> list[float]:
        """Fetch ESA WorldCover 2021 LULC class and map to suitability score."""
        scores = self.mcda_config.lulc_scores
        if self.solar_config.installation == "floating":
            scores = dict(scores)
            scores.update(FLOATING_LULC_OVERRIDES)

        default_score = 0.5

        try:
            return self._fetch_lulc_worldcover(grid_points, scores, default_score)
        except Exception as exc:
            logger.warning("WorldCover LULC fetch failed: %s", exc)
            return [default_score] * len(grid_points)

    def _fetch_lulc_worldcover(
        self,
        grid_points: list[dict],
        scores: dict[int, float],
        default_score: float,
    ) -> list[float]:
        """Download ESA WorldCover 2021 tiles and sample at grid points."""
        import rasterio
        import requests

        tmpdir = Path(tempfile.mkdtemp(prefix="solar_lulc_"))

        tile_size = 3
        min_lat = int(math.floor(self.south / tile_size) * tile_size)
        max_lat = int(math.ceil(self.north / tile_size) * tile_size)
        min_lon = int(math.floor(self.west / tile_size) * tile_size)
        max_lon = int(math.ceil(self.east / tile_size) * tile_size)

        lulc_results = [default_score] * len(grid_points)

        for tile_lat in range(min_lat, max_lat, tile_size):
            for tile_lon in range(min_lon, max_lon, tile_size):
                lat_str = f"N{abs(tile_lat):02d}" if tile_lat >= 0 else f"S{abs(tile_lat):02d}"
                lon_str = f"E{abs(tile_lon):03d}" if tile_lon >= 0 else f"W{abs(tile_lon):03d}"
                tile_name = f"ESA_WorldCover_10m_2021_v200_{lat_str}{lon_str}_Map.tif"
                tile_url = (
                    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
                    f"v200/2021/map/{tile_name}"
                )
                tile_path = tmpdir / tile_name

                try:
                    resp = requests.get(tile_url, timeout=60, stream=True)
                    resp.raise_for_status()
                    with open(tile_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)

                    with rasterio.open(tile_path) as src:
                        for i, pt in enumerate(grid_points):
                            if not (
                                tile_lat <= pt["lat"] < tile_lat + tile_size
                                and tile_lon <= pt["lon"] < tile_lon + tile_size
                            ):
                                continue
                            try:
                                row, col = rasterio.transform.rowcol(
                                    src.transform, pt["lon"], pt["lat"],
                                )
                                row = min(max(row, 0), src.height - 1)
                                col = min(max(col, 0), src.width - 1)
                                window = rasterio.windows.Window(col, row, 1, 1)
                                data = src.read(1, window=window)
                                lulc_class = int(data[0, 0])
                                lulc_results[i] = scores.get(lulc_class, default_score)
                            except Exception:
                                pass

                except Exception as exc:
                    logger.debug("Could not fetch tile %s: %s", tile_name, exc)

        return lulc_results

    # ------------------------------------------------------------------
    # Phase 4: Distance to grid
    # ------------------------------------------------------------------

    def _compute_dist_to_grid(self, lat: float, lon: float) -> float:
        """Compute minimum distance (km) from point to nearest transmission line."""
        if not self.transmission_lines:
            return 0.0

        km_per_deg_lat = 111.32
        mid_lat = (self.south + self.north) / 2
        km_per_deg_lon = 111.32 * math.cos(math.radians(mid_lat))

        min_dist = float("inf")
        for line in self.transmission_lines:
            coords = line.get("coords", [])
            for coord in coords:
                clat, clon = coord[0], coord[1]
                dy = (lat - clat) * km_per_deg_lat
                dx = (lon - clon) * km_per_deg_lon
                dist = math.sqrt(dx * dx + dy * dy)
                min_dist = min(min_dist, dist)

        return min_dist if min_dist < float("inf") else 0.0

    # ------------------------------------------------------------------
    # MCDA weighting methods
    # ------------------------------------------------------------------

    @staticmethod
    def _entropy_weights(norm_matrix) -> "np.ndarray":
        """Compute weights using Shannon entropy method."""
        import numpy as np

        n, m = norm_matrix.shape
        if n <= 1:
            return np.ones(m) / m

        shifted = norm_matrix + 1e-10
        col_sums = shifted.sum(axis=0)
        col_sums[col_sums == 0] = 1
        p = shifted / col_sums

        k = 1.0 / np.log(n)
        with np.errstate(divide="ignore", invalid="ignore"):
            H = -k * np.nansum(p * np.log(p + 1e-30), axis=0)

        d = 1.0 - H
        d = np.maximum(d, 0)

        total = d.sum()
        if total > 0:
            return d / total
        return np.ones(m) / m

    @staticmethod
    def _pca_weights(norm_matrix) -> "np.ndarray":
        """Compute weights from first principal component loadings."""
        import numpy as np
        from sklearn.decomposition import PCA

        n, m = norm_matrix.shape
        if n <= m or m <= 1:
            return np.ones(m) / m

        std = norm_matrix.std(axis=0)
        std[std == 0] = 1
        standardized = (norm_matrix - norm_matrix.mean(axis=0)) / std

        pca = PCA(n_components=1)
        pca.fit(standardized)

        loadings = np.abs(pca.components_[0])
        total = loadings.sum()
        if total > 0:
            return loadings / total
        return np.ones(m) / m

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_summary() -> SolarPVAnalysisSummary:
        return SolarPVAnalysisSummary(
            total_cells=0, feasible_cells=0,
            cf_min=0, cf_max=0, cf_avg=0, ghi_avg=0,
            mcda_score_min=0, mcda_score_max=0,
            total_capacity_mw=0,
        )


# ======================================================================
# Data source helpers (module-level)
# ======================================================================


def _solar_cf_from_irradiance(
    ghi_w,
    temp_c,
    efficiency: float,
    gamma_pmax: float,
    t_noct: float,
) -> float:
    """Compute mean PV capacity factor from hourly GHI and temperature.

    Parameters
    ----------
    ghi_w : array-like
        Hourly global horizontal irradiance (W/m²).
    temp_c : array-like
        Hourly ambient temperature (°C).
    efficiency : float
        Module STC efficiency (0-1).
    gamma_pmax : float
        Temperature coefficient of power (%/°C), typically negative.
    t_noct : float
        Nominal operating cell temperature (°C).
    """
    import numpy as np

    ghi = np.asarray(ghi_w, dtype=float)
    temp = np.asarray(temp_c, dtype=float)

    # Cell temperature using NOCT model
    t_cell = temp + (t_noct - 20.0) / 800.0 * ghi

    # Power output relative to STC (GHI/1000 * temp_correction)
    # gamma_pmax is in %/°C, e.g. -0.40
    temp_factor = 1.0 + (gamma_pmax / 100.0) * (t_cell - 25.0)
    temp_factor = np.clip(temp_factor, 0.0, 1.5)

    # CF = mean(GHI/1000 * temp_factor)
    cf = float(np.nanmean((ghi / 1000.0) * temp_factor))
    return max(0.0, min(cf, 1.0))


def _fetch_open_meteo_solar(
    lat: float, lon: float, year: int,
) -> "tuple[np.ndarray, np.ndarray, list[str]] | None":
    """Fetch hourly GHI and temperature from Open-Meteo Historical API.

    Returns (ghi_w_m2, temp_c, timestamps) or None on failure.
    """
    import numpy as np
    import requests

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "shortwave_radiation,temperature_2m",
        "timezone": "UTC",
    }

    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        ghi = np.array(
            hourly.get("shortwave_radiation", []), dtype=float,
        )
        temp = np.array(
            hourly.get("temperature_2m", []), dtype=float,
        )
        timestamps = hourly.get("time", [])

        if len(ghi) == 0:
            return None

        # Fill temp if shorter
        if len(temp) < len(ghi):
            temp = np.full_like(ghi, 25.0)

        return ghi, temp[:len(ghi)], timestamps[:len(ghi)]

    except Exception as exc:
        logger.debug(
            "Open-Meteo solar fetch failed for (%.2f, %.2f): %s",
            lat, lon, exc,
        )
        return None


def _fetch_nasa_power_solar(
    lat: float, lon: float, year: int,
) -> "tuple[np.ndarray, np.ndarray, list[str]] | None":
    """Fetch hourly GHI and temperature from NASA POWER API (MERRA-2).

    Returns (ghi_w_m2, temp_c, timestamps) or None on failure.
    """
    import numpy as np
    import requests

    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M",
        "community": "RE",
        "longitude": round(lon, 4),
        "latitude": round(lat, 4),
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "JSON",
    }

    try:
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        props = data.get("properties", {}).get("parameter", {})
        ghi_dict = props.get("ALLSKY_SFC_SW_DWN", {})
        t2m_dict = props.get("T2M", {})

        # Build timestamps from dict keys (format: "20220101" + hour index)
        timestamps = []
        valid_ghi = []
        valid_temp = []
        for key, val in ghi_dict.items():
            if val == -999:
                continue
            valid_ghi.append(val)
            t_val = t2m_dict.get(key, -999)
            valid_temp.append(t_val if t_val != -999 else 25.0)
            # Key format: "2022010100" (YYYYMMDDHH)
            try:
                ts = f"{key[:4]}-{key[4:6]}-{key[6:8]}T{key[8:10]}:00"
                timestamps.append(ts)
            except (IndexError, ValueError):
                timestamps.append("")

        ghi = np.array(valid_ghi, dtype=float)
        temp = np.array(valid_temp, dtype=float)

        if len(ghi) == 0:
            return None

        return ghi, temp[:len(ghi)], timestamps[:len(ghi)]

    except Exception as exc:
        logger.debug(
            "NASA POWER solar fetch failed for (%.2f, %.2f): %s",
            lat, lon, exc,
        )
        return None


def generate_solar_pv_development_zones(
    results_gdf,
    min_cf: float,
    min_mcda_score: float,
    buffer_km: float,
    grid_resolution_deg: float = 0.25,
    installation_type: str = "ground",
):
    """Cluster feasible solar PV sites into development zone polygons.

    Uses DBSCAN + hull + buffer algorithm.

    Parameters
    ----------
    results_gdf : GeoDataFrame
        Per-cell results with ``capacity_factor``, ``mcda_score`` columns.
    min_cf : float
        Minimum capacity factor to include.
    min_mcda_score : float
        Minimum MCDA composite score (0-1) to include.
    buffer_km : float
        Buffer distance around cluster polygons.
    grid_resolution_deg : float
        Grid resolution in degrees for DBSCAN eps.
    installation_type : str
        "ground" or "floating".

    Returns
    -------
    GeoDataFrame
        Zone polygons with zone_id, area_km2, num_sites, avg_cf,
        avg_mcda, total_capacity_mw columns.
    """
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import MultiPoint
    from sklearn.cluster import DBSCAN

    _EMPTY_COLS = [
        "zone_id", "geometry", "area_km2", "num_sites",
        "avg_cf", "avg_mcda", "total_capacity_mw",
    ]

    feasible = results_gdf[
        (results_gdf["capacity_factor"] >= min_cf)
        & (results_gdf["mcda_score"] >= min_mcda_score)
    ].copy()

    if feasible.empty:
        return gpd.GeoDataFrame(
            columns=_EMPTY_COLS, geometry="geometry", crs="EPSG:4326",
        )

    # Project to metric CRS
    utm_crs = feasible.estimate_utm_crs()
    feasible_utm = feasible.to_crs(utm_crs)

    coords_m = np.column_stack([
        feasible_utm.geometry.x,
        feasible_utm.geometry.y,
    ])

    # 1. Cluster with DBSCAN
    eps_m = grid_resolution_deg * 111_320.0 * 1.5
    clustering = DBSCAN(eps=eps_m, min_samples=1).fit(coords_m)
    feasible_utm = feasible_utm.copy()
    feasible_utm["cluster"] = clustering.labels_

    # 2. Land/water mask
    clip_mask = None
    if installation_type == "floating":
        # For floating: clip zones to water areas only
        half_cell_m = grid_resolution_deg * 111_320.0 / 2.0
        all_utm = results_gdf.to_crs(utm_crs)
        clip_mask = all_utm.geometry.buffer(
            half_cell_m, cap_style="square",
        ).union_all()
    else:
        # For ground: clip zones to land (exclude water)
        # Use LULC scores: cells with lulc_score > 0 are land
        if "lulc_score" in results_gdf.columns:
            land_cells = results_gdf[results_gdf["lulc_score"] > 0]
        else:
            land_cells = results_gdf
        if not land_cells.empty:
            half_cell_m = grid_resolution_deg * 111_320.0 / 2.0
            land_utm = land_cells.to_crs(utm_crs)
            clip_mask = land_utm.geometry.buffer(
                half_cell_m, cap_style="square",
            ).union_all()

    # 3. Build polygon per cluster
    buffer_m = buffer_km * 1000.0
    zones: list[dict] = []

    # Estimate capacity density: ~30 MW/km^2 for utility-scale solar
    capacity_density_mw_km2 = 30.0

    for cluster_id in sorted(feasible_utm["cluster"].unique()):
        if cluster_id == -1:
            continue

        members = feasible_utm[feasible_utm["cluster"] == cluster_id]
        n = len(members)
        points = MultiPoint(list(members.geometry))

        if n == 1:
            zone_geom = points.buffer(buffer_m)
        elif n == 2:
            zone_geom = points.convex_hull.buffer(buffer_m)
        else:
            try:
                from shapely import concave_hull
                hull = concave_hull(points, ratio=0.3)
            except (ImportError, Exception):
                hull = points.convex_hull
            if hull.is_empty or hull.geom_type in ("Point", "LineString"):
                hull = points.convex_hull
            zone_geom = hull.buffer(buffer_m)

        if zone_geom.is_empty:
            continue

        # Clip to land/water mask
        if clip_mask is not None:
            zone_geom = zone_geom.intersection(clip_mask)
            if zone_geom.is_empty:
                continue

        area_km2 = zone_geom.area / 1e6
        zones.append({
            "zone_id": f"solar_pv_zone_{cluster_id}",
            "geometry": zone_geom,
            "area_km2": area_km2,
            "num_sites": n,
            "avg_cf": float(members["capacity_factor"].mean()),
            "avg_mcda": float(members["mcda_score"].mean()),
            "total_capacity_mw": area_km2 * capacity_density_mw_km2,
        })

    if not zones:
        return gpd.GeoDataFrame(
            columns=_EMPTY_COLS, geometry="geometry", crs="EPSG:4326",
        )

    zones_gdf = gpd.GeoDataFrame(zones, crs=utm_crs).to_crs("EPSG:4326")
    return zones_gdf
