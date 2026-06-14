"""Wind Resource Assessment engine with Multi-Criteria Decision Analysis.

Workflow:
1. Download ERA5 wind data via atlite → compute capacity factors
2. Fetch terrain data (DEM → elevation + slope)
3. Fetch LULC data (ESA WorldCover 2021)
4. Compute distance to existing grid (transmission lines)
5. Run MCDA (manual / entropy / PCA weighting)
6. Generate development zone polygons

Data sources:
- Wind: ERA5 reanalysis via atlite library
- Terrain: Copernicus GLO-90 DEM (90m) via OpenTopography SRTM fallback
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

# ESA WorldCover 2021 class codes → default suitability scores
DEFAULT_LULC_SCORES: dict[int, float] = {
    10: 0.1,   # Tree cover
    20: 0.5,   # Shrubland
    30: 0.9,   # Grassland
    40: 0.7,   # Cropland
    50: 0.0,   # Built-up
    60: 0.8,   # Bare / sparse vegetation
    70: 0.0,   # Snow and ice
    80: 0.0,   # Permanent water bodies (onshore default)
    90: 0.2,   # Herbaceous wetland
    95: 0.0,   # Mangroves
    100: 0.1,  # Moss and lichen
}

# Offshore overrides (water = suitable)
OFFSHORE_LULC_OVERRIDES: dict[int, float] = {
    80: 1.0,   # Water → suitable for offshore
    10: 0.0,   # Tree cover → exclude
    50: 0.0,   # Built-up → exclude
}


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
        "slope": CriterionConfig(True, 0.15, "minimize"),
        "elevation": CriterionConfig(True, 0.10, "minimize"),
        "lulc_score": CriterionConfig(True, 0.20, "maximize"),
        "dist_grid_km": CriterionConfig(True, 0.15, "minimize"),
    })
    lulc_scores: dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_LULC_SCORES),
    )


@dataclass
class TurbineSpec:
    """Technical specification of a wind turbine from the database."""

    key: str  # atlite key or oedb identifier
    name: str
    manufacturer: str
    rated_power_mw: float
    rotor_diameter_m: float
    hub_height_m: float
    source: str = ""  # "atlite" or "oedb"
    wind_speeds: list[float] = field(default_factory=list)  # m/s
    power_curve: list[float] = field(default_factory=list)   # MW

    @property
    def specific_power(self) -> float:
        """W/m² of rotor area."""
        import math
        area = math.pi * (self.rotor_diameter_m / 2) ** 2
        return (self.rated_power_mw * 1e6) / area if area > 0 else 0


def load_turbine_database(*, include_oedb: bool = False) -> list[TurbineSpec]:
    """Load turbine specs from atlite's built-in database.

    Parameters
    ----------
    include_oedb : bool
        If True, also fetch turbines from the Open Energy Database (HTTP).
        Disabled by default to avoid blocking on network I/O.

    Returns a list sorted by manufacturer then rated power.
    """
    turbines: list[TurbineSpec] = []

    # 1. Load atlite built-in turbines (always available offline)
    try:
        turbines.extend(_load_atlite_builtin_turbines())
    except Exception as exc:
        logger.warning("Could not load atlite built-in turbines: %s", exc)

    # 2. Optionally load OEDB turbines (requires internet)
    if include_oedb:
        try:
            oedb_turbines = _load_oedb_turbines()
            existing_keys = {t.key for t in turbines}
            for t in oedb_turbines:
                if t.key not in existing_keys:
                    turbines.append(t)
        except Exception as exc:
            logger.info("OEDB turbine database not available: %s", exc)

    # Sort by manufacturer, then rated power
    turbines.sort(key=lambda t: (t.manufacturer.lower(), t.rated_power_mw))
    return turbines


def _load_atlite_builtin_turbines() -> list[TurbineSpec]:
    """Load turbines from atlite's bundled YAML resource files."""
    import importlib.resources
    import yaml

    turbines: list[TurbineSpec] = []

    # Find atlite's windturbine resource directory
    try:
        # Python 3.9+
        resource_dir = importlib.resources.files("atlite") / "resources" / "windturbine"
        yaml_files = [
            p for p in resource_dir.iterdir()
            if str(p).endswith(".yaml")
        ]
    except (AttributeError, TypeError, FileNotFoundError):
        # Fallback: find via package path
        import atlite
        resource_dir = Path(atlite.__file__).parent / "resources" / "windturbine"
        yaml_files = list(resource_dir.glob("*.yaml"))

    for yf in yaml_files:
        try:
            text = yf.read_text(encoding="utf-8") if hasattr(yf, "read_text") else Path(str(yf)).read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if not data:
                continue

            key = Path(str(yf)).stem  # e.g. "Vestas_V112_3MW"

            # Extract power curve
            wind_speeds = data.get("V", [])
            power_values = data.get("POW", [])

            # Rated power = max of power curve
            rated_mw = max(power_values) if power_values else 0

            # Rotor diameter: some files have it, most don't
            # Try to parse from name (e.g. "V112" → 112m)
            rotor_d = data.get("rotor_diameter", 0)
            if not rotor_d:
                rotor_d = _parse_rotor_diameter(
                    data.get("name", key),
                )

            turbines.append(TurbineSpec(
                key=key,
                name=data.get("name", key),
                manufacturer=data.get("manufacturer", _guess_manufacturer(key)),
                rated_power_mw=rated_mw,
                rotor_diameter_m=rotor_d,
                hub_height_m=float(data.get("HUB_HEIGHT", data.get("hub_height", 80))),
                source="atlite",
                wind_speeds=wind_speeds,
                power_curve=power_values,
            ))
        except Exception as exc:
            logger.debug("Skipping turbine file %s: %s", yf, exc)

    return turbines


def _load_oedb_turbines() -> list[TurbineSpec]:
    """Load turbine data from the Open Energy Database (OEDB) REST API."""
    import json
    import requests

    url = (
        "https://openenergyplatform.org/api/v0/schema/supply/"
        "tables/wind_turbine_library/rows/"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    rows = resp.json()

    turbines: list[TurbineSpec] = []
    for row in rows:
        try:
            has_curve = row.get("has_power_curve", False)
            nominal_w = row.get("nominal_power", 0) or 0
            rated_mw = nominal_w / 1e6

            if rated_mw <= 0 or not has_curve:
                continue

            rotor_d = float(row.get("rotor_diameter", 0) or 0)
            hub_str = str(row.get("hub_height", "80") or "80")
            # Hub heights can be "99; 129; 135" — take first
            hub_h = float(hub_str.split(";")[0].strip())

            # Parse power curve arrays
            ws_str = row.get("power_curve_wind_speeds", "[]")
            pw_str = row.get("power_curve_values", "[]")
            wind_speeds = json.loads(ws_str) if isinstance(ws_str, str) else (ws_str or [])
            power_kw = json.loads(pw_str) if isinstance(pw_str, str) else (pw_str or [])
            power_mw = [p / 1000.0 for p in power_kw] if power_kw else []

            turb_type = row.get("turbine_type", "")
            manufacturer = row.get("manufacturer", "")

            turbines.append(TurbineSpec(
                key=f"oedb:{turb_type}",
                name=row.get("name", turb_type) or turb_type,
                manufacturer=manufacturer,
                rated_power_mw=rated_mw,
                rotor_diameter_m=rotor_d,
                hub_height_m=hub_h,
                source="oedb",
                wind_speeds=wind_speeds,
                power_curve=power_mw,
            ))
        except Exception:
            continue

    return turbines


def _parse_rotor_diameter(name: str) -> float:
    """Try to extract rotor diameter from turbine name.

    E.g. "V112 3MW" → 112, "E-126/4200" → 126, "SWT 107" → 107
    """
    import re
    # Patterns: V112, E-126, E82, SWT_107, S88, V164
    patterns = [
        r"[VE]-?(\d{2,3})",      # V112, E-126, E82
        r"SWT[_\s-]?(\d{2,3})",  # SWT_107
        r"S(\d{2,3})",            # S88
    ]
    for pat in patterns:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return 0.0


def _guess_manufacturer(key: str) -> str:
    """Guess manufacturer from atlite turbine key name."""
    key_lower = key.lower()
    if "vestas" in key_lower or key_lower.startswith("v"):
        return "Vestas"
    if "enercon" in key_lower or key_lower.startswith("e"):
        return "Enercon"
    if "nrel" in key_lower:
        return "NREL"
    if "siemens" in key_lower or "swt" in key_lower:
        return "Siemens"
    if "suzlon" in key_lower:
        return "Suzlon"
    if "bonus" in key_lower:
        return "Bonus"
    return "Unknown"


@dataclass
class WindConfig:
    """User-configurable wind assessment parameters."""

    turbine: str = "Vestas_V112_3MW"
    hub_height: int = 80
    year: int = 2022
    grid_resolution: float = 0.25  # degrees
    min_capacity_factor: float = 0.25
    installation: str = "onshore"
    zone_buffer_km: float = 5.0
    turbine_capacity_mw: float = 3.0  # MW per turbine
    data_source: str = "open_meteo"  # "open_meteo" | "nasa_power" | "era5_atlite"
    parallel_workers: int = 0  # 0 = auto (cpu_count)
    # Power curve for local CF calculation (populated from TurbineSpec)
    wind_speeds: list[float] = field(default_factory=list)
    power_curve: list[float] = field(default_factory=list)  # MW

    @property
    def effective_workers(self) -> int:
        """Return actual worker count (resolve 0 = auto)."""
        import os
        if self.parallel_workers > 0:
            return self.parallel_workers
        return os.cpu_count() or 4


@dataclass
class HourlyWindData:
    """Hourly wind data stored per grid cell for Phase B analysis."""

    timestamps: list[str]
    wind_speed: Any  # np.ndarray — m/s at hub height
    wind_direction: Any  # np.ndarray — degrees 0-360 (or None)
    temperature: Any = None  # np.ndarray — Celsius (if available)


@dataclass
class WindAnalysisSummary:
    """Aggregated wind assessment results."""

    total_cells: int
    feasible_cells: int
    cf_min: float
    cf_max: float
    cf_avg: float
    mcda_score_min: float
    mcda_score_max: float
    total_capacity_mw: float
    computed_weights: dict[str, float] = field(default_factory=dict)
    results_gdf: Any = None  # GeoDataFrame
    hourly_data: dict[tuple[float, float], HourlyWindData] | None = None


class WindAnalyzer(QThread):
    """Run wind resource assessment in background.

    Phases:
    1. ERA5 download + capacity factor computation (0-30%)
    2. DEM → elevation + slope (30-50%)
    3. LULC suitability (50-65%)
    4. Distance to grid (65-70%)
    5. MCDA scoring (70-90%)
    6. Zone generation (90-100%)
    """

    progress = Signal(int, str)  # percent, message
    finished = Signal(object)  # WindAnalysisSummary
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        wind_config: WindConfig,
        mcda_config: MCDAConfig,
        transmission_lines: list | None = None,
        parent=None,
        polygon: list | None = None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self.wind_config = wind_config
        self.mcda_config = mcda_config
        self.transmission_lines = transmission_lines or []
        # Optional precise domain: bbox drives the fetch, the polygon clips it.
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
            logger.exception("WindAnalyzer error")
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------

    def _analyze(self) -> WindAnalysisSummary:
        import geopandas as gpd
        import numpy as np
        from shapely.geometry import Point

        cfg = self.wind_config
        mcda = self.mcda_config

        # ── Phase 1: ERA5 wind capacity factors via atlite ──
        self.progress.emit(2, "Creating atlite cutout for ERA5 data...")

        mean_cf, cf_lats, cf_lons = self._compute_capacity_factors()
        if self._cancelled:
            return self._empty_summary()

        # Build evaluation grid. A precise domain polygon (drawn or imported
        # GeoAsset) clips cells to the exact boundary, not the bounding box.
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
                if not np.isnan(cf_val):
                    grid_points.append({
                        "lat": float(lat),
                        "lon": float(lon),
                        "capacity_factor": cf_val,
                    })

        if not grid_points:
            self.progress.emit(100, "No valid grid points in domain")
            return self._empty_summary()

        self.progress.emit(
            30, f"Computed CF for {len(grid_points)} grid points"
        )
        if self._cancelled:
            return self._empty_summary()

        # ── Phase 2: Terrain (elevation + slope) ──
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

        # ── Phase 3: LULC suitability ──
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

        # ── Phase 4: Distance to grid ──
        self.progress.emit(67, "Computing distance to transmission grid...")
        grid_distances = self._compute_dist_to_grid_vectorized(grid_points)
        for i, pt in enumerate(grid_points):
            pt["dist_grid_km"] = grid_distances[i]

        if self._cancelled:
            return self._empty_summary()
        self.progress.emit(70, "Grid distance computed")

        # ── Phase 5: MCDA scoring ──
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
            # Manual: use user-supplied weights, normalize
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

        # ── Build GeoDataFrame ──
        for pt in grid_points:
            pt["geometry"] = Point(pt["lon"], pt["lat"])

        results_gdf = gpd.GeoDataFrame(grid_points, crs="EPSG:4326")

        # Filter feasible
        feasible_mask = results_gdf["capacity_factor"] >= cfg.min_capacity_factor
        feasible = results_gdf[feasible_mask]

        cf_vals = results_gdf["capacity_factor"]
        if len(feasible) > 0:
            mcda_scores = feasible["mcda_score"]
            total_cap = len(feasible) * cfg.turbine_capacity_mw
        else:
            mcda_scores = results_gdf["mcda_score"]
            total_cap = 0.0

        self.progress.emit(
            100,
            f"Analysis complete: {len(feasible)} feasible cells "
            f"(CF >= {cfg.min_capacity_factor:.0%})",
        )

        # Collect hourly data if available (from Open-Meteo or NASA POWER)
        hourly = getattr(self, "_hourly_data", None)

        return WindAnalysisSummary(
            total_cells=len(grid_points),
            feasible_cells=int(feasible_mask.sum()),
            cf_min=float(cf_vals.min()),
            cf_max=float(cf_vals.max()),
            cf_avg=float(cf_vals.mean()),
            mcda_score_min=float(mcda_scores.min()) if len(mcda_scores) > 0 else 0,
            mcda_score_max=float(mcda_scores.max()) if len(mcda_scores) > 0 else 0,
            total_capacity_mw=total_cap,
            computed_weights=computed_weights,
            results_gdf=results_gdf,
            hourly_data=hourly,
        )

    # ------------------------------------------------------------------
    # Phase 1: Wind capacity factors (dispatcher)
    # ------------------------------------------------------------------

    def _compute_capacity_factors(self):
        """Dispatch to the appropriate data source for wind CF computation."""
        src = self.wind_config.data_source
        if src == "era5_atlite":
            return self._compute_cf_atlite()
        elif src == "nasa_power":
            return self._compute_cf_nasa_power()
        else:
            return self._compute_cf_open_meteo()

    # -- ERA5 via atlite (slow, requires CDS API) --

    def _compute_cf_atlite(self):
        """Download ERA5 data via atlite and compute mean capacity factors."""
        import atlite
        import numpy as np

        tmpdir = Path(tempfile.mkdtemp(prefix="wind_era5_"))
        cutout_path = tmpdir / "wind_cutout.nc"

        self.progress.emit(5, "Downloading ERA5 wind data via CDS (this may take hours)...")

        cutout = atlite.Cutout(
            path=cutout_path,
            module="era5",
            x=slice(self.west, self.east),
            y=slice(self.south, self.north),
            time=str(self.wind_config.year),
        )
        cutout.prepare()

        if self._cancelled:
            return np.array([]), np.array([]), np.array([])

        self.progress.emit(20, "Computing wind capacity factors...")

        turbine_key = self.wind_config.turbine
        turbine_config = atlite.resource.get_windturbineconfig(turbine_key)

        if self.wind_config.hub_height:
            turbine_config["hub_height"] = float(self.wind_config.hub_height)

        cf_ts = cutout.wind(
            turbine=turbine_config,
            capacity_factor_timeseries=True,
        )

        mean_cf = cf_ts.mean(dim="time").values
        lats = cf_ts.coords["y"].values
        lons = cf_ts.coords["x"].values

        return mean_cf, lats, lons

    # -- Open-Meteo Historical API (fast, same ERA5 data) --

    def _compute_cf_open_meteo(self):
        """Fetch ERA5 wind data from Open-Meteo and compute CF locally.

        Uses ThreadPoolExecutor to fetch grid points in parallel (~10x speedup).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import numpy as np

        cfg = self.wind_config
        self.progress.emit(5, "Fetching wind data from Open-Meteo (ERA5)...")

        lats, lons = self._build_grid()
        n_lat, n_lon = len(lats), len(lons)
        mean_cf = np.full((n_lat, n_lon), np.nan)
        self._hourly_data: dict[tuple[float, float], HourlyWindData] = {}

        tasks = [
            (float(lat), float(lon)) for lat in lats for lon in lons
        ]
        total_pts = len(tasks)
        if total_pts == 0:
            return mean_cf, lats, lons

        lat_index = {float(lat): i for i, lat in enumerate(lats)}
        lon_index = {float(lon): j for j, lon in enumerate(lons)}

        def _fetch_one(coords):
            lat, lon = coords
            result = _fetch_open_meteo_wind(
                lat, lon, cfg.year, cfg.hub_height, return_direction=True,
            )
            if isinstance(result, tuple):
                ws, wd, ts = result
            else:
                ws, wd, ts = result, None, None
            cf = None
            if ws is not None and len(ws) > 0:
                cf = _wind_cf_from_speed(
                    ws, cfg.wind_speeds, cfg.power_curve, cfg.turbine_capacity_mw,
                )
            return lat, lon, cf, ws, wd, ts

        done = 0
        n_workers = min(cfg.effective_workers, total_pts)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_fetch_one, t) for t in tasks]
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return mean_cf, lats, lons
                try:
                    lat, lon, cf, ws, wd, ts = future.result()
                except Exception:
                    done += 1
                    continue
                i = lat_index[lat]
                j = lon_index[lon]
                if cf is not None:
                    mean_cf[i, j] = cf
                    self._hourly_data[(lat, lon)] = HourlyWindData(
                        timestamps=ts or [],
                        wind_speed=ws,
                        wind_direction=wd,
                    )
                done += 1
                if done % max(1, total_pts // 20) == 0:
                    pct = 5 + int(25 * done / total_pts)
                    self.progress.emit(
                        pct, f"Open-Meteo: {done}/{total_pts} points...",
                    )

        self.progress.emit(30, f"Open-Meteo: fetched {total_pts} grid points")
        return mean_cf, lats, lons

    # -- NASA POWER API (fast, MERRA-2 reanalysis) --

    def _compute_cf_nasa_power(self):
        """Fetch MERRA-2 wind data from NASA POWER and compute CF locally.

        Uses ThreadPoolExecutor to fetch grid points in parallel (~6x speedup).
        Fewer workers than Open-Meteo due to NASA POWER rate limits.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import numpy as np

        cfg = self.wind_config
        self.progress.emit(5, "Fetching wind data from NASA POWER (MERRA-2)...")

        lats, lons = self._build_grid()
        n_lat, n_lon = len(lats), len(lons)
        mean_cf = np.full((n_lat, n_lon), np.nan)
        self._hourly_data: dict[tuple[float, float], HourlyWindData] = {}

        tasks = [
            (float(lat), float(lon)) for lat in lats for lon in lons
        ]
        total_pts = len(tasks)
        if total_pts == 0:
            return mean_cf, lats, lons

        lat_index = {float(lat): i for i, lat in enumerate(lats)}
        lon_index = {float(lon): j for j, lon in enumerate(lons)}

        def _fetch_one(coords):
            lat, lon = coords
            result = _fetch_nasa_power_wind(
                lat, lon, cfg.year, cfg.hub_height, return_direction=True,
            )
            if isinstance(result, tuple):
                ws, wd, ts = result
            else:
                ws, wd, ts = result, None, None
            cf = None
            if ws is not None and len(ws) > 0:
                cf = _wind_cf_from_speed(
                    ws, cfg.wind_speeds, cfg.power_curve, cfg.turbine_capacity_mw,
                )
            return lat, lon, cf, ws, wd, ts

        done = 0
        n_workers = min(cfg.effective_workers, total_pts)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_fetch_one, t) for t in tasks]
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return mean_cf, lats, lons
                try:
                    lat, lon, cf, ws, wd, ts = future.result()
                except Exception:
                    done += 1
                    continue
                i = lat_index[lat]
                j = lon_index[lon]
                if cf is not None:
                    mean_cf[i, j] = cf
                    self._hourly_data[(lat, lon)] = HourlyWindData(
                        timestamps=ts or [],
                        wind_speed=ws,
                        wind_direction=wd,
                    )
                done += 1
                if done % max(1, total_pts // 20) == 0:
                    pct = 5 + int(25 * done / total_pts)
                    self.progress.emit(
                        pct, f"NASA POWER: {done}/{total_pts} points...",
                    )

        self.progress.emit(30, f"NASA POWER: fetched {total_pts} grid points")
        return mean_cf, lats, lons

    # -- Grid builder --

    def _build_grid(self):
        """Build regular lat/lon grid within domain bounds."""
        import numpy as np
        res = self.wind_config.grid_resolution
        lats = np.arange(self.south + res / 2, self.north, res)
        lons = np.arange(self.west + res / 2, self.east, res)
        if len(lats) == 0:
            lats = np.array([(self.south + self.north) / 2])
        if len(lons) == 0:
            lons = np.array([(self.west + self.east) / 2])
        return lats, lons

    # ------------------------------------------------------------------
    # Phase 2: Terrain data
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
        from rasterio.transform import from_bounds

        # Try to download SRTM data using elevation package
        tmpdir = Path(tempfile.mkdtemp(prefix="wind_dem_"))
        dem_path = tmpdir / "dem.tif"

        try:
            import elevation as elev
            elev.clip(
                bounds=(self.west, self.south, self.east, self.north),
                output=str(dem_path),
            )
        except (ImportError, Exception):
            # If elevation package unavailable, try SRTM direct
            raise RuntimeError("elevation package not available")

        # Read DEM and sample at grid points
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
            res_y = abs(transform.e)  # pixel height in degrees
            res_x = abs(transform.a)  # pixel width in degrees
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

        while len(elevations) < len(grid_points):
            elevations.append(0.0)

        slopes = self._slope_from_elevations(grid_points, elevations)
        return elevations, slopes

    def _slope_from_elevations(
        self, grid_points: list[dict], elevations: list[float],
    ) -> list[float]:
        """Estimate slope from elevation differences between grid neighbours.

        Vectorized with numpy: O(n) distance matrix via broadcasting instead
        of O(n²) Python loop.
        """
        import numpy as np

        n = len(grid_points)
        if n <= 1:
            return [0.0] * n

        res = self.wind_config.grid_resolution
        lats = np.array([pt["lat"] for pt in grid_points])
        lons = np.array([pt["lon"] for pt in grid_points])
        elevs = np.array(elevations)

        # Pairwise distance in degrees via broadcasting
        dlat = lats[:, None] - lats[None, :]  # (n, n)
        dlon = lons[:, None] - lons[None, :]  # (n, n)
        dist_deg = np.sqrt(dlat ** 2 + dlon ** 2)

        # Neighbour mask: within 1.5 * grid_resolution, not self
        np.fill_diagonal(dist_deg, np.inf)
        neighbour_mask = dist_deg < res * 1.5  # (n, n) bool

        # Elevation differences
        elev_diff = np.abs(elevs[:, None] - elevs[None, :])  # (n, n)
        # Set non-neighbours to 0 so they don't contribute to max
        elev_diff_masked = np.where(neighbour_mask, elev_diff, 0.0)
        max_elev_diff = np.max(elev_diff_masked, axis=1)  # (n,)

        # Distance in meters at each point's latitude
        dist_m = res * 111320.0 * np.cos(np.radians(lats))
        dist_m = np.maximum(dist_m, 1.0)

        has_neighbours = np.any(neighbour_mask, axis=1)
        slopes = np.where(
            has_neighbours,
            np.degrees(np.arctan(max_elev_diff / dist_m)),
            0.0,
        )
        return slopes.tolist()

    def _fetch_terrain_api(
        self, grid_points: list[dict],
    ) -> tuple[list[float], list[float]]:
        """Fetch elevation from Open-Elevation API as last-resort fallback."""
        import requests

        elevations = []

        # Batch query (max 256 per request)
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
        """Fetch ESA WorldCover 2021 LULC class and map to suitability score.

        Falls back to a uniform default if tile download fails.
        """
        scores = self.mcda_config.lulc_scores
        if self.wind_config.installation == "offshore":
            scores = dict(scores)
            scores.update(OFFSHORE_LULC_OVERRIDES)

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
        """Download ESA WorldCover 2021 tiles and sample at grid points.

        Tiles are downloaded in parallel with ThreadPoolExecutor (~4x speedup).
        Rasterio sampling is sequential (not thread-safe for same-file reads).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import rasterio
        import requests

        tmpdir = Path(tempfile.mkdtemp(prefix="wind_lulc_"))

        # Determine tiles needed (3x3 degree tiles)
        tile_size = 3
        min_lat = int(math.floor(self.south / tile_size) * tile_size)
        max_lat = int(math.ceil(self.north / tile_size) * tile_size)
        min_lon = int(math.floor(self.west / tile_size) * tile_size)
        max_lon = int(math.ceil(self.east / tile_size) * tile_size)

        # Build tile info list
        tile_infos = []
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
                tile_infos.append((tile_lat, tile_lon, tile_url, tile_path))

        # Download all tiles in parallel
        def _download_tile(info):
            tile_lat, tile_lon, url, path = info
            try:
                resp = requests.get(url, timeout=60, stream=True)
                resp.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                return tile_lat, tile_lon, path, True
            except Exception as exc:
                logger.debug("Could not fetch tile %s: %s", path.name, exc)
                return tile_lat, tile_lon, path, False

        downloaded_tiles = []
        n_workers = min(self.wind_config.effective_workers, len(tile_infos)) if tile_infos else 1
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_download_tile, info) for info in tile_infos]
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return [default_score] * len(grid_points)
                result = future.result()
                if result[3]:  # success
                    downloaded_tiles.append(result)

        # Sample from downloaded tiles sequentially (rasterio not thread-safe)
        lulc_results = [default_score] * len(grid_points)
        for tile_lat, tile_lon, tile_path, _ in downloaded_tiles:
            try:
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
                logger.debug("Could not read tile %s: %s", tile_path.name, exc)

        return lulc_results

    # ------------------------------------------------------------------
    # Phase 4: Distance to grid
    # ------------------------------------------------------------------

    def _compute_dist_to_grid_vectorized(
        self, grid_points: list[dict],
    ) -> list[float]:
        """Compute min distance (km) from each grid point to nearest transmission line.

        Vectorized with numpy: all point-to-line distances computed in bulk.
        """
        import numpy as np

        n = len(grid_points)
        if not self.transmission_lines:
            return [0.0] * n

        # Collect all transmission line vertices into a single array
        line_coords = []
        for line in self.transmission_lines:
            coords = line.get("coords", [])
            for coord in coords:
                line_coords.append((coord[0], coord[1]))

        if not line_coords:
            return [0.0] * n

        lc = np.array(line_coords)  # (m, 2)  lat, lon
        km_per_deg_lat = 111.32
        mid_lat = (self.south + self.north) / 2
        km_per_deg_lon = 111.32 * math.cos(math.radians(mid_lat))

        pt_lats = np.array([pt["lat"] for pt in grid_points])  # (n,)
        pt_lons = np.array([pt["lon"] for pt in grid_points])  # (n,)

        # Pairwise distances via broadcasting: (n, m)
        dy = (pt_lats[:, None] - lc[None, :, 0]) * km_per_deg_lat
        dx = (pt_lons[:, None] - lc[None, :, 1]) * km_per_deg_lon
        dist = np.sqrt(dx ** 2 + dy ** 2)  # (n, m)

        min_dists = np.min(dist, axis=1)  # (n,)
        return min_dists.tolist()

    # ------------------------------------------------------------------
    # MCDA weighting methods
    # ------------------------------------------------------------------

    @staticmethod
    def _entropy_weights(norm_matrix) -> "np.ndarray":
        """Compute weights using Shannon entropy method.

        For each criterion j:
          pij = xij / sum(xi)
          Hj = -1/ln(n) * sum(pij * ln(pij))
          wj = (1 - Hj) / sum(1 - Hj)
        """
        import numpy as np

        n, m = norm_matrix.shape
        if n <= 1:
            return np.ones(m) / m

        # Shift to positive to avoid log(0)
        shifted = norm_matrix + 1e-10

        # Proportions
        col_sums = shifted.sum(axis=0)
        col_sums[col_sums == 0] = 1
        p = shifted / col_sums

        # Entropy
        k = 1.0 / np.log(n)
        with np.errstate(divide="ignore", invalid="ignore"):
            H = -k * np.nansum(p * np.log(p + 1e-30), axis=0)

        # Diversification
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

        # Standardize
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
    def _empty_summary() -> WindAnalysisSummary:
        return WindAnalysisSummary(
            total_cells=0, feasible_cells=0,
            cf_min=0, cf_max=0, cf_avg=0,
            mcda_score_min=0, mcda_score_max=0,
            total_capacity_mw=0,
        )


# ======================================================================
# Data source helpers (module-level)
# ======================================================================


def _wind_cf_from_speed(
    ws_hourly, pc_wind_speeds: list[float], pc_power_mw: list[float],
    rated_mw: float,
) -> float:
    """Compute mean capacity factor from hourly wind speeds and power curve.

    Parameters
    ----------
    ws_hourly : array-like
        Hourly wind speed at hub height (m/s).
    pc_wind_speeds : list[float]
        Power curve wind speeds (m/s).
    pc_power_mw : list[float]
        Power curve output (MW) for each wind speed.
    rated_mw : float
        Turbine rated power (MW).
    """
    import numpy as np

    if not pc_wind_speeds or not pc_power_mw or rated_mw <= 0:
        return 0.0

    ws = np.asarray(ws_hourly, dtype=float)
    # Interpolate power curve
    power_out = np.interp(ws, pc_wind_speeds, pc_power_mw, left=0.0, right=0.0)
    return float(np.nanmean(power_out) / rated_mw)


def _fetch_open_meteo_wind(
    lat: float, lon: float, year: int, hub_height: int,
    return_direction: bool = False,
):
    """Fetch hourly wind speed at hub height from Open-Meteo Historical API.

    If return_direction is False, returns array of hourly wind speeds (m/s)
    or None on failure.  If True, returns (ws_hub, wd_100m, timestamps) tuple.

    Open-Meteo provides wind at 10m and 100m; we interpolate to hub height
    using power-law profile.
    """
    import numpy as np
    import requests

    url = "https://archive-api.open-meteo.com/v1/archive"
    hourly_vars = "wind_speed_10m,wind_speed_100m"
    if return_direction:
        hourly_vars += ",wind_direction_100m"

    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": hourly_vars,
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }

    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        ws10 = np.array(hourly.get("wind_speed_10m", []), dtype=float)
        ws100 = np.array(hourly.get("wind_speed_100m", []), dtype=float)

        if len(ws100) == 0:
            return None

        # Adjust to hub height using power-law interpolation
        # alpha estimated from 10m and 100m: alpha = ln(ws100/ws10) / ln(100/10)
        if hub_height == 100 or len(ws10) == 0:
            ws_hub = ws100
        else:
            ws10_safe = np.maximum(ws10, 0.01)
            alpha = np.log(np.maximum(ws100, 0.01) / ws10_safe) / np.log(100.0 / 10.0)
            alpha = np.clip(alpha, 0.05, 0.50)
            ws_hub = ws100 * (hub_height / 100.0) ** alpha

        if not return_direction:
            return ws_hub

        wd = hourly.get("wind_direction_100m")
        wd_arr = np.array(wd, dtype=float) if wd else None
        timestamps = hourly.get("time", [])
        return ws_hub, wd_arr, timestamps

    except Exception as exc:
        logger.debug("Open-Meteo wind fetch failed for (%.2f, %.2f): %s", lat, lon, exc)
        return None


def _fetch_nasa_power_wind(
    lat: float, lon: float, year: int, hub_height: int,
    return_direction: bool = False,
):
    """Fetch hourly wind speed from NASA POWER API (MERRA-2).

    NASA POWER provides WS10M and WS50M (and WD50M for direction).
    We extrapolate to hub height using power-law profile.

    If return_direction is False, returns array of hourly wind speeds (m/s)
    or None.  If True, returns (ws_hub, wd_50m, timestamps) tuple.
    """
    import numpy as np
    import requests

    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    api_params = "WS10M,WS50M"
    if return_direction:
        api_params += ",WD50M"

    params = {
        "parameters": api_params,
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
        ws10_dict = props.get("WS10M", {})
        ws50_dict = props.get("WS50M", {})

        # Keep keys for timestamp reconstruction
        valid_keys = [k for k, v in ws50_dict.items() if v != -999]
        ws10 = np.array([ws10_dict.get(k, 0.01) for k in valid_keys], dtype=float)
        ws10[ws10 == -999] = 0.01
        ws50 = np.array([ws50_dict[k] for k in valid_keys], dtype=float)

        if len(ws50) == 0:
            return None

        # Extrapolate to hub height from 10m and 50m
        ws10_safe = np.maximum(ws10[:len(ws50)], 0.01)
        alpha = np.log(np.maximum(ws50, 0.01) / ws10_safe) / np.log(50.0 / 10.0)
        alpha = np.clip(alpha, 0.05, 0.50)

        ws_hub = ws50 * (hub_height / 50.0) ** alpha

        if not return_direction:
            return ws_hub

        wd_dict = props.get("WD50M", {})
        wd_arr = np.array(
            [wd_dict.get(k, 0.0) for k in valid_keys], dtype=float,
        ) if wd_dict else None
        if wd_arr is not None:
            wd_arr[wd_arr == -999] = 0.0

        # Reconstruct ISO timestamps from NASA POWER keys (YYYYMMDDHH)
        timestamps = []
        for k in valid_keys:
            try:
                timestamps.append(
                    f"{k[:4]}-{k[4:6]}-{k[6:8]}T{k[8:10]}:00:00"
                )
            except (IndexError, ValueError):
                timestamps.append(k)

        return ws_hub, wd_arr, timestamps

    except Exception as exc:
        logger.debug("NASA POWER wind fetch failed for (%.2f, %.2f): %s", lat, lon, exc)
        return None


def generate_wind_development_zones(
    results_gdf,
    min_cf: float,
    min_mcda_score: float,
    buffer_km: float,
    grid_resolution_deg: float = 0.25,
    installation_type: str = "onshore",
):
    """Cluster feasible wind sites into development zone polygons.

    Same DBSCAN + hull + buffer algorithm as OTEC zones, but uses
    capacity factor and MCDA composite score as thresholds.

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
        "onshore" or "offshore".

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

    # 2. Land/ocean mask
    clip_mask = None
    if installation_type == "offshore":
        # For offshore: clip zones to water areas only
        half_cell_m = grid_resolution_deg * 111_320.0 / 2.0
        all_utm = results_gdf.to_crs(utm_crs)
        clip_mask = all_utm.geometry.buffer(
            half_cell_m, cap_style="square",
        ).union_all()
    else:
        # For onshore: clip zones to land (exclude water)
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

        # Clip to land/ocean mask
        if clip_mask is not None:
            zone_geom = zone_geom.intersection(clip_mask)
            if zone_geom.is_empty:
                continue

        zones.append({
            "zone_id": f"wind_zone_{cluster_id}",
            "geometry": zone_geom,
            "area_km2": zone_geom.area / 1e6,
            "num_sites": n,
            "avg_cf": float(members["capacity_factor"].mean()),
            "avg_mcda": float(members["mcda_score"].mean()),
            "total_capacity_mw": float(
                n * members.get("turbine_capacity_mw", 3.0).iloc[0]
                if "turbine_capacity_mw" in members.columns
                else n * 3.0
            ),
        })

    if not zones:
        return gpd.GeoDataFrame(
            columns=_EMPTY_COLS, geometry="geometry", crs="EPSG:4326",
        )

    zones_gdf = gpd.GeoDataFrame(zones, crs=utm_crs).to_crs("EPSG:4326")
    return zones_gdf
