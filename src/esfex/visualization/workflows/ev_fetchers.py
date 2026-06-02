"""Background data fetchers for the EV & V2G Assessment Workflow.

Thin QThread wrappers around evrex.data functions.  Each fetcher emits
``progress``, ``finished``, and ``error`` signals for GUI integration.

Sources (via evrex):
    - OpenStreetMap (Overpass API): Charging stations, road network
    - World Bank API: GDP, urbanization, population, vehicle ownership
    - IMF DataMapper API: GDP growth, inflation
    - Bundled IEA Global EV Data Explorer: EV stock by country (2010-2024)
    - Bundled BNEF / IEA battery cost data: Pack costs (2013-2024 + projection)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# OSM Charging Station Fetcher
# ══════════════════════════════════════════════════════════════════


class OSMChargingStationFetcher(QThread):
    """Query Overpass API for EV charging stations within bounds."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self._bounds = bounds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(10, "Querying charging stations...")
            from evrex.data.osm import fetch_charging_stations

            result = fetch_charging_stations(self._bounds)

            if self._cancelled:
                return

            n = result.get("charging_stations", 0)
            self.progress.emit(100, f"Found {n} charging stations.")
            self.finished.emit(result)

        except Exception as exc:
            logger.exception("OSMChargingStationFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# OSM Road Network Fetcher
# ══════════════════════════════════════════════════════════════════


class OSMRoadNetworkFetcher(QThread):
    """Compute road density (km of road per km^2) within bounds."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self._bounds = bounds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(20, "Querying road network...")
            from evrex.data.osm import fetch_road_density

            result = fetch_road_density(self._bounds)

            if self._cancelled:
                return

            density = result.get("road_density_km2", 0)
            self.progress.emit(100, f"Road density: {density:.1f} km/km\u00b2")
            self.finished.emit(result)

        except Exception as exc:
            logger.exception("OSMRoadNetworkFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# World Bank EV-relevant indicators
# ══════════════════════════════════════════════════════════════════


class WorldBankEVFetcher(QThread):
    """Fetch EV-relevant indicators from the World Bank API."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(20, "Fetching World Bank data...")
            from evrex.data.world_bank import fetch_world_bank_ev_data

            if self._cancelled:
                return

            result = fetch_world_bank_ev_data(self._iso)

            if self._cancelled:
                return

            self.progress.emit(100, "World Bank data fetched.")
            self.finished.emit(result)

        except Exception as exc:
            logger.exception("WorldBankEVFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# IMF EV fetcher
# ══════════════════════════════════════════════════════════════════


class IMFEVFetcher(QThread):
    """Fetch GDP growth and inflation from the IMF DataMapper API."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(20, "Fetching IMF data...")
            from evrex.data.world_bank import fetch_imf_ev_data

            if self._cancelled:
                return

            result = fetch_imf_ev_data(self._iso)

            if self._cancelled:
                return

            self.progress.emit(100, "IMF data fetched.")
            self.finished.emit(result)

        except Exception as exc:
            logger.exception("IMFEVFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# IEA Global EV Data Explorer (bundled in evrex)
# ══════════════════════════════════════════════════════════════════


class IEAEVDataFetcher(QThread):
    """Provide bundled IEA EV stock data for validation."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, country_iso: str, parent=None):
        super().__init__(parent)
        self._iso = country_iso.upper()

    def run(self):
        try:
            self.progress.emit(30, "Loading IEA EV data...")
            from evrex.data.iea import get_iea_ev_stock

            vd = get_iea_ev_stock(self._iso)

            self.progress.emit(100, f"IEA data loaded: {len(vd.years)} years")
            self.finished.emit({
                "label": vd.label,
                "years": vd.years,
                "ev_stock": vd.ev_stock,
                "source": vd.source,
            })

        except Exception as exc:
            logger.exception("IEAEVDataFetcher error")
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════
# Battery cost data (BNEF, bundled in evrex)
# ══════════════════════════════════════════════════════════════════


class EVBatteryCostFetcher(QThread):
    """Provide battery cost learning curve and projections."""

    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, annual_decline_rate: float = 0.07, parent=None):
        super().__init__(parent)
        self._decline = annual_decline_rate

    def run(self):
        try:
            self.progress.emit(30, "Loading battery cost data...")
            from evrex.data.battery_cost import BNEF_BATTERY_COSTS, project_battery_costs

            self.progress.emit(60, "Projecting battery costs...")
            trajectory = project_battery_costs(
                annual_decline_rate=self._decline,
            )

            self.progress.emit(100, "Battery cost data ready.")
            self.finished.emit({
                "battery_cost_per_kwh": BNEF_BATTERY_COSTS[2024],
                "battery_cost_trajectory": trajectory,
                "annual_decline_rate": self._decline,
            })

        except Exception as exc:
            logger.exception("EVBatteryCostFetcher error")
            self.error.emit(str(exc))
