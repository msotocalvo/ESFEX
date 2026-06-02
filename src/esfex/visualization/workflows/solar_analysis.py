"""Solar rooftop potential analysis engine.

Computes per-building PV potential using pvlib for solar position
and irradiance decomposition, with optional shading from nearby buildings.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PySide6.QtCore import QThread, Signal

from esfex.utils.temporal import HOURS_STD_YEAR

logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    """Configuration for rooftop PV analysis."""

    # Panel specifications
    module_efficiency: float = 0.21
    module_power_w: float = 400.0
    module_area_m2: float = 2.0
    performance_ratio: float = 0.80
    system_losses: float = 0.14

    # Roof suitability
    suitable_fraction: float = 0.30
    min_building_area_m2: float = 20.0
    default_tilt: float = 0.0      # 0 = auto from latitude
    default_azimuth: float = 180.0  # south-facing

    # Shading
    enable_shading: bool = True
    shading_search_radius_m: float = 50.0

    def effective_tilt(self, latitude: float) -> float:
        """Return tilt angle; auto-compute from latitude if default_tilt == 0."""
        if self.default_tilt > 0:
            return self.default_tilt
        # Rule of thumb: optimal tilt ~ |latitude|
        return abs(latitude)

    def effective_azimuth(self, latitude: float) -> float:
        """South-facing in northern hemisphere, north-facing in southern."""
        if latitude >= 0:
            return 180.0
        return 0.0


@dataclass
class BuildingResult:
    """Per-building analysis result."""

    building_id: Any = None
    usable_roof_area: float = 0.0
    capacity_kw: float = 0.0
    annual_kwh: float = 0.0
    specific_yield: float = 0.0  # kWh/kWp/year
    shading_loss: float = 0.0    # fraction 0-1
    suitable: bool = False


@dataclass
class AnalysisSummary:
    """Aggregated analysis results."""

    total_buildings: int = 0
    suitable_buildings: int = 0
    total_usable_area_m2: float = 0.0
    total_capacity_kwp: float = 0.0
    total_annual_yield_mwh: float = 0.0
    avg_capacity_factor: float = 0.0
    avg_specific_yield: float = 0.0
    building_results: list[BuildingResult] = field(default_factory=list)


class SolarRooftopAnalyzer(QThread):
    """Compute per-building rooftop PV potential.

    Runs in a background thread, emitting progress updates.
    """

    progress = Signal(int, str)     # percent, message
    finished = Signal(object)       # AnalysisSummary
    error = Signal(str)

    def __init__(
        self,
        buildings_gdf,       # GeoDataFrame with footprints
        solar_data: dict,    # from SolarResourceFetcher
        config: AnalysisConfig,
        parent=None,
    ):
        super().__init__(parent)
        self.buildings_gdf = buildings_gdf
        self.solar_data = solar_data
        self.config = config
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            result = self._analyze()
            if not self._cancelled:
                self.finished.emit(result)
        except Exception as exc:
            logger.exception("SolarRooftopAnalyzer error")
            self.error.emit(str(exc))

    def _analyze(self) -> AnalysisSummary:
        import pvlib

        cfg = self.config
        lat = self.solar_data["lat"]
        lon = self.solar_data["lon"]
        irr_data = self.solar_data["data"]

        self.progress.emit(5, "Computing solar position...")

        # Solar position for the year
        loc = pvlib.location.Location(latitude=lat, longitude=lon)
        solpos = loc.get_solarposition(irr_data.index)

        # Compute plane-of-array irradiance (shared for all buildings)
        tilt = cfg.effective_tilt(lat)
        azimuth = cfg.effective_azimuth(lat)

        self.progress.emit(10, f"Computing POA irradiance (tilt={tilt:.1f}, az={azimuth:.0f})...")

        ghi = irr_data.get("ghi", irr_data.get("G(h)", None))
        dni = irr_data.get("dni", irr_data.get("Gb(n)", None))
        dhi = irr_data.get("dhi", irr_data.get("Gd(h)", None))

        if ghi is None or dni is None or dhi is None:
            raise ValueError(
                f"Solar data missing GHI/DNI/DHI columns. "
                f"Available: {list(irr_data.columns)}"
            )

        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt,
            surface_azimuth=azimuth,
            solar_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
            dni=dni,
            ghi=ghi,
            dhi=dhi,
            model="isotropic",
        )

        # Annual POA irradiation in kWh/m² (from W/m² hourly)
        annual_poa_kwh_m2 = poa["poa_global"].clip(lower=0).sum() / 1000.0

        self.progress.emit(15, f"Annual POA: {annual_poa_kwh_m2:.0f} kWh/m²")

        # Prepare building height spatial index for shading
        gdf = self.buildings_gdf.copy()
        has_heights = "height" in gdf.columns and gdf["height"].notna().any()

        # Project to UTM for metric calculations
        utm_crs = gdf.estimate_utm_crs()
        gdf_utm = gdf.to_crs(utm_crs)

        # Spatial index for neighbor lookups
        sindex = gdf_utm.sindex if cfg.enable_shading and has_heights else None

        # Process each building
        n_buildings = len(gdf)
        results = []

        self.progress.emit(20, f"Analyzing {n_buildings} buildings...")

        for i, (idx, bldg) in enumerate(gdf_utm.iterrows()):
            if self._cancelled:
                return AnalysisSummary()

            if i % 100 == 0 and n_buildings > 100:
                pct = 20 + int(75 * i / n_buildings)
                self.progress.emit(pct, f"Building {i+1}/{n_buildings}...")

            br = BuildingResult(building_id=idx)

            # Footprint area
            area = bldg.geometry.area
            if "footprint_area_m2" in gdf.columns and not math.isnan(
                gdf.loc[idx, "footprint_area_m2"]
            ):
                area = gdf.loc[idx, "footprint_area_m2"]

            usable = area * cfg.suitable_fraction
            br.usable_roof_area = usable

            if usable < cfg.min_building_area_m2:
                results.append(br)
                continue

            br.suitable = True

            # Capacity
            n_modules = usable / cfg.module_area_m2
            br.capacity_kw = n_modules * cfg.module_power_w / 1000.0

            # Shading factor
            shading_factor = 1.0
            if cfg.enable_shading and has_heights and sindex is not None:
                shading_factor = self._estimate_shading(
                    bldg, gdf_utm, sindex, solpos, cfg.shading_search_radius_m
                )
                br.shading_loss = 1.0 - shading_factor

            # Annual yield
            br.annual_kwh = (
                annual_poa_kwh_m2
                * cfg.module_efficiency
                * usable
                * cfg.performance_ratio
                * (1.0 - cfg.system_losses)
                * shading_factor
            )

            # Specific yield (kWh/kWp/year)
            if br.capacity_kw > 0:
                br.specific_yield = br.annual_kwh / br.capacity_kw

            results.append(br)

        # Aggregate
        suitable = [r for r in results if r.suitable]
        total_cap = sum(r.capacity_kw for r in suitable)
        total_yield = sum(r.annual_kwh for r in suitable)
        avg_sy = np.mean([r.specific_yield for r in suitable]) if suitable else 0
        avg_cf = (total_yield / (total_cap * HOURS_STD_YEAR) * 100) if total_cap > 0 else 0

        summary = AnalysisSummary(
            total_buildings=n_buildings,
            suitable_buildings=len(suitable),
            total_usable_area_m2=sum(r.usable_roof_area for r in suitable),
            total_capacity_kwp=total_cap,
            total_annual_yield_mwh=total_yield / 1000.0,
            avg_capacity_factor=avg_cf,
            avg_specific_yield=avg_sy,
            building_results=results,
        )

        self.progress.emit(100, "Analysis complete")
        return summary

    def _estimate_shading(
        self, building, gdf_utm, sindex, solpos, radius_m: float
    ) -> float:
        """Estimate shading factor (0-1) from nearby taller buildings.

        Uses simplified shadow geometry: for each representative solar hour,
        check if nearby buildings cast shadows that reach this building.

        Returns fraction of sunlight NOT blocked (1.0 = no shading).
        """
        bldg_height = building.get("height", 0)
        if bldg_height is None or math.isnan(bldg_height):
            bldg_height = 0

        centroid = building.geometry.centroid
        buffer = centroid.buffer(radius_m)

        # Find nearby buildings
        candidates_idx = list(sindex.intersection(buffer.bounds))
        if not candidates_idx:
            return 1.0

        nearby = gdf_utm.iloc[candidates_idx]

        # Filter to taller buildings
        taller = nearby[
            nearby["height"].notna()
            & (nearby["height"] > bldg_height + 1.0)
        ]
        if taller.empty:
            return 1.0

        # Sample solar positions: pick 3 representative times per day-of-year
        # Use monthly midpoints, 3 hours each (9am, noon, 3pm solar time approx)
        sun_up = solpos[solpos["apparent_elevation"] > 10]
        if sun_up.empty:
            return 1.0

        # Sample ~36 positions (12 months * 3 hours)
        sample_indices = np.linspace(0, len(sun_up) - 1, min(36, len(sun_up)), dtype=int)
        sampled = sun_up.iloc[sample_indices]

        shaded_count = 0
        total_count = len(sampled)

        for _, sp in sampled.iterrows():
            elev = sp["apparent_elevation"]
            azi = sp["azimuth"]

            if elev <= 0:
                continue

            # For each taller building, compute shadow reach
            elev_rad = math.radians(elev)
            azi_rad = math.radians(azi)
            tan_elev = math.tan(elev_rad)

            for _, tall in taller.iterrows():
                h_diff = tall["height"] - bldg_height
                shadow_length = h_diff / tan_elev if tan_elev > 0.01 else 9999

                # Shadow direction (opposite to sun azimuth)
                shadow_dx = -math.sin(azi_rad) * shadow_length
                shadow_dy = -math.cos(azi_rad) * shadow_length

                # Check if shadow from tall building reaches our building
                tall_centroid = tall.geometry.centroid
                dist = centroid.distance(tall_centroid)

                if dist < shadow_length:
                    # Simplified: if distance < shadow_length and direction matches
                    dx = centroid.x - tall_centroid.x
                    dy = centroid.y - tall_centroid.y
                    if dist > 0:
                        dot = (dx * shadow_dx + dy * shadow_dy) / (dist * shadow_length)
                        if dot > 0.5:  # roughly in shadow direction
                            shaded_count += 1
                            break  # one shadow is enough for this time step

        shading_fraction = shaded_count / total_count if total_count > 0 else 0
        return max(0.0, 1.0 - shading_fraction)
