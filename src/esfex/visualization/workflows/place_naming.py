"""Name grid nodes after the place / region of their centroid.

The Grid Builder names computed nodes generically ("Node 0", "Node 1", ...).
This module reverse-geocodes each node centroid via OpenStreetMap (Nominatim)
to a human place name (city, town, county, ...), de-duplicates collisions, and
falls back to the generic name when offline or on any failure. Geocoding is
done by the caller's background worker, respecting Nominatim's ~1 request/second
usage policy.
"""

from __future__ import annotations

# Address fields from most to least specific. The first present one wins.
_PLACE_KEYS = (
    "city", "town", "village", "municipality", "hamlet", "suburb",
    "city_district", "county", "state_district", "state", "region",
    "province", "country",
)


def place_from_address(address: dict) -> str | None:
    """Most specific human place name from a Nominatim ``address`` dict."""
    for key in _PLACE_KEYS:
        value = address.get(key)
        if value:
            return str(value).strip()
    return None


def reverse_geocode_place(lat: float, lng: float, *, timeout: float = 5.0) -> str | None:
    """English place name for a point via Nominatim reverse geocoding, or None.

    Network failures, timeouts and unparseable responses all return None so the
    caller can fall back to a generic name.
    """
    try:
        import requests

        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": lat, "lon": lng, "format": "json",
                "zoom": 12, "accept-language": "en",
            },
            headers={"User-Agent": "ESFEX-Grid/1.0"},
            timeout=timeout,
        )
        return place_from_address(resp.json().get("address", {}))
    except Exception:
        return None


def name_positions_by_region(
    positions: list[tuple[float, float, str]],
    *,
    geocode=reverse_geocode_place,
    fallback_prefix: str = "Node",
    rate_limit_s: float = 1.1,
    time_budget_s: float = 30.0,
    max_geocode: int = 80,
    cancelled=None,
    progress=None,
) -> list[tuple[float, float, str]]:
    """Replace each position's name with its region, de-duplicated.

    *positions* are ``(lat, lng, generic_name)`` tuples. Returns the same
    positions with region-based names; a place that resolves more than once gets
    a numeric suffix ("Springfield", "Springfield 2", ...). Any node that fails
    to geocode keeps a generic ``"{fallback_prefix} {i}"`` name. ``geocode`` is
    injectable for testing. ``cancelled()`` short-circuits to generic names;
    ``progress(done, total)`` is called after each node.

    Geocoding is reverse Nominatim at ~1 request/second, which does not scale to
    large grids. To guarantee the step always completes (it used to appear hung
    on big regions), geocoding stops once either ``time_budget_s`` of wall time
    or ``max_geocode`` calls is reached; every remaining node keeps its generic
    name. Pass ``time_budget_s=None`` to disable the budget.
    """
    import time

    total = len(positions)
    out: list[tuple[float, float, str]] = []
    seen: dict[str, int] = {}
    start = time.monotonic()
    geocoded = 0

    def _budget_left() -> bool:
        if cancelled and cancelled():
            return False
        if geocoded >= max_geocode:
            return False
        if time_budget_s is not None and (time.monotonic() - start) >= time_budget_s:
            return False
        return True

    for i, (lat, lng, _generic) in enumerate(positions):
        name = None
        if _budget_left():
            try:
                name = geocode(lat, lng)
            except Exception:
                name = None
            geocoded += 1
        if not name:
            name = f"{fallback_prefix} {i}"
        else:
            count = seen.get(name, 0)
            seen[name] = count + 1
            if count:
                name = f"{name} {count + 1}"
        out.append((lat, lng, name))
        if progress:
            progress(i + 1, total)
        # Only pay the rate-limit pause when the next node will be geocoded.
        if rate_limit_s and i < total - 1 and _budget_left():
            time.sleep(rate_limit_s)
    return out
