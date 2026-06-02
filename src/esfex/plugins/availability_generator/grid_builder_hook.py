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
        for their (lat, lng) — slow but realistic. When False (default
        for fast builds) wind/solar fall back to a flat 0.32 / 0.20
        annual factor; non-weather generators always use synthetic
        profiles.
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

    written: dict[str, Path] = {}
    total = len(gens)
    for idx, (gid, gen) in enumerate(gens):
        pct = int(100 * (idx + 1) / total)
        canonical = _normalize_fuel_key(gen.fuel)
        kind = _profile_kind(canonical)

        if progress_callback:
            progress_callback(pct, f"Profile {idx + 1}/{total}: {gid} ({kind})")

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
                "Availability generation failed for %s (%s): %s — "
                "falling back to flat profile.",
                gid, kind, exc,
            )
            from esfex.plugins.availability_generator.synthetic_cf import (
                compute_constant_cf,
            )
            cf = compute_constant_cf(_default_for_kind(kind))

        csv_path = output_dir / f"{gid}_availability.csv"
        np.savetxt(csv_path, cf, delimiter=",", fmt="%.6f")
        gen.availability_file = str(csv_path)
        written[gid] = csv_path

    if progress_callback:
        progress_callback(100, f"Wrote {len(written)} availability CSV(s).")
    return written


# ── Internal helpers ────────────────────────────────────────────────


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
