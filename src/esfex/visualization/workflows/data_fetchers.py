"""Background data fetchers for solar rooftop workflow.

Each fetcher is a QThread that downloads data from external sources
and emits progress/finished/error signals.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class BuildingFetcher(QThread):
    """Fetch building footprints from a selected data source.

    Sources:
        - overture: Overture Maps Foundation (DuckDB cloud Parquet)
        - microsoft: Microsoft Global ML Building Footprints
        - google: Google Open Buildings (DuckDB cloud Parquet)
    """

    progress = Signal(int, str)   # percent, message
    finished = Signal(object)     # GeoDataFrame
    error = Signal(str)

    def __init__(
        self,
        source: str,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.south, self.west, self.north, self.east = bounds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self.source == "overture":
                gdf = self._fetch_overture()
            elif self.source == "microsoft":
                gdf = self._fetch_microsoft()
            elif self.source == "google":
                gdf = self._fetch_google()
            else:
                self.error.emit(f"Unknown source: {self.source}")
                return

            if self._cancelled:
                return

            self.finished.emit(gdf)

        except Exception as exc:
            logger.exception("BuildingFetcher error")
            self.error.emit(str(exc))

    # ── Overture Maps ────────────────────────────────────────────

    @staticmethod
    def _discover_overture_release(conn) -> str:
        """Discover the latest Overture Maps release from S3.

        Tries known recent releases in reverse chronological order.
        """
        import datetime

        # Generate candidate release dates (21st of each month, last 6 months)
        today = datetime.date.today()
        candidates = []
        for months_back in range(0, 6):
            y = today.year
            m = today.month - months_back
            while m <= 0:
                m += 12
                y -= 1
            # Overture releases on ~21st of each month
            for day in (22, 21, 20, 15):
                candidates.append(f"{y}-{m:02d}-{day:02d}.0")

        for release in candidates:
            try:
                # Lightweight probe: read 1 row to see if the release exists
                probe = f"""
                SELECT 1
                FROM read_parquet(
                    's3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*',
                    filename=true, hive_partitioning=true
                ) LIMIT 1
                """
                conn.execute(probe).fetchone()
                return release
            except Exception as exc:
                # Each release probe failing is expected during discovery
                # (the next release may exist). Only the aggregate failure
                # below raises. Log at debug so the per-release reason is
                # available when diagnosing the aggregate RuntimeError.
                import logging
                logging.getLogger(__name__).debug(
                    "Overture release %s probe failed: %s", release, exc,
                )
                continue

        raise RuntimeError(
            "Could not discover any Overture Maps release. "
            "Check your internet connection."
        )

    def _fetch_overture(self):
        import duckdb
        import geopandas as gpd
        from shapely import wkt

        self.progress.emit(5, "Connecting to Overture Maps...")

        conn = duckdb.connect()
        conn.execute("INSTALL spatial; LOAD spatial;")
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region='us-west-2';")

        self.progress.emit(10, "Discovering latest release...")

        release = self._discover_overture_release(conn)
        logger.info(f"Using Overture Maps release: {release}")

        self.progress.emit(20, f"Querying buildings (release {release})...")

        # Query buildings with bbox filter using Overture's bbox columns
        query = f"""
        SELECT
            id,
            names.primary AS name,
            height,
            num_floors,
            roof_shape,
            ST_AsText(geometry) AS geometry_wkt
        FROM read_parquet(
            's3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*',
            filename=true,
            hive_partitioning=true
        )
        WHERE bbox.xmin BETWEEN {self.west} AND {self.east}
          AND bbox.ymin BETWEEN {self.south} AND {self.north}
        """

        df = conn.execute(query).fetchdf()
        conn.close()

        if self._cancelled:
            return None

        self.progress.emit(70, f"Processing {len(df)} buildings...")

        if df.empty:
            return gpd.GeoDataFrame(
                columns=["id", "name", "height", "num_floors",
                          "roof_shape", "geometry"],
                geometry="geometry",
                crs="EPSG:4326",
            )

        # Convert WKT geometry to shapely objects
        df["geometry"] = df["geometry_wkt"].apply(wkt.loads)
        gdf = gpd.GeoDataFrame(df.drop(columns=["geometry_wkt"]),
                                geometry="geometry", crs="EPSG:4326")

        # Compute footprint area in m² (project to UTM)
        utm_crs = gdf.estimate_utm_crs()
        gdf["footprint_area_m2"] = gdf.to_crs(utm_crs).geometry.area

        self.progress.emit(100, f"Loaded {len(gdf)} buildings")
        return gdf

    # ── Microsoft ML Footprints ──────────────────────────────────

    @staticmethod
    def _quadkey_to_bbox(quadkey: str):
        """Convert a QuadKey to a (west, south, east, north) bounding box."""
        n = len(quadkey)
        west, south, east, north = -180.0, -90.0, 180.0, 90.0
        for i in range(n):
            mid_lon = (west + east) / 2
            mid_lat = (south + north) / 2
            digit = int(quadkey[i])
            if digit in (0, 2):
                east = mid_lon
            else:
                west = mid_lon
            if digit in (0, 1):
                south = mid_lat
            else:
                north = mid_lat
        return west, south, east, north

    def _fetch_microsoft(self):
        import gzip
        import io

        import geopandas as gpd
        import pandas as pd
        import requests
        from shapely import wkt
        from shapely.geometry import box

        self.progress.emit(10, "Downloading Microsoft footprint index...")

        index_url = (
            "https://minedbuildings.z5.web.core.windows.net/"
            "global-buildings/dataset-links.csv"
        )
        links_df = pd.read_csv(index_url)

        self.progress.emit(20, "Finding relevant tiles...")

        domain_box = box(self.west, self.south, self.east, self.north)

        # Filter tiles by QuadKey bounding box overlap
        relevant_rows = []
        for _, row in links_df.iterrows():
            qk = str(row.get("QuadKey", ""))
            if not qk:
                continue
            tw, ts, te, tn = self._quadkey_to_bbox(qk)
            tile_box = box(tw, ts, te, tn)
            if tile_box.intersects(domain_box):
                relevant_rows.append(row)

        if not relevant_rows:
            self.progress.emit(100, "No Microsoft tiles overlap this area")
            return gpd.GeoDataFrame(
                columns=["geometry", "height", "footprint_area_m2"],
                geometry="geometry", crs="EPSG:4326",
            )

        self.progress.emit(30, f"Downloading {len(relevant_rows)} tiles...")

        all_gdfs = []
        for i, row in enumerate(relevant_rows):
            if self._cancelled:
                return None
            pct = 30 + int(55 * i / len(relevant_rows))
            self.progress.emit(pct, f"Downloading tile {i+1}/{len(relevant_rows)}...")

            try:
                resp = requests.get(row["Url"], timeout=60)
                if resp.status_code != 200:
                    continue
                tile_df = pd.read_json(
                    gzip.open(io.BytesIO(resp.content)), lines=True
                )
                if tile_df.empty:
                    continue
                tile_df["geometry"] = tile_df["geometry"].apply(wkt.loads)
                tile_gdf = gpd.GeoDataFrame(
                    tile_df, geometry="geometry", crs="EPSG:4326"
                )
                filtered = tile_gdf[tile_gdf.intersects(domain_box)]
                if not filtered.empty:
                    all_gdfs.append(filtered)
            except Exception as exc:
                logger.debug(f"Skipping tile: {exc}")
                continue

        self.progress.emit(90, "Combining results...")

        if not all_gdfs:
            return gpd.GeoDataFrame(
                columns=["geometry", "height", "footprint_area_m2"],
                geometry="geometry", crs="EPSG:4326",
            )

        gdf = pd.concat(all_gdfs, ignore_index=True)
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326")

        utm_crs = gdf.estimate_utm_crs()
        gdf["footprint_area_m2"] = gdf.to_crs(utm_crs).geometry.area

        self.progress.emit(100, f"Loaded {len(gdf)} buildings")
        return gdf

    # ── Google Open Buildings ────────────────────────────────────

    def _fetch_google(self):
        import duckdb
        import geopandas as gpd
        from shapely import wkt

        self.progress.emit(10, "Connecting to Google Open Buildings...")

        conn = duckdb.connect()
        conn.execute("INSTALL spatial; LOAD spatial;")
        conn.execute("INSTALL httpfs; LOAD httpfs;")

        self.progress.emit(20, "Querying buildings in bounding box...")

        # Google Open Buildings via source.coop
        # Try multiple known URLs since the hosting path may change
        urls = [
            "https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/**/*.parquet",
            "https://data.source.coop/cholmes/google-open-buildings/geoparquet-by-country/**/*.parquet",
        ]

        df = None
        last_err = None
        for url in urls:
            if self._cancelled:
                conn.close()
                return None
            try:
                query = f"""
                SELECT
                    ST_AsText(geometry) AS geometry_wkt,
                    confidence,
                    area_in_meters AS footprint_area_m2
                FROM read_parquet('{url}', hive_partitioning=true)
                WHERE bbox.xmin BETWEEN {self.west} AND {self.east}
                  AND bbox.ymin BETWEEN {self.south} AND {self.north}
                """
                df = conn.execute(query).fetchdf()
                break
            except Exception as exc:
                last_err = exc
                self.progress.emit(30, "Trying alternative URL...")
                continue

        conn.close()

        if df is None:
            raise RuntimeError(
                f"Could not fetch Google Open Buildings data: {last_err}"
            )

        if self._cancelled:
            return None

        self.progress.emit(70, f"Processing {len(df)} buildings...")

        if df.empty:
            return gpd.GeoDataFrame(
                columns=["geometry", "confidence", "footprint_area_m2"],
                geometry="geometry",
                crs="EPSG:4326",
            )

        df["geometry"] = df["geometry_wkt"].apply(wkt.loads)
        gdf = gpd.GeoDataFrame(
            df.drop(columns=["geometry_wkt"]),
            geometry="geometry",
            crs="EPSG:4326",
        )

        # Compute area if not provided
        if "footprint_area_m2" not in gdf.columns or gdf["footprint_area_m2"].isna().all():
            utm_crs = gdf.estimate_utm_crs()
            gdf["footprint_area_m2"] = gdf.to_crs(utm_crs).geometry.area

        self.progress.emit(100, f"Loaded {len(gdf)} buildings")
        return gdf


class SolarResourceFetcher(QThread):
    """Fetch solar resource data for a location.

    Sources:
        - pvgis: PVGIS (JRC) via pvlib — no API key required
        - nsrdb: NSRDB (NREL) via pvlib — requires API key
    """

    progress = Signal(int, str)
    finished = Signal(object)   # dict with DataFrame + metadata
    error = Signal(str)

    def __init__(
        self,
        source: str,
        lat: float,
        lon: float,
        year: int = 2022,
        api_key: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.lat = lat
        self.lon = lon
        self.year = year
        self.api_key = api_key

    def run(self):
        try:
            if self.source == "pvgis":
                result = self._fetch_pvgis()
            elif self.source == "nsrdb":
                result = self._fetch_nsrdb()
            else:
                self.error.emit(f"Unknown source: {self.source}")
                return
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("SolarResourceFetcher error")
            self.error.emit(str(exc))

    def _fetch_pvgis(self) -> dict[str, Any]:
        import pvlib

        self.progress.emit(20, f"Fetching PVGIS TMY data for ({self.lat:.3f}, {self.lon:.3f})...")

        # Use TMY (Typical Meteorological Year) which provides proper
        # GHI/DNI/DHI columns — better for annual potential estimates.
        # PVGIS auto-selects the best radiation database for the location.
        result = pvlib.iotools.get_pvgis_tmy(
            latitude=self.lat,
            longitude=self.lon,
            outputformat="json",
            map_variables=True,
        )

        # pvlib >= 0.13.0 returns (data, meta); older returned 4 values
        if len(result) == 2:
            data, meta = result
        else:
            data = result[0]
            meta = result[-1]

        self.progress.emit(70, f"Loaded {len(data)} hourly TMY records")

        # TMY columns with map_variables=True:
        # ghi, dni, dhi, temp_air, wind_speed, relative_humidity, pressure, etc.
        required = {"ghi", "dni", "dhi"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(
                f"PVGIS response missing required columns: {missing}. "
                f"Got: {list(data.columns)}"
            )

        self.progress.emit(100, "Solar resource data ready")
        return {
            "data": data,
            "metadata": meta,
            "source": "PVGIS TMY",
            "lat": self.lat,
            "lon": self.lon,
            "year": self.year,
        }

    def _fetch_nsrdb(self) -> dict[str, Any]:
        import pvlib

        if not self.api_key:
            raise ValueError("NSRDB requires an API key (get one at developer.nrel.gov)")

        self.progress.emit(20, f"Fetching NSRDB data for ({self.lat:.3f}, {self.lon:.3f})...")

        data, meta = pvlib.iotools.get_psm3(
            latitude=self.lat,
            longitude=self.lon,
            api_key=self.api_key,
            email="esfex@example.com",
            names=str(self.year),
            interval=60,
            attributes=(
                "air_temperature", "dhi", "dni", "ghi",
                "surface_albedo", "wind_speed",
            ),
        )

        # Standardize column names
        col_map = {}
        for col in data.columns:
            low = col.lower()
            if "ghi" in low:
                col_map[col] = "ghi"
            elif "dni" in low:
                col_map[col] = "dni"
            elif "dhi" in low:
                col_map[col] = "dhi"
            elif "temperature" in low:
                col_map[col] = "temp_air"
            elif "wind" in low:
                col_map[col] = "wind_speed"
        if col_map:
            data = data.rename(columns=col_map)

        self.progress.emit(100, f"Loaded {len(data)} hourly records from NSRDB")
        return {
            "data": data,
            "metadata": meta,
            "source": "NSRDB (PSM3)",
            "lat": self.lat,
            "lon": self.lon,
            "year": self.year,
        }
