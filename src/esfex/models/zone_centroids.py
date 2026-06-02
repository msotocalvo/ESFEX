"""Zone centroid helpers for zonal demand refactor (Option B).

For each zonal training sample the TFT model needs a (lat, lon) point at
which to sample pixel features. A geometric centroid biases toward remote
regions of a zone; a population-weighted centroid lands where demand
actually lives. This module provides both, falling back to the geometric
centroid when the population raster is unavailable.

Also provides a curated mapping from zonal demand sources to the admin_1
(or custom) polygons that define each zone, so the fetchers can resolve
a centroid without every fetcher owning its own geometry logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from esfex.paths import ADMIN1_SHP, POP_DENSITY_HIST_DIR


# ── Zone → admin_1 polygon resolution ───────────────────────────────────────

# For countries where zones map to aggregations of admin_1 regions we define
# the grouping explicitly. Values are lists of admin_1 `name` entries.
# Sources: cross-referenced against Natural Earth 10m admin_1 (downloaded
# 2026-04-18, 4596 features).

# Taiwan — Taipower publishes 4 load areas. Map counties to the four zones.
TWN_ZONE_MEMBERS: dict[str, list[str]] = {
    "north":   ["Taipei City", "New Taipei", "Keelung City", "Taoyuan",
                "Hsinchu", "Hsinchu City", "Yilan"],
    "central": ["Taichung City", "Miaoli", "Changhua", "Nantou", "Yunlin"],
    "south":   ["Chiayi", "Chiayi City", "Tainan City", "Kaohsiung City",
                "Pingtung"],
    "east":    ["Hualien", "Taitung", "Penghu", "Kinmen", "Lienchiang"],
}

# Thailand — EGAT reports 5 regions. Standard Thai regional grouping of
# 77 provinces (Bangkok metropolitan area vs the rest).
THA_ZONE_MEMBERS: dict[str, list[str]] = {
    "metropolitan": ["Bangkok Metropolis", "Nonthaburi", "Pathum Thani",
                     "Samut Prakan", "Samut Sakhon", "Nakhon Pathom"],
    "central":      ["Phra Nakhon Si Ayutthaya", "Ang Thong", "Lop Buri",
                     "Sing Buri", "Chai Nat", "Saraburi", "Suphan Buri",
                     "Kanchanaburi", "Ratchaburi", "Phetchaburi",
                     "Prachuap Khiri Khan", "Samut Songkhram",
                     "Chachoengsao", "Prachin Buri", "Nakhon Nayok",
                     "Sa Kaeo", "Chon Buri", "Rayong", "Chanthaburi",
                     "Trat"],
    "north":        ["Chiang Mai", "Chiang Rai", "Lampang", "Lamphun",
                     "Mae Hong Son", "Nan", "Phayao", "Phrae", "Uttaradit",
                     "Tak", "Sukhothai", "Phitsanulok", "Kamphaeng Phet",
                     "Phichit", "Phetchabun", "Nakhon Sawan", "Uthai Thani"],
    "northeast":    ["Nong Khai", "Bueng Kan", "Nakhon Phanom", "Sakon Nakhon",
                     "Mukdahan", "Udon Thani", "Loei", "Nong Bua Lam Phu",
                     "Khon Kaen", "Kalasin", "Maha Sarakham", "Roi Et",
                     "Yasothon", "Amnat Charoen", "Ubon Ratchathani",
                     "Si Sa Ket", "Surin", "Buri Ram", "Chaiyaphum",
                     "Nakhon Ratchasima"],
    "south":        ["Chumphon", "Ranong", "Surat Thani", "Phangnga",
                     "Phuket", "Krabi", "Nakhon Si Thammarat", "Trang",
                     "Phatthalung", "Satun", "Songkhla", "Yala", "Pattani",
                     "Narathiwat"],
}

# China — 31 mainland provincial-level divisions coded in the China hourly
# CSV (BJ, TJ, HB, SX, ...). Mapping to admin_1 `name` values.
# Note: admin_1 has 32 features for CHN; we map the 31 we have demand for.
CHN_PROVINCE_CODES: dict[str, str] = {
    "BJ": "Beijing", "TJ": "Tianjin", "HB": "Hebei", "SX": "Shanxi",
    "NM": "Inner Mongol", "LN": "Liaoning", "JL": "Jilin", "HL": "Heilongjiang",
    "SH": "Shanghai", "JS": "Jiangsu", "ZJ": "Zhejiang", "AH": "Anhui",
    "FJ": "Fujian", "JX": "Jiangxi", "SD": "Shandong", "HA": "Henan",
    # Note: "HB" appears twice in the CSV header — second is Hubei; rename at parse time
    "HU": "Hubei",  # use "HU" to disambiguate from Hebei
    "HN": "Hunan", "GD": "Guangdong", "GX": "Guangxi", "HI": "Hainan",
    "CQ": "Chongqing", "SC": "Sichuan", "GZ": "Guizhou", "YN": "Yunnan",
    "XZ": "Xizang", "SN": "Shaanxi", "GS": "Gansu", "QH": "Qinghai",
    "NX": "Ningxia Hui", "XJ": "Xinjiang",
}

# Brazil — ONS subsystems as groupings of states (UF codes).
BRA_SUBSYSTEM_MEMBERS: dict[str, list[str]] = {
    "N":  ["Acre", "Amazonas", "Amapá", "Pará", "Rondônia", "Roraima",
           "Tocantins", "Maranhão"],
    "NE": ["Piauí", "Ceará", "Rio Grande do Norte", "Paraíba", "Pernambuco",
           "Alagoas", "Sergipe", "Bahia"],
    "SE": ["Minas Gerais", "Espírito Santo", "Rio de Janeiro", "São Paulo",
           "Goiás", "Distrito Federal", "Mato Grosso", "Mato Grosso do Sul"],
    "S":  ["Paraná", "Santa Catarina", "Rio Grande do Sul"],
    "CO": [],  # "Centro-Oeste" is merged into SE subsystem since 2001
}

# Australia — NEM region names keyed on state.
AUS_NEM_MEMBERS: dict[str, list[str]] = {
    "NSW1": ["New South Wales", "Australian Capital Territory"],
    "VIC1": ["Victoria"],
    "QLD1": ["Queensland"],
    "SA1":  ["South Australia"],
    "TAS1": ["Tasmania"],
}


# ── Centroid computation ────────────────────────────────────────────────────


@dataclass
class ZoneCentroid:
    zone_id: str
    iso3: str
    lat: float
    lon: float
    method: str  # "pop_weighted" or "geometric"
    n_admin1: int


@lru_cache(maxsize=1)
def _load_admin1():
    import geopandas as gpd
    return gpd.read_file(ADMIN1_SHP)


@lru_cache(maxsize=8)
def _open_pop_raster(epoch: int):
    import rasterio
    path = POP_DENSITY_HIST_DIR / (
        f"gpw_v4_population_density_rev11_{epoch}_30_sec_{epoch}.tif")
    if not path.exists():
        return None
    try:
        return rasterio.open(str(path))
    except Exception:
        return None


def _merge_geoms(members: list[str], gdf):
    """Dissolve admin_1 features matching `members` into one geometry."""
    if not members:
        return None
    sub = gdf[gdf["name"].isin(members)]
    if len(sub) == 0:
        return None
    try:
        return sub.geometry.unary_union
    except Exception:
        return sub.geometry.iloc[0]


def _weighted_centroid_from_geom(geom, pop_epoch: int = 2020,
                                 n_samples: int = 10000
                                 ) -> Optional[tuple[float, float]]:
    """Sample a rectangle of points inside geom, weight by pop density raster."""
    ds = _open_pop_raster(pop_epoch)
    if ds is None:
        return None
    try:
        from shapely.geometry import Point
    except ImportError:
        return None
    minx, miny, maxx, maxy = geom.bounds
    # Pick a grid step that yields ~n_samples interior candidates.
    aspect = max((maxx - minx) / max(maxy - miny, 1e-6), 1e-6)
    ny = int(np.sqrt(n_samples / aspect))
    nx = int(n_samples / max(ny, 1))
    xs = np.linspace(minx, maxx, max(nx, 8))
    ys = np.linspace(miny, maxy, max(ny, 8))
    sum_w = 0.0
    sum_wx = 0.0
    sum_wy = 0.0
    for y in ys:
        for x in xs:
            pt = Point(x, y)
            if not geom.contains(pt):
                continue
            try:
                row, col = ds.index(x, y)
                if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
                    continue
                from rasterio.windows import Window
                v = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
                if ds.nodata is not None and v == ds.nodata:
                    continue
                if v <= 0:
                    continue
                sum_w += float(v)
                sum_wx += float(v) * x
                sum_wy += float(v) * y
            except Exception:
                continue
    if sum_w <= 0:
        return None
    return (sum_wy / sum_w, sum_wx / sum_w)


def resolve_zone_centroid(
    iso3: str,
    zone_id: str,
    members: list[str],
    year: int = 2020,
) -> ZoneCentroid:
    """Resolve a (lat, lon) centroid for a zone.

    Tries population-weighted centroid against the nearest GHSL/GPW epoch.
    Falls back to the geometric centroid of the dissolved admin_1 union.
    """
    gdf = _load_admin1()
    sub = gdf[gdf["adm0_a3"] == iso3]
    if len(sub) == 0:
        # Unknown iso3 in shapefile — punt to (0, 0) so caller can detect.
        return ZoneCentroid(zone_id, iso3, 0.0, 0.0, "missing", 0)
    geom = _merge_geoms(members, sub)
    if geom is None:
        return ZoneCentroid(zone_id, iso3, 0.0, 0.0, "missing", 0)

    # Round year to nearest available epoch (2000,2005,2010,2015,2020).
    epoch = min((2000, 2005, 2010, 2015, 2020), key=lambda e: abs(e - year))
    latlon = _weighted_centroid_from_geom(geom, pop_epoch=epoch)
    if latlon is not None:
        return ZoneCentroid(zone_id, iso3, latlon[0], latlon[1],
                            "pop_weighted",
                            n_admin1=len([m for m in members
                                          if m in sub["name"].values]))
    c = geom.centroid
    return ZoneCentroid(zone_id, iso3, float(c.y), float(c.x), "geometric",
                        n_admin1=len([m for m in members
                                      if m in sub["name"].values]))


def resolve_zones(
    iso3: str,
    zone_members: dict[str, list[str]],
    year: int = 2020,
) -> dict[str, ZoneCentroid]:
    """Batch-resolve all zones of a country."""
    out: dict[str, ZoneCentroid] = {}
    for zone_id, members in zone_members.items():
        if not members:
            continue
        out[zone_id] = resolve_zone_centroid(iso3, zone_id, members, year)
    return out
