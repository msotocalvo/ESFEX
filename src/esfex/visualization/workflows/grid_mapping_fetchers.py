"""Background data fetchers for the Grid Builder workflow.

Three data sources:
  1. OpenStreetMap (Overpass API) — substations, generators, lines, transformers,
     converters, storage
  2. WRI Global Power Plant Database — power plants with capacity / fuel
  3. GridFinder — predicted transmission / distribution line routes

Each fetcher is a QThread with progress / finished / error signals and returns
a list of :class:`GridFeature` in a normalized intermediate format.
"""

from __future__ import annotations

import io
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ── Normalized intermediate format ───────────────────────────────────

_RENEWABLE_SOURCES = frozenset({
    "solar", "wind", "hydro", "tidal", "wave", "geothermal", "biomass",
    "biogas", "waste",
})


@dataclass
class GridFeature:
    """A single grid element normalized from any source."""

    source: str                     # "osm", "wri", "gem", "gridfinder"
    feature_type: str               # "substation", "generator", "line",
                                    # "transformer", "battery", "converter"
    name: str
    latitude: float
    longitude: float
    # Electrical
    voltage_kv: float = 0.0
    voltage_kv_secondary: float = 0.0   # multi-voltage substations
    capacity_mw: float = 0.0
    frequency_hz: float = 50.0
    current_type: str = "AC"             # "AC" or "DC"
    # Generator-specific
    fuel: str = ""
    gen_type: str = ""                   # "Renewable" / "Non-renewable"
    # Battery-specific (energy capacity in MWh, 0 = unknown → builder defaults)
    energy_mwh: float = 0.0
    # Line geometry  [(lat, lng), ...]
    line_coords: list[tuple[float, float]] = field(default_factory=list)
    num_circuits: int = 1
    # Enrichment fields (from any source)
    operator: str = ""                   # plant owner/operator
    commissioning_year: int = 0          # year commissioned (0 = unknown)
    technology: str = ""                 # e.g. "CCGT", "Onshore", "Offshore"
    # Raw data for debugging
    raw_tags: dict[str, Any] = field(default_factory=dict)
    osm_id: str = ""
    # User toggle in review step
    include: bool = True


# ── Voltage / tag helpers ────────────────────────────────────────────


def _normalize_voltage_kv(value: float) -> float:
    """Values > 1200 are assumed Volts and divided by 1000."""
    return value / 1000.0 if value > 1200 else value


def _parse_voltage_tag(raw: str) -> list[float]:
    """Parse an OSM ``voltage`` tag that may be semicolon-separated.

    Returns a list of voltages in kV (descending order).
    E.g. ``"220000;110000"`` → ``[220.0, 110.0]``.
    """
    voltages: list[float] = []
    for part in raw.replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            voltages.append(_normalize_voltage_kv(float(part)))
        except ValueError:
            continue
    voltages.sort(reverse=True)
    return voltages


def _parse_year_tag(raw: str) -> int:
    """Extract a 4-digit year from a date string like '2015', '2015-03-01'.

    Returns 0 if unparseable.
    """
    if not raw:
        return 0
    raw = raw.strip()
    # Try to extract a 4-digit year
    m = re.search(r"(\d{4})", raw)
    if m:
        year = int(m.group(1))
        if 1900 <= year <= 2100:
            return year
    return 0


def _parse_energy_tag(raw: str) -> float:
    """Parse energy capacity tags. Returns MWh.

    Handles patterns like ``"100 MWh"``, ``"50000 kWh"``, ``"200 Wh"``.
    """
    raw = raw.strip()
    if not raw:
        return 0.0
    low = raw.lower()
    multiplier = 1.0
    for suffix, mult in [("mwh", 1.0), ("kwh", 0.001), ("wh", 1e-6)]:
        if low.endswith(suffix):
            multiplier = mult
            raw = raw[: -len(suffix)].strip()
            break
    try:
        return float(raw) * multiplier
    except ValueError:
        return 0.0


def _parse_capacity_tag(raw: str) -> float:
    """Parse ``generator:output:electricity`` or similar capacity tags.

    Handles patterns like ``"50 MW"``, ``"120 kW"``, ``"200000 W"``, ``"50"``.
    Returns MW.
    """
    raw = raw.strip()
    if not raw:
        return 0.0
    # Remove known unit suffixes
    low = raw.lower()
    multiplier = 1.0
    for suffix, mult in [("mw", 1.0), ("kw", 0.001), (" w", 1e-6),
                          ("mva", 1.0), ("kva", 0.001)]:
        if low.endswith(suffix):
            multiplier = mult
            raw = raw[: -len(suffix)].strip()
            break
    try:
        return float(raw) * multiplier
    except ValueError:
        return 0.0


# ── OSM fuel / source mapping ───────────────────────────────────────

_OSM_SOURCE_TO_FUEL: dict[str, tuple[str, str]] = {
    # (ESFEX fuel, gen_type)
    "solar": ("Solar", "Renewable"),
    "wind": ("Wind", "Renewable"),
    "hydro": ("Water", "Renewable"),
    "tidal": ("Water", "Renewable"),
    "wave": ("Water", "Renewable"),
    "geothermal": ("Geothermal", "Renewable"),
    "biomass": ("Biomass", "Renewable"),
    "biogas": ("Biogas", "Renewable"),
    "waste": ("Waste", "Renewable"),
    "gas": ("Natural Gas", "Non-renewable"),
    "coal": ("Coal", "Non-renewable"),
    "oil": ("Diesel", "Non-renewable"),
    "diesel": ("Diesel", "Non-renewable"),
    "nuclear": ("Nuclear", "Non-renewable"),
    "battery": ("None", "Storage"),
}

_OSM_CONTENT_TO_FUEL: dict[str, str] = {
    "oil": "Diesel", "petroleum": "Diesel", "diesel": "Diesel",
    "fuel_oil": "Diesel", "gasoline": "Diesel", "crude": "Diesel",
    "kerosene": "Diesel", "jet_fuel": "Diesel", "naphtha": "Diesel",
    "bitumen": "Diesel", "bunker": "Diesel",
    "gas": "Natural Gas", "natural_gas": "Natural Gas",
    "lng": "Natural Gas", "lpg": "Natural Gas", "cng": "Natural Gas",
    "propane": "Natural Gas", "butane": "Natural Gas",
    "coal": "Coal",
}


# ── Fuel cargo disambiguation ─────────────────────────────────────

_FUEL_CARGO_KEYWORDS = frozenset({
    "oil", "fuel", "lpg", "lng", "liquid_bulk",
    "petroleum", "crude", "diesel", "gasoline",
    "natural_gas", "cng",
})


def _is_fuel_cargo(cargo: str) -> bool:
    """Return True if the OSM ``cargo`` tag indicates fuel-related goods."""
    if not cargo:
        return False
    c = cargo.lower()
    return any(kw in c for kw in _FUEL_CARGO_KEYWORDS)


def _fuel_from_cargo(cargo: str) -> str:
    """Infer a ESFEX fuel name from an OSM ``cargo`` tag value."""
    c = cargo.lower()
    if any(k in c for k in ("lng", "natural_gas", "cng", "lpg")):
        return "Natural Gas"
    if "coal" in c:
        return "Coal"
    return "Diesel"


def _detect_fuel_from_tags(tags: dict[str, str]) -> str:
    """Detect fuel type from OSM tags (content, product, substance)."""
    for key in ("content", "product", "substance"):
        raw = tags.get(key, "").lower().strip()
        if raw:
            for fragment, fuel in _OSM_CONTENT_TO_FUEL.items():
                if fragment in raw:
                    return fuel
    return ""


# ── WRI fuel mapping ────────────────────────────────────────────────

_WRI_FUEL_MAP: dict[str, tuple[str, str]] = {
    "Solar": ("Solar", "Renewable"),
    "Wind": ("Wind", "Renewable"),
    "Hydro": ("Water", "Renewable"),
    "Gas": ("Natural Gas", "Non-renewable"),
    "Oil": ("Diesel", "Non-renewable"),
    "Coal": ("Coal", "Non-renewable"),
    "Nuclear": ("Nuclear", "Non-renewable"),
    "Biomass": ("Biomass", "Renewable"),
    "Geothermal": ("Geothermal", "Renewable"),
    "Wave and Tidal": ("Water", "Renewable"),
    "Petcoke": ("Coal", "Non-renewable"),
    "Cogeneration": ("Natural Gas", "Non-renewable"),
    "Storage": ("None", "Storage"),
    "Waste": ("Waste", "Renewable"),
    "Other": ("Other", "Non-renewable"),
}


# ── Fuel refinement (broad → specific) ──────────────────────────────


def _refine_fuel(
    fuel: str,
    gen_type: str,
    *,
    technology: str = "",
    method: str = "",
    fuels_detail: str = "",
    capacity_mw: float = 0.0,
    commissioning_year: int = 0,
) -> tuple[str, str]:
    """Refine a coarse (fuel, gen_type) using extra plant hints.

    The upstream maps (OSM source, WRI primary_fuel, GEM Type) collapse
    distinct technologies into one bucket — most importantly:
      • "oil"/"Oil" hides the split between reciprocating diesel engines
        (small, fast, peakers) and HFO steam plants (large, slow, like
        coal in commitment behaviour).
      • "oil/gas" or "Cogeneration" lumps gas turbines / CCGT with
        oil-fired steam.

    This function inspects the technology / method strings and applies
    a capacity+age heuristic to recover the right canonical fuel.
    Returns ``(fuel, gen_type)`` — possibly unchanged.
    """
    t = (technology or "").lower()
    m = (method or "").lower()
    fd = (fuels_detail or "").lower()
    combined = f"{t} {m} {fd}"

    # Hints that point to steam-cycle (HFO / fuel-oil / mazut)
    is_steam = any(k in combined for k in (
        "steam", "boiler", "st ", "steam_turbine", "steamturbine",
    )) or t.strip() == "st"
    # Hints that point to reciprocating engines (diesel)
    is_ice = any(k in combined for k in (
        "reciprocat", "engine", "ice ", "diesel", "internal_combustion",
    )) or t.strip() in ("ice", "ic")
    # Hints that point to gas turbines
    is_gas_turbine = any(k in combined for k in (
        "ccgt", "ocgt", "combined_cycle", "combined cycle",
        "gas_turbine", "gas turbine", "gt ", "cc ",
    )) or t.strip() in ("gt", "cc", "ccgt", "ocgt")
    # Hints that point to oil fuel specifically
    mentions_oil = any(k in combined for k in (
        "oil", "fuel_oil", "fueloil", "hfo", "mazut", "bunker",
        "heavy", "residual", "petroleum",
    ))
    mentions_gas = "gas" in combined or "lng" in combined

    # ── Diesel bucket: split engines vs HFO steam ────────────────────
    # The "Diesel" label from upstream maps already implies a liquid
    # hydrocarbon; the question is engine vs steam-cycle.
    if fuel == "Diesel":
        if is_steam:
            return ("Fuel Oil", "Non-renewable")
        if is_ice:
            return ("Diesel", "Non-renewable")
        # No explicit tech — use capacity + age heuristic.
        # HFO steam plants are typically >=50 MW and pre-2005;
        # diesel engine farms are smaller (<50 MW) or newer.
        if capacity_mw >= 50.0 and (
            commissioning_year == 0 or commissioning_year <= 2005
        ):
            return ("Fuel Oil", "Non-renewable")
        return (fuel, gen_type)

    # ── Natural Gas bucket: split CCGT/OCGT vs oil-fired steam ──────
    if fuel == "Natural Gas":
        if is_steam and mentions_oil and not mentions_gas:
            return ("Fuel Oil", "Non-renewable")
        if is_steam and not is_gas_turbine and mentions_oil:
            return ("Fuel Oil", "Non-renewable")
        return (fuel, gen_type)

    return (fuel, gen_type)


# ── Distance helper ──────────────────────────────────────────────────


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Point-in-polygon ────────────────────────────────────────────────


def _point_in_polygon(
    lat: float, lng: float, polygon: list[tuple[float, float]],
) -> bool:
    """Ray-casting point-in-polygon test.

    *polygon* is a list of ``(lat, lng)`` vertices (closed or open ring).
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _line_intersects_polygon(
    line_coords: list[tuple[float, float]],
    polygon: list[tuple[float, float]],
) -> bool:
    """Return True if any vertex of the line lies inside the polygon."""
    for lat, lng in line_coords:
        if _point_in_polygon(lat, lng, polygon):
            return True
    return False


def filter_features_by_polygon(
    features: list[GridFeature],
    polygon: list[tuple[float, float]],
) -> list[GridFeature]:
    """Keep only features whose location falls inside *polygon*.

    For point features (substations, generators, etc.) the centroid must lie
    inside the polygon.  For line features, at least one vertex must be inside.
    """
    if not polygon or len(polygon) < 3:
        return features  # no polygon → keep all

    filtered: list[GridFeature] = []
    for f in features:
        if f.feature_type in ("line", "road") and f.line_coords:
            if _line_intersects_polygon(f.line_coords, polygon):
                filtered.append(f)
        else:
            if _point_in_polygon(f.latitude, f.longitude, polygon):
                filtered.append(f)
    return filtered


# ── Deduplication ────────────────────────────────────────────────────


def _merge_attrs(primary: GridFeature, secondary: GridFeature) -> None:
    """Fill empty fields of *primary* with values from *secondary*.

    Never overwrites a non-empty value on the primary. ``raw_tags`` are
    union-merged (primary wins on key collisions).
    """
    # Numeric: zero counts as missing
    if primary.voltage_kv == 0 and secondary.voltage_kv > 0:
        primary.voltage_kv = secondary.voltage_kv
    if (primary.voltage_kv_secondary == 0
            and secondary.voltage_kv_secondary > 0):
        primary.voltage_kv_secondary = secondary.voltage_kv_secondary
    if primary.capacity_mw == 0 and secondary.capacity_mw > 0:
        primary.capacity_mw = secondary.capacity_mw
    if primary.energy_mwh == 0 and secondary.energy_mwh > 0:
        primary.energy_mwh = secondary.energy_mwh
    if primary.num_circuits <= 1 and secondary.num_circuits > 1:
        primary.num_circuits = secondary.num_circuits
    if primary.commissioning_year == 0 and secondary.commissioning_year > 0:
        primary.commissioning_year = secondary.commissioning_year
    # String: empty counts as missing; preserve generic placeholders
    if not primary.fuel and secondary.fuel:
        primary.fuel = secondary.fuel
        if not primary.gen_type and secondary.gen_type:
            primary.gen_type = secondary.gen_type
    if not primary.gen_type and secondary.gen_type:
        primary.gen_type = secondary.gen_type
    if not primary.operator and secondary.operator:
        primary.operator = secondary.operator
    if not primary.technology and secondary.technology:
        primary.technology = secondary.technology
    if not primary.name or primary.name.startswith(
        ("Parsed", "Generator", "GEM", "Substation", "Line", "Transformer")
    ):
        if secondary.name and not secondary.name.startswith(
            ("Parsed", "Generator", "GEM", "Substation", "Line", "Transformer")
        ):
            primary.name = secondary.name
    # Geometry: prefer the one that actually has coordinates
    if not primary.line_coords and secondary.line_coords:
        primary.line_coords = secondary.line_coords
    # raw_tags: union, primary wins
    for k, v in secondary.raw_tags.items():
        if k not in primary.raw_tags or not primary.raw_tags[k]:
            primary.raw_tags[k] = v


def _line_endpoints_match(
    a: GridFeature, b: GridFeature, proximity_km: float,
) -> bool:
    """True if line endpoints of *a* and *b* coincide (either direction)."""
    if not a.line_coords or not b.line_coords:
        return False
    a_s = a.line_coords[0]
    a_e = a.line_coords[-1] if len(a.line_coords) > 1 else a_s
    b_s = b.line_coords[0]
    b_e = b.line_coords[-1] if len(b.line_coords) > 1 else b_s
    fwd = (
        _haversine_km(a_s[0], a_s[1], b_s[0], b_s[1]) < proximity_km
        and _haversine_km(a_e[0], a_e[1], b_e[0], b_e[1]) < proximity_km
    )
    rev = (
        _haversine_km(a_s[0], a_s[1], b_e[0], b_e[1]) < proximity_km
        and _haversine_km(a_e[0], a_e[1], b_s[0], b_s[1]) < proximity_km
    )
    return fwd or rev


def deduplicate_features(
    features: list[GridFeature],
    proximity_km: float = 1.0,
    capacity_tolerance: float = 0.20,
) -> list[GridFeature]:
    """Remove duplicate features across sources, merging metadata.

    Rules:
      - Generators: match by proximity + similar capacity (±tolerance).
        Keep highest-priority source; **fill empty fields from losers**.
      - Lines: prefer OSM (real geometry); GridFinder lines only kept
        where no OSM line covers the same route. Surviving OSM line
        absorbs voltage/capacity/operator from any matching GridFinder
        ghost.
      - Substations & transformers: cross-source proximity merge so a
        substation seen in two fetchers (rare today, but the API is
        ready) gets unified attributes.
      - Other types: returned as-is.
    """
    # Source priority: osm (best location) > gem (newest) > wri (oldest)
    _SRC_PRIO = {"osm": 0, "gem": 1, "wri": 2, "gridfinder": 3}

    def _proximity_dedup(
        items: list[GridFeature],
        prox_km: float,
        capacity_check: bool,
    ) -> list[GridFeature]:
        """Greedy proximity merge for point-like features.

        Spatially bucketed so the cost is ~O(n) instead of O(n^2): each item is
        only compared against others in its own and adjacent grid cells. Without
        this, a dense cluster (e.g. thousands of co-located OSM generators)
        froze the GUI for minutes."""
        import math
        from collections import defaultdict

        n = len(items)
        if n <= 1:
            return list(items)
        items.sort(key=lambda f: _SRC_PRIO.get(f.source, 9))

        # Cell size ~prox_km in both axes (longitude scaled by cos(lat) so a
        # 3x3 neighbourhood is guaranteed to contain every point within prox).
        mean_lat = sum(f.latitude for f in items) / n
        cell_lat = max(prox_km / 111.0, 1e-9)
        cell_lon = max(prox_km / (111.0 * max(math.cos(math.radians(mean_lat)),
                                              0.01)), 1e-9)
        cells = [
            (int(f.latitude // cell_lat), int(f.longitude // cell_lon))
            for f in items
        ]
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx, c in enumerate(cells):
            buckets[c].append(idx)

        kept: list[GridFeature] = []
        used = [False] * n
        for i, a in enumerate(items):
            if used[i]:
                continue
            ci, cj = cells[i]
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for j in buckets.get((ci + di, cj + dj), ()):
                        if j <= i or used[j]:
                            continue
                        b = items[j]
                        if _haversine_km(a.latitude, a.longitude,
                                         b.latitude, b.longitude) > prox_km:
                            continue
                        if capacity_check:
                            max_cap = max(a.capacity_mw, b.capacity_mw, 0.001)
                            if (abs(a.capacity_mw - b.capacity_mw) / max_cap
                                    > capacity_tolerance):
                                continue
                        used[j] = True
                        _merge_attrs(a, b)
            kept.append(a)
        return kept

    # Separate by type
    generators = [f for f in features if f.feature_type == "generator"]
    batteries = [f for f in features if f.feature_type == "battery"]
    substations = [f for f in features if f.feature_type == "substation"]
    transformers = [f for f in features if f.feature_type == "transformer"]
    lines = [f for f in features if f.feature_type == "line"]
    others = [f for f in features
              if f.feature_type not in (
                  "generator", "battery", "substation",
                  "transformer", "line",
              )]

    kept_gens = _proximity_dedup(
        generators, proximity_km, capacity_check=True,
    )
    kept_bats = _proximity_dedup(
        batteries, proximity_km, capacity_check=True,
    )
    # For static infrastructure, capacity is rarely populated; rely
    # on proximity alone.
    kept_subs = _proximity_dedup(
        substations, proximity_km, capacity_check=False,
    )
    kept_trs = _proximity_dedup(
        transformers, proximity_km * 0.5, capacity_check=False,
    )

    # --- Line dedup with cross-source enrichment ---
    osm_lines = [l for l in lines if l.source == "osm"]
    gf_lines = [l for l in lines if l.source == "gridfinder"]
    kept_lines: list[GridFeature] = list(osm_lines)

    # Whichever GridFinder line shares endpoints with an OSM line is
    # treated as the same physical asset; its non-empty attrs are
    # merged into the OSM record (e.g. voltage when OSM lacks it).
    for gf in gf_lines:
        merged = False
        for osm_l in osm_lines:
            if _line_endpoints_match(osm_l, gf, proximity_km):
                _merge_attrs(osm_l, gf)
                merged = True
                break
        if not merged:
            kept_lines.append(gf)

    return (
        others + kept_gens + kept_bats + kept_subs + kept_trs + kept_lines
    )


def _overpass_error_snippet(text: str) -> str:
    """Pull the human-readable message out of an Overpass HTML error page."""
    if not text:
        return ""
    marker = text.find("Error</strong>:")
    if marker != -1:
        start = marker + len("Error</strong>:")
        return " ".join(text[start:start + 200].split())
    return " ".join(text[:160].split())


# =====================================================================
# OSM Fetcher
# =====================================================================


class OSMGridFetcher(QThread):
    """Fetch power infrastructure from OpenStreetMap via the Overpass API.

    Returns substations, generators, lines, transformers, converters, and
    storage as normalized :class:`GridFeature` objects.
    """

    progress = Signal(int, str)
    finished = Signal(object)      # list[GridFeature]
    error = Signal(str)

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        min_voltage_kv: float = 110.0,
        min_capacity_mw: float = 1.0,
        element_types: set[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self.min_voltage_kv = min_voltage_kv
        self.min_capacity_mw = min_capacity_mw
        self.element_types = element_types or {
            "substation", "generator", "line", "transformer",
            "storage", "converter",
        }
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            features = self._fetch()
            if self._cancelled:
                return
            self.finished.emit(features)
        except Exception as exc:
            logger.exception("OSMGridFetcher error")
            self.error.emit(str(exc))

    # ── Query ────────────────────────────────────────────────────

    def _fetch(self) -> list[GridFeature]:
        import time

        import overpy
        import requests

        self.progress.emit(5, "Connecting to Overpass API...")
        api = overpy.Overpass(url=self._OVERPASS_ENDPOINTS[0])
        # Overpass rejects urllib's default User-Agent with HTTP 406, so bypass
        # overpy's urlopen-based transport and POST via requests with an
        # identifying UA; parse_json still does the heavy lifting.
        headers = {
            "User-Agent": (
                "esfex-gridbuilder/1.0 "
                "(+https://github.com/; contact: manuel.sotocalvo@gmail.com)"
            ),
        }

        tiles = self._tile_bboxes()
        n_tiles = len(tiles)
        if n_tiles > 1:
            self.progress.emit(
                10,
                f"Large region: splitting into {n_tiles} tiles to stay "
                "under the Overpass server timeout...",
            )

        features: list[GridFeature] = []
        seen_ids: set[str] = set()
        failed_tiles = 0
        last_tile_error: Exception | None = None
        for ti, (ts, tw, tn, te) in enumerate(tiles):
            if self._cancelled:
                return []
            query = self._build_query(f"{ts},{tw},{tn},{te}")
            base = 15 + int(70 * ti / n_tiles)
            label = (
                f"Querying Overpass tile {ti + 1}/{n_tiles}..."
                if n_tiles > 1
                else "Querying Overpass API (this may take a while)..."
            )
            self.progress.emit(base, label)
            # Be a polite API citizen between tiles: a short pause avoids
            # bursting the server, which is what triggers the dispatcher
            # timeouts in the first place.
            if ti > 0:
                time.sleep(1.0)
            try:
                result = self._post_query(api, headers, query, time, requests)
            except RuntimeError as exc:
                # One tile failing (e.g. a road-dense tile that times out on
                # every mirror) must not abort the whole region — skip it and
                # keep the rest. A fully-failed fetch is raised below.
                failed_tiles += 1
                last_tile_error = exc
                logger.warning(
                    "OSM tile %d/%d failed, skipping: %s", ti + 1, n_tiles, exc
                )
                continue
            if result is None or self._cancelled:
                return []

            # --- Merge this tile's elements, de-duplicating across tiles. ---
            # A way straddling a tile boundary is returned in full by every
            # tile holding one of its nodes; keep the first copy by osm_id.
            for node in result.nodes:
                if self._cancelled:
                    return []
                feat = self._process_element(
                    tags=node.tags,
                    lat=float(node.lat),
                    lng=float(node.lon),
                    osm_id=f"node/{node.id}",
                )
                if feat and feat.osm_id not in seen_ids:
                    seen_ids.add(feat.osm_id)
                    features.append(feat)

            for way in result.ways:
                if self._cancelled:
                    return []
                feat = self._process_way(way)
                if feat and feat.osm_id not in seen_ids:
                    seen_ids.add(feat.osm_id)
                    features.append(feat)

        # If every tile failed, surface the error; partial coverage is OK.
        if failed_tiles >= n_tiles:
            raise RuntimeError(
                f"All {n_tiles} Overpass tile(s) failed; last error: "
                f"{last_tile_error}"
            )
        if failed_tiles:
            logger.warning(
                "%d of %d OSM tiles failed; returning partial results",
                failed_tiles, n_tiles,
            )

        self.progress.emit(90, f"Filtering (voltage >= {self.min_voltage_kv} kV)...")
        features = self._apply_filters(features)

        skipped = (f" ({failed_tiles} tile(s) skipped)" if failed_tiles else "")
        self.progress.emit(100, f"OSM: {len(features)} features found{skipped}")
        return features

    # ── Tiling + query helpers ───────────────────────────────────

    # Max degrees of latitude/longitude per Overpass tile. A single query
    # over a very large or dense region (e.g. all of Japan) blows past the
    # server-side [timeout:180]; Overpass then returns HTTP 200 with an empty
    # body and a "runtime error ... timed out" remark, which previously parsed
    # as a silent empty success. Splitting the bbox into tiles keeps each
    # query well under that limit.
    _MAX_TILE_DEG = 5.0

    # Overpass mirrors, tried in rotation across retries. The main instance
    # returns HTTP 400 dispatcher timeouts under heavy load; rotating to a
    # mirror sidesteps a single overloaded server.
    _OVERPASS_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    )

    def _tile_bboxes(self) -> list[tuple[float, float, float, float]]:
        """Split the region into a grid of sub-bboxes no larger than
        ``_MAX_TILE_DEG`` per side. Small regions yield a single tile, so the
        common case is unchanged."""
        import math

        s, w, n, e = self.south, self.west, self.north, self.east
        n_lat = max(1, math.ceil((n - s) / self._MAX_TILE_DEG))
        n_lon = max(1, math.ceil((e - w) / self._MAX_TILE_DEG))
        d_lat = (n - s) / n_lat
        d_lon = (e - w) / n_lon
        tiles: list[tuple[float, float, float, float]] = []
        for i in range(n_lat):
            for j in range(n_lon):
                tiles.append(
                    (
                        s + i * d_lat,
                        w + j * d_lon,
                        s + (i + 1) * d_lat,
                        w + (j + 1) * d_lon,
                    )
                )
        return tiles

    def _build_query(self, bbox: str) -> str:
        """Assemble the Overpass QL query for one bbox tile."""
        parts: list[str] = []
        if "substation" in self.element_types:
            parts += [
                f'node["power"="substation"]({bbox});',
                f'way["power"="substation"]({bbox});',
                f'relation["power"="substation"]({bbox});',
            ]
        if "generator" in self.element_types:
            parts += [
                f'node["power"="generator"]({bbox});',
                f'way["power"="generator"]({bbox});',
                f'node["power"="plant"]({bbox});',
                f'way["power"="plant"]({bbox});',
            ]
        if "line" in self.element_types:
            parts += [
                f'way["power"="line"]({bbox});',
                f'way["power"="cable"]({bbox});',
            ]
        if "transformer" in self.element_types:
            parts += [
                f'node["power"="transformer"]({bbox});',
                f'way["power"="transformer"]({bbox});',
            ]
        if "converter" in self.element_types:
            parts += [
                f'node["power"="converter"]({bbox});',
                f'way["power"="converter"]({bbox});',
            ]
        if "storage" in self.element_types:
            parts += [
                f'node["power"="storage"]({bbox});',
                f'way["power"="storage"]({bbox});',
            ]
        if "fuel_entry" in self.element_types:
            parts += [
                f'way["industrial"="refinery"]({bbox});',
                f'node["industrial"="refinery"]({bbox});',
                f'way["industrial"="fuel_depot"]({bbox});',
                f'node["industrial"="fuel_depot"]({bbox});',
                f'way["industrial"="petroleum_terminal"]({bbox});',
                f'node["industrial"="petroleum_terminal"]({bbox});',
                f'way["industrial"="oil"]({bbox});',
                f'node["industrial"="oil"]({bbox});',
                f'way["man_made"="oil_terminal"]({bbox});',
                f'node["man_made"="oil_terminal"]({bbox});',
                f'way["landuse"="port"]["cargo"~"oil|fuel|lpg|lng|liquid_bulk|petroleum"]({bbox});',
                f'node["landuse"="port"]["cargo"~"oil|fuel|lpg|lng|liquid_bulk|petroleum"]({bbox});',
                f'way["harbour"="yes"]["cargo"~"oil|fuel|lpg|lng|liquid_bulk|petroleum"]({bbox});',
                f'node["harbour"="yes"]["cargo"~"oil|fuel|lpg|lng|liquid_bulk|petroleum"]({bbox});',
            ]
        if "fuel_storage" in self.element_types:
            parts += [
                f'node["man_made"="storage_tank"]["content"~"fuel|oil|gas|diesel|lpg|petroleum"]({bbox});',
                f'way["man_made"="storage_tank"]["content"~"fuel|oil|gas|diesel|lpg|petroleum"]({bbox});',
                f'way["industrial"="tank_farm"]({bbox});',
                f'node["industrial"="tank_farm"]({bbox});',
            ]
        if {"fuel_entry", "fuel_storage"} & self.element_types:
            # Only the major arteries used for fuel trucking. Including
            # "secondary" pulls an order of magnitude more road geometry and
            # reliably times out the Overpass dispatcher in dense regions.
            parts += [
                f'way["highway"~"motorway|trunk|primary"]({bbox});',
            ]

        return (
            "[out:json][timeout:180];\n(\n"
            + "\n".join(f"  {p}" for p in parts)
            + "\n);\nout body;\n>;\nout skel qt;"
        )

    def _post_query(self, api, headers, query, time, requests):
        """POST one Overpass query with retries, rotating mirrors and backing
        off between attempts.

        Returns the parsed overpy ``Result``, or ``None`` if cancelled. Raises
        ``RuntimeError`` once retries are exhausted. Two Overpass quirks are
        handled explicitly:

        * A server-side timeout/overload comes back as HTTP 200 with an empty
          body and a ``"runtime error ... timed out"`` remark — treat as a
          retryable failure, not a silently empty success.
        * HTTP 400 covers BOTH genuine QL syntax errors (fatal) and transient
          dispatcher/load timeouts under heavy traffic (retryable). Only a
          body that names a ``parse error``/``static error`` is fatal.
        """
        endpoints = self._OVERPASS_ENDPOINTS
        max_retries = 4
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            if self._cancelled:
                return None
            url = endpoints[attempt % len(endpoints)]
            try:
                resp = requests.post(
                    url,
                    data=query.encode("utf-8"),
                    headers=headers,
                    timeout=300,
                )
                if resp.status_code == 200:
                    sniff = resp.content[:2048] + resp.content[-2048:]
                    if b"runtime error" in sniff or b"timed out" in sniff:
                        last_error = RuntimeError(
                            "Overpass server-side timeout (region tile too "
                            "large or dense)"
                        )
                    else:
                        return api.parse_json(resp.content)
                elif resp.status_code == 400:
                    msg = _overpass_error_snippet(resp.text)
                    low = resp.text.lower()
                    if "static error" in low or "parse error" in low:
                        # Genuine query error (e.g. an out-of-range bbox) —
                        # retrying won't help; fail fast with the message.
                        raise RuntimeError(
                            f"Overpass rejected query (400): {msg}"
                        )
                    # Otherwise a transient dispatcher 400: retry on a mirror.
                    last_error = RuntimeError(f"Overpass busy (400): {msg}")
                elif resp.status_code in (429, 504):
                    # Transient dispatcher/load timeout — retry on a mirror.
                    last_error = RuntimeError(
                        f"Overpass busy ({resp.status_code}): "
                        f"{_overpass_error_snippet(resp.text)}"
                    )
                else:
                    last_error = RuntimeError(
                        f"Overpass HTTP {resp.status_code}: {resp.text[:200]}"
                    )
            except requests.RequestException as exc:
                last_error = exc
            if attempt < max_retries:
                time.sleep(min(10 * (attempt + 1), 45))
        raise RuntimeError(
            f"Unable to get any result from the Overpass API after "
            f"{max_retries + 1} attempts: {last_error}"
        )

    # ── Element processing ───────────────────────────────────────

    def _process_element(
        self,
        tags: dict[str, str],
        lat: float,
        lng: float,
        osm_id: str,
        line_coords: list[tuple[float, float]] | None = None,
    ) -> GridFeature | None:
        """Convert a single OSM element (node or way centroid) to GridFeature."""
        power = tags.get("power", "")
        if not power:
            # Check for fuel infrastructure tags
            return self._process_fuel_element(tags, lat, lng, osm_id)

        name = tags.get("name", "")

        # --- Substation ---
        if power == "substation":
            voltages = _parse_voltage_tag(tags.get("voltage", ""))
            v1 = voltages[0] if voltages else 0.0
            v2 = voltages[1] if len(voltages) > 1 else 0.0
            if not v1:
                # Infer from substation type
                sub_type = tags.get("substation", "")
                if sub_type == "transmission":
                    v1 = 220.0
                elif sub_type == "sub_transmission":
                    v1 = 110.0
                elif sub_type == "distribution":
                    v1 = 33.0
            freq = _parse_frequency(tags.get("frequency", ""), lat, lng)
            operator = tags.get("operator", "")
            feat_name = name
            if not feat_name and operator:
                feat_name = f"{operator} Substation"
            return GridFeature(
                source="osm",
                feature_type="substation",
                name=feat_name or f"Substation {osm_id}",
                latitude=lat, longitude=lng,
                voltage_kv=v1,
                voltage_kv_secondary=v2,
                frequency_hz=freq,
                operator=operator,
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        # --- Generator / Plant ---
        if power in ("generator", "plant"):
            src = tags.get("generator:source", tags.get("plant:source", ""))
            fuel, gen_type = _OSM_SOURCE_TO_FUEL.get(
                src.lower().split(";")[0].strip(),
                ("Other", "Non-renewable"),
            )
            # Try multiple capacity tags in priority order
            capacity_mw = 0.0
            for cap_tag in (
                "generator:output:electricity",
                "plant:output:electricity",
                "capacity",
                "power_rating",
            ):
                cap_raw = tags.get(cap_tag, "")
                if cap_raw:
                    capacity_mw = _parse_capacity_tag(cap_raw)
                    if capacity_mw > 0:
                        break
            # Operator / owner
            operator = tags.get("operator", tags.get("owner", ""))
            # Commissioning date
            comm_year = _parse_year_tag(tags.get(
                "start_date", tags.get("commissioning_date", ""),
            ))
            # Technology / method hints (OSM uses generator:method,
            # plant:method, generator:type for steam/ICE/CCGT info)
            osm_method = tags.get(
                "generator:method", tags.get("plant:method", ""),
            )
            osm_tech = tags.get(
                "generator:type", tags.get("plant:type", ""),
            )
            # Refine using technology + capacity + age
            fuel, gen_type = _refine_fuel(
                fuel, gen_type,
                technology=osm_tech,
                method=osm_method,
                fuels_detail=src,
                capacity_mw=capacity_mw,
                commissioning_year=comm_year,
            )
            # Enrich name: use operator if name is generic
            feat_name = name
            if not feat_name or feat_name.startswith("Generator"):
                if operator:
                    feat_name = f"{operator} {fuel} Plant"
            return GridFeature(
                source="osm",
                feature_type="generator",
                name=feat_name or f"Generator {osm_id}",
                latitude=lat, longitude=lng,
                capacity_mw=capacity_mw,
                fuel=fuel,
                gen_type=gen_type,
                operator=operator,
                commissioning_year=comm_year,
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        # --- Line / Cable ---
        if power in ("line", "cable"):
            voltages = _parse_voltage_tag(tags.get("voltage", ""))
            v = voltages[0] if voltages else 0.0
            circuits_raw = tags.get("circuits", tags.get("cables", "1"))
            try:
                num_circuits = max(1, int(circuits_raw.split(";")[0].strip()))
            except ValueError:
                num_circuits = 1
            freq = _parse_frequency(tags.get("frequency", ""), lat, lng)
            current_type = "DC" if "dc" in tags.get("line", "").lower() else "AC"
            # Try to extract explicit capacity from OSM tags
            cap_mw = 0.0
            for cap_tag in ("capacity", "rating"):
                cap_raw = tags.get(cap_tag, "")
                if cap_raw:
                    cap_mw = _parse_capacity_tag(cap_raw)
                    if cap_mw > 0:
                        break
            operator = tags.get("operator", "")
            feat_name = name or (f"{operator} Line" if operator else f"Line {osm_id}")
            return GridFeature(
                source="osm",
                feature_type="line",
                name=feat_name,
                latitude=lat, longitude=lng,
                voltage_kv=v,
                capacity_mw=cap_mw,
                frequency_hz=freq,
                current_type=current_type,
                num_circuits=num_circuits,
                operator=operator,
                line_coords=line_coords or [],
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        # --- Transformer ---
        if power == "transformer":
            voltages = _parse_voltage_tag(tags.get("voltage", ""))
            v1 = voltages[0] if voltages else 220.0
            v2 = voltages[1] if len(voltages) > 1 else 110.0
            cap_raw = tags.get("rating", tags.get("transformer:output", ""))
            capacity = _parse_capacity_tag(cap_raw)
            operator = tags.get("operator", "")
            feat_name = name
            if not feat_name and operator:
                feat_name = f"{operator} Transformer"
            return GridFeature(
                source="osm",
                feature_type="transformer",
                name=feat_name or f"Transformer {osm_id}",
                latitude=lat, longitude=lng,
                voltage_kv=v1,
                voltage_kv_secondary=v2,
                capacity_mw=capacity if capacity > 0 else 100.0,
                operator=operator,
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        # --- Converter ---
        if power == "converter":
            voltages = _parse_voltage_tag(tags.get("voltage", ""))
            v = voltages[0] if voltages else 220.0
            cap_raw = tags.get("rating", "")
            capacity = _parse_capacity_tag(cap_raw)
            operator = tags.get("operator", "")
            return GridFeature(
                source="osm",
                feature_type="converter",
                name=name or (f"{operator} Converter" if operator else f"Converter {osm_id}"),
                latitude=lat, longitude=lng,
                voltage_kv=v,
                capacity_mw=capacity if capacity > 0 else 100.0,
                current_type="AC_DC",
                operator=operator,
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        # --- Storage ---
        if power == "storage":
            cap_raw = tags.get("storage:output:electricity", "")
            capacity = _parse_capacity_tag(cap_raw)
            # Try alternative capacity tags
            if capacity == 0:
                for alt_tag in ("capacity", "rating", "power_rating"):
                    alt_raw = tags.get(alt_tag, "")
                    if alt_raw:
                        capacity = _parse_capacity_tag(alt_raw)
                        if capacity > 0:
                            break
            # Energy capacity (MWh)
            energy_mwh = 0.0
            for e_tag in ("storage:capacity:energy", "capacity:energy",
                          "battery:capacity"):
                e_raw = tags.get(e_tag, "")
                if e_raw:
                    energy_mwh = _parse_energy_tag(e_raw)
                    if energy_mwh > 0:
                        break
            operator = tags.get("operator", "")
            return GridFeature(
                source="osm",
                feature_type="battery",
                name=name or (f"{operator} Storage" if operator else f"Storage {osm_id}"),
                latitude=lat, longitude=lng,
                capacity_mw=capacity,
                energy_mwh=energy_mwh,
                fuel="None",
                gen_type="Storage",
                operator=operator,
                raw_tags=dict(tags),
                osm_id=osm_id,
            )

        return None

    def _process_way(self, way) -> GridFeature | None:
        """Process an OSM way — extract geometry for lines, centroid for areas."""
        tags = way.tags
        power = tags.get("power", "")
        highway = tags.get("highway", "")
        industrial = tags.get("industrial", "")
        man_made = tags.get("man_made", "")
        landuse = tags.get("landuse", "")

        has_relevant_tag = (
            power
            or industrial in ("refinery", "fuel_depot", "tank_farm",
                              "petroleum_terminal", "oil")
            or man_made in ("storage_tank", "oil_terminal")
            or (landuse == "port" and _is_fuel_cargo(tags.get("cargo", "")))
            or (tags.get("harbour") == "yes"
                and _is_fuel_cargo(tags.get("cargo", "")))
            or highway in ("motorway", "trunk", "primary", "secondary")
        )
        if not has_relevant_tag:
            return None

        # Resolve node coordinates
        coords: list[tuple[float, float]] = []
        for node in way.nodes:
            try:
                coords.append((float(node.lat), float(node.lon)))
            except (TypeError, AttributeError):
                continue

        if not coords:
            return None

        # Centroid
        lat = sum(c[0] for c in coords) / len(coords)
        lng = sum(c[1] for c in coords) / len(coords)

        # Road → special handling (line_coords for graph, include=False)
        if highway in ("motorway", "trunk", "primary", "secondary"):
            return GridFeature(
                source="osm", feature_type="road",
                name=tags.get("name", tags.get("ref", "")),
                latitude=lat, longitude=lng,
                line_coords=coords,
                include=False,  # Not shown in review or built
                raw_tags=dict(tags), osm_id=f"way/{way.id}",
            )

        line_coords = coords if power in ("line", "cable") else None

        if power:
            return self._process_element(
                tags=tags,
                lat=lat, lng=lng,
                osm_id=f"way/{way.id}",
                line_coords=line_coords,
            )

        # Non-power way (fuel infrastructure)
        return self._process_fuel_element(tags, lat, lng, f"way/{way.id}")

    def _process_fuel_element(
        self,
        tags: dict[str, str],
        lat: float,
        lng: float,
        osm_id: str,
    ) -> GridFeature | None:
        """Process non-power elements: fuel infrastructure."""
        name = tags.get("name", "")
        industrial = tags.get("industrial", "")
        man_made = tags.get("man_made", "")
        landuse = tags.get("landuse", "")

        # Fuel entry points: refineries, depots, terminals, oil facilities
        if industrial in ("refinery", "fuel_depot", "petroleum_terminal", "oil"):
            fuel = _detect_fuel_from_tags(tags)
            if not fuel and industrial == "refinery":
                fuel = "Diesel"
            return GridFeature(
                source="osm", feature_type="fuel_entry",
                name=name or f"Fuel Entry {osm_id}",
                latitude=lat, longitude=lng,
                fuel=fuel or "Diesel",
                raw_tags=dict(tags), osm_id=osm_id,
            )

        if man_made == "oil_terminal":
            fuel = _detect_fuel_from_tags(tags) or "Diesel"
            return GridFeature(
                source="osm", feature_type="fuel_entry",
                name=name or f"Oil Terminal {osm_id}",
                latitude=lat, longitude=lng,
                fuel=fuel,
                raw_tags=dict(tags), osm_id=osm_id,
            )

        # Ports/harbours: only accept if cargo tag confirms fuel-related goods
        if landuse == "port" and _is_fuel_cargo(tags.get("cargo", "")):
            fuel = _detect_fuel_from_tags(tags)
            if not fuel:
                fuel = _fuel_from_cargo(tags.get("cargo", ""))
            return GridFeature(
                source="osm", feature_type="fuel_entry",
                name=name or f"Fuel Port {osm_id}",
                latitude=lat, longitude=lng,
                fuel=fuel,
                raw_tags=dict(tags), osm_id=osm_id,
            )

        if tags.get("harbour") == "yes" and _is_fuel_cargo(tags.get("cargo", "")):
            fuel = _detect_fuel_from_tags(tags)
            if not fuel:
                fuel = _fuel_from_cargo(tags.get("cargo", ""))
            return GridFeature(
                source="osm", feature_type="fuel_entry",
                name=name or f"Fuel Harbour {osm_id}",
                latitude=lat, longitude=lng,
                fuel=fuel,
                raw_tags=dict(tags), osm_id=osm_id,
            )

        # Fuel storage: storage tanks, tank farms
        if man_made == "storage_tank" or industrial == "tank_farm":
            content = tags.get("content", "").lower()
            if any(k in content for k in ("fuel", "oil", "gas", "diesel", "lpg", "petroleum")) or industrial == "tank_farm":
                fuel = _detect_fuel_from_tags(tags)
                return GridFeature(
                    source="osm", feature_type="fuel_storage",
                    name=name or f"Fuel Storage {osm_id}",
                    latitude=lat, longitude=lng,
                    fuel=fuel,
                    raw_tags=dict(tags), osm_id=osm_id,
                )

        return None

    # ── Filters ──────────────────────────────────────────────────

    # Hard ceiling on features handed to the GUI. Dense countries (e.g. Japan)
    # map hundreds of thousands of rooftop PV panels; even after dropping them,
    # cap the rest so the single-threaded GUI/dedup never chokes.
    _MAX_GUI_FEATURES = 25000

    @staticmethod
    def _is_rooftop_solar(f: GridFeature) -> bool:
        """A ``power=generator`` point tagged solar with no usable capacity is
        a rooftop PV install — irrelevant to a transmission model and mapped in
        the hundreds of thousands across dense countries. Utility solar
        (``power=plant`` or capacity-tagged) is kept."""
        tags = getattr(f, "raw_tags", None) or {}
        if tags.get("power") == "plant":
            return False
        src = (tags.get("generator:source")
               or tags.get("plant:source")
               or f.fuel or "").lower()
        if "solar" not in src:
            return False
        return not (f.capacity_mw and f.capacity_mw > 0)

    def _apply_filters(self, features: list[GridFeature]) -> list[GridFeature]:
        """Apply voltage/capacity filters, drop rooftop PV, and cap the total."""
        filtered: list[GridFeature] = []
        for f in features:
            # Voltage filter: applies to substations, lines, transformers
            if f.feature_type in ("substation", "line", "transformer"):
                if f.voltage_kv > 0 and f.voltage_kv < self.min_voltage_kv:
                    continue
            # Capacity filter + rooftop-PV drop: applies to generators
            if f.feature_type == "generator":
                if self._is_rooftop_solar(f):
                    continue
                if f.capacity_mw > 0 and f.capacity_mw < self.min_capacity_mw:
                    continue
            filtered.append(f)
        return self._cap_features(filtered)

    def _cap_features(self, features: list[GridFeature]) -> list[GridFeature]:
        """Keep all network infrastructure; if the total still exceeds
        ``_MAX_GUI_FEATURES``, keep only the largest generators so the GUI
        stays responsive. Logs what was dropped instead of silently truncating."""
        if len(features) <= self._MAX_GUI_FEATURES:
            return features
        infra = [f for f in features if f.feature_type != "generator"]
        gens = [f for f in features if f.feature_type == "generator"]
        budget = max(0, self._MAX_GUI_FEATURES - len(infra))
        gens.sort(key=lambda g: g.capacity_mw, reverse=True)
        kept = infra + gens[:budget]
        logger.warning(
            "OSM features capped %d -> %d (dropped %d low-priority generators; "
            "raise min_voltage/min_capacity or select a smaller region)",
            len(features), len(kept), len(features) - len(kept),
        )
        return kept


def _parse_frequency(raw: str, lat: float = 0.0, lng: float = 0.0) -> float:
    """Parse OSM frequency tag.

    Returns Hz from the OSM tag if present and valid; otherwise infers
    from the (lat, lng) using the global 50/60 Hz map. Hard fallback
    is 50 Hz (the world default).
    """
    def _from_geography() -> float:
        try:
            from esfex.visualization.workflows.grid_mapping_quality import (
                infer_frequency_hz,
            )
            return infer_frequency_hz(lat, lng)
        except Exception:
            return 50.0

    if not raw:
        return _from_geography()
    try:
        v = float(raw.split(";")[0].strip())
        return v if v > 0 else _from_geography()
    except ValueError:
        return _from_geography()


def _http_get_with_retry(
    url: str,
    *,
    timeout: int = 60,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    headers: dict | None = None,
    cancelled_cb=None,
):
    """GET *url* with exponential backoff on transient errors.

    Retries on connection resets, SSL handshake failures, timeouts and
    5xx responses. Returns the final ``requests.Response`` (status 200)
    or raises the last exception.
    """
    import time
    import requests

    hdrs = {
        "User-Agent": (
            "esfex-gridbuilder/1.0 "
            "(+https://github.com/; contact: manuel.sotocalvo@gmail.com)"
        ),
    }
    if headers:
        hdrs.update(headers)

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if cancelled_cb is not None and cancelled_cb():
            raise RuntimeError("cancelled")
        try:
            resp = requests.get(url, timeout=timeout, headers=hdrs)
            if resp.status_code == 200:
                return resp
            if 500 <= resp.status_code < 600:
                last_exc = RuntimeError(
                    f"HTTP {resp.status_code} from {url}"
                )
            else:
                resp.raise_for_status()
                return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            logger.warning(
                "GET %s attempt %d/%d failed: %s",
                url, attempt + 1, max_retries + 1, exc,
            )
        if attempt < max_retries:
            wait = backoff_base ** attempt
            time.sleep(wait)
    raise RuntimeError(
        f"Failed to download {url} after {max_retries + 1} attempts: "
        f"{last_exc}"
    )


# =====================================================================
# WRI Global Power Plant Database Fetcher
# =====================================================================


class WRIGridFetcher(QThread):
    """Fetch power plants from the WRI Global Power Plant Database.

    Downloads the CSV from GitHub and filters by bounding box.
    """

    progress = Signal(int, str)
    finished = Signal(object)      # list[GridFeature]
    error = Signal(str)

    _CSV_URL = (
        "https://raw.githubusercontent.com/wri/global-power-plant-database"
        "/master/output_database/global_power_plant_database.csv"
    )

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        min_capacity_mw: float = 1.0,
        parent=None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self.min_capacity_mw = min_capacity_mw
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            features = self._fetch()
            if self._cancelled:
                return
            self.finished.emit(features)
        except Exception as exc:
            logger.exception("WRIGridFetcher error")
            self.error.emit(str(exc))

    def _fetch(self) -> list[GridFeature]:
        import pandas as pd

        self.progress.emit(10, "Downloading WRI power plant database...")
        resp = _http_get_with_retry(
            self._CSV_URL, timeout=60,
            cancelled_cb=lambda: self._cancelled,
        )

        if self._cancelled:
            return []

        self.progress.emit(40, "Parsing CSV...")
        df = pd.read_csv(io.StringIO(resp.text), low_memory=False)

        # Filter by bbox
        mask = (
            (df["latitude"] >= self.south)
            & (df["latitude"] <= self.north)
            & (df["longitude"] >= self.west)
            & (df["longitude"] <= self.east)
        )
        df = df[mask]

        if self._cancelled:
            return []

        self.progress.emit(60, f"Processing {len(df)} power plants...")

        # Capacity filter
        if "capacity_mw" in df.columns:
            df = df[df["capacity_mw"] >= self.min_capacity_mw]

        features: list[GridFeature] = []
        for _, row in df.iterrows():
            if self._cancelled:
                return []
            primary_fuel = str(row.get("primary_fuel", "Other"))
            fuel, gen_type = _WRI_FUEL_MAP.get(
                primary_fuel, ("Other", "Non-renewable")
            )
            # WRI lacks a Technology column, but other_fuel{1..3} carries
            # secondary fuel hints we can feed into refinement together
            # with capacity / age.
            other_fuels = " ".join(
                str(row.get(c, "")) for c in
                ("other_fuel1", "other_fuel2", "other_fuel3")
            ).lower()
            # Storage detection
            ftype = "battery" if gen_type == "Storage" else "generator"

            name = str(row.get("name", ""))
            cap = float(row.get("capacity_mw", 0.0))
            owner = str(row.get("owner", ""))
            if owner == "nan":
                owner = ""
            # Parse commissioning year
            comm_year_raw = row.get("commissioning_year", "")
            comm_year = 0
            try:
                cy = float(comm_year_raw)
                if 1900 <= cy <= 2100:
                    comm_year = int(cy)
            except (ValueError, TypeError):
                pass
            # Refine using secondary fuel hints + capacity + age
            fuel, gen_type = _refine_fuel(
                fuel, gen_type,
                fuels_detail=f"{primary_fuel} {other_fuels}",
                capacity_mw=cap,
                commissioning_year=comm_year,
            )
            # Enrich name with owner if unnamed
            feat_name = name
            if (not feat_name or feat_name == "nan") and owner:
                feat_name = f"{owner} {fuel} Plant"

            features.append(GridFeature(
                source="wri",
                feature_type=ftype,
                name=feat_name or f"WRI Plant {row.get('gppd_idnr', '')}",
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                capacity_mw=cap,
                fuel=fuel,
                gen_type=gen_type,
                operator=owner,
                commissioning_year=comm_year,
                raw_tags={
                    "gppd_idnr": str(row.get("gppd_idnr", "")),
                    "country": str(row.get("country_long", "")),
                    "primary_fuel": primary_fuel,
                    "owner": owner,
                    "commissioning_year": str(comm_year) if comm_year else "",
                },
            ))

        self.progress.emit(100, f"WRI: {len(features)} power plants found")
        return features


# =====================================================================
# GEM Global Power Plant Fetcher
# =====================================================================

_GEM_TYPE_TO_FUEL: dict[str, tuple[str, str]] = {
    # (ESFEX fuel, gen_type)  —  keyed by GEM "Type" column (lowercase)
    "coal": ("Coal", "Non-renewable"),
    "oil/gas": ("Natural Gas", "Non-renewable"),
    "nuclear": ("Nuclear", "Non-renewable"),
    "hydropower": ("Water", "Renewable"),
    "wind": ("Wind", "Renewable"),
    "solar": ("Solar", "Renewable"),
    "geothermal": ("Geothermal", "Renewable"),
    "bioenergy": ("Biomass", "Renewable"),
}


class GEMGridFetcher(QThread):
    """Fetch power plants from the GEM Global Integrated Power database.

    Downloads the Feb 2025 Excel file from GitHub (open-energy-transition/
    gem_per_country) and filters by bounding box.  More recent than WRI
    (2021) with similar global coverage.
    """

    progress = Signal(int, str)
    finished = Signal(object)      # list[GridFeature]
    error = Signal(str)

    _XLSX_URL = (
        "https://open-energy-transition.github.io/"
        "global_energy_monitor_power_tracker/"
        "Global-Integrated-Power-February-2025-update-II.xlsx"
    )
    _SHEET_NAME = "Power facilities"

    # Only include plants that are (or were) physically real
    _ACTIVE_STATUSES = frozenset({
        "operating", "mothballed", "construction",
    })

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        min_capacity_mw: float = 1.0,
        parent=None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self.min_capacity_mw = min_capacity_mw
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            features = self._fetch()
            if self._cancelled:
                return
            self.finished.emit(features)
        except Exception as exc:
            logger.exception("GEMGridFetcher error")
            self.error.emit(str(exc))

    def _fetch(self) -> list[GridFeature]:
        import pandas as pd

        self.progress.emit(10, "Downloading GEM power plant database (~24 MB)...")
        resp = _http_get_with_retry(
            self._XLSX_URL, timeout=180,
            cancelled_cb=lambda: self._cancelled,
        )

        if self._cancelled:
            return []

        self.progress.emit(40, "Parsing Excel file...")
        df = pd.read_excel(
            io.BytesIO(resp.content),
            engine="openpyxl",
            sheet_name=self._SHEET_NAME,
        )

        if self._cancelled:
            return []

        # --- Filter by status (only real / existing plants) ---
        if "Status" in df.columns:
            df = df[df["Status"].str.lower().str.strip().isin(self._ACTIVE_STATUSES)]

        # --- Coordinates ---
        df = df.dropna(subset=["Latitude", "Longitude"])

        # Filter by bounding box
        mask = (
            (df["Latitude"] >= self.south)
            & (df["Latitude"] <= self.north)
            & (df["Longitude"] >= self.west)
            & (df["Longitude"] <= self.east)
        )
        df = df[mask]

        if self._cancelled:
            return []

        self.progress.emit(60, f"Processing {len(df)} GEM power plants...")

        # Capacity filter
        if "Capacity (MW)" in df.columns:
            df["Capacity (MW)"] = pd.to_numeric(
                df["Capacity (MW)"], errors="coerce",
            ).fillna(0)
            df = df[df["Capacity (MW)"] >= self.min_capacity_mw]

        features: list[GridFeature] = []
        for _, row in df.iterrows():
            if self._cancelled:
                return []

            gem_type = str(row.get("Type", "")).lower().strip()
            fuel, gen_type = _GEM_TYPE_TO_FUEL.get(
                gem_type, ("Other", "Non-renewable"),
            )

            name = str(row.get("Plant / Project name", ""))
            cap = float(row.get("Capacity (MW)", 0.0))
            lat = float(row["Latitude"])
            lng = float(row["Longitude"])

            raw_tags: dict[str, str] = {"gem_type": gem_type}
            status = str(row.get("Status", ""))
            if status:
                raw_tags["status"] = status
            country = str(row.get("Country/area", ""))
            if country:
                raw_tags["country"] = country

            # Extract owner/operator
            owner = ""
            for owner_col in ("Owner", "Parent company"):
                val = str(row.get(owner_col, ""))
                if val and val != "nan":
                    owner = val
                    raw_tags["owner"] = val
                    break

            # Commissioning year
            comm_year = 0
            for date_col in (
                "Commercial operation date",
                "Operating year",
                "Year of completion",
            ):
                val = row.get(date_col, "")
                if val is not None and str(val) != "nan":
                    comm_year = _parse_year_tag(str(val))
                    if comm_year:
                        raw_tags["commissioning_year"] = str(comm_year)
                        break

            # Technology sub-type (e.g. "CCGT", "Onshore", "Offshore")
            tech_str = ""
            for tech_col in ("Technology(ies)", "Technology", "Subtype"):
                val = str(row.get(tech_col, ""))
                if val and val != "nan":
                    tech_str = val
                    raw_tags["technology"] = val
                    break

            # Refine fuel for oil/gas split — first pick the broad
            # bucket from Fuel(s), then let _refine_fuel apply the
            # technology-aware split (CCGT vs ST, ICE vs steam).
            fuel_detail = str(row.get("Fuel(s)", "")).lower()
            if gem_type == "oil/gas":
                if "oil" in fuel_detail or "diesel" in fuel_detail:
                    fuel = "Diesel"
                elif "gas" in fuel_detail:
                    fuel = "Natural Gas"
                elif "coal" in fuel_detail:
                    fuel = "Coal"
            fuel, gen_type = _refine_fuel(
                fuel, gen_type,
                technology=tech_str,
                fuels_detail=fuel_detail,
                capacity_mw=cap,
                commissioning_year=comm_year,
            )

            feat_name = name
            if (not feat_name or feat_name == "nan") and owner:
                feat_name = f"{owner} {fuel} Plant"

            features.append(GridFeature(
                source="gem",
                feature_type="generator",
                name=feat_name or "GEM Plant",
                latitude=lat,
                longitude=lng,
                capacity_mw=cap,
                fuel=fuel,
                gen_type=gen_type,
                operator=owner,
                commissioning_year=comm_year,
                technology=tech_str,
                raw_tags=raw_tags,
            ))

        self.progress.emit(100, f"GEM: {len(features)} power plants found")
        return features


# =====================================================================
# GridFinder Fetcher
# =====================================================================


class GridFinderFetcher(QThread):
    """Fetch predicted grid line routes from the GridFinder dataset.

    Downloads GeoJSON from Zenodo and clips to bounding box.
    """

    progress = Signal(int, str)
    finished = Signal(object)      # list[GridFeature]
    error = Signal(str)

    # GridFinder Zenodo dataset — the API provides a redirect to the actual file
    _ZENODO_API = "https://zenodo.org/api/records/3369106"

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        parent=None,
    ):
        super().__init__(parent)
        self.south, self.west, self.north, self.east = bounds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            features = self._fetch()
            if self._cancelled:
                return
            self.finished.emit(features)
        except Exception as exc:
            logger.exception("GridFinderFetcher error")
            self.error.emit(str(exc))

    def _fetch(self) -> list[GridFeature]:
        import json

        self.progress.emit(5, "Resolving GridFinder download URL...")

        # Get file list from Zenodo API
        resp = _http_get_with_retry(
            self._ZENODO_API, timeout=30,
            cancelled_cb=lambda: self._cancelled,
        )
        record = resp.json()

        if self._cancelled:
            return []

        # Find the GeoJSON or GPKG file
        download_url = None
        for f in record.get("files", []):
            fname = f.get("key", "").lower()
            if fname.endswith(".geojson") or fname.endswith(".json"):
                download_url = f.get("links", {}).get("self")
                break

        if not download_url:
            # Try looking for a compressed file
            for f in record.get("files", []):
                fname = f.get("key", "").lower()
                if "grid" in fname and (fname.endswith(".gpkg")
                                         or fname.endswith(".zip")):
                    download_url = f.get("links", {}).get("self")
                    break

        if not download_url:
            self.progress.emit(100, "GridFinder: no compatible file found")
            logger.warning(
                "GridFinder Zenodo record has no GeoJSON file. "
                "Available files: %s",
                [f.get("key") for f in record.get("files", [])],
            )
            return []

        self.progress.emit(20, "Downloading GridFinder data...")
        data_resp = _http_get_with_retry(
            download_url, timeout=120,
            cancelled_cb=lambda: self._cancelled,
        )

        if self._cancelled:
            return []

        self.progress.emit(60, "Parsing GridFinder geometries...")

        # Handle different formats
        content_type = data_resp.headers.get("Content-Type", "")
        raw = data_resp.content

        features: list[GridFeature] = []

        if download_url.endswith(".gpkg") or b"SQLite" in raw[:20]:
            features = self._parse_gpkg(raw)
        else:
            # Assume GeoJSON
            try:
                geojson = json.loads(raw)
                features = self._parse_geojson(geojson)
            except json.JSONDecodeError:
                logger.warning("GridFinder: could not parse downloaded file")
                return []

        self.progress.emit(100, f"GridFinder: {len(features)} line segments found")
        return features

    def _parse_geojson(self, geojson: dict) -> list[GridFeature]:
        """Parse GeoJSON FeatureCollection of LineStrings."""
        features: list[GridFeature] = []
        raw_features = geojson.get("features", [])

        for i, feat in enumerate(raw_features):
            if self._cancelled:
                return []
            geom = feat.get("geometry", {})
            gtype = geom.get("type", "")
            coords_raw = geom.get("coordinates", [])

            if gtype == "MultiLineString":
                # Process each segment
                for segment in coords_raw:
                    gf = self._line_from_coords(segment, i, feat)
                    if gf:
                        features.append(gf)
            elif gtype == "LineString":
                gf = self._line_from_coords(coords_raw, i, feat)
                if gf:
                    features.append(gf)

        return features

    def _parse_gpkg(self, raw_bytes: bytes) -> list[GridFeature]:
        """Parse GeoPackage using geopandas (if available)."""
        try:
            import tempfile

            import geopandas as gpd
            from shapely.geometry import box

            with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name

            gdf = gpd.read_file(tmp_path)

            # Clip to bbox
            bbox = box(self.west, self.south, self.east, self.north)
            gdf = gdf[gdf.intersects(bbox)]

            features: list[GridFeature] = []
            for idx, row in gdf.iterrows():
                if self._cancelled:
                    return []
                geom = row.geometry
                if geom is None:
                    continue
                if geom.geom_type == "LineString":
                    coords = [(c[1], c[0]) for c in geom.coords]
                    if self._in_bbox(coords):
                        lat = sum(c[0] for c in coords) / len(coords)
                        lng = sum(c[1] for c in coords) / len(coords)
                        features.append(GridFeature(
                            source="gridfinder",
                            feature_type="line",
                            name=f"GridFinder Line {idx}",
                            latitude=lat, longitude=lng,
                            line_coords=coords,
                        ))
                elif geom.geom_type == "MultiLineString":
                    for line in geom.geoms:
                        coords = [(c[1], c[0]) for c in line.coords]
                        if self._in_bbox(coords):
                            lat = sum(c[0] for c in coords) / len(coords)
                            lng = sum(c[1] for c in coords) / len(coords)
                            features.append(GridFeature(
                                source="gridfinder",
                                feature_type="line",
                                name=f"GridFinder Line {idx}",
                                latitude=lat, longitude=lng,
                                line_coords=coords,
                            ))

            return features

        except ImportError:
            logger.warning("geopandas not available — cannot parse GPKG")
            return []
        except Exception as exc:
            logger.warning("GridFinder GPKG parse error: %s", exc)
            return []

    def _line_from_coords(
        self, coords_raw: list, index: int, feat: dict,
    ) -> GridFeature | None:
        """Create a GridFeature line from GeoJSON coordinate array."""
        if len(coords_raw) < 2:
            return None
        # GeoJSON is [lng, lat]
        coords = [(c[1], c[0]) for c in coords_raw]
        if not self._in_bbox(coords):
            return None
        lat = sum(c[0] for c in coords) / len(coords)
        lng = sum(c[1] for c in coords) / len(coords)
        return GridFeature(
            source="gridfinder",
            feature_type="line",
            name=f"GridFinder Line {index}",
            latitude=lat, longitude=lng,
            line_coords=coords,
            raw_tags=feat.get("properties", {}),
        )

    def _in_bbox(self, coords: list[tuple[float, float]]) -> bool:
        """Check if any coordinate in the list falls within the bounding box."""
        for lat, lng in coords:
            if (self.south <= lat <= self.north
                    and self.west <= lng <= self.east):
                return True
        return False
