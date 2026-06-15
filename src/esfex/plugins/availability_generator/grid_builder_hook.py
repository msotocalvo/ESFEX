"""Glue between Grid Builder and the availability_generator plugin.

The standard ``generate_availability_profiles`` function operates on a
serialised :class:`ESFEXConfig`.  Inside the Grid Builder pipeline we
have something more direct: a live ``GuiSystemState`` populated with
``GuiGeneratorInstance`` objects, each carrying its own ``latitude``,
``longitude`` and ``fuel``.  This module emits availability CSVs for
that representation in one pass and writes the resulting paths back
onto each generator's ``availability_file`` field.

Wind / solar units use the existing weather-data backends
(``solarex`` / ``windrex``).  Everything else falls back to the
synthetic profiles in :mod:`synthetic_cf`, which are cheap (no I/O)
and good enough for typical screening studies.
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

from esfex.plugins.availability_generator.synthetic_cf import (
    compute_synthetic_cf,
    is_synthetic_fuel,
)

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import (
        GuiGeneratorInstance, GuiSystemState,
    )

logger = logging.getLogger(__name__)

_HOURS_PER_YEAR = 8760

_SOLAR_HINTS = frozenset({"sun", "solar", "pv", "photovoltaic"})
_WIND_HINTS = frozenset({"wind", "eolic", "eolica", "eolico"})

# Co-located generators share one weather query. Coordinates are bucketed to a
# grid cell before fetching: at 0.1° (~11 km) the cell is finer than the weather
# backend's own native resolution (Open-Meteo ≈ 11 km, ERA5 ≈ 31 km), so two
# points inside one cell return all-but-identical capacity factors anyway. This
# collapses hundreds of co-located units (e.g. units of one plant, or a clustered
# wind farm) into a handful of unique fetches.
_WEATHER_CELL_DEG = 0.1

# Bounded concurrency for the remaining unique fetches. Each call is network-
# bound (the GIL is released during I/O), so a small pool gives a large speedup
# while staying well under the weather backends' request-rate limits.
_WEATHER_MAX_WORKERS = 8


def _profile_kind(canonical_fuel: str) -> str:
    """Return ``'solar'``, ``'wind'`` or ``'synthetic'``."""
    f = (canonical_fuel or "").lower()
    if f in _SOLAR_HINTS:
        return "solar"
    if f in _WIND_HINTS:
        return "wind"
    return "synthetic"


def generate_for_grid_build(
    state: "GuiSystemState",
    output_dir: Path,
    *,
    use_weather_data: bool = False,
    weather_year: int = 2023,
    weather_source: str = "open_meteo",
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Path]:
    """Generate hourly availability CSVs for every generator in *state*.

    Parameters
    ----------
    state
        The newly-built ``GuiSystemState``.  Each generator must have
        ``latitude`` / ``longitude`` / ``fuel`` populated.
    output_dir
        Where to write CSVs (created if missing).  One file per
        generator: ``<instance_id>_availability.csv``.
    use_weather_data
        When True, wind and solar generators query the weather backend
        for their (lat, lng) — realistic, but network-bound. Queries are
        de-duplicated per ~11 km grid cell and run concurrently, so the
        cost scales with the number of *distinct* locations, not the
        generator count. If a query fails (or the backend is unavailable),
        that wind/solar unit is left **without** an availability profile —
        no flat or synthetic value is fabricated — and the omission is
        reported. When False (fast builds) wind/solar use a flat
        0.32 / 0.20 annual factor. Non-weather fuels (thermal / hydro /
        geothermal / biomass) always use synthetic profiles.
    weather_year
        Calendar year for the weather query (only used if
        ``use_weather_data`` is True).
    weather_source
        Weather backend: ``open_meteo`` / ``nasa_power`` / ``era5_atlite``.
    progress_callback
        Optional ``callback(percent, message)``.

    Returns
    -------
    dict
        Mapping ``instance_id → csv_path``.  ``state.generators[id]
        .availability_file`` is also updated in place.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from esfex.visualization.workflows.grid_mapping_builder import (
        _normalize_fuel_key,
    )

    gens = list(state.generators.items())
    if not gens:
        if progress_callback:
            progress_callback(100, "No generators to process.")
        return {}

    # Classify once: (gid, gen, canonical_fuel, kind).
    classified = [
        (gid, gen, (canon := _normalize_fuel_key(gen.fuel)), _profile_kind(canon))
        for gid, gen in gens
    ]

    # Pre-fetch weather capacity factors for the *distinct* wind/solar locations
    # in parallel; co-located generators then reuse a cached array. This is the
    # whole point of the optimization — the slow part is the network query, and
    # there are far fewer unique cells than generators.
    weather_cache: dict[tuple, np.ndarray] = {}
    if use_weather_data:
        weather_cache = _prefetch_weather_cfs(
            classified, weather_year, weather_source, progress_callback,
        )

    from esfex.plugins.availability_generator.synthetic_cf import (
        compute_constant_cf,
    )

    written: dict[str, Path] = {}
    skipped: list[str] = []
    total = len(classified)
    for idx, (gid, gen, canonical, kind) in enumerate(classified):
        pct = int(100 * (idx + 1) / total)
        if progress_callback:
            progress_callback(pct, f"Profile {idx + 1}/{total}: {gid} ({kind})")

        if use_weather_data and kind in ("solar", "wind"):
            cf = weather_cache.get(
                _weather_cache_key(kind, gen, weather_year, weather_source))
            if cf is None:
                # No real weather data for this location: do NOT fabricate a
                # flat capacity factor. Leave the unit without an availability
                # file and report it, so the gap is visible rather than faked.
                logger.warning(
                    "No weather data for %s (%s) — leaving it without an "
                    "availability profile (no synthetic fallback).", gid, kind)
                skipped.append(gid)
                continue
        else:
            # Synthetic fuels (thermal/hydro/geothermal/biomass), or weather
            # explicitly disabled → the cheap local profile (no I/O).
            try:
                cf = _compute_cf(
                    kind, canonical, gen,
                    use_weather_data=use_weather_data,
                    weather_year=weather_year,
                    weather_source=weather_source,
                    seed=idx,
                )
            except Exception as exc:
                logger.warning(
                    "Synthetic profile failed for %s (%s): %s — flat default.",
                    gid, kind, exc,
                )
                cf = compute_constant_cf(_default_for_kind(kind))

        csv_path = output_dir / f"{gid}_availability.csv"
        np.savetxt(csv_path, cf, delimiter=",", fmt="%.6f")
        gen.availability_file = str(csv_path)
        written[gid] = csv_path

    if skipped:
        shown = ", ".join(skipped[:20]) + (" …" if len(skipped) > 20 else "")
        logger.warning(
            "%d wind/solar generator(s) had no weather data available; left "
            "without an availability profile: %s", len(skipped), shown)
    if progress_callback:
        progress_callback(100, f"Wrote {len(written)} availability CSV(s).")
    return written


# ── Internal helpers ────────────────────────────────────────────────


def _weather_cache_key(
    kind: str,
    gen: "GuiGeneratorInstance",
    weather_year: int,
    weather_source: str,
) -> tuple:
    """A dedup key for one weather query.

    Coordinates are bucketed to a ~11 km grid cell (see ``_WEATHER_CELL_DEG``).
    Wind capacity factors also depend on the rated power (turbine power-curve
    scaling), so the rounded rating joins the key; solar does not.
    """
    lat = round(gen.latitude / _WEATHER_CELL_DEG) * _WEATHER_CELL_DEG
    lng = round(gen.longitude / _WEATHER_CELL_DEG) * _WEATHER_CELL_DEG
    cell = (round(lat, 3), round(lng, 3), weather_year, weather_source)
    if kind == "wind":
        return ("wind", *cell, round(max(gen.rated_power, 1.0)))
    return ("solar", *cell)


def _prefetch_weather_cfs(
    classified: list,
    weather_year: int,
    weather_source: str,
    progress_callback: Optional[Callable[[int, str], None]],
) -> dict[tuple, np.ndarray]:
    """Fetch the distinct wind/solar weather profiles concurrently.

    Returns a ``cache_key → cf`` map. Keys whose fetch fails are simply absent,
    so the caller falls back to the cheap flat default for those generators.
    """
    # One representative generator per distinct location/kind/rating.
    reps: dict[tuple, "GuiGeneratorInstance"] = {}
    kinds: dict[tuple, str] = {}
    for _gid, gen, _canonical, kind in classified:
        if kind not in ("solar", "wind"):
            continue
        key = _weather_cache_key(kind, gen, weather_year, weather_source)
        if key not in reps:
            reps[key] = gen
            kinds[key] = kind

    cache: dict[tuple, np.ndarray] = {}
    keys = list(reps)
    total = len(keys)
    if not total:
        return cache

    if progress_callback:
        progress_callback(0, f"Fetching weather for {total} location(s)…")

    def _work(key: tuple):
        return key, _fetch_one_weather_cf(
            kinds[key], reps[key], weather_year, weather_source)

    done = 0
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_WEATHER_MAX_WORKERS, total)
    ) as ex:
        for fut in concurrent.futures.as_completed(
            [ex.submit(_work, k) for k in keys]
        ):
            try:
                key, cf = fut.result()
                if cf is not None:
                    cache[key] = cf
            except Exception as exc:
                logger.warning("Weather fetch failed: %s — flat fallback.", exc)
            done += 1
            if progress_callback:
                progress_callback(
                    int(100 * done / total),
                    f"Weather {done}/{total} location(s)…")

    logger.info(
        "Weather availability: %d unique location(s) fetched for the build.",
        len(cache),
    )
    return cache


def _fetch_one_weather_cf(
    kind: str,
    gen: "GuiGeneratorInstance",
    weather_year: int,
    weather_source: str,
) -> Optional[np.ndarray]:
    """Query the weather backend for one location. ``None`` → use flat default.

    Runs on a worker thread; must not touch Qt or shared mutable state.
    """
    if kind == "solar":
        try:
            from solarex import compute_solar_hourly_cf
        except ImportError:
            logger.debug("solarex unavailable — using flat default")
            return None
        return np.asarray(compute_solar_hourly_cf(
            gen.latitude, gen.longitude, weather_year, weather_source,
        ))
    if kind == "wind":
        try:
            from windrex import compute_wind_hourly_cf
        except ImportError:
            logger.debug("windrex unavailable — using flat default")
            return None
        return np.asarray(compute_wind_hourly_cf(
            gen.latitude, gen.longitude, weather_year, weather_source,
            rated_power_mw=max(gen.rated_power, 1.0),
        ))
    return None


def _default_for_kind(kind: str) -> float:
    if kind == "solar":
        return 0.20
    if kind == "wind":
        return 0.32
    return 0.85


def _compute_cf(
    kind: str,
    canonical_fuel: str,
    gen: "GuiGeneratorInstance",
    *,
    use_weather_data: bool,
    weather_year: int,
    weather_source: str,
    seed: int,
) -> np.ndarray:
    if kind == "solar":
        if use_weather_data:
            try:
                from solarex import compute_solar_hourly_cf
                return np.asarray(compute_solar_hourly_cf(
                    gen.latitude, gen.longitude, weather_year, weather_source,
                ))
            except ImportError:
                logger.debug("solarex unavailable — using flat default")
        from esfex.plugins.availability_generator.synthetic_cf import (
            compute_constant_cf,
        )
        return compute_constant_cf(0.20)

    if kind == "wind":
        if use_weather_data:
            try:
                from windrex import compute_wind_hourly_cf
                return np.asarray(compute_wind_hourly_cf(
                    gen.latitude, gen.longitude, weather_year, weather_source,
                    rated_power_mw=max(gen.rated_power, 1.0),
                ))
            except ImportError:
                logger.debug("windrex unavailable — using flat default")
        from esfex.plugins.availability_generator.synthetic_cf import (
            compute_constant_cf,
        )
        return compute_constant_cf(0.32)

    # Synthetic family
    return compute_synthetic_cf(
        canonical_fuel, lat=gen.latitude, seed=seed,
    )
